"""新闻早报：设置、早报、反馈三张表（T-0153）

设计见 docs/v2/NEWS_PLAN.md 第 9 节。

- news_settings：每个工作空间一行；部分唯一索引保证整个部署同一时刻只有一个工作空间启用；
- news_briefings：一份早报（方案 A 由 TrendRadar 推来，方案 B 由智能体写进任务 README）；
- news_feedback：收藏 / 不感兴趣；
- agent_wakeups.source 增加 news（方案 B：到推送时间叫醒智能体写早报）。

downgrade 删除这三张表与 news 唤醒。

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "news_feedback",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("topic", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('favorite', 'dismiss')", name=op.f("ck_news_feedback_kind")),
        sa.CheckConstraint(
            "source_type IN ('hotlist', 'rss')", name=op.f("ck_news_feedback_source_type")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_news_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_news_feedback_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_feedback")),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "kind",
            "url",
            name=op.f("uq_news_feedback_workspace_id_user_id_kind_url"),
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_news_feedback_workspace_created",
        "news_feedback",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "news_settings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "push_times",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"07:30\"]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "briefing_mode", sa.Text(), server_default=sa.text("'trendradar'"), nullable=False
        ),
        sa.Column("agent_instance_id", sa.UUID(), nullable=True),
        sa.Column(
            "platforms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("newsnow_api_url", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "feeds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "exclusions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("filter_method", sa.Text(), server_default=sa.text("'keyword'"), nullable=False),
        sa.Column("ai_model", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("ai_api_base", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("ai_key_ref", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("ai_analysis", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ai_translation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("channel_secrets_ref", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("ingest_token_ref", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("config_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("render_error", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "learned",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
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
            "briefing_mode IN ('trendradar', 'agent')", name=op.f("ck_news_settings_briefing_mode")
        ),
        sa.CheckConstraint(
            "filter_method IN ('keyword', 'ai')", name=op.f("ck_news_settings_filter_method")
        ),
        sa.ForeignKeyConstraint(
            ["agent_instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_news_settings_agent_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["workspace.users.id"],
            name=op.f("fk_news_settings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_news_settings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_news_settings")),
        schema="workspace",
    )
    op.create_index(
        "uq_news_settings_enabled",
        "news_settings",
        ["enabled"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("enabled"),
    )
    op.create_table(
        "news_briefings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("slot", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("parts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_parts", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'receiving'"), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("error", sa.Text(), server_default=sa.text("''"), nullable=False),
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
            "source IN ('trendradar', 'agent')", name=op.f("ck_news_briefings_source")
        ),
        sa.CheckConstraint(
            "status IN ('receiving', 'complete', 'pending', 'failed')",
            name=op.f("ck_news_briefings_status"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace.tasks.id"],
            name=op.f("fk_news_briefings_task_id_tasks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_news_briefings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_briefings")),
        schema="workspace",
    )
    op.create_index(
        "ix_news_briefings_workspace_created",
        "news_briefings",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_news_briefings_task",
        "news_briefings",
        ["task_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    _wakeup_sources("'agent', 'admin', 'issue', 'runner', 'news'")


def _wakeup_sources(values: str) -> None:
    op.drop_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_wakeups_source"),
        "agent_wakeups",
        f"source IN ({values})",
        schema="workspace",
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace.agent_wakeups WHERE source = 'news'")
    _wakeup_sources("'agent', 'admin', 'issue', 'runner'")
    op.drop_index(
        "uq_news_briefings_task",
        table_name="news_briefings",
        schema="workspace",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_news_briefings_workspace_created", table_name="news_briefings", schema="workspace"
    )
    op.drop_table("news_briefings", schema="workspace")
    op.drop_index(
        "uq_news_settings_enabled",
        table_name="news_settings",
        schema="workspace",
        postgresql_where=sa.text("enabled"),
    )
    op.drop_table("news_settings", schema="workspace")
    op.drop_index(
        "ix_news_feedback_workspace_created", table_name="news_feedback", schema="workspace"
    )
    op.drop_table("news_feedback", schema="workspace")
