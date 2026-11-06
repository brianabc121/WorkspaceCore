"""智能体的唤醒（T-0121，docs/v2/AGENT_MESSAGING_PLAN.md 第 7.2 节）

- agent_wakeups：到点叫醒智能体跑一轮。智能体用 `schedule_wakeup` 给自己安排
  （每个实例最多 20 个待执行，5 分钟后到 30 天内），管理员在实例页建（可以每天、工作日、每周重复）。
  实例上的接入包每分钟运行 `wakeup.mjs`，经 `/agent-api/v1/wakeups/claim` 领取到期的唤醒，
  在容器里用 `openclaw agent --session-key` 跑一轮，再报回结果。
  领取后超过 15 分钟没有结果记为失败（结果未知，不重跑）。结束的记录保留 30 天。

新建一张表，downgrade 删除即可回退（都是 0036 之后才有的数据）。

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "agent_wakeups",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("repeat", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column("time_zone", sa.Text(), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("runs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.CheckConstraint("source IN ('agent', 'admin')", name=op.f("ck_agent_wakeups_source")),
        sa.CheckConstraint(
            "repeat IN ('none', 'daily', 'weekdays', 'weekly')",
            name=op.f("ck_agent_wakeups_repeat"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'cancelled')",
            name=op.f("ck_agent_wakeups_status"),
        ),
        sa.CheckConstraint(
            "char_length(prompt) BETWEEN 1 AND 2000", name=op.f("ck_agent_wakeups_prompt")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_wakeups_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_wakeups_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_wakeups_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_wakeups")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_wakeups_due",
        "agent_wakeups",
        ["instance_id", "run_at"],
        schema="workspace",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_agent_wakeups_instance_created",
        "agent_wakeups",
        ["instance_id", "created_at"],
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index("ix_agent_wakeups_instance_created", "agent_wakeups", schema="workspace")
    op.drop_index("ix_agent_wakeups_due", "agent_wakeups", schema="workspace")
    op.drop_table("agent_wakeups", schema="workspace")
