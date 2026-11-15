"""大模型客户端：记下已经通知过的额度暂停（T-0252，docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 的 B7）

llm_bridge_bridges 加一列 pause_notified（平台 → 暂停到什么时候）：
平台因额度用完暂停时通知管理员一次，同一次暂停不重复通知；恢复后清掉，下次暂停再通知。

downgrade 删掉这一列：不影响管理器，只是不再发这类通知。

Revision ID: 0074
Revises: 0073
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_bridge_bridges",
        sa.Column(
            "pause_notified",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_column("llm_bridge_bridges", "pause_notified", schema="workspace")
