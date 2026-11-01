"""OpenClaw 实例的周期检查（V2 T-0063；方案第 4.1、4.3、4.4、6.1 节，契约第 11 节的通知）。

- 在线状态对账（每 5 分钟）：以 OpenIM 的实时在线状态为准纠正 online / offline。上下线回调丢失时
  实例不会一直显示“在线”；停用后又连上来的实例再踢一次。离线超过 OFFLINE_NOTIFY_AFTER 才提醒
  管理员（每周定时重启、冷启动都在几分钟内，不提醒），每次离线只提醒一次。
- 好友关系对账（每小时）：实例与本工作空间状态正常的 owner / admin 互为好友，缺了就补
  （“不能删好友”第 ④ 条；回调丢失或后来才成为管理员的人也能补上）。
- 到期提醒（每天）：客户端证书到期前 CERT_WARN_BEFORE、IM 令牌到期前 IM_TOKEN_WARN_BEFORE 提醒
  管理员。通知按小时去重，每天检查一次，所以到期前每天最多一条，换了新凭证就不再提醒。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import events, notifications
from app.domain.im import ImAccounts
from app.integrations.base import ConnectorError
from app.integrations.openim import OpenIMClient
from app.models import AgentCredential, AgentInstance

logger = logging.getLogger("app.agent_watch")

OFFLINE_SINCE_KEY = "agent_offline_since:{instance_id}"
OFFLINE_NOTIFIED_KEY = "agent_offline_notified:{instance_id}"
OFFLINE_KEY_TTL_SECONDS = 30 * 24 * 3600
OFFLINE_NOTIFY_AFTER = timedelta(minutes=10)
CERT_WARN_BEFORE = timedelta(days=14)
IM_TOKEN_WARN_BEFORE = timedelta(days=7)
# OpenIM 的 get_users_online_status 一次查询的人数（与上游的分页上限保持距离）
ONLINE_BATCH = 100


def _label(instance: AgentInstance) -> str:
    return instance.im_nickname or instance.name


async def mark_online(redis: Redis, instance: AgentInstance) -> None:
    """实例上线：清掉离线时间与“已提醒”标记，下次离线重新计时。"""
    await redis.delete(
        OFFLINE_SINCE_KEY.format(instance_id=instance.id),
        OFFLINE_NOTIFIED_KEY.format(instance_id=instance.id),
    )


async def mark_offline(redis: Redis, instance: AgentInstance, now: datetime | None = None) -> None:
    """实例离线：记下离线开始的时间（已有就不覆盖）。"""
    moment = (now or datetime.now(UTC)).isoformat()
    await redis.set(
        OFFLINE_SINCE_KEY.format(instance_id=instance.id),
        moment,
        nx=True,
        ex=OFFLINE_KEY_TTL_SECONDS,
    )


async def _offline_since(redis: Redis, instance: AgentInstance, now: datetime) -> datetime:
    raw = await redis.get(OFFLINE_SINCE_KEY.format(instance_id=instance.id))
    if raw is not None:
        try:
            return datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else str(raw))
        except ValueError:
            pass
    # 没有记录（Redis 清空、或离线发生在这个功能上线之前）：从现在开始计时
    await mark_offline(redis, instance, now)
    return now


async def _online_users(client: OpenIMClient, ids: Sequence[str]) -> set[str]:
    online: set[str] = set()
    for start in range(0, len(ids), ONLINE_BATCH):
        online |= await client.online_users(list(ids[start : start + ONLINE_BATCH]))
    return online


@dataclass
class PresenceResult:
    changed: int = 0
    # 已写入通知、等提交成功后再记“已提醒”的 (Redis 键, 离线开始时间)
    notified: list[tuple[str, str]] = field(default_factory=list)


async def reconcile_presence(
    db: AsyncSession, accounts: ImAccounts, redis: Redis, *, now: datetime | None = None
) -> PresenceResult:
    """按 OpenIM 的实时在线状态纠正实例状态。调用方提交，提交成功后调用 confirm_notified。

    查询 OpenIM 期间管理员可能停用了实例：状态只用带原状态条件的原子更新修改，
    条件不成立（例如已被停用）就不改，不会把 disabled 覆盖成 online / offline。
    """
    moment = now or datetime.now(UTC)
    result = PresenceResult()
    instances = list(
        (
            await db.scalars(
                select(AgentInstance)
                .where(AgentInstance.im_ready.is_(True))
                .order_by(AgentInstance.created_at, AgentInstance.id)
            )
        ).all()
    )
    if not instances:
        return result
    online = await _online_users(accounts.client, [i.im_user_id for i in instances])
    offline: list[AgentInstance] = []
    for instance in instances:
        connected = instance.im_user_id in online
        current = instance.status
        if current == "disabled":
            if connected:
                # 停用后又连上来（旧令牌在下线之前签发）：再踢一次
                await accounts.force_logout([instance.im_user_id])
            continue
        if connected and current != "online":
            target = "online"
        elif not connected and current == "online":
            target = "offline"
        else:
            if current == "offline":
                offline.append(instance)
            continue
        updated = await db.execute(
            update(AgentInstance)
            .where(AgentInstance.id == instance.id, AgentInstance.status == current)
            .values(status=target)
            .execution_options(synchronize_session=False)
        )
        if getattr(updated, "rowcount", 0) != 1:
            continue  # 状态在查询期间被改了（例如被停用）：以那次修改为准
        result.changed += 1
        events.mark(db, instance.workspace_id, "agents")
        if target == "online":
            await mark_online(redis, instance)
        else:
            await mark_offline(redis, instance, moment)
            offline.append(instance)
    for instance in offline:
        await _notify_if_long_offline(db, redis, instance, moment, result)
    return result


async def confirm_notified(redis: Redis, notified: Sequence[tuple[str, str]]) -> None:
    """离线提醒随事务提交成功后，记下“已提醒”；提交失败时不记，下一轮重新提醒。"""
    for key, since in notified:
        await redis.set(key, since, ex=OFFLINE_KEY_TTL_SECONDS)


async def _notify_if_long_offline(
    db: AsyncSession, redis: Redis, instance: AgentInstance, now: datetime, result: PresenceResult
) -> None:
    since = await _offline_since(redis, instance, now)
    if now - since < OFFLINE_NOTIFY_AFTER:
        return
    notified_key = OFFLINE_NOTIFIED_KEY.format(instance_id=instance.id)
    if await redis.exists(notified_key):
        return
    minutes = int((now - since).total_seconds() // 60)
    # 同一次离线在同一小时内只写一条（notify_admins 按小时去重），提交失败重试时不会重复
    await notifications.notify_admins(
        db,
        instance.workspace_id,
        type_="agent_offline",
        severity="warning",
        title=f"实例“{_label(instance)}”已离线",
        body=f"IM 已断开 {minutes} 分钟。OpenClaw 可能已停止，或网络、接入入口有问题；"
        "恢复连接后会自动显示在线。",
        link="/agents",
        key=f"{instance.id}:{since.isoformat()}",
    )
    result.notified.append((notified_key, since.isoformat()))


async def reconcile_friendships(db: AsyncSession, accounts: ImAccounts) -> int:
    """实例与本工作空间的 owner / admin 缺了好友就补；返回补上的好友数。调用方提交。"""
    instances = (
        await db.scalars(
            select(AgentInstance)
            .where(AgentInstance.im_ready.is_(True), AgentInstance.status != "disabled")
            .order_by(AgentInstance.workspace_id, AgentInstance.created_at)
        )
    ).all()
    admins: dict[object, list[str]] = {}
    restored = 0
    for instance in instances:
        if instance.workspace_id not in admins:
            users = await accounts.workspace_admins(instance.workspace_id)
            admins[instance.workspace_id] = [await accounts.ensure_user(u) for u in users]
        try:
            added = await accounts.befriend(instance.im_user_id, admins[instance.workspace_id])
        except ConnectorError as error:
            logger.warning(
                "friendship reconcile failed",
                extra={"instance_id": str(instance.id), "error_kind": error.kind},
            )
            if error.kind in ("UNAVAILABLE", "TIMEOUT_UNKNOWN"):
                break  # OpenIM 不可用：等下一轮
            continue
        if added:
            logger.info(
                "friendship restored by reconcile",
                extra={"instance_id": str(instance.id), "count": len(added)},
            )
            restored += len(added)
    return restored


async def warn_expiring(db: AsyncSession, *, now: datetime | None = None) -> int:
    """客户端证书与 IM 令牌快到期时提醒管理员；返回发出提醒的实例数。调用方提交。"""
    moment = now or datetime.now(UTC)
    sent = 0
    certs = (
        await db.execute(
            select(AgentCredential, AgentInstance)
            .join(AgentInstance, AgentInstance.id == AgentCredential.instance_id)
            .where(
                AgentCredential.kind == "client_cert",
                AgentCredential.revoked_at.is_(None),
                AgentCredential.expires_at.is_not(None),
                AgentCredential.expires_at > moment,
                AgentCredential.expires_at <= moment + CERT_WARN_BEFORE,
                AgentInstance.status != "disabled",
            )
        )
    ).all()
    for credential, instance in certs:
        expires_at = credential.expires_at or moment
        days = max(1, (expires_at - moment).days)
        if await notifications.notify_admins(
            db,
            instance.workspace_id,
            type_="agent_credential_expiring",
            severity="warning",
            title=f"实例“{_label(instance)}”的客户端证书 {days} 天后到期",
            body="到期后外网入口会拒绝这个实例。请在实例页重新生成接入包并更新到 OpenClaw 服务器"
            "（旧凭证会再保留 24 小时）。",
            link="/agents",
            key=str(credential.id),
        ):
            sent += 1
    tokens = (
        await db.scalars(
            select(AgentInstance).where(
                AgentInstance.status != "disabled",
                AgentInstance.im_token_expires_at.is_not(None),
                AgentInstance.im_token_expires_at > moment,
                AgentInstance.im_token_expires_at <= moment + IM_TOKEN_WARN_BEFORE,
            )
        )
    ).all()
    for instance in tokens:
        token_expires_at = instance.im_token_expires_at or moment
        days = max(1, (token_expires_at - moment).days)
        if await notifications.notify_admins(
            db,
            instance.workspace_id,
            type_="agent_im_token_expiring",
            severity="warning",
            title=f"实例“{_label(instance)}”的 IM 令牌 {days} 天后到期",
            body="IM 令牌只在 OpenClaw 启动时领取，每周的定时重启没有生效：请检查 OpenClaw "
            "服务器上的定时任务（接入包 CHECKLIST.md），或手动重启一次 OpenClaw。",
            link="/agents",
            key=f"{instance.id}:{token_expires_at.isoformat()}",
        ):
            sent += 1
    return sent
