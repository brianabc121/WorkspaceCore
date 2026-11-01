"""IM：OpenIM 账号、群与回调收件箱（V2 OC-3，S6-02）

docs/v2/TRANSFORMATION_PLAN.md 第 4.4、4.5、5 节；接口约定见
docs/decisions/2026-10-31-v2-oc1-contract.md 第 5、10 节。
- im_accounts：管理员（owner / admin）的 OpenIM 账号 ws_<用户 UUID 十六进制>，一个用户一个；
- im_groups / im_group_members：工作空间的群与成员；防刷屏阈值放在群上
  （方案的 im_group_policies 合并进来）；
- im_webhook_inbox：OpenIM after 回调的收件箱，按 event_key 去重。
会话、消息投影、置顶、通讯录分组与上传在 OC-3 后续部分加。只新增表，downgrade 删除即可回退。

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_webhook_inbox",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'completed', 'dead_letter')",
            name=op.f("ck_im_webhook_inbox_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_webhook_inbox")),
        sa.UniqueConstraint("event_key", name=op.f("uq_im_webhook_inbox_event_key")),
        schema="workspace",
    )
    op.create_index(
        "ix_im_webhook_pending",
        "im_webhook_inbox",
        ["next_retry_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("status IN ('pending', 'retry', 'processing')"),
    )
    op.create_table(
        "im_accounts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("im_user_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_im_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_im_accounts")),
        sa.UniqueConstraint("im_user_id", name=op.f("uq_im_accounts_im_user_id")),
        schema="workspace",
    )
    op.create_table(
        "im_groups",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("announcement", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("relay_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_agent_streak", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("max_per_minute", sa.Integer(), server_default=sa.text("6"), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
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
            "max_agent_streak BETWEEN 1 AND 50", name=op.f("ck_im_groups_max_agent_streak")
        ),
        sa.CheckConstraint(
            "max_per_minute BETWEEN 1 AND 60", name=op.f("ck_im_groups_max_per_minute")
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["workspace.users.id"],
            name=op.f("fk_im_groups_owner_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_im_groups_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_groups")),
        sa.UniqueConstraint("group_id", name=op.f("uq_im_groups_group_id")),
        schema="workspace",
    )
    op.create_table(
        "im_group_members",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("group_pk", sa.UUID(), nullable=False),
        sa.Column("im_user_id", sa.Text(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name=op.f("ck_im_group_members_role")
        ),
        sa.CheckConstraint(
            "(instance_id IS NULL) <> (user_id IS NULL)",
            name=op.f("ck_im_group_members_one_member"),
        ),
        sa.ForeignKeyConstraint(
            ["group_pk"],
            ["workspace.im_groups.id"],
            name=op.f("fk_im_group_members_group_pk_im_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_im_group_members_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_im_group_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_group_members")),
        sa.UniqueConstraint(
            "group_pk", "im_user_id", name=op.f("uq_im_group_members_group_pk_im_user_id")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_im_group_members_im_user_id",
        "im_group_members",
        ["im_user_id"],
        unique=False,
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_im_group_members_im_user_id", table_name="im_group_members", schema="workspace"
    )
    op.drop_table("im_group_members", schema="workspace")
    op.drop_table("im_groups", schema="workspace")
    op.drop_table("im_accounts", schema="workspace")
    op.drop_index(
        "ix_im_webhook_pending",
        table_name="im_webhook_inbox",
        schema="workspace",
        postgresql_where=sa.text("status IN ('pending', 'retry', 'processing')"),
    )
    op.drop_table("im_webhook_inbox", schema="workspace")
