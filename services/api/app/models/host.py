"""服务器状态（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节，迁移 0031）。

- AgentHostSample：每个实例每分钟一条，保留 30 天；CPU 使用率与网速由两次上报的累计值之差算出，
  计数器变小（重启或回绕）时这一分钟记为空。
- AgentHostState：最近一次上报的累计值、当前值（latest）与正在提醒的告警
  （alerts，恢复前只提醒一次）。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _instance() -> Mapped[uuid.UUID]:
    return mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )


def _workspace() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspace.workspaces.id", ondelete="CASCADE"))


class AgentHostSample(Base):
    __tablename__ = "agent_host_samples"
    __table_args__ = (Index("ix_agent_host_samples_minute", "minute"),)

    instance_id: Mapped[uuid.UUID] = _instance()
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
    rx_rate: Mapped[int | None] = mapped_column(BigInteger)
    tx_rate: Mapped[int | None] = mapped_column(BigInteger)


class AgentHostState(Base):
    __tablename__ = "agent_host_state"

    instance_id: Mapped[uuid.UUID] = _instance()
    workspace_id: Mapped[uuid.UUID] = _workspace()
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cpu_model: Mapped[str | None] = mapped_column(Text)
    total_ticks: Mapped[int | None] = mapped_column(BigInteger)
    idle_ticks: Mapped[int | None] = mapped_column(BigInteger)
    rx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    mem_limit: Mapped[int | None] = mapped_column(BigInteger)
    latest: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    alerts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
