"""大模型客户端：一键部署 MCP 管理器（T-0234，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 5 节）

新增两张表，不改已有数据：

- llm_bridge_installs：工作台部署出去的 MCP 管理器——部署在哪台智能体服务器或容器服务器上、
  网络怎么接、专用转发密钥的加密引用，以及部署时那台服务器的地址和主机公钥。
- llm_bridge_deploy_jobs：部署、重新部署、重启、卸载任务，带步骤和脱敏后的日志。

管理令牌、服务器密码、sudo 密码都不在这两张表里。

downgrade 删掉这两张表：已经部署的管理器还在运行、还登记着，但工作台不再认得它是“工作台
部署的”，经 SSH 通道访问的管理器会连不上（转发密钥的记录没有了），要重新部署或手动处理。

Revision ID: 0070
Revises: 0069
Create Date: 2026-11-14
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INSTALLS = "llm_bridge_installs"
JOBS = "llm_bridge_deploy_jobs"


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


def _json_list(name: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
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
    op.create_table(
        INSTALLS,
        _id(),
        _uuid("workspace_id", nullable=False),
        _uuid("bridge_id", nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        _uuid("server_id"),
        _uuid("runner_host_id"),
        sa.Column("server_name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("host_key", sa.Text(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("install_dir", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("tunnel_key_ref", sa.Text(), nullable=True),
        sa.Column("tunnel_public_key", sa.Text(), nullable=True),
        _json_list("platforms"),
        sa.Column("source_tag", sa.Text(), nullable=True),
        sa.Column("needs_sudo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _uuid("deployed_by"),
        _stamp("deployed_at"),
        _stamp("updated_at"),
        _fk(INSTALLS, "workspace_id", "workspaces", "CASCADE"),
        _fk(INSTALLS, "bridge_id", "llm_bridge_bridges", "CASCADE"),
        _fk(INSTALLS, "server_id", "agent_servers", "SET NULL"),
        _fk(INSTALLS, "runner_host_id", "runner_hosts", "SET NULL"),
        _fk(INSTALLS, "deployed_by", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_installs")),
        sa.UniqueConstraint("bridge_id", name=op.f("uq_llm_bridge_installs_bridge_id")),
        schema="workspace",
    )
    op.create_index(
        "ix_llm_bridge_installs_server", INSTALLS, ["workspace_id", "server_id"], schema="workspace"
    )
    op.create_index(
        "ix_llm_bridge_installs_runner_host",
        INSTALLS,
        ["workspace_id", "runner_host_id"],
        schema="workspace",
    )

    op.create_table(
        JOBS,
        _id(),
        _uuid("workspace_id", nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        _uuid("server_id"),
        _uuid("runner_host_id"),
        sa.Column("server_name", sa.Text(), nullable=False),
        _uuid("bridge_id"),
        sa.Column("name", sa.Text(), nullable=False),
        _json_list("platforms"),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("install_dir", sa.Text(), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        _uuid("requested_by"),
        _json_list("steps"),
        sa.Column("log", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _stamp("started_at"),
        _stamp("updated_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _fk(JOBS, "workspace_id", "workspaces", "CASCADE"),
        _fk(JOBS, "server_id", "agent_servers", "SET NULL"),
        _fk(JOBS, "runner_host_id", "runner_hosts", "SET NULL"),
        _fk(JOBS, "bridge_id", "llm_bridge_bridges", "SET NULL"),
        _fk(JOBS, "requested_by", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_deploy_jobs")),
        sa.UniqueConstraint(
            "workspace_id",
            "request_id",
            name=op.f("uq_llm_bridge_deploy_jobs_workspace_id_request_id"),
        ),
        schema="workspace",
    )
    for name, column in (
        ("uq_llm_bridge_deploy_jobs_running", "server_id"),
        ("uq_llm_bridge_deploy_jobs_running_runner", "runner_host_id"),
    ):
        op.create_index(
            name,
            JOBS,
            [column],
            unique=True,
            schema="workspace",
            postgresql_where=sa.text("status = 'running'"),
        )
    op.create_index(
        "ix_llm_bridge_deploy_jobs_listing",
        JOBS,
        ["workspace_id", "started_at"],
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index("ix_llm_bridge_deploy_jobs_listing", table_name=JOBS, schema="workspace")
    op.drop_index("uq_llm_bridge_deploy_jobs_running_runner", table_name=JOBS, schema="workspace")
    op.drop_index("uq_llm_bridge_deploy_jobs_running", table_name=JOBS, schema="workspace")
    op.drop_table(JOBS, schema="workspace")
    op.drop_index("ix_llm_bridge_installs_runner_host", table_name=INSTALLS, schema="workspace")
    op.drop_index("ix_llm_bridge_installs_server", table_name=INSTALLS, schema="workspace")
    op.drop_table(INSTALLS, schema="workspace")
