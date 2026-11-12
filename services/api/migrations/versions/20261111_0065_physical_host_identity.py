"""One physical server contributes capacity to one registration across both pools."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_agent_servers_workspace_id_id",
        "agent_servers",
        ["workspace_id", "id"],
        schema="workspace",
    )
    op.create_table(
        "physical_host_registrations",
        sa.Column("physical_id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspace.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_server_id", pg.UUID(as_uuid=True)),
        sa.Column("runner_host_id", pg.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("physical_id ~ '^[a-f0-9]{64}$'", name="physical_id_format"),
        sa.CheckConstraint(
            "(agent_server_id IS NOT NULL) <> (runner_host_id IS NOT NULL)", name="one_pool"
        ),
        sa.UniqueConstraint("agent_server_id"),
        sa.UniqueConstraint("runner_host_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_server_id"],
            ["workspace.agent_servers.workspace_id", "workspace.agent_servers.id"],
            name="fk_physical_host_agent_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "runner_host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            name="fk_physical_host_runner_scope",
            ondelete="CASCADE",
        ),
        schema="workspace",
    )


def downgrade():
    # Serialize the emptiness check with in-flight claims, not just DROP.
    op.get_bind().execute(
        sa.text("LOCK TABLE workspace.physical_host_registrations IN ACCESS EXCLUSIVE MODE")
    )
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM workspace.physical_host_registrations")):
        raise RuntimeError("物理服务器身份已登记，不能静默移除容量防重保护")
    op.drop_table("physical_host_registrations", schema="workspace")
    op.drop_constraint(
        "uq_agent_servers_workspace_id_id", "agent_servers", type_="unique", schema="workspace"
    )
