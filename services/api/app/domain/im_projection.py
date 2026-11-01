"""IM 消息投影、回调收件箱的处理与对账（V2 OC-3，契约第 5.3、10 节）。

- **投影**：回调里的 seq 总是 0（OC-0 实测），所以回调只当作“这个会话有变化”的提示：
  用 `/msg/newest_seq` 取会话的最大序号，再用 `/msg/pull_msg_by_seq` 补齐缺的消息（imAdmin
  令牌，以会话成员的身份拉）。内容以拉回的为准；每个会话最多补最近 1000 条。
- **收件箱**：after 回调写入 im_webhook_inbox（app/domain/im_callbacks.py），这里领取处理：
  - 消息 → 同步那个会话；
  - 上线 → 实例状态 online，补一次好友关系；下线 → 向 OpenIM 核对后才改为 offline
    （同一实例可能有新旧两个连接）；按回调里的时间戳丢弃过期的事件；
  - 删好友 → 实例与本工作空间的管理员之间的好友被删时立即补回，并通知那个管理员。
- **对账**：每 30 秒按每个管理员账号的 newest_seq 补齐有变化的会话（回调丢失也能补上）；
  每天一次全量：重新拉每个会话最近 1000 条，补上中间缺的。

会话 ID 的格式（OC-0 实测）：单聊 `si_<两个用户 ID 按字典序>`，群聊 `sg_<群 ID>`。
用户 ID 都是 35 个字符（`ws_` / `oc_` 加 32 位十六进制），所以单聊的会话 ID 可以按位置拆开。
"""

import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain import events, notifications
from app.domain.im import ImAccounts, is_im_admin, user_id_from_im
from app.domain.sync import retry_delay
from app.integrations.base import ConnectorError
from app.integrations.openim import (
    CONTENT_AT_TEXT,
    CONTENT_PICTURE,
    CONTENT_TEXT,
    OpenIMClient,
    group_conversation_id,
    single_conversation_id,
)
from app.models import (
    AgentInstance,
    ImAccount,
    ImConversation,
    ImGroup,
    ImGroupMember,
    ImMedia,
    ImMessage,
    ImWebhookInboxEvent,
    User,
)
from app.schemas.openim_callbacks import (
    DeleteFriendCallback,
    MsgCallback,
    UserStatusCallback,
)

logger = logging.getLogger("app.im")

PULL_PAGE = 100
MAX_BACKFILL = 1000
MAX_CONTENT_BYTES = 64 * 1024
INBOX_BATCH = 50
INBOX_LEASE = timedelta(minutes=2)
INBOX_MAX_ATTEMPTS = 8
PRESENCE_KEY = "im_presence:{im_user_id}"
PRESENCE_TTL_SECONDS = 7 * 24 * 3600
_USER_ID_LENGTH = 35

# 系统消息的说明（contentType 来自锁定的 openimsdk/protocol v0.0.73-alpha.19 constant 包）
SYSTEM_TEXTS: dict[int, str] = {
    1201: "已成为好友",
    1204: "已成为好友",
    1501: "群已创建",
    1502: "群资料已更新",
    1504: "有成员退出了群",
    1507: "群主已转让",
    1508: "有成员被移出群",
    1509: "有新成员被邀请入群",
    1510: "有新成员入群",
    1511: "群已解散",
    1519: "群公告已更新",
    1520: "群名已更新",
}
# OpenIM 的通知类消息从 1000 开始
_NOTIFICATION_BEGIN = 1000


# ---------------------------------------------------------------- 解析


@dataclass(frozen=True)
class ParsedMessage:
    server_msg_id: str
    client_msg_id: str
    seq: int
    sender_im_id: str
    content_type: int
    content: dict[str, Any]
    plain_text: str | None
    at_im_ids: list[str]
    sent_at: datetime
    # 工作台代发时写进 ex 的 Idempotency-Key
    idempotency_key: uuid.UUID | None = None


def _decode_content(raw: object) -> dict[str, Any]:
    """MsgData.content 是 base64 编码的 JSON；过大或无法解析时返回 {}。"""
    if not isinstance(raw, str) or len(raw) > MAX_CONTENT_BYTES * 2:
        return {}
    try:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) > MAX_CONTENT_BYTES:
            return {}
        content = json.loads(decoded or b"{}")
    except (binascii.Error, ValueError):
        return {}
    return content if isinstance(content, dict) else {}


EX_KEY = "myws_key"


def ex_for(key: uuid.UUID) -> str:
    """代发消息的 ex：带上 Idempotency-Key，结果未知时靠它找回。"""
    return json.dumps({EX_KEY: str(key)}, separators=(",", ":"))


def _key_from_ex(raw: object) -> uuid.UUID | None:
    if not isinstance(raw, str) or not raw or len(raw) > 1024:
        return None
    try:
        data = json.loads(raw)
        return uuid.UUID(str(data[EX_KEY])) if isinstance(data, dict) and EX_KEY in data else None
    except (ValueError, TypeError):
        return None


def parse_message(raw: dict[str, Any]) -> ParsedMessage | None:
    """pull_msg_by_seq 返回的一条 MsgData；缺必要字段时返回 None。"""
    try:
        seq = int(raw.get("seq") or 0)
        content_type = int(raw.get("contentType") or 0)
        send_time = int(raw.get("sendTime") or 0)
    except (TypeError, ValueError):
        return None
    server_msg_id = str(raw.get("serverMsgID") or "")
    sender = str(raw.get("sendID") or "")
    if seq <= 0 or not server_msg_id or not sender:
        return None
    content = _decode_content(raw.get("content"))
    text: str | None = None
    at: list[str] = []
    if content_type == CONTENT_TEXT:
        text = str(content.get("content") or "")
    elif content_type == CONTENT_AT_TEXT:
        text = str(content.get("text") or "")
        at = [str(u) for u in content.get("atUserList") or [] if isinstance(u, str)][:50]
    elif content_type in SYSTEM_TEXTS:
        text = SYSTEM_TEXTS[content_type]
    elif content_type >= _NOTIFICATION_BEGIN:
        text = "系统通知"
    return ParsedMessage(
        server_msg_id=server_msg_id[:128],
        client_msg_id=str(raw.get("clientMsgID") or server_msg_id)[:128],
        seq=seq,
        sender_im_id=sender[:64],
        content_type=content_type,
        content=content if content_type in (CONTENT_TEXT, CONTENT_AT_TEXT, CONTENT_PICTURE) else {},
        plain_text=text,
        at_im_ids=at,
        sent_at=datetime.fromtimestamp(send_time / 1000, UTC)
        if send_time > 0
        else datetime.now(UTC),
        idempotency_key=_key_from_ex(raw.get("ex")),
    )


# ---------------------------------------------------------------- 会话


@dataclass(frozen=True)
class ConversationRef:
    conversation_id: str
    workspace_id: uuid.UUID
    kind: Literal["single", "group"]
    # 以谁的身份拉取（必须是会话成员）
    pull_as: str
    # 能看到这个会话的管理员（各有一行 im_conversations）
    owners: tuple[uuid.UUID, ...]
    peer_instance_id: uuid.UUID | None = None
    group_pk: uuid.UUID | None = None


def split_single(conversation_id: str) -> tuple[str, str] | None:
    body = conversation_id[3:]
    if not conversation_id.startswith("si_") or len(body) != 2 * _USER_ID_LENGTH + 1:
        return None
    if body[_USER_ID_LENGTH] != "_":
        return None
    return body[:_USER_ID_LENGTH], body[_USER_ID_LENGTH + 1 :]


async def resolve(db: AsyncSession, conversation_id: str) -> ConversationRef | None:
    """工作台关心的会话：管理员 ↔ 本工作空间的实例，或工作台建的群。其他返回 None。"""
    if conversation_id.startswith("si_"):
        pair = split_single(conversation_id)
        if pair is None:
            return None
        admin_im = next((u for u in pair if u.startswith("ws_")), "")
        instance_im = next((u for u in pair if u.startswith("oc_")), "")
        user_id = user_id_from_im(admin_im)
        instance = await db.scalar(
            select(AgentInstance).where(AgentInstance.im_user_id == instance_im)
        )
        if user_id is None or instance is None:
            return None
        if not await is_im_admin(db, user_id, instance.workspace_id):
            return None
        return ConversationRef(
            conversation_id=conversation_id,
            workspace_id=instance.workspace_id,
            kind="single",
            pull_as=admin_im,
            owners=(user_id,),
            peer_instance_id=instance.id,
        )
    if conversation_id.startswith("sg_"):
        group = await db.scalar(select(ImGroup).where(ImGroup.group_id == conversation_id[3:]))
        if group is None:
            return None
        members = (
            await db.scalars(select(ImGroupMember).where(ImGroupMember.group_pk == group.id))
        ).all()
        users = [m for m in members if m.user_id is not None]
        owners = tuple(
            [
                m.user_id
                for m in users
                if m.user_id is not None and await is_im_admin(db, m.user_id, group.workspace_id)
            ]
        )
        # 群主建群时就在群里，能看到全部消息；不在了就换其他成员
        by_owner = next((m for m in users if m.user_id == group.owner_user_id), None)
        puller = by_owner or (users[0] if users else (members[0] if members else None))
        if puller is None:
            return None
        return ConversationRef(
            conversation_id=conversation_id,
            workspace_id=group.workspace_id,
            kind="group",
            pull_as=puller.im_user_id,
            owners=owners,
            group_pk=group.id,
        )
    return None


async def _ensure_conversations(db: AsyncSession, ref: ConversationRef) -> None:
    for owner in ref.owners:
        await db.execute(
            pg_insert(ImConversation)
            .values(
                workspace_id=ref.workspace_id,
                owner_user_id=owner,
                conversation_id=ref.conversation_id,
                kind=ref.kind,
                peer_instance_id=ref.peer_instance_id,
                group_pk=ref.group_pk,
            )
            .on_conflict_do_nothing(index_elements=["owner_user_id", "conversation_id"])
        )


async def _upsert(
    db: AsyncSession, ref: ConversationRef, parsed: ParsedMessage, instance_id: uuid.UUID | None
) -> bool:
    stmt = pg_insert(ImMessage).values(
        workspace_id=ref.workspace_id,
        conversation_id=ref.conversation_id,
        server_msg_id=parsed.server_msg_id,
        client_msg_id=parsed.client_msg_id,
        seq=parsed.seq,
        sender_im_id=parsed.sender_im_id,
        sender_instance_id=instance_id,
        content_type=parsed.content_type,
        content_json=parsed.content,
        plain_text=parsed.plain_text,
        at_im_ids=parsed.at_im_ids,
        status="sent",
        idempotency_key=parsed.idempotency_key,
        sent_at=parsed.sent_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["server_msg_id"],
        set_={
            "seq": stmt.excluded.seq,
            "status": "sent",
            "content_type": stmt.excluded.content_type,
            "content_json": stmt.excluded.content_json,
            "plain_text": stmt.excluded.plain_text,
            "at_im_ids": stmt.excluded.at_im_ids,
            "sender_instance_id": stmt.excluded.sender_instance_id,
        },
    )
    try:
        async with db.begin_nested():
            if parsed.idempotency_key is not None:
                # 工作台代发、结果未知的那条本地记录：认领它，避免同一条消息出现两行
                await db.execute(
                    update(ImMessage)
                    .where(
                        ImMessage.idempotency_key == parsed.idempotency_key,
                        ImMessage.server_msg_id.is_(None),
                    )
                    .values(server_msg_id=parsed.server_msg_id, client_msg_id=parsed.client_msg_id)
                )
            await db.execute(stmt)
    except IntegrityError:
        # 同一个序号或客户端消息 ID 已被另一条记录占用：记日志跳过，全量对账时再看
        logger.warning(
            "im message projection conflict",
            extra={"conversation_id": ref.conversation_id, "seq": parsed.seq},
        )
        return False
    return True


async def sync_conversation(
    db: AsyncSession,
    client: OpenIMClient,
    ref: ConversationRef,
    *,
    remote_max: int | None = None,
    full: bool = False,
) -> int:
    """把会话补齐到 OpenIM 的最大序号；返回写入的消息数。由调用方提交。"""
    if remote_max is None:
        remote_max = (await client.max_seqs(ref.pull_as)).get(ref.conversation_id, 0)
    local_max = int(
        await db.scalar(
            select(func.max(ImMessage.seq)).where(ImMessage.conversation_id == ref.conversation_id)
        )
        or 0
    )
    floor = max(1, remote_max - MAX_BACKFILL + 1)
    begin = floor if full else max(local_max + 1, floor)
    written = 0
    while begin <= remote_max:
        end = min(begin + PULL_PAGE - 1, remote_max)
        pulled = await client.pull_messages(
            ref.pull_as, ref.conversation_id, begin, end, num=PULL_PAGE
        )
        parsed = [p for p in (parse_message(m) for m in pulled) if p is not None]
        senders = {p.sender_im_id for p in parsed if p.sender_im_id.startswith("oc_")}
        rows = await db.execute(
            select(AgentInstance.im_user_id, AgentInstance.id).where(
                AgentInstance.im_user_id.in_(senders)
            )
        )
        # Result 有 keys()，dict(result) 会把它当成映射：逐行取
        instances = {im_user_id: instance_id for im_user_id, instance_id in rows}
        for item in parsed:
            if await _upsert(db, ref, item, instances.get(item.sender_im_id)):
                written += 1
        begin = end + 1
    await _ensure_conversations(db, ref)
    last_at = await db.scalar(
        select(func.max(ImMessage.sent_at)).where(ImMessage.conversation_id == ref.conversation_id)
    )
    await db.execute(
        update(ImConversation)
        .where(ImConversation.conversation_id == ref.conversation_id)
        .values(max_seq=func.greatest(ImConversation.max_seq, remote_max), last_message_at=last_at)
    )
    if written:
        events.mark(db, ref.workspace_id, "im")
    return written


# ---------------------------------------------------------------- 收件箱


@dataclass
class ImContext:
    sessions: async_sessionmaker[AsyncSession]
    client: OpenIMClient
    redis: Redis


async def _claim(db: AsyncSession, limit: int) -> list[uuid.UUID]:
    now = datetime.now(UTC)
    events_ = (
        await db.scalars(
            select(ImWebhookInboxEvent)
            .where(
                ImWebhookInboxEvent.status.in_(("pending", "retry", "processing")),
                ImWebhookInboxEvent.next_retry_at <= now,
            )
            .order_by(ImWebhookInboxEvent.received_at, ImWebhookInboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for event in events_:
        event.status = "processing"
        event.attempts += 1
        event.next_retry_at = now + INBOX_LEASE
    return [event.id for event in events_]


def _finish(event: ImWebhookInboxEvent, status: str, error: str | None = None) -> None:
    event.status = status
    event.error_text = error
    event.next_retry_at = None
    event.processed_at = datetime.now(UTC)


async def process_inbox(ctx: ImContext, *, limit: int = INBOX_BATCH) -> int:
    """领取一批到期的回调逐个处理；返回处理的数量。"""
    async with ctx.sessions() as db:
        ids = await _claim(db, limit)
        await db.commit()
    for event_id in ids:
        await _process_event(ctx, event_id)
    return len(ids)


async def _process_event(ctx: ImContext, event_id: uuid.UUID) -> None:
    async with ctx.sessions() as db:
        event = await db.get(ImWebhookInboxEvent, event_id, with_for_update=True)
        if event is None or event.status != "processing":
            await db.rollback()
            return
        command, payload, attempts = event.command, dict(event.payload_json), event.attempts
        try:
            outcome = await apply_event(db, ctx, command, payload)
        except Exception as error:  # 连接器错误与意外错误都按暂时性错误重试，超限进 dead_letter
            await db.rollback()
            kind = error.kind if isinstance(error, ConnectorError) else type(error).__name__
            if not isinstance(error, ConnectorError):
                logger.exception(
                    "im inbox event failed unexpectedly",
                    extra={"event_id": str(event_id), "error_type": kind},
                )
            event = await db.get(ImWebhookInboxEvent, event_id, with_for_update=True)
            if event is None:
                return
            if attempts >= INBOX_MAX_ATTEMPTS:
                _finish(event, "dead_letter", f"{kind}:max_attempts")
            else:
                event.status = "retry"
                event.error_text = kind
                event.next_retry_at = datetime.now(UTC) + retry_delay(attempts)
            await db.commit()
            return
        _finish(event, "completed", outcome)
        await db.commit()


async def apply_event(
    db: AsyncSession, ctx: ImContext, command: str, payload: dict[str, Any]
) -> str | None:
    """处理一条回调；返回说明（例如为什么忽略），正常处理返回 None。"""
    if command in ("callbackAfterSendSingleMsgCommand", "callbackAfterSendGroupMsgCommand"):
        msg = MsgCallback.model_validate(payload)
        conversation_id = (
            group_conversation_id(msg.group_id)
            if msg.group_id
            else single_conversation_id(msg.send_id, msg.recv_id)
        )
        ref = await resolve(db, conversation_id)
        if ref is None:
            return "not_tracked"
        await sync_conversation(db, ctx.client, ref)
        return None
    if command in ("callbackAfterUserOnlineCommand", "callbackAfterUserOfflineCommand"):
        status = UserStatusCallback.model_validate(payload)
        return await apply_presence(
            db, ctx, status, online=command == "callbackAfterUserOnlineCommand"
        )
    if command == "callbackAfterDeleteFriendCommand":
        return await restore_friend(db, ctx.client, DeleteFriendCallback.model_validate(payload))
    return "ignored"


async def apply_presence(
    db: AsyncSession, ctx: ImContext, status: UserStatusCallback, *, online: bool
) -> str | None:
    instance = await db.scalar(
        select(AgentInstance).where(AgentInstance.im_user_id == status.user_id)
    )
    if instance is None:
        return "not_instance"
    accounts = ImAccounts(db, ctx.client)
    if instance.status == "disabled":
        if online:
            # 停用后又连上来（旧令牌在下线之前签发）：再踢一次
            await accounts.force_logout([instance.im_user_id])
        return "disabled"
    key = PRESENCE_KEY.format(im_user_id=instance.im_user_id)
    last = int(await ctx.redis.get(key) or 0)
    if status.seq and status.seq < last:
        return "stale"
    if status.seq:
        await ctx.redis.set(key, str(status.seq), ex=PRESENCE_TTL_SECONDS)
    if online:
        instance.status = "online"
        instance.last_seen_at = datetime.now(UTC)
        # 上线时补一次好友关系（契约第 10 节）：后来才加入的管理员也能收到
        admins = [
            await accounts.ensure_user(u)
            for u in await accounts.workspace_admins(instance.workspace_id)
        ]
        await accounts.befriend(instance.im_user_id, admins)
    else:
        # 同一实例可能有新旧两个连接：以 OpenIM 的实时状态为准
        if instance.im_user_id in await ctx.client.online_users([instance.im_user_id]):
            return "still_online"
        if instance.status == "online":
            instance.status = "offline"
    events.mark(db, instance.workspace_id, "agents")
    return None


async def restore_friend(
    db: AsyncSession, client: OpenIMClient, deleted: DeleteFriendCallback
) -> str | None:
    """实例与本工作空间管理员之间的好友被删时补回，并通知那个管理员。"""
    pair = (deleted.owner_user_id, deleted.friend_user_id)
    instance_im = next((u for u in pair if u.startswith("oc_")), "")
    admin_im = next((u for u in pair if u.startswith("ws_")), "")
    user_id = user_id_from_im(admin_im)
    instance = await db.scalar(select(AgentInstance).where(AgentInstance.im_user_id == instance_im))
    if user_id is None or instance is None or instance.status == "disabled":
        return "not_tracked"
    if not await is_im_admin(db, user_id, instance.workspace_id):
        return "not_admin"
    restored = await ImAccounts(db, client).befriend(instance.im_user_id, [admin_im])
    if restored:
        await notifications.notify_user(
            db,
            instance.workspace_id,
            user_id,
            type_="im_friend_restored",
            severity="info",
            title=f"已恢复与“{instance.im_nickname or instance.name}”的好友关系",
            body="工作台不支持删除好友，好友关系已自动补回。",
            link=f"/im?conversation={single_conversation_id(admin_im, instance_im)}",
            key=f"{instance.id}:{user_id}:{datetime.now(UTC).isoformat()}",
        )
    return None


# ---------------------------------------------------------------- 对账


async def reconcile(ctx: ImContext, *, full: bool = False) -> int:
    """按每个管理员账号的 newest_seq 补齐有变化的会话；返回写入的消息数。

    full=True 时重新拉每个会话最近 1000 条（补上中间缺的）。单个账号失败只记日志。
    """
    async with ctx.sessions() as db:
        accounts = (
            (
                await db.execute(
                    select(ImAccount.im_user_id)
                    .join(User, User.id == ImAccount.user_id)
                    .where(User.status == "active")
                    .order_by(ImAccount.created_at)
                )
            )
            .scalars()
            .all()
        )
    written = 0
    seen: set[str] = set()
    for im_user_id in accounts:
        try:
            written += await _reconcile_account(ctx, im_user_id, seen, full=full)
        except ConnectorError as error:
            logger.warning(
                "im reconcile failed",
                extra={"im_user_id": im_user_id, "error_kind": error.kind},
            )
            if error.kind in ("UNAVAILABLE", "TIMEOUT_UNKNOWN"):
                break  # OpenIM 不可用：等下一轮
    return written


async def _reconcile_account(ctx: ImContext, im_user_id: str, seen: set[str], *, full: bool) -> int:
    remote = await ctx.client.max_seqs(im_user_id)
    candidates = [c for c in remote if c.startswith(("si_", "sg_")) and c not in seen]
    if not candidates:
        return 0
    async with ctx.sessions() as db:
        rows = await db.execute(
            select(ImMessage.conversation_id, func.max(ImMessage.seq))
            .where(ImMessage.conversation_id.in_(candidates))
            .group_by(ImMessage.conversation_id)
        )
        local = {conversation_id: seq for conversation_id, seq in rows}
        written = 0
        for conversation_id in candidates:
            if not full and int(local.get(conversation_id) or 0) >= remote[conversation_id]:
                continue
            ref = await resolve(db, conversation_id)
            seen.add(conversation_id)
            if ref is None:
                continue
            written += await sync_conversation(
                db, ctx.client, ref, remote_max=remote[conversation_id], full=full
            )
            await db.commit()
    return written


async def purge(
    db: AsyncSession,
    *,
    message_retention: timedelta,
    completed_retention: timedelta = timedelta(days=7),
) -> tuple[int, int]:
    """删除超过保留期的消息投影与早已处理完的回调（dead_letter 保留，便于排查）。调用方提交。"""
    now = datetime.now(UTC)
    messages = await db.execute(
        delete(ImMessage).where(ImMessage.sent_at < now - message_retention)
    )
    # 没有用于发消息的上传：到期删除记录（契约第 5.4 节）
    await db.execute(delete(ImMedia).where(ImMedia.status == "uploaded", ImMedia.expires_at < now))
    inbox = await db.execute(
        delete(ImWebhookInboxEvent).where(
            ImWebhookInboxEvent.status == "completed",
            ImWebhookInboxEvent.processed_at < now - completed_retention,
        )
    )
    return (
        int(getattr(messages, "rowcount", 0) or 0),
        int(getattr(inbox, "rowcount", 0) or 0),
    )
