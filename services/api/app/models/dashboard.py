"""仪表盘与组件（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 8–9 项）。

约定见 docs/decisions/2026-10-30-sprint-2-dashboard-contract.md 第 3、6 节。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Dashboard(Base):
    __tablename__ = "dashboards"
    __table_args__ = (
        # 每个工作空间恰好一个默认仪表盘
        Index(
            "uq_dashboards_default_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.dashboards.id", ondelete="CASCADE"), index=True
    )
    widget_type: Mapped[str] = mapped_column(Text)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
