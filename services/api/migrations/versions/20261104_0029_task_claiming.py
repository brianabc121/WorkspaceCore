"""任务接取（T-0111，docs/v2/AGENT_OPS_PLAN.md 第 3 节）

- tasks.claim_mode：assigned 由管理员指派 / open 可自由接取；claim_scope：all 全部智能体 /
  project 只限项目成员；claim_opened_by：设为自由接取的人（用他的 IM 账号通知智能体）；
  部分索引 ix_tasks_claimable 找能领取的任务；
- task_events：任务的动态（设为自由接取、领取、放弃、完成、打回），note 是原因或完成说明；
- ai_tool_executions.risk_level 增加 direct
  （领取 / 放弃 / 完成任务直接执行，人类 2026-11-04 决定）。

已有任务都是 assigned（与之前一样由管理员指派）。downgrade 删除这些列与表（任务的动态随之删除）；
已经有 direct 的执行记录时拒绝降级（收紧约束会让这些记录不合法，不改写历史，由人决定）。

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RISK_NEW = "risk_level IN ('read', 'write', 'critical', 'knowledge', 'direct')"
_RISK_OLD = "risk_level IN ('read', 'write', 'critical', 'knowledge')"


def _risk_check(condition: str) -> None:
    op.drop_constraint(
        op.f("ck_ai_tool_executions_risk_level"),
        "ai_tool_executions",
        type_="check",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_ai_tool_executions_risk_level"),
        "ai_tool_executions",
        condition,
        schema="workspace",
    )


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("claim_mode", sa.Text(), server_default=sa.text("'assigned'"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "tasks",
        sa.Column("claim_scope", sa.Text(), server_default=sa.text("'all'"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "tasks", sa.Column("claim_opened_by", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_check_constraint(
        op.f("ck_tasks_claim_mode"),
        "tasks",
        "claim_mode IN ('assigned', 'open')",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_tasks_claim_scope"),
        "tasks",
        "claim_scope IN ('all', 'project')",
        schema="workspace",
    )
    op.create_foreign_key(
        op.f("fk_tasks_claim_opened_by_users"),
        "tasks",
        "users",
        ["claim_opened_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tasks_claimable",
        "tasks",
        ["workspace_id"],
        schema="workspace",
        postgresql_where=sa.text(
            "claim_mode = 'open' AND NOT done "
            "AND assignee_user_id IS NULL AND assignee_instance_id IS NULL"
        ),
    )
    op.create_table(
        "task_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('opened', 'claimed', 'released', 'completed', 'reopened')",
            name=op.f("ck_task_events_kind"),
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'instance', 'system')", name=op.f("ck_task_events_actor_kind")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_task_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace.tasks.id"],
            name=op.f("fk_task_events_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            name=op.f("fk_task_events_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_task_events_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_events")),
        schema="workspace",
    )
    op.create_index(
        "ix_task_events_task_created",
        "task_events",
        ["task_id", "created_at"],
        schema="workspace",
    )
    _risk_check(_RISK_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    direct = bind.execute(
        sa.text("SELECT count(*) FROM workspace.ai_tool_executions WHERE risk_level = 'direct'")
    ).scalar_one()
    if direct:
        raise RuntimeError(
            "已有 direct 的工具执行记录（领取 / 放弃 / 完成任务）："
            "降级会让这些记录不合法，请人工处理"
        )
    _risk_check(_RISK_OLD)
    op.drop_index("ix_task_events_task_created", table_name="task_events", schema="workspace")
    op.drop_table("task_events", schema="workspace")
    op.drop_index("ix_tasks_claimable", table_name="tasks", schema="workspace")
    op.drop_constraint(
        op.f("fk_tasks_claim_opened_by_users"), "tasks", type_="foreignkey", schema="workspace"
    )
    op.drop_constraint(op.f("ck_tasks_claim_scope"), "tasks", type_="check", schema="workspace")
    op.drop_constraint(op.f("ck_tasks_claim_mode"), "tasks", type_="check", schema="workspace")
    for column in ("claim_opened_by", "claim_scope", "claim_mode"):
        op.drop_column("tasks", column, schema="workspace")
