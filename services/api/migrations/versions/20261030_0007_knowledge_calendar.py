"""知识来源、文档收藏、跨系统关联与本地日历（S4-01、S4-02）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 11、13 项；约定见
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 3 ~ 5 节。
resource_links 的 source / target 分别指向收藏与外部资源投影，任一方删除时级联删除。
只新增表，downgrade 删除即可回退（会丢失收藏与日程，回退前先备份）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("location", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("all_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "(all_day AND start_date IS NOT NULL AND end_date IS NOT NULL)"
            " OR (NOT all_day AND start_date IS NULL AND end_date IS NULL)",
            name=op.f("ck_calendar_events_all_day_dates"),
        ),
        sa.CheckConstraint("end_at > start_at", name=op.f("ck_calendar_events_time_order")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["workspace.users.id"],
            name=op.f("fk_calendar_events_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_calendar_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_events")),
        schema="workspace",
    )
    op.create_index(
        "ix_calendar_events_workspace_start",
        "calendar_events",
        ["workspace_id", "start_at"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_sources_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_knowledge_sources_workspace_id"),
        "knowledge_sources",
        ["workspace_id"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("link_kind", sa.Text(), nullable=False),
        sa.Column("affine_workspace_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("suffix", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'workspace'"), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "link_kind IN ('workspace_doc', 'share')", name=op.f("ck_bookmarks_link_kind")
        ),
        sa.CheckConstraint(
            "visibility IN ('workspace', 'private')", name=op.f("ck_bookmarks_visibility")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["workspace.users.id"],
            name=op.f("fk_bookmarks_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["workspace.knowledge_sources.id"],
            name=op.f("fk_bookmarks_source_id_knowledge_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_bookmarks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookmarks")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_bookmarks_source_id"), "bookmarks", ["source_id"], unique=False, schema="workspace"
    )
    op.create_index(
        "ix_bookmarks_workspace_updated",
        "bookmarks",
        ["workspace_id", "updated_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_bookmarks_private_doc",
        "bookmarks",
        ["source_id", "affine_workspace_id", "doc_id", "owner_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("visibility = 'private'"),
    )
    op.create_index(
        "uq_bookmarks_workspace_doc",
        "bookmarks",
        ["source_id", "affine_workspace_id", "doc_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("visibility = 'workspace'"),
    )
    op.create_table(
        "resource_links",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("source_resource_id", sa.UUID(), nullable=False),
        sa.Column("target_resource_id", sa.UUID(), nullable=False),
        sa.Column("relation", sa.Text(), server_default=sa.text("'related'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id"],
            ["workspace.bookmarks.id"],
            name=op.f("fk_resource_links_source_resource_id_bookmarks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_resource_id"],
            ["workspace.external_resources.id"],
            name=op.f("fk_resource_links_target_resource_id_external_resources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_resource_links_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_links")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_resource_links_target_resource_id"),
        "resource_links",
        ["target_resource_id"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_resource_links_pair",
        "resource_links",
        ["source_resource_id", "target_resource_id"],
        unique=True,
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("resource_links", schema="workspace")
    op.drop_table("bookmarks", schema="workspace")
    op.drop_table("knowledge_sources", schema="workspace")
    op.drop_table("calendar_events", schema="workspace")
