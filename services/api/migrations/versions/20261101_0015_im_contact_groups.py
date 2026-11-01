"""IM：通讯录分组（V2 OC-3，S6-02）

契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5.7 节。
- im_contact_groups：属于工作空间，名称在工作空间内唯一；
- im_contact_group_members：分组与实例的对应关系（一个实例可以在多个分组里）。
只新增表，downgrade 删除即可回退。

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_contact_groups",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 0 AND 10000", name=op.f("ck_im_contact_groups_sort_order")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_im_contact_groups_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_contact_groups")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_im_contact_groups_workspace_id_name")
        ),
        schema="workspace",
    )
    op.create_table(
        "im_contact_group_members",
        sa.Column("contact_group_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_group_id"],
            ["workspace.im_contact_groups.id"],
            name=op.f("fk_im_contact_group_members_contact_group_id_im_contact_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_im_contact_group_members_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "contact_group_id", "instance_id", name=op.f("pk_im_contact_group_members")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_im_contact_group_members_instance_id",
        "im_contact_group_members",
        ["instance_id"],
        unique=False,
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_im_contact_group_members_instance_id",
        table_name="im_contact_group_members",
        schema="workspace",
    )
    op.drop_table("im_contact_group_members", schema="workspace")
    op.drop_table("im_contact_groups", schema="workspace")
