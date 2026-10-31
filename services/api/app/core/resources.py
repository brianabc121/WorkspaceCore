"""进程级共享资源：数据库引擎与 Redis 客户端。创建时不连接，首次使用时才建立连接。"""

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


def create_redis(settings: Settings) -> Redis:
    timeout = settings.readiness_timeout_seconds
    client: Redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    return client


def create_stream_redis(settings: Settings) -> Redis:
    """SSE 用：XREAD 会阻塞 sse_block_seconds 秒，读超时要比它长。"""
    client: Redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.sse_block_seconds + 10,
    )
    return client
