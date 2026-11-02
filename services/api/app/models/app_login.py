"""n8n 管理界面的登录账号（T-0080；T-0082 移除 Vikunja 后只剩 n8n）。

人类要求：在工作台「项目」「自动化」页直接看到这两个账号，复制粘贴去登录。
- 每个工作空间每个应用一条；密码只以 `enc:v1:` 引用保存（用途 `app_login:<id>:password`），
  数据库里没有明文。
- 只有 admin 以上能读写；查看明文要单独请求，每次都写审计
  （docs/decisions/2026-11-02-app-logins.md）。
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

APPS = ("n8n",)


class AppLogin(Base):
    __tablename__ = "app_logins"
    __table_args__ = (
        CheckConstraint("app = 'n8n'", name="app"),
        UniqueConstraint("workspace_id", "app"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    app: Mapped[str] = mapped_column(Text)
    # n8n 的登录邮箱
    username: Mapped[str] = mapped_column(Text)
    password_ref: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
