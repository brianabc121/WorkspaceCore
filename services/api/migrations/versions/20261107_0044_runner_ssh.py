"""运行服务器：页面上通过 SSH 部署（T-0136，docs/v2/RUNNER_PLAN.md 第 9 节）

- runner_ssh_targets：目标服务器（地址、账号、owner 确认过的主机公钥、工作台专用私钥的加密引用），
  每台运行服务器最多一个；
- runner_ssh_jobs：一次部署或卸载的步骤与脱敏后的日志，同一个目标同时只有一个进行中的任务。

全部新增，downgrade 删掉这两张表（服务器上已装的 runner-agent 与专用公钥不受影响）。

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_ssh_targets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("host_id", sa.UUID(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("host_key_algorithm", sa.Text(), nullable=False),
        sa.Column("host_key", sa.Text(), nullable=False),
        sa.Column("host_key_fingerprint", sa.Text(), nullable=False),
        sa.Column("host_key_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("host_key_confirmed_by", sa.UUID(), nullable=True),
        sa.Column("client_key_ref", sa.Text(), nullable=True),
        sa.Column("client_public_key", sa.Text(), nullable=True),
        sa.Column("install_dir", sa.Text(), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name=op.f("ck_runner_ssh_targets_port")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_runner_ssh_targets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["host_key_confirmed_by"],
            ["workspace.users.id"],
            name=op.f("fk_runner_ssh_targets_host_key_confirmed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            name=op.f("fk_runner_ssh_targets_workspace_id_host_id_runner_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_ssh_targets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_ssh_targets")),
        sa.UniqueConstraint("host_id", name=op.f("uq_runner_ssh_targets_host_id")),
        schema="workspace",
    )
    op.create_table(
        "runner_ssh_jobs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("host_id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "kind IN ('install', 'uninstall')", name=op.f("ck_runner_ssh_jobs_kind")
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name=op.f("ck_runner_ssh_jobs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["started_by"],
            ["workspace.users.id"],
            name=op.f("fk_runner_ssh_jobs_started_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["workspace.runner_ssh_targets.id"],
            name=op.f("fk_runner_ssh_jobs_target_id_runner_ssh_targets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            name=op.f("fk_runner_ssh_jobs_workspace_id_host_id_runner_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_ssh_jobs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_ssh_jobs")),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_ssh_jobs_target_started",
        "runner_ssh_jobs",
        ["target_id", "started_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_runner_ssh_jobs_running",
        "runner_ssh_jobs",
        ["target_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_runner_ssh_jobs_running",
        table_name="runner_ssh_jobs",
        schema="workspace",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(
        "ix_runner_ssh_jobs_target_started", table_name="runner_ssh_jobs", schema="workspace"
    )
    op.drop_table("runner_ssh_jobs", schema="workspace")
    op.drop_table("runner_ssh_targets", schema="workspace")
