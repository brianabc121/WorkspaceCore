"""大模型客户端管理：按实例限定能用哪几个 MCP 管理器（T-0230，docs/v2/LLM_BRIDGE_PLAN.md 第 19 节）

llm_bridge_grants 新增一列 bridge_ids（JSONB，可空）：实例能用的会话桥 id 列表。
NULL 表示都能用（包括以后登记的），已有的行保持 NULL，行为不变；空列表表示一个都不能用。
只新增一列，不改已有数据。

downgrade 删掉这一列（限定信息丢失，回到“都能用”）。

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_bridge_grants",
        sa.Column("bridge_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_column("llm_bridge_grants", "bridge_ids", schema="workspace")
