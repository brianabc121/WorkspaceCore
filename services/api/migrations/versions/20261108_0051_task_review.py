"""任务的“已完成待查看”（T-0163，docs/v2/HOME_PLAN.md 第 2 节）

- tasks.review_pending：智能体用 complete_task 完成时置真，owner / admin 确认结果、打回，
  或人改了完成状态时清掉。首页“任务进展”的“已完成待查看”就是它。
  已有的任务不回填（迁移前完成的按已查看处理）。
- 部分索引 ix_tasks_review_pending：只索引待查看的任务，首页按工作空间数它们。
- task_events.kind 增加 reviewed（确认了结果）。

downgrade 删除 reviewed 动态、恢复原来的种类约束，删除这一列和索引。

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "('opened', 'claimed', 'released', 'completed', 'reopened')"
NEW_KINDS = "('opened', 'claimed', 'released', 'completed', 'reopened', 'reviewed')"


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("review_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="workspace",
    )
    op.create_index(
        "ix_tasks_review_pending",
        "tasks",
        ["workspace_id"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("review_pending"),
    )
    op.drop_constraint(
        op.f("ck_task_events_kind"), "task_events", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_task_events_kind"), "task_events", f"kind IN {NEW_KINDS}", schema="workspace"
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace.task_events WHERE kind = 'reviewed'")
    op.drop_constraint(
        op.f("ck_task_events_kind"), "task_events", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_task_events_kind"), "task_events", f"kind IN {OLD_KINDS}", schema="workspace"
    )
    op.drop_index(
        "ix_tasks_review_pending",
        table_name="tasks",
        schema="workspace",
        postgresql_where=sa.text("review_pending"),
    )
    op.drop_column("tasks", "review_pending", schema="workspace")
