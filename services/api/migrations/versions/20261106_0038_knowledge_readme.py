"""项目与任务的 README（T-0122，docs/v2/AGENT_MESSAGING_PLAN.md 第 9 节）

- 笔记类型加 `readme`：项目 README 是项目知识库里置顶的一篇（每个项目知识库最多一篇），
  任务 README 在同一个知识库的“任务导览”文件夹里，每个任务最多一篇。
- knowledge_notes.task_id：任务 README 属于哪个任务。任务删除时置空（工作台同时把它放进回收站）。
- 已有的项目和任务由 Worker 补建（不在迁移里写笔记）。

downgrade：先删掉 readme 笔记（连同版本与链接，外键级联），再删列、恢复原来的类型约束。

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPES = (
    "'overview', 'brief', 'decision', 'rule', 'process', 'runbook', 'lesson', "
    "'review', 'preference', 'glossary', 'record', 'plan', 'risk', 'design', 'template', "
    "'correction'"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_knowledge_notes_type"), "knowledge_notes", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_knowledge_notes_type"),
        "knowledge_notes",
        f"type IN ({_TYPES}, 'readme')",
        schema="workspace",
    )
    op.add_column(
        "knowledge_notes", sa.Column("task_id", sa.UUID(), nullable=True), schema="workspace"
    )
    op.create_foreign_key(
        op.f("fk_knowledge_notes_task_id_tasks"),
        "knowledge_notes",
        "tasks",
        ["task_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="SET NULL",
    )
    # 只有 readme 才有 task_id
    op.create_check_constraint(
        op.f("ck_knowledge_notes_task_readme"),
        "knowledge_notes",
        "task_id IS NULL OR type = 'readme'",
        schema="workspace",
    )
    # 每个任务最多一篇 README；每个知识库最多一篇项目 README
    op.create_index(
        "uq_knowledge_notes_task_readme",
        "knowledge_notes",
        ["task_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("task_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_knowledge_notes_project_readme",
        "knowledge_notes",
        ["space_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text(
            "type = 'readme' AND task_id IS NULL AND folder = '' AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace.knowledge_notes WHERE type = 'readme'")
    op.drop_index("uq_knowledge_notes_project_readme", "knowledge_notes", schema="workspace")
    op.drop_index("uq_knowledge_notes_task_readme", "knowledge_notes", schema="workspace")
    op.drop_constraint(
        op.f("ck_knowledge_notes_task_readme"),
        "knowledge_notes",
        schema="workspace",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_knowledge_notes_task_id_tasks"),
        "knowledge_notes",
        schema="workspace",
        type_="foreignkey",
    )
    op.drop_column("knowledge_notes", "task_id", schema="workspace")
    op.drop_constraint(
        op.f("ck_knowledge_notes_type"), "knowledge_notes", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_knowledge_notes_type"),
        "knowledge_notes",
        f"type IN ({_TYPES})",
        schema="workspace",
    )
