"""插拔式模块的启用记录（T-0159）

设计见 docs/v2/MODULES_PLAN.md 与 docs/modules/README.md。

- workspace_modules：工作空间 + 模块 key → 是否启用。没有记录时按模块清单的 default_enabled，
  所以已有的工作空间不需要回填数据（新闻早报默认启用，行为与迁成模块之前一样）。

downgrade 删除这张表（只丢失“停用了哪些模块”的记录，模块自己的数据不受影响）。

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_modules",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("module_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "module_key ~ '^[a-z][a-z0-9_]{1,31}$'",
            name=op.f("ck_workspace_modules_module_key_format"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_workspace_modules_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["workspace.users.id"],
            name=op.f("fk_workspace_modules_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "module_key", name=op.f("pk_workspace_modules")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("workspace_modules", schema="workspace")
