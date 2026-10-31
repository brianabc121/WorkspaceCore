"""服务健康告警（S5-02；Sprint 5 契约第 3.1 节）：Worker 每分钟检查一次，状态变化时通知管理员。

- 检查的服务：api（经内部地址的 /readyz）、Prometheus、Grafana。
  PostgreSQL 不可用时通知本身写不进去，Redis 不可用时 Worker 无法调度，这两项只在运维页显示；
  Worker 自己是否在运行由 api 按心跳判断（运维页与首页的 operations 模块）。
- 连续两次检查为 down 才发 service_down，避免偶发的超时刷屏；从 down 恢复时发 service_recovered。
- 部署级的服务发给每个工作空间的 owner / admin；source_key 带小时，同一变化一小时内只通知一次。
- 状态记在 Redis 里，通知写入并提交成功之后才更新：写失败时下一分钟会再试。
"""

import logging
from collections.abc import Callable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import notifications
from app.repositories import notifications as notification_repo
from app.schemas.operations import ServiceStatusOut

logger = logging.getLogger("app.alerts")

_STATE_KEY = "ops:alert:{service}"
_DOWNS_KEY = "ops:alert:{service}:downs"
DOWN_CHECKS = 2


async def _notify_all(db: AsyncSession, item: ServiceStatusOut, *, down: bool) -> None:
    for workspace_id in await notification_repo.workspace_ids(db):
        if down:
            await notifications.notify_admins(
                db,
                workspace_id,
                type_="service_down",
                severity="error",
                title=f"{item.name} 不可用",
                body="连续两次检查都失败了，依赖它的功能会降级。请查看运维页了解详情。",
                link="/operations",
                key=item.id,
            )
        else:
            await notifications.notify_admins(
                db,
                workspace_id,
                type_="service_recovered",
                severity="info",
                title=f"{item.name} 已恢复",
                body="最近一次检查已恢复正常。",
                link="/operations",
                key=item.id,
            )


async def evaluate(
    redis: Redis, sessions: Callable[[], AsyncSession], items: list[ServiceStatusOut]
) -> list[str]:
    """比较这次与上次的状态，需要时发通知；返回发出的通知类型（便于测试与日志）。"""
    sent: list[str] = []
    for item in items:
        state_key = _STATE_KEY.format(service=item.id)
        downs_key = _DOWNS_KEY.format(service=item.id)
        if item.status in {"not_configured", "unknown"}:
            await redis.delete(downs_key)
            continue
        previous = await redis.get(state_key)
        was_down = previous in {b"down", "down"}
        if item.status == "down":
            downs = int(await redis.incr(downs_key))
            await redis.expire(downs_key, 600)
            if downs < DOWN_CHECKS or was_down:
                continue
            async with sessions() as db:
                await _notify_all(db, item, down=True)
                await db.commit()
            await redis.set(state_key, "down")
            sent.append(f"service_down:{item.id}")
            logger.warning("service down", extra={"service": item.id, "reason": item.reason})
        else:
            await redis.delete(downs_key)
            if not was_down:
                continue
            async with sessions() as db:
                await _notify_all(db, item, down=False)
                await db.commit()
            await redis.set(state_key, "up")
            sent.append(f"service_recovered:{item.id}")
            logger.info("service recovered", extra={"service": item.id})
    return sent
