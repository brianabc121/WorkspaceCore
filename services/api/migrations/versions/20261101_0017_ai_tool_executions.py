"""AI：MCP 工具调用记录与审批（V2 OC-5，S6-02 / S6-03）

契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 6、9 节。ai_tool_executions 记录每次工具调用；
写工具与高危工具先是 pending_approval（没有执行），由 owner / admin 在工作台批准后按存下的参数执行。
同一实例、同一工具、同样参数同时只有一条待审批（部分唯一索引）。只新增表，downgrade 删除即可回退。

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_tool_executions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("instance_name", sa.Text(), nullable=False),
        sa.Column("acting_user_id", sa.UUID(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "arguments_display",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("arguments_hash", sa.Text(), nullable=False),
        sa.Column("target_version", sa.Text(), nullable=True),
        sa.Column(
            "source",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "risk_level IN ('read', 'write', 'critical')",
            name=op.f("ck_ai_tool_executions_risk_level"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'rejected', 'expired', 'executing', "
            "'succeeded', 'failed', 'unknown', 'denied')",
            name=op.f("ck_ai_tool_executions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["acting_user_id"],
            ["workspace.users.id"],
            name=op.f("fk_ai_tool_executions_acting_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["workspace.users.id"],
            name=op.f("fk_ai_tool_executions_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_ai_tool_executions_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_ai_tool_executions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_tool_executions")),
        schema="workspace",
    )
    op.create_index(
        "ix_ai_tool_executions_acting_user",
        "ai_tool_executions",
        ["acting_user_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_ai_tool_executions_pending_expiry",
        "ai_tool_executions",
        ["expires_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("status = 'pending_approval'"),
    )
    op.create_index(
        "ix_ai_tool_executions_workspace_created",
        "ai_tool_executions",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_ai_tool_executions_pending_args",
        "ai_tool_executions",
        ["instance_id", "tool_name", "arguments_hash"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("status = 'pending_approval'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_ai_tool_executions_pending_args",
        table_name="ai_tool_executions",
        schema="workspace",
        postgresql_where=sa.text("status = 'pending_approval'"),
    )
    op.drop_index(
        "ix_ai_tool_executions_workspace_created",
        table_name="ai_tool_executions",
        schema="workspace",
    )
    op.drop_index(
        "ix_ai_tool_executions_pending_expiry",
        table_name="ai_tool_executions",
        schema="workspace",
        postgresql_where=sa.text("status = 'pending_approval'"),
    )
    op.drop_index(
        "ix_ai_tool_executions_acting_user", table_name="ai_tool_executions", schema="workspace"
    )
    op.drop_table("ai_tool_executions", schema="workspace")
