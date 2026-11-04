"""智能体的 IM 会话目录与读消息（T-0117，docs/v2/AGENT_MESSAGING_PLAN.md 第 3 节）。

- `im_list_targets`：能私聊的人（本工作空间状态正常的 owner / admin，
  与 OpenIM 回调的单聊规则一致），
  自己当前所在的群（成员、规则、接力是否暂停、这一分钟还能发几条）。
- `im_read_messages`：只读自己的会话——与管理员的私聊、当前所在的群里入群之后的消息；
  数据来自工作台的消息投影（im_messages），不新开 OpenIM 读接口。

名字、群名、正文等来自用户输入的文字放进 `untrusted`（与其他读工具一致）。
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ai_tools import ToolCallError
from app.domain.im import IM_ROLES, NOTICE_USER_ID, admin_im_user_id, user_id_from_im
from app.domain.im_callbacks import RATE_KEY, STREAK_KEY
from app.integrations.openim import (
    CONTENT_AT_TEXT,
    CONTENT_PICTURE,
    CONTENT_TEXT,
    group_conversation_id,
    single_conversation_id,
)
from app.models import (
    AgentInstance,
    ImAccount,
    ImGroup,
    ImGroupMember,
    ImMessage,
    Project,
    User,
    WorkspaceMember,
)
from app.schemas.ai_tools import (
    ImAttachment,
    ImAttachmentText,
    ImDirectTarget,
    ImGroupMemberItem,
    ImGroupMemberText,
    ImGroupRules,
    ImGroupState,
    ImGroupTarget,
    ImGroupText,
    ImListTargetsInput,
    ImListTargetsOutput,
    ImMe,
    ImMessageItem,
    ImMessageText,
    ImNameText,
    ImReadMessagesInput,
    ImReadMessagesOutput,
)

CONTENT_VIDEO = 104
CONTENT_FILE = 105
TEXT_LIMIT = 2000
SYSTEM_SENDERS = frozenset({NOTICE_USER_ID, "imAdmin"})


@dataclass(frozen=True)
class DirectPeer:
    user_id: uuid.UUID
    im_user_id: str
    role: str
    name: str
    has_account: bool


@dataclass(frozen=True)
class Target:
    """解析后的目标：私聊的管理员或所在的群。"""

    kind: Literal["user", "group"]
    conversation_id: str
    recv_id: str = ""
    group: ImGroup | None = None
    member: ImGroupMember | None = None
    peer: DirectPeer | None = None


async def direct_peers(db: AsyncSession, workspace_id: uuid.UUID) -> list[DirectPeer]:
    """能和实例私聊的人：本工作空间状态正常的 owner / admin（回调第 5.1 节的单聊规则）。"""
    rows = (
        await db.execute(
            select(User.id, User.display_name, WorkspaceMember.role, ImAccount.user_id)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .outerjoin(ImAccount, ImAccount.user_id == User.id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role.in_(IM_ROLES),
                User.status == "active",
            )
            .order_by(WorkspaceMember.role.desc(), User.display_name, User.id)
        )
    ).all()
    return [
        DirectPeer(uid, admin_im_user_id(uid), role, name, account is not None)
        for uid, name, role, account in rows
    ]


async def resolve_target(db: AsyncSession, instance: AgentInstance, target: str) -> Target:
    """`user:<IM 用户 ID>` 只能是本工作空间的管理员；`group:<群 ID>` 只能是自己当前所在的群。"""
    kind, _, ident = target.partition(":")
    if kind == "user":
        user_id = user_id_from_im(ident)
        peers = await direct_peers(db, instance.workspace_id) if user_id else []
        peer = next((p for p in peers if p.user_id == user_id), None)
        if peer is None:
            if ident.startswith("oc_"):
                raise ToolCallError(
                    "TARGET_NOT_ALLOWED",
                    "智能体之间不能私聊：在你们共同所在的群里 @ 它",
                )
            raise ToolCallError(
                "TARGET_NOT_ALLOWED", "只能私聊本工作空间的管理员；用 im_list_targets 查看"
            )
        return Target(
            "user",
            single_conversation_id(instance.im_user_id, peer.im_user_id),
            recv_id=peer.im_user_id,
            peer=peer,
        )
    row = (
        await db.execute(
            select(ImGroup, ImGroupMember)
            .join(ImGroupMember, ImGroupMember.group_pk == ImGroup.id)
            .where(
                ImGroup.group_id == ident,
                ImGroup.workspace_id == instance.workspace_id,
                ImGroup.dismissed_at.is_(None),
                ImGroupMember.instance_id == instance.id,
            )
        )
    ).first()
    if row is None:
        raise ToolCallError(
            "TARGET_NOT_ALLOWED", "你不在这个群里；用 im_list_targets 查看你所在的群"
        )
    group, member = row
    return Target("group", group_conversation_id(group.group_id), group=group, member=member)


async def group_state(redis: Redis | None, group: ImGroup, sender: str) -> ImGroupState:
    """接力是否暂停、这一分钟还能发几条（只读，不计数；判断仍以回调为准）。"""
    streak = 0
    used = 0
    if redis is not None:
        streak = int(await redis.get(STREAK_KEY.format(group_id=group.group_id)) or 0)
        used = int(await redis.get(RATE_KEY.format(group_id=group.group_id, sender=sender)) or 0)
    return ImGroupState(
        paused=streak >= group.max_agent_streak,
        agent_streak=streak,
        remaining_this_minute=max(0, group.max_per_minute - used),
    )


async def _last_message_at(
    db: AsyncSession, conversation_ids: Sequence[str]
) -> dict[str, datetime]:
    if not conversation_ids:
        return {}
    rows = await db.execute(
        select(ImMessage.conversation_id, func.max(ImMessage.sent_at))
        .where(ImMessage.conversation_id.in_(conversation_ids))
        .group_by(ImMessage.conversation_id)
    )
    return {cid: at for cid, at in rows.all() if at is not None}


def _matches(query: str | None, *texts: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(text and needle in text.casefold() for text in texts)


async def list_targets(
    db: AsyncSession, redis: Redis | None, instance: AgentInstance, parsed: ImListTargetsInput
) -> ImListTargetsOutput:
    notes: list[str] = []
    if not instance.im_ready:
        notes.append("你的 IM 账号还没就绪，暂时不能收发消息")
    whitelist = set(instance.bundle_whitelist) if instance.bundle_whitelist is not None else None
    direct: list[ImDirectTarget] = []
    if parsed.kind in ("all", "direct"):
        peers = [
            p
            for p in await direct_peers(db, instance.workspace_id)
            if _matches(parsed.query, p.name)
        ]
        cids = {
            p.im_user_id: single_conversation_id(instance.im_user_id, p.im_user_id) for p in peers
        }
        last = await _last_message_at(db, list(cids.values()))
        for peer in peers:
            reach = None if whitelist is None else peer.im_user_id in whitelist
            hints: list[str] = []
            if reach is False:
                hints.append(
                    "他是接入包签发之后才成为管理员的：他回你的私聊你收不到，"
                    "管理员重新签发接入包后才收得到"
                )
            if not peer.has_account:
                hints.append("这位管理员还没打开过 IM，消息要等他打开 IM 后才能看到")
            note = "；".join(hints) or None
            direct.append(
                ImDirectTarget(
                    target=f"user:{peer.im_user_id}",
                    im_user_id=peer.im_user_id,
                    role="owner" if peer.role == "owner" else "admin",
                    conversation_id=cids[peer.im_user_id],
                    last_message_at=last.get(cids[peer.im_user_id]),
                    replies_reach_you=reach,
                    note=note,
                    untrusted=ImNameText(name=peer.name),
                )
            )
        if whitelist is None and peers:
            notes.append("接入包签发得较早，无法判断管理员回你的私聊你收不收得到")
    groups: list[ImGroupTarget] = []
    if parsed.kind in ("all", "group"):
        groups = await _groups(db, redis, instance, parsed.query)
    if not direct and not groups:
        notes.append("现在没有可以主动发消息的对象")
    notes.append("智能体之间不能私聊；要和其他智能体沟通，就在共同所在的群里 @ 它")
    return ImListTargetsOutput(
        me=ImMe(
            im_user_id=instance.im_user_id,
            im_ready=instance.im_ready,
            untrusted=ImNameText(name=instance.name),
        ),
        direct=direct,
        groups=groups,
        notes=notes,
    )


async def _groups(
    db: AsyncSession, redis: Redis | None, instance: AgentInstance, query: str | None
) -> list[ImGroupTarget]:
    rows = (
        await db.execute(
            select(ImGroup, Project.title)
            .join(ImGroupMember, ImGroupMember.group_pk == ImGroup.id)
            .outerjoin(Project, Project.id == ImGroup.project_id)
            .where(
                ImGroupMember.instance_id == instance.id,
                ImGroup.workspace_id == instance.workspace_id,
                ImGroup.dismissed_at.is_(None),
            )
            .order_by(ImGroup.name, ImGroup.id)
        )
    ).all()
    found = [(g, title) for g, title in rows if _matches(query, g.name, title)]
    if not found:
        return []
    members = await _members(db, [g.id for g, _ in found], instance.im_user_id)
    last = await _last_message_at(db, [group_conversation_id(g.group_id) for g, _ in found])
    out: list[ImGroupTarget] = []
    for group, title in found:
        cid = group_conversation_id(group.group_id)
        out.append(
            ImGroupTarget(
                target=f"group:{group.group_id}",
                group_id=group.group_id,
                conversation_id=cid,
                project_id=group.project_id,
                rules=ImGroupRules(
                    relay_enabled=group.relay_enabled,
                    max_per_minute=group.max_per_minute,
                    max_agent_streak=group.max_agent_streak,
                ),
                state=await group_state(redis, group, instance.im_user_id),
                members=members.get(group.id, []),
                last_message_at=last.get(cid),
                untrusted=ImGroupText(name=group.name, project_title=title),
            )
        )
    return out


async def _members(
    db: AsyncSession, group_pks: Sequence[uuid.UUID], self_im_id: str
) -> dict[uuid.UUID, list[ImGroupMemberItem]]:
    rows = (
        await db.execute(
            select(ImGroupMember, User.display_name, AgentInstance.name)
            .outerjoin(User, User.id == ImGroupMember.user_id)
            .outerjoin(AgentInstance, AgentInstance.id == ImGroupMember.instance_id)
            .where(ImGroupMember.group_pk.in_(group_pks))
            .order_by(ImGroupMember.joined_at, ImGroupMember.id)
        )
    ).all()
    out: dict[uuid.UUID, list[ImGroupMemberItem]] = {}
    for member, user_name, instance_name in rows:
        kind: Literal["admin", "agent"] = "agent" if member.instance_id else "admin"
        name = (instance_name if kind == "agent" else user_name) or member.im_user_id
        out.setdefault(member.group_pk, []).append(
            ImGroupMemberItem(
                im_user_id=member.im_user_id,
                kind=kind,
                is_you=member.im_user_id == self_im_id,
                untrusted=ImGroupMemberText(name=name),
            )
        )
    return out


# ---------------------------------------------------------------- 读消息


async def read_messages(
    db: AsyncSession, instance: AgentInstance, parsed: ImReadMessagesInput
) -> ImReadMessagesOutput:
    target = await resolve_target(db, instance, parsed.target)
    query = select(ImMessage).where(ImMessage.conversation_id == target.conversation_id)
    if target.member is not None:
        # 群里只读入群之后的消息
        query = query.where(ImMessage.sent_at >= target.member.joined_at)
    if parsed.before is not None:
        anchor = await db.scalar(
            select(ImMessage).where(
                ImMessage.id == parsed.before,
                ImMessage.conversation_id == target.conversation_id,
            )
        )
        if anchor is None:
            raise ToolCallError("NOT_FOUND", "before 指定的消息不在这个会话里")
        query = query.where(
            (ImMessage.sent_at < anchor.sent_at)
            | ((ImMessage.sent_at == anchor.sent_at) & (ImMessage.id < anchor.id))
        )
    rows = list(
        (
            await db.scalars(
                query.order_by(ImMessage.sent_at.desc(), ImMessage.id.desc()).limit(
                    parsed.limit + 1
                )
            )
        ).all()
    )
    has_more = len(rows) > parsed.limit
    rows = rows[: parsed.limit]
    names = await _sender_names(db, {m.sender_im_id for m in rows})
    items = [_message_item(m, names, instance.im_user_id) for m in reversed(rows)]
    return ImReadMessagesOutput(
        target=parsed.target,
        conversation_id=target.conversation_id,
        items=items,
        has_more=has_more,
        next_before=items[0].id if has_more and items else None,
    )


async def _sender_names(db: AsyncSession, im_ids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    user_ids = {uid for i in im_ids if (uid := user_id_from_im(i)) is not None}
    if user_ids:
        for uid, name in (
            await db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
        ).all():
            names[admin_im_user_id(uid)] = name
    agent_ids = [i for i in im_ids if i.startswith("oc_")]
    if agent_ids:
        for im_id, name in (
            await db.execute(
                select(AgentInstance.im_user_id, AgentInstance.name).where(
                    AgentInstance.im_user_id.in_(agent_ids)
                )
            )
        ).all():
            names[im_id] = name
    return names


def _message_item(message: ImMessage, names: dict[str, str], self_im_id: str) -> ImMessageItem:
    sender = message.sender_im_id
    sender_kind: Literal["admin", "agent", "system"] = (
        "system" if sender in SYSTEM_SENDERS else "agent" if sender.startswith("oc_") else "admin"
    )
    text = message.plain_text
    truncated = text is not None and len(text) > TEXT_LIMIT
    attachments = _attachments(message)
    kind: Literal["text", "image", "file", "video", "other"]
    if message.content_type in (CONTENT_TEXT, CONTENT_AT_TEXT):
        kind = "text"
    elif attachments:
        kind = attachments[0].kind
    else:
        kind = "other"
    return ImMessageItem(
        id=message.id,
        sent_at=message.sent_at,
        sender_im_id=sender,
        sender_kind=sender_kind,
        is_you=sender == self_im_id,
        kind=kind,
        mentions=list(message.at_im_ids or []),
        status=message.status,
        attachments=attachments,
        untrusted=ImMessageText(
            sender_name="工作台" if sender_kind == "system" else names.get(sender, sender),
            text=text[:TEXT_LIMIT] if text is not None else None,
            truncated=truncated,
        ),
    )


def _uuid_ref(value: Any) -> str | None:
    try:
        return f"im_media:{uuid.UUID(str(value))}" if value else None
    except ValueError:
        return None


def _attachments(message: ImMessage) -> list[ImAttachment]:
    content = message.content_json or {}
    if message.content_type == CONTENT_PICTURE:
        source = content.get("sourcePicture") or {}
        return [
            ImAttachment(
                kind="image",
                ref=_uuid_ref(source.get("uuid")),
                mime=source.get("type") or None,
                size=source.get("size") or None,
                untrusted=ImAttachmentText(name=None),
            )
        ]
    if message.content_type == CONTENT_FILE:
        return [
            ImAttachment(
                kind="file",
                ref=_uuid_ref(content.get("uuid")),
                mime=content.get("fileType") or None,
                size=content.get("fileSize") or None,
                untrusted=ImAttachmentText(name=content.get("fileName") or None),
            )
        ]
    if message.content_type == CONTENT_VIDEO:
        return [
            ImAttachment(
                kind="video",
                ref=_uuid_ref(content.get("videoUUID")),
                mime=content.get("videoType") or None,
                size=content.get("videoSize") or None,
                untrusted=ImAttachmentText(name=None),
            )
        ]
    return []
