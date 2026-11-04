"""项目群（T-0110，docs/v2/AGENT_OPS_PLAN.md 第 2.2 节）

- im_groups.project_id：项目群属于哪个项目；每个项目最多一个没有解散的群（部分唯一索引）。
  项目删除时外键置空（删除前先解散群，解散失败时群留在 IM 里，由群主处理）。
- projects.im_group_status：none 没有项目群 / pending 要建群或同步成员（Worker 每分钟重试）/
  ready 已建好且与项目成员一致；
- projects.im_group_owner_id：项目群的群主（新建项目的人，或点“建项目群”的人）；
- projects.im_group_error：上一次建群或同步失败的原因（错误类别，不含第三方返回的原文）。

已有项目都是 none（没有项目群），可以在项目页补建。downgrade 删除这些列与索引，
已经建好的群保留为普通群。

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("im_group_status", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "projects", sa.Column("im_group_owner_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.add_column(
        "projects", sa.Column("im_group_error", sa.Text(), nullable=True), schema="workspace"
    )
    op.create_check_constraint(
        op.f("ck_projects_im_group_status"),
        "projects",
        "im_group_status IN ('none', 'pending', 'ready')",
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_projects_im_group_owner_id_users"),
        "projects",
        "users",
        ["im_group_owner_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_projects_im_group_pending",
        "projects",
        ["im_group_status"],
        schema="workspace",
        postgresql_where=sa.text("im_group_status = 'pending'"),
    )
    op.add_column(
        "im_groups", sa.Column("project_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_im_groups_project_id_projects"),
        "im_groups",
        "projects",
        ["project_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_im_groups_project",
        "im_groups",
        ["project_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("project_id IS NOT NULL AND dismissed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_im_groups_project", table_name="im_groups", schema="workspace")
    op.drop_constraint(
        op.f("fk_im_groups_project_id_projects"),
        "im_groups",
        type_="foreignkey",
        schema="workspace",
    )
    op.drop_column("im_groups", "project_id", schema="workspace")
    op.drop_index("ix_projects_im_group_pending", table_name="projects", schema="workspace")
    op.drop_constraint(
        op.f("fk_projects_im_group_owner_id_users"),
        "projects",
        type_="foreignkey",
        schema="workspace",
    )
    op.drop_constraint(
        op.f("ck_projects_im_group_status"), "projects", type_="check", schema="workspace"
    )
    for column in ("im_group_error", "im_group_owner_id", "im_group_status"):
        op.drop_column("projects", column, schema="workspace")
