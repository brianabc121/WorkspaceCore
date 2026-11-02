"""外部改动回到工作台：GitHub 的周期对账与手动同步，以及过期记录的清理。

规范第 10 节；Sprint 4 契约第 7 节。T-0082 移除 Vikunja 后，没有 Webhook 收件箱与 Webhook 注册了：
项目与任务由工作台自己管理（app/domain/tasks.py）。

周期对账：每轮做增量，距上次全量超过 FULL_SYNC_INTERVAL 再做全量（覆盖删除）。
Token 被拒属于永久失败，不再自动重试，集成标为 unauthorized 提示管理员（规范第 10 节）。
"""

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events, github_sync, notifications, sync_lock
from app.domain.connectors import ConnectorRuntime, integration_status_after, rate_limited_until
from app.integrations.base import ConnectorError
from app.integrations.github import PROVIDER as GITHUB
from app.models import Integration
from app.repositories import integrations as integration_repo

logger = logging.getLogger("app.sync")

RETRY_BASE = timedelta(seconds=30)
RETRY_MAX = timedelta(hours=1)
# 对账覆盖的集成：未测试（pending）、已停用、Token 被拒的不自动同步
SYNCABLE = ("active", "unavailable")


@dataclass
class SyncContext:
    """Worker 作业共用的资源（app/jobs/worker.py 在启动时创建）。"""

    sessions: async_sessionmaker[AsyncSession]
    runtime: ConnectorRuntime
    redis: Redis
    settings: Settings


def retry_delay(attempts: int, retry_after: int | None = None) -> timedelta:
    """第 n 次失败后的等待时间：30 秒起指数增长，上限 1 小时，±20% 抖动（IM 收件箱也用）。"""
    base: timedelta = min(RETRY_BASE * 2 ** max(attempts - 1, 0), RETRY_MAX)
    delay: timedelta = base * random.uniform(0.8, 1.2)  # noqa: S311 - 退避抖动，不用于安全用途
    if retry_after is not None:
        delay = max(delay, timedelta(seconds=retry_after))
    return delay


def _healthy(integration: Integration) -> None:
    if integration.status == "unavailable":
        integration.status = "active"


# ---------------------------------------------------------------- 周期对账与手动同步


async def reconcile_all(ctx: SyncContext) -> int:
    """定时对账：逐个同步可用的集成；正在同步的（锁被占用）跳过。返回同步的数量。"""
    async with ctx.sessions() as db:
        ids = await integration_repo.list_ids_with_status(db, SYNCABLE)
    done = 0
    for integration_id in ids:
        owner = sync_lock.new_owner("cron")
        if not await sync_lock.acquire(ctx.redis, integration_id, owner):
            continue
        try:
            await sync_integration(ctx, integration_id)
            done += 1
        except Exception as error:
            # 一个集成的意外错误不影响其他集成；下一轮对账会再试
            logger.exception(
                "reconcile failed unexpectedly",
                extra={"integration_id": str(integration_id), "error_type": type(error).__name__},
            )
        finally:
            await sync_lock.release(ctx.redis, integration_id, owner)
    return done


async def run_manual_sync(ctx: SyncContext, integration_id: uuid.UUID, owner: str) -> bool:
    """手动同步作业（API 已加锁并投递）：接手锁后做一次全量同步。"""
    if not await sync_lock.adopt(ctx.redis, integration_id, owner):
        return False  # 另一个同步正在进行
    try:
        await sync_integration(ctx, integration_id, force_full=True)
    finally:
        await sync_lock.release(ctx.redis, integration_id, owner)
    return True


async def sync_integration(
    ctx: SyncContext, integration_id: uuid.UUID, *, force_full: bool = False
) -> str:
    """同步一个集成；返回 full / incremental / skipped / failed。调用方负责加锁。"""
    async with ctx.sessions() as db:
        integration = await integration_repo.get_by_id(db, integration_id)
        account = await integration_repo.get_account(db, integration_id) if integration else None
        if integration is None or account is None or integration.status not in SYNCABLE:
            return "skipped"
        # 同步之后数据与新鲜度都可能变了（包括批量 tombstone）；只在提交成功时发布
        events.mark(db, integration.workspace_id, "integrations", "operations", "overview")
        if integration.provider == GITHUB:
            events.mark(db, integration.workspace_id, "developer")
            return await _sync_github(db, ctx, integration, account, force_full=force_full)
        return "skipped"  # T-0082 起只有 GitHub 需要同步


async def _sync_github(
    db: AsyncSession,
    ctx: SyncContext,
    integration: Integration,
    account: Any,
    *,
    force_full: bool,
) -> str:
    """GitHub：只读同步（Sprint 4 契约第 7 节），没有 Webhook；限流期间直接跳过这一轮。"""
    limited = rate_limited_until(integration)
    if limited is not None and limited > datetime.now(UTC):
        return "skipped"
    try:
        client = ctx.runtime.github(integration, account)
    except ApiError as error:
        logger.error(
            "sync skipped: credential unreadable",
            extra={"integration_id": str(integration.id), "error_type": error.code},
        )
        return "skipped"
    full = force_full or await _full_due(db, integration.id, ctx.settings)
    old_status = integration.status
    try:
        await github_sync.sync(db, integration, client, full=full)
    except ConnectorError as error:
        integration.status = integration_status_after(error, integration.status)
        await notifications.integration_status_changed(db, integration, old_status)
        if error.kind == "RATE_LIMITED":
            wait = timedelta(seconds=error.retry_after or 60)
            await notifications.integration_rate_limited(db, integration, wait)
        await db.commit()
        logger.warning(
            "github sync failed",
            extra={"integration_id": str(integration.id), "error_type": error.kind},
        )
        return "failed"
    _healthy(integration)
    await notifications.integration_status_changed(db, integration, old_status)
    await db.commit()
    return "full" if full else "incremental"


async def _full_due(db: AsyncSession, integration_id: uuid.UUID, settings: Settings) -> bool:
    cursor = await integration_repo.get_cursor(db, integration_id, github_sync.FULL_SYNC)
    if cursor is None or cursor.last_success_at is None:
        return True
    age = datetime.now(UTC) - cursor.last_success_at
    return age >= timedelta(seconds=settings.full_sync_interval_seconds)


# ---------------------------------------------------------------- 清理


async def purge(ctx: SyncContext) -> None:
    """删除过期的幂等记录。"""
    now = datetime.now(UTC)
    async with ctx.sessions() as db:
        expired = await integration_repo.purge_expired_idempotency(db, now)
        await db.commit()
    logger.info("purge finished", extra={"idempotency": expired})
