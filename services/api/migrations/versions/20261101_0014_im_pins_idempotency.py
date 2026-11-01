"""IM：消息置顶与代发的幂等键（V2 OC-3，S6-02）

契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5.3、5.5 节。
- im_message_pins：消息置顶，挂在当前用户的会话上（每个会话最多 10 条，由接口检查）；
- im_messages.idempotency_key：工作台代发时的 Idempotency-Key，写进 OpenIM 消息的 ex，
  结果未知时靠它在投影里找回那条消息。
只新增表与可空列，downgrade 删除即可回退。

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_message_pins",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_pk", sa.UUID(), nullable=False),
        sa.Column("server_msg_id", sa.Text(), nullable=False),
        sa.Column("pinned_by", sa.UUID(), nullable=True),
        sa.Column(
            "pinned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_pk"],
            ["workspace.im_conversations.id"],
            name=op.f("fk_im_message_pins_conversation_pk_im_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pinned_by"],
            ["workspace.users.id"],
            name=op.f("fk_im_message_pins_pinned_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_message_pins")),
        sa.UniqueConstraint(
            "conversation_pk",
            "server_msg_id",
            name=op.f("uq_im_message_pins_conversation_pk_server_msg_id"),
        ),
        schema="workspace",
    )
    op.add_column(
        "im_messages", sa.Column("idempotency_key", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_unique_constraint(
        op.f("uq_im_messages_idempotency_key"),
        "im_messages",
        ["idempotency_key"],
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_im_messages_idempotency_key"), "im_messages", schema="workspace", type_="unique"
    )
    op.drop_column("im_messages", "idempotency_key", schema="workspace")
    op.drop_table("im_message_pins", schema="workspace")
