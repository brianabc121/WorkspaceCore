"""跨进程锁（应用中心 T-0210，方案 docs/v2/app-center/ 契约第 3 节）。

用 PostgreSQL 的会话级 advisory lock，放在单独的数据库连接上：锁不随请求事务的提交释放，
能覆盖“写开关 → 提交 → 执行钩子 → 记录结果”整个过程，也能在 api 的多个进程、Worker 之间生效
（单进程的 asyncio.Lock 做不到）。离开时在 finally 里释放；释放失败就作废这个连接，
连接一关数据库也会自动释放它持有的会话级锁。

锁的顺序固定：工作空间的模块操作锁 → 部署级资源锁（例如新闻早报的配置文件写锁）→ 业务行锁。
只做渲染的入口可以只拿资源锁，但不能在持有资源锁时再去拿工作空间锁。
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = logging.getLogger("app.modules")
_POLL_SECONDS = 0.05


def _engine(db: AsyncSession) -> AsyncEngine:
    bind = db.bind
    if not isinstance(bind, AsyncEngine):  # 会话都由绑定 AsyncEngine 的 sessionmaker 创建
        raise RuntimeError("advisory lock needs a session bound to an AsyncEngine")
    return bind


def _key(name: str) -> ColumnElement[int]:
    return func.hashtextextended(name, 0)


@asynccontextmanager
async def advisory_lock(db: AsyncSession, name: str, *, wait: float = 0) -> AsyncIterator[bool]:
    """取名为 name 的会话级锁；wait 秒内取不到时产出 False（调用方决定报 409 还是跳过）。

    name 只能是代码里的固定前缀加 UUID / 固定配置，不能是用户输入的任意文字。
    这个连接只为这把锁借出，离开时用 pg_advisory_unlock_all 释放：即使取锁的语句被取消、
    不知道锁到底拿没拿到，也不会把锁留在归还连接池的连接上。
    """
    async with _engine(db).connect() as connection:
        try:
            deadline = time.monotonic() + wait
            while True:
                acquired = bool(
                    await connection.scalar(select(func.pg_try_advisory_lock(_key(name))))
                )
                # 每次尝试都结束自己的事务，避免这个连接长时间“事务中空闲”
                await connection.commit()
                if acquired or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(_POLL_SECONDS)
            yield acquired
        finally:
            try:
                await connection.execute(select(func.pg_advisory_unlock_all()))
                await connection.commit()
            except Exception:  # 释放失败：作废连接，数据库在断开时释放会话级锁
                logger.warning("advisory unlock failed", extra={"lock": name.split(":")[0]})
                await connection.invalidate()


def module_operation_lock(workspace_id: uuid.UUID) -> str:
    """一个工作空间的模块启用 / 停用与同步重试共用的锁。"""
    return f"module-op:{workspace_id}"
