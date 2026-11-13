"""运行服务器池、实际后端与本地镜像构建亲和性。

Revision ID: 0062
Revises: 0061
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_runner_hosts_workspace_id", "runner_hosts", schema="workspace", type_="unique"
    )
    op.add_column(
        "runner_hosts",
        sa.Column("backend", sa.Text(), nullable=False, server_default="docker"),
        schema="workspace",
    )
    op.add_column("runner_hosts", sa.Column("node_name", sa.Text()), schema="workspace")
    op.add_column(
        "runner_hosts",
        sa.Column(
            "runtime_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="workspace",
    )
    op.add_column(
        "runner_templates",
        sa.Column("build_host_id", postgresql.UUID(as_uuid=True)),
        schema="workspace",
    )
    op.execute(
        "UPDATE workspace.runner_templates t SET build_host_id = h.id "
        "FROM workspace.runner_hosts h WHERE h.workspace_id=t.workspace_id"
    )
    op.add_column(
        "runner_environments",
        sa.Column("runtime_backend", sa.Text(), nullable=False, server_default="docker"),
        schema="workspace",
    )
    for field in ("runtime_metadata", "placement"):
        op.add_column(
            "runner_environments",
            sa.Column(
                field, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
            schema="workspace",
        )


def downgrade() -> None:
    # 不删除第二台主机或它的环境。先要求操作者清空多机配置，避免破坏已有快照和卷记录。
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM workspace.runner_hosts "
            "GROUP BY workspace_id HAVING count(*) > 1)"
        )
    ):
        raise RuntimeError("存在多服务器工作空间，不能安全回退到单服务器结构")
    for field in ("placement", "runtime_metadata", "runtime_backend"):
        op.drop_column("runner_environments", field, schema="workspace")
    op.drop_column("runner_templates", "build_host_id", schema="workspace")
    for field in ("runtime_metadata", "node_name", "backend"):
        op.drop_column("runner_hosts", field, schema="workspace")
    op.create_unique_constraint(
        "uq_runner_hosts_workspace_id", "runner_hosts", ["workspace_id"], schema="workspace"
    )
