"""一台服务器多个 OpenClaw：SSH 登录信息按服务器，每个实例的部署单独记（T-0144 / T-0145）

设计见 docs/v2/MULTI_OPENCLAW_PLAN.md 第 4、6、8 节（D7：每台服务器一把专用密钥）。

- agent_ssh_targets 改为每台服务器一条（server_id → agent_servers，唯一），去掉 instance_id、
  install_dir、installed_at，加 prepared_at；
- agent_deployments（新）：实例部署在哪台服务器、哪个目录；
- agent_ssh_jobs：instance_id 可空（准备服务器的任务没有实例），kind 增加 prepare；
- agent_ops_actions：加 server_id，instance_id 可空（清理镜像、查看全部容器是服务器级的），
  action 增加 containers；同一台服务器同时只有一个进行中的动作。

人类 2026-11-07 确认还没有部署过（D5）：旧的按实例的 SSH 目标、任务与运维记录直接删除，不迁移。
downgrade 回到 0045 的结构，同样不保留这些记录。

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WS = "workspace"
_INSTANCE_FK = ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"]


def _clear() -> None:
    op.execute("DELETE FROM workspace.agent_ops_actions")
    op.execute("DELETE FROM workspace.agent_ssh_jobs")
    op.execute("DELETE FROM workspace.agent_ssh_targets")


def upgrade() -> None:
    _clear()
    # ---- agent_ssh_targets：按服务器
    op.drop_constraint(
        op.f("fk_agent_ssh_targets_workspace_id_instance_id_agent_instances"),
        "agent_ssh_targets",
        type_="foreignkey",
        schema=_WS,
    )
    op.drop_constraint(
        op.f("uq_agent_ssh_targets_instance_id"), "agent_ssh_targets", type_="unique", schema=_WS
    )
    for column in ("instance_id", "install_dir", "installed_at"):
        op.drop_column("agent_ssh_targets", column, schema=_WS)
    op.add_column(
        "agent_ssh_targets", sa.Column("server_id", sa.UUID(), nullable=False), schema=_WS
    )
    op.add_column(
        "agent_ssh_targets",
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        schema=_WS,
    )
    op.create_foreign_key(
        op.f("fk_agent_ssh_targets_server_id_agent_servers"),
        "agent_ssh_targets",
        "agent_servers",
        ["server_id"],
        ["id"],
        source_schema=_WS,
        referent_schema=_WS,
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_agent_ssh_targets_server_id"), "agent_ssh_targets", ["server_id"], schema=_WS
    )

    # ---- agent_deployments
    op.create_table(
        "agent_deployments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("server_id", sa.UUID(), nullable=False),
        sa.Column("install_dir", sa.Text(), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_deployments_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["workspace.agent_servers.id"],
            name=op.f("fk_agent_deployments_server_id_agent_servers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            _INSTANCE_FK,
            name=op.f("fk_agent_deployments_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_deployments_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_deployments")),
        sa.UniqueConstraint("instance_id", name=op.f("uq_agent_deployments_instance_id")),
        schema=_WS,
    )
    op.create_index(
        "ix_agent_deployments_server_id", "agent_deployments", ["server_id"], schema=_WS
    )

    # ---- agent_ssh_jobs
    op.alter_column("agent_ssh_jobs", "instance_id", nullable=True, schema=_WS)
    op.drop_constraint(op.f("ck_agent_ssh_jobs_kind"), "agent_ssh_jobs", type_="check", schema=_WS)
    op.create_check_constraint(
        op.f("ck_agent_ssh_jobs_kind"),
        "agent_ssh_jobs",
        "kind IN ('prepare', 'install', 'uninstall')",
        schema=_WS,
    )

    # ---- agent_ops_actions
    op.drop_index("uq_agent_ops_actions_running", table_name="agent_ops_actions", schema=_WS)
    op.alter_column("agent_ops_actions", "instance_id", nullable=True, schema=_WS)
    op.add_column(
        "agent_ops_actions", sa.Column("server_id", sa.UUID(), nullable=False), schema=_WS
    )
    op.create_foreign_key(
        op.f("fk_agent_ops_actions_server_id_agent_servers"),
        "agent_ops_actions",
        "agent_servers",
        ["server_id"],
        ["id"],
        source_schema=_WS,
        referent_schema=_WS,
        ondelete="CASCADE",
    )
    op.drop_constraint(
        op.f("ck_agent_ops_actions_action"), "agent_ops_actions", type_="check", schema=_WS
    )
    op.create_check_constraint(
        op.f("ck_agent_ops_actions_action"),
        "agent_ops_actions",
        "action IN ('status', 'logs', 'restart', 'up', 'prune', 'report', 'containers')",
        schema=_WS,
    )
    op.create_check_constraint(
        op.f("ck_agent_ops_actions_instance_scope"),
        "agent_ops_actions",
        "instance_id IS NOT NULL OR action IN ('prune', 'containers')",
        schema=_WS,
    )
    op.create_index(
        "uq_agent_ops_actions_running",
        "agent_ops_actions",
        ["server_id"],
        unique=True,
        schema=_WS,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_agent_ops_actions_server_started",
        "agent_ops_actions",
        ["server_id", "started_at"],
        schema=_WS,
    )


def downgrade() -> None:
    _clear()
    op.execute("DELETE FROM workspace.agent_deployments")
    # ---- agent_ops_actions
    op.drop_index("ix_agent_ops_actions_server_started", table_name="agent_ops_actions", schema=_WS)
    op.drop_index("uq_agent_ops_actions_running", table_name="agent_ops_actions", schema=_WS)
    op.drop_constraint(
        op.f("ck_agent_ops_actions_instance_scope"), "agent_ops_actions", type_="check", schema=_WS
    )
    op.drop_constraint(
        op.f("ck_agent_ops_actions_action"), "agent_ops_actions", type_="check", schema=_WS
    )
    op.create_check_constraint(
        op.f("ck_agent_ops_actions_action"),
        "agent_ops_actions",
        "action IN ('status', 'logs', 'restart', 'up', 'prune', 'report')",
        schema=_WS,
    )
    op.drop_constraint(
        op.f("fk_agent_ops_actions_server_id_agent_servers"),
        "agent_ops_actions",
        type_="foreignkey",
        schema=_WS,
    )
    op.drop_column("agent_ops_actions", "server_id", schema=_WS)
    op.alter_column("agent_ops_actions", "instance_id", nullable=False, schema=_WS)
    op.create_index(
        "uq_agent_ops_actions_running",
        "agent_ops_actions",
        ["instance_id"],
        unique=True,
        schema=_WS,
        postgresql_where=sa.text("status = 'running'"),
    )

    # ---- agent_ssh_jobs
    op.drop_constraint(op.f("ck_agent_ssh_jobs_kind"), "agent_ssh_jobs", type_="check", schema=_WS)
    op.create_check_constraint(
        op.f("ck_agent_ssh_jobs_kind"),
        "agent_ssh_jobs",
        "kind IN ('install', 'uninstall')",
        schema=_WS,
    )
    op.alter_column("agent_ssh_jobs", "instance_id", nullable=False, schema=_WS)

    # ---- agent_deployments
    op.drop_index("ix_agent_deployments_server_id", table_name="agent_deployments", schema=_WS)
    op.drop_table("agent_deployments", schema=_WS)

    # ---- agent_ssh_targets
    op.drop_constraint(
        op.f("uq_agent_ssh_targets_server_id"), "agent_ssh_targets", type_="unique", schema=_WS
    )
    op.drop_constraint(
        op.f("fk_agent_ssh_targets_server_id_agent_servers"),
        "agent_ssh_targets",
        type_="foreignkey",
        schema=_WS,
    )
    op.drop_column("agent_ssh_targets", "prepared_at", schema=_WS)
    op.drop_column("agent_ssh_targets", "server_id", schema=_WS)
    op.add_column(
        "agent_ssh_targets", sa.Column("instance_id", sa.UUID(), nullable=False), schema=_WS
    )
    op.add_column(
        "agent_ssh_targets", sa.Column("install_dir", sa.Text(), nullable=True), schema=_WS
    )
    op.add_column(
        "agent_ssh_targets",
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        schema=_WS,
    )
    op.create_unique_constraint(
        op.f("uq_agent_ssh_targets_instance_id"), "agent_ssh_targets", ["instance_id"], schema=_WS
    )
    op.create_foreign_key(
        op.f("fk_agent_ssh_targets_workspace_id_instance_id_agent_instances"),
        "agent_ssh_targets",
        "agent_instances",
        ["workspace_id", "instance_id"],
        ["workspace_id", "id"],
        source_schema=_WS,
        referent_schema=_WS,
        ondelete="CASCADE",
    )
