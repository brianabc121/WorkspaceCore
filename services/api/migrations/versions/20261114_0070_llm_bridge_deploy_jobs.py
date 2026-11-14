"""大模型客户端：页面上一键部署 MCP 管理器的任务（T-0233，docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）

新增一张表 llm_bridge_deploy_jobs：部署、重新部署、卸载各一条记录——部署到哪台智能体服务器、
这次安装的编号、网络怎么接、步骤和脱敏后的日志。管理令牌不在这张表里（登记成功后加密保存在
llm_bridge_bridges.token_ref）。只新增表，不改已有数据。

downgrade 删掉这张表（部署记录丢失：已经部署的管理器还在运行、还登记着，只是页面上不再认得
它是“工作台部署的”，不能再从页面上重新部署和卸载）。

Revision ID: 0070
Revises: 0069
Create Date: 2026-11-14
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "llm_bridge_deploy_jobs"


def _uuid(name: str, *, nullable: bool = True) -> sa.Column[uuid.UUID]:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _stamp(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        _uuid("workspace_id", nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        _uuid("server_id"),
        sa.Column("server_name", sa.Text(), nullable=False),
        _uuid("bridge_id"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "platforms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("install_dir", sa.Text(), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        _uuid("requested_by"),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("log", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _stamp("started_at"),
        _stamp("updated_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_deploy_jobs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["workspace.agent_servers.id"],
            name=op.f("fk_llm_bridge_deploy_jobs_server_id_agent_servers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["bridge_id"],
            ["workspace.llm_bridge_bridges.id"],
            name=op.f("fk_llm_bridge_deploy_jobs_bridge_id_llm_bridge_bridges"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["workspace.users.id"],
            name=op.f("fk_llm_bridge_deploy_jobs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_deploy_jobs")),
        sa.UniqueConstraint(
            "workspace_id",
            "request_id",
            name=op.f("uq_llm_bridge_deploy_jobs_workspace_id_request_id"),
        ),
        schema="workspace",
    )
    op.create_index(
        "uq_llm_bridge_deploy_jobs_running",
        TABLE,
        ["server_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_llm_bridge_deploy_jobs_listing",
        TABLE,
        ["workspace_id", "started_at"],
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index("ix_llm_bridge_deploy_jobs_listing", table_name=TABLE, schema="workspace")
    op.drop_index("uq_llm_bridge_deploy_jobs_running", table_name=TABLE, schema="workspace")
    op.drop_table(TABLE, schema="workspace")
