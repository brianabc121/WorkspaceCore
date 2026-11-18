"""开发环境的“云端远程容器”（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 11、21 节）

容器服务器（核心）：

- runner_environments 加归属：owner_kind（instance 默认 / 模块 key，例如 llm_bridge）、
  owner_ref、owner_label，以及 no_auto_archive（不自动归档）。已有的行都是 instance。
- runner_runs 加托管进程：kind（exec 默认 / proc）、owner_kind、owner_ref、acked_offset
  （工作台已经确认收到的输出字节数）。已有的行都是 exec。

大模型客户端模块：

- llm_bridge_remote_containers：MCP 管理器在容器服务器上的远程开发容器（对应一个容器服务器环境）。
- llm_bridge_remote_procs：管理器的远程进程号 ↔ 容器服务器的运行，转发输出、补发结束时用。

不存凭据、提问内容和输出：这些只在内存和 Redis 里转一次。

downgrade 删掉两张新表和新列：已经建好的远程开发容器还在容器服务器上，但变回“没有创建者实例”的环境，
会被回收当成孤儿删除；管理器里的远程环境用不了。

Revision ID: 0079
Revises: 0078
Create Date: 2026-11-18
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTAINERS = "llm_bridge_remote_containers"
PROCS = "llm_bridge_remote_procs"


def _uuid(name: str, *, nullable: bool = True) -> sa.Column[uuid.UUID]:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _id() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _stamp(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _json(name: str, default: str = "'{}'::jsonb") -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(default),
        nullable=False,
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"workspace.{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    # ---- 容器服务器：归属与托管进程 ----
    op.add_column(
        "runner_environments",
        sa.Column("owner_kind", sa.Text(), server_default=sa.text("'instance'"), nullable=False),
        schema="workspace",
    )
    op.add_column("runner_environments", sa.Column("owner_ref", sa.Text()), schema="workspace")
    op.add_column("runner_environments", sa.Column("owner_label", sa.Text()), schema="workspace")
    op.add_column(
        "runner_environments",
        sa.Column("no_auto_archive", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_runner_environments_owner_ref"),
        "runner_environments",
        "owner_kind = 'instance' OR owner_ref IS NOT NULL",
        schema="workspace",
    )
    op.create_index(
        "ix_runner_environments_owner_ref",
        "runner_environments",
        ["owner_kind", "owner_ref"],
        schema="workspace",
        postgresql_where=sa.text("owner_kind <> 'instance'"),
    )
    op.add_column(
        "runner_runs",
        sa.Column("kind", sa.Text(), server_default=sa.text("'exec'"), nullable=False),
        schema="workspace",
    )
    op.add_column("runner_runs", sa.Column("owner_kind", sa.Text()), schema="workspace")
    op.add_column("runner_runs", sa.Column("owner_ref", sa.Text()), schema="workspace")
    op.add_column(
        "runner_runs",
        sa.Column("acked_offset", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_runner_runs_kind"), "runner_runs", "kind IN ('exec', 'proc')", schema="workspace"
    )
    op.create_check_constraint(
        op.f("ck_runner_runs_acked_offset"),
        "runner_runs",
        "acked_offset >= 0",
        schema="workspace",
    )
    # 对账时找某台服务器上还没结束的托管进程
    op.create_index(
        "ix_runner_runs_open_procs",
        "runner_runs",
        ["environment_id"],
        schema="workspace",
        postgresql_where=sa.text("kind = 'proc' AND finished_at IS NULL"),
    )

    # ---- 大模型客户端：远程开发容器 ----
    op.create_table(
        CONTAINERS,
        _id(),
        _uuid("workspace_id", nullable=False),
        _uuid("bridge_id", nullable=False),
        _uuid("runner_environment_id"),
        _uuid("host_id"),
        sa.Column("name", sa.Text(), nullable=False),
        # builtin（内置“大模型开发环境”）/ template（容器服务器上的模板）/ custom（自定义镜像）
        sa.Column("image_kind", sa.Text(), nullable=False),
        sa.Column("image", sa.Text()),
        _uuid("template_id"),
        _json("cli_versions"),
        # 资源、运行时、网络与域名、密钥名称、空闲停止（分钟）
        _json("settings"),
        _uuid("created_by"),
        _stamp("created_at"),
        _stamp("updated_at"),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _fk(CONTAINERS, "workspace_id", "workspaces", "CASCADE"),
        _fk(CONTAINERS, "bridge_id", "llm_bridge_bridges", "CASCADE"),
        _fk(CONTAINERS, "runner_environment_id", "runner_environments", "SET NULL"),
        _fk(CONTAINERS, "host_id", "runner_hosts", "SET NULL"),
        _fk(CONTAINERS, "template_id", "runner_templates", "SET NULL"),
        _fk(CONTAINERS, "created_by", "users", "SET NULL"),
        sa.CheckConstraint(
            "image_kind IN ('builtin', 'template', 'custom')",
            name=op.f(f"ck_{CONTAINERS}_image_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CONTAINERS}")),
        sa.UniqueConstraint("bridge_id", "name", name=op.f(f"uq_{CONTAINERS}_bridge_id_name")),
        sa.UniqueConstraint(
            "runner_environment_id", name=op.f(f"uq_{CONTAINERS}_runner_environment_id")
        ),
        schema="workspace",
    )
    op.create_table(
        PROCS,
        _id(),
        _uuid("workspace_id", nullable=False),
        _uuid("bridge_id", nullable=False),
        _uuid("container_id", nullable=False),
        # 管理器给的进程号（rp_…）
        sa.Column("proc_id", sa.Text(), nullable=False),
        _uuid("run_id"),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text()),
        # 结束已经告诉管理器了
        sa.Column("exit_sent", sa.Boolean(), server_default=sa.false(), nullable=False),
        _stamp("created_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _fk(PROCS, "workspace_id", "workspaces", "CASCADE"),
        _fk(PROCS, "bridge_id", "llm_bridge_bridges", "CASCADE"),
        _fk(PROCS, "container_id", CONTAINERS, "CASCADE"),
        _fk(PROCS, "run_id", "runner_runs", "SET NULL"),
        sa.CheckConstraint(
            "purpose IN ('turn', 'login', 'job', 'prepare')", name=op.f(f"ck_{PROCS}_purpose")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PROCS}")),
        sa.UniqueConstraint("bridge_id", "proc_id", name=op.f(f"uq_{PROCS}_bridge_id_proc_id")),
        schema="workspace",
    )
    op.create_index(f"ix_{PROCS}_run", PROCS, ["run_id"], schema="workspace")
    op.create_index(
        f"ix_{PROCS}_unsent",
        PROCS,
        ["bridge_id"],
        schema="workspace",
        postgresql_where=sa.text("exit_sent = false"),
    )


def downgrade() -> None:
    op.drop_index(f"ix_{PROCS}_unsent", table_name=PROCS, schema="workspace")
    op.drop_index(f"ix_{PROCS}_run", table_name=PROCS, schema="workspace")
    op.drop_table(PROCS, schema="workspace")
    op.drop_table(CONTAINERS, schema="workspace")
    op.drop_index("ix_runner_runs_open_procs", table_name="runner_runs", schema="workspace")
    for name in ("ck_runner_runs_acked_offset", "ck_runner_runs_kind"):
        op.drop_constraint(op.f(name), "runner_runs", schema="workspace", type_="check")
    for column in ("acked_offset", "owner_ref", "owner_kind", "kind"):
        op.drop_column("runner_runs", column, schema="workspace")
    op.drop_index(
        "ix_runner_environments_owner_ref", table_name="runner_environments", schema="workspace"
    )
    op.drop_constraint(
        op.f("ck_runner_environments_owner_ref"),
        "runner_environments",
        schema="workspace",
        type_="check",
    )
    for column in ("no_auto_archive", "owner_label", "owner_ref", "owner_kind"):
        op.drop_column("runner_environments", column, schema="workspace")
