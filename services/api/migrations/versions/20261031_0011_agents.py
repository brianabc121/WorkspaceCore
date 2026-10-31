"""OpenClaw 实例、接入凭证、密钥库与内部 CA（V2 OC-2，S6-01）

docs/v2/TRANSFORMATION_PLAN.md 第 4.1 ~ 4.3、5 节；接口约定见
docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节。
- 实例令牌只存 SHA-256，客户端证书只存指纹；密钥值与 CA 私钥只以 enc:v1 引用保存；
- 已有表的密文引用本身带 key_id（`enc:v1:<key_id>:…`），加密密钥轮换不需要给它们加列
  （方案第 5 节的 key_version 列由引用里的 key_id 承担，见 app/commands/rotate_credentials.py）。
只新增表，downgrade 删除即可回退（会丢失实例、凭证与密钥库）。

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_ca",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("key_ref", sa.Text(), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_ca")),
        schema="workspace",
    )
    op.create_index(
        "uq_agent_ca_active",
        "agent_ca",
        [sa.literal_column("(retired_at IS NULL)")],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "agent_instances",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("network_mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("scope", sa.Text(), server_default=sa.text("'read'"), nullable=False),
        sa.Column("max_role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.Column("im_user_id", sa.Text(), nullable=False),
        sa.Column("im_nickname", sa.Text(), nullable=False),
        sa.Column("im_ready", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("acting_user_id", sa.UUID(), nullable=True),
        sa.Column("reported_version", sa.Text(), nullable=True),
        sa.Column("plugin_version", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bundle_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
            "max_role IN ('admin', 'member', 'viewer')", name=op.f("ck_agent_instances_max_role")
        ),
        sa.CheckConstraint(
            "network_mode IN ('intranet', 'public')", name=op.f("ck_agent_instances_network_mode")
        ),
        sa.CheckConstraint(
            "scope IN ('read', 'read_write')", name=op.f("ck_agent_instances_scope")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'online', 'offline', 'disabled')",
            name=op.f("ck_agent_instances_status"),
        ),
        sa.ForeignKeyConstraint(
            ["acting_user_id"],
            ["workspace.users.id"],
            name=op.f("fk_agent_instances_acting_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_instances_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_instances")),
        sa.UniqueConstraint("im_user_id", name=op.f("uq_agent_instances_im_user_id")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_agent_instances_workspace_id_id")),
        schema="workspace",
    )
    op.create_table(
        "agent_secrets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default=sa.text("'generic'"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "last_rotated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            "kind IN ('model_api_key', 'api_token', 'generic')", name=op.f("ck_agent_secrets_kind")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_secrets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_secrets")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_agent_secrets_workspace_id_id")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_agent_secrets_workspace_id_name")
        ),
        schema="workspace",
    )
    op.create_table(
        "agent_settings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("agents_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_settings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_settings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_agent_settings")),
        schema="workspace",
    )
    op.create_table(
        "agent_credentials",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ingress", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=True),
        sa.Column("token_prefix", sa.Text(), nullable=True),
        sa.Column("cert_fingerprint", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'token' AND token_hash IS NOT NULL AND cert_fingerprint IS NULL)"
            " OR (kind = 'client_cert' AND cert_fingerprint IS NOT NULL"
            " AND token_hash IS NULL)",
            name=op.f("ck_agent_credentials_material"),
        ),
        sa.CheckConstraint(
            "ingress IN ('intranet', 'public')", name=op.f("ck_agent_credentials_ingress")
        ),
        sa.CheckConstraint(
            "kind IN ('token', 'client_cert')", name=op.f("ck_agent_credentials_kind")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_agent_credentials_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_credentials")),
        sa.UniqueConstraint("cert_fingerprint", name=op.f("uq_agent_credentials_cert_fingerprint")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_agent_credentials_token_hash")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_credentials_active",
        "agent_credentials",
        ["instance_id", "kind"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "agent_secret_accesses",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("instance_name", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("secret_id", sa.UUID(), nullable=True),
        sa.Column("value_version", sa.Integer(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("source_ip", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "result IN ('ok', 'not_found', 'rate_limited')",
            name=op.f("ck_agent_secret_accesses_result"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_agent_secret_accesses_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["secret_id"],
            ["workspace.agent_secrets.id"],
            name=op.f("fk_agent_secret_accesses_secret_id_agent_secrets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_secret_accesses_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_secret_accesses")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_secret_accesses_instance_at",
        "agent_secret_accesses",
        ["instance_id", "at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_agent_secret_accesses_workspace_at",
        "agent_secret_accesses",
        ["workspace_id", "at"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "agent_secret_grants",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("secret_id", sa.UUID(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_agent_secret_grants_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "secret_id"],
            ["workspace.agent_secrets.workspace_id", "workspace.agent_secrets.id"],
            name=op.f("fk_agent_secret_grants_workspace_id_secret_id_agent_secrets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_secret_grants")),
        sa.UniqueConstraint(
            "instance_id", "ref_id", name=op.f("uq_agent_secret_grants_instance_id_ref_id")
        ),
        schema="workspace",
    )
    op.create_table(
        "agent_secret_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("secret_id", sa.UUID(), nullable=False),
        sa.Column("value_version", sa.Integer(), nullable=False),
        sa.Column("ciphertext_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "secret_id"],
            ["workspace.agent_secrets.workspace_id", "workspace.agent_secrets.id"],
            name=op.f("fk_agent_secret_versions_workspace_id_secret_id_agent_secrets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_secret_versions")),
        sa.UniqueConstraint(
            "secret_id",
            "value_version",
            name=op.f("uq_agent_secret_versions_secret_id_value_version"),
        ),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("agent_secret_versions", schema="workspace")
    op.drop_table("agent_secret_grants", schema="workspace")
    op.drop_index(
        "ix_agent_secret_accesses_workspace_at",
        table_name="agent_secret_accesses",
        schema="workspace",
    )
    op.drop_index(
        "ix_agent_secret_accesses_instance_at",
        table_name="agent_secret_accesses",
        schema="workspace",
    )
    op.drop_table("agent_secret_accesses", schema="workspace")
    op.drop_index(
        "ix_agent_credentials_active",
        table_name="agent_credentials",
        schema="workspace",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("agent_credentials", schema="workspace")
    op.drop_table("agent_settings", schema="workspace")
    op.drop_table("agent_secrets", schema="workspace")
    op.drop_table("agent_instances", schema="workspace")
    op.drop_index(
        "uq_agent_ca_active",
        table_name="agent_ca",
        schema="workspace",
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.drop_table("agent_ca", schema="workspace")
