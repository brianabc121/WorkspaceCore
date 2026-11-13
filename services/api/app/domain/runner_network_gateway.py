"""Authoritative, short-lived gateway decisions; destinations never come from clients."""

import uuid
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.runner_capabilities import has_capability
from app.domain.runner_network_limits import MAX_GATEWAY_BINDINGS, public_sources_available
from app.models import AgentInstance, AgentSettings, RunnerEnvironment, RunnerHost
from app.models.runner_network import RunnerIngressRule, RunnerPublishedEndpoint, RunnerService

Kind = Literal["ingress", "published"]
Record = RunnerIngressRule | RunnerPublishedEndpoint


class WorkloadIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: uuid.UUID
    instance_id: uuid.UUID
    environment_id: uuid.UUID
    allocation_id: uuid.UUID
    generation: int = Field(ge=1)
    pod_uid: str = Field(min_length=1, max_length=128)


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: uuid.UUID
    kind: Kind
    peer_ip: str = Field(max_length=64)
    source: WorkloadIdentity | None = None


class GatewayAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: uuid.UUID
    kind: Kind
    revision: int = Field(ge=1)
    generation: int = Field(ge=1)
    success: bool
    active: bool = True


def networks(value: str) -> list[Any]:
    return [ip_network(item.strip(), strict=True) for item in value.split(",") if item.strip()]


def live(row: RunnerService | Record) -> bool:
    return row.state not in ("revoked", "expired") and (
        row.expires_at is None or row.expires_at > datetime.now(UTC)
    )


class NetworkGateway:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def configured(self) -> bool:
        return bool(
            self.settings.runner_network_verified
            and self.settings.runner_network_gateway_token.get_secret_value()
            and self.settings.runner_network_denied_cidrs.strip()
            and self.settings.runner_network_pod_cidrs.strip()
        )

    async def record(self, kind: Kind, record_id: uuid.UUID) -> Record | None:
        model = RunnerIngressRule if kind == "ingress" else RunnerPublishedEndpoint
        return await self.db.get(model, record_id)

    async def instance_enabled(self, workspace_id: uuid.UUID, instance_id: uuid.UUID) -> bool:
        instance = await self.db.get(AgentInstance, instance_id)
        options = await self.db.get(AgentSettings, workspace_id)
        return bool(
            instance is not None
            and instance.workspace_id == workspace_id
            and instance.status != "disabled"
            and instance.runner_enabled
            and (options is None or options.agents_enabled)
        )

    async def ready(self) -> bool:
        """Only an authenticated, operational gateway applies already approved grants.

        This never creates approvals or removes the target-inbound requirement.
        Subsequent leases still check every permission and live binding.
        """
        from app.domain.runner_capabilities import RunnerCapabilityService
        from app.models.runner_capability import RunnerCapabilityGrant

        if not self.configured():
            return False
        workspaces = {
            h.workspace_id
            for h in await self.db.scalars(
                select(RunnerHost).where(
                    RunnerHost.backend == "kubernetes", RunnerHost.status == "online"
                )
            )
            if (h.runtime_metadata or {}).get("network_validation_revision") == 1
        }
        if not workspaces:
            return False
        grants = list(
            await self.db.scalars(
                select(RunnerCapabilityGrant).where(
                    RunnerCapabilityGrant.workspace_id.in_(workspaces),
                    RunnerCapabilityGrant.capability == "runner.cross_agent_communication",
                    RunnerCapabilityGrant.state == "approved_pending_apply",
                )
            )
        )
        for grant in grants:
            if await self.instance_enabled(grant.workspace_id, grant.instance_id):
                await RunnerCapabilityService(self.db, self.settings).mark_applied(
                    grant.id, grant.desired_revision
                )
        await self.db.commit()
        return True

    async def target(self, row: Record) -> dict[str, Any] | None:
        if (
            not self.configured()
            or not live(row)
            or not await self.instance_enabled(row.workspace_id, row.owner_instance_id)
        ):
            return None
        if isinstance(row, RunnerPublishedEndpoint) and not public_sources_available(
            self.settings, row.source_cidrs
        ):
            return None
        service = await self.db.get(RunnerService, row.service_id)
        env = await self.db.get(RunnerEnvironment, row.environment_id)
        if service is None or env is None:
            return None
        host = await self.db.get(RunnerHost, env.host_id)
        instance = await self.db.get(AgentInstance, env.owner_instance_id)
        metadata = env.runtime_metadata or {}
        generation = metadata.get("generation")
        if (
            env.state != "running"
            or env.runtime_backend != "kubernetes"
            or env.workspace_id != row.workspace_id
            or env.host_id != row.host_id
            or env.owner_instance_id != row.owner_instance_id
            or row.desired_generation != generation
            or service.workspace_id != env.workspace_id
            or service.environment_id != env.id
            or service.owner_instance_id != env.owner_instance_id
            or service.host_id != env.host_id
            or service.protocol != "TCP"
            or service.state != "active"
            or not live(service)
            or service.desired_revision != service.applied_revision
            or service.applied_generation != generation
            or host is None
            or host.status != "online"
            or not host.node_name
            or (host.runtime_metadata or {}).get("network_validation_revision") != 1
            or instance is None
            or instance.status == "disabled"
            or not metadata.get("pod_uid")
            or metadata.get("node") != host.node_name
            or type(generation) is not int
            or generation < 1
        ):
            return None
        try:
            address = ip_address(str(metadata.get("pod_ip")))
            if address.version != 4 or not any(
                address in net for net in networks(self.settings.runner_network_pod_cidrs)
            ):
                return None
            allocation = uuid.UUID(str(metadata.get("allocation_id") or env.id))
        except ValueError:
            return None
        return {
            "workspace_id": str(env.workspace_id),
            "instance_id": str(env.owner_instance_id),
            "environment_id": str(env.id),
            "allocation_id": str(allocation),
            "generation": generation,
            "pod_uid": str(metadata["pod_uid"]),
            "pod_ip": str(address),
            "node_name": host.node_name,
            "port": service.port,
        }

    async def source_allowed(
        self, row: RunnerIngressRule, source: WorkloadIdentity | None, peer_ip: str
    ) -> bool:
        if (
            source is None
            or source.workspace_id != row.workspace_id
            or source.instance_id != row.source_instance_id
        ):
            return False
        obj = await self.db.get(RunnerEnvironment, source.environment_id)
        instance = await self.db.get(AgentInstance, source.instance_id)
        if obj is None or instance is None or instance.status == "disabled":
            return False
        metadata = obj.runtime_metadata or {}
        if (
            obj.state != "running"
            or obj.runtime_backend != "kubernetes"
            or obj.workspace_id != source.workspace_id
            or obj.owner_instance_id != source.instance_id
            or str(metadata.get("allocation_id") or obj.id) != str(source.allocation_id)
            or metadata.get("generation") != source.generation
            or metadata.get("pod_uid") != source.pod_uid
            or metadata.get("pod_ip") != peer_ip
        ):
            return False
        host = await self.db.get(RunnerHost, obj.host_id)
        if (
            host is None
            or host.status != "online"
            or (host.runtime_metadata or {}).get("network_validation_revision") != 1
        ):
            return False
        return await has_capability(
            self.db, row.workspace_id, row.source_instance_id, "runner.cross_agent_communication"
        )

    async def config(self) -> dict[str, Any]:
        bindings = []
        active_count = 0
        for kind, model in (("ingress", RunnerIngressRule), ("published", RunnerPublishedEndpoint)):
            for row in await self.db.scalars(select(model).order_by(model.gateway_port)):
                if not isinstance(row, RunnerIngressRule | RunnerPublishedEndpoint):
                    raise TypeError("unexpected network model")
                if row.desired_generation < 1:
                    continue  # legacy Docker declarations never acquired a managed binding
                if (
                    row.state in ("revoked", "expired")
                    and row.applied_revision == row.desired_revision
                    and row.applied_generation == row.desired_generation
                    and row.apply_error is None
                ):
                    continue
                target = await self.target(row)
                if isinstance(row, RunnerIngressRule) and not await has_capability(
                    self.db,
                    row.workspace_id,
                    row.source_instance_id,
                    "runner.cross_agent_communication",
                ):
                    target = None
                if target is not None:
                    if active_count >= MAX_GATEWAY_BINDINGS:
                        target = None
                    else:
                        active_count += 1
                bindings.append(
                    {
                        "record_id": str(row.id),
                        "kind": kind,
                        "port": row.gateway_port,
                        "revision": row.desired_revision,
                        "generation": row.desired_generation,
                        "active": target is not None,
                        "target": target,
                        "source_instance_id": str(row.source_instance_id)
                        if isinstance(row, RunnerIngressRule)
                        else None,
                        "source_cidrs": row.source_cidrs
                        if isinstance(row, RunnerPublishedEndpoint)
                        else [],
                    }
                )
        denied = list(
            dict.fromkeys(
                str(n)
                for n in networks(
                    self.settings.runner_network_denied_cidrs
                    + ","
                    + self.settings.runner_network_pod_cidrs
                )
            )
        )
        return {"revision": 1, "bindings": bindings, "denied_cidrs": denied}

    async def lease(self, request: LeaseRequest) -> dict[str, Any]:
        denied: dict[str, Any] = {
            "allowed": False,
            "lease_seconds": 0,
            "revision": 0,
            "generation": 0,
            "target": None,
        }
        row = await self.record(request.kind, request.record_id)
        if row is None or row.state != "active" or row.applied_revision != row.desired_revision:
            return denied
        target = await self.target(row)
        if target is None or row.applied_generation != target["generation"]:
            return denied
        try:
            peer = ip_address(request.peer_ip)
            if peer.version != 4:
                return denied
            if isinstance(row, RunnerPublishedEndpoint):
                blocked = networks(
                    self.settings.runner_network_denied_cidrs
                    + ","
                    + self.settings.runner_network_pod_cidrs
                )
                if (
                    request.source is not None
                    or peer.is_loopback
                    or peer.is_link_local
                    or peer.is_multicast
                    or any(peer in n for n in blocked)
                ):
                    return denied
                if not any(peer in ip_network(n) for n in row.source_cidrs):
                    return denied
            elif not await self.source_allowed(row, request.source, str(peer)):
                return denied
        except ValueError:
            return denied
        remaining = (row.expires_at - datetime.now(UTC)).total_seconds() if row.expires_at else 0
        if remaining <= 0:
            return denied
        return {
            "allowed": True,
            "lease_seconds": min(20.0, remaining),
            "revision": row.desired_revision,
            "generation": target["generation"],
            "target": target,
        }

    async def acknowledge(self, body: GatewayAck) -> bool:
        model = RunnerIngressRule if body.kind == "ingress" else RunnerPublishedEndpoint
        row = await self.db.scalar(
            select(model).where(model.id == body.record_id).with_for_update()
        )
        if row is not None and not isinstance(row, RunnerIngressRule | RunnerPublishedEndpoint):
            raise TypeError("unexpected network model")
        if (
            row is None
            or row.desired_revision != body.revision
            or row.desired_generation != body.generation
        ):
            return False
        if not body.success:
            row.apply_error = "NETWORK_GATEWAY_UNAVAILABLE"
            if live(row):
                row.state = "apply_failed"
        elif not live(row):
            row.applied_revision, row.applied_generation, row.apply_error = (
                body.revision,
                body.generation,
                None,
            )
            if row.state != "revoked":
                row.state = "expired"
        else:
            target = await self.target(row)
            capability = not isinstance(row, RunnerIngressRule) or await has_capability(
                self.db,
                row.workspace_id,
                row.source_instance_id,
                "runner.cross_agent_communication",
            )
            if not body.active or target is None or not capability:
                # An inactive config asks the gateway to withdraw a listener.
                # Confirm withdrawal without manufacturing an active policy.
                row.state, row.apply_error = "approved_pending_apply", "NETWORK_RUNTIME_UNCONFIRMED"
            else:
                row.state, row.apply_error = "active", None
                row.applied_revision, row.applied_generation = body.revision, body.generation
        await self.db.commit()
        return True
