"""工作空间的图片存储配置（T-0223）

知识库图片（与常驻环境快照）的主存储、副本和 R2 / OSS 的连接信息，原来写在 .env 里（FILE_STORAGE、
R2_*、OSS_*），要在服务器上运行部署脚本修改。改成每个工作空间在网页“设置 → 图片存储”里填：
一个工作空间一行，没有这一行就是本机存储、没有副本。密钥用凭证密钥加密（*_secret_ref），其余是明文。

只建新表，不搬 .env 里的旧配置（还没有生产部署，人类 2026-11-12 确认不需要兼容旧版本）。

downgrade 删掉这张表。

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _text(name: str) -> sa.Column[str]:
    return sa.Column(name, sa.Text(), server_default=sa.text("''"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "storage_settings",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_store", sa.Text(), server_default=sa.text("'local'"), nullable=False),
        sa.Column("replica_store", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        _text("r2_account_id"),
        _text("r2_bucket"),
        _text("r2_jurisdiction"),
        _text("r2_access_key_id"),
        _text("r2_secret_ref"),
        _text("oss_region"),
        _text("oss_bucket"),
        _text("oss_access_key_id"),
        _text("oss_secret_ref"),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "primary_store IN ('local', 'r2', 'oss')",
            name=op.f("ck_storage_settings_primary_store"),
        ),
        sa.CheckConstraint(
            "replica_store IN ('none', 'local', 'r2', 'oss')",
            name=op.f("ck_storage_settings_replica_store"),
        ),
        sa.CheckConstraint(
            "replica_store <> primary_store", name=op.f("ck_storage_settings_replica_differs")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["workspace.users.id"],
            name=op.f("fk_storage_settings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_storage_settings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_storage_settings")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("storage_settings", schema="workspace")
