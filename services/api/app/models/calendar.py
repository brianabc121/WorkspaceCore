"""本地日历（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 13 项）：工作台是主源。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 5 节。
start_at / end_at 总是 UTC 时间（全天事件为 timezone 当地 0 点），区间查询只看这两列；
全天事件另存 start_date / end_date。
V1 不支持重复事件，rrule 列保留为空。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="time_order"),
        CheckConstraint(
            "(all_day AND start_date IS NOT NULL AND end_date IS NOT NULL)"
            " OR (NOT all_day AND start_date IS NULL AND end_date IS NULL)",
            name="all_day_dates",
        ),
        Index("ix_calendar_events_workspace_start", "workspace_id", "start_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    location: Mapped[str] = mapped_column(Text, server_default=text("''"))
    all_day: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(Text)
    rrule: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
