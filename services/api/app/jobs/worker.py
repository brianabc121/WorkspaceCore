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
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings
from arq.cron import CronJob
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging, dict_config
from app.core.resources import create_engine
from app.domain import sync
from app.domain.connectors import ConnectorRuntime
from app.integrations.http import CircuitBreaker
from app.jobs.queue import PROCESS_WEBHOOKS, REGISTER_WEBHOOKS, SYNC_INTEGRATION

logger = logging.getLogger("app.worker")

LOG_CONFIG = dict_config(get_settings().log_level)


async def heartbeat(ctx: dict[str, Any]) -> str:
    """定时写一条日志，便于确认 Worker 在正常调度。"""
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
    """清理过期的幂等记录与已处理完的 Inbox 事件。"""
    await sync.purge(_sync(ctx))


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    ctx["engine"] = engine
    # ctx["redis"] 是 ARQ 的连接（redis.asyncio.Redis 的子类），同步锁与 API 共用同一个 Redis
    ctx["sync"] = sync.SyncContext(
        sessions=async_sessionmaker(engine, expire_on_commit=False),
        runtime=ConnectorRuntime(settings, CircuitBreaker()),
        redis=ctx["redis"],
        settings=settings,
    )
    logger.info("worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
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
        cron(heartbeat, minute=set(range(0, 60, 5))),
        # 兜底：API 投递失败或事件到了重试时间时，也会在一分钟内被处理
        cron(process_webhooks, second=30),
        cron(reconcile, minute=set(range(2, 60, 5)), timeout=900),
        cron(purge, minute=17),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    on_startup = startup
    on_shutdown = shutdown
    health_check_interval = 60
