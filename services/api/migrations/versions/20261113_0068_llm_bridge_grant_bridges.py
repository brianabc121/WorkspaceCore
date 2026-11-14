"""大模型客户端管理：按实例限定能用哪几个 MCP 管理器（T-0230，docs/v2/LLM_BRIDGE_PLAN.md 第 19 节）

llm_bridge_grants 新增一列 bridge_ids（JSONB，可空）：实例能用的会话桥 id 列表。
NULL 表示都能用（包括以后登记的），已有的行保持 NULL，行为不变；空列表表示一个都不能用。
只新增一列，不改已有数据。

downgrade 仅在没有显式限定时删列，拒绝丢失限定信息或扩大桥访问权限。

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.runtime_schema_compat_0068 import (
    reconcile_legacy_runtime,
    refuse_bridge_scope_loss,
)

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older acceptance branches also stamped gateway-only/bridge-only schemas 0067.
    # Validate and complete them before applying the published scope column DDL.
    reconcile_legacy_runtime()
    op.add_column(
        "llm_bridge_grants",
        sa.Column("bridge_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="workspace",
    )


def downgrade() -> None:
    refuse_bridge_scope_loss()
    op.drop_column("llm_bridge_grants", "bridge_ids", schema="workspace")
