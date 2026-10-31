"""通知中心（S5-03）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 14 项；约定见
docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 4 节。
每个收件人一行；(user_id, workspace_id, source_key) 唯一，重复的事件不会产生重复通知。
只新增表，downgrade 删除即可回退（会丢失通知）。

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("source_key", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_notifications_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        schema="workspace",
    )
    op.create_index(
        "ix_notifications_recipient_created",
        "notifications",
        ["user_id", "workspace_id", "created_at", "id"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id", "workspace_id"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "uq_notifications_recipient_source",
        "notifications",
        ["user_id", "workspace_id", "source_key"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_notifications_recipient_source", table_name="notifications", schema="workspace"
    )
    op.drop_index("ix_notifications_unread", table_name="notifications", schema="workspace")
    op.drop_index(
        "ix_notifications_recipient_created", table_name="notifications", schema="workspace"
    )
    op.drop_table("notifications", schema="workspace")
