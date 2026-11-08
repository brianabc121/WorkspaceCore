"""新闻早报的定时任务（由模块清单登记到 worker）。"""

import logging
from typing import Any

from app.domain.sync import SyncContext
from app.modules.news import agent

logger = logging.getLogger("app.worker")


async def news_tick(ctx: dict[str, Any]) -> int:
    """方案 B 到点建任务、收早报、超时；补写配置文件；每天重新计算偏好（T-0153）。

    模块在打开早报的那个工作空间里停用时什么都不做（agent.tick 里检查）。
    """
    context: SyncContext = ctx["sync"]
    async with context.sessions() as db:
        stats = await agent.tick(db, context.settings, context.runtime, ctx["redis"])
    if any(stats.values()):
        logger.info("news tick", extra=stats)
    return sum(stats.values())
