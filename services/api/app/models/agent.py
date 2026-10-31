"""OpenClaw 实例、接入凭证、密钥库与内部 CA（V2 OC-2）。

依据 docs/v2/TRANSFORMATION_PLAN.md 第 4.1 ~ 4.3、5 节与
docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节：

- 实例令牌只存 SHA-256（令牌是 32 字节随机数，不需要慢哈希）；客户端证书只存 SHA-256 指纹。
- 密钥的值以 `enc:v1:` 引用保存，加密用途绑定到密钥与版本；轮换后旧版本的密文立即清除，
  只留版本号与时间，旧值不再下发、也无法恢复。
- 内部 CA 的私钥同样以 `enc:v1:` 引用保存；整个部署共用一套 CA（Caddy 的外网入口只信任它）。
- 取密钥记录单独成表（量大、按实例与结果筛选），不进 audit_logs。
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

NETWORK_MODES = ("intranet", "public")
INSTANCE_STATUSES = ("pending", "online", "offline", "disabled")
MCP_SCOPES = ("read", "read_write")
MAX_ROLES = ("admin", "member", "viewer")
CREDENTIAL_KINDS = ("token", "client_cert")
SECRET_KINDS = ("model_api_key", "api_token", "generic")
ACCESS_RESULTS = ("ok", "not_found", "rate_limited")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentSettings(Base):
    """每个工作空间一行：实例总开关。没有这一行时视为打开。"""

    __tablename__ = "agent_settings"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    agents_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )


class AgentInstance(Base):
    __tablename__ = "agent_instances"
    __table_args__ = (
        CheckConstraint(f"network_mode IN {NETWORK_MODES}", name="network_mode"),
        CheckConstraint(f"status IN {INSTANCE_STATUSES}", name="status"),
        CheckConstraint(f"scope IN {MCP_SCOPES}", name="scope"),
        CheckConstraint(f"max_role IN {MAX_ROLES}", name="max_role"),
        # 复合外键（凭证、授权）引用 (workspace_id, id)，保证子记录与实例在同一个工作空间
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("im_user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    network_mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    scope: Mapped[str] = mapped_column(Text, server_default=text("'read'"))
    max_role: Mapped[str] = mapped_column(Text, server_default=text("'member'"))
    # oc_ 加实例 UUID 的 32 位十六进制；由应用在插入前生成（需要先知道 id）
    im_user_id: Mapped[str] = mapped_column(Text)
    im_nickname: Mapped[str] = mapped_column(Text)
    im_ready: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    acting_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    reported_version: Mapped[str | None] = mapped_column(Text)
    plugin_version: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bundle_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AgentCredential(Base):
    __tablename__ = "agent_credentials"
    __table_args__ = (
        CheckConstraint(f"kind IN {CREDENTIAL_KINDS}", name="kind"),
        CheckConstraint(f"ingress IN {NETWORK_MODES}", name="ingress"),
        CheckConstraint(
            "(kind = 'token' AND token_hash IS NOT NULL AND cert_fingerprint IS NULL)"
            " OR (kind = 'client_cert' AND cert_fingerprint IS NOT NULL AND token_hash IS NULL)",
            name="material",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash"),
        UniqueConstraint("cert_fingerprint"),
        Index(
            "ix_agent_credentials_active",
            "instance_id",
            "kind",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text)
    ingress: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(Text)
    token_prefix: Mapped[str | None] = mapped_column(Text)
    cert_fingerprint: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AgentSecret(Base):
    __tablename__ = "agent_secrets"
    __table_args__ = (
        CheckConstraint(f"kind IN {SECRET_KINDS}", name="kind"),
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("workspace_id", "id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default=text("'generic'"))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    current_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    last_rotated_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AgentSecretVersion(Base):
    """密钥的每个版本。只有当前版本有密文；轮换时旧版本的 ciphertext_ref 置为 NULL。"""

    __tablename__ = "agent_secret_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "secret_id"],
            ["workspace.agent_secrets.workspace_id", "workspace.agent_secrets.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("secret_id", "value_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    secret_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    value_version: Mapped[int] = mapped_column(Integer)
    ciphertext_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSecretGrant(Base):
    __tablename__ = "agent_secret_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "secret_id"],
            ["workspace.agent_secrets.workspace_id", "workspace.agent_secrets.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("instance_id", "ref_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    secret_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ref_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AgentSecretAccess(Base):
    """取密钥记录：实例、ref_id、密钥与版本、结果、来源 IP。不含任何密钥内容。"""

    __tablename__ = "agent_secret_accesses"
    __table_args__ = (
        CheckConstraint(f"result IN {ACCESS_RESULTS}", name="result"),
        Index("ix_agent_secret_accesses_workspace_at", "workspace_id", "at"),
        Index("ix_agent_secret_accesses_instance_at", "instance_id", "at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    instance_name: Mapped[str] = mapped_column(Text)
    ref_id: Mapped[str] = mapped_column(Text)
    secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_secrets.id", ondelete="SET NULL")
    )
    value_version: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(Text)
    source_ip: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = _created()


class AgentCa(Base):
    """内部 CA：签发外网模式的客户端证书。整个部署同时只有一套在用（retired_at 为 NULL）。"""

    __tablename__ = "agent_ca"
    __table_args__ = (
        Index(
            "uq_agent_ca_active",
            text("(retired_at IS NULL)"),
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    cert_pem: Mapped[str] = mapped_column(Text)
    key_ref: Mapped[str] = mapped_column(Text)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
