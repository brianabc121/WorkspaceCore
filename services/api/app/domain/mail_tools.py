"""智能体的邮件工具（T-0129，docs/v2/MAIL_PLAN.md 第 5 节；docs/AGENTS.md 第 8 节例外七）。

- 读（mail_accounts / mail_search / mail_get / mail_thread）：实例的“查询邮件”开关（默认打开）；
- 发（mail_send）：实例的“发送邮件”开关（默认关闭，owner / admin 打开时必须选收件人范围），
  直接发出、不审批，但必须经过：权限范围与角色、邮箱分配给了这个实例（T-0132）与 gmail.send 授权、
  收件人范围、密钥拦截（MailService.send）、每小时 10 封 / 每天 50 封、client_key 24 小时防重复、
  署名行、执行记录（不存正文）、审计、通知管理员；结果未知时不自动重发。
- 智能体只看到管理员分配给它的邮箱（agent_mail_accounts）；拿不到 Google 令牌，
  也不能删除或修改邮件。
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain import mail_format as fmt
from app.domain import notifications
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.audit import AuditTrail
from app.domain.mail import MailError, MailService, Sender, can_send
from app.domain.ratelimit import WindowLimiter
from app.models import AgentInstance, AgentMailAccount, MailAccount, MailSend
from app.schemas.ai_tools import MAIL_READ_TOOL_NAMES, ToolSpec
from app.schemas.mail_tools import (
    MailAccountItem,
    MailAccountsOutput,
    MailAttachmentItem,
    MailAttachmentText,
    MailGetInput,
    MailGetOutput,
    MailMessageItem,
    MailMessageText,
    MailSearchInput,
    MailSearchOutput,
    MailSendInput,
    MailSendOutput,
    MailSummaryItem,
    MailSummaryText,
    MailThreadInput,
    MailThreadOutput,
)

logger = logging.getLogger("app.mail")

SENDS_PER_HOUR = 10
SENDS_PER_DAY = 50
REPLAY_WINDOW = timedelta(hours=24)


def signature(instance_name: str) -> str:
    return f"—— 由工作台智能体「{instance_name}」发送"


def in_scope(address: str, allowed: Sequence[str]) -> bool:
    """收件人范围：完整地址（a@b.com）或域名（@b.com，只匹配这个域名本身）。"""
    address = address.strip().lower()
    domain = "@" + address.rpartition("@")[2]
    for entry in allowed:
        entry = entry.strip().lower()
        if entry == address or (entry.startswith("@") and entry == domain):
            return True
    return False


def _service(service: AiToolService) -> MailService:
    if service.redis is None:
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "邮件服务暂时不可用")
    return MailService(service.db, service.settings, service.redis, service.runtime)


async def _accounts(service: AiToolService, requester: Requester) -> list[MailAccount]:
    """分配给这个实例、没断开的邮箱（T-0132）。"""
    return list(
        await service.db.scalars(
            select(MailAccount)
            .join(AgentMailAccount, AgentMailAccount.account_id == MailAccount.id)
            .where(
                AgentMailAccount.instance_id == requester.instance.id,
                MailAccount.workspace_id == requester.workspace.id,
                MailAccount.status != "disconnected",
            )
            .order_by(MailAccount.created_at)
        )
    )


async def _account(
    service: AiToolService, requester: Requester, account_id: uuid.UUID | None
) -> MailAccount:
    accounts = await _accounts(service, requester)
    if account_id is not None:
        found = next((a for a in accounts if a.id == account_id), None)
        if found is None:
            raise ToolCallError(
                "NOT_FOUND", "没有这个邮箱，或管理员没有把它分配给你；用 mail_accounts 查看"
            )
        return found
    if not accounts:
        raise ToolCallError(
            "NOT_FOUND", "管理员还没有给你分配 Gmail 邮箱（在 设置 → 邮件 或实例卡片里分配）"
        )
    if len(accounts) > 1:
        raise ToolCallError(
            "VALIDATION_FAILED", "有多个邮箱，请用 account_id 指定（见 mail_accounts）"
        )
    return accounts[0]


def _tool_error(error: MailError) -> ToolCallError:
    return ToolCallError(error.code, error.message)


def _summary(parsed: fmt.ParsedMessage) -> MailSummaryItem:
    headers = parsed.headers
    return MailSummaryItem(
        message_id=parsed.message_id,
        thread_id=parsed.thread_id,
        date=parsed.internal_date,
        unread=parsed.unread,
        untrusted=MailSummaryText(
            sender=headers.get("From", ""),
            to=headers.get("To", ""),
            cc=headers.get("Cc", ""),
            subject=headers.get("Subject", ""),
            snippet=parsed.snippet,
            labels=parsed.labels,
        ),
    )


def _message(parsed: fmt.ParsedMessage) -> MailMessageItem:
    headers = parsed.headers
    return MailMessageItem(
        message_id=parsed.message_id,
        thread_id=parsed.thread_id,
        date=parsed.internal_date,
        unread=parsed.unread,
        truncated=parsed.truncated,
        attachments=[
            MailAttachmentItem(
                size=a.size,
                untrusted=MailAttachmentText(filename=a.filename, mime_type=a.mime_type),
            )
            for a in parsed.attachments
        ],
        untrusted=MailMessageText(
            sender=headers.get("From", ""),
            to=headers.get("To", ""),
            cc=headers.get("Cc", ""),
            subject=headers.get("Subject", ""),
            text=parsed.text,
            labels=parsed.labels,
        ),
    )


# ---------------------------------------------------------------- 读


async def _read(
    service: AiToolService, requester: Requester, spec: ToolSpec, parsed: BaseModel
) -> BaseModel:
    instance = requester.instance
    if spec.name == "mail_accounts":
        send = instance.mail_send_enabled and instance.scope == "read_write"
        return MailAccountsOutput(
            items=[
                MailAccountItem(account_id=a.id, email=a.email, can_send=send and can_send(a))
                for a in await _accounts(service, requester)
            ],
            send_enabled=instance.mail_send_enabled,
            send_scope=instance.mail_send_scope if instance.mail_send_enabled else None,
            allowed_recipients=list(instance.mail_send_recipients or [])
            if instance.mail_send_enabled and instance.mail_send_scope == "listed"
            else [],
        )
    mail = _service(service)
    try:
        if isinstance(parsed, MailSearchInput):
            account = await _account(service, requester, parsed.account_id)
            listing = await mail.search(
                account,
                query=parsed.query,
                label=parsed.label,
                max_results=parsed.max_results,
                page_token=parsed.page_token,
            )
            return MailSearchOutput(
                account_id=account.id,
                items=[_summary(m) for m in listing.messages],
                next_page_token=listing.next_page_token,
                estimate=listing.estimate,
            )
        if isinstance(parsed, MailGetInput):
            account = await _account(service, requester, parsed.account_id)
            message = await mail.message(account, parsed.message_id)
            return MailGetOutput(account_id=account.id, message=_message(message))
        if isinstance(parsed, MailThreadInput):
            account = await _account(service, requester, parsed.account_id)
            messages = await mail.thread(account, parsed.thread_id)
            return MailThreadOutput(
                account_id=account.id,
                thread_id=parsed.thread_id,
                messages=[_message(m) for m in messages],
            )
    except MailError as error:
        raise _tool_error(error) from None
    raise ToolCallError("NOT_FOUND", "没有这个工具")


# ---------------------------------------------------------------- 发


def _send_out(record: MailSend, *, replayed: bool) -> MailSendOutput:
    return MailSendOutput(
        send_id=record.id,
        status=record.status,
        replayed=replayed,
        gmail_message_id=record.gmail_message_id,
        gmail_thread_id=record.gmail_thread_id,
        error_code=record.error_code,
    )


async def _replay(
    service: AiToolService, instance_id: uuid.UUID, client_key: str
) -> MailSendOutput | None:
    found = await service.db.scalar(
        select(MailSend).where(
            MailSend.instance_id == instance_id, MailSend.client_key == client_key
        )
    )
    if found is None:
        return None
    if found.created_at < datetime.now(UTC) - REPLAY_WINDOW:
        raise ToolCallError("VALIDATION_FAILED", "这个 client_key 已经用过了，换一个新的")
    return _send_out(found, replayed=True)


async def _limit(service: AiToolService, instance_id: uuid.UUID) -> None:
    if service.redis is None:
        return
    hour = await WindowLimiter(service.redis, 3600).hit(
        f"mail_sends:h:{instance_id}", SENDS_PER_HOUR
    )
    if hour is not None:
        raise ToolCallError("RATE_LIMITED", f"每小时最多发 {SENDS_PER_HOUR} 封")
    day = await WindowLimiter(service.redis, 86400).hit(
        f"mail_sends:d:{instance_id}", SENDS_PER_DAY
    )
    if day is not None:
        raise ToolCallError("RATE_LIMITED", f"每天最多发 {SENDS_PER_DAY} 封")


async def _send(
    service: AiToolService, requester: Requester, args: MailSendInput
) -> tuple[MailSendOutput, str]:
    instance = requester.instance
    instance_id, instance_name = instance.id, instance.name
    workspace_id = requester.workspace.id
    if args.client_key:
        replay = await _replay(service, instance_id, args.client_key)
        if replay is not None:
            return replay, f"mail_send：重复的 client_key，返回之前的结果（{replay.status}）"
    account = await _account(service, requester, args.account_id)
    if not can_send(account):
        raise ToolCallError(
            "MAIL_SCOPE_MISSING", f"邮箱 {account.email} 没有授权发信或需要重新连接，请管理员处理"
        )
    recipients = [*args.to, *args.cc, *args.bcc]
    try:
        cleaned = MailService.clean_recipients(recipients)
    except MailError as error:
        raise _tool_error(error) from None
    if instance.mail_send_scope != "any":
        allowed = list(instance.mail_send_recipients or [])
        outside = [r for r in cleaned if not in_scope(r, allowed)]
        if outside:
            raise ToolCallError(
                "RECIPIENT_NOT_ALLOWED",
                "这些收件人不在管理员允许的范围里：" + "、".join(outside[:5]),
            )
    await _limit(service, instance_id)
    email = account.email
    footer = signature(instance_name) if account.agent_signature else None
    mail = _service(service)
    try:
        record = await mail.send(
            account,
            Sender(kind="instance", name=instance_name, instance_id=instance_id),
            to=args.to,
            cc=args.cc,
            bcc=args.bcc,
            subject=args.subject,
            body=args.body,
            reply_to_message_id=args.reply_to_message_id,
            client_key=args.client_key,
            footer=footer,
        )
    except IntegrityError:
        # 同一个 client_key 的并发重试：另一个请求先写了记录。回滚会让 ORM 对象过期，
        # 后面写执行记录要用到实例、工作空间与登记人，先重新读出来（异步会话不能隐式加载）
        await service.db.rollback()
        await service.db.refresh(requester.instance)
        await service.db.refresh(requester.workspace)
        if requester.acting_user is not None:
            await service.db.refresh(requester.acting_user)
        replay = await _replay(service, instance_id, args.client_key or "")
        if replay is None:
            raise
        return replay, f"mail_send：重复的 client_key，返回之前的结果（{replay.status}）"
    except MailError as error:
        await _after_failure(service, workspace_id, instance_id, instance_name, email, error)
        raise _tool_error(error) from None
    subject = record.subject
    count = len(record.to_addresses) + len(record.cc_addresses) + record.bcc_count
    await AuditTrail(service.db, service.settings).record(
        "mail.sent",
        workspace_ids=[workspace_id],
        actor_id=None,
        resource_type="mail_send",
        resource_id=record.id,
        metadata={
            "by_instance": str(instance_id),
            "email": email,
            "recipients": count,
            "reply": bool(record.reply_to_message_id),
        },
    )
    await notifications.notify_admins(
        service.db,
        workspace_id,
        type_="mail.sent_by_agent",
        severity="info",
        title=f"智能体「{instance_name}」发了一封邮件",
        body=f"从 {email} 发给 {', '.join(record.to_addresses[:5])}"
        + (f" 等 {count} 人" if count > 1 else "")
        + f"；主题：{subject}",
        link="/mail?tab=sends",
        key=str(record.id),
    )
    output = _send_out(record, replayed=False)
    return output, f"发信给 {count} 个收件人：{subject}"


async def _after_failure(
    service: AiToolService,
    workspace_id: uuid.UUID,
    instance_id: uuid.UUID,
    instance_name: str,
    email: str,
    error: MailError,
) -> None:
    """发信结果未知：通知管理员去 Gmail 的“已发送”里确认（不会自动重发）。"""
    if error.code != "TIMEOUT_UNKNOWN":
        return
    record = await service.db.scalar(
        select(MailSend)
        .where(MailSend.instance_id == instance_id, MailSend.status == "unknown")
        .order_by(MailSend.created_at.desc())
        .limit(1)
    )
    await AuditTrail(service.db, service.settings).record(
        "mail.send_unknown",
        workspace_ids=[workspace_id],
        actor_id=None,
        resource_type="mail_send",
        resource_id=record.id if record else None,
        metadata={"by_instance": str(instance_id), "email": email},
    )
    await notifications.notify_admins(
        service.db,
        workspace_id,
        type_="mail.send_unknown",
        severity="warning",
        title=f"智能体「{instance_name}」发的一封邮件结果未知",
        body=(
            f"Gmail 没有及时响应，不知道 {email} 有没有发出去；"
            "不会自动重发，请到 Gmail 的“已发送”里确认。"
        ),
        link="/mail?tab=sends",
        key=str(record.id) if record else str(instance_id),
    )
    await service.db.commit()


# ---------------------------------------------------------------- 入口


def _arguments(spec: ToolSpec, parsed: BaseModel) -> dict[str, Any]:
    """执行记录里的参数：发信不存正文，只记长度。"""
    arguments = parsed.model_dump(mode="json")
    if spec.name == "mail_send":
        body = str(arguments.pop("body", ""))
        arguments["body_chars"] = len(body)
        arguments["bcc"] = len(arguments.get("bcc") or [])
    return arguments


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = _arguments(spec, parsed)
    reading = spec.name in MAIL_READ_TOOL_NAMES
    instance = requester.instance
    denied = service.check(requester, spec)
    if denied is None:
        enabled = instance.mail_read_enabled if reading else instance.mail_send_enabled
        if not enabled:
            denied = "FORBIDDEN_CAPABILITY"
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        if denied == "FORBIDDEN_CAPABILITY":
            message = (
                "管理员关掉了你查询邮件的权限" if reading else "管理员还没有给你开“发送邮件”的权限"
            )
        else:
            message = "这个实例没有权限调用这个工具"
        raise ToolCallError(denied, message)
    instance_id = instance.id
    if reading:
        try:
            output = await _read(service, requester, spec, parsed)
        except ToolCallError as error:
            await service.record_read(
                requester, spec, arguments, ok=False, error_code=error.code, request_id=request_id
            )
            raise
        await service.record_read(requester, spec, arguments, ok=True, request_id=request_id)
        return output
    if not isinstance(parsed, MailSendInput):
        raise ToolCallError("NOT_FOUND", "没有这个工具")
    try:
        sent, summary = await _send(service, requester, parsed)
    except ToolCallError as error:
        await service.db.rollback()
        current = await service.db.get(AgentInstance, instance_id)
        if current is not None:
            await service.record_knowledge(
                await service.requester(current),
                spec,
                arguments,
                ok=False,
                summary=f"mail_send：{error.code}",
                error_code=error.code,
                request_id=request_id,
            )
        raise
    await service.record_knowledge(
        requester,
        spec,
        arguments,
        ok=sent.status == "sent",
        summary=summary,
        error_code=sent.error_code,
        request_id=request_id,
    )
    return sent


__all__ = ["SENDS_PER_DAY", "SENDS_PER_HOUR", "in_scope", "run", "signature"]
