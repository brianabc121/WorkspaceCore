"""大模型客户端：回合结束后叫醒发起的智能体（T-0250，docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 4.1 的 A1）

- 新增 llm_bridge_turn_watches：智能体发送时选了“结束后叫我”的回合。后台任务隔十几秒问一次管理器，
  回合结束（完成、失败、结果未知、取消，或者被打断后没有回合接着做）就给这个实例排一次唤醒，
  这一行记上 done_at。被打断后有回合接着做的，改成盯接着做的那一回合。
- agent_wakeups.source 增加 llm_bridge。

downgrade 删掉这张表、删掉 source = 'llm_bridge' 的唤醒：没叫醒的智能体要自己用工具查结果。

Revision ID: 0073
Revises: 0072
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workspace"
TABLE = "llm_bridge_turn_watches"


def _wakeup_sources(values: str) -> None:
    op.drop_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", f"source IN ({values})", schema=SCHEMA
    )


def upgrade() -> None:
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
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("first_turn_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_turn_watches_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bridge_id"],
            ["workspace.llm_bridge_bridges.id"],
            name=op.f("fk_llm_bridge_turn_watches_bridge_id_llm_bridge_bridges"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_llm_bridge_turn_watches_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_turn_watches")),
        sa.UniqueConstraint(
            "bridge_id",
            "first_turn_id",
            name=op.f("uq_llm_bridge_turn_watches_bridge_id_first_turn_id"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_bridge_turn_watches_open",
        TABLE,
        ["bridge_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("done_at IS NULL"),
    )
    _wakeup_sources("'agent', 'admin', 'issue', 'runner', 'news', 'llm_bridge'")


def downgrade() -> None:
    op.execute("DELETE FROM workspace.agent_wakeups WHERE source = 'llm_bridge'")
    _wakeup_sources("'agent', 'admin', 'issue', 'runner', 'news'")
    op.drop_index("ix_llm_bridge_turn_watches_open", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
