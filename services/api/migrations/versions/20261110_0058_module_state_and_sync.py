"""应用中心：模块启停的状态版本与同步记录（T-0210，方案 docs/v2/app-center/）

扩展迁移，不改已有数据的含义：

- workspace_modules 加 state_version（目标状态版本，现有行为 0；实际启停一次加 1），
  与清单里的发布版本 version 无关；不回填历史操作；
- 新表 module_sync_attempts：启停或重试后执行模块钩子（例如新闻早报重写 TrendRadar 配置）的
  每一次尝试与结果。原因只记固定类别，不存异常正文；按工作空间删除级联。

downgrade 删掉新表和新列（只在空库或测试库上使用）。

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYNC_STATES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "unknown",
    "superseded",
    "not_required",
)
SYNC_REASONS = (
    "hook_failed",
    "config_render_failed",
    "timeout",
    "interrupted",
    "stale_revision",
    "busy",
    "result_unverified",
    "not_deployed",
    "application_not_active",
)
TRIGGERS = ("toggle", "retry")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column(
        "workspace_modules",
        sa.Column("state_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_workspace_modules_state_version"),
        "workspace_modules",
        "state_version >= 0",
        schema="workspace",
    )
    op.create_table(
        "module_sync_attempts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("module_key", sa.Text(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("target_enabled", sa.Boolean(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "module_key ~ '^[a-z][a-z0-9_]{1,31}$'",
            name=op.f("ck_module_sync_attempts_module_key_format"),
        ),
        sa.CheckConstraint(
            "state_version >= 0", name=op.f("ck_module_sync_attempts_state_version")
        ),
        sa.CheckConstraint(
            f"state IN {_in(SYNC_STATES)}", name=op.f("ck_module_sync_attempts_state")
        ),
        sa.CheckConstraint(
            f"reason IS NULL OR reason IN {_in(SYNC_REASONS)}",
            name=op.f("ck_module_sync_attempts_reason"),
        ),
        sa.CheckConstraint(
            f"trigger IN {_in(TRIGGERS)}", name=op.f("ck_module_sync_attempts_trigger")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_module_sync_attempts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["workspace.users.id"],
            name=op.f("fk_module_sync_attempts_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_module_sync_attempts")),
        schema="workspace",
    )
    op.create_index(
        op.f("ix_module_sync_attempts_workspace_id_module_key_created_at"),
        "module_sync_attempts",
        ["workspace_id", "module_key", "created_at"],
        unique=False,
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_module_sync_attempts_workspace_id_module_key_created_at"),
        table_name="module_sync_attempts",
        schema="workspace",
    )
    op.drop_table("module_sync_attempts", schema="workspace")
    op.drop_constraint(
        op.f("ck_workspace_modules_state_version"),
        "workspace_modules",
        schema="workspace",
        type_="check",
    )
    op.drop_column("workspace_modules", "state_version", schema="workspace")
