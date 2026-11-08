"""侧栏布局（T-0161；docs/modules/README.md 第 1 节、docs/v2/MODULES_PLAN.md 第 2 节）。

每个工作空间一行：owner 自定义的分组、把已有页面挪到别的分组、零代码页面（外部链接、站内页面、
知识库笔记、仪表盘、嵌入页）。整份布局放 JSONB，由 app/schemas/navigation.py 校验；
version 防并发覆盖。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkspaceNavigation(Base):
    __tablename__ = "workspace_navigation"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    layout: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
