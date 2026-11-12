"""Critical owner decisions for runtime capabilities, separate from tool execution identity."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.auth import AuthContext
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess, not_found, require_role
from app.integrations.http import CircuitBreaker
from app.models import AgentInstance, AgentSettings, AiToolExecution, RunnerEnvironment, RunnerHost
from app.models.runner_capability import (
    CAPABILITIES,
    RunnerCapabilityGrant,
    RunnerCapabilityRequest,
)
from app.schemas.runner_capabilities import (
    RunnerCapabilitiesOut,
    RunnerCapabilityGrantOut,
    RunnerCapabilityPendingOut,
    RunnerCapabilityRequestInput,
    RunnerCapabilityRevokeInput,
)
from app.schemas.workspaces import Role

if TYPE_CHECKING:
    from app.schemas.ai_tools import PendingApproval

POLICY_VERSION = 1
DEFAULT_LIMIT = 5


def now() -> datetime:
    return datetime.now(UTC)


class ContainerApprovalRequired(Exception):
    code = "APPROVAL_REQUIRED"

    def __init__(self, approval_id: uuid.UUID, *, deduplicated: bool = False) -> None:
        self.approval_id = approval_id
        self.deduplicated = deduplicated
        self.message = "已保有5个运行环境，需要owner批准免数量上限权限"
        super().__init__(self.message)


async def has_capability(
    db: AsyncSession, workspace_id: uuid.UUID, instance_id: uuid.UUID, capability: str
) -> bool:
    if capability not in CAPABILITIES:
        return False
    instance = await db.scalar(
        select(AgentInstance).where(
            AgentInstance.id == instance_id,
            AgentInstance.workspace_id == workspace_id,
            AgentInstance.status != "disabled",
            AgentInstance.runner_enabled.is_(True),
        )
    )
    if instance is None:
        return False
    enabled = await db.get(AgentSettings, workspace_id)
    if enabled is not None and not enabled.agents_enabled:
        return False
    return bool(
        await db.scalar(
            select(RunnerCapabilityGrant.id)
            .where(
                RunnerCapabilityGrant.workspace_id == workspace_id,
                RunnerCapabilityGrant.instance_id == instance_id,
                RunnerCapabilityGrant.capability == capability,
                RunnerCapabilityGrant.state == "active",
                RunnerCapabilityGrant.revoked_at.is_(None),
                RunnerCapabilityGrant.policy_version == POLICY_VERSION,
                RunnerCapabilityGrant.applied_revision == RunnerCapabilityGrant.desired_revision,
                (RunnerCapabilityGrant.expires_at.is_(None))
                | (RunnerCapabilityGrant.expires_at > now()),
            )
            .limit(1)
        )
    )


async def held_count(db: AsyncSession, workspace_id: uuid.UUID, instance_id: uuid.UUID) -> int:
    # Unknown/failed residuals remain held until an explicit archived/deleted result.
    return int(
        await db.scalar(
            select(func.count())
            .select_from(RunnerEnvironment)
            .where(
                RunnerEnvironment.workspace_id == workspace_id,
                RunnerEnvironment.owner_instance_id == instance_id,
                RunnerEnvironment.state.not_in(("archived", "deleted")),
            )
        )
        or 0
    )


class RunnerCapabilityService:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db, self.settings, self.redis = db, settings, redis

    async def request(
        self,
        instance: AgentInstance,
        *,
        capability: str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> "PendingApproval":
        # Serializes differing reasons/expiries too: there is one pending decision per capability.
        from app.domain.ai_tools import AiToolService, ToolCallError, _pending
        from app.schemas.ai_tools import TOOLS

        locked_instance = await self.db.scalar(
            select(AgentInstance)
            .where(
                AgentInstance.id == instance.id, AgentInstance.workspace_id == instance.workspace_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_instance is None or locked_instance.status == "disabled":
            raise ToolCallError("AGENT_DISABLED", "实例已停用或已删除")
        instance = locked_instance
        if not instance.runner_enabled:
            raise ToolCallError("FORBIDDEN_CAPABILITY", "管理员还没有给你开运行环境权限")
        service = AiToolService(
            self.db, self.settings, ConnectorRuntime(self.settings, CircuitBreaker()), self.redis
        )
        requester = await service.requester(instance)
        spec = TOOLS["runner_request_capability"]
        denied = service.check(requester, spec)
        settings = await self.db.get(AgentSettings, instance.workspace_id)
        if settings is not None and not settings.agents_enabled:
            raise ToolCallError("AGENT_DISABLED", "总开关已关闭")
        if denied:
            raise ToolCallError(denied, "这个实例没有权限申请运行环境能力")
        parsed = RunnerCapabilityRequestInput.model_validate(
            dict(capability=capability, reason=reason, expires_at=expires_at)
        )
        if parsed.expires_at is not None and parsed.expires_at <= now():
            raise ToolCallError("VALIDATION_FAILED", "授权有效期必须在未来")
        requests = (
            await self.db.execute(
                select(RunnerCapabilityRequest, AiToolExecution)
                .join(AiToolExecution, AiToolExecution.id == RunnerCapabilityRequest.approval_id)
                .where(
                    RunnerCapabilityRequest.instance_id == instance.id,
                    RunnerCapabilityRequest.capability == capability,
                    AiToolExecution.status == "pending_approval",
                )
            )
        ).all()
        for _request, execution in requests:
            if execution.expires_at is not None and execution.expires_at > now():
                return _pending(execution, deduplicated=True)
            execution.status, execution.arguments = "expired", None
            execution.version += 1
        arguments = parsed.model_dump(mode="json")
        label = (
            "免除数量上限"
            if capability == CAPABILITIES[0]
            else "允许跨智能体通信（仍需目标入站同意）"
        )
        row = service._row(
            requester,
            spec,
            arguments,
            stored=arguments,
            status="pending_approval",
            summary=f"为「{instance.name}」{label}",
        )
        row.id = uuid.uuid4()
        row.expires_at = now() + timedelta(minutes=15)
        self.db.add(row)
        await self.db.flush()
        self.db.add(
            RunnerCapabilityRequest(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                approval_id=row.id,
                capability=capability,
                reason=reason,
                expires_at=parsed.expires_at,
                policy_version=POLICY_VERSION,
            )
        )
        await service._notify_requested(row)
        await self.db.flush()
        return _pending(row, deduplicated=False)

    async def approve_execution(
        self, context: AuthContext, row: AiToolExecution, instance: AgentInstance | None
    ) -> str:
        # Use the verified human owner, never the registering user's substituted AuthContext.
        await require_role(
            self.db, user_id=context.user.id, workspace_id=row.workspace_id, minimum=Role.owner
        )
        if (
            row.decided_by != context.user.id
            or row.risk_level != "critical"
            or row.status != "executing"
        ):
            raise ApiError(403, "FORBIDDEN", "需要owner审批凭证")
        request = await self.db.scalar(
            select(RunnerCapabilityRequest).where(
                RunnerCapabilityRequest.approval_id == row.id,
                RunnerCapabilityRequest.workspace_id == row.workspace_id,
                RunnerCapabilityRequest.instance_id == row.instance_id,
            )
        )
        if request is None or request.policy_version != POLICY_VERSION:
            raise ApiError(409, "POLICY_CHANGED", "权限申请已过期，请重新申请")
        if request.expires_at is not None and request.expires_at <= now():
            raise ApiError(409, "GRANT_EXPIRED", "授权有效期已过")
        if (
            instance is None
            or instance.workspace_id != row.workspace_id
            or instance.status == "disabled"
            or not instance.runner_enabled
        ):
            raise ApiError(403, "FORBIDDEN_CAPABILITY", "实例运行环境权限已关闭")
        from app.domain.ai_tools import AiToolService

        service = AiToolService(
            self.db, self.settings, ConnectorRuntime(self.settings, CircuitBreaker()), self.redis
        )
        who = await service.requester(instance)
        from app.schemas.ai_tools import TOOLS

        denied = service.check(who, TOOLS["runner_request_capability"])
        enabled = await self.db.get(AgentSettings, instance.workspace_id)
        if denied or (enabled is not None and not enabled.agents_enabled):
            raise ApiError(403, denied or "AGENT_DISABLED", "申请资格已变化")
        args = service.unsealed(row)
        expected = dict(
            capability=request.capability,
            reason=request.reason,
            expires_at=request.expires_at.isoformat() if request.expires_at else None,
        )
        # Datetime formatting may differ after PostgreSQL normalization; compare parsed values.

        if RunnerCapabilityRequestInput.model_validate(
            args
        ) != RunnerCapabilityRequestInput.model_validate(expected):
            raise ApiError(409, "ARGUMENTS_TAMPERED", "申请范围与审批不一致")
        grant = RunnerCapabilityGrant(
            workspace_id=row.workspace_id,
            instance_id=instance.id,
            approval_id=row.id,
            requested_by=row.acting_user_id,
            approved_by=context.user.id,
            capability=request.capability,
            reason=request.reason,
            expires_at=request.expires_at,
            policy_version=request.policy_version,
            scope={
                "workspace_id": str(row.workspace_id),
                "instance_id": str(instance.id),
                "target_inbound_required": request.capability == CAPABILITIES[1],
            },
        )
        self.db.add(grant)
        await self.db.flush()
        if grant.capability == CAPABILITIES[0]:
            # Docker count policy is enforced entirely by this database gate. Kubernetes
            # requires its separate ResourceQuota/controller acknowledgment before activation.
            non_docker = await self.db.scalar(
                select(RunnerHost.id)
                .where(
                    RunnerHost.workspace_id == grant.workspace_id, RunnerHost.backend != "docker"
                )
                .limit(1)
            )
            if non_docker is None:
                await self.mark_applied(grant.id, grant.desired_revision)
                return "免数量上限权限已生效；资源预算仍有效，请使用原幂等键重试"
        return "权限已批准，等待策略应用确认；请使用原幂等键重试"

    async def mark_applied(self, grant_id: uuid.UUID, revision: int) -> bool:
        """Trusted policy-controller hook. Stale acknowledgments cannot reactivate revocations."""
        row = await self.db.scalar(
            select(RunnerCapabilityGrant)
            .where(RunnerCapabilityGrant.id == grant_id)
            .with_for_update()
        )
        if row is None or revision != row.desired_revision:
            return False
        row.applied_revision = revision
        if row.state == "approved_pending_apply" and (
            row.expires_at is None or row.expires_at > now()
        ):
            row.state = "active"
        await self.db.flush()
        return True

    async def list(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> RunnerCapabilitiesOut:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
            )
        )
        if instance is None:
            raise not_found()
        rows = list(
            await self.db.scalars(
                select(RunnerCapabilityGrant)
                .where(
                    RunnerCapabilityGrant.workspace_id == access.workspace.id,
                    RunnerCapabilityGrant.instance_id == instance_id,
                )
                .order_by(RunnerCapabilityGrant.approved_at.desc())
            )
        )
        grants = []
        for row in rows:
            out = RunnerCapabilityGrantOut.model_validate(row)
            if (
                row.state in ("active", "approved_pending_apply")
                and row.expires_at
                and row.expires_at <= now()
            ):
                out.state = "expired"
            grants.append(out)
        pending_rows = (
            await self.db.execute(
                select(RunnerCapabilityRequest, AiToolExecution)
                .join(AiToolExecution, AiToolExecution.id == RunnerCapabilityRequest.approval_id)
                .where(
                    RunnerCapabilityRequest.workspace_id == access.workspace.id,
                    RunnerCapabilityRequest.instance_id == instance_id,
                    AiToolExecution.status == "pending_approval",
                    AiToolExecution.expires_at > now(),
                )
            )
        ).all()
        pending_requests = [
            RunnerCapabilityPendingOut(
                action_id=execution.id,
                capability=request.capability,
                reason=request.reason,
                expires_at=execution.expires_at,
            )
            for request, execution in pending_rows
        ]
        return RunnerCapabilitiesOut(
            instance_id=instance_id,
            held_count=await held_count(self.db, access.workspace.id, instance_id),
            unlimited_active=await has_capability(
                self.db, access.workspace.id, instance_id, CAPABILITIES[0]
            ),
            grants=grants,
            pending_approval_ids=[p.action_id for p in pending_requests],
            pending_requests=pending_requests,
        )

    async def revoke(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        grant_id: uuid.UUID,
        body: RunnerCapabilityRevokeInput,
    ) -> RunnerCapabilityGrantOut:
        from app.domain.step_up import verify_step_up

        await require_role(
            self.db, user_id=context.user.id, workspace_id=access.workspace.id, minimum=Role.owner
        )
        await verify_step_up(self.redis, context.user, body.password)
        row = await self.db.scalar(
            select(RunnerCapabilityGrant)
            .where(
                RunnerCapabilityGrant.id == grant_id,
                RunnerCapabilityGrant.instance_id == instance_id,
                RunnerCapabilityGrant.workspace_id == access.workspace.id,
            )
            .with_for_update()
        )
        if row is None:
            raise not_found()
        if row.revoked_at is None:
            row.state, row.revoked_by, row.revoked_at, row.revoke_reason = (
                "revoked",
                context.user.id,
                now(),
                body.reason,
            )
            row.desired_revision += 1
        await self.db.commit()
        return RunnerCapabilityGrantOut.model_validate(row)


class RunnerQuotaService(RunnerCapabilityService):
    async def check_count(self, instance: AgentInstance) -> None:
        # Serialize count decisions without blocking result/wakeup foreign keys.
        # The instance primary key is never changed by admission.
        await self.db.scalar(
            select(AgentInstance.id)
            .where(AgentInstance.id == instance.id)
            .with_for_update(key_share=True)
        )
        if await has_capability(self.db, instance.workspace_id, instance.id, CAPABILITIES[0]):
            return
        count = await held_count(self.db, instance.workspace_id, instance.id)
        explicit_limit = (instance.runner_limits or {}).get("envs")
        if (
            isinstance(explicit_limit, int | float)
            and not isinstance(explicit_limit, bool)
            and explicit_limit < DEFAULT_LIMIT
            and count + 1 > explicit_limit
        ):
            from app.domain.runner import RunnerError

            raise RunnerError("QUOTA_EXCEEDED", "保有环境数量会超过管理员为该实例设置的限额")
        if count < DEFAULT_LIMIT:
            return
        pending = await self.request(
            instance, capability=CAPABILITIES[0], reason="申请第6个保有运行环境，需要免除数量上限"
        )
        raise ContainerApprovalRequired(pending.action_id, deduplicated=pending.deduplicated)
