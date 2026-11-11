"""Scoped desired network services and owner-consented ingress (0063)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("environment_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_instance_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("host_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("desired_revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("desired_generation", sa.Integer(), nullable=False),
        sa.Column("applied_generation", sa.Integer()),
        sa.Column("apply_error", sa.Text()),
        sa.Column(
            "created_by", pg.UUID(as_uuid=True), sa.ForeignKey("workspace.users.id"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "revoked_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspace.users.id", ondelete="SET NULL"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["workspace.runner_environments.workspace_id", "workspace.runner_environments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "environment_id", "id"),
        sa.CheckConstraint(
            "state IN ('approved_pending_apply','active','revoked','expired','apply_failed')",
            name="state",
        ),
        sa.CheckConstraint(
            "desired_revision >= 1 AND applied_revision >= 0 "
            "AND applied_revision <= desired_revision",
            name="revision",
        ),
    ]


def service_fk():
    return sa.ForeignKeyConstraint(
        ["workspace_id", "environment_id", "service_id"],
        [
            "workspace.runner_services.workspace_id",
            "workspace.runner_services.environment_id",
            "workspace.runner_services.id",
        ],
        ondelete="CASCADE",
    )


def upgrade():
    op.create_table(
        "runner_services",
        *common(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("desired_dns", sa.Text()),
        sa.UniqueConstraint("environment_id", "name"),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="port"),
        sa.CheckConstraint("protocol IN ('TCP','UDP')", name="protocol"),
        schema="workspace",
    )
    op.create_table(
        "runner_ingress_rules",
        *common(),
        sa.Column("service_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source_instance_id", pg.UUID(as_uuid=True), nullable=False),
        service_fk(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("expires_at IS NOT NULL", name="finite_lease"),
        schema="workspace",
    )
    op.create_table(
        "runner_published_endpoints",
        *common(),
        sa.Column("service_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_cidrs", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("enforcement", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        service_fk(),
        sa.CheckConstraint("expires_at IS NOT NULL", name="finite_lease"),
        schema="workspace",
    )


def downgrade():
    for table in ("runner_published_endpoints", "runner_ingress_rules", "runner_services"):
        op.drop_table(table, schema="workspace")
