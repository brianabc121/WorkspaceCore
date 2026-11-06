"""终止智能体正在执行的任务（T-0126，docs/v2/AGENT_MESSAGING_PLAN.md 第 7.3 节）

- agent_run_stops：终止令。管理员或打开了“终止其他智能体任务”权限的智能体下令；
  下令后 5 分钟内这个实例的 MCP 工具调用返回 RUN_STOPPED（软停止），实例上的接入包每分钟运行
  `stop-runs.mjs` 领取终止令，在容器里对正在运行的会话调网关的 `sessions.abort`，
  报回中止了几个会话（硬停止）。
  10 分钟没被领取记为过期（实例没更新接入包或不在线），领取后 5 分钟没有结果记为失败。保留 30 天。
- agent_instances.stop_others_enabled（默认关闭，第 11 节 D20）与打开它的人和时间。

新建一张表、加三列；downgrade 删除即可回退（都是 0037 之后才有的数据）。

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "agent_run_stops",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_by_instance", sa.UUID(), nullable=True),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("soft_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aborted", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'expired')",
            name=op.f("ck_agent_run_stops_status"),
        ),
        sa.CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 500", name=op.f("ck_agent_run_stops_reason")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_run_stops_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_run_stops_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_run_stops_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_instance"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_run_stops_requested_by_instance_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace.tasks.id"],
            name=op.f("fk_agent_run_stops_task_id_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_stops")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_run_stops_instance_created",
        "agent_run_stops",
        ["instance_id", "created_at"],
        schema="workspace",
    )
    op.add_column(
        "agent_instances",
        sa.Column(
            "stop_others_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        schema="workspace",
    )
    op.add_column(
        "agent_instances", sa.Column("stop_others_by", sa.UUID(), nullable=True), schema="workspace"
    )
    op.add_column(
        "agent_instances",
        sa.Column("stop_others_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_agent_instances_stop_others_by_users"),
        "agent_instances",
        "users",
        ["stop_others_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_agent_instances_stop_others_by_users"),
        "agent_instances",
        schema="workspace",
        type_="foreignkey",
    )
    op.drop_column("agent_instances", "stop_others_at", schema="workspace")
    op.drop_column("agent_instances", "stop_others_by", schema="workspace")
    op.drop_column("agent_instances", "stop_others_enabled", schema="workspace")
    op.drop_index("ix_agent_run_stops_instance_created", "agent_run_stops", schema="workspace")
    op.drop_table("agent_run_stops", schema="workspace")
