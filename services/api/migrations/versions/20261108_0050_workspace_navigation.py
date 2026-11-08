"""侧栏布局：自定义分组、页面挪分组、零代码页面（T-0161）

设计见 docs/v2/MODULES_PLAN.md 第 2 节。每个工作空间一行，布局放 JSONB（接口层校验）。
没有记录时用默认侧栏，所以已有工作空间不需要回填。

downgrade 删除这张表（自定义的分组和零代码页面会丢失，页面本身的数据不受影响）。

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_navigation",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "layout",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_workspace_navigation_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["workspace.users.id"],
            name=op.f("fk_workspace_navigation_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_workspace_navigation")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("workspace_navigation", schema="workspace")
