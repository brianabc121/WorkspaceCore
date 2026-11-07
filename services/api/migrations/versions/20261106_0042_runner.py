"""运行服务器（T-0136 ~ T-0139，docs/v2/RUNNER_PLAN.md 第 12 节）

- runner_hosts：运行服务器（V1 每个工作空间一台）：凭证哈希、证书指纹、容量、版本、最后上报。
- runner_templates：环境模板（规格 JSON、版本号、构建状态与镜像）。
- runner_environments：环境（容器 + 数据卷）：类别、复用键、状态、配置记录、初始化步骤、用量与时间。
- runner_delete_conditions：环境的删除条件。
- runner_runs：在环境里执行的一次命令。
- runner_commands：下发给 runner-agent 的指令（不存密钥原文）。
- agent_instances 加运行环境权限：runner_enabled（默认 false）、runner_by、
  runner_at、runner_limits。
- agent_wakeups.source 加 'runner'：较久的运行结束后叫醒智能体看结果。

全部新增，downgrade 删掉这些表与列（运行服务器上的容器不受影响，按标签由人清理）。

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_hosts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("ingress", sa.Text(), server_default=sa.text("'intranet'"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_prefix", sa.Text(), nullable=False),
        sa.Column("cert_fingerprint", sa.Text(), nullable=True),
        sa.Column("cpus", sa.Integer(), nullable=True),
        sa.Column("mem_bytes", sa.BigInteger(), nullable=True),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=True),
        sa.Column("disk_free_bytes", sa.BigInteger(), nullable=True),
        sa.Column("kvm", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "runtimes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("docker_version", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            "ingress IN ('intranet', 'public')", name=op.f("ck_runner_hosts_ingress")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'online', 'offline', 'disabled')",
            name=op.f("ck_runner_hosts_status"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_runner_hosts_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_hosts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_hosts")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_runner_hosts_token_hash")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_runner_hosts_workspace_id_id")),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_runner_hosts_workspace_id")),
        schema="workspace",
    )
    op.create_table(
        "runner_templates",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("owner_instance_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("build_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("build_error", sa.Text(), nullable=True),
        sa.Column("build_log", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("image_ref", sa.Text(), nullable=True),
        sa.Column("image_digest", sa.Text(), nullable=True),
        sa.Column("image_bytes", sa.BigInteger(), nullable=True),
        sa.Column("build_seconds", sa.Float(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "build_status IN ('pending', 'building', 'ready', 'failed')",
            name=op.f("ck_runner_templates_build_status"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_runner_templates_status")
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64", name=op.f("ck_runner_templates_name")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_runner_templates_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_runner_templates_owner_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_templates_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_templates")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_runner_templates_workspace_id_id")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_runner_templates_workspace_id_name")
        ),
        schema="workspace",
    )
    op.create_table(
        "runner_environments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("host_id", sa.UUID(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("template_revision", sa.Integer(), nullable=True),
        sa.Column("owner_instance_id", sa.UUID(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("lifetime", sa.Text(), nullable=False),
        sa.Column("reuse_key", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'creating'"), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "setup",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("share", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("container_name", sa.Text(), nullable=False),
        sa.Column("volume_name", sa.Text(), nullable=False),
        sa.Column("network_name", sa.Text(), nullable=False),
        sa.Column("snapshot_key", sa.Text(), nullable=True),
        sa.Column("snapshot_bytes", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_seconds", sa.Float(), nullable=True),
        sa.Column("rebuild_seconds", sa.Float(), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("mem_bytes", sa.BigInteger(), nullable=True),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "egress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "lifetime IN ('once', 'session', 'persistent')",
            name=op.f("ck_runner_environments_lifetime"),
        ),
        sa.CheckConstraint(
            "share = 'none' OR project_id IS NOT NULL",
            name=op.f("ck_runner_environments_share_project"),
        ),
        sa.CheckConstraint(
            "share IN ('none', 'project')", name=op.f("ck_runner_environments_share")
        ),
        sa.CheckConstraint(
            "state IN ('creating', 'running', 'stopped', 'archived', 'pending_delete',"
            " 'deleted', 'failed')",
            name=op.f("ck_runner_environments_state"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_runner_environments_owner_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["workspace.projects.id"],
            name=op.f("fk_runner_environments_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["workspace.runner_templates.id"],
            name=op.f("fk_runner_environments_template_id_runner_templates"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            name=op.f("fk_runner_environments_workspace_id_host_id_runner_hosts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_environments")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_runner_environments_workspace_id_id")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_environments_owner_reuse",
        "runner_environments",
        ["owner_instance_id", "reuse_key"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_runner_environments_workspace_state",
        "runner_environments",
        ["workspace_id", "state"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "runner_delete_conditions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.UUID(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seconds", sa.Integer(), nullable=True),
        sa.Column("created_by_instance_id", sa.UUID(), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('run_finished', 'task_done', 'issue_closed', 'project_archived', 'at',"
            " 'idle', 'released')",
            name=op.f("ck_runner_delete_conditions_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_runner_delete_conditions_created_by_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["workspace.runner_environments.id"],
            name=op.f("fk_runner_delete_conditions_environment_id_runner_environments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_delete_conditions")),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_delete_conditions_environment",
        "runner_delete_conditions",
        ["environment_id"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_runner_delete_conditions_open",
        "runner_delete_conditions",
        ["kind", "ref_id"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("fired_at IS NULL"),
    )
    op.create_table(
        "runner_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("output", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "output_truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("wake", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'timeout', 'unknown',"
            " 'cancelled')",
            name=op.f("ck_runner_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["workspace.runner_environments.id"],
            name=op.f("fk_runner_runs_environment_id_runner_environments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_runner_runs_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_runs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_runs")),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_runs_environment_created",
        "runner_runs",
        ["environment_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_runner_runs_workspace_created",
        "runner_runs",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "runner_commands",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("host_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=True),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'done', 'failed', 'unknown', 'cancelled')",
            name=op.f("ck_runner_commands_status"),
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["workspace.runner_environments.id"],
            name=op.f("fk_runner_commands_environment_id_runner_environments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["workspace.runner_hosts.id"],
            name=op.f("fk_runner_commands_host_id_runner_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workspace.runner_runs.id"],
            name=op.f("fk_runner_commands_run_id_runner_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["workspace.runner_templates.id"],
            name=op.f("fk_runner_commands_template_id_runner_templates"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_runner_commands_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runner_commands")),
        schema="workspace",
    )
    op.create_index(
        "ix_runner_commands_host_status",
        "runner_commands",
        ["host_id", "status", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.add_column(
        "agent_instances",
        sa.Column("runner_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "agent_instances", sa.Column("runner_by", sa.UUID(), nullable=True), schema="workspace"
    )
    op.add_column(
        "agent_instances",
        sa.Column("runner_at", sa.DateTime(timezone=True), nullable=True),
        schema="workspace",
    )
    op.add_column(
        "agent_instances",
        sa.Column(
            "runner_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_agent_instances_runner_by_users"),
        "agent_instances",
        "users",
        ["runner_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.drop_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_wakeups_source"),
        "agent_wakeups",
        "source IN ('agent', 'admin', 'issue', 'runner')",
        schema="workspace",
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace.agent_wakeups WHERE source = 'runner'")
    op.drop_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_wakeups_source"),
        "agent_wakeups",
        "source IN ('agent', 'admin', 'issue')",
        schema="workspace",
    )
    op.drop_constraint(
        op.f("fk_agent_instances_runner_by_users"),
        "agent_instances",
        schema="workspace",
        type_="foreignkey",
    )
    op.drop_column("agent_instances", "runner_limits", schema="workspace")
    op.drop_column("agent_instances", "runner_at", schema="workspace")
    op.drop_column("agent_instances", "runner_by", schema="workspace")
    op.drop_column("agent_instances", "runner_enabled", schema="workspace")
    op.drop_index(
        "ix_runner_commands_host_status", table_name="runner_commands", schema="workspace"
    )
    op.drop_table("runner_commands", schema="workspace")
    op.drop_index("ix_runner_runs_workspace_created", table_name="runner_runs", schema="workspace")
    op.drop_index(
        "ix_runner_runs_environment_created", table_name="runner_runs", schema="workspace"
    )
    op.drop_table("runner_runs", schema="workspace")
    op.drop_index(
        "ix_runner_delete_conditions_open",
        table_name="runner_delete_conditions",
        schema="workspace",
        postgresql_where=sa.text("fired_at IS NULL"),
    )
    op.drop_index(
        "ix_runner_delete_conditions_environment",
        table_name="runner_delete_conditions",
        schema="workspace",
    )
    op.drop_table("runner_delete_conditions", schema="workspace")
    op.drop_index(
        "ix_runner_environments_workspace_state",
        table_name="runner_environments",
        schema="workspace",
    )
    op.drop_index(
        "ix_runner_environments_owner_reuse", table_name="runner_environments", schema="workspace"
    )
    op.drop_table("runner_environments", schema="workspace")
    op.drop_table("runner_templates", schema="workspace")
    op.drop_table("runner_hosts", schema="workspace")
