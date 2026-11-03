"""OpenClaw 实例的 SSH 安装任务（V2 T-0089 B2）

agent_ssh_jobs：一次安装的步骤、脱敏后的输出与结果；任务在 API 进程内运行，登录密码从不保存。
同一台目标服务器同时只有一个进行中的任务（部分唯一索引）。agent_ssh_targets 增加安装目录与完成时间。
新建一张表、两列可空列，downgrade 删除即可回退。

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_ssh_targets", sa.Column("install_dir", sa.Text(), nullable=True), schema="workspace"
    )
    op.add_column(
        "agent_ssh_targets",
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.create_table(
        "agent_ssh_jobs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("log", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_by", sa.UUID(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('install')", name=op.f("ck_agent_ssh_jobs_kind")),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name=op.f("ck_agent_ssh_jobs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["started_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_ssh_jobs_started_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["workspace.agent_ssh_targets.id"],
            name=op.f("fk_agent_ssh_jobs_target_id_agent_ssh_targets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_agent_ssh_jobs_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_ssh_jobs")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_ssh_jobs_target_started",
        "agent_ssh_jobs",
        ["target_id", "started_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_agent_ssh_jobs_running",
        "agent_ssh_jobs",
        ["target_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_ssh_jobs_running", table_name="agent_ssh_jobs", schema="workspace")
    op.drop_index(
        "ix_agent_ssh_jobs_target_started", table_name="agent_ssh_jobs", schema="workspace"
    )
    op.drop_table("agent_ssh_jobs", schema="workspace")
    op.drop_column("agent_ssh_targets", "installed_at", schema="workspace")
    op.drop_column("agent_ssh_targets", "install_dir", schema="workspace")
