"""工作台自己的项目与任务，移除 Vikunja（T-0082）

人类 2026-11-02 决定彻底移除 Vikunja，项目与任务由工作台自己管理
（单人版，没有负责人、指派、评论）。docs/decisions/2026-11-02-remove-vikunja.md。

upgrade 的顺序有讲究：
1. 建 projects、tasks；
2. 把之前从 Vikunja 同步来的项目、任务（external_resources 里 provider = vikunja 的行）
   原样转过来，**ID 不变**，所以收藏的关联、AI 审批记录里的 task_id 照旧有效。
   找不到所属项目的任务放进每个工作空间一个「未分组任务」项目；
3. resource_links 的目标改成项目或任务：去掉指向 external_resources 的外键，
   删掉指向已不存在对象的关联，项目、任务删除时由触发器删掉指向它的关联；
4. 删掉 Vikunja 集成（级联删除它的投影、凭证、同步游标）、Webhook 收件箱、
   integrations.webhook_secret_ref、保存的 Vikunja 登录账号；
5. 搜索索引改为由 projects、tasks 的触发器维护（provider = local），最后整体重建一次。

downgrade 只恢复表结构（webhook_inbox、外键、旧的搜索函数等），**不恢复数据**：
工作台里的项目、任务会被删除，也不会重新出现 Vikunja 集成。
之前的数据仍在 Vikunja 自己的数据卷里（升级时保留）。

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-02
"""

import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FALLBACK_TITLE = "未分组任务"
FALLBACK_DESCRIPTION = "从 Vikunja 转换时找不到所属项目的任务"

_VIKUNJA = """
FROM workspace.external_resources r
JOIN workspace.integrations i ON i.id = r.integration_id
WHERE i.provider = 'vikunja' AND r.deleted_at IS NULL
"""

# 下面的 SQL 只拼接本文件里的常量，没有外部输入
_CONVERT = [
    # 项目：只有标题与是否归档
    f"""
INSERT INTO workspace.projects (id, workspace_id, title, archived, created_at, updated_at)
SELECT r.id, r.workspace_id, r.title,
       coalesce((r.metadata_json ->> 'archived')::boolean, false),
       coalesce(r.source_updated_at, r.synced_at), coalesce(r.source_updated_at, r.synced_at)
{_VIKUNJA} AND r.resource_type = 'project'
""",
    # 找不到所属项目的任务：每个工作空间建一个「未分组任务」项目
    f"""
INSERT INTO workspace.projects (workspace_id, title, description)
SELECT DISTINCT r.workspace_id, '{FALLBACK_TITLE}', '{FALLBACK_DESCRIPTION}'
{_VIKUNJA} AND r.resource_type = 'task'
  AND NOT EXISTS (
      SELECT 1 FROM workspace.projects p
      WHERE p.workspace_id = r.workspace_id
        AND p.id::text = r.metadata_json ->> 'project_resource_id'
  )
""",  # noqa: S608
    f"""
INSERT INTO workspace.tasks (
    id, workspace_id, project_id, title, description, done, done_at, priority, due_at, labels,
    created_at, updated_at
)
SELECT r.id, r.workspace_id,
       coalesce(p.id, (
           SELECT f.id FROM workspace.projects f
           WHERE f.workspace_id = r.workspace_id
             AND f.title = '{FALLBACK_TITLE}' AND f.description = '{FALLBACK_DESCRIPTION}'
           ORDER BY f.created_at LIMIT 1
       )),
       r.title,
       coalesce(r.metadata_json ->> 'description', ''),
       coalesce((r.metadata_json ->> 'done')::boolean, false),
       nullif(r.metadata_json ->> 'done_at', '')::timestamptz,
       least(greatest(coalesce((r.metadata_json ->> 'priority')::integer, 0), 0), 5),
       nullif(r.metadata_json ->> 'due_at', '')::timestamptz,
       coalesce(ARRAY(
           SELECT jsonb_array_elements_text(
               CASE WHEN jsonb_typeof(r.metadata_json -> 'labels') = 'array'
                    THEN r.metadata_json -> 'labels' ELSE '[]'::jsonb END
           )
       ), '{{}}'::text[]),
       coalesce(r.source_updated_at, r.synced_at), coalesce(r.source_updated_at, r.synced_at)
{
        _VIKUNJA.replace(
            "WHERE",
            "LEFT JOIN workspace.projects p ON p.workspace_id = r.workspace_id"
            " AND p.id::text = r.metadata_json ->> 'project_resource_id'\nWHERE",
        )
    }
  AND r.resource_type = 'task'
""",  # noqa: S608
]

# 收藏的关联：目标可能是项目也可能是任务，没有外键，删除时由触发器清理
_LINKS = [
    "ALTER TABLE workspace.resource_links "
    "DROP CONSTRAINT fk_resource_links_target_resource_id_external_resources",
    """
DELETE FROM workspace.resource_links l
WHERE NOT EXISTS (SELECT 1 FROM workspace.projects p WHERE p.id = l.target_resource_id)
  AND NOT EXISTS (SELECT 1 FROM workspace.tasks t WHERE t.id = l.target_resource_id)
""",
    """
CREATE FUNCTION workspace.delete_resource_links() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM workspace.resource_links WHERE target_resource_id = OLD.id;
    RETURN OLD;
END;
$$
""",
    "CREATE TRIGGER delete_resource_links AFTER DELETE ON workspace.projects "
    "FOR EACH ROW EXECUTE FUNCTION workspace.delete_resource_links()",
    "CREATE TRIGGER delete_resource_links AFTER DELETE ON workspace.tasks "
    "FOR EACH ROW EXECUTE FUNCTION workspace.delete_resource_links()",
]

_REMOVE_VIKUNJA = [
    # 级联删除它的投影（external_resources）、凭证（integration_accounts）、同步游标、Webhook 收件箱
    "DELETE FROM workspace.integrations WHERE provider = 'vikunja'",
    "DROP INDEX workspace.ix_webhook_pending",
    "DROP TABLE workspace.webhook_inbox",
    "ALTER TABLE workspace.integrations DROP COLUMN webhook_secret_ref",
    "DELETE FROM workspace.app_logins WHERE app = 'vikunja'",
    "ALTER TABLE workspace.app_logins DROP CONSTRAINT ck_app_logins_app",
    "ALTER TABLE workspace.app_logins ADD CONSTRAINT ck_app_logins_app CHECK (app = 'n8n')",
]

# 搜索索引：external_resources 只剩 GitHub；项目、任务由各自的触发器维护
_SEARCH = """
CREATE OR REPLACE FUNCTION workspace.search_index_external(r workspace.external_resources)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_body text := '';
    v_context text;
BEGIN
    IF r.deleted_at IS NOT NULL OR r.resource_type NOT IN ('repo', 'issue', 'pull') THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = r.resource_type AND resource_id = r.id;
        RETURN;
    END IF;
    IF r.resource_type = 'repo' THEN
        v_body := coalesce(r.metadata_json ->> 'description', '');
    ELSE
        v_body := coalesce((
            SELECT string_agg(label, ' ')
            FROM jsonb_array_elements_text(
                coalesce(r.metadata_json -> 'labels', '[]'::jsonb)
            ) AS label
        ), '');
        v_context := r.metadata_json ->> 'repo_full_name';
    END IF;
    INSERT INTO workspace.search_documents AS d (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        r.workspace_id, r.resource_type, r.id, 'github', r.title, btrim(v_body), v_context,
        nullif(r.source_url, ''), NULL, '{"scope": "workspace"}', r.source_updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, context = EXCLUDED.context,
        source_url = EXCLUDED.source_url, source_updated_at = EXCLUDED.source_updated_at,
        indexed_at = now();
END;
$$;

CREATE OR REPLACE FUNCTION workspace.search_trigger_external() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = OLD.resource_type AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_external(NEW);
    RETURN NEW;
END;
$$;

CREATE FUNCTION workspace.search_index_project(p workspace.projects) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO workspace.search_documents (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        p.workspace_id, 'project', p.id, 'local', p.title, p.description, NULL,
        NULL, NULL, '{"scope": "workspace"}', p.updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body,
        source_updated_at = EXCLUDED.source_updated_at, indexed_at = now();
    -- 项目改名：它的任务的 context 跟着改
    UPDATE workspace.search_documents d SET context = p.title
    FROM workspace.tasks t
    WHERE d.resource_type = 'task' AND d.resource_id = t.id AND t.project_id = p.id
      AND d.context IS DISTINCT FROM p.title;
END;
$$;

CREATE FUNCTION workspace.search_index_task(t workspace.tasks) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_context text;
BEGIN
    SELECT p.title INTO v_context FROM workspace.projects p WHERE p.id = t.project_id;
    INSERT INTO workspace.search_documents (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        t.workspace_id, 'task', t.id, 'local', t.title,
        btrim(t.description || ' ' || array_to_string(t.labels, ' ')), v_context,
        NULL, NULL, '{"scope": "workspace"}', t.updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, context = EXCLUDED.context,
        source_updated_at = EXCLUDED.source_updated_at, indexed_at = now();
END;
$$;

CREATE FUNCTION workspace.search_trigger_project() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'project' AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_project(NEW);
    RETURN NEW;
END;
$$;

CREATE FUNCTION workspace.search_trigger_task() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'task' AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_task(NEW);
    RETURN NEW;
END;
$$;

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
    SELECT count(*) INTO v_count FROM workspace.search_documents;
    RETURN v_count;
END;
$$;

CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.projects
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_project();
CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.tasks
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_task();
"""

# downgrade：恢复迁移 0008 的搜索函数（包含 Vikunja 的分支）
_OLD_SEARCH = """
CREATE OR REPLACE FUNCTION workspace.search_index_external(r workspace.external_resources)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_body text := '';
    v_context text;
    v_provider text;
BEGIN
    IF r.deleted_at IS NOT NULL
       OR r.resource_type NOT IN ('task', 'project', 'repo', 'issue', 'pull') THEN
        DELETE FROM workspace.search_documents WHERE resource_id = r.id;
        RETURN;
    END IF;
    v_provider := CASE WHEN r.resource_type IN ('task', 'project') THEN 'vikunja' ELSE 'github' END;
    IF r.resource_type IN ('task', 'repo') THEN
        v_body := coalesce(r.metadata_json ->> 'description', '');
    END IF;
    IF r.resource_type IN ('task', 'issue', 'pull') THEN
        v_body := v_body || ' ' || coalesce((
            SELECT string_agg(label, ' ')
            FROM jsonb_array_elements_text(
                coalesce(r.metadata_json -> 'labels', '[]'::jsonb)
            ) AS label
        ), '');
    END IF;
    IF r.resource_type = 'task' THEN
        SELECT p.title INTO v_context FROM workspace.external_resources p
        WHERE p.id::text = r.metadata_json ->> 'project_resource_id' AND p.deleted_at IS NULL;
    ELSIF r.resource_type IN ('issue', 'pull') THEN
        v_context := r.metadata_json ->> 'repo_full_name';
    END IF;
    INSERT INTO workspace.search_documents AS d (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        r.workspace_id, r.resource_type, r.id, v_provider, r.title, btrim(v_body), v_context,
        nullif(r.source_url, ''), NULL, '{"scope": "workspace"}', r.source_updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, context = EXCLUDED.context,
        source_url = EXCLUDED.source_url, source_updated_at = EXCLUDED.source_updated_at,
        indexed_at = now();
    IF r.resource_type = 'project' THEN
        UPDATE workspace.search_documents d SET context = r.title
        FROM workspace.external_resources t
        WHERE d.resource_type = 'task' AND d.resource_id = t.id
          AND t.metadata_json ->> 'project_resource_id' = r.id::text;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION workspace.search_trigger_external() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents WHERE resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_external(NEW);
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION workspace.search_rebuild() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_count integer;
BEGIN
    DELETE FROM workspace.search_documents;
    PERFORM workspace.search_index_external(r) FROM workspace.external_resources r
    ORDER BY (r.resource_type <> 'project'), r.id;
    PERFORM workspace.search_index_bookmark(b) FROM workspace.bookmarks b;
    PERFORM workspace.search_index_event(e) FROM workspace.calendar_events e;
    SELECT count(*) INTO v_count FROM workspace.search_documents;
    RETURN v_count;
END;
$$;
"""


def _split(sql: str) -> list[str]:
    """按行首的 CREATE 拆成单条语句（asyncpg 一次只能执行一条）。"""
    return [s.strip() for s in re.split(r"\n(?=CREATE )", sql) if s.strip()]


def _timestamps() -> list[sa.Column[Any]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_projects_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_projects_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_projects_workspace_id_id")),
        schema="workspace",
    )
    op.create_index(
        "ix_projects_workspace_archived",
        "projects",
        ["workspace_id", "archived"],
        schema="workspace",
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("done", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.CheckConstraint("priority BETWEEN 0 AND 5", name=op.f("ck_tasks_priority")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_tasks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["workspace.projects.workspace_id", "workspace.projects.id"],
            name=op.f("fk_tasks_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_tasks_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        schema="workspace",
    )
    op.create_index(
        "ix_tasks_workspace_done_due",
        "tasks",
        ["workspace_id", "done", "due_at"],
        schema="workspace",
    )
    op.create_index("ix_tasks_project", "tasks", ["project_id"], schema="workspace")

    for statement in (*_CONVERT, *_LINKS, *_REMOVE_VIKUNJA, *_split(_SEARCH)):
        op.execute(statement)
    op.execute("SELECT workspace.search_rebuild()")


def downgrade() -> None:
    for table in ("projects", "tasks"):
        op.execute(f"DROP TRIGGER IF EXISTS search_index ON workspace.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS delete_resource_links ON workspace.{table}")
    for function in (
        "search_trigger_project()",
        "search_trigger_task()",
        "search_index_project(workspace.projects)",
        "search_index_task(workspace.tasks)",
        "delete_resource_links()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS workspace.{function}")
    for statement in _split(_OLD_SEARCH):
        op.execute(statement)

    op.execute("DELETE FROM workspace.app_logins WHERE app NOT IN ('vikunja', 'n8n')")
    op.execute("ALTER TABLE workspace.app_logins DROP CONSTRAINT ck_app_logins_app")
    op.execute(
        "ALTER TABLE workspace.app_logins ADD CONSTRAINT ck_app_logins_app "
        "CHECK (app IN ('vikunja', 'n8n'))"
    )
    op.add_column(
        "integrations",
        sa.Column("webhook_secret_ref", sa.Text(), nullable=True),
        schema="workspace",
    )
    op.create_table(
        "webhook_inbox",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["workspace.integrations.id"],
            name=op.f("fk_webhook_inbox_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_inbox")),
        sa.UniqueConstraint(
            "integration_id", "event_key", name=op.f("uq_webhook_inbox_integration_id_event_key")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_webhook_pending",
        "webhook_inbox",
        ["next_retry_at"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("status IN ('pending', 'retry', 'processing')"),
    )

    # 关联只能指向 external_resources：指向工作台项目、任务的关联随它们一起删除
    op.execute(
        "DELETE FROM workspace.resource_links l WHERE NOT EXISTS "
        "(SELECT 1 FROM workspace.external_resources r WHERE r.id = l.target_resource_id)"
    )
    op.create_foreign_key(
        op.f("fk_resource_links_target_resource_id_external_resources"),
        "resource_links",
        "external_resources",
        ["target_resource_id"],
        ["id"],
        source_schema="workspace",
        referent_schema="workspace",
        ondelete="CASCADE",
    )
    op.drop_table("tasks", schema="workspace")
    op.drop_table("projects", schema="workspace")
    op.execute("SELECT workspace.search_rebuild()")
