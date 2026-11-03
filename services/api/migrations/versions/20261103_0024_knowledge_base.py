"""知识库（T-0098，docs/v2/KNOWLEDGE_PLAN.md 第 4 节）

全部是新表：三类知识库、参与项目的智能体、笔记、版本、链接、简报已读、读取统计、保护记录。
另外：
- ai_tool_executions.risk_level 增加 'knowledge'（知识库工具直接执行、不审批，方案 3.7）；
- 笔记进全局搜索（resource_type 'note'），与其他资源一样由触发器维护；回收站里的笔记不在索引里；
- 补建知识库：每个工作空间的公共库、已有项目的项目库、已有实例的智能体库。

downgrade 删除新表、知识库工具的调用记录与笔记的搜索索引，恢复原来的约束与 search_rebuild。

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-03
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEARCH_NOTE = """
CREATE FUNCTION workspace.search_index_note(n workspace.knowledge_notes) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_context text;
BEGIN
    IF n.deleted_at IS NOT NULL THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'note' AND resource_id = n.id;
        RETURN;
    END IF;
    SELECT s.name INTO v_context FROM workspace.knowledge_spaces s WHERE s.id = n.space_id;
    INSERT INTO workspace.search_documents (
        workspace_id, resource_type, resource_id, provider, title, body, context,
        source_url, owner_id, visibility_json, source_updated_at
    ) VALUES (
        n.workspace_id, 'note', n.id, 'local', n.title,
        btrim(array_to_string(n.aliases, ' ') || ' ' || array_to_string(n.tags, ' ')
              || ' ' || n.body),
        v_context, NULL, NULL, '{"scope": "workspace"}', n.updated_at
    )
    ON CONFLICT (resource_type, resource_id) DO UPDATE SET
        title = EXCLUDED.title, body = EXCLUDED.body, context = EXCLUDED.context,
        source_updated_at = EXCLUDED.source_updated_at, indexed_at = now();
END;
$$;

CREATE FUNCTION workspace.search_trigger_note() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM workspace.search_documents
        WHERE resource_type = 'note' AND resource_id = OLD.id;
        RETURN OLD;
    END IF;
    PERFORM workspace.search_index_note(NEW);
    RETURN NEW;
END;
$$;

CREATE TRIGGER search_index AFTER INSERT OR UPDATE OR DELETE ON workspace.knowledge_notes
    FOR EACH ROW EXECUTE FUNCTION workspace.search_trigger_note();
"""

# search_rebuild：迁移 0020 的版本再加上笔记
_REBUILD = """
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
        WHERE n.deleted_at IS NULL ORDER BY n.id;
    SELECT count(*) INTO v_count FROM workspace.search_documents;
    RETURN v_count;
END;
$$;
"""

_OLD_REBUILD = """
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
"""

# 补建知识库（应用里的 ensure_spaces 用同样的语句，之后新建的项目、实例在第一次访问时补上）
_BACKFILL = """
INSERT INTO workspace.knowledge_spaces (workspace_id, kind, name, agent_access)
SELECT w.id, 'public', '公共知识库', 'read_write' FROM workspace.workspaces w;
INSERT INTO workspace.knowledge_spaces (workspace_id, kind, project_id, name, agent_access)
SELECT p.workspace_id, 'project', p.id, p.title, 'none' FROM workspace.projects p;
INSERT INTO workspace.knowledge_spaces (workspace_id, kind, instance_id, name, agent_access)
SELECT i.workspace_id, 'agent', i.id, i.name, 'read' FROM workspace.agent_instances i;
"""

_RISK_NEW = "risk_level IN ('read', 'write', 'critical', 'knowledge')"
_RISK_OLD = "risk_level IN ('read', 'write', 'critical')"


def _split(sql: str) -> list[str]:
    """asyncpg 一次只能执行一条语句：按 CREATE / INSERT 开头的行拆开。"""
    return [s.strip() for s in re.split(r"\n(?=CREATE |INSERT )", sql) if s.strip()]


def _risk_check(condition: str) -> None:
    op.drop_constraint(
        op.f("ck_ai_tool_executions_risk_level"),
        "ai_tool_executions",
        schema="workspace",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_tool_executions_risk_level"),
        "ai_tool_executions",
        condition,
        schema="workspace",
    )


def upgrade() -> None:
    op.create_table(
        "knowledge_brief_reads",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_brief_reads_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["workspace.projects.id"],
            name=op.f("fk_knowledge_brief_reads_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_brief_reads_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "instance_id", name=op.f("pk_knowledge_brief_reads")),
        schema="workspace",
    )
    op.create_table(
        "knowledge_guard_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("instance_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("note_title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('secret_blocked', 'delete_paused', 'rate_limited', 'writes_disabled')",
            name=op.f("ck_knowledge_guard_events_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_guard_events_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_guard_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_guard_events")),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_guard_events_workspace_created",
        "knowledge_guard_events",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "knowledge_spaces",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("agent_access", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column("writes_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("deletes_paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("corrections_unseen", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "agent_access IN ('read_write', 'read', 'none')",
            name=op.f("ck_knowledge_spaces_agent_access"),
        ),
        sa.CheckConstraint(
            "kind IN ('public', 'project', 'agent')", name=op.f("ck_knowledge_spaces_kind")
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_spaces_instance_id_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["workspace.projects.id"],
            name=op.f("fk_knowledge_spaces_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_spaces_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_spaces")),
        sa.UniqueConstraint("instance_id", name=op.f("uq_knowledge_spaces_instance_id")),
        sa.UniqueConstraint("project_id", name=op.f("uq_knowledge_spaces_project_id")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_knowledge_spaces_workspace_id_id")),
        schema="workspace",
    )
    op.create_index(
        "uq_knowledge_spaces_public",
        "knowledge_spaces",
        ["workspace_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("kind = 'public'"),
    )
    op.create_table(
        "project_agents",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("access", sa.Text(), server_default=sa.text("'read_write'"), nullable=False),
        sa.Column("added_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "access IN ('read_write', 'read')", name=op.f("ck_project_agents_access")
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["workspace.users.id"],
            name=op.f("fk_project_agents_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_project_agents_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["workspace.projects.workspace_id", "workspace.projects.id"],
            name=op.f("fk_project_agents_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_project_agents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "instance_id", name=op.f("pk_project_agents")),
        schema="workspace",
    )
    op.create_index(
        "ix_project_agents_instance",
        "project_agents",
        ["instance_id"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "knowledge_notes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("folder", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_key", sa.Text(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("type", sa.Text(), server_default=sa.text("'record'"), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("review_at", sa.Date(), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user", sa.UUID(), nullable=True),
        sa.Column("created_by_instance", sa.UUID(), nullable=True),
        sa.Column("updated_by_user", sa.UUID(), nullable=True),
        sa.Column("updated_by_instance", sa.UUID(), nullable=True),
        sa.Column("source_kind", sa.Text(), server_default=sa.text("'human'"), nullable=False),
        sa.Column("conflict_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user", sa.UUID(), nullable=True),
        sa.Column("deleted_by_instance", sa.UUID(), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
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
            "source_kind IN ('human', 'observation', 'task', 'conversation', 'external', 'system')",
            name=op.f("ck_knowledge_notes_source_kind"),
        ),
        sa.CheckConstraint(
            "type IN ('overview', 'brief', 'decision', 'rule', 'process', 'runbook', 'lesson', "
            "'review', 'preference', 'glossary', 'record', 'plan', 'risk', 'design', 'template', "
            "'correction')",
            name=op.f("ck_knowledge_notes_type"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["workspace.users.id"],
            name=op.f("fk_knowledge_notes_confirmed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_instance"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_notes_created_by_instance_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user"],
            ["workspace.users.id"],
            name=op.f("fk_knowledge_notes_created_by_user_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_instance"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_notes_deleted_by_instance_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user"],
            ["workspace.users.id"],
            name=op.f("fk_knowledge_notes_deleted_by_user_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_instance"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_notes_updated_by_instance_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user"],
            ["workspace.users.id"],
            name=op.f("fk_knowledge_notes_updated_by_user_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "space_id"],
            ["workspace.knowledge_spaces.workspace_id", "workspace.knowledge_spaces.id"],
            name=op.f("fk_knowledge_notes_workspace_id_space_id_knowledge_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_notes_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_notes")),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_notes_purge",
        "knowledge_notes",
        ["purge_after"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_notes_title_trgm",
        "knowledge_notes",
        ["title"],
        unique=False,
        schema="workspace",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_knowledge_notes_workspace_updated",
        "knowledge_notes",
        ["workspace_id", "updated_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_knowledge_notes_brief",
        "knowledge_notes",
        ["space_id"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("type = 'brief' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_knowledge_notes_title",
        "knowledge_notes",
        ["space_id", "title_key"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "knowledge_links",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("source_note_id", sa.UUID(), nullable=False),
        sa.Column("target_note_id", sa.UUID(), nullable=True),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("target_space_key", sa.Text(), nullable=True),
        sa.Column("target_resource_type", sa.Text(), nullable=True),
        sa.Column("target_resource_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_resource_type IS NULL "
            "OR target_resource_type IN ('task', 'project', 'bookmark')",
            name=op.f("ck_knowledge_links_target_resource_type"),
        ),
        sa.ForeignKeyConstraint(
            ["source_note_id"],
            ["workspace.knowledge_notes.id"],
            name=op.f("fk_knowledge_links_source_note_id_knowledge_notes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_note_id"],
            ["workspace.knowledge_notes.id"],
            name=op.f("fk_knowledge_links_target_note_id_knowledge_notes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_links_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_links")),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_links_pending",
        "knowledge_links",
        ["workspace_id", "target_key"],
        unique=False,
        schema="workspace",
        postgresql_where=sa.text("target_note_id IS NULL AND target_resource_type IS NULL"),
    )
    op.create_index(
        "ix_knowledge_links_source",
        "knowledge_links",
        ["source_note_id"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_links_target",
        "knowledge_links",
        ["target_note_id"],
        unique=False,
        schema="workspace",
    )
    op.create_table(
        "knowledge_note_revisions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("note_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("author_user", sa.UUID(), nullable=True),
        sa.Column("author_instance", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.UUID(), nullable=True),
        sa.Column("request_hash", sa.Text(), nullable=True),
        sa.Column("lines_added", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lines_removed", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "change IN ('create', 'update', 'delete', 'restore', 'revert', 'rename')",
            name=op.f("ck_knowledge_note_revisions_change"),
        ),
        sa.ForeignKeyConstraint(
            ["author_instance"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_note_revisions_author_instance_agent_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["author_user"],
            ["workspace.users.id"],
            name=op.f("fk_knowledge_note_revisions_author_user_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["workspace.knowledge_notes.id"],
            name=op.f("fk_knowledge_note_revisions_note_id_knowledge_notes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_note_revisions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_note_revisions")),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_note_revisions_note",
        "knowledge_note_revisions",
        ["note_id", "version"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_note_revisions_workspace_created",
        "knowledge_note_revisions",
        ["workspace_id", "created_at"],
        unique=False,
        schema="workspace",
    )
    op.create_index(
        "uq_knowledge_note_revisions_idempotency",
        "knowledge_note_revisions",
        ["author_instance", "idempotency_key"],
        unique=True,
        schema="workspace",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "knowledge_read_stats",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("note_id", sa.UUID(), nullable=False),
        sa.Column("reads", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_knowledge_read_stats_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["workspace.knowledge_notes.id"],
            name=op.f("fk_knowledge_read_stats_note_id_knowledge_notes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_knowledge_read_stats_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "day", "instance_id", "note_id", name=op.f("pk_knowledge_read_stats")
        ),
        schema="workspace",
    )
    op.create_index(
        "ix_knowledge_read_stats_note",
        "knowledge_read_stats",
        ["note_id", "day"],
        unique=False,
        schema="workspace",
    )
    for statement in (*_split(_SEARCH_NOTE), _REBUILD, *_split(_BACKFILL)):
        op.execute(statement)
    _risk_check(_RISK_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM workspace.ai_tool_executions WHERE risk_level = 'knowledge'")
    _risk_check(_RISK_OLD)
    op.execute(_OLD_REBUILD)
    op.execute("DROP TRIGGER search_index ON workspace.knowledge_notes")
    op.execute("DROP FUNCTION workspace.search_trigger_note()")
    op.execute("DROP FUNCTION workspace.search_index_note(workspace.knowledge_notes)")
    op.execute("DELETE FROM workspace.search_documents WHERE resource_type = 'note'")
    op.drop_index(
        "ix_knowledge_read_stats_note", table_name="knowledge_read_stats", schema="workspace"
    )
    op.drop_table("knowledge_read_stats", schema="workspace")
    op.drop_index(
        "uq_knowledge_note_revisions_idempotency",
        table_name="knowledge_note_revisions",
        schema="workspace",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        "ix_knowledge_note_revisions_workspace_created",
        table_name="knowledge_note_revisions",
        schema="workspace",
    )
    op.drop_index(
        "ix_knowledge_note_revisions_note",
        table_name="knowledge_note_revisions",
        schema="workspace",
    )
    op.drop_table("knowledge_note_revisions", schema="workspace")
    op.drop_index("ix_knowledge_links_target", table_name="knowledge_links", schema="workspace")
    op.drop_index("ix_knowledge_links_source", table_name="knowledge_links", schema="workspace")
    op.drop_index(
        "ix_knowledge_links_pending",
        table_name="knowledge_links",
        schema="workspace",
        postgresql_where=sa.text("target_note_id IS NULL AND target_resource_type IS NULL"),
    )
    op.drop_table("knowledge_links", schema="workspace")
    op.drop_index(
        "uq_knowledge_notes_title",
        table_name="knowledge_notes",
        schema="workspace",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_knowledge_notes_brief",
        table_name="knowledge_notes",
        schema="workspace",
        postgresql_where=sa.text("type = 'brief' AND deleted_at IS NULL"),
    )
    op.drop_index(
        "ix_knowledge_notes_workspace_updated", table_name="knowledge_notes", schema="workspace"
    )
    op.drop_index(
        "ix_knowledge_notes_title_trgm",
        table_name="knowledge_notes",
        schema="workspace",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.drop_index("ix_knowledge_notes_purge", table_name="knowledge_notes", schema="workspace")
    op.drop_table("knowledge_notes", schema="workspace")
    op.drop_index("ix_project_agents_instance", table_name="project_agents", schema="workspace")
    op.drop_table("project_agents", schema="workspace")
    op.drop_index(
        "uq_knowledge_spaces_public",
        table_name="knowledge_spaces",
        schema="workspace",
        postgresql_where=sa.text("kind = 'public'"),
    )
    op.drop_table("knowledge_spaces", schema="workspace")
    op.drop_index(
        "ix_knowledge_guard_events_workspace_created",
        table_name="knowledge_guard_events",
        schema="workspace",
    )
    op.drop_table("knowledge_guard_events", schema="workspace")
    op.drop_table("knowledge_brief_reads", schema="workspace")
