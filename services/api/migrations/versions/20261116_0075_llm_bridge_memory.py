"""大模型客户端：会话纪要与知识库外发开关（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 5、6 节）

- 新增 llm_bridge_digests：每个大模型会话一篇“会话纪要”笔记，记着写在哪个知识库、写到第几个回合、
  状态（照常更新 / 关掉 / 已转成正式笔记 / 人改过 / 已删除 / 随保留期清掉）。
- llm_bridge_bridges.digest_settings：管理器设置里纪要的开关，同步会话索引时带回来。
- llm_bridge_sessions 加 compactions（平台压缩过几次）、purged_at（对话内容按保留期清掉的时间）。
- knowledge_notes.type 增加 digest（会话纪要）。
- knowledge_spaces.external_llm_allowed：这个知识库的笔记能不能附在发给外部大模型平台的消息里，
  默认允许（人类 2026-11-16 决定）。

downgrade 删掉纪要表、两列，把 type=digest 的笔记改成 record（保留内容，不删笔记）。

Revision ID: 0075
Revises: 0074
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workspace"
TABLE = "llm_bridge_digests"
_TYPES = (
    "'overview', 'brief', 'decision', 'rule', 'process', 'runbook', 'lesson', 'review', "
    "'preference', 'glossary', 'record', 'plan', 'risk', 'design', 'template', 'correction', "
    "'readme'"
)


def _note_types(values: str) -> None:
    op.drop_constraint(
        op.f("ck_knowledge_notes_type"), "knowledge_notes", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        op.f("ck_knowledge_notes_type"), "knowledge_notes", f"type IN ({values})", schema=SCHEMA
    )


def upgrade() -> None:
    _note_types(f"{_TYPES}, 'digest'")
    op.add_column(
        "knowledge_spaces",
        sa.Column(
            "external_llm_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "llm_bridge_bridges",
        sa.Column(
            "digest_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "llm_bridge_sessions",
        sa.Column("compactions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "llm_bridge_sessions",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("turns_written", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("compactions_seen", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('active', 'off', 'converted', 'edited', 'deleted', 'purged')",
            name=op.f("ck_llm_bridge_digests_state"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_digests_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bridge_id"],
            ["workspace.llm_bridge_bridges.id"],
            name=op.f("fk_llm_bridge_digests_bridge_id_llm_bridge_bridges"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["workspace.knowledge_notes.id"],
            name=op.f("fk_llm_bridge_digests_note_id_knowledge_notes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["workspace.knowledge_spaces.id"],
            name=op.f("fk_llm_bridge_digests_space_id_knowledge_spaces"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_digests")),
        sa.UniqueConstraint(
            "bridge_id", "session_id", name=op.f("uq_llm_bridge_digests_bridge_id_session_id")
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_llm_bridge_digests_note", TABLE, ["note_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_llm_bridge_digests_note", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
    op.drop_column("llm_bridge_sessions", "purged_at", schema=SCHEMA)
    op.drop_column("llm_bridge_sessions", "compactions", schema=SCHEMA)
    op.drop_column("llm_bridge_bridges", "digest_settings", schema=SCHEMA)
    op.drop_column("knowledge_spaces", "external_llm_allowed", schema=SCHEMA)
    # 纪要笔记保留下来，改成普通记录（类型约束里没有 digest 了）
    op.execute("UPDATE workspace.knowledge_notes SET type = 'record' WHERE type = 'digest'")
    _note_types(_TYPES)
