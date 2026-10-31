"""自动化：n8n 连接、白名单工作流与执行记录。

docs/DEVELOPMENT_SPEC.md 第 7.5 节；Sprint 5 契约第 6 节。

- n8n 连接每个工作空间一条；API Key 与 Webhook 共享密钥只以 `enc:v1:` 引用保存，数据库里没有明文。
- 白名单移出时只做软删除（deleted_at），执行记录保留，可在审计里追溯。
- 执行记录只保存状态、n8n 的执行 ID 与错误摘要，不保存触发参数与第三方 payload。
  trace_id 是触发请求的 W3C traceparent 里的 trace-id，用来在 n8n 的执行记录里找到对应的那一条。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class AutomationConnection(Base):
    __tablename__ = "automation_connections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"), unique=True
    )
    base_url: Mapped[str] = mapped_column(Text)
    public_url: Mapped[str] = mapped_column(Text)
    # pending / active / unauthorized / unavailable / disabled（与集成的状态相同）
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    api_key_ref: Mapped[str] = mapped_column(Text)
    trigger_secret_ref: Mapped[str] = mapped_column(Text)
    # instance_version、last_tested_at、last_sync_at、last_error 等非敏感信息
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (
        Index(
            "uq_automations_workspace_path",
            "workspace_id",
            "webhook_path",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_automations_workspace_workflow",
            "workspace_id",
            "workflow_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    workflow_id: Mapped[str] = mapped_column(Text)
    webhook_path: Mapped[str] = mapped_column(Text)
    # ParamSpec 列表（Sprint 5 契约第 6.2 节）
    params_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # 最近一次从 n8n 读到的工作流是否激活；从未读到时为 NULL
    workflow_active: Mapped[bool | None] = mapped_column(Boolean)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        Index("ix_automation_runs_automation_created", "automation_id", "created_at", "id"),
        # 对账只看没有结束的执行
        Index(
            "ix_automation_runs_open",
            "workspace_id",
            "created_at",
            postgresql_where=text("status IN ('queued', 'running', 'unknown')"),
        ),
        Index(
            "uq_automation_runs_execution",
            "automation_id",
            "external_execution_id",
            unique=True,
            postgresql_where=text("external_execution_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    automation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.automations.id", ondelete="CASCADE")
    )
    # queued / running / success / error / canceled / unknown
    status: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(Text)
    external_execution_id: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
