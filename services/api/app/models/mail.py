"""Gmail 邮件（T-0128，docs/v2/MAIL_PLAN.md）。

- mail_oauth_clients：每个工作空间一条，管理员填的 Google OAuth 客户端（client_secret 加密）。
- mail_accounts：连接的 Gmail 邮箱：实际授权的范围、加密的 refresh token、状态、是否允许智能体访问、
  智能体发信是否加署名行。access token 不进数据库（加密后放 Redis，按 expires_in 过期）。
- mail_sends：发件记录（人或智能体）：收件人、主题、Gmail 返回的消息 / 会话 ID、状态；**不存正文**。
"""

import uuid
from datetime import datetime

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
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ACCOUNT_STATUSES = ("active", "needs_reconnect", "disconnected")
SEND_STATUSES = ("sending", "sent", "failed", "unknown")
SEND_ACTORS = ("user", "instance")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _workspace() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspace.workspaces.id", ondelete="CASCADE"))


def _user() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("workspace.users.id", ondelete="SET NULL"))


class MailOAuthClient(Base):
    __tablename__ = "mail_oauth_clients"
    __table_args__ = (UniqueConstraint("workspace_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_ref: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = _user()
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MailAccount(Base):
    __tablename__ = "mail_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "email"),
        CheckConstraint(f"status IN {ACCOUNT_STATUSES}", name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    refresh_token_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    last_error: Mapped[str | None] = mapped_column(Text)
    agents_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    agent_signature: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    connected_by: Mapped[uuid.UUID | None] = _user()
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MailSend(Base):
    __tablename__ = "mail_sends"
    __table_args__ = (
        CheckConstraint(f"status IN {SEND_STATUSES}", name="status"),
        CheckConstraint(f"actor_kind IN {SEND_ACTORS}", name="actor_kind"),
        CheckConstraint("(user_id IS NULL) OR (instance_id IS NULL)", name="one_actor"),
        CheckConstraint("char_length(subject) <= 200", name="subject"),
        Index("ix_mail_sends_workspace_created", "workspace_id", "created_at"),
        Index("ix_mail_sends_instance_created", "instance_id", "created_at"),
        Index(
            "uq_mail_sends_client_key",
            "instance_id",
            "client_key",
            unique=True,
            postgresql_where=text("client_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.mail_accounts.id", ondelete="SET NULL")
    )
    actor_kind: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = _user()
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    actor_name: Mapped[str] = mapped_column(Text)
    sender: Mapped[str] = mapped_column(Text)
    to_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text))
    cc_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    bcc_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    subject: Mapped[str] = mapped_column(Text)
    body_chars: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    reply_to_message_id: Mapped[str | None] = mapped_column(Text)
    gmail_message_id: Mapped[str | None] = mapped_column(Text)
    gmail_thread_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'sending'"))
    error_code: Mapped[str | None] = mapped_column(Text)
    client_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
