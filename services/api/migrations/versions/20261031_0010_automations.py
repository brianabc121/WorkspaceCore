"""n8n 连接、白名单工作流与执行记录（S5-04）

docs/DEVELOPMENT_SPEC.md 第 7.5 节；约定见
docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 6 节。
n8n 的 API Key 与共享密钥只以 enc:v1 引用保存；白名单软删除，执行记录保留；
执行记录不保存触发参数与第三方 payload。
只新增表，downgrade 删除即可回退（会丢失白名单与执行记录）。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_connections",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("api_key_ref", sa.Text(), nullable=False),
        sa.Column("trigger_secret_ref", sa.Text(), nullable=False),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_automation_connections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_connections")),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_automation_connections_workspace_id")),
        schema="workspace",
    )
    op.create_table(
        "automations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("webhook_path", sa.Text(), nullable=False),
        sa.Column(
            "params_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("workflow_active", sa.Boolean(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_automations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automations")),
        schema="workspace",
    )
    op.create_index(
        "uq_automations_workspace_path",
        "automations",
        ["workspace_id", "webhook_path"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_automations_workspace_workflow",
        "automations",
        ["workspace_id", "workflow_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("automation_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("external_execution_id", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.UUID(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["automation_id"],
            ["workspace.automations.id"],
            name=op.f("fk_automation_runs_automation_id_automations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by"],
            ["workspace.users.id"],
            name=op.f("fk_automation_runs_triggered_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_automation_runs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_runs")),
        schema="workspace",
    )
    op.create_index(
        "ix_automation_runs_automation_created",
        "automation_runs",
        ["automation_id", "created_at", "id"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_automation_runs_open",
        "automation_runs",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("status IN ('queued', 'running', 'unknown')"),
    )
    op.create_index(
        "uq_automation_runs_execution",
        "automation_runs",
        ["automation_id", "external_execution_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("external_execution_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_automation_runs_execution",
        table_name="automation_runs",
        schema="workspace",
        postgresql_where=sa.text("external_execution_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_automation_runs_open",
        table_name="automation_runs",
        schema="workspace",
        postgresql_where=sa.text("status IN ('queued', 'running', 'unknown')"),
    )
    op.drop_index(
        "ix_automation_runs_automation_created", table_name="automation_runs", schema="workspace"
    )
    op.drop_table("automation_runs", schema="workspace")
    op.drop_index(
        "uq_automations_workspace_workflow",
        table_name="automations",
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_automations_workspace_path",
        table_name="automations",
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("automations", schema="workspace")
    op.drop_table("automation_connections", schema="workspace")
