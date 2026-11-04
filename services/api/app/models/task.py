"""工作台自己的项目与任务（T-0082：移除 Vikunja，人类 2026-11-02 决定）。

单人版：没有负责人、指派、评论、按项目共享；谁能看、谁能改沿用工作空间的角色
（viewer 只读，member 及以上可写）。
- 任务必须属于一个项目，复合外键保证项目与任务在同一个工作空间；删除项目时它的任务一起删除。
- `version` 用于乐观并发：每次修改加 1。
- 迁移 0020 把之前从 Vikunja 同步来的项目、任务转成这两张表，ID 不变
  （收藏的关联与搜索索引照旧有效）。
- 时间轴（T-0104，迁移 0027）：计划开始时间、执行者（成员或 OpenClaw 实例，二选一）、
  第一次开始执行的时间（由执行记录 task_runs 维护，不参与 version）。
- 项目群（T-0110，迁移 0028）：建群状态 none / pending / ready、群主、上一次失败的原因；
  群本身在 im_groups（project_id）。这些列由 app/domain/project_groups.py 维护，不参与 version。
- 接取方式（T-0111，迁移 0029）：指派或自由接取；动态记在 task_events（app/models/timeline.py）。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


IM_GROUP_STATES = ("none", "pending", "ready")
CLAIM_MODES = ("assigned", "open")
CLAIM_SCOPES = ("all", "project")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        # 任务的复合外键引用 (workspace_id, id)
        UniqueConstraint("workspace_id", "id"),
        Index("ix_projects_workspace_archived", "workspace_id", "archived"),
        CheckConstraint(f"im_group_status IN {IM_GROUP_STATES}", name="im_group_status"),
        Index(
            "ix_projects_im_group_pending",
            "im_group_status",
            postgresql_where=sql_text("im_group_status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=sql_text("''"))
    archived: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    im_group_status: Mapped[str] = mapped_column(Text, server_default=sql_text("'none'"))
    im_group_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    im_group_error: Mapped[str | None] = mapped_column(Text)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["workspace.projects.workspace_id", "workspace.projects.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("priority BETWEEN 0 AND 5", name="priority"),
        CheckConstraint(
            "assignee_user_id IS NULL OR assignee_instance_id IS NULL", name="assignee"
        ),
        CheckConstraint(f"claim_mode IN {CLAIM_MODES}", name="claim_mode"),
        CheckConstraint(f"claim_scope IN {CLAIM_SCOPES}", name="claim_scope"),
        Index("ix_tasks_workspace_done_due", "workspace_id", "done", "due_at"),
        Index(
            "ix_tasks_claimable",
            "workspace_id",
            postgresql_where=sql_text(
                "claim_mode = 'open' AND NOT done "
                "AND assignee_user_id IS NULL AND assignee_instance_id IS NULL"
            ),
        ),
        Index("ix_tasks_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=sql_text("''"))
    done: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 0 未设置、1 低、2 中、3 高、4 紧急、5 立即（沿用之前的取值）
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=sql_text("0"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'::text[]"))
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    assignee_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    # 接取方式（T-0111，迁移 0029）：assigned 由管理员指派；open 可自由接取（没有执行者时
    # 空闲的智能体用 claim_task 领取），范围 all 全部智能体 / project 只限项目成员；
    # claim_opened_by 是设为自由接取的人（用他的 IM 账号通知智能体）
    claim_mode: Mapped[str] = mapped_column(Text, server_default=sql_text("'assigned'"))
    claim_scope: Mapped[str] = mapped_column(Text, server_default=sql_text("'all'"))
    claim_opened_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
