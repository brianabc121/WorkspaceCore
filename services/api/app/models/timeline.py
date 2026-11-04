"""任务时间轴（T-0104，docs/v2/TIMELINE_PLAN.md 第 2.2 节，迁移 0027）。

- task_runs：一段执行一行。一个任务可以有多段；同一个任务同时只有一段在执行（部分唯一索引）。
  - 执行者是工作空间成员（user）或 OpenClaw 实例（instance）；`executor_name` 是开始时的名字，
    成员离开、实例被删除后仍能看出是谁。
  - `start_key` 是开始这段时的 Idempotency-Key：重试同一个请求不会多开一段，
    交给实例执行时也不会多发消息。
  - 结束原因：done 完成 / paused 暂停 / interrupted 中断 / cancelled 取消；没结束时为空。
- agent_usage_minutes：每个实例每分钟的 token 增量（输入、输出、缓存读、缓存写），保留 90 天。
- agent_usage_cursors：每个实例、每天（UTC）、每个会话最后一次上报的累计值，用来算增量；
  会话只存接入包算好的 SHA-256（不存 OpenClaw 的会话 key），保留 3 天。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RUN_END_REASONS = ("done", "paused", "interrupted", "cancelled")
EXECUTOR_KINDS = ("user", "instance")


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        CheckConstraint(f"executor_kind IN {EXECUTOR_KINDS}", name="executor_kind"),
        CheckConstraint(
            f"end_reason IS NULL OR end_reason IN {RUN_END_REASONS}", name="end_reason"
        ),
        CheckConstraint("(ended_at IS NULL) = (end_reason IS NULL)", name="ended"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="order"),
        UniqueConstraint("start_key"),
        Index(
            "uq_task_runs_open",
            "task_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_task_runs_workspace_started", "workspace_id", "started_at"),
        Index("ix_task_runs_instance_started", "instance_id", "started_at"),
        Index("ix_task_runs_task_started", "task_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace.tasks.id", ondelete="CASCADE"))
    executor_kind: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    executor_name: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    # 系统结束（实例停用 / 离线、改了执行者）时为空
    ended_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    start_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentUsageMinute(Base):
    __tablename__ = "agent_usage_minutes"
    __table_args__ = (Index("ix_agent_usage_minutes_workspace_minute", "workspace_id", "minute"),)

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    input: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    output: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cache_read: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cache_write: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class AgentUsageCursor(Base):
    __tablename__ = "agent_usage_cursors"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    session_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    input: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    output: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cache_read: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cache_write: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
