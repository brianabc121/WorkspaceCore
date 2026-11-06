"""IM 附件：文件与视频，智能体的附件开关（T-0119，docs/v2/AGENT_MESSAGING_PLAN.md 第 4 节）

- im_media 从“只有图片”扩展到图片 / 文件 / 视频：kind、file_name、duration_seconds、sha256，
  智能体上传的记下 instance_id；尺寸约束只对图片要求宽高。
- agent_instances.im_media_enabled（默认关闭，第 11 节 D3）与打开它的人和时间。

downgrade：删除非图片的媒体行后恢复原来的约束并删列（文件与视频是 0035 之后才有的数据）。

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "im_media",
        sa.Column("kind", sa.Text(), server_default=sa.text("'image'"), nullable=False),
        schema="workspace",
    )
    op.add_column("im_media", sa.Column("file_name", sa.Text(), nullable=True), schema="workspace")
    op.add_column(
        "im_media", sa.Column("duration_seconds", sa.Integer(), nullable=True), schema="workspace"
    )
    op.add_column("im_media", sa.Column("sha256", sa.Text(), nullable=True), schema="workspace")
    op.add_column(
        "im_media", sa.Column("instance_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_im_media_instance_id_agent_instances"),
        "im_media",
        "agent_instances",
        ["instance_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.drop_constraint(
        op.f("ck_im_media_dimensions"), "im_media", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        "dimensions",
        "im_media",
        "size > 0 AND width >= 0 AND height >= 0"
        " AND (kind <> 'image' OR (width > 0 AND height > 0))",
        schema="workspace",
    )
    op.create_check_constraint(
        "kind", "im_media", "kind IN ('image', 'file', 'video')", schema="workspace"
    )
    op.add_column(
        "agent_instances",
        sa.Column(
            "im_media_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        schema="workspace",
    )
    op.add_column(
        "agent_instances", sa.Column("im_media_by", sa.UUID(), nullable=True), schema="workspace"
    )
    op.add_column(
        "agent_instances",
        sa.Column("im_media_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_agent_instances_im_media_by_users"),
        "agent_instances",
        "users",
        ["im_media_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_agent_instances_im_media_by_users"),
        "agent_instances",
        schema="workspace",
        type_="foreignkey",
    )
    op.drop_column("agent_instances", "im_media_at", schema="workspace")
    op.drop_column("agent_instances", "im_media_by", schema="workspace")
    op.drop_column("agent_instances", "im_media_enabled", schema="workspace")
    op.execute("DELETE FROM workspace.im_media WHERE kind <> 'image'")
    op.drop_constraint(op.f("ck_im_media_kind"), "im_media", schema="workspace", type_="check")
    op.drop_constraint(
        op.f("ck_im_media_dimensions"), "im_media", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        "dimensions", "im_media", "size > 0 AND width > 0 AND height > 0", schema="workspace"
    )
    op.drop_constraint(
        op.f("fk_im_media_instance_id_agent_instances"),
        "im_media",
        schema="workspace",
        type_="foreignkey",
    )
    for column in ("instance_id", "sha256", "duration_seconds", "file_name", "kind"):
        op.drop_column("im_media", column, schema="workspace")
