"""运行服务器：常驻环境的快照（T-0139，docs/v2/RUNNER_PLAN.md 第 6.5 节）

- runner_snapshots：要保存的目录分块加密后存在对象存储里（key_ref 是封存的数据密钥，
  entries 是每个目录的块数与字节数）；环境删除后保留到 expires_at。

全部新增，downgrade 删掉这张表；对象存储里已有的快照块不会自动删除
（都在前缀 runner/<工作空间 ID>/ 下，需要时按前缀手动清理）。

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_snapshots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'uploading'"), nullable=False),
        sa.Column("key_ref", sa.Text(), nullable=False),
        sa.Column("store", sa.Text(), nullable=False),
        sa.Column(
            "entries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("parts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('uploading', 'ready', 'failed', 'deleted')",
            name=op.f("ck_runner_snapshots_status"),
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["workspace.runner_environments.id"],
            name=op.f("fk_runner_snapshots_environment_id_runner_environments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_snapshots_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_snapshots")),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_snapshots_environment",
        "runner_snapshots",
        ["environment_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_runner_snapshots_expires",
        "runner_snapshots",
        ["expires_at"],
        unique=False,
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index("ix_runner_snapshots_expires", table_name="runner_snapshots", schema="workspace")
    op.drop_index(
        "ix_runner_snapshots_environment", table_name="runner_snapshots", schema="workspace"
    )
    op.drop_table("runner_snapshots", schema="workspace")
