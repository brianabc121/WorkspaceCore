"""跨资源搜索索引与维护它的触发器（S4-03、S4-05）

docs/DEVELOPMENT_SPEC.md 第 5.1 节第 12 项、第 5.3 节；约定见
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 8 节。

search_documents 只由触发器维护：
external_resources（Vikunja 任务 / 项目、GitHub 仓库 / Issue / PR）、
bookmarks、calendar_events 的插入、更新、tombstone 与删除
都在同一个事务里同步索引，
任何写入路径（同步、Webhook、接口写入）都不会漏，删除的资源立即不再被搜到（A10）。
私有收藏只记 owner_id，创建者被删除（owner_id 变为 NULL）时从索引移除。
workspace.search_rebuild() 按当前数据整体重建索引；升级时调用一次，补齐已有数据。
downgrade 删除触发器、函数与表即可回退，不影响源数据。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# 每类资源怎么写进索引；触发器与 search_rebuild() 共用这几个函数
_FUNCTIONS = """
CREATE FUNCTION workspace.search_index_external(r workspace.external_resources) RETURNS void
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
        -- 项目改名：它的任务的 context 跟着改
        UPDATE workspace.search_documents d SET context = r.title
        FROM workspace.external_resources t
        WHERE d.resource_type = 'task' AND d.resource_id = t.id
          AND t.metadata_json ->> 'project_resource_id' = r.id::text;
    END IF;
END;
$$;

CREATE FUNCTION workspace.search_index_bookmark(b workspace.bookmarks) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF b.visibility = 'private' AND b.owner_id IS NULL THEN
        -- 创建者已被删除的私有收藏对任何人都不可见
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'bookmark' AND resource_id = b.id;
        RETURN;
    END IF;
    INSERT INTO workspace.search_documents (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        b.workspace_id, 'bookmark', b.id, 'affine', b.title, b.note, NULL, NULL,
        CASE WHEN b.visibility = 'private' THEN b.owner_id END,
        CASE WHEN b.visibility = 'private'
            THEN jsonb_build_object('scope', 'private', 'owner_id', b.owner_id)
            ELSE '{"scope": "workspace"}'::jsonb END,
        b.updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, owner_id = EXCLUDED.owner_id,
        visibility_json = EXCLUDED.visibility_json,
        source_updated_at = EXCLUDED.source_updated_at, indexed_at = now();
END;
$$;

CREATE FUNCTION workspace.search_index_event(e workspace.calendar_events) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO workspace.search_documents (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        e.workspace_id, 'event', e.id, 'local', e.title,
        btrim(e.description || ' ' || e.location),
        to_char(e.start_at AT TIME ZONE e.timezone, 'YYYY-MM-DD'),
        NULL, NULL, '{"scope": "workspace"}', e.updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, context = EXCLUDED.context,
        source_updated_at = EXCLUDED.source_updated_at, indexed_at = now();
END;
$$;

CREATE FUNCTION workspace.search_trigger_external() RETURNS trigger
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

CREATE FUNCTION workspace.search_trigger_bookmark() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'bookmark' AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_bookmark(NEW);
    RETURN NEW;
END;
$$;

CREATE FUNCTION workspace.search_trigger_event() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'event' AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_event(NEW);
    RETURN NEW;
END;
$$;

CREATE FUNCTION workspace.search_rebuild() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_count integer;
BEGIN
    DELETE FROM workspace.search_documents;
    -- 先索引项目，任务的 context（项目名）才能取到
    PERFORM workspace.search_index_external(r) FROM workspace.external_resources r
    ORDER BY (r.resource_type <> 'project'), r.id;
    PERFORM workspace.search_index_bookmark(b) FROM workspace.bookmarks b;
    PERFORM workspace.search_index_event(e) FROM workspace.calendar_events e;
    SELECT count(*) INTO v_count FROM workspace.search_documents;
    RETURN v_count;
END;
$$;
"""

_TRIGGERS = """
CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.external_resources
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_external();
CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.bookmarks
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_bookmark();
CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.calendar_events
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_event();
"""


def _split(sql: str) -> list[str]:
    """按行首的 CREATE 拆成单条语句。"""
    return [s.strip() for s in re.split(r"\n(?=CREATE )", sql) if s.strip()]


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_documents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column(
            "visibility_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text('\'{"scope": "workspace"}\'::jsonb'),
            nullable=False,
        ),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english'::regconfig, title || ' ' || body)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_search_documents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_documents")),
        schema="workspace",
    )
    op.create_index(
        "ix_search_documents_title_trgm",
        "search_documents",
        ["title"],
        unique=False,
        schema="workspace",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_search_documents_vector",
        "search_documents",
        ["search_vector"],
        unique=False,
        schema="workspace",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_search_documents_workspace_type",
        "search_documents",
        ["workspace_id", "resource_type"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_search_documents_resource",
        "search_documents",
        ["resource_type", "resource_id"],
        unique=True,
        schema="workspace",
    )

    # asyncpg 一次只能执行一条语句
    for statement in (*_split(_FUNCTIONS), *_split(_TRIGGERS)):
        op.execute(statement)
    op.execute("SELECT workspace.search_rebuild()")


def downgrade() -> None:
    for table in ("external_resources", "bookmarks", "calendar_events"):
        op.execute(f"DROP TRIGGER IF EXISTS search_index ON workspace.{table}")
    for function in (
        "search_rebuild()",
        "search_trigger_external()",
        "search_trigger_bookmark()",
        "search_trigger_event()",
        "search_index_external(workspace.external_resources)",
        "search_index_bookmark(workspace.bookmarks)",
        "search_index_event(workspace.calendar_events)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS workspace.{function}")
    op.drop_table("search_documents", schema="workspace")
