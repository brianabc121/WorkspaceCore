"""接入包的私聊白名单快照（T-0117，docs/v2/AGENT_MESSAGING_PLAN.md 第 3.1 节）

agent_instances.bundle_whitelist：签发接入包时写进 OpenClaw 配置的 inboundWhitelist
（当时的 owner / admin 与全部实例的 IM 用户 ID）。`im_list_targets` 用它告诉智能体
“这个管理员回你的私聊你收不收得到”。为空表示接入包是本迁移之前签发的，无法判断。

只加一个可空列，downgrade 删除即可回退。

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_instances",
        sa.Column("bundle_whitelist", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_column("agent_instances", "bundle_whitelist", schema="workspace")
