"""远程运维动作（T-0114，docs/v2/AGENT_OPS_PLAN.md 第 6.1 节）

agent_ops_actions：对用 SSH 部署过的实例执行的一次固定运维动作（查看状态、查看日志、重启 OpenClaw、
重新拉起全部服务、清理无用镜像、立即上报）。命令写死在工作台里，不拼接输入；输出最多 64 KB、
脱敏后保存，7 天后由 Worker 删除（审计只记谁、什么时候、什么动作、退出码，不记输出）。
同一实例同时只有一个进行中的动作（部分唯一索引）。

新建一张表，downgrade 删除即可回退（动作记录是 0032 之后才有的数据）。

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "agent_ops_actions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("output", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_by", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('status', 'logs', 'restart', 'up', 'prune', 'report')",
            name=op.f("ck_agent_ops_actions_action"),
        ),
        sa.CheckConstraint(
            "service IS NULL OR service IN ('openclaw', 'tunnel', 'image-proxy')",
            name=op.f("ck_agent_ops_actions_service"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name=op.f("ck_agent_ops_actions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["started_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_ops_actions_started_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_agent_ops_actions_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_ops_actions")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_ops_actions_instance_started",
        "agent_ops_actions",
        ["instance_id", "started_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_agent_ops_actions_expires_at",
        "agent_ops_actions",
        ["expires_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_agent_ops_actions_running",
        "agent_ops_actions",
        ["instance_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_ops_actions_running", table_name="agent_ops_actions", schema="workspace"
    )
    op.drop_index(
        "ix_agent_ops_actions_expires_at", table_name="agent_ops_actions", schema="workspace"
    )
    op.drop_index(
        "ix_agent_ops_actions_instance_started", table_name="agent_ops_actions", schema="workspace"
    )
    op.drop_table("agent_ops_actions", schema="workspace")
