"""Webhook Inbox（S3-05）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 15 项、第 5.2 节与第 10 节；sync_cursors 已在 0005 建立。
部分索引 ix_webhook_pending 比规范的示意多包含 processing：处理中的事件
next_retry_at 是租约到期时间，进程中途退出后按它重新领取。只新增表，downgrade 删除即可回退。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_inbox",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["workspace.integrations.id"],
            name=op.f("fk_webhook_inbox_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_inbox")),
        sa.UniqueConstraint(
            "integration_id", "event_key", name=op.f("uq_webhook_inbox_integration_id_event_key")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_webhook_pending",
        "webhook_inbox",
        ["next_retry_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("status IN ('pending', 'retry', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_pending",
        table_name="webhook_inbox",
        schema="workspace",
        postgresql_where=sa.text("status IN ('pending', 'retry', 'processing')"),
    )
    op.drop_table("webhook_inbox", schema="workspace")
