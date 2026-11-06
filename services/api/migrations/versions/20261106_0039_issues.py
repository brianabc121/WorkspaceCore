"""项目里的 Issues（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 第 8 节）

- issues（按项目编号）、issue_assignees、issue_tasks、issue_comments、issue_events、
  issue_mentions、issue_reads；
- projects.issue_notices：Issue 新开 / 关闭时在项目群里发一行提示（默认关闭）；
- agent_wakeups.source 加 'issue'：被提到、被指派或自己的 Issue 有新动态时合并唤醒（方案 8.5）；
- 全局搜索收录 Issue 的标题和正文（resource_type = 'project_issue'，触发器维护，
  search_rebuild 一并重建）。

新建的都是新表、新列；downgrade 删除即可回退（先删 issue 唤醒再恢复约束）。

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_ONE = "(user_id IS NULL) <> (instance_id IS NULL)"

_SEARCH_INDEX = """
CREATE FUNCTION workspace.search_index_issue(i workspace.issues) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_context text;
BEGIN
    IF i.deleted_at IS NOT NULL THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'project_issue' AND resource_id = i.id;
        RETURN;
    END IF;
    SELECT p.title INTO v_context FROM workspace.projects p WHERE p.id = i.project_id;
    INSERT INTO workspace.search_documents (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        i.workspace_id, 'project_issue', i.id, 'local', '#' || i.number || ' ' || i.title,
        btrim(array_to_string(i.labels, ' ') || ' ' || i.body),
        v_context, NULL, NULL, '{"scope": "workspace"}', i.updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, context = EXCLUDED.context,
        source_updated_at = EXCLUDED.source_updated_at, indexed_at = now();
END;
$$;
"""

_SEARCH_TRIGGER = """
CREATE FUNCTION workspace.search_trigger_issue() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'project_issue' AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_issue(NEW);
    RETURN NEW;
END;
$$;
"""

_SEARCH_HOOK = """
CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.issues
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_issue();
"""

_REBUILD_BODY = """
CREATE OR REPLACE FUNCTION workspace.search_rebuild() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_count integer;
BEGIN
    DELETE FROM workspace.search_documents;
    PERFORM workspace.search_index_external(r) FROM workspace.external_resources r ORDER BY r.id;
    -- 先索引项目，任务的 context（项目名）才能取到
    PERFORM workspace.search_index_project(p) FROM workspace.projects p ORDER BY p.id;
    PERFORM workspace.search_index_task(t) FROM workspace.tasks t ORDER BY t.id;
    PERFORM workspace.search_index_bookmark(b) FROM workspace.bookmarks b;
    PERFORM workspace.search_index_event(e) FROM workspace.calendar_events e;
    PERFORM workspace.search_index_note(n) FROM workspace.knowledge_notes n
        WHERE n.deleted_at IS NULL ORDER BY n.id;{issues}
    SELECT count(*) INTO v_count FROM workspace.search_documents;
    RETURN v_count;
END;
$$;
"""
_REBUILD = _REBUILD_BODY.replace(
    "{issues}",
    "\n    PERFORM workspace.search_index_issue(i) FROM workspace.issues i\n"
    "        WHERE i.deleted_at IS NULL ORDER BY i.id;",
)
_OLD_REBUILD = _REBUILD_BODY.replace("{issues}", "")


def _actor_columns(prefix: str = "") -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(f"{prefix}user_id", sa.UUID(), nullable=True),
        sa.Column(f"{prefix}instance_id", sa.UUID(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "issues",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("kind", sa.Text(), server_default=sa.text("'other'"), nullable=False),
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("duplicate_of", sa.UUID(), nullable=True),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=True),
        sa.Column("author_instance_id", sa.UUID(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_streak", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("comment_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "last_activity_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "kind IN ('question', 'proposal', 'bug', 'contract', 'handoff', 'review', 'blocker', "
            "'other')",
            name=op.f("ck_issues_kind"),
        ),
        sa.CheckConstraint("state IN ('open', 'closed')", name=op.f("ck_issues_state")),
        sa.CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('resolved', 'wontfix', 'duplicate')",
            name=op.f("ck_issues_close_reason"),
        ),
        sa.CheckConstraint(
            "(state = 'closed') = (close_reason IS NOT NULL)", name=op.f("ck_issues_closed")
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200", name=op.f("ck_issues_title")),
        sa.CheckConstraint("char_length(body) <= 20000", name=op.f("ck_issues_body")),
        sa.CheckConstraint("cardinality(labels) <= 10", name=op.f("ck_issues_labels")),
        sa.CheckConstraint(
            "(author_user_id IS NULL) OR (author_instance_id IS NULL)",
            name=op.f("ck_issues_one_author"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            ondelete="CASCADE",
            name=op.f("fk_issues_workspace_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["workspace.projects.id"],
            ondelete="CASCADE",
            name=op.f("fk_issues_project_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of"],
            ["workspace.issues.id"],
            ondelete="SET NULL",
            name=op.f("fk_issues_duplicate_of_issues"),
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["workspace.users.id"],
            ondelete="SET NULL",
            name=op.f("fk_issues_author_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["author_instance_id"],
            ["workspace.agent_instances.id"],
            ondelete="SET NULL",
            name=op.f("fk_issues_author_instance_id_agent_instances"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issues")),
        sa.UniqueConstraint("project_id", "number", name=op.f("uq_issues_project_id_number")),
        schema="workspace",
    )
    op.create_index(
        "ix_issues_project_state", "issues", ["project_id", "state", "number"], schema="workspace"
    )
    op.create_index(
        "ix_issues_workspace_activity",
        "issues",
        ["workspace_id", "last_activity_at"],
        schema="workspace",
    )

    def actor_fks(table: str) -> list[sa.ForeignKeyConstraint]:
        return [
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["workspace.users.id"],
                ondelete="CASCADE",
                name=op.f(f"fk_{table}_user_id_users"),
            ),
            sa.ForeignKeyConstraint(
                ["instance_id"],
                ["workspace.agent_instances.id"],
                ondelete="CASCADE",
                name=op.f(f"fk_{table}_instance_id_agent_instances"),
            ),
        ]

    def issue_fk(table: str) -> sa.ForeignKeyConstraint:
        return sa.ForeignKeyConstraint(
            ["issue_id"],
            ["workspace.issues.id"],
            ondelete="CASCADE",
            name=op.f(f"fk_{table}_issue_id_issues"),
        )

    op.create_table(
        "issue_assignees",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        *_actor_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(_ONE, name=op.f("ck_issue_assignees_one")),
        issue_fk("issue_assignees"),
        *actor_fks("issue_assignees"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_assignees")),
        schema="workspace",
    )
    for kind in ("user", "instance"):
        op.create_index(
            f"uq_issue_assignees_{kind}",
            "issue_assignees",
            ["issue_id", f"{kind}_id"],
            unique=True,
            schema="workspace",
            postgresql_where=sa.text(f"{kind}_id IS NOT NULL"),
        )
    op.create_index(
        "ix_issue_assignees_instance", "issue_assignees", ["instance_id"], schema="workspace"
    )

    op.create_table(
        "issue_tasks",
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        issue_fk("issue_tasks"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace.tasks.id"],
            ondelete="CASCADE",
            name=op.f("fk_issue_tasks_task_id_tasks"),
        ),
        sa.PrimaryKeyConstraint("issue_id", "task_id", name=op.f("pk_issue_tasks")),
        schema="workspace",
    )
    op.create_index("ix_issue_tasks_task", "issue_tasks", ["task_id"], schema="workspace")

    op.create_table(
        "issue_comments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("reply_to", sa.UUID(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=True),
        sa.Column("author_instance_id", sa.UUID(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 10000", name=op.f("ck_issue_comments_body")
        ),
        sa.CheckConstraint(
            "(author_user_id IS NULL) OR (author_instance_id IS NULL)",
            name=op.f("ck_issue_comments_one_author"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            ondelete="CASCADE",
            name=op.f("fk_issue_comments_workspace_id_workspaces"),
        ),
        issue_fk("issue_comments"),
        sa.ForeignKeyConstraint(
            ["reply_to"],
            ["workspace.issue_comments.id"],
            ondelete="SET NULL",
            name=op.f("fk_issue_comments_reply_to_issue_comments"),
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["workspace.users.id"],
            ondelete="SET NULL",
            name=op.f("fk_issue_comments_author_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["author_instance_id"],
            ["workspace.agent_instances.id"],
            ondelete="SET NULL",
            name=op.f("fk_issue_comments_author_instance_id_agent_instances"),
        ),
        sa.ForeignKeyConstraint(
            ["hidden_by"],
            ["workspace.users.id"],
            ondelete="SET NULL",
            name=op.f("fk_issue_comments_hidden_by_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_comments")),
        schema="workspace",
    )
    op.create_index(
        "ix_issue_comments_issue_created",
        "issue_comments",
        ["issue_id", "created_at"],
        schema="workspace",
    )

    op.create_table(
        "issue_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "kind IN ('opened', 'closed', 'reopened', 'assigned', 'unassigned', 'labeled', "
            "'unlabeled', 'linked', 'unlinked', 'retitled', 'locked', 'unlocked', 'mentioned')",
            name=op.f("ck_issue_events_kind"),
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'instance', 'system')", name=op.f("ck_issue_events_actor_kind")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            ondelete="CASCADE",
            name=op.f("fk_issue_events_workspace_id_workspaces"),
        ),
        issue_fk("issue_events"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["workspace.users.id"],
            ondelete="SET NULL",
            name=op.f("fk_issue_events_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            ondelete="SET NULL",
            name=op.f("fk_issue_events_instance_id_agent_instances"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_events")),
        schema="workspace",
    )
    op.create_index(
        "ix_issue_events_issue_created",
        "issue_events",
        ["issue_id", "created_at"],
        schema="workspace",
    )

    op.create_table(
        "issue_mentions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("comment_id", sa.UUID(), nullable=True),
        *_actor_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(_ONE, name=op.f("ck_issue_mentions_one")),
        issue_fk("issue_mentions"),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["workspace.issue_comments.id"],
            ondelete="CASCADE",
            name=op.f("fk_issue_mentions_comment_id_issue_comments"),
        ),
        *actor_fks("issue_mentions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_mentions")),
        schema="workspace",
    )
    op.create_index(
        "ix_issue_mentions_instance",
        "issue_mentions",
        ["instance_id", "created_at"],
        schema="workspace",
    )
    op.create_index(
        "ix_issue_mentions_user", "issue_mentions", ["user_id", "created_at"], schema="workspace"
    )

    op.create_table(
        "issue_reads",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        *_actor_columns(),
        sa.Column("read_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(_ONE, name=op.f("ck_issue_reads_one")),
        issue_fk("issue_reads"),
        *actor_fks("issue_reads"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_reads")),
        schema="workspace",
    )
    for kind in ("user", "instance"):
        op.create_index(
            f"uq_issue_reads_{kind}",
            "issue_reads",
            ["issue_id", f"{kind}_id"],
            unique=True,
            schema="workspace",
            postgresql_where=sa.text(f"{kind}_id IS NOT NULL"),
        )

    op.add_column(
        "projects",
        sa.Column("issue_notices", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="workspace",
    )
    op.drop_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_wakeups_source"),
        "agent_wakeups",
        "source IN ('agent', 'admin', 'issue')",
        schema="workspace",
    )
    for statement in (_SEARCH_INDEX, _SEARCH_TRIGGER, _SEARCH_HOOK):
        op.execute(statement)
    op.execute(_REBUILD)


def downgrade() -> None:
    op.execute(_OLD_REBUILD)
    op.execute("DROP TRIGGER IF EXISTS search_index ON workspace.issues")
    op.execute("DROP FUNCTION IF EXISTS workspace.search_trigger_issue()")
    op.execute("DROP FUNCTION IF EXISTS workspace.search_index_issue(workspace.issues)")
    op.execute("DELETE FROM workspace.search_documents WHERE resource_type = 'project_issue'")
    op.execute("DELETE FROM workspace.agent_wakeups WHERE source = 'issue'")
    op.drop_constraint(
        op.f("ck_agent_wakeups_source"), "agent_wakeups", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_wakeups_source"),
        "agent_wakeups",
        "source IN ('agent', 'admin')",
        schema="workspace",
    )
    op.drop_column("projects", "issue_notices", schema="workspace")
    for table in (
        "issue_reads",
        "issue_mentions",
        "issue_events",
        "issue_comments",
        "issue_tasks",
        "issue_assignees",
        "issues",
    ):
        op.drop_table(table, schema="workspace")
