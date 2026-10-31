"""通知中心（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 14 项；Sprint 5 契约第 4 节）。

通知属于“用户 + 工作空间”：每个收件人一行，已读状态各自独立。
source_key 是产生通知的事件的标识（例如 integration_unauthorized:<集成 ID>:<小时>），
同一个收件人、同一个 source_key 只保存一条，重复的事件不会产生重复通知。
title / body 是纯文本，不含 Token、第三方 payload 原文。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # 列表按时间倒序翻页
        Index("ix_notifications_recipient_created", "user_id", "workspace_id", "created_at", "id"),
        # 未读数
        Index(
            "ix_notifications_unread",
            "user_id",
            "workspace_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        # 去重：同一个收件人、同一个事件只有一条
        Index(
            "uq_notifications_recipient_source",
            "user_id",
            "workspace_id",
            "source_key",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace.users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    # info / warning / error
    severity: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # 工作台内部路径（以 / 开头）
    link: Mapped[str | None] = mapped_column(Text)
    source_key: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
