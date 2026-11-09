"""任务状态闭环（T-0166，docs/v2/pages-redesign 规范第 4.2、4.3 节）

- tasks.completion_seq：完成轮次。历史任务 0（迁移前完成的按“已查看”处理，不刷成全部未读）；
  之后每次未完成→完成加 1。迁移 0051 里 review_pending 为真的任务（智能体完成、还没人确认）
  记为第 1 轮，所有成员都还没查看。
- tasks.blocked_at / blocked_reason / blocked_by：明确阻塞（有人写了原因）；成对出现，
  原因不超过 2000 字。
- task_result_reads：每个成员查看过哪一轮结果（每用户每任务一行）；复合外键保证任务、成员关系与
  工作空间一致，成员离开或任务删除时随之删除。tasks 增加 (workspace_id, id) 唯一约束作外键目标。
- task_events.kind 增加 blocked、unblocked（标记 / 解除阻塞）。
- 删除 0051 的 review_pending 与它的部分索引（由完成轮次与个人查看记录取代；规范第 4.2 节：
  “查看”按人隔离，不再是 owner / admin 的一次性确认）。

downgrade：review_pending 按“已完成、轮次 > 0、没有任何人看过这一轮”回填；删除阻塞动态与新表、新列。

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "('opened', 'claimed', 'released', 'completed', 'reopened', 'reviewed')"
NEW_KINDS = (
    "('opened', 'claimed', 'released', 'completed', 'reopened', 'reviewed', 'blocked', 'unblocked')"
)


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("completion_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema="workspace",
    )
    op.add_column("tasks", sa.Column("blocked_at", sa.DateTime(timezone=True)), schema="workspace")
    op.add_column("tasks", sa.Column("blocked_reason", sa.Text()), schema="workspace")
    op.add_column(
        "tasks", sa.Column("blocked_by", postgresql.UUID(as_uuid=True)), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_tasks_blocked_by_users"),
        "tasks",
        "users",
        ["blocked_by"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_tasks_blocked_pair"),
        "tasks",
        "(blocked_at IS NULL) = (blocked_reason IS NULL)",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_tasks_blocked_reason_length"),
        "tasks",
        "char_length(blocked_reason) <= 2000",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_tasks_completion_seq"), "tasks", "completion_seq >= 0", schema="workspace"
    )
    op.create_index(
        "ix_tasks_blocked",
        "tasks",
        ["workspace_id"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("blocked_at IS NOT NULL"),
    )
    op.create_unique_constraint(
        op.f("uq_tasks_workspace_id_id"), "tasks", ["workspace_id", "id"], schema="workspace"
    )
    # 智能体完成、还没人确认的任务：第 1 轮，所有人都还没查看
    op.execute("UPDATE workspace.tasks SET completion_seq = 1 WHERE review_pending")
    op.drop_index("ix_tasks_review_pending", table_name="tasks", schema="workspace")
    op.drop_column("tasks", "review_pending", schema="workspace")

    op.create_table(
        "task_result_reads",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seen_completion_seq", sa.Integer(), nullable=False),
        sa.Column(
            "viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "seen_completion_seq >= 0", name=op.f("ck_task_result_reads_seen_completion_seq")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["workspace.tasks.workspace_id", "workspace.tasks.id"],
            name=op.f("fk_task_result_reads_workspace_id_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            [
                "workspace.workspace_members.workspace_id",
                "workspace.workspace_members.user_id",
            ],
            name=op.f("fk_task_result_reads_workspace_id_user_id_workspace_members"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "user_id", name=op.f("pk_task_result_reads")),
        schema="workspace",
    )
    op.create_index(
        "ix_task_result_reads_user",
        "task_result_reads",
        ["workspace_id", "user_id"],
        unique=False,
        schema="workspace",
    )

    op.drop_constraint(
        op.f("ck_task_events_kind"), "task_events", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_task_events_kind"), "task_events", f"kind IN {NEW_KINDS}", schema="workspace"
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace.task_events WHERE kind IN ('blocked', 'unblocked')")
    op.drop_constraint(
        op.f("ck_task_events_kind"), "task_events", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_task_events_kind"), "task_events", f"kind IN {OLD_KINDS}", schema="workspace"
    )
    op.add_column(
        "tasks",
        sa.Column("review_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="workspace",
    )
    op.execute(
        "UPDATE workspace.tasks t SET review_pending = true "
        "WHERE t.done AND t.completion_seq > 0 AND NOT EXISTS ("
        "SELECT 1 FROM workspace.task_result_reads r "
        "WHERE r.task_id = t.id AND r.seen_completion_seq >= t.completion_seq)"
    )
    op.create_index(
        "ix_tasks_review_pending",
        "tasks",
        ["workspace_id"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("review_pending"),
    )
    op.drop_index("ix_task_result_reads_user", table_name="task_result_reads", schema="workspace")
    op.drop_table("task_result_reads", schema="workspace")
    op.drop_constraint(
        op.f("uq_tasks_workspace_id_id"), "tasks", schema="workspace", type_="unique"
    )
    op.drop_index("ix_tasks_blocked", table_name="tasks", schema="workspace")
    op.drop_constraint(op.f("ck_tasks_completion_seq"), "tasks", schema="workspace", type_="check")
    op.drop_constraint(
        op.f("ck_tasks_blocked_reason_length"), "tasks", schema="workspace", type_="check"
    )
    op.drop_constraint(op.f("ck_tasks_blocked_pair"), "tasks", schema="workspace", type_="check")
    op.drop_constraint(
        op.f("fk_tasks_blocked_by_users"), "tasks", schema="workspace", type_="foreignkey"
    )
    op.drop_column("tasks", "blocked_by", schema="workspace")
    op.drop_column("tasks", "blocked_reason", schema="workspace")
    op.drop_column("tasks", "blocked_at", schema="workspace")
    op.drop_column("tasks", "completion_seq", schema="workspace")
