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
from app.domain import alerts, automations, events, notifications, sync
from app.domain.connectors import ConnectorRuntime
from app.domain.operations import WORKER_HEARTBEAT_KEY, DeploymentHealth, ServiceCache
from app.integrations.http import CircuitBreaker
from app.jobs.queue import PROCESS_WEBHOOKS, REGISTER_WEBHOOKS, SYNC_INTEGRATION

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
    """清理过期的幂等记录、已处理完的 Inbox 事件与过期的通知。"""
    context = _sync(ctx)
    await sync.purge(context)
    async with context.sessions() as db:
        retention = timedelta(days=context.settings.notification_retention_days)
        removed = await notifications.purge(db, retention=retention)
        await db.commit()
    if removed:
        logger.info("notifications purged", extra={"count": removed})


async def reconcile_automations(ctx: dict[str, Any]) -> int:
    """n8n 执行记录对账（Sprint 5 契约第 6.4 节）：状态、错误摘要与结果通知。"""
    context = _sync(ctx)
    return await automations.reconcile_all(context.sessions, context.settings, context.runtime)


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
    ]
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(heartbeat, second=0, run_at_startup=True),
        # 兜底：API 投递失败或事件到了重试时间时，也会在一分钟内被处理
        cron(process_webhooks, second=30),
        cron(reconcile, minute=RECONCILE_MINUTES, timeout=900),
        cron(purge, minute=17),
        cron(check_services, second=45, timeout=60),
        cron(reconcile_automations, second=15, timeout=120),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    on_startup = startup
    on_shutdown = shutdown
    health_check_interval = 60
