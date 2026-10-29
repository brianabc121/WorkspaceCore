"""审计日志（S1-05）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 23 项。只追加：触发器拒绝 UPDATE；
DELETE 仍然允许（随工作空间级联删除，以及将来的保留期清理）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column(
            "metadata_redacted_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_audit_logs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_audit_logs_workspace_id_created_at_id"),
        "audit_logs",
        ["workspace_id", "created_at", "id"],
        unique=False,
        schema="workspace",
    )
    op.execute(
        """
        CREATE FUNCTION workspace.audit_logs_reject_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'workspace.audit_logs 只允许追加，不能修改';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE ON workspace.audit_logs
        FOR EACH ROW EXECUTE FUNCTION workspace.audit_logs_reject_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_logs_append_only ON workspace.audit_logs")
    op.execute("DROP FUNCTION workspace.audit_logs_reject_update()")
    op.drop_index(
        op.f("ix_audit_logs_workspace_id_created_at_id"),
        table_name="audit_logs",
        schema="workspace",
    )
    op.drop_table("audit_logs", schema="workspace")
