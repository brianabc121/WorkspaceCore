"""同一个集成同一时间只做一次同步（契约第 4 节 `SYNC_IN_PROGRESS`）。

Redis 键 `ws:sync-lock:<集成 ID>`，值是持有者标识，带过期时间：持有者异常退出时锁自动释放。
手动同步由 API 先加锁再投递作业，作业沿用同一个持有者标识；定时对账拿不到锁就跳过这一轮。
"""

import uuid

from redis.asyncio import Redis

LOCK_TTL_SECONDS = 900

# 只删除自己持有的锁（比较并删除，原子执行）
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _key(integration_id: uuid.UUID) -> str:
    return f"ws:sync-lock:{integration_id}"


def new_owner(kind: str) -> str:
    return f"{kind}:{uuid.uuid4().hex}"


async def acquire(redis: Redis, integration_id: uuid.UUID, owner: str) -> bool:
    return bool(await redis.set(_key(integration_id), owner, nx=True, ex=LOCK_TTL_SECONDS))


async def adopt(redis: Redis, integration_id: uuid.UUID, owner: str) -> bool:
    """作业开始时：锁仍是投递时的持有者就续期接手；锁已过期就重新加锁。"""
    current = await redis.get(_key(integration_id))
    if current is None:
        return await acquire(redis, integration_id, owner)
    holder = current.decode() if isinstance(current, bytes) else str(current)
    if holder == owner:
        await redis.expire(_key(integration_id), LOCK_TTL_SECONDS)
        return True
    return False


async def release(redis: Redis, integration_id: uuid.UUID, owner: str) -> None:
    await redis.eval(_RELEASE, 1, _key(integration_id), owner)  # type: ignore[misc]
