"""第三方集成、外部资源投影、幂等记录与同步游标。

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 6、7、10、15、16、18 项。

约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md。凭证与 Webhook 签名密钥只以
`enc:v1:` 引用（Sprint 1 契约第 12 节）保存，数据库里没有明文。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", "name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    public_url: Mapped[str] = mapped_column(Text)
    # pending / active / unauthorized / unavailable / disabled（契约第 4.2 节）
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    # tested_version、api_version、instance_version、last_tested_at、webhooks 等非敏感信息
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Webhook 签名密钥的 enc:v1 引用（用途 integration_webhook:<id>）
    webhook_secret_ref: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class IntegrationAccount(Base):
    """访问第三方用的凭证；V1 每个集成一条（服务 Token）。"""

    __tablename__ = "integration_accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.integrations.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    auth_type: Mapped[str] = mapped_column(Text)
    # enc:v1:… 或 env:WS_SECRET_…（Sprint 1 契约第 12 节）
    credential_ref: Mapped[str] = mapped_column(Text)
    scopes_json: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ExternalResource(Base):
    """外部对象的只读投影：Vikunja 是主源，这里只存映射、展示所需的字段和新鲜度。"""

    __tablename__ = "external_resources"
    __table_args__ = (
        UniqueConstraint("integration_id", "resource_type", "external_id"),
        Index(
            "ix_external_resources_workspace_type",
            "workspace_id",
            "resource_type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.integrations.id", ondelete="CASCADE")
    )
    resource_type: Mapped[str] = mapped_column(Text)  # project / task
    external_id: Mapped[str] = mapped_column(Text)  # Vikunja 的数字 ID（字符串）
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # tombstone：外部已删除（规范第 10 节）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRequest(Base):
    """写操作的幂等记录（契约第 7 节）；保留 24 小时。"""

    __tablename__ = "idempotency_requests"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    request_fingerprint: Mapped[str] = mapped_column(Text)
    # processing / succeeded / failed / unknown
    status: Mapped[str] = mapped_column(Text)
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 查询未知结果所需的上下文（操作类型、目标、发出时间），不含任务内容以外的敏感信息
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SyncCursor(Base):
    """每个集成、每种资源的同步进度（规范第 5.1 节第 16 项）。"""

    __tablename__ = "sync_cursors"
    __table_args__ = (UniqueConstraint("integration_id", "resource_type"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.integrations.id", ondelete="CASCADE")
    )
    resource_type: Mapped[str] = mapped_column(Text)
    cursor_value: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 机器可读的错误码，不含外部响应原文
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated_at()


class WebhookInboxEvent(Base):
    """收到的 Webhook 事件（规范第 5.1 节第 15 项、第 10 节）。

    status：pending → processing → completed；失败 retry（next_retry_at 之后再试），
    超过次数上限或永久失败为 dead_letter。processing 时 next_retry_at 是租约到期时间：
    处理它的进程中途退出后，租约到期即可被重新领取。payload_json 只保存事件名、时间与资源 ID。
    """

    __tablename__ = "webhook_inbox"
    __table_args__ = (
        UniqueConstraint("integration_id", "event_key"),
        Index(
            "ix_webhook_pending",
            "next_retry_at",
            postgresql_where=text("status IN ('pending', 'retry', 'processing')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.integrations.id", ondelete="CASCADE")
    )
    # sha256(原始请求体)：Vikunja 没有投递 ID，请求体里的 time 精确到纳秒（核验 ADR 第 6 节）
    event_key: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = _created_at()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 机器可读的错误码，不含外部响应原文
    error_text: Mapped[str | None] = mapped_column(Text)
