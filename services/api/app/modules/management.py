"""应用中心的管理投影（T-0210，方案 docs/v2/app-center/ 契约第 2、3 节）。

- 读（列表、详情、操作记录）：owner / admin；只读注册表、数据库与健康缓存，不探测外部服务；
- 检测（health-check）：owner / admin；只探测这个模块清单声明的服务，限时、限并发、限频；
- 重试同步（reconcile）：owner；与启用 / 停用共用工作空间的模块操作锁，按当前目标执行钩子。

四类状态分开：启用（enabled）、部署（ready / missing）、健康（health）、外部配置同步（sync）。
不输出 Settings 的值、凭据、导入路径、内部地址或异常正文。
"""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit_query import AuditFilters, AuditQueryService
from app.domain.operations import DeploymentHealth
from app.domain.policy import WorkspaceAccess, has_role
from app.domain.ratelimit import WindowLimiter
from app.models import ModuleSyncAttempt
from app.modules import registry
from app.modules.base import HOST_API, CronSpec, ModuleManifest, ServiceCheckSpec
from app.modules.locks import advisory_lock, module_operation_lock
from app.modules.service import (
    ModuleService,
    ModuleState,
    busy_error,
    check_state_version,
    module_states,
)
from app.modules.sync import IN_FLIGHT_LIMIT_SECONDS, ModuleSyncCoordinator
from app.schemas.audit import AuditLogView
from app.schemas.common import Page
from app.schemas.module_management import (
    HealthState,
    ModuleActions,
    ModuleCapabilities,
    ModuleCronOut,
    ModuleHealthOut,
    ModuleManagementList,
    ModuleManagementOut,
    ModuleManagementSummary,
    ModuleRelationOut,
    ModuleServiceOut,
    ModuleSyncOut,
    ModuleToolOut,
)
from app.schemas.operations import ServiceStatusOut
from app.schemas.workspaces import Role

# 显式检测：每个工作空间的每个模块每分钟最多 3 次；单个服务最多 10 秒；同时最多 3 个
HEALTH_CHECKS_PER_MINUTE = 3
HEALTH_CHECK_MAX_SECONDS = 10.0
HEALTH_CHECK_CONCURRENCY = 3
_NEEDS_ATTENTION_SYNC = ("failed", "unknown")
_NEEDS_ATTENTION_HEALTH = ("down", "degraded")
_NO_RETRY_SYNC = ("succeeded", "pending", "running")


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "没有这个模块")


def _manifest(key: str) -> ModuleManifest:
    manifest = registry.get(key)
    if manifest is None:
        raise _not_found()
    return manifest


def schedule_summary(schedule: Mapping[str, Any]) -> str:
    """arq cron 参数的中文说明（没写的时间字段表示“每个”，second 默认 0）。"""
    if any(schedule.get(field) is not None for field in ("month", "day", "weekday")):
        return "按计划运行"
    hour, minute = schedule.get("hour"), schedule.get("minute")
    if isinstance(hour, int) and isinstance(minute, int):
        return f"每天 {hour:02d}:{minute:02d}"
    if hour is None and isinstance(minute, int):
        return f"每小时第 {minute} 分钟"
    if hour is None and minute is None:
        return "每分钟"
    return "按计划运行"


def _cron_out(spec: CronSpec) -> ModuleCronOut:
    # 只给任务名，不给导入路径
    return ModuleCronOut(
        id=spec.target.rpartition(":")[2], schedule_summary=schedule_summary(spec.schedule)
    )


def aggregate_health(checks: Sequence[ServiceStatusOut]) -> HealthState:
    states = [check.status for check in checks]
    if not states:
        return "not_applicable"
    if all(state == "not_configured" for state in states):
        return "not_configured"
    if "down" in states:
        return "down"
    if "degraded" in states or "not_configured" in states:
        return "degraded"
    if all(state == "ok" for state in states):
        return "ok"
    return "unknown"


def _health_out(checks: list[ServiceStatusOut], stale: bool) -> ModuleHealthOut:
    times = [check.checked_at for check in checks if check.checked_at is not None]
    return ModuleHealthOut(
        state=aggregate_health(checks),
        # 有多个服务时取最早的那次：整体结果不比它更新
        checked_at=min(times) if times else None,
        stale=stale,
        checks=checks,
    )


def _offline_status(check: ServiceCheckSpec, state: str, reason: str) -> ServiceStatusOut:
    return ServiceStatusOut.model_validate(
        {
            "id": check.service_id,
            "name": check.name,
            "kind": "integration",
            "provider": check.service_id,
            "status": state,
            "reason": reason,
            "checked_at": None,
        }
    )


def sync_view(
    manifest: ModuleManifest,
    state: ModuleState,
    latest: ModuleSyncAttempt | None,
    now: datetime,
) -> ModuleSyncOut:
    """当前目标版本的同步结果：旧版本的记录只是历史，不代表当前目标。"""
    if manifest.on_toggle is None:
        return ModuleSyncOut(
            state="not_required",
            state_version=state.version,
            attempt_id=None,
            target_enabled=None,
            trigger=None,
            reason=None,
            started_at=None,
            finished_at=None,
        )
    if latest is None or latest.state_version != state.version:
        return ModuleSyncOut(
            state="unrecorded",
            state_version=state.version,
            attempt_id=None,
            target_enabled=None,
            trigger=None,
            reason=None,
            started_at=None,
            finished_at=None,
        )
    sync_state, reason = latest.state, latest.reason
    started = latest.started_at or latest.created_at
    if sync_state in ("pending", "running") and (
        started is None or now - started > timedelta(seconds=IN_FLIGHT_LIMIT_SECONDS)
    ):
        # 执行期限早过了还没有结果：进程中断，不再当作“执行中”
        sync_state, reason = "unknown", "interrupted"
    return ModuleSyncOut.model_validate(
        {
            "state": sync_state,
            "state_version": latest.state_version,
            "attempt_id": latest.id,
            "target_enabled": latest.target_enabled,
            "trigger": latest.trigger,
            "reason": reason,
            "started_at": latest.started_at,
            "finished_at": latest.finished_at,
        }
    )


class ModuleManagementService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: Any,
        health: DeploymentHealth,
        redis: Redis | None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.health = health
        self.redis = redis
        self.modules = ModuleService(db, settings, runtime)

    # ------------------------------------------------------------ 读

    async def list(self, access: WorkspaceAccess) -> ModuleManagementList:
        now = datetime.now(UTC)
        states = await module_states(self.db, access.workspace.id)
        attempts = await self._latest_attempts(access.workspace.id)
        items = [self._out(access, m, states, attempts.get(m.key), now) for m in registry.MODULES]
        return ModuleManagementList(
            items=items,
            summary=ModuleManagementSummary(
                registered=len(items),
                enabled=sum(1 for item in items if item.module.enabled),
                attention=sum(1 for item in items if item.attention),
            ),
            generated_at=now,
        )

    async def get(self, access: WorkspaceAccess, key: str) -> ModuleManagementOut:
        manifest = _manifest(key)
        states = await module_states(self.db, access.workspace.id)
        attempts = await self._latest_attempts(access.workspace.id, key)
        return self._out(access, manifest, states, attempts.get(key), datetime.now(UTC))

    async def activity(
        self, access: WorkspaceAccess, key: str | None, *, cursor: str | None, limit: int
    ) -> Page[AuditLogView]:
        """模块的操作记录（启用 / 停用、同步与重试）：精确匹配 resource_type 与 resource_id。

        key 为 None 时列出所有模块（包括已经移除的模块）的记录。
        """
        if key is not None:
            _manifest(key)
        filters = AuditFilters(resource_type="module", resource_id=key)
        return await AuditQueryService(self.db, self.settings).list(
            access, filters, cursor=cursor, limit=limit
        )

    # ------------------------------------------------------------ 检测与重试

    async def check_health(self, access: WorkspaceAccess, key: str) -> ModuleHealthOut:
        manifest = _manifest(key)
        states = await module_states(self.db, access.workspace.id)
        if not states[key].enabled:
            raise ApiError(409, "MODULE_DISABLED_FOR_CHECK", "模块已停用，启用后才能检测")
        checks = manifest.service_checks
        if not checks:
            return _health_out([], stale=False)
        if registry.missing_requirements(manifest, self.settings):
            # 没部署：不请求任何外部地址
            return _health_out(
                [_offline_status(c, "not_configured", "not_deployed") for c in checks],
                stale=False,
            )
        await self._check_rate(access, key)
        gate = asyncio.Semaphore(HEALTH_CHECK_CONCURRENCY)

        async def run(check: ServiceCheckSpec) -> ServiceStatusOut:
            async with gate:
                return await self.health.fresh_module_check(
                    check, max_seconds=HEALTH_CHECK_MAX_SECONDS
                )

        results = await asyncio.gather(*(run(check) for check in checks))
        return _health_out(list(results), stale=False)

    async def reconcile(
        self,
        access: WorkspaceAccess,
        key: str,
        expected_state_version: int,
        client_ip: str | None,
    ) -> ModuleManagementOut:
        """按当前目标重新执行同步钩子；不改启用状态，不自动启用依赖。"""
        manifest = _manifest(key)
        workspace_id = access.workspace.id
        async with advisory_lock(self.db, module_operation_lock(workspace_id)) as acquired:
            if not acquired:
                raise busy_error()
            state = (await module_states(self.db, workspace_id))[key]
            check_state_version(state.version, expected_state_version)
            await ModuleSyncCoordinator(self.db, self.settings, self.runtime).run(
                manifest,
                workspace_id,
                state_version=state.version,
                enabled=state.enabled,
                trigger="retry",
                actor_id=access.user_id,
                client_ip=client_ip,
            )
        return await self.get(access, key)

    # ------------------------------------------------------------ 内部

    async def _check_rate(self, access: WorkspaceAccess, key: str) -> None:
        if self.redis is None:
            return
        retry = await WindowLimiter(self.redis, 60).hit(
            f"module-health:{access.workspace.id}:{key}", HEALTH_CHECKS_PER_MINUTE
        )
        if retry is not None:
            raise ApiError(
                429,
                "RATE_LIMITED",
                "检测太频繁，请稍后再试",
                details={"retry_after_seconds": retry},
                headers={"Retry-After": str(retry)},
            )

    async def _latest_attempts(
        self, workspace_id: uuid.UUID, key: str | None = None
    ) -> dict[str, ModuleSyncAttempt]:
        stmt = (
            select(ModuleSyncAttempt)
            .where(ModuleSyncAttempt.workspace_id == workspace_id)
            .ext(distinct_on(ModuleSyncAttempt.module_key))
            # 版本高的在前：当前目标（版本最高）的记录不会被更晚写入的旧版本记录挡住
            .order_by(
                ModuleSyncAttempt.module_key,
                ModuleSyncAttempt.state_version.desc(),
                ModuleSyncAttempt.created_at.desc(),
                ModuleSyncAttempt.id.desc(),
            )
            # 刚由协调器写过的记录可能还在这个会话里：按数据库里的最新值读
            .execution_options(populate_existing=True)
        )
        if key is not None:
            stmt = stmt.where(ModuleSyncAttempt.module_key == key)
        return {row.module_key: row for row in await self.db.scalars(stmt)}

    def _cached_health(self, manifest: ModuleManifest) -> ModuleHealthOut:
        """只读缓存：没有探测器 not_applicable；没部署 not_configured；没检测过 unknown。"""
        missing = bool(registry.missing_requirements(manifest, self.settings))
        checks: list[ServiceStatusOut] = []
        stale = False
        for check in manifest.service_checks:
            cached = self.health.cached_module_check(check)
            if cached is not None:
                value, expired = cached
                checks.append(value)
                stale = stale or expired
            elif missing:
                checks.append(_offline_status(check, "not_configured", "not_deployed"))
            else:
                checks.append(_offline_status(check, "unknown", "not_checked"))
        return _health_out(checks, stale)

    def _out(
        self,
        access: WorkspaceAccess,
        manifest: ModuleManifest,
        states: Mapping[str, ModuleState],
        latest: ModuleSyncAttempt | None,
        now: datetime,
    ) -> ModuleManagementOut:
        state = states[manifest.key]
        module = self.modules.out(manifest, state, access)
        health = self._cached_health(manifest)
        sync = sync_view(manifest, state, latest, now)
        is_owner = has_role(access.role, Role.owner)
        attention = sync.state in _NEEDS_ATTENTION_SYNC or (
            module.enabled and (not module.ready or health.state in _NEEDS_ATTENTION_HEALTH)
        )

        def relation(key: str) -> ModuleRelationOut:
            other = registry.BY_KEY[key]
            return ModuleRelationOut(key=key, name=other.name, enabled=states[key].enabled)

        specs = sorted(
            (spec for spec in registry.tool_specs() if spec.name in module.tools),
            key=lambda spec: spec.name,
        )
        return ModuleManagementOut(
            module=module,
            host_api=HOST_API,
            capabilities=ModuleCapabilities(
                tools=[ModuleToolOut(name=s.name, risk=s.risk) for s in specs],
                widgets=list(manifest.widgets),
                crons=[_cron_out(spec) for spec in manifest.crons],
                event_topics=list(manifest.event_topics),
                depends_on=[relation(k) for k in manifest.depends_on if k in registry.BY_KEY],
                dependents=[relation(k) for k in registry.dependents(manifest.key)],
                services=[
                    ModuleServiceOut(id=c.service_id, name=c.name) for c in manifest.service_checks
                ],
                has_sync_hook=manifest.on_toggle is not None,
            ),
            health=health,
            sync=sync,
            attention=attention,
            actions=ModuleActions(
                can_toggle=is_owner,
                can_retry_sync=is_owner
                and manifest.on_toggle is not None
                and sync.state not in _NO_RETRY_SYNC,
                can_check_health=module.enabled and bool(manifest.service_checks),
            ),
        )
