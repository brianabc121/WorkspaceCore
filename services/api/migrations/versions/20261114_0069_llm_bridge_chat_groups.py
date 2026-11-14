"""大模型聚合聊天：多模型同问的分组（T-0232，docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）

新增两张表：
- llm_bridge_chat_groups：一组同问（标题、谁建的、幂等号、状态）。
- llm_bridge_chat_group_members：组里的每个目标（哪座会话桥、平台、用法、模型、
  会话编号或没建成的原因）。
消息内容不在这里：每个成员是会话桥上的一个普通会话，内容仍然只在会话桥里。
只新增表，不改已有数据。

downgrade 删掉这两张表（分组关系丢失；会话桥上的会话不受影响）。

Revision ID: 0069
Revises: 0068
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_bridge_chat_groups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_chat_groups_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_llm_bridge_chat_groups_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_chat_groups")),
        sa.UniqueConstraint(
            "workspace_id",
            "created_by",
            "request_id",
            name=op.f("uq_llm_bridge_chat_groups_workspace_id_created_by_request_id"),
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_llm_bridge_chat_groups_listing",
        "llm_bridge_chat_groups",
        ["workspace_id", "status", "updated_at"],
        schema="workspace",
    )
    op.create_table(
        "llm_bridge_chat_group_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("bridge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bridge_name", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["workspace.llm_bridge_chat_groups.id"],
            # 按命名约定生成的名字超过 63 个字符，用模型里给的短名字
            name="fk_llm_bridge_chat_group_members_group",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bridge_id"],
            ["workspace.llm_bridge_bridges.id"],
            name=op.f("fk_llm_bridge_chat_group_members_bridge_id_llm_bridge_bridges"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_chat_group_members")),
        sa.UniqueConstraint(
            "group_id", "position", name=op.f("uq_llm_bridge_chat_group_members_group_id_position")
        ),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("llm_bridge_chat_group_members", schema="workspace")
    op.drop_index(
        "ix_llm_bridge_chat_groups_listing", table_name="llm_bridge_chat_groups", schema="workspace"
    )
    op.drop_table("llm_bridge_chat_groups", schema="workspace")
