"""项目里的 Issues（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 第 8 节）。

- Issue 属于项目，按项目编号（#1、#2……）；任务通过关联看到自己的 Issue（D12）。
- 作者、指派、评论者可以是人或智能体：user_id / instance_id 二选一，
  另存当时的名字（实例或用户删了也看得懂）。
- 评论不物理删除：owner / admin 隐藏；Issue 由 owner / admin 删除时进回收站（deleted_at）。
- 时间线（issue_events）记打开、关闭、重新打开、指派、标签、关联任务、改标题、锁定、被提到。
- 已读（issue_reads）：每个参与者（人或智能体）对每个 Issue 记一个“读到哪里”，算未读与收件箱。
- 提及（issue_mentions）：正文和评论里 @ 到的人或智能体，智能体的收件箱与唤醒用它。
- agent_streak：连续多少条评论来自智能体、中间没有人评论；到 10 条智能体暂停评论（8.6）。
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
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ISSUE_KINDS = (
    "question",
    "proposal",
    "bug",
    "contract",
    "handoff",
    "review",
    "blocker",
    "other",
)
ISSUE_STATES = ("open", "closed")
CLOSE_REASONS = ("resolved", "wontfix", "duplicate")
ISSUE_EVENT_KINDS = (
    "opened",
    "closed",
    "reopened",
    "assigned",
    "unassigned",
    "labeled",
    "unlabeled",
    "linked",
    "unlinked",
    "retitled",
    "locked",
    "unlocked",
    "mentioned",
)
ACTOR_KINDS = ("user", "instance", "system")
_ONE_ACTOR = "(user_id IS NULL) <> (instance_id IS NULL)"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _workspace() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspace.workspaces.id", ondelete="CASCADE"))


def _user() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("workspace.users.id", ondelete="SET NULL"))


def _instance() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("workspace.agent_instances.id", ondelete="SET NULL"))


def _issue() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspace.issues.id", ondelete="CASCADE"))


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        CheckConstraint(f"kind IN {ISSUE_KINDS}", name="kind"),
        CheckConstraint(f"state IN {ISSUE_STATES}", name="state"),
        CheckConstraint(
            f"close_reason IS NULL OR close_reason IN {CLOSE_REASONS}", name="close_reason"
        ),
        CheckConstraint("(state = 'closed') = (close_reason IS NOT NULL)", name="closed"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title"),
        CheckConstraint("char_length(body) <= 20000", name="body"),
        CheckConstraint("cardinality(labels) <= 10", name="labels"),
        CheckConstraint(
            "(author_user_id IS NULL) OR (author_instance_id IS NULL)", name="one_author"
        ),
        Index("ix_issues_project_state", "project_id", "state", "number"),
        Index("ix_issues_workspace_activity", "workspace_id", "last_activity_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.projects.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    kind: Mapped[str] = mapped_column(Text, server_default=text("'other'"))
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    state: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    close_reason: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.issues.id", ondelete="SET NULL")
    )
    locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    author_user_id: Mapped[uuid.UUID | None] = _user()
    author_instance_id: Mapped[uuid.UUID | None] = _instance()
    author_name: Mapped[str] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 连续多少条评论来自智能体（中间没有人评论）；到上限智能体暂停评论
    agent_streak: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    comment_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IssueAssignee(Base):
    __tablename__ = "issue_assignees"
    __table_args__ = (
        CheckConstraint(_ONE_ACTOR, name="one"),
        Index(
            "uq_issue_assignees_user",
            "issue_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_issue_assignees_instance",
            "issue_id",
            "instance_id",
            unique=True,
            postgresql_where=text("instance_id IS NOT NULL"),
        ),
        Index("ix_issue_assignees_instance", "instance_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = _issue()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueTask(Base):
    __tablename__ = "issue_tasks"
    __table_args__ = (
        PrimaryKeyConstraint("issue_id", "task_id"),
        Index("ix_issue_tasks_task", "task_id"),
    )

    issue_id: Mapped[uuid.UUID] = _issue()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace.tasks.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueComment(Base):
    __tablename__ = "issue_comments"
    __table_args__ = (
        CheckConstraint("char_length(body) BETWEEN 1 AND 10000", name="body"),
        CheckConstraint(
            "(author_user_id IS NULL) OR (author_instance_id IS NULL)", name="one_author"
        ),
        Index("ix_issue_comments_issue_created", "issue_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    issue_id: Mapped[uuid.UUID] = _issue()
    reply_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.issue_comments.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text)
    author_user_id: Mapped[uuid.UUID | None] = _user()
    author_instance_id: Mapped[uuid.UUID | None] = _instance()
    author_name: Mapped[str] = mapped_column(Text)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_by: Mapped[uuid.UUID | None] = _user()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueEvent(Base):
    __tablename__ = "issue_events"
    __table_args__ = (
        CheckConstraint(f"kind IN {ISSUE_EVENT_KINDS}", name="kind"),
        CheckConstraint(f"actor_kind IN {ACTOR_KINDS}", name="actor_kind"),
        Index("ix_issue_events_issue_created", "issue_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    issue_id: Mapped[uuid.UUID] = _issue()
    kind: Mapped[str] = mapped_column(Text)
    actor_kind: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = _user()
    instance_id: Mapped[uuid.UUID | None] = _instance()
    actor_name: Mapped[str] = mapped_column(Text)
    # 事件的细节：标签、被指派的人、任务、原标题、关闭原因……（只有 ID 与短文字）
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueMention(Base):
    __tablename__ = "issue_mentions"
    __table_args__ = (
        CheckConstraint(_ONE_ACTOR, name="one"),
        Index("ix_issue_mentions_instance", "instance_id", "created_at"),
        Index("ix_issue_mentions_user", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = _issue()
    comment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.issue_comments.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueRead(Base):
    __tablename__ = "issue_reads"
    __table_args__ = (
        CheckConstraint(_ONE_ACTOR, name="one"),
        Index(
            "uq_issue_reads_user",
            "issue_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_issue_reads_instance",
            "issue_id",
            "instance_id",
            unique=True,
            postgresql_where=text("instance_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = _issue()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
