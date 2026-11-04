"""知识库图片（T-0103，docs/v2/TIMELINE_PLAN.md 第 4 节）

- knowledge_files：上传的图片（同一工作空间同一内容一行，对象 key 是 kb/<workspace_id>/<sha256>）、
  写入的主存储、副本复制状态；
- knowledge_file_refs：笔记引用了哪些图片（历史版本里引用过的也保留），
  没有引用的由 Worker 每天清理。

downgrade 删除两张表；存储里的图片文件不动（运维自行清理卷或桶里的 kb/ 前缀）。

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_files",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("storage", sa.Text(), nullable=False),
        sa.Column("replica", sa.Text(), nullable=True),
        sa.Column("replica_state", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column("replica_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("replica_error", sa.Text(), nullable=True),
        sa.Column("created_by_user", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "replica_state IN ('none', 'pending', 'done', 'failed')",
            name=op.f("ck_knowledge_files_replica_state"),
        ),
        sa.CheckConstraint(
            "storage IN ('local', 'r2', 'oss')", name=op.f("ck_knowledge_files_storage")
        ),
        sa.CheckConstraint("size > 0", name=op.f("ck_knowledge_files_size")),
        sa.ForeignKeyConstraint(
            ["created_by_user"],
            ["workspace.users.id"],
            name=op.f("fk_knowledge_files_created_by_user_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_files_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_files")),
        sa.UniqueConstraint(
            "workspace_id", "sha256", name=op.f("uq_knowledge_files_workspace_id_sha256")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_files_replica_pending",
        "knowledge_files",
        ["created_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("replica_state = 'pending'"),
    )
    op.create_table(
        "knowledge_file_refs",
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("note_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["workspace.knowledge_files.id"],
            name=op.f("fk_knowledge_file_refs_file_id_knowledge_files"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["workspace.knowledge_notes.id"],
            name=op.f("fk_knowledge_file_refs_note_id_knowledge_notes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_file_refs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("file_id", "note_id", name=op.f("pk_knowledge_file_refs")),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_file_refs_note",
        "knowledge_file_refs",
        ["note_id"],
        unique=False,
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_file_refs_note", table_name="knowledge_file_refs", schema="workspace"
    )
    op.drop_table("knowledge_file_refs", schema="workspace")
    op.drop_index(
        "ix_knowledge_files_replica_pending", table_name="knowledge_files", schema="workspace"
    )
    op.drop_table("knowledge_files", schema="workspace")
