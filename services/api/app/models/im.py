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
- 会话、消息投影、置顶、通讯录分组与上传，在 OC-3 后续部分与用到它们的代码一起加。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

GROUP_MEMBER_ROLES = ("owner", "admin", "member")
INBOX_STATUSES = ("pending", "processing", "retry", "completed", "dead_letter")


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
