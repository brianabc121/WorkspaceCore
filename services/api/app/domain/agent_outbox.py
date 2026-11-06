"""智能体的发件箱（T-0118，docs/v2/AGENT_MESSAGING_PLAN.md 第 4.1、5、6 节）。

- `im_send`：工作台以实例的身份（sendID = oc_…）经 OpenIM 管理接口 `/msg/send_msg` 发出。
  OpenIM 对这样发出的消息照样调用 before 回调（核验记录 2.2），规则不变；发送前先按同一套规则
  **只读**预检，明显会被拒收的当场返回原因。
- 发件箱：一条 OpenIM 消息一行；排队、定时、限流后补发、退避重试；`ex` 里写发件箱 ID，
  结果未知时在消息投影里按它对账（im_messages.idempotency_key），不会重复发。
- 同一个幂等键（client_key）重试返回同一次的结果；内容不同就报错。
- 回调拒收了实例发的消息（主要是插件发的自动回复）时记一条拒收记录；每分钟上限（5002）
  拒收的文字回复在限额恢复后由工作台补发（最多等 2 分钟，每群每实例最多 3 条）。
"""

import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import agent_im, agent_media, agent_wakeups, events, ops_metrics
from app.domain.agent_ops import redact
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.im_callbacks import RATE_KEY
from app.domain.im_projection import EX_KEY, resolve, signed_ex, sync_conversation
from app.integrations.base import ConnectorError
from app.integrations.openim import (
    CONTENT_AT_TEXT,
    CONTENT_TEXT,
    OpenIMClient,
    OpenIMError,
    group_conversation_id,
    single_conversation_id,
)
from app.models import (
    AgentImOutbox,
    AgentImRejection,
    AgentInstance,
    AgentSettings,
    ImMedia,
    ImMessage,
)
from app.schemas.ai_tools import (
    CancelWakeupInput,
    ImCancelInput,
    ImCancelOutput,
    ImOutboxInput,
    ImOutboxItem,
    ImOutboxOutput,
    ImOutboxText,
    ImRejectedReply,
    ImSendInput,
    ImSendOutput,
    ImSendResultItem,
    ScheduleWakeupInput,
    ToolSpec,
)
from app.schemas.openim_callbacks import MsgCallback

logger = logging.getLogger("app.im")

SENDS_PER_HOUR = 60
HOUR_KEY = "im_send_hour:{instance_id}"
# 工具调用里最多等多久（接入包里 MCP 请求超时是 20 秒）
SYNC_BUDGET_SECONDS = 12.0
RATE_WAIT = timedelta(minutes=10)
REPLY_RETRY_WAIT = timedelta(minutes=2)
REPLY_RETRY_MAX = 3
MAX_ATTEMPTS = 5
BACKOFF = (5, 30, 120, 600, 1800)
GIVE_UP_AFTER = timedelta(hours=1)
UNKNOWN_GRACE = timedelta(minutes=5)
STUCK_SENDING = timedelta(minutes=2)
SCHEDULE_MIN = timedelta(minutes=1)
SCHEDULE_MAX = timedelta(days=30)
KEY_BUCKET_SECONDS = 600
PREVIEW_CHARS = 120
OUTBOX_RETENTION = timedelta(days=30)
REJECTION_RETENTION = timedelta(days=7)
PENDING = ("scheduled", "queued", "unknown")
FINISHED = ("sent", "rejected", "failed", "cancelled")

# 拒收码 → （原因，能否再发，怎么办）；与 app/domain/im_callbacks.py 的规则一一对应
REJECT_REASONS: dict[int, tuple[str, bool, str]] = {
    5001: (
        "群里智能体连续发言已达上限，自动接力已暂停",
        False,
        "等管理员在群里发言后再发；急事可以私聊管理员",
    ),
    5002: ("你在这个群里每分钟的条数已达上限", True, "稍等一会儿再发"),
    5003: ("智能体之间不能私聊", False, "在你们共同所在的群里 @ 它"),
    5004: ("实例已停用，或智能体总开关已关闭", False, "请管理员处理"),
    5005: (
        "你不在这个群里，或对方不是本工作空间的管理员",
        False,
        "用 im_list_targets 查看能发给谁",
    ),
    5006: ("这个群关闭了接力，不能 @ 其他智能体", True, "去掉对其他智能体的 @ 后再发"),
    5007: ("智能体只能经工作台发附件", False, "用 im_send 的 attachments 发附件"),
    5009: ("工作台暂时判断不了", True, "稍后重试"),
}
# 与管理员发消息一致（app/domain/im_service.py）：这些 errCode 表示 OpenIM 拒收或参数不对，不重试
REJECT_CODES = frozenset(range(5001, 5010)) | {1001, 1004, 1101, 1201, 1203, 1204}


def reason_for(code: int) -> tuple[str, bool, str]:
    return REJECT_REASONS.get(code, (f"OpenIM 拒收了这条消息（errCode {code}）", False, ""))


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _kind(content_type: int) -> Literal["text", "image", "file", "video"]:
    return {102: "image", 104: "video", 105: "file"}.get(content_type, "text")  # type: ignore[return-value]


@dataclass
class SendContext:
    db: AsyncSession
    settings: Settings
    redis: Redis | None
    client: OpenIMClient | None
    # 取附件网址用的 transport（只在测试里替换，与 Connector 共用）
    transport: Any = None


async def _rate_retry_after(redis: Redis | None, group_id: str, sender: str) -> int:
    if redis is None:
        return 10
    ttl = int(await redis.ttl(RATE_KEY.format(group_id=group_id, sender=sender)))
    return max(ttl, 1) if ttl > 0 else 10


async def _agents_enabled(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    settings = await db.get(AgentSettings, workspace_id)
    return settings is None or settings.agents_enabled


async def precheck(
    ctx: SendContext, instance: AgentInstance, target: agent_im.Target, mention: Sequence[str]
) -> tuple[int, int | None] | None:
    """按回调的规则只读预检（不计数）：会被拒收时返回（拒收码，多久后可重试）。"""
    if instance.status == "disabled" or not await _agents_enabled(ctx.db, instance.workspace_id):
        return 5004, None
    group = target.group
    if group is None:
        return None
    if not group.relay_enabled and any(
        m.startswith("oc_") and m != instance.im_user_id for m in mention
    ):
        return 5006, None
    state = await agent_im.group_state(ctx.redis, group, instance.im_user_id)
    if state.paused:
        return 5001, None
    if state.remaining_this_minute <= 0:
        return 5002, await _rate_retry_after(ctx.redis, group.group_id, instance.im_user_id)
    return None


def _default_key(parsed: ImSendInput, now: datetime) -> str:
    bucket = int(now.timestamp()) // KEY_BUCKET_SECONDS
    raw = f"{parsed.target}|{parsed.text}|{','.join(parsed.mention)}|{parsed.send_at}|{bucket}"
    return "auto-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


class AgentOutbox:
    def __init__(self, ctx: SendContext) -> None:
        self.ctx = ctx
        self.db = ctx.db

    # ------------------------------------------------------------ im_send

    async def send(self, instance: AgentInstance, parsed: ImSendInput) -> ImSendOutput:
        now = _now()
        if not parsed.text and not parsed.attachments:
            raise ToolCallError("VALIDATION_FAILED", "text 与 attachments 至少要有一个")
        if instance.status == "disabled":
            raise ToolCallError("AGENT_DISABLED", "实例已停用")
        if not instance.im_ready:
            raise ToolCallError("IM_NOT_READY", "你的 IM 账号还没就绪，暂时不能发消息")
        client = self.ctx.client
        if client is None:
            raise ToolCallError("EXTERNAL_UNAVAILABLE", "IM 服务没有部署或暂时不可用")
        target = await agent_im.resolve_target(self.db, instance, parsed.target)
        mention = list(dict.fromkeys(parsed.mention))
        if mention:
            if target.group is None:
                raise ToolCallError("VALIDATION_FAILED", "私聊不能 @ 人；mention 只能在群里用")
            if not parsed.text:
                raise ToolCallError("VALIDATION_FAILED", "@ 人要和文字一起发")
            members = await self._group_members(target)
            unknown = [m for m in mention if m not in members]
            if unknown:
                raise ToolCallError("VALIDATION_FAILED", f"这些人不在群里：{', '.join(unknown)}")
        send_at = parsed.send_at
        if send_at is not None:
            if send_at > now + SCHEDULE_MAX:
                raise ToolCallError("VALIDATION_FAILED", "定时发送最远 30 天")
            if send_at <= now + SCHEDULE_MIN:
                send_at = None
        key = parsed.client_key or _default_key(parsed, now)
        request_hash = _hash(
            {
                "target": parsed.target,
                "text": parsed.text,
                "mention": mention,
                "send_at": send_at.isoformat() if send_at else None,
                "attachments": [a.model_dump(mode="json") for a in parsed.attachments],
            }
        )
        existing = await self._by_key(instance.id, key)
        if existing:
            return await self._replay(instance, parsed.target, key, request_hash, existing)
        parts = (1 if parsed.text else 0) + len(parsed.attachments)
        if self.ctx.redis is not None:
            used = int(await self.ctx.redis.get(HOUR_KEY.format(instance_id=instance.id)) or 0)
            if used + parts > SENDS_PER_HOUR:
                raise ToolCallError(
                    "RATE_LIMITED", f"每小时最多主动发 {SENDS_PER_HOUR} 条，稍后再发"
                )
        prepared: list[agent_media.Prepared] = []
        if parsed.attachments:
            sources = [
                (
                    agent_media.AttachmentSource(
                        url=a.url,
                        data_base64=a.data_base64,
                        text_content=a.text_content,
                        ref=a.ref,
                        filename=a.filename,
                        kind=a.kind,
                    ),
                    agent_media.AttachmentSource(
                        url=a.cover.url, data_base64=a.cover.data_base64, ref=a.cover.ref
                    )
                    if a.cover
                    else None,
                )
                for a in parsed.attachments
            ]
            try:
                prepared = await agent_media.prepare(
                    self.db,
                    self.ctx.settings,
                    self.ctx.redis,
                    client,
                    instance,
                    sources,
                    transport=self.ctx.transport,
                )
            except agent_media.AttachmentError as error:
                await self.db.rollback()
                raise ToolCallError(error.code, error.message) from None
        if self.ctx.redis is not None:
            async with self.ctx.redis.pipeline(transaction=True) as pipe:
                pipe.incrby(HOUR_KEY.format(instance_id=instance.id), parts)
                pipe.expire(HOUR_KEY.format(instance_id=instance.id), 3600, nx=True)
                await pipe.execute()
        verdict = await precheck(self.ctx, instance, target, mention)
        contents: list[tuple[int, dict[str, Any], str | None, uuid.UUID | None]] = []
        if parsed.text:
            contents.append(
                (
                    CONTENT_AT_TEXT if mention else CONTENT_TEXT,
                    {"text": parsed.text, "atUserList": mention}
                    if mention
                    else {"content": parsed.text},
                    parsed.text,
                    None,
                )
            )
        contents += [(p.content_type, p.content_json, p.summary, p.media.id) for p in prepared]
        rows: list[AgentImOutbox] = []
        for part, (content_type, content, plain, media_id) in enumerate(contents):
            row = AgentImOutbox(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                target=parsed.target,
                conversation_id=target.conversation_id,
                client_key=key,
                part=part,
                request_hash=request_hash,
                content_type=content_type,
                content_json=content,
                plain_text=plain,
                at_im_ids=mention if content_type == CONTENT_AT_TEXT else [],
                media_id=media_id,
                status="queued",
                source="tool",
                if_rate_limited=parsed.if_rate_limited,
                send_at=send_at or now,
                next_attempt_at=send_at or now,
            )
            if verdict is not None:
                code, retry_after = verdict
                if code == 5002 and parsed.if_rate_limited == "queue" and send_at is None:
                    row.next_attempt_at = now + timedelta(seconds=retry_after or 10)
                    row.deadline_at = now + RATE_WAIT
                    row.error_code = "5002"
                elif send_at is None or code != 5002:
                    row.status = "rejected"
                    row.error_code = str(code)
                    row.error_message = reason_for(code)[0]
            elif send_at is not None:
                row.status = "scheduled"
            self.db.add(row)
            rows.append(row)
        try:
            await self.db.commit()
        except IntegrityError:
            # 同一个键的并发调用：按已经写下的结果返回
            await self.db.rollback()
            return await self._replay(
                instance, parsed.target, key, request_hash, await self._by_key(instance.id, key)
            )
        for row in rows:
            await ops_metrics.bump(self.ctx.redis, "im_outbox", "created", row.status)
        deadline = time.monotonic() + SYNC_BUDGET_SECONDS
        for item in rows:
            if item.status == "queued" and item.next_attempt_at <= _now():
                if time.monotonic() > deadline:
                    break
                await self.deliver(item.id)
        return await self._output(parsed.target, key, rows, replayed=False)

    async def _group_members(self, target: agent_im.Target) -> set[str]:
        from app.models import ImGroupMember

        if target.group is None:
            return set()
        return set(
            (
                await self.db.scalars(
                    select(ImGroupMember.im_user_id).where(
                        ImGroupMember.group_pk == target.group.id
                    )
                )
            ).all()
        )

    async def _by_key(self, instance_id: uuid.UUID, key: str) -> list[AgentImOutbox]:
        return list(
            (
                await self.db.scalars(
                    select(AgentImOutbox)
                    .where(
                        AgentImOutbox.instance_id == instance_id, AgentImOutbox.client_key == key
                    )
                    .order_by(AgentImOutbox.part)
                )
            ).all()
        )

    async def _replay(
        self,
        instance: AgentInstance,
        target: str,
        key: str,
        request_hash: str,
        rows: list[AgentImOutbox],
    ) -> ImSendOutput:
        if not rows or rows[0].request_hash != request_hash:
            raise ToolCallError(
                "IDEMPOTENCY_MISMATCH", "这个 client_key 已经用来发过别的内容；换一个键"
            )
        for row in rows:
            if row.status in ("queued", "unknown") and row.next_attempt_at <= _now():
                await self.deliver(row.id)
        return await self._output(target, key, rows, replayed=True)

    async def _output(
        self, target: str, key: str, rows: list[AgentImOutbox], *, replayed: bool
    ) -> ImSendOutput:
        items: list[ImSendResultItem] = []
        for row in rows:
            await self.db.refresh(row)
            items.append(self._item(row))
        statuses = {i.status for i in items}
        overall: Literal["sent", "queued", "scheduled", "rejected", "failed", "partial"]
        if statuses == {"sent"}:
            overall = "sent"
        elif statuses <= {"queued", "sending", "unknown"}:
            overall = "queued"
        elif statuses == {"scheduled"}:
            overall = "scheduled"
        elif statuses == {"rejected"}:
            overall = "rejected"
        elif statuses <= {"failed", "cancelled"}:
            overall = "failed"
        else:
            overall = "partial"
        hint = None
        if overall == "queued":
            hint = (
                "还没发出：工作台会自动重试，稍后用 im_outbox 查看结果；"
                "不要用新的 client_key 再发一次"
            )
        elif overall == "rejected":
            hint = next((r.reason for r in items if r.reason), None)
        return ImSendOutput(
            status=overall,
            target=target,
            client_key=key,
            replayed=replayed,
            messages=items,
            hint=hint,
        )

    @staticmethod
    def _item(row: AgentImOutbox) -> ImSendResultItem:
        code = int(row.error_code) if row.error_code and row.error_code.isdigit() else None
        reason = row.error_message
        retryable = row.status in ("queued", "scheduled", "unknown", "sending")
        retry_after: int | None = None
        if code is not None and row.status == "rejected":
            text, retryable, hint = reason_for(code)
            reason = f"{text}。{hint}" if hint else text
        if row.status in ("queued", "unknown") and row.next_attempt_at:
            retry_after = max(0, int((row.next_attempt_at - _now()).total_seconds()))
        return ImSendResultItem(
            message_id=row.id,
            part=row.part,
            kind=_kind(row.content_type),
            status=row.status,
            sent_at=row.sent_at,
            next_attempt_at=row.next_attempt_at if row.status in PENDING else None,
            code=code if row.status == "rejected" or code == 5002 else None,
            reason=reason,
            retryable=retryable,
            retry_after_seconds=retry_after,
        )

    # ------------------------------------------------------------ 投递

    async def deliver(self, row_id: uuid.UUID) -> None:
        """发一条到期的消息；拿不到锁（别处在发）就跳过。"""
        now = _now()
        row = await self.db.scalar(
            select(AgentImOutbox)
            .where(
                AgentImOutbox.id == row_id,
                AgentImOutbox.status.in_(PENDING),
                AgentImOutbox.next_attempt_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
        if row is None:
            await self.db.rollback()
            return
        instance = await self.db.get(AgentInstance, row.instance_id)
        if (
            instance is None
            or instance.status == "disabled"
            or not await _agents_enabled(self.db, row.workspace_id)
        ):
            self._finish(row, "cancelled", "AGENT_DISABLED", "实例已停用或智能体总开关已关闭")
            await self._fail_local(row)
            await self.db.commit()
            return
        if row.deadline_at is not None and now > row.deadline_at:
            self._finish(row, "rejected", "5002", reason_for(5002)[0])
            await self._fail_local(row)
            await self.db.commit()
            return
        previous = await self._previous_pending(row)
        if previous is not None:
            row.next_attempt_at = max(previous.next_attempt_at, now) + timedelta(seconds=2)
            await self.db.commit()
            return
        if row.status == "unknown":
            if await self._resolve_unknown(row):
                await self.db.commit()
                return
            if now - row.updated_at < UNKNOWN_GRACE:
                row.next_attempt_at = now + timedelta(seconds=30)
                await self.db.commit()
                return
            # 宽限期过了还没在 OpenIM 里出现：当作没有发出，重新发
        client = self.ctx.client
        if client is None:
            self._retry(row, now, "EXTERNAL_UNAVAILABLE", "IM 服务暂时不可用")
            await self.db.commit()
            return
        row.status = "sending"
        row.attempts += 1
        local = await self._ensure_local(row, instance, now)
        await self.db.commit()
        kind, _, ident = row.target.partition(":")
        try:
            ex = signed_ex(self._ex_secret(), row.id)
            if row.content_type in (CONTENT_TEXT, CONTENT_AT_TEXT):
                sent = await client.send_text(
                    send_id=instance.im_user_id,
                    text=row.plain_text or "",
                    recv_id=ident if kind == "user" else "",
                    group_id=ident if kind == "group" else "",
                    at_user_ids=row.at_im_ids,
                    sender_nickname=instance.im_nickname,
                    ex=ex,
                )
            else:
                sent = await client.send_elem(
                    send_id=instance.im_user_id,
                    content_type=row.content_type,
                    content=row.content_json,
                    recv_id=ident if kind == "user" else "",
                    group_id=ident if kind == "group" else "",
                    sender_nickname=instance.im_nickname,
                    ex=ex,
                )
        except OpenIMError as error:
            await self._after_error(row, instance, local, error)
            return
        except ConnectorError as error:
            await self._after_error(row, instance, local, error)
            return
        row = await self._reload(row.id)
        refreshed = await self.db.get(ImMessage, local.id)
        row.status = "sent"
        row.server_msg_id = sent.server_msg_id or None
        row.sent_at = (
            datetime.fromtimestamp(sent.send_time_ms / 1000, UTC) if sent.send_time_ms > 0 else now
        )
        row.error_code = None
        row.error_message = None
        if refreshed is not None:
            refreshed.status = "sent"
            refreshed.server_msg_id = sent.server_msg_id or None
            if sent.client_msg_id:
                refreshed.client_msg_id = sent.client_msg_id
            refreshed.sent_at = row.sent_at
        await self._mark_media_used(row)
        events.mark(self.db, row.workspace_id, "im")
        await self.db.commit()
        await ops_metrics.bump(self.ctx.redis, "im_outbox", "sent", row.source)

    def _ex_secret(self) -> str | None:
        secret = self.ctx.settings.openim_secret if self.ctx.settings is not None else None
        return secret.get_secret_value() if secret is not None else None

    async def _mark_media_used(self, row: AgentImOutbox) -> None:
        """附件发出后：媒体（视频连同封面）记为已用，归到这个会话（管理员与实例按会话取）。"""
        ids: list[uuid.UUID] = []
        if row.media_id is not None:
            ids.append(row.media_id)
        cover = row.content_json.get("snapshotUUID") if row.content_type == 104 else None
        if cover:
            with contextlib.suppress(ValueError):
                ids.append(uuid.UUID(str(cover)))
        if not ids:
            return
        await self.db.execute(
            update(ImMedia)
            .where(ImMedia.id.in_(ids), ImMedia.workspace_id == row.workspace_id)
            .values(
                status="used", conversation_id=row.conversation_id, server_msg_id=row.server_msg_id
            )
        )

    async def _previous_pending(self, row: AgentImOutbox) -> AgentImOutbox | None:
        """同一次发送里前一条还没结束：等它（一条一条按顺序发）。"""
        if row.part == 0:
            return None
        return await self.db.scalar(
            select(AgentImOutbox).where(
                AgentImOutbox.instance_id == row.instance_id,
                AgentImOutbox.client_key == row.client_key,
                AgentImOutbox.part == row.part - 1,
                AgentImOutbox.status.in_((*PENDING, "sending")),
            )
        )

    async def _reload(self, row_id: uuid.UUID) -> AgentImOutbox:
        row = await self.db.get(AgentImOutbox, row_id, with_for_update=True, populate_existing=True)
        if row is None:
            raise RuntimeError("发件记录不存在")
        return row

    async def _after_error(
        self,
        row: AgentImOutbox,
        instance: AgentInstance,
        local: ImMessage,
        error: ConnectorError,
    ) -> None:
        row = await self._reload(row.id)
        now = _now()
        code = error.err_code if isinstance(error, OpenIMError) else None
        if code is not None and code in REJECT_CODES and code != 5009:
            if (
                code == 5002
                and row.if_rate_limited == "queue"
                and (row.deadline_at is None or now < row.deadline_at)
            ):
                ident = row.target.partition(":")[2]
                wait = await _rate_retry_after(self.ctx.redis, ident, instance.im_user_id)
                row.status = "queued"
                row.error_code = "5002"
                row.error_message = reason_for(5002)[0]
                row.next_attempt_at = now + timedelta(seconds=wait)
                row.deadline_at = row.deadline_at or now + RATE_WAIT
            else:
                self._finish(row, "rejected", str(code), reason_for(code)[0])
                await self._fail_local(row)
            await self.db.commit()
            await ops_metrics.bump(self.ctx.redis, "im_outbox", "rejected", str(code))
            return
        if code is not None or error.not_sent or error.kind in ("AUTH_FAILED", "UNAVAILABLE"):
            # 确定没有发出（或 5009 判断不了）：退避重试
            self._retry(row, now, error.kind, "IM 服务暂时不可用，稍后自动重试")
        else:
            # 请求可能已经到了 OpenIM：先对账，再决定要不要重发
            row.status = "unknown"
            row.error_code = "RESULT_UNKNOWN"
            row.error_message = "发送结果未知，工作台正在确认"
            row.next_attempt_at = now + timedelta(seconds=30)
        if row.status == "failed":
            await self._fail_local(row)
        await self.db.commit()
        logger.warning(
            "agent im send failed",
            extra={"error_kind": error.kind, "outbox_id": str(row.id), "status": row.status},
        )

    def _retry(self, row: AgentImOutbox, now: datetime, code: str, message: str) -> None:
        if row.attempts >= MAX_ATTEMPTS or now - row.created_at > GIVE_UP_AFTER:
            self._finish(row, "failed", code, "多次重试仍然没有发出")
            return
        row.status = "queued"
        row.error_code = code
        row.error_message = message
        row.next_attempt_at = now + timedelta(seconds=BACKOFF[min(row.attempts, len(BACKOFF) - 1)])

    @staticmethod
    def _finish(row: AgentImOutbox, status: str, code: str | None, message: str | None) -> None:
        row.status = status
        row.error_code = code
        row.error_message = message
        row.next_attempt_at = _now()

    async def _ensure_local(
        self, row: AgentImOutbox, instance: AgentInstance, now: datetime
    ) -> ImMessage:
        """在消息投影里先放一条“发送中”，管理员网页马上看得到；OpenIM 的回调到了会合并。"""
        local = await self.db.scalar(select(ImMessage).where(ImMessage.idempotency_key == row.id))
        if local is not None:
            local.status = "sending"
            return local
        local = ImMessage(
            workspace_id=row.workspace_id,
            conversation_id=row.conversation_id,
            client_msg_id=f"myws-{row.id}",
            sender_im_id=instance.im_user_id,
            sender_instance_id=instance.id,
            content_type=row.content_type,
            content_json=row.content_json,
            plain_text=row.plain_text,
            at_im_ids=list(row.at_im_ids),
            status="sending",
            idempotency_key=row.id,
            sent_at=now,
        )
        self.db.add(local)
        await self.db.flush()
        return local

    async def _fail_local(self, row: AgentImOutbox) -> None:
        local = await self.db.scalar(select(ImMessage).where(ImMessage.idempotency_key == row.id))
        if local is not None and local.server_msg_id is None:
            local.status = "failed"
            events.mark(self.db, row.workspace_id, "im")

    async def _resolve_unknown(self, row: AgentImOutbox) -> bool:
        found = await self.db.scalar(select(ImMessage).where(ImMessage.idempotency_key == row.id))
        if (found is None or found.server_msg_id is None) and self.ctx.client is not None:
            ref = await resolve(self.db, row.conversation_id)
            if ref is not None:
                try:
                    await sync_conversation(self.db, self.ctx.client, ref)
                except ConnectorError:
                    logger.warning("im sync for unknown agent send failed")
            found = await self.db.scalar(
                select(ImMessage).where(ImMessage.idempotency_key == row.id)
            )
        if found is not None and found.server_msg_id is not None:
            found.status = "sent"
            row.status = "sent"
            row.server_msg_id = found.server_msg_id
            row.sent_at = found.sent_at
            row.error_code = None
            row.error_message = None
            return True
        return False

    async def deliver_due(self, limit: int = 100) -> int:
        """Worker：发所有到期的；卡在“发送中”太久的（进程中途退出）先改成结果未知再对账。"""
        now = _now()
        await self.db.execute(
            update(AgentImOutbox)
            .where(
                AgentImOutbox.status == "sending", AgentImOutbox.updated_at < now - STUCK_SENDING
            )
            .values(status="unknown", next_attempt_at=now, error_code="RESULT_UNKNOWN")
        )
        await self.db.execute(
            update(AgentImOutbox)
            .where(AgentImOutbox.status == "scheduled", AgentImOutbox.next_attempt_at <= now)
            .values(status="queued")
        )
        ids = list(
            (
                await self.db.scalars(
                    select(AgentImOutbox.id)
                    .where(
                        AgentImOutbox.status.in_(("queued", "unknown")),
                        AgentImOutbox.next_attempt_at <= now,
                    )
                    .order_by(AgentImOutbox.next_attempt_at, AgentImOutbox.created_at)
                    .limit(limit)
                )
            ).all()
        )
        await self.db.commit()
        for row_id in ids:
            await self.deliver(row_id)
        return len(ids)

    # ------------------------------------------------------------ 取消与查询

    async def cancel(self, instance: AgentInstance, parsed: ImCancelInput) -> ImCancelOutput:
        rows = (
            await self.db.scalars(
                select(AgentImOutbox)
                .where(
                    AgentImOutbox.id.in_(parsed.message_ids),
                    AgentImOutbox.instance_id == instance.id,
                    AgentImOutbox.status.in_(("scheduled", "queued")),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        cancelled = []
        for row in rows:
            self._finish(row, "cancelled", "CANCELLED", "已取消")
            await self._fail_local(row)
            cancelled.append(row.id)
        return ImCancelOutput(
            cancelled=cancelled,
            not_cancellable=[i for i in dict.fromkeys(parsed.message_ids) if i not in cancelled],
        )

    async def list(self, instance_id: uuid.UUID, parsed: ImOutboxInput) -> ImOutboxOutput:
        query = select(AgentImOutbox).where(AgentImOutbox.instance_id == instance_id)
        groups = {
            "pending": ("scheduled", "queued", "sending", "unknown"),
            "sent": ("sent",),
            "rejected": ("rejected",),
            "failed": ("failed", "cancelled"),
        }
        if parsed.status != "all":
            query = query.where(AgentImOutbox.status.in_(groups[parsed.status]))
        if parsed.since is not None:
            query = query.where(AgentImOutbox.created_at >= parsed.since)
        rows = (
            await self.db.scalars(
                query.order_by(AgentImOutbox.created_at.desc(), AgentImOutbox.part).limit(
                    parsed.limit
                )
            )
        ).all()
        items = [
            ImOutboxItem(
                message_id=r.id,
                target=r.target,
                kind=_kind(r.content_type),
                status=r.status,
                source=r.source,
                created_at=r.created_at,
                sent_at=r.sent_at,
                next_attempt_at=r.next_attempt_at if r.status in PENDING else None,
                code=int(r.error_code) if r.error_code and r.error_code.isdigit() else None,
                reason=r.error_message,
                untrusted=ImOutboxText(text=(r.plain_text or "")[:200] or None),
            )
            for r in rows
        ]
        replies = []
        if parsed.status in ("all", "rejected"):
            since = parsed.since or _now() - REJECTION_RETENTION
            for r in (
                await self.db.scalars(
                    select(AgentImRejection)
                    .where(
                        AgentImRejection.instance_id == instance_id,
                        AgentImRejection.created_at >= since,
                    )
                    .order_by(AgentImRejection.created_at.desc())
                    .limit(parsed.limit)
                )
            ).all():
                replies.append(
                    ImRejectedReply(
                        conversation_id=r.conversation_id,
                        code=r.code,
                        reason=reason_for(r.code)[0],
                        retried=r.retried,
                        created_at=r.created_at,
                        untrusted=ImOutboxText(text=r.preview or None),
                    )
                )
        return ImOutboxOutput(
            items=items,
            rejected_replies=replies,
            wakeups=await agent_wakeups.pending_items(self.db, instance_id),
        )

    async def recent_rejections(self, instance_id: uuid.UUID, hours: int = 24) -> int:
        since = _now() - timedelta(hours=hours)
        replies = await self.db.scalar(
            select(func.count())
            .select_from(AgentImRejection)
            .where(
                AgentImRejection.instance_id == instance_id, AgentImRejection.created_at >= since
            )
        )
        sends = await self.db.scalar(
            select(func.count())
            .select_from(AgentImOutbox)
            .where(
                AgentImOutbox.instance_id == instance_id,
                AgentImOutbox.status == "rejected",
                AgentImOutbox.created_at >= since,
            )
        )
        return int(replies or 0) + int(sends or 0)


async def sends_left(redis: Redis | None, instance_id: uuid.UUID) -> int:
    if redis is None:
        return SENDS_PER_HOUR
    used = int(await redis.get(HOUR_KEY.format(instance_id=instance_id)) or 0)
    return max(0, SENDS_PER_HOUR - used)


# ---------------------------------------------------------------- 回调里的拒收


def _callback_text(msg: MsgCallback) -> tuple[str | None, list[str]]:
    try:
        content = json.loads(msg.content) if msg.content else {}
    except ValueError:
        return None, []
    if not isinstance(content, dict):
        return None, []
    if msg.content_type == CONTENT_TEXT:
        text = content.get("content")
        return (str(text) if text else None), []
    if msg.content_type == CONTENT_AT_TEXT:
        text = content.get("text")
        at = content.get("atUserList") or []
        return (str(text) if text else None), [str(a) for a in at if isinstance(a, str)]
    return None, []


def _outbox_key(raw: str) -> bool:
    """ex 里带着发件箱 ID：这条是 im_send 发的，拒收由发件箱自己记录。"""
    if not raw or len(raw) > 1024:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    return isinstance(data, dict) and EX_KEY in data


async def record_rejection(
    db: AsyncSession, redis: Redis | None, msg: MsgCallback, code: int
) -> AgentImRejection | None:
    """before 回调拒收了实例发的消息（插件发的自动回复）：记下来；5002 的文字回复排队补发。"""
    if not msg.send_id.startswith("oc_") or _outbox_key(msg.ex):
        return None
    instance = await db.scalar(select(AgentInstance).where(AgentInstance.im_user_id == msg.send_id))
    if instance is None:
        return None
    conversation = (
        group_conversation_id(msg.group_id)
        if msg.group_id
        else single_conversation_id(msg.send_id, msg.recv_id)
    )
    text, at = _callback_text(msg)
    preview = redact(text or "")[:PREVIEW_CHARS]
    client_msg_id = msg.client_msg_id or msg.server_msg_id or uuid.uuid4().hex
    inserted = await db.execute(
        pg_insert(AgentImRejection)
        .values(
            workspace_id=instance.workspace_id,
            instance_id=instance.id,
            conversation_id=conversation,
            code=code,
            content_type=msg.content_type,
            client_msg_id=client_msg_id,
            preview=preview,
        )
        .on_conflict_do_nothing(index_elements=["instance_id", "client_msg_id"])
        .returning(AgentImRejection.id)
    )
    rejection_id = inserted.scalar()
    if rejection_id is None:
        await db.commit()
        return None
    rejection = await db.get(AgentImRejection, rejection_id)
    if rejection is None:
        return None
    if code == 5002 and msg.group_id and text:
        pending = await db.scalar(
            select(func.count())
            .select_from(AgentImOutbox)
            .where(
                AgentImOutbox.instance_id == instance.id,
                AgentImOutbox.conversation_id == conversation,
                AgentImOutbox.source == "reply_retry",
                AgentImOutbox.status.in_(PENDING),
            )
        )
        if int(pending or 0) < REPLY_RETRY_MAX:
            wait = await _rate_retry_after(redis, msg.group_id, msg.send_id)
            now = _now()
            db.add(
                AgentImOutbox(
                    workspace_id=instance.workspace_id,
                    instance_id=instance.id,
                    target=f"group:{msg.group_id}",
                    conversation_id=conversation,
                    client_key=f"reply-{client_msg_id}"[:64],
                    part=0,
                    request_hash="reply",
                    content_type=CONTENT_AT_TEXT if at else CONTENT_TEXT,
                    content_json={"text": text, "atUserList": at} if at else {"content": text},
                    plain_text=text,
                    at_im_ids=at,
                    status="queued",
                    source="reply_retry",
                    if_rate_limited="queue",
                    next_attempt_at=now + timedelta(seconds=wait),
                    deadline_at=now + REPLY_RETRY_WAIT,
                )
            )
            rejection.retried = True
    await db.commit()
    await ops_metrics.bump(redis, "im_reply_rejections", str(code))
    return rejection


async def purge(db: AsyncSession) -> tuple[int, int]:
    from sqlalchemy import delete

    now = _now()
    outbox = await db.execute(
        delete(AgentImOutbox).where(
            AgentImOutbox.created_at < now - OUTBOX_RETENTION,
            AgentImOutbox.status.in_(FINISHED),
        )
    )
    rejections = await db.execute(
        delete(AgentImRejection).where(AgentImRejection.created_at < now - REJECTION_RETENTION)
    )
    return int(outbox.rowcount or 0), int(rejections.rowcount or 0)  # type: ignore[attr-defined]


# ---------------------------------------------------------------- MCP 工具


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
    im: OpenIMClient | None,
) -> BaseModel:
    """im_send / im_cancel（直接执行，不审批：docs/AGENTS.md 第 8 节例外四）。"""
    try:
        parsed: BaseModel = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = parsed.model_dump(mode="json")
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    instance_id = requester.instance.id
    transport = getattr(service.runtime, "transport", None)
    outbox = AgentOutbox(SendContext(service.db, service.settings, service.redis, im, transport))
    try:
        if isinstance(parsed, ImSendInput):
            output: BaseModel = await outbox.send(requester.instance, parsed)
            summary = f"主动发消息：{parsed.target}（{output.status}）"  # type: ignore[attr-defined]
        elif isinstance(parsed, ScheduleWakeupInput):
            scheduled = await agent_wakeups.schedule(service.db, requester.instance, parsed)
            output = scheduled
            summary = f"安排唤醒：{scheduled.run_at.isoformat(timespec='minutes')}"
        elif isinstance(parsed, CancelWakeupInput):
            output = await agent_wakeups.cancel_own(service.db, requester.instance, parsed)
            summary = "取消唤醒"
        else:
            if not isinstance(parsed, ImCancelInput):
                raise ToolCallError("NOT_FOUND", "没有这个工具")
            output = await outbox.cancel(requester.instance, parsed)
            await service.db.commit()
            summary = f"取消排队的消息：{len(output.cancelled)} 条"
    except ToolCallError as error:
        await service.db.rollback()
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            await service.record_knowledge(
                await service.requester(instance),
                spec,
                _record_args(arguments),
                ok=False,
                summary=f"{spec.name}：{error.code}",
                error_code=error.code,
                request_id=request_id,
            )
        raise
    instance = await service.db.get(AgentInstance, instance_id)
    if instance is not None:
        await service.record_knowledge(
            await service.requester(instance),
            spec,
            _record_args(arguments),
            ok=True,
            summary=summary,
            request_id=request_id,
        )
    return output


def _record_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """执行记录里不放完整正文（只留前 200 字），也不放附件内容（方案第 6 节“不记附件内容”）：
    data_base64 / text_content（包括视频封面的）只记长度，被密钥拦截的内容也就不会进执行记录。"""
    out = dict(arguments)
    text = out.get("text")
    if isinstance(text, str) and len(text) > 200:
        out["text"] = text[:200] + "…"
    attachments = out.get("attachments")
    if isinstance(attachments, list):
        out["attachments"] = [_record_attachment(a) for a in attachments]
    return out


def _record_attachment(item: object) -> object:
    if not isinstance(item, dict):
        return item
    out = dict(item)
    data = out.get("data_base64")
    if isinstance(data, str):
        out["data_base64"] = f"（{len(data)} 个 base64 字符，不记录）"
    content = out.get("text_content")
    if isinstance(content, str):
        out["text_content"] = f"（{len(content)} 字，不记录）"
    if isinstance(out.get("cover"), dict):
        out["cover"] = _record_attachment(out["cover"])
    return out
