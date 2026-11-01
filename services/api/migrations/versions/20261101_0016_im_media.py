"""IM：上传的图片（V2 OC-3，S6-02）

契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5.4 节。im_media 记录管理员上传到 OpenIM
对象存储的图片；id 同时是 upload_id 与 media_id。没有用于发消息的上传到期后删除记录。
只新增表，downgrade 删除即可回退。

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_media",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("object_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'uploaded'"), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("server_msg_id", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('uploaded', 'used')", name=op.f("ck_im_media_status")),
        sa.CheckConstraint(
            "size > 0 AND width > 0 AND height > 0", name=op.f("ck_im_media_dimensions")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["workspace.users.id"],
            name=op.f("fk_im_media_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_im_media_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_media")),
        sa.UniqueConstraint("object_name", name=op.f("uq_im_media_object_name")),
        schema="workspace",
    )
    op.create_index(
        "ix_im_media_unused_expiry",
        "im_media",
        ["expires_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("status = 'uploaded'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_im_media_unused_expiry",
        table_name="im_media",
        schema="workspace",
        postgresql_where=sa.text("status = 'uploaded'"),
    )
    op.drop_table("im_media", schema="workspace")
