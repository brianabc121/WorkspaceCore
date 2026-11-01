"""OpenClaw 实例：记下最近一次签发的 IM 令牌何时到期（V2 T-0063）

方案第 4.3 节：IM 令牌有效期 30 天，到期前 7 天提醒管理员。令牌只在实例启动时领取（OC-0 F1），
工作台签发时按 OpenIM 返回的 expireTimeSeconds 记下到期时间，Worker 每天检查。
只新增一个可空列，downgrade 删除即可回退。

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_instances",
        sa.Column("im_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_column("agent_instances", "im_token_expires_at", schema="workspace")
