"""仪表盘与组件（S2-01）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 8–9 项；
约定见 docs/decisions/2026-10-30-sprint-2-dashboard-contract.md。
每个工作空间恰好一个默认仪表盘（部分唯一索引）。为已有的工作空间补建默认“首页”与入门组件：
只新增行，downgrade 删除两张表即可回退。入门组件是这一刻的快照，不引用应用代码。

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STARTER_WIDGETS = [
    (
        "note",
        {"text": "欢迎使用 My Workspace。进入编辑模式后可以拖动、调整或添加组件。"},
        {"desktop": {"x": 0, "y": 0, "w": 6, "h": 3}, "mobile": {"y": 0, "h": 3}},
    ),
    (
        "quick_links",
        {"links": []},
        {"desktop": {"x": 6, "y": 0, "w": 6, "h": 3}, "mobile": {"y": 3, "h": 3}},
    ),
    (
        "todo_summary",
        {},
        {"desktop": {"x": 0, "y": 3, "w": 4, "h": 3}, "mobile": {"y": 6, "h": 3}},
    ),
    (
        "today_tasks",
        {"limit": 5},
        {"desktop": {"x": 4, "y": 3, "w": 8, "h": 4}, "mobile": {"y": 9, "h": 4}},
    ),
]


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            ["owner_id"],
            ["workspace.users.id"],
            name=op.f("fk_dashboards_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_dashboards_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboards")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_dashboards_workspace_id"),
        "dashboards",
        ["workspace_id"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_dashboards_default_per_workspace",
        "dashboards",
        ["workspace_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "widgets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("dashboard_id", sa.UUID(), nullable=False),
        sa.Column("widget_type", sa.Text(), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("layout_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            ["dashboard_id"],
            ["workspace.dashboards.id"],
            name=op.f("fk_widgets_dashboard_id_dashboards"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_widgets")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_widgets_dashboard_id"),
        "widgets",
        ["dashboard_id"],
        unique=False,
        schema="workspace",
    )

    # 为已有的工作空间补建默认仪表盘与入门组件（新表，此时的默认仪表盘都是刚建的）
    op.execute(
        """
        INSERT INTO workspace.dashboards (workspace_id, owner_id, title, is_default)
        SELECT id, owner_id, '首页', true FROM workspace.workspaces
        """
    )
    bind = op.get_bind()
    for widget_type, config, layout in STARTER_WIDGETS:
        bind.execute(
            sa.text(
                """
                INSERT INTO workspace.widgets (dashboard_id, widget_type, config_json, layout_json)
                SELECT id, :widget_type, CAST(:config AS jsonb), CAST(:layout AS jsonb)
                FROM workspace.dashboards WHERE is_default
                """
            ),
            {
                "widget_type": widget_type,
                "config": json.dumps(config, ensure_ascii=False),
                "layout": json.dumps(layout),
            },
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_widgets_dashboard_id"), table_name="widgets", schema="workspace")
    op.drop_table("widgets", schema="workspace")
    op.drop_index(
        "uq_dashboards_default_per_workspace",
        table_name="dashboards",
        schema="workspace",
        postgresql_where=sa.text("is_default"),
    )
    op.drop_index(op.f("ix_dashboards_workspace_id"), table_name="dashboards", schema="workspace")
    op.drop_table("dashboards", schema="workspace")
