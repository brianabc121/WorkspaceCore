"""服务器与负载（T-0113；多实例 T-0144 / T-0146，迁移 0045）。

设计见 docs/v2/MULTI_OPENCLAW_PLAN.md 第 5、6 节。

- AgentServer：一台 OpenClaw 服务器。上面可以有多个实例（每个实例一个 Compose 项目）。
  kind=auto：由上报的机器指纹（boot_id 的 HMAC，machine_key）自动识别；
  kind=ssh：管理员添加（T-0145）。
  latest / reported_at 是整机的最近一次数值；alerts 是整机正在提醒的告警。
- AgentServerSample：每台服务器每分钟一条整机样本（多个实例都会报，先到的写入），保留 30 天。
- AgentInstanceSample：每个实例每分钟一条自己容器的样本（cgroup），保留 30 天。
- AgentHostState：每个实例上一次的累计值（整机 CPU 时钟、容器 CPU 用量、网卡字节），
  自己容器的当前值（container）与实例级告警（alerts）。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SERVER_KINDS = ("auto", "ssh")


def _workspace() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspace.workspaces.id", ondelete="CASCADE"))


class AgentServer(Base):
    __tablename__ = "agent_servers"
    __table_args__ = (
        CheckConstraint(f"kind IN {SERVER_KINDS}", name="kind"),
        # 同一个工作空间里同一台机器（同一个 machine_key）只有一条
        Index(
            "uq_agent_servers_machine",
            "workspace_id",
            "machine_key",
            unique=True,
            postgresql_where=text("machine_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = _workspace()
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default=text("'auto'"))
    machine_key: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cpu_model: Mapped[str | None] = mapped_column(Text)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    latest: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    resource_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    alerts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentServerSample(Base):
    __tablename__ = "agent_server_samples"
    __table_args__ = (Index("ix_agent_server_samples_minute", "minute"),)

    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="CASCADE"), primary_key=True
    )
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _workspace()
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    load1: Mapped[float | None] = mapped_column(Float)
    load5: Mapped[float | None] = mapped_column(Float)
    load15: Mapped[float | None] = mapped_column(Float)
    cores: Mapped[int | None] = mapped_column(Integer)
    mem_total: Mapped[int | None] = mapped_column(BigInteger)
    mem_available: Mapped[int | None] = mapped_column(BigInteger)
    swap_total: Mapped[int | None] = mapped_column(BigInteger)
    swap_used: Mapped[int | None] = mapped_column(BigInteger)
    data_total: Mapped[int | None] = mapped_column(BigInteger)
    data_free: Mapped[int | None] = mapped_column(BigInteger)
    root_total: Mapped[int | None] = mapped_column(BigInteger)
    root_free: Mapped[int | None] = mapped_column(BigInteger)


class AgentInstanceSample(Base):
    __tablename__ = "agent_instance_samples"
    __table_args__ = (
        Index("ix_agent_instance_samples_minute", "minute"),
        Index("ix_agent_instance_samples_server_minute", "server_id", "minute"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _workspace()
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_servers.id", ondelete="SET NULL")
    )
    cpu_cores: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    mem_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mem_limit: Mapped[int | None] = mapped_column(BigInteger)
    pids: Mapped[int | None] = mapped_column(Integer)
    data_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rx_rate: Mapped[int | None] = mapped_column(BigInteger)
    tx_rate: Mapped[int | None] = mapped_column(BigInteger)


class AgentHostState(Base):
    __tablename__ = "agent_host_state"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[uuid.UUID] = _workspace()
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cpu_model: Mapped[str | None] = mapped_column(Text)
    total_ticks: Mapped[int | None] = mapped_column(BigInteger)
    idle_ticks: Mapped[int | None] = mapped_column(BigInteger)
    rx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    mem_limit: Mapped[int | None] = mapped_column(BigInteger)
    # 多实例（T-0146）：自己容器的 CPU 用量累计（微秒）、看到的机器指纹、自己容器的当前值
    cpu_usec: Mapped[int | None] = mapped_column(BigInteger)
    machine_key: Mapped[str | None] = mapped_column(Text)
    container: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    latest: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    alerts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
