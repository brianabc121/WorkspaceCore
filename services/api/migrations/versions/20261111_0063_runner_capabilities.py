"""Independent runtime capability requests and grants.

Revision ID: 0063
Revises: 0062
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def _fk(
    column: str, target: str, ondelete: str | None = None, nullable: bool = False
) -> sa.Column[uuid.UUID]:
    return sa.Column(
        column,
        pg.UUID(as_uuid=True),
        sa.ForeignKey("workspace." + target, ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    def common() -> list[sa.schema.SchemaItem]:
        return [
            sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
            _fk("workspace_id", "workspaces.id", "CASCADE"),
            _fk("instance_id", "agent_instances.id", "CASCADE"),
            _fk("approval_id", "ai_tool_executions.id"),
            sa.Column("capability", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("policy_version", sa.Integer(), nullable=False),
            sa.UniqueConstraint("approval_id"),
            sa.CheckConstraint(
                "capability IN ('runner.unlimited_container_count', "
                "'runner.cross_agent_communication')",
                name="capability",
            ),
        ]

    op.create_table(
        "runner_capability_requests",
        *common(),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="workspace",
    )
    op.create_table(
        "runner_capability_grants",
        *common(),
        _fk("requested_by", "users.id", "SET NULL", nullable=True),
        _fk("approved_by", "users.id"),
        sa.Column("scope", pg.JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("desired_revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column(
            "approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("revoked_by", "users.id", "SET NULL", nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.Text()),
        sa.CheckConstraint(
            "state IN ('approved_pending_apply','active','revoked','expired','apply_failed')",
            name="state",
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_capability_grants_scope",
        "runner_capability_grants",
        ["workspace_id", "instance_id", "capability"],
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("runner_capability_grants", schema="workspace")
    op.drop_table("runner_capability_requests", schema="workspace")
