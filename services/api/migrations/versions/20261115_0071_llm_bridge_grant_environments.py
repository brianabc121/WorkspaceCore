"""大模型客户端：按实例限定可用的开发环境（T-0247，docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 0-2）

llm_bridge_grants 加一列 environment_ids：管理员给这个实例勾选的开发环境（管理器里的环境编号）。
空列表表示一个都不能用——例外九写的是“开发会话只能使用管理员定好的环境”，之前没有这个限制，
有写权限的实例能用任何启用的环境。

已有的授权升级后都是空列表：智能体暂时不能新建开发会话（已经建好的会话不受影响），
要管理员在“智能体权限”里勾选后才行。这是有意收紧。

downgrade 删掉这一列，回到不限制的状态。

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workspace"


def upgrade() -> None:
    op.add_column(
        "llm_bridge_grants",
        sa.Column(
            "environment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("llm_bridge_grants", "environment_ids", schema=SCHEMA)
