"""Database-owned desired network policy; runtime acknowledgments are separate facts.

Cross-instance/public enforcement is intentionally unavailable until a real gateway
can prove source identity, NAT hairpin protection, TCP/UDP lease termination and
revocation. No API request can manufacture an applied policy or connect address.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, TypeVar, overload

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess, not_found, require_role
from app.domain.runner_capabilities import has_capability
from app.domain.step_up import verify_step_up
from app.models import AgentInstance, RunnerEnvironment, RunnerHost
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
    RunnerNetworkOut,
    RunnerPublishedEndpointOut,
    RunnerPublishInput,
    RunnerRevokeInput,
    RunnerServiceCreate,
    RunnerServiceOut,
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

    @staticmethod
    @overload
    def output(row: RunnerService, env: RunnerEnvironment) -> RunnerServiceOut: ...

    @staticmethod
    @overload
    def output(row: RunnerIngressRule, env: RunnerEnvironment) -> RunnerIngressOut: ...

    @staticmethod
    @overload
    def output(
        row: RunnerPublishedEndpoint, env: RunnerEnvironment
    ) -> RunnerPublishedEndpointOut: ...

    @staticmethod
    @overload
    def output(row: NetworkRecord, env: RunnerEnvironment) -> NetworkRecordOut: ...

    @staticmethod
    def output(row: NetworkRecord, env: RunnerEnvironment) -> NetworkRecordOut:
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
        if out.state == "active" and (not identity_current or row.applied_generation != generation):
            out.state, out.apply_error = "approved_pending_apply", "NETWORK_RUNTIME_UNCONFIRMED"
        # Private service use is same-owner only. Gateway paths stay pending.
        if (
            isinstance(row, RunnerService)
            and out.state == "active"
            and row.applied_revision == row.desired_revision
        ):
            out.connect_host, out.connect_port = row.desired_dns, row.port
            out.accepting_new_connections = True
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
        await self._service(env, service_id)
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
        row = RunnerIngressRule(
            **self._fields(env, context),
            service_id=service_id,
            source_instance_id=source.id,
            expires_at=body.expires_at,
            apply_error=UNAVAILABLE,
        )
        self.db.add(row)
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
        await self._service(env, service_id)
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
        await self.db.commit()
        return self.output(row, env)

    async def desired_policy(self, host_id: uuid.UUID) -> dict[str, Any]:
        """Internal authenticated-host hook; includes tombstones for reliable revocation."""
        host = await self.db.get(RunnerHost, host_id)
        if host is None:
            raise not_found()
        result: dict[str, Any] = {
            "host_id": str(host_id),
            "gateway_supported": False,
            "services": [],
            "ingress_rules": [],
            "published_endpoints": [],
        }
        records: tuple[tuple[str, type[NetworkRecord]], ...] = (
            ("services", RunnerService),
            ("ingress_rules", RunnerIngressRule),
            ("published_endpoints", RunnerPublishedEndpoint),
        )
        for key, model in records:
            rows = await self.db.scalars(
                select(model)
                .where(model.host_id == host_id, model.workspace_id == host.workspace_id)
                .with_for_update()
            )
            for row in rows:
                env = await self.environment(host.workspace_id, row.environment_id)
                generation = int((env.runtime_metadata or {}).get("generation", 0))
                if row.desired_generation != generation and row.state not in ("revoked", "expired"):
                    row.desired_generation = generation
                    row.desired_revision += 1
                    row.state = "approved_pending_apply"
                out = self.output(row, env).model_dump(mode="json")
                out.update(
                    generation=generation,
                    backend=env.runtime_backend,
                    instance_id=str(row.owner_instance_id),
                    allocation_id=str((env.runtime_metadata or {}).get("allocation_id") or env.id),
                    node_name=host.node_name,
                )
                if row.host_id != env.host_id or row.owner_instance_id != env.owner_instance_id:
                    out["state"] = "revoked"
                if isinstance(row, RunnerService):
                    out.update(
                        namespace="oc-" + row.owner_instance_id.hex,
                        service_name="svc-" + row.id.hex,
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
