"""首页聚合的模块数据（契约 ADR 第 8、9 节）。

每个模块由一个 provider 提供数据：读本地投影 / 缓存，不同步等待外部服务。
所有 provider 并发执行，各自有超时，整体有总耗时上限；单个 provider 出错或超时
只把该模块标为 unavailable（reason 为 error / timeout），不影响其他模块和整个接口。
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.connectors import sync_state
from app.integrations.vikunja import PROVIDER
from app.models import CalendarEvent, KnowledgeSource
from app.repositories import integrations as integration_repo
from app.repositories import projection as projection_repo
from app.schemas.dashboards import ModuleStatus, TaskBrief

logger = logging.getLogger("app.overview")


@dataclass(frozen=True)
class ModuleResult:
    status: ModuleStatus
    # 该模块提供的汇总值（键为 OverviewSummary 的字段名）；未知时不提供或为 None
    summary: dict[str, int | None] = field(default_factory=dict)
    today_tasks: list[TaskBrief] = field(default_factory=list)


class ModuleProvider(Protocol):
    name: str
    timeout_seconds: float

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult: ...


def unavailable(reason: str, *, retryable: bool) -> ModuleStatus:
    return ModuleStatus(status="unavailable", as_of=None, retryable=retryable, reason=reason)


class TasksProvider:
    """任务模块：读 Vikunja 的本地投影（Sprint 3 契约第 10 节），不访问 Vikunja。"""

    name = "tasks"
    timeout_seconds = 2.0

    def __init__(
        self,
        sessions: Callable[[], AsyncSession] | None = None,
        stale_after_seconds: int = 900,
    ) -> None:
        self._sessions = sessions
        self._stale_after = stale_after_seconds

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        if self._sessions is None:
            return _not_configured()
        async with self._sessions() as db:
            integration = await integration_repo.get_provider(db, workspace_id, PROVIDER)
            cursor = (
                await integration_repo.get_cursor(db, integration.id, projection_repo.TASK)
                if integration
                else None
            )
            state = sync_state(integration, cursor, stale_after_seconds=self._stale_after)
            if state.status == "not_configured":
                return _not_configured()
            if state.status not in {"ok", "stale"}:
                return ModuleResult(
                    status=ModuleStatus(
                        status="unauthorized" if state.status == "unauthorized" else "unavailable",
                        as_of=state.as_of,
                        retryable=state.status != "unauthorized",
                        reason=state.reason,
                    ),
                    summary={"pending_tasks": None, "active_projects": None},
                )
            tomorrow = datetime.now(UTC).date() + timedelta(days=1)
            pending, projects, today = await projection_repo.task_summary(
                db,
                workspace_id,
                due_until=datetime.combine(tomorrow, time.min, tzinfo=UTC),
                today_limit=TODAY_LIMIT,
            )
            titles = await projection_repo.titles_by_id(
                db, [p for row in today if (p := _project_uuid(row.metadata_json))]
            )
        return ModuleResult(
            status=ModuleStatus(
                status=state.status,
                as_of=state.as_of,
                retryable=True,
                reason=state.reason,
            ),
            summary={"pending_tasks": pending, "active_projects": projects},
            today_tasks=[
                TaskBrief(
                    id=str(row.id),
                    title=row.title,
                    due_at=_due(row.metadata_json),
                    project_title=titles.get(p)
                    if (p := _project_uuid(row.metadata_json))
                    else None,
                    source_url=row.source_url,
                )
                for row in today
            ],
        )


TODAY_LIMIT = 20


def _not_configured() -> ModuleResult:
    return ModuleResult(
        status=unavailable("not_configured", retryable=False),
        summary={"pending_tasks": None, "active_projects": None},
    )


def _project_uuid(meta: dict[str, object]) -> uuid.UUID | None:
    value = meta.get("project_resource_id")
    try:
        return uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _due(meta: dict[str, object]) -> datetime | None:
    value = meta.get("due_at")
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


class CalendarProvider:
    """日历模块（Sprint 4 契约第 9.1 节）：本地数据，能读就是 ok；组件自己调用 /calendar/events。"""

    name = "calendar"
    timeout_seconds = 2.0

    def __init__(self, sessions: Callable[[], AsyncSession] | None = None) -> None:
        self._sessions = sessions

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        if self._sessions is None:
            return ModuleResult(status=unavailable("not_configured", retryable=False))
        async with self._sessions() as db:
            await db.scalar(
                select(CalendarEvent.id).where(CalendarEvent.workspace_id == workspace_id).limit(1)
            )
        return ModuleResult(
            status=ModuleStatus(status="ok", as_of=datetime.now(UTC), retryable=True)
        )


class KnowledgeProvider:
    """知识模块：没有配置 AFFiNE 地址时 not_configured，否则 ok（不访问 AFFiNE）。"""

    name = "knowledge"
    timeout_seconds = 2.0

    def __init__(self, sessions: Callable[[], AsyncSession] | None = None) -> None:
        self._sessions = sessions

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        if self._sessions is None:
            return ModuleResult(status=unavailable("not_configured", retryable=False))
        async with self._sessions() as db:
            configured = await db.scalar(
                select(KnowledgeSource.id)
                .where(KnowledgeSource.workspace_id == workspace_id)
                .limit(1)
            )
        if configured is None:
            return ModuleResult(status=unavailable("not_configured", retryable=False))
        return ModuleResult(
            status=ModuleStatus(status="ok", as_of=datetime.now(UTC), retryable=True)
        )


def default_providers(
    sessions: Callable[[], AsyncSession] | None = None, stale_after_seconds: int = 900
) -> list[ModuleProvider]:
    return [
        TasksProvider(sessions, stale_after_seconds),
        CalendarProvider(sessions),
        KnowledgeProvider(sessions),
    ]


async def collect(
    providers: Sequence[ModuleProvider], workspace_id: uuid.UUID, *, total_timeout: float
) -> dict[str, ModuleResult]:
    async def run(provider: ModuleProvider) -> ModuleResult:
        return await asyncio.wait_for(
            provider.fetch(workspace_id), timeout=min(provider.timeout_seconds, total_timeout)
        )

    tasks = {provider.name: asyncio.create_task(run(provider)) for provider in providers}
    if not tasks:
        return {}
    _, pending = await asyncio.wait(tasks.values(), timeout=total_timeout)
    for task in pending:
        task.cancel()

    results: dict[str, ModuleResult] = {}
    for name, task in tasks.items():
        if task in pending:
            results[name] = ModuleResult(status=unavailable("timeout", retryable=True))
            continue
        error = task.exception()
        if isinstance(error, TimeoutError):
            results[name] = ModuleResult(status=unavailable("timeout", retryable=True))
        elif error is not None:
            # 只记录异常类型，避免把外部服务返回的内容写进日志
            logger.warning(
                "overview module failed",
                extra={"overview_module": name, "error_type": type(error).__name__},
            )
            results[name] = ModuleResult(status=unavailable("error", retryable=True))
        else:
            results[name] = task.result()
    return results
