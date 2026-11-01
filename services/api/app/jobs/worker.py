"""ARQ Worker 入口：

    arq app.jobs.worker.WorkerSettings --custom-log-dict app.jobs.worker.LOG_CONFIG

`--custom-log-dict` 让 arq 自己的日志也输出为 JSON。
健康检查：`arq app.jobs.worker.WorkerSettings --check`
（Worker 每 health_check_interval 秒写一次心跳键）。

ARQ 目前处于仅维护状态（见 docs/decisions/2026-10-29-backend-stack.md），
作业逻辑不要依赖 ARQ 特有的 API，以便将来替换。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings
from arq.cron import CronJob
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging, dict_config
from app.core.resources import create_engine
from app.domain import (
    alerts,
    approval_cards,
    automations,
    events,
    im,
    im_projection,
    notifications,
    sync,
)
from app.domain.ai_tools import AiToolService
from app.domain.connectors import ConnectorRuntime
from app.domain.operations import WORKER_HEARTBEAT_KEY, DeploymentHealth, ServiceCache
from app.integrations.http import CircuitBreaker
from app.jobs.queue import (
    PROCESS_IM_INBOX,
    PROCESS_WEBHOOKS,
    REGISTER_WEBHOOKS,
    SEND_APPROVAL_CARDS,
    SYNC_INTEGRATION,
)

logger = logging.getLogger("app.worker")

LOG_CONFIG = dict_config(get_settings().log_level)
# 对账的分钟：错开整点（第 2 分钟起），间隔由 RECONCILE_INTERVAL_MINUTES 决定
_interval = get_settings().reconcile_interval_minutes
RECONCILE_MINUTES = set(range(2 % _interval, 60, _interval))


async def heartbeat(ctx: dict[str, Any]) -> str:
    """每分钟写一次心跳时间；运维页按它判断 Worker 是否在正常调度（Sprint 5 契约第 3.1 节）。"""
    now = datetime.now(UTC)
    await ctx["redis"].set(WORKER_HEARTBEAT_KEY, now.isoformat())
    if now.minute % 5 == 0:
        logger.info("worker heartbeat")
    return "ok"


def _sync(ctx: dict[str, Any]) -> sync.SyncContext:
    context: sync.SyncContext = ctx["sync"]
    return context


async def process_webhooks(ctx: dict[str, Any]) -> int:
    """处理 Webhook Inbox 里到期的事件（收到 Webhook 后由 API 投递，另有每分钟一次的兜底）。"""
    return await sync.process_inbox(_sync(ctx))


async def sync_integration(ctx: dict[str, Any], integration_id: str, owner: str) -> bool:
    """手动同步（POST /api/v1/integrations/{id}/sync 投递）。"""
    return await sync.run_manual_sync(_sync(ctx), uuid.UUID(integration_id), owner)


async def register_webhooks(ctx: dict[str, Any], integration_id: str) -> str:
    """测试连接通过后为每个项目注册 Webhook。"""
    return await sync.register_for(_sync(ctx), uuid.UUID(integration_id))


async def reconcile(ctx: dict[str, Any]) -> int:
    """周期对账：增量 + 定期全量（规范第 10 节）。"""
    return await sync.reconcile_all(_sync(ctx))


async def purge(ctx: dict[str, Any]) -> None:
    """清理过期的幂等记录、已处理完的 Inbox 事件、过期的通知与 IM 消息投影。"""
    context = _sync(ctx)
    await sync.purge(context)
    async with context.sessions() as db:
        retention = timedelta(days=context.settings.notification_retention_days)
        removed = await notifications.purge(db, retention=retention)
        im_messages, im_events = await im_projection.purge(
            db, message_retention=timedelta(days=context.settings.im_message_retention_days)
        )
        await db.commit()
    if removed:
        logger.info("notifications purged", extra={"count": removed})
    if im_messages or im_events:
        logger.info("im purged", extra={"messages": im_messages, "inbox_events": im_events})


async def reconcile_automations(ctx: dict[str, Any]) -> int:
    """n8n 执行记录对账（Sprint 5 契约第 6.4 节）：状态、错误摘要与结果通知。"""
    context = _sync(ctx)
    return await automations.reconcile_all(context.sessions, context.settings, context.runtime)


async def provision_im(ctx: dict[str, Any]) -> int:
    """IM 账号尚未就绪的实例再试一次（V2 OC-3）；没有部署 OpenIM 时什么也不做。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return 0
    async with context.sessions() as db:
        return await im.provision_pending(db, client)


def _im(ctx: dict[str, Any]) -> im_projection.ImContext | None:
    """没有部署 OpenIM 时为 None。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return None
    return im_projection.ImContext(sessions=context.sessions, client=client, redis=ctx["redis"])


async def process_im_inbox(ctx: dict[str, Any]) -> int:
    """处理 OpenIM 回调收件箱（回调写入后由 API 投递，另有每分钟一次的兜底）。"""
    im_ctx = _im(ctx)
    return await im_projection.process_inbox(im_ctx) if im_ctx is not None else 0


async def reconcile_im(ctx: dict[str, Any]) -> int:
    """IM 增量对账（每 30 秒）：按管理员账号的 newest_seq 补齐有变化的会话。"""
    im_ctx = _im(ctx)
    return await im_projection.reconcile(im_ctx) if im_ctx is not None else 0


async def reconcile_im_full(ctx: dict[str, Any]) -> int:
    """IM 全量对账（每天一次）：重新拉每个会话最近 1000 条，补上中间缺的。"""
    im_ctx = _im(ctx)
    return await im_projection.reconcile(im_ctx, full=True) if im_ctx is not None else 0


async def send_approval_cards(ctx: dict[str, Any], execution_id: str) -> int:
    """新的待审批：由工作空间的通知账号给 owner / admin 发 IM 审批卡片（V2 OC-5）。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return 0
    async with context.sessions() as db:
        return await approval_cards.send_cards(db, client, ctx["redis"], uuid.UUID(execution_id))


async def expire_ai_approvals(ctx: dict[str, Any]) -> int:
    """15 分钟没人处理的待审批默认拒绝（expired，V2 OC-5）。"""
    context = _sync(ctx)
    async with context.sessions() as db:
        return await AiToolService(db, context.settings, context.runtime, ctx["redis"]).expire_due()


async def check_services(ctx: dict[str, Any]) -> list[str]:
    """服务健康告警（S5-02）：api、Prometheus、Grafana 变为 down 或恢复时通知管理员。"""
    health: DeploymentHealth = ctx["health"]
    items = [await health.api_internal(), *await health.monitoring()]
    return await alerts.evaluate(ctx["redis"], _sync(ctx).sessions, items)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    ctx["engine"] = engine
    # ctx["redis"] 是 ARQ 的连接（redis.asyncio.Redis 的子类），同步锁与 API 共用同一个 Redis
    bus = events.EventBus(ctx["redis"])
    runtime = ConnectorRuntime(settings, CircuitBreaker())
    ctx["events"] = bus
    ctx["sync"] = sync.SyncContext(
        sessions=async_sessionmaker(engine, expire_on_commit=False, info=bus.session_info()),
        runtime=runtime,
        redis=ctx["redis"],
        settings=settings,
    )
    ctx["health"] = DeploymentHealth(ctx["redis"], settings, runtime, {}, ServiceCache())
    logger.info("worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
    bus = ctx.get("events")
    if bus is not None:
        await bus.drain()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    logger.info("worker stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        heartbeat,
        func(process_webhooks, name=PROCESS_WEBHOOKS),
        func(sync_integration, name=SYNC_INTEGRATION, timeout=900),
        func(register_webhooks, name=REGISTER_WEBHOOKS),
        func(process_im_inbox, name=PROCESS_IM_INBOX),
        func(send_approval_cards, name=SEND_APPROVAL_CARDS),
    ]
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(heartbeat, second=0, run_at_startup=True),
        # 兜底：API 投递失败或事件到了重试时间时，也会在一分钟内被处理
        cron(process_webhooks, second=30),
        cron(reconcile, minute=RECONCILE_MINUTES, timeout=900),
        cron(purge, minute=17),
        cron(check_services, second=45, timeout=60),
        cron(reconcile_automations, second=15, timeout=120),
        cron(provision_im, minute=set(range(4, 60, 5)), second=40, timeout=120),
        cron(process_im_inbox, second=50, timeout=120),
        cron(reconcile_im, second={5, 35}, timeout=60),
        cron(expire_ai_approvals, second=25, timeout=60),
        cron(reconcile_im_full, hour=19, minute=23, timeout=1800),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    on_startup = startup
    on_shutdown = shutdown
    health_check_interval = 60
