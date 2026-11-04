"""智能体的发件箱与拒收记录（T-0118，docs/v2/AGENT_MESSAGING_PLAN.md 第 5、6 节）

- agent_im_outbox：智能体经 `im_send` 主动发的消息，一条 OpenIM 消息一行
  （文字之后的每个附件各一行）。工作台以实例身份经 /msg/send_msg 发出；排队、定时、
  限流后补发、退避重试、结果未知时按 ex 里的发件箱 ID 对账。
  `(instance_id, client_key, part)` 唯一，同一个幂等键重试不会发出两次。保留 30 天。
- agent_im_rejections：实例发的消息被 OpenIM before 回调拒收的记录（主要是插件发的自动回复），
  只存拒收码、消息类型和脱敏后的前 120 字，保留 7 天。

新建两张表，downgrade 删除即可回退（都是 0034 之后才有的数据）。

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = (
    "('scheduled', 'queued', 'sending', 'sent', 'rejected', 'failed', 'unknown', 'cancelled')"
)


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "agent_im_outbox",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("client_key", sa.Text(), nullable=False),
        sa.Column("part", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Integer(), nullable=False),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("plain_text", sa.Text(), nullable=True),
        sa.Column(
            "at_im_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("media_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'tool'"), nullable=False),
        sa.Column("if_rate_limited", sa.Text(), server_default=sa.text("'queue'"), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=now, nullable=False
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("server_msg_id", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.CheckConstraint(f"status IN {STATUSES}", name=op.f("ck_agent_im_outbox_status")),
        sa.CheckConstraint(
            "source IN ('tool', 'reply_retry', 'wakeup')", name=op.f("ck_agent_im_outbox_source")
        ),
        sa.CheckConstraint(
            "if_rate_limited IN ('queue', 'fail')", name=op.f("ck_agent_im_outbox_if_rate_limited")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_im_outbox_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_im_outbox_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_im_outbox")),
        sa.UniqueConstraint(
            "instance_id",
            "client_key",
            "part",
            name=op.f("uq_agent_im_outbox_instance_id_client_key_part"),
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_im_outbox_due",
        "agent_im_outbox",
        ["next_attempt_at"],
        schema="workspace",
        postgresql_where=sa.text("status IN ('scheduled', 'queued', 'unknown')"),
    )
    op.create_index(
        "ix_agent_im_outbox_instance_created",
        "agent_im_outbox",
        ["instance_id", "created_at"],
        schema="workspace",
    )
    op.create_table(
        "agent_im_rejections",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("code", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.Integer(), nullable=False),
        sa.Column("client_msg_id", sa.Text(), nullable=False),
        sa.Column("preview", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("retried", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_im_rejections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_im_rejections_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_im_rejections")),
        sa.UniqueConstraint(
            "instance_id",
            "client_msg_id",
            name=op.f("uq_agent_im_rejections_instance_id_client_msg_id"),
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_im_rejections_instance_created",
        "agent_im_rejections",
        ["instance_id", "created_at"],
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("agent_im_rejections", schema="workspace")
    op.drop_table("agent_im_outbox", schema="workspace")
