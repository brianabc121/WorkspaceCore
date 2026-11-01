"""管理员端 IM（V2 OC-3，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5 节）。

浏览器只调用工作台接口，工作台以 imAdmin 身份代当前用户（`ws_<用户>`）操作 OpenIM：
- 会话、已读、置顶、免打扰、消息置顶都属于“当前用户 + 工作空间”（im_conversations 每个用户一行）；
- 消息列表读工作台的投影（app/domain/im_projection.py），OpenIM 不可用时也能看历史；
- 发送必须带 Idempotency-Key：先记一条 sending 的本地消息，再调用 OpenIM；Idempotency-Key 写进
  消息的 ex。结果未知时返回 504，用同一个键重发时先补齐投影、按 ex 找回那条消息，
  超过 5 分钟仍找不到就确认没有生效（409 OPERATION_NOT_APPLIED）；
- 置顶与免打扰同步到 OpenIM，OpenIM 不可用时返回 503、本地不改；消息置顶只在工作台记录。
"""

import base64
import binascii
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events, idempotency
from app.domain.audit import AuditTrail
from app.domain.im import ImAccounts, admin_im_user_id, im_unavailable
from app.domain.im_projection import ex_for, resolve, sync_conversation
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.integrations.base import ConnectorError
from app.integrations.openim import (
    CONTENT_AT_TEXT,
    CONTENT_PICTURE,
    CONTENT_TEXT,
    SESSION_GROUP,
    SESSION_SINGLE,
    OpenIMClient,
    OpenIMError,
    single_conversation_id,
)
from app.models import (
    AgentInstance,
    ImAccount,
    ImContactGroup,
    ImContactGroupMember,
    ImConversation,
    ImGroup,
    ImGroupMember,
    ImMessage,
    ImMessagePin,
    User,
)
from app.schemas.im import (
    MAX_PINS_PER_CONVERSATION,
    ImBadges,
    ImConversationOut,
    ImConversationPage,
    ImConversationUpdate,
    ImImage,
    ImMessageOut,
    ImMessagePage,
    ImMessagePinList,
    ImMessagePinOut,
    ImMessagePreview,
    ImMessageSend,
    ImStatus,
    MessageKind,
)

logger = logging.getLogger("app.im")

SEND_PER_MINUTE = 30
# 结果未知的消息：超过这个时间投影里仍找不到，就确认 OpenIM 没有受理
UNKNOWN_GRACE = timedelta(minutes=5)
# OpenIM 的通知会话（sn_）的会话类型
SESSION_NOTIFICATION = 4
# before 回调的拒收码（契约第 10 节）与 OpenIM 的参数类错误：发送被拒，不是暂时不可用
_REJECT_CODES = frozenset(range(5001, 5010)) | {1001, 1004, 1101, 1201, 1203, 1204}
NOTICE_TITLE = "审批通知"


def _not_found(what: str = "会话") -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what}不存在")


def _bad_cursor() -> ApiError:
    return ApiError(422, "VALIDATION_FAILED", "cursor 无效", details={"errors": []})


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise _bad_cursor() from None
    if not isinstance(data, dict):
        raise _bad_cursor()
    return data


def message_kind(row: ImMessage) -> MessageKind:
    if row.content_type in (CONTENT_TEXT, CONTENT_AT_TEXT):
        return "text"
    if row.content_type == CONTENT_PICTURE:
        return "image"
    if row.content_type >= 1000:
        return "system"
    return "unsupported"


def _image(row: ImMessage) -> ImImage | None:
    """图片的尺寸来自 sourcePicture；对象存储里的图片经 /im/media 显示（上传与代理在后续部分）。"""
    if row.content_type != CONTENT_PICTURE:
        return None
    source = row.content_json.get("sourcePicture") or {}
    width = source.get("width") if isinstance(source, dict) else None
    height = source.get("height") if isinstance(source, dict) else None
    return ImImage(
        media_id=None,
        external_url=None,
        width=width if isinstance(width, int) else None,
        height=height if isinstance(height, int) else None,
    )


def message_out(row: ImMessage, *, pinned: bool) -> ImMessageOut:
    return ImMessageOut(
        server_msg_id=row.server_msg_id,
        client_msg_id=row.client_msg_id,
        conversation_id=row.conversation_id,
        seq=row.seq,
        sender_im_id=row.sender_im_id,
        sender_instance_id=row.sender_instance_id,
        kind=message_kind(row),
        text=row.plain_text if message_kind(row) in ("text", "system") else None,
        image=_image(row),
        at_im_ids=list(row.at_im_ids or []),
        approval_execution_id=None,
        status=row.status,
        sent_at=row.sent_at,
        pinned=pinned,
    )


def _preview(row: ImMessage) -> ImMessagePreview:
    kind = message_kind(row)
    if kind == "image":
        text = "[图片]"
    elif kind == "unsupported":
        text = "暂不支持的消息"
    else:
        text = (row.plain_text or "")[:100]
    return ImMessagePreview(
        kind=kind, text_preview=text, sender_im_id=row.sender_im_id, sent_at=row.sent_at
    )


@dataclass(frozen=True)
class SendResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool = False


class ImService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None,
        client: OpenIMClient | None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.client = client
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 身份

    @staticmethod
    def _user_id(access: WorkspaceAccess) -> uuid.UUID:
        if access.user_id is None:  # pragma: no cover - 管理员接口总是带当前用户
            raise ApiError(401, "UNAUTHENTICATED", "未登录")
        return access.user_id

    def _require_client(self) -> OpenIMClient:
        if self.client is None:
            raise im_unavailable()
        return self.client

    async def _ensure_account(self, access: WorkspaceAccess) -> str:
        """当前用户的 OpenIM 账号；第一次时注册（需要 OpenIM 可用）。"""
        user_id = self._user_id(access)
        existing = await self.db.get(ImAccount, user_id)
        if existing is not None:
            return existing.im_user_id
        user = await self.db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _not_found("用户")
        try:
            im_user_id = await ImAccounts(self.db, self._require_client()).ensure_user(user)
        except ConnectorError as error:
            raise im_unavailable() from error
        await self.db.commit()
        return im_user_id

    # ---------------------------------------------------------------- 状态与角标

    async def status(self, access: WorkspaceAccess) -> ImStatus:
        """第一次打开 IM：注册账号，并与本工作空间已就绪的实例补好友（OpenIM 不可用时跳过）。"""
        user_id = self._user_id(access)
        available = self.client is not None
        if self.client is not None:
            try:
                user = await self.db.get(User, user_id)
                accounts = ImAccounts(self.db, self.client)
                if user is not None:
                    im_user_id = await accounts.ensure_user(user)
                    ready = (
                        await self.db.scalars(
                            select(AgentInstance.im_user_id).where(
                                AgentInstance.workspace_id == access.workspace.id,
                                AgentInstance.im_ready.is_(True),
                                AgentInstance.status != "disabled",
                            )
                        )
                    ).all()
                    for instance_im in ready:
                        await accounts.befriend(instance_im, [im_user_id])
                await self.db.commit()
            except ConnectorError as error:
                await self.db.rollback()
                available = False
                logger.warning("openim unavailable for status", extra={"error_kind": error.kind})
        account = await self.db.get(ImAccount, user_id)
        return ImStatus(
            available=available,
            account_ready=account is not None,
            im_user_id=account.im_user_id if account else None,
        )

    def _unread_query(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> Any:
        """每个会话的未读数：序号大于已读序号、不是自己发的消息。"""
        return (
            select(ImConversation.id, func.count(ImMessage.id))
            .join(ImMessage, ImMessage.conversation_id == ImConversation.conversation_id)
            .where(
                ImConversation.owner_user_id == user_id,
                ImConversation.workspace_id == workspace_id,
                ImMessage.seq > ImConversation.read_seq,
                ImMessage.sender_im_id != admin_im_user_id(user_id),
            )
            .group_by(ImConversation.id)
        )

    async def _unread_total(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> int:
        stmt = self._unread_query(user_id, workspace_id).where(ImConversation.muted.is_(False))
        return sum(int(count) for _, count in await self.db.execute(stmt))

    async def badges(self, access: WorkspaceAccess) -> ImBadges:
        # 待审批数在工具调用与审批（OC-5）里实现
        return ImBadges(
            im_unread=await self._unread_total(self._user_id(access), access.workspace.id),
            pending_approvals=0,
        )

    # ---------------------------------------------------------------- 会话

    async def _conversation(self, access: WorkspaceAccess, conversation_id: str) -> ImConversation:
        conversation = await self.db.scalar(
            select(ImConversation).where(
                ImConversation.owner_user_id == self._user_id(access),
                ImConversation.workspace_id == access.workspace.id,
                ImConversation.conversation_id == conversation_id,
            )
        )
        if conversation is None:
            raise _not_found()
        return conversation

    async def _outs(
        self, access: WorkspaceAccess, rows: Sequence[ImConversation]
    ) -> list[ImConversationOut]:
        if not rows:
            return []
        user_id = self._user_id(access)
        ids = [r.id for r in rows]
        unread = dict(
            (cid, int(count))
            for cid, count in await self.db.execute(
                self._unread_query(user_id, access.workspace.id).where(ImConversation.id.in_(ids))
            )
        )
        instance_ids = {r.peer_instance_id for r in rows if r.peer_instance_id}
        group_pks = {r.group_pk for r in rows if r.group_pk}
        instances = {
            i.id: i
            for i in await self.db.scalars(
                select(AgentInstance).where(AgentInstance.id.in_(instance_ids))
            )
        }
        groups = {
            g.id: g for g in await self.db.scalars(select(ImGroup).where(ImGroup.id.in_(group_pks)))
        }
        latest = {
            m.conversation_id: m
            for m in await self.db.scalars(
                select(ImMessage)
                .where(ImMessage.conversation_id.in_([r.conversation_id for r in rows]))
                .ext(distinct_on(ImMessage.conversation_id))
                .order_by(ImMessage.conversation_id, ImMessage.sent_at.desc(), ImMessage.id.desc())
            )
        }
        outs = []
        for row in rows:
            instance = instances.get(row.peer_instance_id) if row.peer_instance_id else None
            group = groups.get(row.group_pk) if row.group_pk else None
            if instance is not None:
                title = instance.im_nickname or instance.name
            elif group is not None:
                title = group.name
            else:
                title = NOTICE_TITLE
            last = latest.get(row.conversation_id)
            outs.append(
                ImConversationOut(
                    conversation_id=row.conversation_id,
                    kind=row.kind,
                    title=title,
                    peer_instance_id=row.peer_instance_id,
                    group_id=group.group_id if group else None,
                    pinned=row.pinned_at is not None,
                    pinned_at=row.pinned_at,
                    muted=row.muted,
                    unread_count=unread.get(row.id, 0),
                    read_seq=row.read_seq,
                    max_seq=row.max_seq,
                    last_message=_preview(last) if last else None,
                    last_message_at=row.last_message_at,
                )
            )
        return outs

    async def list_conversations(
        self,
        access: WorkspaceAccess,
        *,
        kind: str | None,
        contact_group_id: uuid.UUID | None,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> ImConversationPage:
        user_id = self._user_id(access)
        group_instances: list[uuid.UUID] | None = None
        if contact_group_id is not None:
            group = await self.db.scalar(
                select(ImContactGroup.id).where(
                    ImContactGroup.id == contact_group_id,
                    ImContactGroup.workspace_id == access.workspace.id,
                )
            )
            if group is None:
                raise _not_found("分组")
            group_instances = list(
                (
                    await self.db.scalars(
                        select(ImContactGroupMember.instance_id).where(
                            ImContactGroupMember.contact_group_id == contact_group_id
                        )
                    )
                ).all()
            )
        decoded = _decode_cursor(cursor) or {}
        offset = decoded.get("o", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1_000_000:
            raise _bad_cursor()
        stmt = (
            select(ImConversation)
            .outerjoin(AgentInstance, AgentInstance.id == ImConversation.peer_instance_id)
            .outerjoin(ImGroup, ImGroup.id == ImConversation.group_pk)
            .where(
                ImConversation.owner_user_id == user_id,
                ImConversation.workspace_id == access.workspace.id,
            )
        )
        if kind:
            stmt = stmt.where(ImConversation.kind == kind)
        if group_instances is not None:
            stmt = stmt.where(ImConversation.peer_instance_id.in_(group_instances))
        if q:
            needle = q.lower()
            stmt = stmt.where(
                or_(
                    func.strpos(func.lower(AgentInstance.im_nickname), needle) > 0,
                    func.strpos(func.lower(AgentInstance.name), needle) > 0,
                    func.strpos(func.lower(ImGroup.name), needle) > 0,
                )
            )
        rows = (
            await self.db.scalars(
                stmt.order_by(
                    ImConversation.pinned_at.desc().nulls_last(),
                    ImConversation.last_message_at.desc().nulls_last(),
                    ImConversation.id,
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return ImConversationPage(
            items=await self._outs(access, rows[:limit]),
            next_cursor=_encode_cursor({"o": offset + limit}) if len(rows) > limit else None,
            total_estimate=None,
            unread_total=await self._unread_total(user_id, access.workspace.id),
        )

    async def open_conversation(
        self, access: WorkspaceAccess, instance_id: uuid.UUID
    ) -> ImConversationOut:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
            )
        )
        if instance is None:
            raise _not_found("实例")
        if not instance.im_ready:
            raise ApiError(409, "IM_NOT_READY", "这个实例的 IM 账号还没就绪，请稍后再试")
        im_user_id = await self._ensure_account(access)
        try:
            # 实例登记之后才成为管理员的用户：这里补上好友
            await ImAccounts(self.db, self._require_client()).befriend(
                instance.im_user_id, [im_user_id]
            )
        except ConnectorError as error:
            raise im_unavailable() from error
        conversation_id = single_conversation_id(im_user_id, instance.im_user_id)
        await self.db.execute(
            pg_insert(ImConversation)
            .values(
                workspace_id=access.workspace.id,
                owner_user_id=self._user_id(access),
                conversation_id=conversation_id,
                kind="single",
                peer_instance_id=instance.id,
            )
            .on_conflict_do_nothing(index_elements=["owner_user_id", "conversation_id"])
        )
        await self.db.commit()
        return (await self._outs(access, [await self._conversation(access, conversation_id)]))[0]

    async def update_conversation(
        self, access: WorkspaceAccess, conversation_id: str, body: ImConversationUpdate
    ) -> ImConversationOut:
        row = await self._conversation(access, conversation_id)
        if body.pinned is not None or body.muted is not None:
            client = self._require_client()
            peer = ""
            group_id = ""
            if row.peer_instance_id:
                instance = await self.db.get(AgentInstance, row.peer_instance_id)
                peer = instance.im_user_id if instance else ""
            if row.group_pk:
                group = await self.db.get(ImGroup, row.group_pk)
                group_id = group.group_id if group else ""
            session = {"single": SESSION_SINGLE, "group": SESSION_GROUP}.get(
                row.kind, SESSION_NOTIFICATION
            )
            try:
                await client.set_conversation(
                    admin_im_user_id(self._user_id(access)),
                    conversation_id,
                    conversation_type=session,
                    peer_user_id=peer,
                    group_id=group_id,
                    pinned=body.pinned,
                    muted=body.muted,
                )
            except ConnectorError as error:
                raise im_unavailable() from error
            if body.pinned is not None:
                if body.pinned and row.pinned_at is None:
                    row.pinned_at = datetime.now(UTC)
                elif not body.pinned:
                    row.pinned_at = None
            if body.muted is not None:
                row.muted = body.muted
            row.version += 1
            events.mark(self.db, access.workspace.id, "im")
            await self.db.commit()
        return (await self._outs(access, [row]))[0]

    async def mark_read(
        self, access: WorkspaceAccess, conversation_id: str, seq: int
    ) -> ImConversationOut:
        row = await self._conversation(access, conversation_id)
        target = min(seq, row.max_seq)
        if target > row.read_seq:
            # 只增不减：并发的旧请求不会把已读序号改小
            await self.db.execute(
                update(ImConversation)
                .where(ImConversation.id == row.id)
                .values(read_seq=func.greatest(ImConversation.read_seq, target))
            )
            events.mark(self.db, access.workspace.id, "im")
            await self.db.commit()
            await self.db.refresh(row)
        return (await self._outs(access, [row]))[0]

    # ---------------------------------------------------------------- 消息

    async def _pinned_ids(self, conversation: ImConversation) -> set[str]:
        return set(
            (
                await self.db.scalars(
                    select(ImMessagePin.server_msg_id).where(
                        ImMessagePin.conversation_pk == conversation.id
                    )
                )
            ).all()
        )

    async def list_messages(
        self, access: WorkspaceAccess, conversation_id: str, *, cursor: str | None, limit: int
    ) -> ImMessagePage:
        conversation = await self._conversation(access, conversation_id)
        stmt = select(ImMessage).where(ImMessage.conversation_id == conversation_id)
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            try:
                before_at = datetime.fromisoformat(str(decoded["t"]))
                before_id = uuid.UUID(str(decoded["i"]))
            except (KeyError, ValueError):
                raise _bad_cursor() from None
            stmt = stmt.where(
                or_(
                    ImMessage.sent_at < before_at,
                    (ImMessage.sent_at == before_at) & (ImMessage.id < before_id),
                )
            )
        rows = (
            await self.db.scalars(
                stmt.order_by(ImMessage.sent_at.desc(), ImMessage.id.desc()).limit(limit + 1)
            )
        ).all()
        pinned = await self._pinned_ids(conversation)
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = _encode_cursor({"t": last.sent_at.isoformat(), "i": str(last.id)})
        return ImMessagePage(
            items=[message_out(r, pinned=r.server_msg_id in pinned) for r in page],
            next_cursor=next_cursor,
            total_estimate=None,
        )

    async def _target(self, conversation: ImConversation) -> tuple[str, str, set[str]]:
        """(recv_id, group_id, 群成员)；单聊的群成员为空。"""
        if conversation.peer_instance_id:
            instance = await self.db.get(AgentInstance, conversation.peer_instance_id)
            if instance is None:
                raise _not_found("实例")
            return instance.im_user_id, "", set()
        if conversation.group_pk:
            group = await self.db.get(ImGroup, conversation.group_pk)
            if group is None or group.dismissed_at is not None:
                raise ApiError(409, "IM_SEND_REJECTED", "群已解散，不能再发消息")
            members = set(
                (
                    await self.db.scalars(
                        select(ImGroupMember.im_user_id).where(ImGroupMember.group_pk == group.id)
                    )
                ).all()
            )
            return "", group.group_id, members
        raise ApiError(409, "IM_SEND_REJECTED", "这个会话不能发消息")

    async def send_message(
        self,
        access: WorkspaceAccess,
        conversation_id: str,
        body: ImMessageSend,
        key: uuid.UUID,
        *,
        path: str,
    ) -> SendResult:
        user_id = self._user_id(access)
        conversation = await self._conversation(access, conversation_id)
        if body.kind == "image":
            # 图片上传（POST /im/uploads）在后续部分实现；在那之前没有可用的上传
            raise _not_found("上传")
        recv_id, group_id, members = await self._target(conversation)
        if body.at_im_ids and not group_id:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "单聊不能 @ 成员",
                details={"errors": [{"loc": ["body", "at_im_ids"], "msg": "单聊必须为空"}]},
            )
        outsiders = sorted(set(body.at_im_ids) - members)
        if outsiders:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "@ 的成员不在群里",
                details={"errors": [{"loc": ["body", "at_im_ids"], "msg": ",".join(outsiders)}]},
            )
        begun = await idempotency.begin(
            self.db,
            workspace_id=access.workspace.id,
            actor_id=user_id,
            key=key,
            request_fingerprint=idempotency.fingerprint(
                access.workspace.id, "POST", path, body.model_dump(mode="json")
            ),
            context={"operation": "im.send", "conversation_id": conversation_id},
        )
        if begun.state == "replay":
            status_code, stored = idempotency.replay(begun.record)
            return SendResult(status_code, stored, replayed=True)
        if begun.state == "unknown":
            return await self._resolve_unknown(begun, conversation, key)
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 60).hit(f"im_send:{user_id}", SEND_PER_MINUTE)
            if retry is not None:
                await idempotency.discard(self.db, begun.record)
                await self.db.commit()
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "发送过于频繁，请稍后再试",
                    headers={"Retry-After": str(retry)},
                )
        if self.client is None:
            await idempotency.discard(self.db, begun.record)
            await self.db.commit()
            raise im_unavailable()
        sender = admin_im_user_id(user_id)
        local = ImMessage(
            workspace_id=access.workspace.id,
            conversation_id=conversation_id,
            client_msg_id=f"myws-{key}",
            sender_im_id=sender,
            content_type=CONTENT_AT_TEXT if body.at_im_ids else CONTENT_TEXT,
            content_json={},
            plain_text=body.text,
            at_im_ids=list(body.at_im_ids),
            status="sending",
            idempotency_key=key,
            sent_at=datetime.now(UTC),
        )
        self.db.add(local)
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        try:
            sent = await self.client.send_text(
                send_id=sender,
                recv_id=recv_id,
                group_id=group_id,
                text=body.text or "",
                at_user_ids=body.at_im_ids,
                ex=ex_for(key),
            )
        except ConnectorError as error:
            raise await self._send_failed(begun.record, local, error) from error
        record = await self.db.get(type(begun.record), begun.record.id, with_for_update=True)
        local.server_msg_id = sent.server_msg_id or None
        if sent.client_msg_id:
            local.client_msg_id = sent.client_msg_id
        if sent.send_time_ms > 0:
            local.sent_at = datetime.fromtimestamp(sent.send_time_ms / 1000, UTC)
        local.status = "sent"
        out = message_out(local, pinned=False).model_dump(mode="json")
        if record is not None:
            idempotency.succeed(record, 202, out)
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        return SendResult(202, out)

    async def _send_failed(self, record: Any, local: ImMessage, error: ConnectorError) -> ApiError:
        record = await self.db.get(type(record), record.id, with_for_update=True)
        if isinstance(error, OpenIMError) and error.err_code in _REJECT_CODES:
            problem = ApiError(
                409,
                "IM_SEND_REJECTED",
                "OpenIM 拒收了这条消息",
                details={"err_code": error.err_code},
            )
            local.status = "failed"
            if record is not None:
                idempotency.fail(record, problem)
        elif error.not_sent or error.kind == "AUTH_FAILED":
            # 确定没有发出（连不上、熔断、管理员令牌被拒）：允许用同一个键重试
            problem = im_unavailable()
            await self.db.delete(local)
            if record is not None:
                await idempotency.discard(self.db, record)
        else:
            problem = ApiError(
                504,
                "RESULT_UNKNOWN",
                "发送结果未知：请用同一个 Idempotency-Key 重新发送以查询结果",
                details={"retry_with_same_key": True},
            )
            local.status = "unknown"
            if record is not None:
                idempotency.mark_unknown(record)
        logger.warning(
            "im send failed",
            extra={"error_kind": error.kind, "conversation_id": local.conversation_id},
        )
        await self.db.commit()
        return problem

    async def _resolve_unknown(
        self, begun: idempotency.Begun, conversation: ImConversation, key: uuid.UUID
    ) -> SendResult:
        """结果未知的发送：补齐投影后按 ex 里的键找那条消息。"""
        record = begun.record
        found = await self._by_key(key)
        if (found is None or found.server_msg_id is None) and self.client is not None:
            ref = await resolve(self.db, conversation.conversation_id)
            if ref is not None:
                try:
                    await sync_conversation(self.db, self.client, ref)
                except ConnectorError:
                    logger.warning("im sync for unknown send failed")
            found = await self._by_key(key)
        if found is not None and found.server_msg_id is not None:
            found.status = "sent"
            out = message_out(found, pinned=False).model_dump(mode="json")
            idempotency.succeed(record, 202, out)
            await self.db.commit()
            return SendResult(202, out)
        if datetime.now(UTC) - idempotency.started_at(record) > UNKNOWN_GRACE:
            problem = idempotency.not_applied()
            if found is not None:
                found.status = "failed"
            idempotency.fail(record, problem)
            await self.db.commit()
            raise problem
        await self.db.commit()
        raise ApiError(
            504,
            "RESULT_UNKNOWN",
            "发送结果仍未知：请稍后用同一个 Idempotency-Key 再查询",
            details={"retry_with_same_key": True},
        )

    async def _by_key(self, key: uuid.UUID) -> ImMessage | None:
        found: ImMessage | None = await self.db.scalar(
            select(ImMessage).where(ImMessage.idempotency_key == key)
        )
        return found

    # ---------------------------------------------------------------- 消息置顶

    async def _pin_out(self, pin: ImMessagePin, message: ImMessage) -> ImMessagePinOut:
        return ImMessagePinOut(
            server_msg_id=pin.server_msg_id,
            pinned_by=pin.pinned_by,
            pinned_at=pin.pinned_at,
            message=message_out(message, pinned=True),
        )

    async def list_pins(self, access: WorkspaceAccess, conversation_id: str) -> ImMessagePinList:
        conversation = await self._conversation(access, conversation_id)
        rows = (
            await self.db.execute(
                select(ImMessagePin, ImMessage)
                .join(ImMessage, ImMessage.server_msg_id == ImMessagePin.server_msg_id)
                .where(ImMessagePin.conversation_pk == conversation.id)
                .order_by(ImMessagePin.pinned_at.desc())
            )
        ).all()
        return ImMessagePinList(items=[await self._pin_out(pin, msg) for pin, msg in rows])

    async def pin(
        self, access: WorkspaceAccess, conversation_id: str, server_msg_id: str
    ) -> ImMessagePinOut:
        conversation = await self._conversation(access, conversation_id)
        message = await self.db.scalar(
            select(ImMessage).where(
                ImMessage.server_msg_id == server_msg_id,
                ImMessage.conversation_id == conversation_id,
            )
        )
        if message is None:
            raise _not_found("消息")
        existing = await self.db.scalar(
            select(ImMessagePin).where(
                ImMessagePin.conversation_pk == conversation.id,
                ImMessagePin.server_msg_id == server_msg_id,
            )
        )
        if existing is not None:
            return await self._pin_out(existing, message)
        # 锁住会话行再数，避免并发置顶超过上限
        await self.db.execute(
            select(ImConversation.id).where(ImConversation.id == conversation.id).with_for_update()
        )
        count = await self.db.scalar(
            select(func.count(ImMessagePin.id)).where(
                ImMessagePin.conversation_pk == conversation.id
            )
        )
        if int(count or 0) >= MAX_PINS_PER_CONVERSATION:
            await self.db.rollback()
            raise ApiError(
                409,
                "PIN_LIMIT_REACHED",
                f"每个会话最多置顶 {MAX_PINS_PER_CONVERSATION} 条消息",
            )
        pin = ImMessagePin(
            conversation_pk=conversation.id,
            server_msg_id=server_msg_id,
            pinned_by=self._user_id(access),
        )
        self.db.add(pin)
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        await self.db.refresh(pin)
        return await self._pin_out(pin, message)

    async def unpin(
        self, access: WorkspaceAccess, conversation_id: str, server_msg_id: str
    ) -> None:
        conversation = await self._conversation(access, conversation_id)
        result = await self.db.execute(
            delete(ImMessagePin).where(
                ImMessagePin.conversation_pk == conversation.id,
                ImMessagePin.server_msg_id == server_msg_id,
            )
        )
        if not getattr(result, "rowcount", 0):
            await self.db.rollback()
            raise _not_found("置顶")
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()

    # ---------------------------------------------------------------- 导出

    async def export(
        self, access: WorkspaceAccess, conversation_id: str, *, client_ip: str
    ) -> AsyncIterator[bytes]:
        """先检查权限并写审计，再按序号升序逐批输出 NDJSON。"""
        conversation = await self._conversation(access, conversation_id)
        await self.audit.record(
            "im.conversation.exported",
            workspace_ids=[access.workspace.id],
            actor_id=self._user_id(access),
            resource_type="im_conversation",
            resource_id=conversation_id,
            client_ip=client_ip,
        )
        await self.db.commit()
        pinned = await self._pinned_ids(conversation)
        return self._export_rows(conversation_id, pinned)

    async def _export_rows(self, conversation_id: str, pinned: set[str]) -> AsyncIterator[bytes]:
        last_seq = 0
        while True:
            rows = (
                await self.db.scalars(
                    select(ImMessage)
                    .where(ImMessage.conversation_id == conversation_id, ImMessage.seq > last_seq)
                    .order_by(ImMessage.seq)
                    .limit(500)
                )
            ).all()
            if not rows:
                return
            for row in rows:
                out = message_out(row, pinned=row.server_msg_id in pinned)
                yield (out.model_dump_json() + "\n").encode()
            last_seq = int(rows[-1].seq or last_seq)
