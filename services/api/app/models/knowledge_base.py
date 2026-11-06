"""知识库（T-0098，docs/v2/KNOWLEDGE_PLAN.md 第 3、4 节）。

- 三类知识库（KnowledgeSpace）：每个工作空间一个公共库；每个项目一个项目库；
  每个 OpenClaw 实例一个智能体库。
  项目或实例删除后外键置空，知识库保留为只读归档，由人决定是否删除（方案 3.1）。
- project_agents：哪些智能体参与哪个项目，决定它们能否读写这个项目的知识库（方案 3.2）。
- 笔记（KnowledgeNote）正文是 Markdown；属性和来源都在列里，服务端写，正文里的文字改不了它们。
  删除只是放进回收站（deleted_at），30 天后（purge_after）由 Worker 彻底删除。
- 每次保存一个版本（KnowledgeNoteRevision）；智能体写入的幂等键也记在版本上。
- 链接（KnowledgeLink）在保存时从正文解析出来；指向还不存在的笔记时 target_note_id 为空，
  新建同名笔记时补上。
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SPACE_KINDS = ("public", "project", "agent")
AGENT_ACCESS = ("read_write", "read", "none")
PARTICIPANT_ACCESS = ("read_write", "read")
NOTE_TYPES = (
    "overview",
    "brief",
    "decision",
    "rule",
    "process",
    "runbook",
    "lesson",
    "review",
    "preference",
    "glossary",
    "record",
    "plan",
    "risk",
    "design",
    "template",
    "correction",
    "readme",
)
SOURCE_KINDS = ("human", "observation", "task", "conversation", "external", "system")
CHANGE_KINDS = ("create", "update", "delete", "restore", "revert", "rename")
LINK_RESOURCE_TYPES = ("task", "project", "bookmark")
GUARD_KINDS = ("secret_blocked", "delete_paused", "rate_limited", "writes_disabled")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _user_fk() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("workspace.users.id", ondelete="SET NULL"))


def _instance_fk() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("workspace.agent_instances.id", ondelete="SET NULL"))


class KnowledgeSpace(Base):
    __tablename__ = "knowledge_spaces"
    __table_args__ = (
        CheckConstraint(f"kind IN {SPACE_KINDS}", name="kind"),
        CheckConstraint(f"agent_access IN {AGENT_ACCESS}", name="agent_access"),
        # 笔记的复合外键引用 (workspace_id, id)，保证笔记与知识库在同一个工作空间
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("project_id"),
        UniqueConstraint("instance_id"),
        Index(
            "uq_knowledge_spaces_public",
            "workspace_id",
            unique=True,
            postgresql_where=text("kind = 'public'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    # 项目库 / 智能体库的来源；被删除后置空，知识库变成只读归档
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.projects.id", ondelete="SET NULL")
    )
    instance_id: Mapped[uuid.UUID | None] = _instance_fk()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # 公共库：所有智能体的权限；智能体库：其他智能体的权限（read / none）；
    # 项目库不用这一列（看 project_agents）
    agent_access: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    # 只对智能体库有意义：这个智能体能否写入（关掉后它只能读）、删除是否被自动暂停、未读的纠正数
    writes_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    deletes_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrections_unseen: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ProjectAgent(Base):
    """参与项目的智能体（方案 3.2）。复合外键保证项目、实例都在同一个工作空间。"""

    __tablename__ = "project_agents"
    __table_args__ = (
        PrimaryKeyConstraint("project_id", "instance_id"),
        CheckConstraint(f"access IN {PARTICIPANT_ACCESS}", name="access"),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["workspace.projects.workspace_id", "workspace.projects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        Index("ix_project_agents_instance", "instance_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    access: Mapped[str] = mapped_column(Text, server_default=text("'read_write'"))
    added_by: Mapped[uuid.UUID | None] = _user_fk()
    created_at: Mapped[datetime] = _created_at()


class KnowledgeNote(Base):
    __tablename__ = "knowledge_notes"
    __table_args__ = (
        CheckConstraint(f"type IN {NOTE_TYPES}", name="type"),
        CheckConstraint(f"source_kind IN {SOURCE_KINDS}", name="source_kind"),
        ForeignKeyConstraint(
            ["workspace_id", "space_id"],
            ["workspace.knowledge_spaces.workspace_id", "workspace.knowledge_spaces.id"],
            ondelete="CASCADE",
        ),
        # 同一个知识库里未删除的笔记标题不重复（title_key 是规范化、转小写后的标题）
        Index(
            "uq_knowledge_notes_title",
            "space_id",
            "title_key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 每个知识库最多一篇项目简报
        Index(
            "uq_knowledge_notes_brief",
            "space_id",
            unique=True,
            postgresql_where=text("type = 'brief' AND deleted_at IS NULL"),
        ),
        # README（T-0122）：每个任务最多一篇；每个知识库最多一篇项目 README（根目录、没有 task_id）
        CheckConstraint("task_id IS NULL OR type = 'readme'", name="task_readme"),
        Index(
            "uq_knowledge_notes_task_readme",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_knowledge_notes_project_readme",
            "space_id",
            unique=True,
            postgresql_where=text(
                "type = 'readme' AND task_id IS NULL AND folder = '' AND deleted_at IS NULL"
            ),
        ),
        Index("ix_knowledge_notes_workspace_updated", "workspace_id", "updated_at"),
        Index("ix_knowledge_notes_purge", "purge_after"),
        Index(
            "ix_knowledge_notes_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    folder: Mapped[str] = mapped_column(Text, server_default=text("''"))
    title: Mapped[str] = mapped_column(Text)
    title_key: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    type: Mapped[str] = mapped_column(Text, server_default=text("'record'"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    review_at: Mapped[date | None] = mapped_column(Date)
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 任务 README 属于哪个任务（T-0122）；任务删除时置空，工作台同时把 README 放进回收站
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.tasks.id", ondelete="SET NULL")
    )
    created_by_user: Mapped[uuid.UUID | None] = _user_fk()
    created_by_instance: Mapped[uuid.UUID | None] = _instance_fk()
    updated_by_user: Mapped[uuid.UUID | None] = _user_fk()
    updated_by_instance: Mapped[uuid.UUID | None] = _instance_fk()
    source_kind: Mapped[str] = mapped_column(Text, server_default=text("'human'"))
    # 冲突或重复提醒（不阻止写入，方案 3.8 第 6 条）；为空表示没有
    conflict_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user: Mapped[uuid.UUID | None] = _user_fk()
    deleted_by_instance: Mapped[uuid.UUID | None] = _instance_fk()
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class KnowledgeNoteRevision(Base):
    __tablename__ = "knowledge_note_revisions"
    __table_args__ = (
        CheckConstraint(f"change IN {CHANGE_KINDS}", name="change"),
        Index("ix_knowledge_note_revisions_note", "note_id", "version"),
        Index("ix_knowledge_note_revisions_workspace_created", "workspace_id", "created_at"),
        # 智能体写入的幂等键：同一个实例的同一个键只对应一次改动
        Index(
            "uq_knowledge_note_revisions_idempotency",
            "author_instance",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.knowledge_notes.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # 保存时的属性快照：space_id、folder、type、tags、aliases、review_at
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    change: Mapped[str] = mapped_column(Text)
    author_user: Mapped[uuid.UUID | None] = _user_fk()
    author_instance: Mapped[uuid.UUID | None] = _instance_fk()
    reason: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # 依据：只存 id（任务、项目、笔记、IM 会话与消息），不存对话正文（规范 1.2）
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_hash: Mapped[str | None] = mapped_column(Text)
    # 与上一版相比增加 / 删除的行数（变更记录里显示，不必每次重算差异）
    lines_added: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    lines_removed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class KnowledgeLink(Base):
    __tablename__ = "knowledge_links"
    __table_args__ = (
        CheckConstraint(
            f"target_resource_type IS NULL OR target_resource_type IN {LINK_RESOURCE_TYPES}",
            name="target_resource_type",
        ),
        Index("ix_knowledge_links_source", "source_note_id"),
        Index("ix_knowledge_links_target", "target_note_id"),
        # 新建、改名的笔记补上指向它的未解析链接
        Index(
            "ix_knowledge_links_pending",
            "workspace_id",
            "target_key",
            postgresql_where=text("target_note_id IS NULL AND target_resource_type IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    source_note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.knowledge_notes.id", ondelete="CASCADE")
    )
    target_note_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.knowledge_notes.id", ondelete="SET NULL")
    )
    # 链接里写的标题（原样）与它的规范化形式；[[知识库/标题]] 时 target_space_key 是知识库名
    target_text: Mapped[str] = mapped_column(Text)
    target_key: Mapped[str] = mapped_column(Text)
    target_space_key: Mapped[str | None] = mapped_column(Text)
    # [[task:<id>]]、[[project:<id>]]、[[bookmark:<id>]]
    target_resource_type: Mapped[str | None] = mapped_column(Text)
    target_resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created_at()


class KnowledgeBriefRead(Base):
    """每个智能体最近读到的项目简报版本（方案 3.10）。"""

    __tablename__ = "knowledge_brief_reads"
    __table_args__ = (PrimaryKeyConstraint("project_id", "instance_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.projects.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    read_at: Mapped[datetime] = _created_at()


class KnowledgeReadStat(Base):
    """智能体按天读取每篇笔记的次数（不逐条记录读取）。"""

    __tablename__ = "knowledge_read_stats"
    __table_args__ = (
        PrimaryKeyConstraint("day", "instance_id", "note_id"),
        Index("ix_knowledge_read_stats_note", "note_id", "day"),
    )

    day: Mapped[date] = mapped_column(Date)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.knowledge_notes.id", ondelete="CASCADE")
    )
    reads: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class KnowledgeGuardEvent(Base):
    """保护措施拦下或暂停的记录（方案 3.8）：被拦下的写入、自动暂停、限流。不含被拦下的内容。"""

    __tablename__ = "knowledge_guard_events"
    __table_args__ = (
        CheckConstraint(f"kind IN {GUARD_KINDS}", name="kind"),
        Index("ix_knowledge_guard_events_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = _instance_fk()
    instance_name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    note_title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


REPLICA_STATES = ("none", "pending", "done", "failed")


class KnowledgeFile(Base):
    """知识库图片（T-0103，docs/v2/TIMELINE_PLAN.md 第 4 节）。

    同一工作空间、同一内容只存一份：对象 key 是 kb/<workspace_id>/<sha256>。
    `storage` 是上传时写入的主存储（local / r2 / oss），换了主存储以后旧图片仍从原处读；
    `replica_*` 是副本复制的状态（Worker 异步复制，失败重试）。
    """

    __tablename__ = "knowledge_files"
    __table_args__ = (
        UniqueConstraint("workspace_id", "sha256"),
        CheckConstraint(f"replica_state IN {REPLICA_STATES}", name="replica_state"),
        CheckConstraint("storage IN ('local', 'r2', 'oss')", name="storage"),
        CheckConstraint("size > 0", name="size"),
        Index(
            "ix_knowledge_files_replica_pending",
            "created_at",
            postgresql_where=text("replica_state = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    sha256: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    storage: Mapped[str] = mapped_column(Text)
    replica: Mapped[str | None] = mapped_column(Text)
    replica_state: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    replica_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    replica_error: Mapped[str | None] = mapped_column(Text)
    created_by_user: Mapped[uuid.UUID | None] = _user_fk()
    created_at: Mapped[datetime] = _created_at()


class KnowledgeFileRef(Base):
    """笔记引用了哪些图片。历史版本里引用过的也保留（current = false），保证版本历史里的图片不丢；
    笔记彻底删除时随之删除。没有任何引用、上传超过 24 小时的图片由 Worker 每天清理。"""

    __tablename__ = "knowledge_file_refs"
    __table_args__ = (
        PrimaryKeyConstraint("file_id", "note_id"),
        Index("ix_knowledge_file_refs_note", "note_id"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.knowledge_files.id", ondelete="CASCADE")
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.knowledge_notes.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()
