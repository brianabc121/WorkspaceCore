"""一台服务器多个 OpenClaw：服务器与两级负载样本（T-0144 / T-0146）

设计见 docs/v2/MULTI_OPENCLAW_PLAN.md 第 6 节。

- agent_servers（新）：一台 OpenClaw 服务器。kind=auto 按上报的机器指纹（machine_key）自动识别，
  kind=ssh 由管理员添加（T-0145）；latest / alerts 是整机的当前值与告警；
- agent_server_samples（新）：每台服务器每分钟一条整机样本（不含网速：每个实例有自己的网卡，
  整机网速是同一分钟所有实例网速之和，查询时从 agent_instance_samples 算），保留 30 天；
- agent_instance_samples（新）：每个实例每分钟一条自己容器的样本
  （CPU、内存、进程数、数据目录、网速），保留 30 天；
- agent_host_state 加列：容器 CPU 用量累计、机器指纹、容器当前值；
- agent_instances 加列：所在服务器（删服务器时置空）、是否手动指定、内存与 CPU 上限；
- agent_host_samples 删除（人类 2026-11-07 确认还没有部署过，D6：不迁移旧数据）。

downgrade 回到 0044 的结构，同样不保留数据（服务器上的实例不受影响，下一次上报会重新写入）。

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB(astext_type=sa.Text())
_EMPTY = sa.text("'{}'::jsonb")


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.workspaces.id"],
        name=op.f(f"fk_{table}_workspace_id_workspaces"),
        ondelete="CASCADE",
    )


def _host_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    big = sa.BigInteger()
    return [
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
    ]


def upgrade() -> None:
    big = sa.BigInteger()
    op.drop_index(
        "ix_agent_host_samples_minute", table_name="agent_host_samples", schema="workspace"
    )
    op.drop_table("agent_host_samples", schema="workspace")

    op.create_table(
        "agent_servers",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default=sa.text("'auto'"), nullable=False),
        sa.Column("machine_key", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cpu_model", sa.Text(), nullable=True),
        sa.Column("uptime_seconds", big, nullable=True),
        sa.Column("latest", _JSON, server_default=_EMPTY, nullable=False),
        sa.Column("alerts", _JSON, server_default=_EMPTY, nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('auto', 'ssh')", name=op.f("ck_agent_servers_kind")),
        _workspace_fk("agent_servers"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_servers")),
        schema="workspace",
    )
    op.create_index(
        "uq_agent_servers_machine",
        "agent_servers",
        ["workspace_id", "machine_key"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("machine_key IS NOT NULL"),
    )

    op.create_table(
        "agent_server_samples",
        sa.Column("server_id", sa.UUID(), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_host_columns(),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["workspace.agent_servers.id"],
            name=op.f("fk_agent_server_samples_server_id_agent_servers"),
            ondelete="CASCADE",
        ),
        _workspace_fk("agent_server_samples"),
        sa.PrimaryKeyConstraint("server_id", "minute", name=op.f("pk_agent_server_samples")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_server_samples_minute", "agent_server_samples", ["minute"], schema="workspace"
    )

    op.create_table(
        "agent_instance_samples",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("server_id", sa.UUID(), nullable=True),
        sa.Column("cpu_cores", sa.Float(), nullable=True),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("mem_bytes", big, nullable=True),
        sa.Column("mem_limit", big, nullable=True),
        sa.Column("pids", sa.Integer(), nullable=True),
        sa.Column("data_bytes", big, nullable=True),
        sa.Column("rx_rate", big, nullable=True),
        sa.Column("tx_rate", big, nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_instance_samples_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["workspace.agent_servers.id"],
            name=op.f("fk_agent_instance_samples_server_id_agent_servers"),
            ondelete="SET NULL",
        ),
        _workspace_fk("agent_instance_samples"),
        sa.PrimaryKeyConstraint("instance_id", "minute", name=op.f("pk_agent_instance_samples")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_instance_samples_minute", "agent_instance_samples", ["minute"], schema="workspace"
    )
    op.create_index(
        "ix_agent_instance_samples_server_minute",
        "agent_instance_samples",
        ["server_id", "minute"],
        schema="workspace",
    )

    op.add_column("agent_host_state", sa.Column("cpu_usec", big, nullable=True), schema="workspace")
    op.add_column(
        "agent_host_state", sa.Column("machine_key", sa.Text(), nullable=True), schema="workspace"
    )
    op.add_column(
        "agent_host_state",
        sa.Column("container", _JSON, server_default=_EMPTY, nullable=False),
        schema="workspace",
    )

    op.add_column(
        "agent_instances", sa.Column("server_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.add_column(
        "agent_instances",
        sa.Column("server_manual", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "agent_instances",
        sa.Column("mem_limit_mb", sa.Integer(), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "agent_instances", sa.Column("cpus", sa.Float(), nullable=True), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_agent_instances_server_id_agent_servers"),
        "agent_instances",
        "agent_servers",
        ["server_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_agent_instances_mem_limit"),
        "agent_instances",
        "mem_limit_mb IS NULL OR mem_limit_mb BETWEEN 256 AND 1048576",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_agent_instances_cpus"),
        "agent_instances",
        "cpus IS NULL OR (cpus >= 0.1 AND cpus <= 256)",
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_instances_cpus"), "agent_instances", type_="check", schema="workspace"
    )
    op.drop_constraint(
        op.f("ck_agent_instances_mem_limit"), "agent_instances", type_="check", schema="workspace"
    )
    op.drop_constraint(
        op.f("fk_agent_instances_server_id_agent_servers"),
        "agent_instances",
        type_="foreignkey",
        schema="workspace",
    )
    for column in ("cpus", "mem_limit_mb", "server_manual", "server_id"):
        op.drop_column("agent_instances", column, schema="workspace")
    for column in ("container", "machine_key", "cpu_usec"):
        op.drop_column("agent_host_state", column, schema="workspace")

    op.drop_index(
        "ix_agent_instance_samples_server_minute",
        table_name="agent_instance_samples",
        schema="workspace",
    )
    op.drop_index(
        "ix_agent_instance_samples_minute", table_name="agent_instance_samples", schema="workspace"
    )
    op.drop_table("agent_instance_samples", schema="workspace")
    op.drop_index(
        "ix_agent_server_samples_minute", table_name="agent_server_samples", schema="workspace"
    )
    op.drop_table("agent_server_samples", schema="workspace")
    op.drop_index("uq_agent_servers_machine", table_name="agent_servers", schema="workspace")
    op.drop_table("agent_servers", schema="workspace")

    big = sa.BigInteger()
    op.create_table(
        "agent_host_samples",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_host_columns(),
        sa.Column("rx_rate", big, nullable=True),
        sa.Column("tx_rate", big, nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_host_samples_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        _workspace_fk("agent_host_samples"),
        sa.PrimaryKeyConstraint("instance_id", "minute", name=op.f("pk_agent_host_samples")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_host_samples_minute", "agent_host_samples", ["minute"], schema="workspace"
    )
