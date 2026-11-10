"""插拔式模块在各工作空间的启用记录与同步记录（docs/modules/README.md）。

没有记录时按模块清单的 default_enabled；owner 在应用中心启用 / 停用后写一行。
停用不删除模块自己的数据，再启用就恢复。

应用中心（T-0210，docs/v2/app-center/）：state_version 是目标状态的版本（实际启停一次加 1），
与清单里的发布版本 version 无关；module_sync_attempts 记下启停或重试后执行模块钩子的每一次尝试。
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkspaceModule(Base):
    __tablename__ = "workspace_modules"
    __table_args__ = (
        CheckConstraint("module_key ~ '^[a-z][a-z0-9_]{1,31}$'", name="module_key_format"),
        CheckConstraint("state_version >= 0", name="state_version"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    module_key: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # 目标状态版本：实际启停一次加 1（新页面带上它做乐观并发；旧客户端可以不带）
    state_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


SYNC_STATES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "unknown",
    "superseded",
    "not_required",
)
# 只记固定类别，不存异常正文
SYNC_REASONS = (
    "hook_failed",
    "config_render_failed",
    "timeout",
    "interrupted",
    "stale_revision",
    "busy",
    "result_unverified",
    "not_deployed",
    "application_not_active",
)
SYNC_TRIGGERS = ("toggle", "retry")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


class ModuleSyncAttempt(Base):
    """启停或重试后执行一次模块钩子的记录（钩子失败不回滚开关，结果在这里看）。"""

    __tablename__ = "module_sync_attempts"
    __table_args__ = (
        CheckConstraint("module_key ~ '^[a-z][a-z0-9_]{1,31}$'", name="module_key_format"),
        CheckConstraint("state_version >= 0", name="state_version"),
        CheckConstraint(f"state IN {_in(SYNC_STATES)}", name="state"),
        CheckConstraint(f"reason IS NULL OR reason IN {_in(SYNC_REASONS)}", name="reason"),
        CheckConstraint(f"trigger IN {_in(SYNC_TRIGGERS)}", name="trigger"),
        Index(None, "workspace_id", "module_key", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    module_key: Mapped[str] = mapped_column(Text)
    state_version: Mapped[int] = mapped_column(Integer)
    target_enabled: Mapped[bool] = mapped_column(Boolean)
    trigger: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
