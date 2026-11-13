"""Database-owned desired network policy; runtime acknowledgments are separate facts.

Cross-instance/public enforcement is intentionally unavailable until a real gateway
can prove source identity, NAT hairpin protection, TCP/UDP lease termination and
revocation. No API request can manufacture an applied policy or connect address.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, TypeVar, overload

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess, not_found, require_role
from app.domain.runner_capabilities import has_capability
from app.domain.runner_network_limits import require_public_sources, reserve_gateway_slot
from app.domain.step_up import verify_step_up
from app.models import AgentInstance, RunnerEnvironment, RunnerHost, Workspace
from app.models.runner_network import (
    NetworkRecord,
    RunnerIngressRule,
    RunnerPublishedEndpoint,
    RunnerService,
)
from app.schemas.runner_network import (
    NetworkRecordOut,
    RunnerIngressInput,
    RunnerIngressOut,
    RunnerIngressUpdate,
    RunnerNetworkOut,
    RunnerPublishedEndpointOut,
    RunnerPublishInput,
    RunnerPublishUpdate,
    RunnerRevokeInput,
    RunnerServiceCreate,
    RunnerServiceOut,
    RunnerServiceUpdate,
)
from app.schemas.workspaces import Role

UNAVAILABLE = "NETWORK_ENFORCEMENT_UNAVAILABLE"
NetworkModel = TypeVar("NetworkModel", bound=NetworkRecord)
NETWORK_MODELS: tuple[type[NetworkRecord], ...] = (
    RunnerService,
    RunnerIngressRule,
    RunnerPublishedEndpoint,
)


def now() -> datetime:
    return datetime.now(UTC)


def admits_gateway(row: RunnerIngressRule | RunnerPublishedEndpoint) -> bool:
    """A live child whose service's node policy must admit gateway traffic."""
    return (
        row.state not in ("revoked", "expired")
        and row.expires_at is not None
        and row.expires_at > now()
    )


def ack_allowed(
    host_id: uuid.UUID,
    expected_host: uuid.UUID,
    revision: int,
    desired: int,
    generation: int,
    expected_generation: int,
    *,
    high_risk: bool,
) -> bool:
    return (
        host_id == expected_host
        and revision == desired
        and generation == expected_generation
        and generation > 0
        and not high_risk
    )


class RunnerNetworkService:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None):
        self.db, self.settings, self.redis = db, settings, redis

    async def environment(
        self, workspace_id: uuid.UUID, environment_id: uuid.UUID, *, lock: bool = False
    ) -> RunnerEnvironment:
        query = select(RunnerEnvironment).where(
            RunnerEnvironment.id == environment_id, RunnerEnvironment.workspace_id == workspace_id
        )
        if lock:
            query = query.with_for_update()
        result = await self.db.scalar(query)
        if result is None:
            raise not_found()
        return result

    async def _owner(
        self, context: AuthContext, access: WorkspaceAccess, body: RunnerRevokeInput | None = None
    ) -> None:
        await require_role(
            self.db, user_id=context.user.id, workspace_id=access.workspace.id, minimum=Role.owner
        )
        if body is not None:
            await verify_step_up(self.redis, context.user, body.password)
        await self.db.scalar(
            select(Workspace)
            .where(Workspace.id == access.workspace.id)
            .with_for_update(key_share=True)
        )

    async def _identity(self, env: RunnerEnvironment) -> None:
        if env.owner_instance_id is None or env.state in ("deleted", "archived", "pending_delete"):
            raise ApiError(409, "NETWORK_IDENTITY_UNAVAILABLE", "环境没有有效运行实例身份")
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == env.owner_instance_id,
                AgentInstance.workspace_id == env.workspace_id,
            )
        )
        if instance is None or instance.status == "disabled":
            raise ApiError(409, "NETWORK_IDENTITY_UNAVAILABLE", "环境归属实例不可用")

    @staticmethod
    def _instance_id(env: RunnerEnvironment) -> uuid.UUID:
        if env.owner_instance_id is None:
            raise ApiError(409, "NETWORK_IDENTITY_UNAVAILABLE", "环境没有有效运行实例身份")
        return env.owner_instance_id

    @staticmethod
    def _fields(env: RunnerEnvironment, context: AuthContext) -> dict[str, Any]:
        return dict(
            workspace_id=env.workspace_id,
            environment_id=env.id,
            host_id=env.host_id,
            owner_instance_id=env.owner_instance_id,
            created_by=context.user.id,
            desired_generation=int((env.runtime_metadata or {}).get("generation", 0)),
        )

    @overload
    def output(self, row: RunnerService, env: RunnerEnvironment) -> RunnerServiceOut: ...

    @overload
    def output(self, row: RunnerIngressRule, env: RunnerEnvironment) -> RunnerIngressOut: ...

    @overload
    def output(
        self, row: RunnerPublishedEndpoint, env: RunnerEnvironment
    ) -> RunnerPublishedEndpointOut: ...

    @overload
    def output(self, row: NetworkRecord, env: RunnerEnvironment) -> NetworkRecordOut: ...

    def output(self, row: NetworkRecord, env: RunnerEnvironment) -> NetworkRecordOut:
        schema = (
            RunnerServiceOut
            if isinstance(row, RunnerService)
            else RunnerIngressOut
            if isinstance(row, RunnerIngressRule)
            else RunnerPublishedEndpointOut
        )
        out = schema.model_validate(row)
        identity_current = (
            row.host_id == env.host_id and row.owner_instance_id == env.owner_instance_id
        )
        generation = int((env.runtime_metadata or {}).get("generation", 0))
        if row.expires_at and row.expires_at <= now() and row.state != "revoked":
            out.state = "expired"
        out.revocation_confirmed = (
            row.state in ("revoked", "expired")
            and row.applied_revision == row.desired_revision
            and row.applied_generation == row.desired_generation
            and row.apply_error is None
        )
        if out.state == "active" and (
            not identity_current or row.applied_generation != generation or env.state != "running"
        ):
            out.state, out.apply_error = "approved_pending_apply", "NETWORK_RUNTIME_UNCONFIRMED"
        # Private service use is same-owner only. Gateway paths stay pending.
        if out.state == "active" and row.applied_revision == row.desired_revision:
            if isinstance(row, RunnerService):
                out.connect_host, out.connect_port = row.desired_dns, row.port
            elif self.settings.runner_network_verified:
                out.connect_host = (
                    self.settings.runner_network_private_host
                    if isinstance(row, RunnerIngressRule)
                    else self.settings.runner_network_public_host or None
                )
                out.connect_port = getattr(row, "gateway_port", None)
            out.accepting_new_connections = bool(out.connect_host and out.connect_port)
        return out

    async def _rows(self, model: type[NetworkModel], env: RunnerEnvironment) -> list[NetworkModel]:
        return list(
            await self.db.scalars(
                select(model)
                .where(model.workspace_id == env.workspace_id, model.environment_id == env.id)
                .order_by(model.created_at, model.id)
            )
        )

    async def list(self, access: WorkspaceAccess, environment_id: uuid.UUID) -> RunnerNetworkOut:
        env = await self.environment(access.workspace.id, environment_id)
        services = [self.output(r, env) for r in await self._rows(RunnerService, env)]
        rules = [self.output(r, env) for r in await self._rows(RunnerIngressRule, env)]
        endpoints = [self.output(r, env) for r in await self._rows(RunnerPublishedEndpoint, env)]
        status = "unknown"
        error: str | None = "NETWORK_RUNTIME_UNCONFIRMED"
        if env.runtime_backend != "kubernetes":
            status, error = "unsupported", "MANAGED_NETWORK_REQUIRES_KUBERNETES"
        elif services:
            status = "active" if all(s.state == "active" for s in services) else "pending_apply"
            error = None if status == "active" else "NETWORK_RUNTIME_UNCONFIRMED"
            if any(s.state == "apply_failed" for s in services):
                status, error = "apply_failed", "NETWORK_APPLY_FAILED"
        return RunnerNetworkOut(
            environment_id=env.id,
            workspace_id=env.workspace_id,
            owner_instance_id=env.owner_instance_id,
            host_id=env.host_id,
            runtime_status=status,
            runtime_error=error,
            runtime_ip=(env.runtime_metadata or {}).get("pod_ip")
            if env.state == "running"
            else None,
            services=services,
            ingress_rules=rules,
            published_endpoints=endpoints,
        )

    async def create_service(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        environment_id: uuid.UUID,
        body: RunnerServiceCreate,
    ) -> RunnerServiceOut:
        await self._owner(context, access)
        env = await self.environment(access.workspace.id, environment_id, lock=True)
        await self._identity(env)
        duplicate = await self.db.scalar(
            select(RunnerService.id).where(
                RunnerService.environment_id == env.id, RunnerService.name == body.name
            )
        )
        if duplicate:
            raise ApiError(409, "SERVICE_NAME_EXISTS", "环境内服务名称已存在")
        sid = uuid.uuid4()
        dns = (
            f"svc-{sid.hex}.oc-{self._instance_id(env).hex}.svc.cluster.local"
            if env.runtime_backend == "kubernetes"
            else None
        )
        row = RunnerService(
            id=sid,
            **self._fields(env, context),
            **body.model_dump(),
            desired_dns=dns,
            apply_error=None if dns else "MANAGED_NETWORK_REQUIRES_KUBERNETES",
        )
        self.db.add(row)
        await self.db.commit()
        return self.output(row, env)

    async def _service(self, env: RunnerEnvironment, service_id: uuid.UUID) -> RunnerService:
        row = await self.db.scalar(
            select(RunnerService)
            .where(
                RunnerService.id == service_id,
                RunnerService.environment_id == env.id,
                RunnerService.workspace_id == env.workspace_id,
            )
            .with_for_update()
        )
        if row is None:
            raise not_found()
        if (
            row.state in ("revoked", "expired")
            or row.host_id != env.host_id
            or row.owner_instance_id != env.owner_instance_id
        ):
            raise ApiError(409, "SERVICE_UNAVAILABLE", "服务身份已变化或已撤销")
        return row

    async def update_service(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        environment_id: uuid.UUID,
        service_id: uuid.UUID,
        body: RunnerServiceUpdate,
    ) -> RunnerServiceOut:
        await self._owner(context, access, body)
        env = await self.environment(access.workspace.id, environment_id, lock=True)
        await self._identity(env)
        row = await self._service(env, service_id)
        self._revision(row, body.expected_revision)
        if body.protocol != "TCP":
            children = [
                r
                for model in (RunnerIngressRule, RunnerPublishedEndpoint)
                for r in await self._rows(model, env)
                if isinstance(r, RunnerIngressRule | RunnerPublishedEndpoint)
                and r.service_id == row.id
                and r.state not in ("revoked", "expired")
            ]
            if children:
                raise ApiError(
                    409, "NETWORK_PROTOCOL_UNSUPPORTED", "先撤销跨实例及公网入口，再改为UDP"
                )
        duplicate = await self.db.scalar(
            select(RunnerService.id).where(
                RunnerService.environment_id == env.id,
                RunnerService.name == body.name,
                RunnerService.id != row.id,
            )
        )
        if duplicate:
            raise ApiError(409, "SERVICE_NAME_EXISTS", "环境内服务名称已存在")
        row.name, row.port, row.protocol = body.name, body.port, body.protocol
        row.desired_revision += 1
        row.state, row.apply_error = "approved_pending_apply", None
        await self._invalidate_children(env, row.id, revoke=False, context=context)
        await self._audit(context, env, row, "updated")
        await self.db.commit()
        return self.output(row, env)

    @staticmethod
    def _revision(row: NetworkRecord, expected: int) -> None:
        if row.desired_revision != expected:
            raise ApiError(409, "VERSION_CONFLICT", "网络配置已变化，请刷新后重试")
        if row.state in ("revoked", "expired") or (row.expires_at and row.expires_at <= now()):
            raise ApiError(409, "SERVICE_UNAVAILABLE", "配置已撤销或过期")

    async def update_rule(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        environment_id: uuid.UUID,
        record_id: uuid.UUID,
        body: RunnerIngressUpdate | RunnerPublishUpdate,
        *,
        kind: str,
    ) -> NetworkRecordOut:
        await self._owner(context, access, body)
        env = await self.environment(access.workspace.id, environment_id, lock=True)
        await self._identity(env)
        model = RunnerIngressRule if kind == "ingress" else RunnerPublishedEndpoint
        row = await self.db.scalar(
            select(model)
            .where(
                model.id == record_id,
                model.environment_id == env.id,
                model.workspace_id == env.workspace_id,
            )
            .with_for_update()
        )
        if row is None:
            raise not_found()
        if not isinstance(row, RunnerIngressRule | RunnerPublishedEndpoint):
            raise TypeError("unexpected network model")
        self._revision(row, body.expected_revision)
        await self._service(env, row.service_id)
        if isinstance(row, RunnerIngressRule) and not await has_capability(
            self.db, env.workspace_id, row.source_instance_id, "runner.cross_agent_communication"
        ):
            raise ApiError(403, "SOURCE_CAPABILITY_REQUIRED", "来源实例需要有效跨智能体通信权限")
        row.expires_at = body.expires_at
        if isinstance(row, RunnerPublishedEndpoint) and isinstance(body, RunnerPublishUpdate):
            require_public_sources(self.settings, body.source_cidrs)
            row.source_cidrs = body.source_cidrs
        row.state, row.apply_error = "approved_pending_apply", None
        row.desired_revision += 1
        await self._audit(context, env, row, "updated")
        await self.db.commit()
        return self.output(row, env)

    async def _gateway_ingress(self, env: RunnerEnvironment, service_id: uuid.UUID) -> bool:
        """Whether desired_policy already reports gateway_ingress for this service."""
        for rule in await self._rows(RunnerIngressRule, env):
            if (
                isinstance(rule, RunnerIngressRule)
                and rule.service_id == service_id
                and admits_gateway(rule)
                and await has_capability(
                    self.db,
                    env.workspace_id,
                    rule.source_instance_id,
                    "runner.cross_agent_communication",
                )
            ):
                return True
        return any(
            isinstance(row, RunnerPublishedEndpoint)
            and row.service_id == service_id
            and admits_gateway(row)
            for row in await self._rows(RunnerPublishedEndpoint, env)
        )

    @staticmethod
    def _fence_gateway_ingress(service: RunnerService, admitted: bool) -> None:
        # The first gateway child turns on the service's gateway-ingress policy on
        # the node, so the service waits for the node to confirm it before the
        # gateway may use it. Later children reuse that applied policy: bumping the
        # service again would withdraw every live sibling rule and its sessions.
        if not admitted:
            service.desired_revision += 1
            service.state = "approved_pending_apply"

    async def _invalidate_children(
        self, env: RunnerEnvironment, service_id: uuid.UUID, *, revoke: bool, context: AuthContext
    ) -> None:
        for model in (RunnerIngressRule, RunnerPublishedEndpoint):
            for child in await self._rows(model, env):
                if not isinstance(child, RunnerIngressRule | RunnerPublishedEndpoint):
                    raise TypeError("unexpected network model")
                if child.service_id != service_id or child.state in ("revoked", "expired"):
                    continue
                child.desired_revision += 1
                child.state = "revoked" if revoke else "approved_pending_apply"
                child.apply_error = "REVOCATION_PENDING_CONFIRMATION" if revoke else None
                if revoke:
                    child.revoked_at, child.revoked_by = now(), context.user.id

    async def _audit(
        self, context: AuthContext, env: RunnerEnvironment, row: NetworkRecord, verb: str
    ) -> None:
        from app.domain.audit import AuditTrail

        await AuditTrail(self.db, self.settings).record(
            "runner.network." + verb,
            workspace_ids=[env.workspace_id],
            actor_id=context.user.id,
            resource_type="runner_network",
            resource_id=row.id,
            metadata={"environment_id": str(env.id), "desired_revision": row.desired_revision},
        )

    async def create_ingress(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        environment_id: uuid.UUID,
        service_id: uuid.UUID,
        body: RunnerIngressInput,
    ) -> RunnerIngressOut:
        await self._owner(context, access, body)
        env = await self.environment(access.workspace.id, environment_id, lock=True)
        await self._identity(env)
        service = await self._service(env, service_id)
        if service.protocol != "TCP":
            raise ApiError(409, "NETWORK_PROTOCOL_UNSUPPORTED", "跨实例及公网网关目前仅支持TCP")
        source = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == body.source_instance_id,
                AgentInstance.workspace_id == env.workspace_id,
            )
        )
        if source is None:
            raise not_found()
        if source.id == env.owner_instance_id:
            raise ApiError(409, "SAME_INSTANCE_PRIVATE", "同实例使用私网服务，不需要跨实例入站规则")
        if not await has_capability(
            self.db, env.workspace_id, source.id, "runner.cross_agent_communication"
        ):
            raise ApiError(
                403, "SOURCE_CAPABILITY_REQUIRED", "来源实例需要已生效的跨智能体通信权限"
            )
        admitted = await self._gateway_ingress(env, service.id)
        await reserve_gateway_slot(self.db, kind="ingress")
        row = RunnerIngressRule(
            **self._fields(env, context),
            service_id=service_id,
            source_instance_id=source.id,
            expires_at=body.expires_at,
            apply_error=UNAVAILABLE,
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(context, env, row, "ingress_created")
        self._fence_gateway_ingress(service, admitted)
        await self.db.commit()
        return self.output(row, env)

    async def publish(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        environment_id: uuid.UUID,
        service_id: uuid.UUID,
        body: RunnerPublishInput,
    ) -> RunnerPublishedEndpointOut:
        await self._owner(context, access, body)
        env = await self.environment(access.workspace.id, environment_id, lock=True)
        await self._identity(env)
        service = await self._service(env, service_id)
        if service.protocol != "TCP":
            raise ApiError(409, "NETWORK_PROTOCOL_UNSUPPORTED", "跨实例及公网网关目前仅支持TCP")
        require_public_sources(self.settings, body.source_cidrs)
        admitted = await self._gateway_ingress(env, service.id)
        await reserve_gateway_slot(self.db, kind="published")
        row = RunnerPublishedEndpoint(
            **self._fields(env, context),
            service_id=service_id,
            expires_at=body.expires_at,
            source_cidrs=body.source_cidrs,
            apply_error=UNAVAILABLE,
            enforcement={
                "gateway_supported": False,
                "managed_sources_require_instance_identity": True,
                "max_runtime_lease_seconds": 60,
                "ipv6_supported": False,
            },
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(context, env, row, "published")
        self._fence_gateway_ingress(service, admitted)
        await self.db.commit()
        return self.output(row, env)

    async def revoke(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        environment_id: uuid.UUID,
        record_id: uuid.UUID,
        body: RunnerRevokeInput,
        *,
        kind: str,
    ) -> NetworkRecordOut:
        await self._owner(context, access, body)
        env = await self.environment(access.workspace.id, environment_id, lock=True)
        models: dict[str, type[NetworkRecord]] = {
            "service": RunnerService,
            "ingress": RunnerIngressRule,
            "published": RunnerPublishedEndpoint,
        }
        model = models[kind]
        row = await self.db.scalar(
            select(model)
            .where(
                model.id == record_id,
                model.workspace_id == env.workspace_id,
                model.environment_id == env.id,
            )
            .with_for_update()
        )
        if row is None:
            raise not_found()
        if row.state != "revoked":
            row.state, row.revoked_at, row.revoked_by = "revoked", now(), context.user.id
            row.desired_revision += 1
            row.apply_error = "REVOCATION_PENDING_CONFIRMATION"
            if isinstance(row, RunnerService):
                await self._invalidate_children(env, row.id, revoke=True, context=context)
            await self._audit(context, env, row, "revoked")
        await self.db.commit()
        return self.output(row, env)

    async def desired_policy(self, host_id: uuid.UUID) -> dict[str, Any]:
        """Internal authenticated-host hook; includes tombstones for reliable revocation."""
        host = await self.db.get(RunnerHost, host_id)
        if host is None:
            raise not_found()
        await self.db.scalar(
            select(Workspace)
            .where(Workspace.id == host.workspace_id)
            .with_for_update(key_share=True)
        )
        policy_revision = int(
            await self.db.scalar(
                text("SELECT nextval('workspace.runner_network_policy_revision_seq')")
            )
            or 1
        )
        result: dict[str, Any] = {
            "host_id": str(host_id),
            "gateway_supported": False,
            "services": [],
            "ingress_rules": [],
            "published_endpoints": [],
            "domains": [],
        }
        all_rules = list(
            await self.db.scalars(
                select(RunnerIngressRule).where(
                    RunnerIngressRule.workspace_id == host.workspace_id,
                    RunnerIngressRule.state.not_in(("revoked", "expired")),
                    RunnerIngressRule.expires_at > now(),
                )
            )
        )
        valid_rules = [
            r
            for r in all_rules
            if await has_capability(
                self.db, host.workspace_id, r.source_instance_id, "runner.cross_agent_communication"
            )
        ]
        environments = await self.db.scalars(
            select(RunnerEnvironment).where(
                RunnerEnvironment.host_id == host_id,
                RunnerEnvironment.workspace_id == host.workspace_id,
                RunnerEnvironment.runtime_backend == "kubernetes",
                RunnerEnvironment.owner_instance_id.is_not(None),
                RunnerEnvironment.state.not_in(("deleted", "archived", "pending_delete")),
            )
        )
        for env in environments:
            result["domains"].append(
                {
                    "workspace_id": str(env.workspace_id),
                    "instance_id": str(env.owner_instance_id),
                    "environment_id": str(env.id),
                    "allocation_id": str(
                        (env.runtime_metadata or {}).get("allocation_id") or env.id
                    ),
                    "generation": int((env.runtime_metadata or {}).get("generation", 1)),
                    "node_name": host.node_name,
                    "gateway_revision": policy_revision,
                    "gateway_ports": sorted(
                        {
                            r.gateway_port
                            for r in valid_rules
                            if r.source_instance_id == env.owner_instance_id
                        }
                    ),
                }
            )
        records: tuple[tuple[str, type[NetworkRecord]], ...] = (
            ("services", RunnerService),
            ("ingress_rules", RunnerIngressRule),
            ("published_endpoints", RunnerPublishedEndpoint),
        )
        for key, model in records:
            rows = await self.db.scalars(
                select(model).where(
                    model.host_id == host_id, model.workspace_id == host.workspace_id
                )
            )
            for row in rows:
                env = await self.environment(host.workspace_id, row.environment_id)
                generation = int((env.runtime_metadata or {}).get("generation", 0))
                if row.desired_generation != generation and row.state not in ("revoked", "expired"):
                    # The scan above is unlocked so polling never waits on owners. Re-read
                    # this row under its lock before writing, or a concurrent owner update
                    # could be overwritten and two contents would share one revision.
                    await self.db.scalar(
                        select(model)
                        .where(model.id == row.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if row.desired_generation != generation and row.state not in (
                        "revoked",
                        "expired",
                    ):
                        row.desired_generation = generation
                        row.desired_revision += 1
                        row.state = "approved_pending_apply"
                out = self.output(row, env).model_dump(mode="json")
                out.update(
                    generation=row.desired_generation
                    if row.state in ("revoked", "expired")
                    else generation,
                    backend=env.runtime_backend,
                    instance_id=str(row.owner_instance_id),
                    allocation_id=str((env.runtime_metadata or {}).get("allocation_id") or env.id),
                    node_name=host.node_name,
                )
                if row.host_id != env.host_id or row.owner_instance_id != env.owner_instance_id:
                    out["state"] = "revoked"
                if isinstance(row, RunnerService):
                    endpoints = await self._rows(RunnerPublishedEndpoint, env)
                    out.update(
                        namespace="oc-" + row.owner_instance_id.hex,
                        service_name="svc-" + row.id.hex,
                        gateway_ingress=any(r.service_id == row.id for r in valid_rules)
                        or any(
                            isinstance(e, RunnerPublishedEndpoint)
                            and e.service_id == row.id
                            and admits_gateway(e)
                            for e in endpoints
                        ),
                    )
                result[key].append(out)
        await self.db.flush()
        return result

    async def acknowledge(
        self,
        host_id: uuid.UUID,
        record_id: uuid.UUID,
        revision: int,
        *,
        generation: int,
        success: bool,
        error: str | None = None,
    ) -> bool:
        """Trusted host/controller only. Never expose via user/session authentication.

        The caller authenticates the host and validates a real API server receipt.
        Public/cross-instance enforcement has no supported ACK path yet.
        """
        for model in NETWORK_MODELS:
            row = await self.db.scalar(
                select(model)
                .where(model.id == record_id, model.host_id == host_id)
                .with_for_update()
            )
            if row is None:
                continue
            env = await self.environment(row.workspace_id, row.environment_id)
            expected = int((env.runtime_metadata or {}).get("generation", 0))
            if generation != row.desired_generation:
                return False
            if env.owner_instance_id != row.owner_instance_id or not ack_allowed(
                host_id,
                env.host_id,
                revision,
                row.desired_revision,
                generation,
                expected,
                high_risk=model is not RunnerService,
            ):
                return False
            if env.runtime_backend != "kubernetes":
                return False
            if not success:
                row.apply_error = (error or "NETWORK_APPLY_FAILED")[:200]
                if row.state != "revoked":
                    row.state = "apply_failed"
            else:
                row.applied_revision, row.applied_generation = revision, generation
                row.apply_error = None
                if row.state not in ("revoked", "expired"):
                    row.state = (
                        "expired" if row.expires_at and row.expires_at <= now() else "active"
                    )
            await self.db.flush()
            return True
        return False
