"""大模型客户端：工作台里的会话索引（T-0248，docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 3.4）

新增 llm_bridge_sessions：每个 MCP 管理器上有哪些会话——谁建的、出处、标题、状态、回合数、
失败和中断的数量、累计 token、最后活动时间。不含任何提问和回答，对话内容仍然只在管理器里。
由后台任务按管理器给的“变更序号”增量同步；llm_bridge_bridges 加一列 index_rev 记同步到哪了。

有了它才能跨管理器列出和筛选会话、按人和按智能体汇总用量。

downgrade 删掉这张表和这一列：管理器里的会话不受影响，重新升级后会从头同步一遍。

Revision ID: 0072
Revises: 0071
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workspace"
TABLE = "llm_bridge_sessions"


def upgrade() -> None:
    op.add_column(
        "llm_bridge_bridges",
        sa.Column("index_rev", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bridge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("owner_label", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("busy", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("turns_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turns_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turns_interrupted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "owner_kind IN ('agent', 'user', 'external')",
            name=op.f("ck_llm_bridge_sessions_owner_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_sessions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bridge_id"],
            ["workspace.llm_bridge_bridges.id"],
            name=op.f("fk_llm_bridge_sessions_bridge_id_llm_bridge_bridges"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_sessions")),
        sa.UniqueConstraint(
            "bridge_id", "session_id", name=op.f("uq_llm_bridge_sessions_bridge_id_session_id")
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_bridge_sessions_recent", TABLE, ["workspace_id", "updated_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_llm_bridge_sessions_owner", TABLE, ["workspace_id", "owner_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_llm_bridge_sessions_owner", table_name=TABLE, schema=SCHEMA)
    op.drop_index("ix_llm_bridge_sessions_recent", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
    op.drop_column("llm_bridge_bridges", "index_rev", schema=SCHEMA)
