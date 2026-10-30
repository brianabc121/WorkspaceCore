"""首页聚合的模块数据（契约 ADR 第 8、9 节）。

每个模块由一个 provider 提供数据：读本地投影 / 缓存，不同步等待外部服务。
所有 provider 并发执行，各自有超时，整体有总耗时上限；单个 provider 出错或超时
只把该模块标为 unavailable（reason 为 error / timeout），不影响其他模块和整个接口。
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

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
    """任务模块：Sprint 3 接入 Vikunja 后改为读取本地任务投影。"""

    name = "tasks"
    timeout_seconds = 2.0

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        return ModuleResult(
            status=unavailable("not_configured", retryable=False),
            summary={"pending_tasks": None, "active_projects": None},
        )


def default_providers() -> list[ModuleProvider]:
    return [TasksProvider()]


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
