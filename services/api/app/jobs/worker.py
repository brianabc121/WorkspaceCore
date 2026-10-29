"""ARQ Worker 入口：

    arq app.jobs.worker.WorkerSettings --custom-log-dict app.jobs.worker.LOG_CONFIG

`--custom-log-dict` 让 arq 自己的日志也输出为 JSON。
健康检查：`arq app.jobs.worker.WorkerSettings --check`
（Worker 每 health_check_interval 秒写一次心跳键）。

ARQ 目前处于仅维护状态（见 docs/decisions/2026-10-29-backend-stack.md），
作业逻辑不要依赖 ARQ 特有的 API，以便将来替换。
"""

import logging
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from arq.cron import CronJob

from app.core.config import get_settings
from app.core.logging import configure_logging, dict_config

logger = logging.getLogger("app.worker")

LOG_CONFIG = dict_config(get_settings().log_level)


async def heartbeat(ctx: dict[str, Any]) -> str:
    """定时写一条日志，便于确认 Worker 在正常调度。"""
    logger.info("worker heartbeat")
    return "ok"


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().log_level)
    logger.info("worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [heartbeat]
    cron_jobs: ClassVar[list[CronJob]] = [cron(heartbeat, minute=set(range(0, 60, 5)))]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    on_startup = startup
    on_shutdown = shutdown
    health_check_interval = 60
