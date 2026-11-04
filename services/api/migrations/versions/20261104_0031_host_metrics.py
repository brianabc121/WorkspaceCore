"""服务器状态（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节）

- agent_host_samples：每个实例每分钟一条样本（CPU 使用率、三个负载、核心数、内存与 Swap、
  两个分区、上下行速率），保留 30 天（Worker 清理）；
- agent_host_state：每个实例最近一次上报——算 CPU 使用率与网速用的累计值、CPU 型号、开机时长、
  容器内存上限、当前值（latest），以及正在提醒的告警（alerts：恢复前只提醒一次）。

只存数字，不存进程、文件名、IP。downgrade 删除这两张表（样本是 0031 之后才有的数据）。

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fks(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f(f"fk_{table}_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f(f"fk_{table}_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
    ]


def upgrade() -> None:
    big = sa.BigInteger()
    op.create_table(
        "agent_host_samples",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("load5", sa.Float(), nullable=True),
        sa.Column("load15", sa.Float(), nullable=True),
        sa.Column("cores", sa.Integer(), nullable=True),
        sa.Column("mem_total", big, nullable=True),
        sa.Column("mem_available", big, nullable=True),
        sa.Column("swap_total", big, nullable=True),
        sa.Column("swap_used", big, nullable=True),
        sa.Column("data_total", big, nullable=True),
        sa.Column("data_free", big, nullable=True),
        sa.Column("root_total", big, nullable=True),
        sa.Column("root_free", big, nullable=True),
        sa.Column("rx_rate", big, nullable=True),
        sa.Column("tx_rate", big, nullable=True),
        *_fks("agent_host_samples"),
        sa.PrimaryKeyConstraint("instance_id", "minute", name=op.f("pk_agent_host_samples")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_host_samples_minute", "agent_host_samples", ["minute"], schema="workspace"
    )
    op.create_table(
        "agent_host_state",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_model", sa.Text(), nullable=True),
        sa.Column("total_ticks", big, nullable=True),
        sa.Column("idle_ticks", big, nullable=True),
        sa.Column("rx_bytes", big, nullable=True),
        sa.Column("tx_bytes", big, nullable=True),
        sa.Column("uptime_seconds", big, nullable=True),
        sa.Column("mem_limit", big, nullable=True),
        sa.Column(
            "latest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "alerts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_fks("agent_host_state"),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_agent_host_state")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("agent_host_state", schema="workspace")
    op.drop_index(
        "ix_agent_host_samples_minute", table_name="agent_host_samples", schema="workspace"
    )
    op.drop_table("agent_host_samples", schema="workspace")
