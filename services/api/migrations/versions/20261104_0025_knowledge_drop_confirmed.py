"""知识库取消“人已确认”（T-0102，docs/v2/TIMELINE_PLAN.md 第 3 节）

删除 knowledge_notes.confirmed_by / confirmed_at：笔记以后会很多，人看不过来，确认标记不再维护，
检索也不再按它加权。

downgrade 加回两列（全部为空）和指向 users 的外键；原来的确认记录不恢复。

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_knowledge_notes_confirmed_by_users"),
        "knowledge_notes",
        schema="workspace",
        type_="foreignkey",
    )
    op.drop_column("knowledge_notes", "confirmed_at", schema="workspace")
    op.drop_column("knowledge_notes", "confirmed_by", schema="workspace")


def downgrade() -> None:
    op.add_column(
        "knowledge_notes",
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "knowledge_notes",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_knowledge_notes_confirmed_by_users"),
        "knowledge_notes",
        "users",
        ["confirmed_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
