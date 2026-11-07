"""运行服务器（T-0136 ~ T-0139，docs/v2/RUNNER_PLAN.md 第 12 节）。

- runner_hosts：运行服务器（V1 每个工作空间一台）：凭证哈希、证书指纹、容量、版本、最后上报。
- runner_templates：环境模板：规格（JSON）、版本号、构建状态与镜像；建立者是实例或用户。
- runner_environments：环境（一个容器 + 它的数据卷）：类别、复用键、状态、
  **配置记录**（建这个环境时的完整规格）、初始化步骤、用量与时间。
- runner_delete_conditions：环境的删除条件（“删除回调”）。
- runner_runs：在环境里执行的一次命令：状态、退出码、输出（最多 1 MB，脱敏）。
- runner_commands：下发给 runner-agent 的指令（幂等与对账用）。
  **不存密钥原文**：注入的密钥只在下发时解密。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

HOST_STATUSES = ("pending", "online", "offline", "disabled")
HOST_INGRESSES = ("intranet", "public")
TEMPLATE_STATUSES = ("active", "disabled")
BUILD_STATUSES = ("pending", "building", "ready", "failed")
LIFETIMES = ("once", "session", "persistent")
ENV_STATES = ("creating", "running", "stopped", "archived", "pending_delete", "deleted", "failed")
SHARES = ("none", "project")
CONDITION_KINDS = (
    "run_finished",
    "task_done",
    "issue_closed",
    "project_archived",
    "at",
    "idle",
    "released",
)
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "timeout", "unknown", "cancelled")
COMMAND_STATUSES = ("pending", "sent", "done", "failed", "unknown", "cancelled")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _workspace() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspace.workspaces.id", ondelete="CASCADE"))


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _when() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True))


def _instance() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("workspace.agent_instances.id", ondelete="SET NULL"))


def _json() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class RunnerHost(Base):
    __tablename__ = "runner_hosts"
    __table_args__ = (
        UniqueConstraint("workspace_id"),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("token_hash"),
        CheckConstraint(f"status IN {HOST_STATUSES}", name="status"),
        CheckConstraint(f"ingress IN {HOST_INGRESSES}", name="ingress"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    ingress: Mapped[str] = mapped_column(Text, server_default=text("'intranet'"))
    token_hash: Mapped[str] = mapped_column(Text)
    token_prefix: Mapped[str] = mapped_column(Text)
    cert_fingerprint: Mapped[str | None] = mapped_column(Text)
    cpus: Mapped[int | None] = mapped_column(Integer)
    mem_bytes: Mapped[int | None] = mapped_column(BigInteger)
    disk_bytes: Mapped[int | None] = mapped_column(BigInteger)
    disk_free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    kvm: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    runtimes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    docker_version: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(Text)
    # 最近一次上报的汇总（容器、数据卷、镜像的数量与占用），页面总览用
    report: Mapped[dict[str, Any]] = _json()
    # 回收策略的覆盖值（第 6 节默认值见 app/domain/runner_specs.py）
    policy: Mapped[dict[str, Any]] = _json()
    last_seen_at: Mapped[datetime | None] = _when()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunnerTemplate(Base):
    __tablename__ = "runner_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint(f"status IN {TEMPLATE_STATUSES}", name="status"),
        CheckConstraint(f"build_status IN {BUILD_STATUSES}", name="build_status"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    owner_instance_id: Mapped[uuid.UUID | None] = _instance()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    spec: Mapped[dict[str, Any]] = _json()
    build_status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    build_error: Mapped[str | None] = mapped_column(Text)
    build_log: Mapped[str] = mapped_column(Text, server_default=text("''"))
    image_ref: Mapped[str | None] = mapped_column(Text)
    image_digest: Mapped[str | None] = mapped_column(Text)
    image_bytes: Mapped[int | None] = mapped_column(BigInteger)
    build_seconds: Mapped[float | None] = mapped_column(Float)
    built_at: Mapped[datetime | None] = _when()
    last_used_at: Mapped[datetime | None] = _when()
    use_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunnerEnvironment(Base):
    __tablename__ = "runner_environments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint(f"lifetime IN {LIFETIMES}", name="lifetime"),
        CheckConstraint(f"state IN {ENV_STATES}", name="state"),
        CheckConstraint(f"share IN {SHARES}", name="share"),
        CheckConstraint("share = 'none' OR project_id IS NOT NULL", name="share_project"),
        Index("ix_runner_environments_workspace_state", "workspace_id", "state"),
        Index("ix_runner_environments_owner_reuse", "owner_instance_id", "reuse_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_templates.id", ondelete="SET NULL")
    )
    template_revision: Mapped[int | None] = mapped_column(Integer)
    owner_instance_id: Mapped[uuid.UUID | None] = _instance()
    purpose: Mapped[str] = mapped_column(Text)
    lifetime: Mapped[str] = mapped_column(Text)
    reuse_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default=text("'creating'"))
    state_reason: Mapped[str | None] = mapped_column(Text)
    # 配置记录：建这个环境时的完整规格（镜像与 digest、运行时、用户、网络、资源、密钥名称……）
    spec: Mapped[dict[str, Any]] = _json()
    setup: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    share: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.projects.id", ondelete="SET NULL")
    )
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    container_name: Mapped[str] = mapped_column(Text)
    volume_name: Mapped[str] = mapped_column(Text)
    network_name: Mapped[str] = mapped_column(Text)
    snapshot_key: Mapped[str | None] = mapped_column(Text)
    snapshot_bytes: Mapped[int | None] = mapped_column(BigInteger)
    snapshot_at: Mapped[datetime | None] = _when()
    start_seconds: Mapped[float | None] = mapped_column(Float)
    rebuild_seconds: Mapped[float | None] = mapped_column(Float)
    use_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    mem_bytes: Mapped[int | None] = mapped_column(BigInteger)
    disk_bytes: Mapped[int | None] = mapped_column(BigInteger)
    egress: Mapped[dict[str, Any]] = _json()
    last_used_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = _when()
    stopped_at: Mapped[datetime | None] = _when()
    archived_at: Mapped[datetime | None] = _when()
    deleted_at: Mapped[datetime | None] = _when()
    delete_after: Mapped[datetime | None] = _when()
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class RunnerDeleteCondition(Base):
    __tablename__ = "runner_delete_conditions"
    __table_args__ = (
        CheckConstraint(f"kind IN {CONDITION_KINDS}", name="kind"),
        Index(
            "ix_runner_delete_conditions_open",
            "kind",
            "ref_id",
            postgresql_where=text("fired_at IS NULL"),
        ),
        Index("ix_runner_delete_conditions_environment", "environment_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.runner_environments.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    at: Mapped[datetime | None] = _when()
    seconds: Mapped[int | None] = mapped_column(Integer)
    created_by_instance_id: Mapped[uuid.UUID | None] = _instance()
    fired_at: Mapped[datetime | None] = _when()
    created_at: Mapped[datetime] = _created()


class RunnerRun(Base):
    __tablename__ = "runner_runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="status"),
        Index("ix_runner_runs_environment_created", "environment_id", "created_at"),
        Index("ix_runner_runs_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.runner_environments.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = _instance()
    command: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    output: Mapped[str] = mapped_column(Text, server_default=text("''"))
    output_truncated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 超过同步等待时间的运行：结束后用唤醒叫醒智能体
    wake: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = _when()
    finished_at: Mapped[datetime | None] = _when()


class RunnerCommand(Base):
    __tablename__ = "runner_commands"
    __table_args__ = (
        CheckConstraint(f"status IN {COMMAND_STATUSES}", name="status"),
        Index("ix_runner_commands_host_status", "host_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    host_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.runner_hosts.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = _json()
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    result: Mapped[dict[str, Any]] = _json()
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_environments.id", ondelete="SET NULL")
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_templates.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.runner_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()
    sent_at: Mapped[datetime | None] = _when()
    finished_at: Mapped[datetime | None] = _when()
    deadline_at: Mapped[datetime | None] = _when()


SNAPSHOT_STATUSES = ("uploading", "ready", "failed", "deleted")


class RunnerSnapshot(Base):
    """常驻环境的快照（T-0139，方案第 6.5 节）：要保存的目录打包、分块加密存到对象存储。

    每块用这份快照自己的数据密钥（AES-256-GCM）加密，数据密钥用工作台的凭证密钥封存（`key_ref`）；
    `entries` 是清单（每个目录的块数与字节数）。环境删除后快照保留到 `expires_at`。
    """

    __tablename__ = "runner_snapshots"
    __table_args__ = (
        CheckConstraint(f"status IN {SNAPSHOT_STATUSES}", name="status"),
        Index("ix_runner_snapshots_environment", "environment_id", "created_at"),
        Index("ix_runner_snapshots_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.runner_environments.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(Text, server_default=text("'uploading'"))
    key_ref: Mapped[str] = mapped_column(Text)
    store: Mapped[str] = mapped_column(Text)
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    parts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    ready_at: Mapped[datetime | None] = _when()
    expires_at: Mapped[datetime | None] = _when()
