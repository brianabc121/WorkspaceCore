"""中转站管理：模型连接、模型目录、连接授权、连接检测、智能体主模型绑定与配置应用任务。

依据 docs/v2/model-connections/ 的设计与接口约定（T-0200 ~ T-0208），以及
docs/decisions/2026-11-09-model-connections.md：

- 连接只保存业务元数据（类型、协议、base URL、模型范围、启停）；API Key 放在
  agent_secrets / agent_secret_versions 里（加密用途绑定 secret_id + value_version），
  credential_secret_id 指向那条内部凭据，一条凭据最多对应一个连接（密钥库里看不到它）；
- 授权、绑定都带 workspace_id 的复合外键，跨工作空间的引用在数据库层面就建不起来；
- 绑定分开保存“目标”（connection_id / model_id / desired_revision）与“已应用”
  （applied_*），实例回报确认后才写已应用；
- 检测结果只保存固定类别，不存远端原始响应。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CONNECTION_TYPES = ("official", "relay")
OFFICIAL_PROVIDERS = ("openai", "anthropic", "google")
PROTOCOLS = ("openai_compatible", "anthropic_native", "gemini_native")
MODEL_SCOPES = ("selected", "all_available")
CONFIGURATION_STATES = ("draft", "ready", "needs_review")
SYNC_STATES = ("complete", "partial", "unsupported", "failed")
MODEL_SOURCES = ("manual", "discovered")
MODEL_AVAILABILITY = ("listed", "verified")
CHECK_MODES = ("metadata", "inference")
CHECK_STATUSES = ("passed", "failed", "rate_limited", "unsupported", "unknown")
CHECK_REASONS = (
    "unauthorized",
    "forbidden",
    "timeout",
    "unreachable",
    "tls_error",
    "invalid_response",
    "model_not_available",
    "protocol_unsupported",
    "models_endpoint_unsupported",
    "destination_not_allowed",
    "credential_unreadable",
    "stale_revision",
)
APPLY_STATES = (
    "pending",
    "applying",
    "applied",
    "failed",
    "unknown",
    "manual_required",
)
JOB_STATES = ("queued", "running", "succeeded", "failed", "unknown", "manual_required")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ModelConnection(Base):
    __tablename__ = "model_connections"
    __table_args__ = (
        CheckConstraint(
            f"connection_type IS NULL OR connection_type IN {_in(CONNECTION_TYPES)}",
            name="connection_type",
        ),
        CheckConstraint(
            f"official_provider IS NULL OR official_provider IN {_in(OFFICIAL_PROVIDERS)}",
            name="official_provider",
        ),
        CheckConstraint(f"protocol IS NULL OR protocol IN {_in(PROTOCOLS)}", name="protocol"),
        CheckConstraint(f"model_scope IN {_in(MODEL_SCOPES)}", name="model_scope"),
        CheckConstraint(
            f"configuration_state IN {_in(CONFIGURATION_STATES)}", name="configuration_state"
        ),
        CheckConstraint(
            f"sync_state IS NULL OR sync_state IN {_in(SYNC_STATES)}", name="sync_state"
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="name_length"),
        CheckConstraint("char_length(description) <= 500", name="description_length"),
        CheckConstraint(
            "base_url IS NULL OR char_length(base_url) <= 2048", name="base_url_length"
        ),
        # 官方标识只能配“官方直连”
        CheckConstraint(
            "official_provider IS NULL OR connection_type = 'official'", name="official_type"
        ),
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("credential_secret_id"),
        ForeignKeyConstraint(
            ["workspace_id", "credential_secret_id"],
            ["workspace.agent_secrets.workspace_id", "workspace.agent_secrets.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_model_connections_workspace_updated", "workspace_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    connection_type: Mapped[str | None] = mapped_column(Text)
    official_provider: Mapped[str | None] = mapped_column(Text)
    protocol: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    credential_secret_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    model_scope: Mapped[str] = mapped_column(Text, server_default=text("'selected'"))
    configuration_state: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # URL / 协议 / 模型集合 / Key 变化时加 1；检测结果只在修订号仍一致时写回 last_check
    config_revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    sync_state: Mapped[str | None] = mapped_column(Text)
    # 最近一次完整同步到的模型 ID（最多 1000 个）；同步不完整或失败时保留上一次的完整快照
    discovered_models: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    models_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ModelConnectionModel(Base):
    """连接下明确的模型 ID（原样保存服务商的大小写），每条连接最多 1000 个。"""

    __tablename__ = "model_connection_models"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["workspace.model_connections.workspace_id", "workspace.model_connections.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(f"source IN {_in(MODEL_SOURCES)}", name="source"),
        CheckConstraint(f"availability IN {_in(MODEL_AVAILABILITY)}", name="availability"),
        CheckConstraint("char_length(model_id) BETWEEN 1 AND 128", name="model_id_length"),
        CheckConstraint("char_length(display_name) <= 128", name="display_name_length"),
        UniqueConstraint("connection_id", "model_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    model_id: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    availability: Mapped[str] = mapped_column(Text, server_default=text("'listed'"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()


class ModelConnectionGrant(Base):
    """连接授权给哪些实例。"""

    __tablename__ = "model_connection_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["workspace.model_connections.workspace_id", "workspace.model_connections.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("connection_id", "instance_id"),
        Index("ix_model_connection_grants_instance", "instance_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class ModelConnectionCheck(Base):
    """一次检测的结果：固定的状态与原因类别，不存远端原始响应。"""

    __tablename__ = "model_connection_checks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["workspace.model_connections.workspace_id", "workspace.model_connections.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(f"mode IN {_in(CHECK_MODES)}", name="mode"),
        CheckConstraint(f"status IN {_in(CHECK_STATUSES)}", name="status"),
        CheckConstraint(f"reason IS NULL OR reason IN {_in(CHECK_REASONS)}", name="reason"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency"),
        Index("ix_model_connection_checks_connection_at", "connection_id", "checked_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    config_revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = _created()
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )


class AgentModelBinding(Base):
    """每个实例一个主模型绑定：目标（connection_id / model_id）与已应用快照分开保存。"""

    __tablename__ = "agent_model_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["workspace.model_connections.workspace_id", "workspace.model_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "applied_connection_id"],
            ["workspace.model_connections.workspace_id", "workspace.model_connections.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"apply_state IN {_in(APPLY_STATES)}", name="apply_state"),
        CheckConstraint("(connection_id IS NULL) = (model_id IS NULL)", name="target_pair"),
        CheckConstraint(
            "(applied_connection_id IS NULL) = (applied_model_id IS NULL)", name="applied_pair"
        ),
        Index("ix_agent_model_bindings_connection", "connection_id"),
        Index("ix_agent_model_bindings_applied_connection", "applied_connection_id"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    model_id: Mapped[str | None] = mapped_column(Text)
    # 目标每变一次加 1；实例按它判断要不要重新应用，迟到的旧确认不能覆盖新目标
    desired_revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    applied_connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    applied_model_id: Mapped[str | None] = mapped_column(Text)
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    apply_state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ModelApplyJob(Base):
    """把绑定的目标应用到实例的任务。实例领取（running）并回报（succeeded / failed）。

    同一实例同一目标修订只有一个任务（幂等）。"""

    __tablename__ = "model_apply_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(f"state IN {_in(JOB_STATES)}", name="state"),
        UniqueConstraint("instance_id", "desired_revision"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    desired_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    attempt: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
