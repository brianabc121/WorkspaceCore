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
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
        # 打开发送邮件时必须选了收件人范围（T-0128）
        CheckConstraint(
            "mail_send_scope IS NULL OR mail_send_scope IN ('any', 'listed')",
            name="mail_send_scope",
        ),
        CheckConstraint(
            "NOT mail_send_enabled OR mail_send_scope IS NOT NULL", name="mail_send_needs_scope"
        ),
        CheckConstraint("cardinality(mail_send_recipients) <= 50", name="mail_send_recipients"),
        CheckConstraint(
            "mem_limit_mb IS NULL OR mem_limit_mb BETWEEN 256 AND 1048576", name="mem_limit"
        ),
        CheckConstraint("cpus IS NULL OR (cpus >= 0.1 AND cpus <= 256)", name="cpus"),
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
    # 签发接入包时写进 inboundWhitelist 的 IM 用户 ID（T-0117）；为空表示接入包签发得更早，无法判断
    bundle_whitelist: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # 最近一次签发的 IM 令牌何时到期（令牌只在实例启动时领取；到期前 7 天提醒，迁移 0018）
    im_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 最近一次收到 token 用量上报（接入包的 usage-report，迁移 0027）；为空表示没上报过
    usage_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 项目管理权限（T-0112，迁移 0030）：管理员打开后它能新建项目、把智能体拉进它建的项目；
    # project_admin_by 是打开的人（它建的项目群的群主），不参与 version
    project_admin_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    project_admin_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    project_admin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 允许经 im_send 发图片、文件、视频（T-0119，迁移 0035；默认关闭，方案 D3）
    im_media_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    im_media_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    im_media_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 允许终止其他智能体正在执行的任务（T-0126，迁移 0037；默认关闭，方案 D20）
    stop_others_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    stop_others_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    stop_others_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 邮件（T-0128 / T-0129，迁移 0040，docs/v2/MAIL_PLAN.md 第 5 节）：
    # 查询默认打开，发送默认关闭；打开发送时必须选收件人范围：
    # any（任何地址）或 listed（只限 mail_send_recipients 里的地址 / 域名）
    mail_read_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    mail_send_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    mail_send_scope: Mapped[str | None] = mapped_column(Text)
    mail_send_recipients: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    mail_send_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    mail_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 运行环境权限（T-0138，迁移 0042，docs/v2/RUNNER_PLAN.md 例外八）：
    # 打开后直接执行 runner_* / env_*；runner_limits 是管理员改过的限额
    # （空则用默认值，见 app/domain/runner_specs.py）
    runner_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    runner_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    runner_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runner_limits: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # 多实例（T-0144，迁移 0045，docs/v2/MULTI_OPENCLAW_PLAN.md）：所在的服务器；
    # server_manual=true 表示管理员指定（不再按机器指纹自动改）；资源上限写进接入包的 compose.yaml
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="SET NULL")
    )
    server_manual: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    mem_limit_mb: Mapped[int | None] = mapped_column(Integer)
    cpus: Mapped[float | None] = mapped_column(Float)
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


# 最近一次连接检测的结果（T-0178，迁移 0054）：白名单，只记类别，不记远端原文
CONNECTION_REASONS = (
    "connected",
    "auth_failed",
    "unreachable",
    "host_key_changed",
    "credential_unreadable",
)
# 旧专用公钥的退役状态（T-0178）：有历史公钥 / 私钥的记录迁移时回填 pending
LEGACY_KEY_CLEANUP_STATES = ("not_needed", "pending", "done", "manual_required")


class AgentSshTarget(Base):
    """一台 OpenClaw 服务器的 SSH 登录信息（T-0089；T-0145 起按服务器，迁移 0046）。

    与 agent_servers 一一对应（kind=ssh）：

    - 主机公钥先探测、owner 核对指纹并确认后才固定（host_key_confirmed_at），
      之后的连接只接受这把公钥；
    - 登录密码加密保存（T-0178，迁移 0054，
      docs/decisions/2026-11-09-agent-server-password-auth.md）：
      `login_password_ref` 是 `enc:v1:` 引用，用途绑定到这条记录（agent_server_password:<id>）；
      `credential_revision` 每次设置 / 替换密码加 1（主密钥轮换不加），后台任务据此发现密码被换过；
    - client_key_ref / client_public_key 是旧“一次密码后改用专用密钥”方案（D7）留下的兼容列：
      新逻辑不再生成新的专用密钥，旧公钥经密码登录后定向退役（legacy_key_cleanup_state）。

    每个实例部署在哪个目录记在 agent_deployments。
    """

    __tablename__ = "agent_ssh_targets"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="port"),
        CheckConstraint("credential_revision >= 0", name="credential_revision"),
        CheckConstraint(
            f"last_connection_reason IS NULL OR last_connection_reason IN {CONNECTION_REASONS}",
            name="last_connection_reason",
        ),
        CheckConstraint(
            f"legacy_key_cleanup_state IN {LEGACY_KEY_CLEANUP_STATES}",
            name="legacy_key_cleanup_state",
        ),
        UniqueConstraint("server_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="CASCADE")
    )
    # owner 填写的主机名或 IP，以及探测时解析出的地址（之后都连这个地址）
    host: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(Text)
    host_key_algorithm: Mapped[str] = mapped_column(Text)
    host_key: Mapped[str] = mapped_column(Text)
    host_key_fingerprint: Mapped[str] = mapped_column(Text)
    host_key_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    host_key_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    client_key_ref: Mapped[str | None] = mapped_column(Text)
    client_public_key: Mapped[str | None] = mapped_column(Text)
    # 准备好（初始化完成、Docker 可用）的时间
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 保存的登录密码（T-0178）：只在后台登录时解密，任何接口都不返回
    login_password_ref: Mapped[str | None] = mapped_column(Text)
    credential_revision: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_connection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_connection_reason: Mapped[str | None] = mapped_column(Text)
    legacy_key_cleanup_state: Mapped[str] = mapped_column(Text, server_default=text("'not_needed'"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AgentDeployment(Base):
    """一个实例部署在哪台服务器、哪个目录（T-0145，迁移 0046）。

    开始部署时建；部署成功后记下目录与时间；卸载并删除目录后删掉这一行。
    服务器上还有部署记录时不能移除那台服务器。

    runtime_installed（T-0178，迁移 0054）：部署成功写 true，卸载容器但保留目录写 false；
    迁移前的旧记录是 null（未知），不能凭 installed_at 推断现在还装着。
    """

    __tablename__ = "agent_deployments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("instance_id"),
        Index("ix_agent_deployments_server_id", "server_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="RESTRICT")
    )
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    install_dir: Mapped[str | None] = mapped_column(Text)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runtime_installed: Mapped[bool | None] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# prepare：迁移 0046（T-0145，准备服务器：装专用公钥、装 Docker）
SSH_JOB_KINDS = ("prepare", "install", "uninstall")
SSH_JOB_STATUSES = ("running", "succeeded", "failed", "interrupted")


class AgentSshJob(Base):
    """一次通过 SSH 的准备服务器、部署实例或卸载实例（T-0089 B2 / B3；T-0145）。

    同一台服务器同时只有一个进行中的任务（部署也一个一个来）；准备服务器的任务没有实例。

    在 API 进程内运行；进程重启时 running 改为 interrupted。

    steps：[{key, status, detail}]；log：命令输出的末尾，已脱敏（密码、令牌、私钥不会出现）。
    """

    __tablename__ = "agent_ssh_jobs"
    __table_args__ = (
        # 'uninstall'：迁移 0023（T-0089 B3）
        CheckConstraint(f"kind IN {SSH_JOB_KINDS}", name="kind"),
        CheckConstraint(f"status IN {SSH_JOB_STATUSES}", name="status"),
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        # 同一台目标服务器同时只有一个进行中的任务
        Index(
            "uq_agent_ssh_jobs_running",
            "target_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_agent_ssh_jobs_target_started", "target_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_ssh_targets.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    log: Mapped[str] = mapped_column(Text, server_default=text("''"))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated()


OPS_ACTIONS = ("status", "logs", "restart", "up", "prune", "report", "containers")
# 服务器级的动作（T-0145）：作用于整台服务器，不属于某个实例
SERVER_OPS_ACTIONS = ("prune", "containers")
OPS_SERVICES = ("openclaw", "tunnel", "image-proxy")
OPS_STATUSES = ("running", "succeeded", "failed", "interrupted")


class AgentOpsAction(Base):
    """对用 SSH 部署过的实例执行的一次固定运维动作（T-0114，AGENT_OPS_PLAN 6.1，迁移 0032）。

    一台服务器多个实例后（T-0145，迁移 0046）：每条都记在服务器上（server_id）；清理镜像、
    查看全部容器是服务器级的（没有实例）。同一台服务器同时只有一个进行中的动作，
    与这台服务器上的部署任务也互斥。

    命令写死在工作台里（app/domain/agent_ops.py），不拼接输入；在 API 进程内运行，进程重启时
    running 改为 interrupted。output 是脱敏后的输出（最多 64 KB），7 天后连同记录一起删除；
    审计只记谁、什么时候、什么动作、退出码。
    """

    __tablename__ = "agent_ops_actions"
    __table_args__ = (
        CheckConstraint(f"action IN {OPS_ACTIONS}", name="action"),
        CheckConstraint(f"service IS NULL OR service IN {OPS_SERVICES}", name="service"),
        CheckConstraint(f"status IN {OPS_STATUSES}", name="status"),
        CheckConstraint(
            f"instance_id IS NOT NULL OR action IN {SERVER_OPS_ACTIONS}", name="instance_scope"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        # 同一台服务器同时只有一个进行中的动作
        Index(
            "uq_agent_ops_actions_running",
            "server_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_agent_ops_actions_instance_started", "instance_id", "started_at"),
        Index("ix_agent_ops_actions_server_started", "server_id", "started_at"),
        Index("ix_agent_ops_actions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="CASCADE")
    )
    action: Mapped[str] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str] = mapped_column(Text, server_default=text("''"))
    truncated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


RUN_STOP_STATUSES = ("pending", "running", "done", "failed", "expired")


class AgentRunStop(Base):
    """终止令（T-0126，方案第 7.3 节）：叫停一个实例正在执行的任务。

    软停止：`soft_until` 之前这个实例的 MCP 工具调用返回 RUN_STOPPED；
    硬停止：接入包的 stop-runs.mjs 领取后在容器里对正在运行的会话调 `sessions.abort`。
    """

    __tablename__ = "agent_run_stops"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STOP_STATUSES}", name="status"),
        CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="reason"),
        Index("ix_agent_run_stops_instance_created", "instance_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    requested_by_instance: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.tasks.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    soft_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    aborted: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
