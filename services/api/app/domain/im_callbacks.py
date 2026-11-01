"""OpenIM 回调（V2 OC-3，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 10 节）。

- before 回调（发单聊、发群聊）**同步判断**，计数在 Redis；数据库或 Redis 出错时拒收 5009，
  不放行（方案第 4.5 节）。
- after 回调写入收件箱 im_webhook_inbox（按 event_key 去重），由 Worker 处理；
  回调没有签名，所以只取其中的 ID，内容以拉取为准。
- 其他命令放行并忽略，按命令计数（Redis 哈希 openim:callback_ignored）。

身份按 OpenIM 用户 ID 的格式区分：`ws_<用户>` 是管理员，`oc_<实例>` 是实例，
imAdmin 与 ws_notice 是工作台自己（系统消息放行）。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import notifications
from app.domain.im import IM_ROLES, NOTICE_USER_ID, user_id_from_im
from app.domain.ratelimit import WindowLimiter, _resolve
from app.models import (
    AgentInstance,
    AgentSettings,
    ImGroup,
    ImGroupMember,
    ImWebhookInboxEvent,
    User,
    WorkspaceMember,
)
from app.schemas.openim_callbacks import (
    CallbackResponse,
    DeleteFriendCallback,
    MsgCallback,
    RejectCode,
    UserStatusCallback,
)

logger = logging.getLogger("app.im")

STREAK_KEY = "im_relay_streak:{group_id}"
PAUSE_NOTIFIED_KEY = "im_relay_paused:{group_id}"
RATE_KEY = "im_relay_rate:{group_id}:{sender}"
IGNORED_KEY = "openim:callback_ignored"
# 连续计数没有管理员发言时保留多久（群长期没人管时不留垃圾键）
STREAK_TTL_SECONDS = 7 * 24 * 3600

AFTER_MSG_COMMANDS = ("callbackAfterSendSingleMsgCommand", "callbackAfterSendGroupMsgCommand")
STATUS_COMMANDS = {
    "callbackAfterUserOnlineCommand": "online",
    "callbackAfterUserOfflineCommand": "offline",
}
DELETE_FRIEND_COMMAND = "callbackAfterDeleteFriendCommand"


class Rejected(Exception):
    def __init__(self, code: RejectCode) -> None:
        super().__init__(code)
        self.code: RejectCode = code


@dataclass(frozen=True)
class Verdict:
    response: CallbackResponse
    # 这条消息让群进入“自动接力已暂停”（第一次拒收 5001 时为 True，用于通知）
    paused_group: ImGroup | None = None


def _allow() -> Verdict:
    return Verdict(CallbackResponse.allow())


class CallbackJudge:
    """before 回调的同步判断。"""

    def __init__(self, db: AsyncSession, redis: Redis, *, admin_user_id: str) -> None:
        self.db = db
        self.redis = redis
        self.system_ids = frozenset({admin_user_id, NOTICE_USER_ID})

    async def judge(self, msg: MsgCallback) -> Verdict:
        try:
            if msg.send_id in self.system_ids:
                return _allow()
            if msg.session_type == 1 and msg.recv_id and not msg.group_id:
                await self._single(msg)
                return _allow()
            if msg.session_type == 3 and msg.group_id:
                return await self._group(msg)
            raise Rejected(5005)
        except Rejected as rejected:
            return Verdict(CallbackResponse.reject(rejected.code))

    # ------------------------------------------------------------------ 身份

    async def _instance(self, im_user_id: str) -> AgentInstance | None:
        if not im_user_id.startswith("oc_"):
            return None
        instance: AgentInstance | None = await self.db.scalar(
            select(AgentInstance).where(AgentInstance.im_user_id == im_user_id)
        )
        return instance

    async def _check_sender_enabled(self, instance: AgentInstance) -> None:
        if instance.status == "disabled":
            raise Rejected(5004)
        settings = await self.db.get(AgentSettings, instance.workspace_id)
        if settings is not None and not settings.agents_enabled:
            raise Rejected(5004)

    async def _is_im_admin(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
        found = await self.db.scalar(
            select(WorkspaceMember.user_id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.role.in_(IM_ROLES),
                User.status == "active",
            )
        )
        return found is not None

    # ------------------------------------------------------------------ 单聊

    async def _single(self, msg: MsgCallback) -> None:
        """单聊只在“管理员 ↔ 本工作空间的实例”之间（契约第 5.1 节）。"""
        sender_instance = await self._instance(msg.send_id)
        if sender_instance is not None:
            await self._check_sender_enabled(sender_instance)
            if msg.recv_id.startswith("oc_"):
                raise Rejected(5003)
            user_id = user_id_from_im(msg.recv_id)
            if user_id is None or not await self._is_im_admin(
                user_id, sender_instance.workspace_id
            ):
                raise Rejected(5005)
            return
        sender_user = user_id_from_im(msg.send_id)
        recv_instance = await self._instance(msg.recv_id)
        if (
            sender_user is None
            or recv_instance is None
            or not await self._is_im_admin(sender_user, recv_instance.workspace_id)
        ):
            raise Rejected(5005)

    # ------------------------------------------------------------------ 群聊

    async def _group(self, msg: MsgCallback) -> Verdict:
        group = await self.db.scalar(
            select(ImGroup).where(ImGroup.group_id == msg.group_id, ImGroup.dismissed_at.is_(None))
        )
        if group is None:
            raise Rejected(5005)
        member = await self.db.scalar(
            select(ImGroupMember).where(
                ImGroupMember.group_pk == group.id, ImGroupMember.im_user_id == msg.send_id
            )
        )
        if member is None:
            raise Rejected(5005)
        streak_key = STREAK_KEY.format(group_id=group.group_id)
        if member.user_id is not None:
            if not await self._is_im_admin(member.user_id, group.workspace_id):
                raise Rejected(5005)
            # 管理员发言：清零连续计数，恢复自动接力
            await self.redis.delete(streak_key, PAUSE_NOTIFIED_KEY.format(group_id=group.group_id))
            return _allow()
        instance = await self.db.get(AgentInstance, member.instance_id)
        if instance is None or instance.workspace_id != group.workspace_id:
            raise Rejected(5005)
        await self._check_sender_enabled(instance)
        if not group.relay_enabled and any(
            at.startswith("oc_") and at != msg.send_id for at in msg.at_user_list or ()
        ):
            raise Rejected(5006)
        streak = int(await self.redis.get(streak_key) or 0)
        if streak >= group.max_agent_streak:
            return await self._paused(group)
        rate_key = RATE_KEY.format(group_id=group.group_id, sender=msg.send_id)
        if await WindowLimiter(self.redis, 60).hit(rate_key, group.max_per_minute) is not None:
            raise Rejected(5002)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(streak_key)
            pipe.expire(streak_key, STREAK_TTL_SECONDS)
            streak, _ = await pipe.execute()
        if int(streak) > group.max_agent_streak:
            # 并发的消息同时通过了上面的检查：多出来的这条拒收
            await self.redis.decr(streak_key)
            return await self._paused(group)
        return _allow()

    async def _paused(self, group: ImGroup) -> Verdict:
        """连续条数达到上限：拒收 5001；第一次拒收时通知（每次暂停只通知一次）。"""
        first = await self.redis.set(
            PAUSE_NOTIFIED_KEY.format(group_id=group.group_id),
            "1",
            nx=True,
            ex=STREAK_TTL_SECONDS,
        )
        return Verdict(CallbackResponse.reject(5001), paused_group=group if first else None)


async def relay_paused(db: AsyncSession, group: ImGroup) -> None:
    """通知本工作空间的管理员：群的自动接力已暂停（im_relay_paused，契约第 11 节）。

    群里由 ws_notice 发的提示在群功能（OC-3 后续部分）里加：建群时把 ws_notice 拉进群。
    """
    await notifications.notify_admins(
        db,
        group.workspace_id,
        type_="im_relay_paused",
        severity="warning",
        title=f"群“{group.name}”的自动接力已暂停",
        body=f"实例连续发言达到 {group.max_agent_streak} 条，管理员在群里发言后恢复。",
        link=f"/im?conversation=sg_{group.group_id}",
        key=group.group_id,
    )
    await db.commit()


# ---------------------------------------------------------------- after 回调 → 收件箱


def inbox_event_key(command: str, payload: dict[str, Any]) -> str | None:
    """收件箱的去重键；载荷缺字段时返回 None（不入库，靠对账补齐）。"""
    try:
        if command in AFTER_MSG_COMMANDS:
            return f"msg:{MsgCallback.model_validate(payload).server_msg_id}"
        if command in STATUS_COMMANDS:
            status = UserStatusCallback.model_validate(payload)
            return f"{STATUS_COMMANDS[command]}:{status.user_id}:{status.platform_id}:{status.seq}"
        if command == DELETE_FRIEND_COMMAND:
            friend = DeleteFriendCallback.model_validate(payload)
            received = int(datetime.now(UTC).timestamp() * 1000)
            return f"delete_friend:{friend.owner_user_id}:{friend.friend_user_id}:{received}"
    except ValidationError:
        return None
    return None


async def record_inbox(
    db: AsyncSession, command: str, event_key: str, payload: dict[str, Any]
) -> bool:
    """写入收件箱；同一个 event_key 只写一次。返回是否新写入。"""
    result = await db.execute(
        pg_insert(ImWebhookInboxEvent)
        .values(
            event_key=event_key,
            command=command,
            payload_json=payload,
            next_retry_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(ImWebhookInboxEvent.id)
    )
    inserted = result.scalar_one_or_none() is not None
    await db.commit()
    return inserted


async def count_ignored(redis: Redis, command: str) -> None:
    try:
        await _resolve(redis.hincrby(IGNORED_KEY, command[:64], 1))
    except Exception:  # 计数只是观测用，失败不影响应答
        logger.debug("callback counter failed")
