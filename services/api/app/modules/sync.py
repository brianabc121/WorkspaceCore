"""模块启用 / 停用之后的外部配置同步（应用中心 T-0210，方案 docs/v2/app-center/ 契约第 3 节）。

调用方必须已经持有这个工作空间的模块操作锁（``locks.module_operation_lock``），
并且启用状态已经提交：同步失败不回滚开关。

- 每次执行写一条同步记录（module_sync_attempts），先以 running 提交，再执行钩子；
- 钩子在单独的数据库会话里执行，全程限时（HOOK_TIMEOUT_SECONDS）：超时或被中断记为 unknown，
  不当作成功，也不承诺外部的副作用已经撤销；
- 只有钩子明确返回 HookOutcome.applied 才记 succeeded；返回 None（旧钩子）记 unknown；
- 结果与审计在独立的事务里写，钩子回滚不会丢掉记录；原因只记固定类别，不存异常正文；
- 遗留的 running / pending（进程崩溃）在下一次执行前改成 unknown / interrupted，
  旧版本的改成 superseded / stale_revision，不会被当作当前目标的结果。
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import events
from app.domain.audit import AuditTrail
from app.models import ModuleSyncAttempt, WorkspaceModule
from app.modules import registry
from app.modules.base import HookOutcome, ModuleManifest

logger = logging.getLogger("app.modules")

# 钩子的总时限（含等待新闻早报配置文件锁的时间）
HOOK_TIMEOUT_SECONDS = 15.0
# running / pending 超过这么久还没有结果，读的时候显示为 unknown / interrupted
IN_FLIGHT_LIMIT_SECONDS = 60.0
SyncTrigger = Literal["toggle", "retry"]
_IN_FLIGHT = ("pending", "running")
_OUTCOME_AUDIT = {
    "succeeded": "module.sync.succeeded",
    "failed": "module.sync.failed",
    "unknown": "module.sync.unknown",
}
_REASONS = {
    "hook_failed",
    "config_render_failed",
    "timeout",
    "interrupted",
    "stale_revision",
    "busy",
    "result_unverified",
    "not_deployed",
    "application_not_active",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _reason(value: str | None, fallback: str) -> str:
    """钩子给的原因只接受固定类别，其他文字一律换成 fallback（不把任意文字写进库）。"""
    return value if value in _REASONS else fallback


async def current_state_version(db: AsyncSession, workspace_id: uuid.UUID, key: str) -> int:
    version = await db.scalar(
        select(WorkspaceModule.state_version).where(
            WorkspaceModule.workspace_id == workspace_id, WorkspaceModule.module_key == key
        )
    )
    return int(version or 0)


class ModuleSyncCoordinator:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: Any) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime

    async def run(
        self,
        manifest: ModuleManifest,
        workspace_id: uuid.UUID,
        *,
        state_version: int,
        enabled: bool,
        trigger: SyncTrigger,
        actor_id: uuid.UUID | None,
        client_ip: str | None,
    ) -> ModuleSyncAttempt | None:
        """执行一次钩子并记录结果；模块没有钩子时返回 None（不写记录）。"""
        if manifest.on_toggle is None:
            return None
        key = manifest.key
        await self._close_stale(workspace_id, key, state_version)
        attempt = ModuleSyncAttempt(
            workspace_id=workspace_id,
            module_key=key,
            state_version=state_version,
            target_enabled=enabled,
            trigger=trigger,
            state="running",
            requested_by=actor_id,
            started_at=_now(),
        )
        self.db.add(attempt)
        await self.db.flush()
        if trigger == "retry":
            await self._audit("module.sync.retried", attempt, actor_id, client_ip)
        await self.db.commit()

        state, reason = await self._invoke(manifest.key, manifest.on_toggle, workspace_id, enabled)
        # 持有操作锁时目标不会变；仍然再读一次，旧版本的结果不能冒充当前目标
        if await current_state_version(self.db, workspace_id, key) != state_version:
            state, reason = "superseded", "stale_revision"
        attempt.state = state
        attempt.reason = reason
        attempt.finished_at = _now()
        action = _OUTCOME_AUDIT.get(state)
        if action is not None:
            await self._audit(action, attempt, actor_id, client_ip)
        # 其他打开着应用中心的页面刷新同步结果
        events.mark(self.db, workspace_id, "modules")
        await self.db.commit()
        return attempt

    async def _close_stale(self, workspace_id: uuid.UUID, key: str, state_version: int) -> None:
        """上一次执行没有写完结果（进程崩溃、被杀）：不能当作还在执行，也不能当成功。"""
        same = ModuleSyncAttempt.state_version == state_version
        await self.db.execute(
            update(ModuleSyncAttempt)
            .where(
                ModuleSyncAttempt.workspace_id == workspace_id,
                ModuleSyncAttempt.module_key == key,
                ModuleSyncAttempt.state.in_(_IN_FLIGHT),
            )
            .values(
                state=case((same, "unknown"), else_="superseded"),
                reason=case((same, "interrupted"), else_="stale_revision"),
                finished_at=_now(),
            )
            .execution_options(synchronize_session=False)
        )

    async def _invoke(
        self, key: str, target: str, workspace_id: uuid.UUID, enabled: bool
    ) -> tuple[str, str | None]:
        hook: Any = registry.resolve(target)
        # 钩子用自己的会话：超时或出错时整个丢掉，不影响写结果的这个会话
        session = AsyncSession(
            bind=self.db.bind, expire_on_commit=False, info=events.carried_info(self.db)
        )
        try:
            async with asyncio.timeout(HOOK_TIMEOUT_SECONDS):
                outcome = await hook(session, self.settings, self.runtime, workspace_id, enabled)
        except TimeoutError:
            logger.warning("module sync hook timed out", extra={"module_key": key})
            return "unknown", "timeout"
        except Exception as error:
            logger.warning(
                "module sync hook failed",
                extra={"module_key": key, "error_type": type(error).__name__},
            )
            return "failed", "hook_failed"
        finally:
            await _discard(session)
        if not isinstance(outcome, HookOutcome):
            return "unknown", "result_unverified"
        if outcome.state == "applied":
            return "succeeded", None
        if outcome.state == "not_required":
            return "not_required", _reason(outcome.reason, "application_not_active")
        return "failed", _reason(outcome.reason, "hook_failed")

    async def _audit(
        self,
        action: str,
        attempt: ModuleSyncAttempt,
        actor_id: uuid.UUID | None,
        client_ip: str | None,
    ) -> None:
        await AuditTrail(self.db, self.settings).record(
            action,
            workspace_ids=[attempt.workspace_id],
            actor_id=actor_id,
            resource_type="module",
            resource_id=attempt.module_key,
            metadata={
                "module": attempt.module_key,
                "state_version": attempt.state_version,
                "attempt_id": str(attempt.id),
                "target_enabled": attempt.target_enabled,
                "trigger": attempt.trigger,
                "reason": attempt.reason,
            },
            client_ip=client_ip,
        )


async def _discard(session: AsyncSession) -> None:
    try:
        await session.close()
    except Exception:  # 被取消的查询可能让连接处于未知状态：作废，连接池不再复用
        await session.invalidate()
