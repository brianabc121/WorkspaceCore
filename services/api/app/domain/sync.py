"""外部改动回到工作台：Webhook Inbox 的处理、周期对账与 Webhook 注册。

规范第 10 节；Sprint 3 契约第 9、10 节。

两条路径互为补充：
- **Webhook**（快，但 Vikunja 约 2 秒内重试 6 次后就丢弃）：Inbox 里的事件由 Worker 领取，按事件里的
  ID 读取 Vikunja 的**当前**状态写入投影，所以重复、乱序的事件不会写入旧数据；
  读到 404 记 tombstone。
- **周期对账**（兜底）：每轮做增量（上次以来更新过的任务），距上次全量超过
  FULL_SYNC_INTERVAL 再做全量（覆盖删除与丢失的事件），全量之后检查并补注册 Webhook。

失败处理：暂时性错误按指数退避加抖动重试，超过次数上限进 dead_letter；Token 被拒属于永久失败，
不再自动重试，集成标为 unauthorized 提示管理员（规范第 10 节）。
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
from app.domain import events, github_sync, notifications, projection, sync_lock
from app.domain.connectors import (
    ConnectorRuntime,
    integration_status_after,
    rate_limited_until,
    webhook_context,
)
from app.integrations.base import ConnectorError
from app.integrations.github import PROVIDER as GITHUB
from app.integrations.vikunja import TaskConnector, VikunjaClient, WebhookRegistrar
from app.models import Integration, WebhookInboxEvent
from app.repositories import inbox
from app.repositories import integrations as integration_repo
from app.repositories import projection as projection_repo

logger = logging.getLogger("app.sync")

# 注册时订阅的事件（契约第 9 节）
WEBHOOK_EVENTS = (
    "task.created",
    "task.updated",
    "task.deleted",
    "project.updated",
    "project.deleted",
)
WEBHOOK_PATH = "/api/v1/webhooks/vikunja/{integration_id}"
# 领取后的租约：处理一个事件只需要几秒，进程中途退出后租约到期即可被重新领取
LEASE = timedelta(minutes=5)
BATCH_SIZE = 20
RETRY_BASE = timedelta(seconds=30)
RETRY_MAX = timedelta(hours=1)
# 对账覆盖的集成：未测试（pending）、已停用、Token 被拒的不自动同步
SYNCABLE = ("active", "unavailable")
# 永久失败：重试也不会成功
_PERMANENT = frozenset({"VALIDATION_FAILED", "NOT_SUPPORTED", "CONFLICT"})


@dataclass
class SyncContext:
    """Worker 作业共用的资源（app/jobs/worker.py 在启动时创建）。"""

    sessions: async_sessionmaker[AsyncSession]
    runtime: ConnectorRuntime
    redis: Redis
    settings: Settings


def retry_delay(attempts: int, retry_after: int | None = None) -> timedelta:
    """第 n 次失败后的等待时间：30 秒起指数增长，上限 1 小时，±20% 抖动。"""
    base: timedelta = min(RETRY_BASE * 2 ** max(attempts - 1, 0), RETRY_MAX)
    delay: timedelta = base * random.uniform(0.8, 1.2)  # noqa: S311 - 退避抖动，不用于安全用途
    if retry_after is not None:
        delay = max(delay, timedelta(seconds=retry_after))
    return delay


def _client(runtime: ConnectorRuntime, integration: Integration, account: Any) -> VikunjaClient:
    return runtime.vikunja(integration, account)


def _healthy(integration: Integration) -> None:
    if integration.status == "unavailable":
        integration.status = "active"


# ---------------------------------------------------------------- Webhook Inbox


async def process_inbox(ctx: SyncContext, *, limit: int = BATCH_SIZE) -> int:
    """领取一批到期的事件逐个处理；返回处理的数量。"""
    async with ctx.sessions() as db:
        claimed = await inbox.claim(db, now=datetime.now(UTC), lease=LEASE, limit=limit)
        ids = [event.id for event in claimed]
        await db.commit()
    for event_id in ids:
        await process_event(ctx, event_id)
    return len(ids)


async def process_event(ctx: SyncContext, event_id: uuid.UUID) -> None:
    async with ctx.sessions() as db:
        event = await inbox.get(db, event_id)
        if event is None or event.status != "processing":
            await db.rollback()
            return
        integration = await integration_repo.get_by_id(db, event.integration_id)
        account = await integration_repo.get_account(db, integration.id) if integration else None
        if (
            integration is None
            or account is None
            or integration.status
            not in {
                "active",
                "unavailable",
                "unauthorized",
            }
        ):
            # 未测试或已停用的集成：事件不再处理，重新测试 / 启用后的全量同步会补齐
            _finish(event, "completed", "integration_inactive")
            await db.commit()
            return
        old_status = integration.status
        try:
            client = _client(ctx.runtime, integration, account)
            await apply_event(db, integration, client, event.event_type, event.payload_json)
        except ConnectorError as error:
            _failed(event, integration, error, max_attempts=ctx.settings.webhook_max_attempts)
        except ApiError as error:
            # 凭证无法解密（密钥更换或缺失）：重试也不会成功
            _finish(event, "dead_letter", error.code)
        except Exception as error:
            # 代码缺陷等意外错误：记下类型后按暂时性错误重试，最终进 dead_letter
            logger.exception(
                "webhook event failed unexpectedly",
                extra={"event_id": str(event.id), "error_type": type(error).__name__},
            )
            _retry_or_dead(event, type(error).__name__, ctx.settings.webhook_max_attempts, None)
        else:
            _healthy(integration)
            _finish(event, "completed", None)
        await notifications.integration_status_changed(db, integration, old_status)
        await db.commit()


def _finish(event: WebhookInboxEvent, status: str, error: str | None) -> None:
    event.status = status
    event.error_text = error
    event.next_retry_at = None
    event.processed_at = datetime.now(UTC)


def _retry_or_dead(
    event: WebhookInboxEvent, error: str, max_attempts: int, retry_after: int | None
) -> None:
    if event.attempts >= max_attempts:
        _finish(event, "dead_letter", f"{error}:max_attempts")
        return
    event.status = "retry"
    event.error_text = error
    event.next_retry_at = datetime.now(UTC) + retry_delay(event.attempts, retry_after)


def _failed(
    event: WebhookInboxEvent, integration: Integration, error: ConnectorError, *, max_attempts: int
) -> None:
    integration.status = integration_status_after(error, integration.status)
    code = error.reason or error.kind
    if error.kind == "AUTH_FAILED" and not error.resource_level:
        _finish(event, "dead_letter", "token_rejected")  # 永久失败：等管理员更换 Token
    elif error.kind in _PERMANENT:
        _finish(event, "dead_letter", code)
    else:
        _retry_or_dead(event, code, max_attempts, error.retry_after)


def _gone(error: ConnectorError) -> bool:
    """资源已不存在，或 Token 已无权访问它：投影里不再显示。"""
    return error.kind == "NOT_FOUND" or (error.kind == "AUTH_FAILED" and error.resource_level)


async def apply_event(
    db: AsyncSession,
    integration: Integration,
    client: TaskConnector,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """按事件里的 ID 读 Vikunja 的当前状态写入投影；事件内容本身不写入。"""
    events.mark(db, integration.workspace_id, "tasks", "projects", "overview")
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    if event_type.startswith("task.") and isinstance(task_id, int):
        try:
            task = await client.get_task(task_id)
        except ConnectorError as error:
            if not _gone(error):
                raise
            await projection_repo.tombstone_external(
                db,
                integration_id=integration.id,
                resource_type=projection_repo.TASK,
                external_id=str(task_id),
            )
            return
        await _ensure_project(db, integration, client, task.project_id)
        await projection.save_task(db, integration, task)
    elif event_type.startswith("project.") and isinstance(project_id, int):
        try:
            project = await client.get_project(project_id)
        except ConnectorError as error:
            if not _gone(error):
                raise
            await projection_repo.tombstone_external(
                db,
                integration_id=integration.id,
                resource_type=projection_repo.PROJECT,
                external_id=str(project_id),
            )
            await projection_repo.tombstone_project_tasks(
                db, integration_id=integration.id, project_external_ids=[str(project_id)]
            )
            return
        await projection.save_project(db, integration, project)
    # 其他事件（没有订阅的、缺少 ID 的）不需要处理


async def _ensure_project(
    db: AsyncSession, integration: Integration, client: TaskConnector, project_id: int
) -> None:
    """新项目里的任务可能先于项目本身到达：先补上项目，任务才能关联到它。"""
    existing = await projection_repo.get_by_external(
        db,
        integration_id=integration.id,
        resource_type=projection_repo.PROJECT,
        external_id=str(project_id),
    )
    if existing is not None and existing.deleted_at is None:
        return
    try:
        project = await client.get_project(project_id)
    except ConnectorError as error:
        if _gone(error):
            return  # 项目已删除或无权访问：任务照常保存，只是不关联项目
        raise
    await projection.save_project(db, integration, project)


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
        try:
            client = _client(ctx.runtime, integration, account)
        except ApiError as error:
            logger.error(
                "sync skipped: credential unreadable",
                extra={"integration_id": str(integration_id), "error_type": error.code},
            )
            return "skipped"
        full = force_full or await _full_due(db, integration_id, ctx.settings)
        events.mark(db, integration.workspace_id, "tasks", "projects")
        old_status = integration.status
        try:
            if full:
                await projection.full_sync(db, integration, client)
            else:
                await projection.incremental_sync(db, integration, client)
        except ConnectorError as error:
            integration.status = integration_status_after(error, integration.status)
            await notifications.integration_status_changed(db, integration, old_status)
            await db.commit()
            logger.warning(
                "sync failed",
                extra={"integration_id": str(integration_id), "error_type": error.kind},
            )
            return "failed"
        _healthy(integration)
        await notifications.integration_status_changed(db, integration, old_status)
        await db.commit()
        if full:
            await register_webhooks(db, ctx, integration, client)
        return "full" if full else "incremental"


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
    cursor = await integration_repo.get_cursor(db, integration_id, projection.FULL_SYNC)
    if cursor is None or cursor.last_success_at is None:
        return True
    age = datetime.now(UTC) - cursor.last_success_at
    return age >= timedelta(seconds=settings.full_sync_interval_seconds)


# ---------------------------------------------------------------- Webhook 注册


async def register_for(ctx: SyncContext, integration_id: uuid.UUID) -> str:
    """测试连接通过后由 API 投递：只注册 Webhook，不同步（API 已经做过首次全量同步）。"""
    async with ctx.sessions() as db:
        integration = await integration_repo.get_by_id(db, integration_id)
        account = await integration_repo.get_account(db, integration_id) if integration else None
        if (
            integration is None
            or account is None
            or integration.status not in SYNCABLE
            or integration.provider == GITHUB  # V1 不注册 GitHub Webhook
        ):
            return "skipped"
        try:
            client = _client(ctx.runtime, integration, account)
        except ApiError:
            return "skipped"
        result = await register_webhooks(db, ctx, integration, client)
        return str(result["mode"])


def webhook_target(settings: Settings, integration_id: uuid.UUID) -> str | None:
    base = settings.webhook_base_url.rstrip("/")
    return base + WEBHOOK_PATH.format(integration_id=integration_id) if base else None


async def register_webhooks(
    db: AsyncSession, ctx: SyncContext, integration: Integration, client: WebhookRegistrar
) -> dict[str, Any]:
    """为每个未归档项目注册（或确认已有）指向本集成的 Webhook；结果记在 settings_json.webhooks。

    target_url 含集成 ID，所以已有同一地址的 Webhook 就是本集成之前注册的（密钥不会变）。
    没有 projects_webhooks 权限（401）时停止，只靠周期对账；个别项目没有写权限（403）时跳过该项目。
    """
    target = webhook_target(ctx.settings, integration.id)
    projects = await projection_repo.list_projects(
        db,
        workspace_id=integration.workspace_id,
        integration_id=integration.id,
        include_archived=False,
        q=None,
        offset=0,
        limit=1000,
    )
    result: dict[str, Any]
    if target is None:
        result = {"mode": "unavailable", "registered_projects": 0, "reason": "not_configured"}
    elif not integration.webhook_secret_ref:
        result = {"mode": "unavailable", "registered_projects": 0, "reason": "no_secret"}
    else:
        result = await _register(
            ctx, integration, client, target, [int(p.external_id) for p in projects]
        )
    integration.settings_json = {**integration.settings_json, "webhooks": result}
    await db.commit()
    return result


async def _register(
    ctx: SyncContext,
    integration: Integration,
    client: WebhookRegistrar,
    target: str,
    project_ids: list[int],
) -> dict[str, Any]:
    secret = (
        ctx.runtime.credentials()
        .resolve(integration.webhook_secret_ref or "", context=webhook_context(integration.id))
        .get_secret_value()
    )
    registered = 0
    skipped = 0
    for project_id in project_ids:
        try:
            hooks = await client.list_webhooks(project_id)
            if not any(hook.target_url == target for hook in hooks):
                await client.create_webhook(
                    project_id, target_url=target, events=WEBHOOK_EVENTS, secret=secret
                )
            registered += 1
        except ConnectorError as error:
            if error.kind == "AUTH_FAILED" and not error.resource_level:
                return {
                    "mode": "unavailable",
                    "registered_projects": registered,
                    "reason": "missing_permission",
                }
            skipped += 1
            logger.warning(
                "webhook registration failed",
                extra={"integration_id": str(integration.id), "error_type": error.kind},
            )
    if registered == 0 and project_ids:
        return {"mode": "unavailable", "registered_projects": 0, "reason": "registration_failed"}
    return {
        "mode": "auto",
        "registered_projects": registered,
        "reason": "partial" if skipped else None,
    }


# ---------------------------------------------------------------- 清理


async def purge(ctx: SyncContext, *, completed_retention: timedelta = timedelta(days=7)) -> None:
    """删除过期的幂等记录与早已处理完的 Inbox 事件（dead_letter 保留，便于排查）。"""
    now = datetime.now(UTC)
    async with ctx.sessions() as db:
        expired = await integration_repo.purge_expired_idempotency(db, now)
        events = await inbox.purge_completed(db, before=now - completed_retention)
        await db.commit()
    logger.info("purge finished", extra={"idempotency": expired, "inbox_events": events})
