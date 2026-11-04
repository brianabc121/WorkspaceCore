"""任务时间轴（T-0104，docs/v2/TIMELINE_PLAN.md 第 2.2 节）

- tasks：计划开始时间 planned_start_at、第一次开始执行的时间 started_at、
  执行者 assignee_user_id / assignee_instance_id（二选一，都空表示未分配）；
- task_runs：执行记录，一段一行；同一个任务同时只有一段在执行；
- agent_usage_minutes：每个实例每分钟的 token 增量；
- agent_usage_cursors：每个实例、每天、每个会话最后一次上报的累计值；
- agent_instances.usage_reported_at：最近一次收到用量上报的时间（为空表示没上报过）。

已有任务的新字段都为空（未分配、没有执行记录）。downgrade 删除三张表和四个字段：
执行记录与 token 用量随之删除（这些是 0027 之后才有的数据）。

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tokens() -> list[sa.Column[int]]:
    return [
        sa.Column(name, sa.BigInteger(), server_default=sa.text("0"), nullable=False)
        for name in ("input", "output", "cache_read", "cache_write")
    ]


def upgrade() -> None:
    op.add_column(
        "agent_instances",
        sa.Column("usage_reported_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "tasks",
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "tasks",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "tasks", sa.Column("assignee_user_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.add_column(
        "tasks", sa.Column("assignee_instance_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_tasks_assignee_user_id_users"),
        "tasks",
        "users",
        ["assignee_user_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_tasks_assignee_instance_id_agent_instances"),
        "tasks",
        "agent_instances",
        ["assignee_instance_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_tasks_assignee"),
        "tasks",
        "assignee_user_id IS NULL OR assignee_instance_id IS NULL",
        schema="workspace",
    )

    op.create_table(
        "task_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("executor_kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("executor_name", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("started_by", sa.UUID(), nullable=True),
        sa.Column("ended_by", sa.UUID(), nullable=True),
        sa.Column("start_key", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "executor_kind IN ('user', 'instance')", name=op.f("ck_task_runs_executor_kind")
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('done', 'paused', 'interrupted', 'cancelled')",
            name=op.f("ck_task_runs_end_reason"),
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (end_reason IS NULL)", name=op.f("ck_task_runs_ended")
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name=op.f("ck_task_runs_order")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_task_runs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace.tasks.id"],
            name=op.f("fk_task_runs_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_task_runs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_task_runs_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["started_by"],
            ["workspace.users.id"],
            name=op.f("fk_task_runs_started_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ended_by"],
            ["workspace.users.id"],
            name=op.f("fk_task_runs_ended_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_runs")),
        sa.UniqueConstraint("start_key", name=op.f("uq_task_runs_start_key")),
        schema="workspace",
    )
    op.create_index(
        "uq_task_runs_open",
        "task_runs",
        ["task_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_task_runs_workspace_started",
        "task_runs",
        ["workspace_id", "started_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_task_runs_instance_started",
        "task_runs",
        ["instance_id", "started_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_task_runs_task_started",
        "task_runs",
        ["task_id", "started_at"],
        unique=False,
        schema="workspace",
    )

    op.create_table(
        "agent_usage_minutes",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_tokens(),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_usage_minutes_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_usage_minutes_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instance_id", "minute", name=op.f("pk_agent_usage_minutes")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_usage_minutes_workspace_minute",
        "agent_usage_minutes",
        ["workspace_id", "minute"],
        unique=False,
        schema="workspace",
    )

    op.create_table(
        "agent_usage_cursors",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("session_hash", sa.Text(), nullable=False),
        *_tokens(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_usage_cursors_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id", "day", "session_hash", name=op.f("pk_agent_usage_cursors")
        ),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("agent_usage_cursors", schema="workspace")
    op.drop_index(
        "ix_agent_usage_minutes_workspace_minute",
        table_name="agent_usage_minutes",
        schema="workspace",
    )
    op.drop_table("agent_usage_minutes", schema="workspace")
    for name in (
        "ix_task_runs_task_started",
        "ix_task_runs_instance_started",
        "ix_task_runs_workspace_started",
        "uq_task_runs_open",
    ):
        op.drop_index(name, table_name="task_runs", schema="workspace")
    op.drop_table("task_runs", schema="workspace")
    op.drop_constraint(op.f("ck_tasks_assignee"), "tasks", type_="check", schema="workspace")
    op.drop_constraint(
        op.f("fk_tasks_assignee_instance_id_agent_instances"),
        "tasks",
        type_="foreignkey",
        schema="workspace",
    )
    op.drop_constraint(
        op.f("fk_tasks_assignee_user_id_users"), "tasks", type_="foreignkey", schema="workspace"
    )
    for column in ("assignee_instance_id", "assignee_user_id", "started_at", "planned_start_at"):
        op.drop_column("tasks", column, schema="workspace")
    op.drop_column("agent_instances", "usage_reported_at", schema="workspace")
