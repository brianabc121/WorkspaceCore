"""第三方集成、凭证、外部资源投影、幂等记录与同步游标（S3-02 ~ S3-04）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 6、7、10、16、18 项；
约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md。
只新增表，不改已有数据；downgrade 删除这五张表即可回退。凭证与 Webhook 签名密钥只存 enc:v1 引用。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.UUID(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["workspace.users.id"],
            name=op.f("fk_idempotency_requests_actor_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_idempotency_requests_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_requests")),
        sa.UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name=op.f("uq_idempotency_requests_actor_id_idempotency_key"),
        ),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_idempotency_requests_expires_at"),
        "idempotency_requests",
        ["expires_at"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "integrations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("webhook_secret_ref", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_integrations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "name",
            name=op.f("uq_integrations_workspace_id_provider_name"),
        ),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_integrations_workspace_id"),
        "integrations",
        ["workspace_id"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "external_resources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["workspace.integrations.id"],
            name=op.f("fk_external_resources_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_external_resources_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_resources")),
        sa.UniqueConstraint(
            "integration_id",
            "resource_type",
            "external_id",
            name=op.f("uq_external_resources_integration_id_resource_type_external_id"),
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_external_resources_workspace_type",
        "external_resources",
        ["workspace_id", "resource_type"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "integration_accounts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("auth_type", sa.Text(), nullable=False),
        sa.Column("credential_ref", sa.Text(), nullable=False),
        sa.Column(
            "scopes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["workspace.integrations.id"],
            name=op.f("fk_integration_accounts_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_integration_accounts_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_accounts")),
        sa.UniqueConstraint("integration_id", name=op.f("uq_integration_accounts_integration_id")),
        schema="workspace",
    )
    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("cursor_value", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["workspace.integrations.id"],
            name=op.f("fk_sync_cursors_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_cursors")),
        sa.UniqueConstraint(
            "integration_id",
            "resource_type",
            name=op.f("uq_sync_cursors_integration_id_resource_type"),
        ),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("sync_cursors", schema="workspace")
    op.drop_table("integration_accounts", schema="workspace")
    op.drop_index(
        "ix_external_resources_workspace_type",
        table_name="external_resources",
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("external_resources", schema="workspace")
    op.drop_index(
        op.f("ix_integrations_workspace_id"), table_name="integrations", schema="workspace"
    )
    op.drop_table("integrations", schema="workspace")
    op.drop_index(
        op.f("ix_idempotency_requests_expires_at"),
        table_name="idempotency_requests",
        schema="workspace",
    )
    op.drop_table("idempotency_requests", schema="workspace")
