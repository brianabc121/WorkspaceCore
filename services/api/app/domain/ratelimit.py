"""失败次数限流：Redis 固定窗口计数（登录与首次安装共用）。

只统计失败；任一键达到上限即拒绝，直到窗口过期。成功登录会清除该账户的计数。
"""

import inspect
from collections.abc import Iterable, Mapping
from typing import Any

from redis.asyncio import Redis


async def _resolve(value: Any) -> Any:
    # redis-py 的类型标注把命令结果写成“可能同步、可能异步”，这里按实际返回处理
    return await value if inspect.isawaitable(value) else value


class FailureLimiter:
    def __init__(self, redis: Redis, window_seconds: int) -> None:
        self._redis = redis
        self._window = window_seconds

    async def retry_after(self, limits: Mapping[str, int]) -> int | None:
        """任一键的失败次数达到上限时，返回还需等待的秒数；否则返回 None。"""
        for key, limit in limits.items():
            count = await _resolve(self._redis.get(key))
            if count is not None and int(count) >= limit:
                ttl = int(await _resolve(self._redis.ttl(key)))
                return max(ttl, 1)
        return None

    async def record_failure(self, keys: Iterable[str]) -> None:
        async with self._redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.incr(key)
                pipe.expire(key, self._window, nx=True)
            await pipe.execute()

    async def clear(self, key: str) -> None:
        await _resolve(self._redis.delete(key))


class WindowLimiter:
    """请求次数限流：Redis 固定窗口计数（V2 实例接口按实例限流）。"""

    def __init__(self, redis: Redis, window_seconds: int) -> None:
        self._redis = redis
        self._window = window_seconds

    async def hit(self, key: str, limit: int) -> int | None:
        """记一次请求；超过上限时返回还需等待的秒数，否则返回 None。"""
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._window, nx=True)
            count, _ = await pipe.execute()
        if int(count) <= limit:
            return None
        ttl = int(await _resolve(self._redis.ttl(key)))
        return max(ttl, 1)
