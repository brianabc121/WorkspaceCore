"""大模型会话桥模块（T-0228，docs/v2/LLM_BRIDGE_PLAN.md 第 11 节）

两张新表：llm_bridge_bridges（工作空间登记的会话桥，管理令牌加密保存）、
llm_bridge_grants（按实例的“大模型会话权限”）。只新增，不改已有的表。

downgrade 仅在空表时删掉这两张表，拒绝丢弃已有桥数据。

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-13
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.runtime_schema_compat_0068 import refuse_bridge_data_loss

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _flag(name: str, default: str = "false") -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text(default), nullable=False)


def _stamp(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "llm_bridge_bridges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("token_ref", sa.Text(), server_default=sa.text("''"), nullable=False),
        _flag("enabled", "true"),
        _stamp("created_at"),
        _stamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_bridges_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_bridges")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_llm_bridge_bridges_workspace_id_name")
        ),
        schema="workspace",
    )
    op.create_table(
        "llm_bridge_grants",
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _flag("enabled"),
        _flag("can_write"),
        _flag("can_account"),
        _flag("can_takeover"),
        sa.Column(
            "platforms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        _stamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_llm_bridge_grants_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_grants_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_llm_bridge_grants")),
        schema="workspace",
    )


def downgrade() -> None:
    refuse_bridge_data_loss()
    op.drop_table("llm_bridge_grants", schema="workspace")
    op.drop_table("llm_bridge_bridges", schema="workspace")
