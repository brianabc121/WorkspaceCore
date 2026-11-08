"""插拔式模块在各工作空间的启用记录（docs/modules/README.md）。

没有记录时按模块清单的 default_enabled；owner 在应用中心启用 / 停用后写一行。
停用不删除模块自己的数据，再启用就恢复。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkspaceModule(Base):
    __tablename__ = "workspace_modules"
    __table_args__ = (
        CheckConstraint("module_key ~ '^[a-z][a-z0-9_]{1,31}$'", name="module_key_format"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    module_key: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
