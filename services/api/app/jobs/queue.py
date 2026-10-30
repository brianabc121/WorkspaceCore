"""后台作业的投递（API → Worker）。

只在这里接触 ARQ 的投递接口（backend-stack ADR：ARQ 仅维护，将来可能替换）；
领域代码依赖 `JobQueue`，测试里换成记录调用的假实现。
"""

from typing import Any, Protocol

from arq.connections import ArqRedis
from redis.asyncio import Redis

# Worker 注册的作业名（app/jobs/worker.py）
PROCESS_WEBHOOKS = "process_webhooks"
SYNC_INTEGRATION = "sync_integration"
REGISTER_WEBHOOKS = "register_webhooks"


class JobQueue(Protocol):
    async def enqueue(self, function: str, *args: Any) -> None: ...


class ArqJobQueue:
    """与 API 共用同一个 Redis 连接池。"""

    def __init__(self, redis: Redis) -> None:
        self._arq = ArqRedis(pool_or_conn=redis.connection_pool)

    async def enqueue(self, function: str, *args: Any) -> None:
        await self._arq.enqueue_job(function, *args)
