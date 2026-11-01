"""IM：会话与消息投影（V2 OC-3，S6-02）

docs/v2/TRANSFORMATION_PLAN.md 第 5 节；接口约定见 docs/decisions/2026-10-31-v2-oc1-contract.md
第 5.2、5.3 节。
- im_conversations：每个管理员在一个工作空间里的会话（已读、置顶、免打扰各自独立）；
- im_messages：消息投影，按 OpenIM 的会话 ID 存一份；序号与内容以 pull_msg_by_seq 拉回的为准，
  工作台代发、还没被受理的消息 seq 与 server_msg_id 为空。
只新增表，downgrade 删除即可回退。

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("peer_instance_id", sa.UUID(), nullable=True),
        sa.Column("group_pk", sa.UUID(), nullable=True),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("muted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("read_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind IN ('single', 'group', 'notice')", name=op.f("ck_im_conversations_kind")
        ),
        sa.CheckConstraint("read_seq >= 0 AND max_seq >= 0", name=op.f("ck_im_conversations_seqs")),
        sa.ForeignKeyConstraint(
            ["group_pk"],
            ["workspace.im_groups.id"],
            name=op.f("fk_im_conversations_group_pk_im_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["workspace.users.id"],
            name=op.f("fk_im_conversations_owner_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["peer_instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_im_conversations_peer_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_im_conversations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_conversations")),
        sa.UniqueConstraint(
            "owner_user_id",
            "conversation_id",
            name=op.f("uq_im_conversations_owner_user_id_conversation_id"),
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_im_conversations_conversation_id",
        "im_conversations",
        ["conversation_id"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "im_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("server_msg_id", sa.Text(), nullable=True),
        sa.Column("client_msg_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("sender_im_id", sa.Text(), nullable=False),
        sa.Column("sender_instance_id", sa.UUID(), nullable=True),
        sa.Column("content_type", sa.Integer(), nullable=False),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("plain_text", sa.Text(), nullable=True),
        sa.Column(
            "at_im_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'sent'"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('sending', 'sent', 'failed', 'unknown')", name=op.f("ck_im_messages_status")
        ),
        sa.ForeignKeyConstraint(
            ["sender_instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_im_messages_sender_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_im_messages_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_messages")),
        sa.UniqueConstraint(
            "conversation_id",
            "client_msg_id",
            name=op.f("uq_im_messages_conversation_id_client_msg_id"),
        ),
        sa.UniqueConstraint(
            "conversation_id", "seq", name=op.f("uq_im_messages_conversation_id_seq")
        ),
        sa.UniqueConstraint("server_msg_id", name=op.f("uq_im_messages_server_msg_id")),
        schema="workspace",
    )
    op.create_index(
        "ix_im_messages_conversation_sent",
        "im_messages",
        ["conversation_id", "sent_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_im_messages_sent_at", "im_messages", ["sent_at"], unique=False, schema="workspace"
    )


def downgrade() -> None:
    op.drop_index("ix_im_messages_sent_at", table_name="im_messages", schema="workspace")
    op.drop_index("ix_im_messages_conversation_sent", table_name="im_messages", schema="workspace")
    op.drop_table("im_messages", schema="workspace")
    op.drop_index(
        "ix_im_conversations_conversation_id", table_name="im_conversations", schema="workspace"
    )
    op.drop_table("im_conversations", schema="workspace")
