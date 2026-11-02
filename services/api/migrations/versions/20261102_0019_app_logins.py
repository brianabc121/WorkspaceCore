"""Vikunja、n8n 管理界面的登录账号（T-0080）

人类要求在「项目」「自动化」页直接看到这两个账号，复制粘贴去登录。
密码只以 enc:v1 引用保存；每个工作空间每个应用一条。
只新增表，downgrade 删除即可回退（会丢失保存的账号，Vikunja、n8n 里的账号不受影响）。

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_logins",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("app", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_ref", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.CheckConstraint("app IN ('vikunja', 'n8n')", name=op.f("ck_app_logins_app")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_app_logins_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["workspace.users.id"],
            name=op.f("fk_app_logins_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_logins")),
        sa.UniqueConstraint("workspace_id", "app", name=op.f("uq_app_logins_workspace_id_app")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("app_logins", schema="workspace")
