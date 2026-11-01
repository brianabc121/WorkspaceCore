"""IM：OpenIM 账号、群与回调收件箱（V2 OC-3）。

依据 docs/v2/TRANSFORMATION_PLAN.md 第 4.4、4.5、5 节与契约
docs/decisions/2026-10-31-v2-oc1-contract.md 第 5、10 节。

- 管理员的 OpenIM 账号 `ws_<用户 UUID 十六进制>` 按用户建一个（多个工作空间共用），
  第一次需要时创建；
  实例的账号在 agent_instances.im_user_id（`oc_<实例 UUID 十六进制>`）。
- 群属于一个工作空间；防刷屏的阈值（方案第 4.5 节）直接放在群上（方案第 5 节的 im_group_policies
  合并进来：一对一，没有单独成表的必要）。
- 回调收件箱：按 event_key 去重；处理状态与 webhook_inbox 相同（pending → processing → completed，
  失败 retry，超限 dead_letter）。回调没有签名，payload 只当作“有变化”的提示，内容以拉取为准。
- 会话属于“用户 + 工作空间”（每个管理员一行，各自的已读、置顶、免打扰）；消息投影按 OpenIM 的
  会话 ID 存一份（群里的多个管理员共用），序号与内容以 pull_msg_by_seq 拉回的为准。
- 消息置顶挂在当前用户的会话上；通讯录分组属于工作空间（名称在工作空间内唯一）。
- 上传在 OC-3 后续部分与用到它的代码一起加。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

GROUP_MEMBER_ROLES = ("owner", "admin", "member")
INBOX_STATUSES = ("pending", "processing", "retry", "completed", "dead_letter")
CONVERSATION_KINDS = ("single", "group", "notice")
MESSAGE_STATUSES = ("sending", "sent", "failed", "unknown")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ImAccount(Base):
    """管理员（owner / admin）的 OpenIM 账号；一个用户一个。"""

    __tablename__ = "im_accounts"
    __table_args__ = (UniqueConstraint("im_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE"), primary_key=True
    )
    im_user_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class ImGroup(Base):
    __tablename__ = "im_groups"
    __table_args__ = (
        UniqueConstraint("group_id"),
        CheckConstraint("max_agent_streak BETWEEN 1 AND 50", name="max_agent_streak"),
        CheckConstraint("max_per_minute BETWEEN 1 AND 60", name="max_per_minute"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # OpenIM 的群 ID；会话 ID 是 sg_<group_id>
    group_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    announcement: Mapped[str] = mapped_column(Text, server_default=text("''"))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    relay_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    max_agent_streak: Mapped[int] = mapped_column(Integer, server_default=text("8"))
    max_per_minute: Mapped[int] = mapped_column(Integer, server_default=text("6"))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ImGroupMember(Base):
    """群成员：实例（instance_id）或用户（user_id），二者恰好一个。"""

    __tablename__ = "im_group_members"
    __table_args__ = (
        UniqueConstraint("group_pk", "im_user_id"),
        CheckConstraint(f"role IN {GROUP_MEMBER_ROLES}", name="role"),
        CheckConstraint("(instance_id IS NULL) <> (user_id IS NULL)", name="one_member"),
        Index("ix_im_group_members_im_user_id", "im_user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    group_pk: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.im_groups.id", ondelete="CASCADE")
    )
    im_user_id: Mapped[str] = mapped_column(Text)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text, server_default=text("'member'"))
    joined_at: Mapped[datetime] = _created()


class ImWebhookInboxEvent(Base):
    """OpenIM 的 after 回调（消息、上下线、删好友）。before 回调同步判断，不进收件箱。"""

    __tablename__ = "im_webhook_inbox"
    __table_args__ = (
        UniqueConstraint("event_key"),
        CheckConstraint(f"status IN {INBOX_STATUSES}", name="status"),
        Index(
            "ix_im_webhook_pending",
            "next_retry_at",
            postgresql_where=text("status IN ('pending', 'retry', 'processing')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # msg:<serverMsgID>、online:<userID>:<platformID>:<seq>、
    # delete_friend:<owner>:<friend>:<收到的毫秒>
    event_key: Mapped[str] = mapped_column(Text)
    command: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_text: Mapped[str | None] = mapped_column(Text)


class ImConversation(Base):
    """一个管理员在一个工作空间里的会话（单聊或群聊）。"""

    __tablename__ = "im_conversations"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "conversation_id"),
        CheckConstraint(f"kind IN {CONVERSATION_KINDS}", name="kind"),
        CheckConstraint("read_seq >= 0 AND max_seq >= 0", name="seqs"),
        Index("ix_im_conversations_conversation_id", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE")
    )
    # OpenIM 的会话 ID：si_<两个用户>、sg_<群>、sn_<通知>
    conversation_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    peer_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    group_pk: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.im_groups.id", ondelete="CASCADE")
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    muted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    read_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    max_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ImMessage(Base):
    """消息投影：每条 OpenIM 消息一行；工作台代发、还没被 OpenIM 受理的消息 seq 与
    server_msg_id 为空。"""

    __tablename__ = "im_messages"
    __table_args__ = (
        UniqueConstraint("server_msg_id"),
        UniqueConstraint("conversation_id", "seq"),
        UniqueConstraint("conversation_id", "client_msg_id"),
        CheckConstraint(f"status IN {MESSAGE_STATUSES}", name="status"),
        UniqueConstraint("idempotency_key"),
        Index("ix_im_messages_conversation_sent", "conversation_id", "sent_at"),
        Index("ix_im_messages_sent_at", "sent_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[str] = mapped_column(Text)
    server_msg_id: Mapped[str | None] = mapped_column(Text)
    client_msg_id: Mapped[str] = mapped_column(Text)
    seq: Mapped[int | None] = mapped_column(BigInteger)
    sender_im_id: Mapped[str] = mapped_column(Text)
    sender_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    content_type: Mapped[int] = mapped_column(Integer)
    # 解码后的 content（JSON 对象）；过大或无法解析时为 {}
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    # 纯文本：文字消息的正文，系统消息的说明；其他类型为空
    plain_text: Mapped[str | None] = mapped_column(Text)
    at_im_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'sent'"))
    # 工作台代发时的 Idempotency-Key：写进 OpenIM 消息的 ex，结果未知时靠它在投影里找回
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class ImMessagePin(Base):
    """消息置顶：属于“当前用户 + 工作空间”（挂在当前用户的会话上），只在工作台记录。"""

    __tablename__ = "im_message_pins"
    __table_args__ = (UniqueConstraint("conversation_pk", "server_msg_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_pk: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.im_conversations.id", ondelete="CASCADE")
    )
    server_msg_id: Mapped[str] = mapped_column(Text)
    pinned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    pinned_at: Mapped[datetime] = _created()


class ImContactGroup(Base):
    """通讯录分组：属于工作空间；一个实例可以在多个分组里。删除分组不影响实例与好友关系。"""

    __tablename__ = "im_contact_groups"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name"),
        CheckConstraint("sort_order BETWEEN 0 AND 10000", name="sort_order"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ImContactGroupMember(Base):
    __tablename__ = "im_contact_group_members"
    __table_args__ = (Index("ix_im_contact_group_members_instance_id", "instance_id"),)

    contact_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.im_contact_groups.id", ondelete="CASCADE"), primary_key=True
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
