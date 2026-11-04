"""智能体的项目管理权限（T-0112，docs/v2/AGENT_OPS_PLAN.md 第 2.3 节）

- agent_instances.project_admin_enabled：管理员按实例打开“允许它新建项目、拉智能体进项目”，默认关；
  project_admin_by / project_admin_at：是谁、什么时候打开的（这个人做它建的项目群的群主）；
- projects.created_by_instance：智能体建的项目记下是哪个实例建的（它只能把智能体拉进自己建的项目）。

已有实例都是关闭的。downgrade 删除这些列（智能体建的项目保留，只是不再记得是谁建的）。

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_instances",
        sa.Column(
            "project_admin_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        schema="workspace",
    )
    op.add_column(
        "agent_instances",
        sa.Column("project_admin_by", sa.UUID(), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "agent_instances",
        sa.Column("project_admin_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_agent_instances_project_admin_by_users"),
        "agent_instances",
        "users",
        ["project_admin_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.add_column(
        "projects", sa.Column("created_by_instance", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_projects_created_by_instance_agent_instances"),
        "projects",
        "agent_instances",
        ["created_by_instance"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_projects_created_by_instance_agent_instances"),
        "projects",
        type_="foreignkey",
        schema="workspace",
    )
    op.drop_column("projects", "created_by_instance", schema="workspace")
    op.drop_constraint(
        op.f("fk_agent_instances_project_admin_by_users"),
        "agent_instances",
        type_="foreignkey",
        schema="workspace",
    )
    for column in ("project_admin_at", "project_admin_by", "project_admin_enabled"):
        op.drop_column("agent_instances", column, schema="workspace")
