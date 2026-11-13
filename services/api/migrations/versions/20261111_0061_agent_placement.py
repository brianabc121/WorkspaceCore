"""Independent SSH resource snapshots and persistent deployment placement receipts.

Revision ID: 0061
Revises: 0060
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, column in (
        ("agent_servers", "resource_snapshot"),
        ("agent_deployments", "selection"),
    ):
        op.add_column(
            table,
            sa.Column(
                column, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
            schema="workspace",
        )


def downgrade() -> None:
    op.drop_column("agent_deployments", "selection", schema="workspace")
    op.drop_column("agent_servers", "resource_snapshot", schema="workspace")
