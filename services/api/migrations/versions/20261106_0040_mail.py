"""Gmail 邮件（T-0128，docs/v2/MAIL_PLAN.md）

- mail_oauth_clients：每个工作空间的 Google OAuth 客户端（client_secret 加密）。
- mail_accounts：连接的 Gmail 邮箱（实际授权范围、加密的 refresh token、状态、
  是否允许智能体访问、智能体发信的署名行）。
- mail_sends：发件记录（人或智能体），不存正文。
- agent_instances：mail_read_enabled（默认打开）、mail_send_enabled（默认关闭）、
  mail_send_scope（any / listed）、mail_send_recipients、打开发送的人和时间。

只新增表与列；downgrade 删除即可回退（都是 0040 之后才有的数据）。

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-06
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_EMPTY = sa.text("'{}'::text[]")


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.workspaces.id"],
        name=op.f(f"fk_{table}_workspace_id_workspaces"),
        ondelete="CASCADE",
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["workspace.users.id"],
        name=op.f(f"fk_{table}_{column}_users"),
        ondelete="SET NULL",
    )


def upgrade() -> None:
    op.create_table(
        "mail_oauth_clients",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_ref", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        _workspace_fk("mail_oauth_clients"),
        _user_fk("mail_oauth_clients", "updated_by"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_oauth_clients")),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_mail_oauth_clients_workspace_id")),
        schema="workspace",
    )
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default=_EMPTY, nullable=False),
        sa.Column("refresh_token_ref", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("agents_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("agent_signature", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("connected_by", sa.UUID(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'needs_reconnect', 'disconnected')",
            name=op.f("ck_mail_accounts_status"),
        ),
        _workspace_fk("mail_accounts"),
        _user_fk("mail_accounts", "connected_by"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_accounts")),
        sa.UniqueConstraint(
            "workspace_id", "email", name=op.f("uq_mail_accounts_workspace_id_email")
        ),
        schema="workspace",
    )
    op.create_table(
        "mail_sends",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("to_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "cc_addresses", postgresql.ARRAY(sa.Text()), server_default=_EMPTY, nullable=False
        ),
        sa.Column("bcc_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_chars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reply_to_message_id", sa.Text(), nullable=True),
        sa.Column("gmail_message_id", sa.Text(), nullable=True),
        sa.Column("gmail_thread_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'sending'"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("client_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('sending', 'sent', 'failed', 'unknown')", name=op.f("ck_mail_sends_status")
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'instance')", name=op.f("ck_mail_sends_actor_kind")
        ),
        sa.CheckConstraint(
            "(user_id IS NULL) OR (instance_id IS NULL)", name=op.f("ck_mail_sends_one_actor")
        ),
        sa.CheckConstraint("char_length(subject) <= 200", name=op.f("ck_mail_sends_subject")),
        _workspace_fk("mail_sends"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["workspace.mail_accounts.id"],
            name=op.f("fk_mail_sends_account_id_mail_accounts"),
            ondelete="SET NULL",
        ),
        _user_fk("mail_sends", "user_id"),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_mail_sends_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_sends")),
        schema="workspace",
    )
    op.create_index(
        "ix_mail_sends_workspace_created",
        "mail_sends",
        ["workspace_id", "created_at"],
        schema="workspace",
    )
    op.create_index(
        "ix_mail_sends_instance_created",
        "mail_sends",
        ["instance_id", "created_at"],
        schema="workspace",
    )
    op.create_index(
        "uq_mail_sends_client_key",
        "mail_sends",
        ["instance_id", "client_key"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("client_key IS NOT NULL"),
    )

    columns: list[sa.Column[Any]] = [
        sa.Column(
            "mail_read_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "mail_send_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("mail_send_scope", sa.Text(), nullable=True),
        sa.Column(
            "mail_send_recipients",
            postgresql.ARRAY(sa.Text()),
            server_default=_EMPTY,
            nullable=False,
        ),
        sa.Column("mail_send_by", sa.UUID(), nullable=True),
        sa.Column("mail_send_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in columns:
        op.add_column("agent_instances", column, schema="workspace")
    op.create_foreign_key(
        op.f("fk_agent_instances_mail_send_by_users"),
        "agent_instances",
        "users",
        ["mail_send_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_agent_instances_mail_send_scope"),
        "agent_instances",
        "mail_send_scope IS NULL OR mail_send_scope IN ('any', 'listed')",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_agent_instances_mail_send_needs_scope"),
        "agent_instances",
        "NOT mail_send_enabled OR mail_send_scope IS NOT NULL",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_agent_instances_mail_send_recipients"),
        "agent_instances",
        "cardinality(mail_send_recipients) <= 50",
        schema="workspace",
    )


def downgrade() -> None:
    for name in ("mail_send_recipients", "mail_send_needs_scope", "mail_send_scope"):
        op.drop_constraint(
            op.f(f"ck_agent_instances_{name}"), "agent_instances", schema="workspace", type_="check"
        )
    op.drop_constraint(
        op.f("fk_agent_instances_mail_send_by_users"),
        "agent_instances",
        schema="workspace",
        type_="foreignkey",
    )
    for column in (
        "mail_send_at",
        "mail_send_by",
        "mail_send_recipients",
        "mail_send_scope",
        "mail_send_enabled",
        "mail_read_enabled",
    ):
        op.drop_column("agent_instances", column, schema="workspace")
    op.drop_index("uq_mail_sends_client_key", "mail_sends", schema="workspace")
    op.drop_index("ix_mail_sends_instance_created", "mail_sends", schema="workspace")
    op.drop_index("ix_mail_sends_workspace_created", "mail_sends", schema="workspace")
    op.drop_table("mail_sends", schema="workspace")
    op.drop_table("mail_accounts", schema="workspace")
    op.drop_table("mail_oauth_clients", schema="workspace")
