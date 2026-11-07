"""新闻早报（T-0153，docs/v2/NEWS_PLAN.md 第 9 节）。

- news_settings：每个工作空间一行；整个部署只有一个 TrendRadar，所以同一时刻只能有一个工作空间启用
  （部分唯一索引 uq_news_settings_enabled）。设置里的列表（平台、RSS、主题、渠道）放 JSONB；
  密钥（大模型 Key、推送凭证、接收 token）用凭证加密后存引用，不回传浏览器。
- news_briefings：一份早报（方案 A 由 TrendRadar 的通用 Webhook 分批推来，
  方案 B 由智能体写进任务 README）。
- news_feedback：收藏 / 不感兴趣，供偏好学习用。
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

BRIEFING_MODES = ("trendradar", "agent")
FILTER_METHODS = ("keyword", "ai")
BRIEFING_SOURCES = ("trendradar", "agent")
BRIEFING_STATUSES = ("receiving", "complete", "pending", "failed")
FEEDBACK_KINDS = ("favorite", "dismiss")
FEEDBACK_SOURCES = ("hotlist", "rss")


class NewsSettings(Base):
    __tablename__ = "news_settings"
    __table_args__ = (
        CheckConstraint(f"briefing_mode IN {BRIEFING_MODES}", name="briefing_mode"),
        CheckConstraint(f"filter_method IN {FILTER_METHODS}", name="filter_method"),
        Index(
            "uq_news_settings_enabled",
            "enabled",
            unique=True,
            postgresql_where=text("enabled"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    push_times: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[\"07:30\"]'::jsonb")
    )
    briefing_mode: Mapped[str] = mapped_column(Text, server_default=text("'trendradar'"))
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    platforms: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    newsnow_api_url: Mapped[str] = mapped_column(Text, server_default=text("''"))
    feeds: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    topics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    exclusions: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    filter_method: Mapped[str] = mapped_column(Text, server_default=text("'keyword'"))
    ai_model: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_api_base: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_key_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_analysis: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    ai_translation: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 渠道 → 明文设置（含 enabled）；渠道 → 加密的密钥字段（JSON 整体加密）
    channels: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    channel_secrets_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ingest_token_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # 最近一次生成配置文件
    config_rev: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    render_error: Mapped[str] = mapped_column(Text, server_default=text("''"))
    learned: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NewsBriefing(Base):
    __tablename__ = "news_briefings"
    __table_args__ = (
        CheckConstraint(f"source IN {BRIEFING_SOURCES}", name="source"),
        CheckConstraint(f"status IN {BRIEFING_STATUSES}", name="status"),
        Index("ix_news_briefings_workspace_created", "workspace_id", "created_at"),
        Index(
            "uq_news_briefings_task",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    local_date: Mapped[date] = mapped_column(Date)
    slot: Mapped[str] = mapped_column(Text, server_default=text("''"))  # 推送时间 HH:MM（方案 B）
    source: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, server_default=text("''"))
    content: Mapped[str] = mapped_column(Text, server_default=text("''"))
    parts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    total_parts: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=text("'receiving'"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.tasks.id", ondelete="SET NULL")
    )
    error: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NewsFeedback(Base):
    __tablename__ = "news_feedback"
    __table_args__ = (
        CheckConstraint(f"kind IN {FEEDBACK_KINDS}", name="kind"),
        CheckConstraint(f"source_type IN {FEEDBACK_SOURCES}", name="source_type"),
        UniqueConstraint("workspace_id", "user_id", "kind", "url"),
        Index("ix_news_feedback_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace.users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text, server_default=text("''"))
    source_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    topic: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
