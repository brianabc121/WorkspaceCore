"""知识库（T-0098，docs/v2/KNOWLEDGE_PLAN.md 第 3、4、5 节）。

人用的接口（app/api/v1/knowledge_base.py）和智能体的 MCP 工具（app/domain/knowledge_tools.py）
都走这里的同一套写入路径：权限 → 内容校验（大小、密钥）→ 保存与版本 → 链接 → 纠正回写。

权限（方案 3.1、3.2）：
- 人：viewer 读所有知识库；member 及以上编辑笔记；owner / admin 管理设置、参与的智能体、彻底删除。
- 智能体（OpenClaw 实例）：
  - 公共库按 agent_access；
  - 项目库只有 project_agents 里的智能体能读写；
  - 自己的知识库读写，别人的知识库按对方的 agent_access（read / none）。
  已归档的知识库（项目归档，或项目 / 实例已删除）对智能体只读。
  项目简报只有人能改，《收到的纠正》由工作台维护（方案第 12 节第 2 条）。
"""

import difflib
import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import knowledge_links as links
from app.domain import notifications, secret_scan
from app.domain.audit import AuditTrail
from app.domain.policy import WorkspaceAccess, has_role, not_found
from app.domain.ratelimit import WindowLimiter
from app.models import (
    AgentInstance,
    Bookmark,
    KnowledgeBriefRead,
    KnowledgeGuardEvent,
    KnowledgeLink,
    KnowledgeNote,
    KnowledgeNoteRevision,
    KnowledgeReadStat,
    KnowledgeSpace,
    Project,
    ProjectAgent,
    Task,
    User,
)
from app.schemas.knowledge_base import (
    NOTE_BODY_MAX_AGENT,
    NOTE_BODY_MAX_HUMAN,
    TRASH_DAYS,
    AgentReads,
    Author,
    BacklinkOut,
    DiffLine,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphOut,
    LinkOut,
    NoteOut,
    NoteSummary,
    RevisionDetail,
    RevisionOut,
    SpaceAgentOut,
    SpaceOut,
    UsageOut,
)
from app.schemas.workspaces import Role

# 智能体的保护上限（方案 3.8 第 4 条）
AGENT_WRITES_PER_HOUR = 20
AGENT_DELETES_PER_HOUR = 5
MAX_NOTES_PER_SPACE = 5000
GRAPH_MAX_NODES = 2000
CORRECTIONS_TITLE = "收到的纠正"
CORRECTIONS_MAX_LINES = 200
SYSTEM = Author(kind="system", id=None, name="工作台")
_SIMILAR = 0.75

_ENSURE = (
    """
    INSERT INTO workspace.knowledge_spaces (workspace_id, kind, name, agent_access)
    SELECT :ws, 'public', '公共知识库', 'read_write'
    WHERE NOT EXISTS (
        SELECT 1 FROM workspace.knowledge_spaces WHERE workspace_id = :ws AND kind = 'public'
    )
    ON CONFLICT DO NOTHING
    """,
    """
    INSERT INTO workspace.knowledge_spaces (workspace_id, kind, project_id, name, agent_access)
    SELECT p.workspace_id, 'project', p.id, p.title, 'none' FROM workspace.projects p
    WHERE p.workspace_id = :ws
      AND NOT EXISTS (SELECT 1 FROM workspace.knowledge_spaces s WHERE s.project_id = p.id)
    ON CONFLICT DO NOTHING
    """,
    """
    INSERT INTO workspace.knowledge_spaces (workspace_id, kind, instance_id, name, agent_access)
    SELECT i.workspace_id, 'agent', i.id, i.name, 'read' FROM workspace.agent_instances i
    WHERE i.workspace_id = :ws
      AND NOT EXISTS (SELECT 1 FROM workspace.knowledge_spaces s WHERE s.instance_id = i.id)
    ON CONFLICT DO NOTHING
    """,
    """
    UPDATE workspace.knowledge_spaces s SET name = p.title, updated_at = now()
    FROM workspace.projects p
    WHERE s.project_id = p.id AND s.workspace_id = :ws AND s.name <> p.title
    """,
    """
    UPDATE workspace.knowledge_spaces s SET name = i.name, updated_at = now()
    FROM workspace.agent_instances i
    WHERE s.instance_id = i.id AND s.workspace_id = :ws AND s.name <> i.name
    """,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _err(status: int, code: str, message: str, **details: Any) -> ApiError:
    return ApiError(status, code, message, details=details or None)


def _bytes(text_value: str) -> int:
    return len(text_value.encode())


def _clean_list(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = " ".join(value.split())
        key = links.normalize(item)
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _clean_folder(folder: str) -> str:
    parts = [" ".join(p.split()) for p in folder.replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p)


def _line_stats(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def _alias_match(key: str) -> Any:
    """别名匹配（别名保存的是显示用的写法，比较时转小写）。"""
    return text(
        ":alias_key = ANY (SELECT lower(a) FROM unnest(workspace.knowledge_notes.aliases) a)"
    ).bindparams(alias_key=key)


def request_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Editor:
    """谁在改：人（user_id）或智能体（instance）。"""

    workspace_id: uuid.UUID
    user_id: uuid.UUID | None = None
    instance: AgentInstance | None = None
    client_ip: str | None = None
    user_name: str = ""

    @property
    def is_agent(self) -> bool:
        return self.instance is not None

    @property
    def name(self) -> str:
        return self.instance.name if self.instance is not None else self.user_name


@dataclass
class NoteFields:
    """新建或修改时要写入的字段；None 表示不改。"""

    space_id: uuid.UUID | None = None
    title: str | None = None
    body: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    folder: str | None = None
    aliases: list[str] | None = None
    review_at: date | None = None
    review_set: bool = False
    pinned: bool | None = None


@dataclass
class SaveOptions:
    reason: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_kind: str = "human"
    idempotency_key: uuid.UUID | None = None
    request_hash: str | None = None


@dataclass(frozen=True)
class BriefStatus:
    project_id: uuid.UUID
    project: str
    version: int
    read: int | None

    @property
    def unread(self) -> bool:
        return self.read != self.version


@dataclass(frozen=True)
class SpaceState:
    space: KnowledgeSpace
    archived: bool


class KnowledgeBase:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)

    # ================================================================ 知识库

    async def ensure_spaces(self, workspace_id: uuid.UUID) -> None:
        """补建缺少的知识库，同步项目 / 实例改名（迁移 0024 补建过一次，之后在访问时补）。"""
        for statement in _ENSURE:
            await self.db.execute(text(statement), {"ws": workspace_id})

    async def spaces(self, workspace_id: uuid.UUID) -> list[SpaceState]:
        rows = (
            await self.db.execute(
                select(KnowledgeSpace, Project.archived)
                .outerjoin(Project, Project.id == KnowledgeSpace.project_id)
                .where(KnowledgeSpace.workspace_id == workspace_id)
                .order_by(KnowledgeSpace.kind.desc(), KnowledgeSpace.name, KnowledgeSpace.id)
            )
        ).all()
        return [SpaceState(space, self._archived(space, archived)) for space, archived in rows]

    @staticmethod
    def _archived(space: KnowledgeSpace, project_archived: bool | None) -> bool:
        if space.kind == "project":
            return space.project_id is None or bool(project_archived)
        if space.kind == "agent":
            return space.instance_id is None
        return False

    async def space(self, workspace_id: uuid.UUID, space_id: uuid.UUID) -> SpaceState:
        row = (
            await self.db.execute(
                select(KnowledgeSpace, Project.archived)
                .outerjoin(Project, Project.id == KnowledgeSpace.project_id)
                .where(KnowledgeSpace.workspace_id == workspace_id, KnowledgeSpace.id == space_id)
            )
        ).first()
        if row is None:
            raise not_found()
        return SpaceState(row[0], self._archived(row[0], row[1]))

    async def agent_space(self, instance: AgentInstance) -> KnowledgeSpace:
        space = await self.db.scalar(
            select(KnowledgeSpace).where(KnowledgeSpace.instance_id == instance.id)
        )
        if space is None:
            await self.ensure_spaces(instance.workspace_id)
            space = await self.db.scalar(
                select(KnowledgeSpace).where(KnowledgeSpace.instance_id == instance.id)
            )
        if space is None:  # 不会发生：ensure_spaces 刚补建
            raise not_found()
        return space

    async def participation(self, instance_id: uuid.UUID) -> dict[uuid.UUID, str]:
        rows = await self.db.execute(
            select(ProjectAgent.project_id, ProjectAgent.access).where(
                ProjectAgent.instance_id == instance_id
            )
        )
        return {project_id: access for project_id, access in rows.all()}

    @staticmethod
    def agent_level(
        state: SpaceState, instance_id: uuid.UUID, participation: dict[uuid.UUID, str]
    ) -> str:
        """智能体对一个知识库的权限：read_write / read / none。"""
        space = state.space
        if space.kind == "public":
            level = space.agent_access
        elif space.kind == "project":
            level = participation.get(space.project_id, "none") if space.project_id else "none"
        elif space.instance_id == instance_id:
            level = "read_write"
        else:
            level = "read" if space.agent_access == "read" else "none"
        if level == "read_write" and state.archived:
            return "read"
        return level

    async def space_outs(self, states: Sequence[SpaceState]) -> list[SpaceOut]:
        ids = [s.space.id for s in states]
        counts: dict[uuid.UUID, tuple[int, datetime | None]] = {}
        briefs: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
        agents: dict[uuid.UUID, list[SpaceAgentOut]] = {}
        if ids:
            alive = KnowledgeNote.deleted_at.is_(None)
            for space_id, count, last in (
                await self.db.execute(
                    select(KnowledgeNote.space_id, func.count(), func.max(KnowledgeNote.updated_at))
                    .where(KnowledgeNote.space_id.in_(ids), alive)
                    .group_by(KnowledgeNote.space_id)
                )
            ).all():
                counts[space_id] = (count, last)
            for space_id, note_id, version in (
                await self.db.execute(
                    select(KnowledgeNote.space_id, KnowledgeNote.id, KnowledgeNote.version).where(
                        KnowledgeNote.space_id.in_(ids), KnowledgeNote.type == "brief", alive
                    )
                )
            ).all():
                briefs[space_id] = (note_id, version)
            project_ids = [s.space.project_id for s in states if s.space.project_id]
            if project_ids:
                space_of = {s.space.project_id: s.space.id for s in states if s.space.project_id}
                rows = (
                    await self.db.execute(
                        select(ProjectAgent, AgentInstance.name, KnowledgeBriefRead.version)
                        .join(AgentInstance, AgentInstance.id == ProjectAgent.instance_id)
                        .outerjoin(
                            KnowledgeBriefRead,
                            and_(
                                KnowledgeBriefRead.project_id == ProjectAgent.project_id,
                                KnowledgeBriefRead.instance_id == ProjectAgent.instance_id,
                            ),
                        )
                        .where(ProjectAgent.project_id.in_(project_ids))
                        .order_by(AgentInstance.name)
                    )
                ).all()
                for pa, name, read_version in rows:
                    agents.setdefault(space_of[pa.project_id], []).append(
                        SpaceAgentOut(
                            instance_id=pa.instance_id,
                            name=name,
                            access=pa.access,
                            brief_version_read=read_version,
                            added_at=pa.created_at,
                        )
                    )
        out = []
        for state in states:
            space = state.space
            count, last_at = counts.get(space.id, (0, None))
            brief = briefs.get(space.id)
            out.append(
                SpaceOut(
                    id=space.id,
                    kind=space.kind,
                    name=space.name,
                    description=space.description,
                    project_id=space.project_id,
                    instance_id=space.instance_id,
                    archived=state.archived,
                    agent_access=space.agent_access,
                    writes_enabled=space.writes_enabled,
                    deletes_paused_at=space.deletes_paused_at,
                    notes=count,
                    last_updated_at=last_at,
                    brief_note_id=brief[0] if brief else None,
                    brief_version=brief[1] if brief else None,
                    agents=agents.get(space.id, []),
                    version=space.version,
                )
            )
        return out

    # ================================================================ 读取

    async def note(
        self, workspace_id: uuid.UUID, note_id: uuid.UUID, *, lock: bool = False
    ) -> KnowledgeNote:
        stmt = select(KnowledgeNote).where(
            KnowledgeNote.workspace_id == workspace_id, KnowledgeNote.id == note_id
        )
        if lock:
            stmt = stmt.with_for_update()
        note = await self.db.scalar(stmt)
        if note is None:
            raise not_found()
        return note

    async def note_by_title(self, space_id: uuid.UUID, title: str) -> KnowledgeNote | None:
        key = links.normalize(title)
        return await self.db.scalar(
            select(KnowledgeNote)
            .where(
                KnowledgeNote.space_id == space_id,
                KnowledgeNote.deleted_at.is_(None),
                or_(KnowledgeNote.title_key == key, _alias_match(key)),
            )
            .order_by(KnowledgeNote.title_key != key)
            .limit(1)
        )

    async def authors(
        self, user_ids: Iterable[uuid.UUID | None], instance_ids: Iterable[uuid.UUID | None]
    ) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
        users = {u for u in user_ids if u}
        instances = {i for i in instance_ids if i}
        user_names: dict[uuid.UUID, str] = {}
        instance_names: dict[uuid.UUID, str] = {}
        if users:
            for uid, name in (
                await self.db.execute(select(User.id, User.display_name).where(User.id.in_(users)))
            ).all():
                user_names[uid] = name
        if instances:
            for iid, name in (
                await self.db.execute(
                    select(AgentInstance.id, AgentInstance.name).where(
                        AgentInstance.id.in_(instances)
                    )
                )
            ).all():
                instance_names[iid] = name
        return user_names, instance_names

    @staticmethod
    def author(
        user_id: uuid.UUID | None,
        instance_id: uuid.UUID | None,
        names: tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]],
        *,
        system_if_empty: bool = True,
    ) -> Author:
        if instance_id is not None:
            return Author(
                kind="agent", id=instance_id, name=names[1].get(instance_id, "已删除的实例")
            )
        if user_id is not None:
            return Author(kind="user", id=user_id, name=names[0].get(user_id, "已删除的用户"))
        if system_if_empty:
            return SYSTEM
        return Author(kind="user", id=None, name="已删除的用户")

    async def summaries(
        self, notes: Sequence[KnowledgeNote], spaces: dict[uuid.UUID, KnowledgeSpace]
    ) -> list[NoteSummary]:
        names = await self.authors(
            (n.updated_by_user for n in notes), (n.updated_by_instance for n in notes)
        )
        return [self.summary(n, spaces[n.space_id], names) for n in notes]

    def summary(
        self,
        note: KnowledgeNote,
        space: KnowledgeSpace,
        names: tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]],
    ) -> NoteSummary:
        return NoteSummary(
            id=note.id,
            space_id=space.id,
            space_kind=space.kind,
            space_name=space.name,
            title=note.title,
            type=note.type,
            folder=note.folder,
            tags=list(note.tags),
            pinned=note.pinned,
            confirmed=note.confirmed_at is not None,
            conflict=note.conflict_note is not None,
            source_kind=note.source_kind,
            updated_by=self.author(note.updated_by_user, note.updated_by_instance, names),
            updated_at=note.updated_at,
            version=note.version,
        )

    async def space_map(self, workspace_id: uuid.UUID) -> dict[uuid.UUID, KnowledgeSpace]:
        rows = await self.db.scalars(
            select(KnowledgeSpace).where(KnowledgeSpace.workspace_id == workspace_id)
        )
        return {s.id: s for s in rows.all()}

    async def note_out(self, note: KnowledgeNote) -> NoteOut:
        spaces = await self.space_map(note.workspace_id)
        space = spaces[note.space_id]
        latest = await self.db.scalar(
            select(KnowledgeNoteRevision)
            .where(KnowledgeNoteRevision.note_id == note.id)
            .order_by(KnowledgeNoteRevision.version.desc(), KnowledgeNoteRevision.created_at.desc())
            .limit(1)
        )
        names = await self.authors(
            (note.updated_by_user, note.created_by_user, note.confirmed_by),
            (note.updated_by_instance, note.created_by_instance),
        )
        summary = self.summary(note, space, names)
        return NoteOut(
            **summary.model_dump(),
            body=note.body,
            aliases=list(note.aliases),
            review_at=note.review_at,
            confirmed_by=(
                self.author(note.confirmed_by, None, names) if note.confirmed_by else None
            ),
            confirmed_at=note.confirmed_at,
            conflict_note=note.conflict_note,
            created_by=self.author(note.created_by_user, note.created_by_instance, names),
            created_at=note.created_at,
            deleted_at=note.deleted_at,
            purge_after=note.purge_after,
            reason=latest.reason if latest else "",
            evidence=[Evidence.model_validate(e) for e in (latest.evidence if latest else [])],
            links=await self.out_links(note, spaces),
            backlinks=await self.backlinks(note, spaces),
            usage=await self.usage(note.id),
        )

    async def out_links(
        self, note: KnowledgeNote, spaces: dict[uuid.UUID, KnowledgeSpace]
    ) -> list[LinkOut]:
        rows = (
            await self.db.execute(
                select(KnowledgeLink, KnowledgeNote)
                .outerjoin(KnowledgeNote, KnowledgeNote.id == KnowledgeLink.target_note_id)
                .where(KnowledgeLink.source_note_id == note.id)
                .order_by(KnowledgeLink.created_at, KnowledgeLink.id)
            )
        ).all()
        titles = await self._resource_titles(
            (link.target_resource_type, link.target_resource_id) for link, _ in rows
        )
        out = []
        for link, target in rows:
            resource_title = (
                titles.get((link.target_resource_type, link.target_resource_id))
                if link.target_resource_type and link.target_resource_id
                else None
            )
            out.append(
                LinkOut(
                    text=link.target_text,
                    note_id=target.id if target else None,
                    title=target.title if target else resource_title,
                    space_name=spaces[target.space_id].name if target else None,
                    deleted=bool(target and target.deleted_at),
                    resource_type=link.target_resource_type,
                    resource_id=link.target_resource_id,
                )
            )
        return out

    async def _resource_titles(
        self, refs: Iterable[tuple[str | None, uuid.UUID | None]]
    ) -> dict[tuple[str, uuid.UUID], str]:
        wanted: dict[str, set[uuid.UUID]] = {}
        for kind, rid in refs:
            if kind and rid:
                wanted.setdefault(kind, set()).add(rid)
        out: dict[tuple[str, uuid.UUID], str] = {}
        models: dict[str, Any] = {"task": Task, "project": Project, "bookmark": Bookmark}
        for kind, ids in wanted.items():
            model = models[kind]
            for rid, title in (
                await self.db.execute(select(model.id, model.title).where(model.id.in_(ids)))
            ).all():
                out[(kind, rid)] = title
        return out

    async def backlinks(
        self, note: KnowledgeNote, spaces: dict[uuid.UUID, KnowledgeSpace]
    ) -> list[BacklinkOut]:
        rows = (
            await self.db.scalars(
                select(KnowledgeNote)
                .join(KnowledgeLink, KnowledgeLink.source_note_id == KnowledgeNote.id)
                .where(
                    KnowledgeLink.target_note_id == note.id,
                    KnowledgeNote.deleted_at.is_(None),
                    KnowledgeNote.id != note.id,
                )
                .order_by(KnowledgeNote.updated_at.desc())
                .limit(100)
            )
        ).all()
        out = []
        for source in rows:
            space = spaces[source.space_id]
            snippet = links.snippet(source.body, note.title)
            for alias in note.aliases:
                if snippet:
                    break
                snippet = links.snippet(source.body, alias)
            out.append(
                BacklinkOut(
                    note_id=source.id,
                    title=source.title,
                    space_id=space.id,
                    space_kind=space.kind,
                    space_name=space.name,
                    snippet=snippet,
                )
            )
        return out

    async def usage(self, note_id: uuid.UUID) -> UsageOut:
        since = date.today() - timedelta(days=30)
        rows = (
            await self.db.execute(
                select(
                    KnowledgeReadStat.instance_id,
                    AgentInstance.name,
                    func.sum(KnowledgeReadStat.reads),
                    func.max(KnowledgeReadStat.day),
                )
                .outerjoin(AgentInstance, AgentInstance.id == KnowledgeReadStat.instance_id)
                .where(KnowledgeReadStat.note_id == note_id, KnowledgeReadStat.day >= since)
                .group_by(KnowledgeReadStat.instance_id, AgentInstance.name)
                .order_by(func.sum(KnowledgeReadStat.reads).desc())
            )
        ).all()
        by_agent = [
            AgentReads(instance_id=iid, name=name or "已删除的实例", reads=int(reads))
            for iid, name, reads, _ in rows
        ]
        last = max((day for *_, day in rows), default=None)
        return UsageOut(
            reads_30d=sum(a.reads for a in by_agent), by_agent=by_agent, last_read_at=last
        )

    async def brief_status(
        self, instance_id: uuid.UUID, project_id: uuid.UUID | None = None
    ) -> list[BriefStatus]:
        """它参与的项目里有简报的：最新版本与它读过的版本（方案 3.10“谁读过最新简报”）。"""
        stmt = (
            select(
                ProjectAgent.project_id,
                KnowledgeSpace.name,
                KnowledgeNote.version,
                KnowledgeBriefRead.version,
            )
            .join(KnowledgeSpace, KnowledgeSpace.project_id == ProjectAgent.project_id)
            .join(
                KnowledgeNote,
                and_(
                    KnowledgeNote.space_id == KnowledgeSpace.id,
                    KnowledgeNote.type == "brief",
                    KnowledgeNote.deleted_at.is_(None),
                ),
            )
            .outerjoin(
                KnowledgeBriefRead,
                and_(
                    KnowledgeBriefRead.project_id == ProjectAgent.project_id,
                    KnowledgeBriefRead.instance_id == ProjectAgent.instance_id,
                ),
            )
            .where(ProjectAgent.instance_id == instance_id)
            .order_by(KnowledgeSpace.name)
        )
        if project_id is not None:
            stmt = stmt.where(ProjectAgent.project_id == project_id)
        rows = (await self.db.execute(stmt)).all()
        return [BriefStatus(project_id=r[0], project=r[1], version=r[2], read=r[3]) for r in rows]

    async def record_read(self, instance: AgentInstance, note: KnowledgeNote) -> None:
        stmt = pg_insert(KnowledgeReadStat).values(
            day=date.today(),
            workspace_id=note.workspace_id,
            instance_id=instance.id,
            note_id=note.id,
            reads=1,
        )
        await self.db.execute(
            stmt.on_conflict_do_update(
                index_elements=["day", "instance_id", "note_id"],
                set_={"reads": KnowledgeReadStat.reads + 1},
            )
        )

    # ================================================================ 写入

    def _check_content(
        self, editor: Editor, fields: NoteFields, current: KnowledgeNote | None
    ) -> None:
        body = fields.body if fields.body is not None else (current.body if current else "")
        limit = NOTE_BODY_MAX_AGENT if editor.is_agent else NOTE_BODY_MAX_HUMAN
        if fields.body is not None and _bytes(body) > limit:
            raise _err(
                422,
                "NOTE_TOO_LARGE",
                f"正文超过上限（{limit // 1024} KB）；请拆成几篇，或删掉不需要的内容",
                limit_bytes=limit,
            )

    async def _scan(self, editor: Editor, fields: NoteFields, title: str) -> None:
        texts = [title, fields.body or "", *(fields.aliases or []), *(fields.tags or [])]
        found = secret_scan.scan(*texts)
        if not found:
            return
        if editor.instance is not None:
            await self.guard(
                editor.instance,
                "secret_blocked",
                "内容里像有：" + "、".join(found),
                note_title=title,
            )
            await self.db.commit()
        raise _err(
            422,
            "SECRET_DETECTED",
            "内容里像有密钥或密码（" + "、".join(found) + "），没有保存。"
            "请只写密钥库里的名字，不要写明文",
            kinds=found,
        )

    async def guard(
        self,
        instance: AgentInstance,
        kind: str,
        detail: str,
        *,
        note_title: str | None = None,
    ) -> None:
        self.db.add(
            KnowledgeGuardEvent(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                instance_name=instance.name,
                kind=kind,
                detail=detail,
                note_title=note_title,
            )
        )

    async def _check_agent_write(
        self,
        editor: Editor,
        state: SpaceState,
        note: KnowledgeNote | None,
        new_type: str | None,
        *,
        deleting: bool = False,
    ) -> KnowledgeSpace:
        instance = editor.instance
        if instance is None:
            raise RuntimeError("_check_agent_write 只用于智能体")
        own = await self.agent_space(instance)
        if not own.writes_enabled:
            raise _err(403, "WRITES_DISABLED", "管理员关闭了这个智能体的写入，现在只能读")
        level = self.agent_level(state, instance.id, await self.participation(instance.id))
        if level != "read_write":
            if level == "none":
                raise not_found()
            raise _err(403, "READ_ONLY", "这个知识库对你是只读的")
        for kind in (new_type, note.type if note else None):
            if kind == "brief":
                raise _err(403, "BRIEF_HUMAN_ONLY", "项目简报只有人能修改")
            if kind == "correction":
                raise _err(403, "SYSTEM_NOTE", "《收到的纠正》由工作台维护，不能修改")
        if deleting and own.deletes_paused_at is not None:
            raise _err(
                403,
                "DELETES_PAUSED",
                "你的删除已被自动暂停（删除太多），等管理员解除后才能再删除",
            )
        return own

    async def _limit(self, instance: AgentInstance, kind: str) -> None:
        if self.redis is None:
            return
        limit = AGENT_DELETES_PER_HOUR if kind == "delete" else AGENT_WRITES_PER_HOUR
        wait = await WindowLimiter(self.redis, 3600).hit(f"kb_{kind}:{instance.id}", limit)
        if wait is None:
            return
        own = await self.agent_space(instance)
        if kind == "delete":
            if own.deletes_paused_at is None:
                own.deletes_paused_at = _now()
                own.version += 1
                await self.guard(
                    instance,
                    "delete_paused",
                    f"1 小时内删除超过 {AGENT_DELETES_PER_HOUR} 篇，已自动暂停它的删除；"
                    "读取和写入不受影响",
                )
                await notifications.notify_admins(
                    self.db,
                    instance.workspace_id,
                    type_="knowledge_delete_paused",
                    severity="warning",
                    title=f"{instance.name} 的删除已自动暂停",
                    body=f"1 小时内删除超过 {AGENT_DELETES_PER_HOUR} 篇笔记。"
                    "删除的笔记都在回收站，可以恢复；确认没问题后在知识库里解除暂停。",
                    link="/knowledge/changes",
                    key=str(instance.id),
                )
            await self.db.commit()
            raise _err(
                403,
                "DELETES_PAUSED",
                "1 小时内删除太多，你的删除已被自动暂停，等管理员解除后才能再删除",
            )
        await self.guard(instance, "rate_limited", f"1 小时内写入超过 {AGENT_WRITES_PER_HOUR} 次")
        await self.db.commit()
        raise ApiError(
            429,
            "RATE_LIMITED",
            f"1 小时内最多写入 {AGENT_WRITES_PER_HOUR} 次，请 {wait} 秒后再试",
            headers={"Retry-After": str(wait)},
        )

    async def _space_capacity(self, space_id: uuid.UUID) -> None:
        count = await self.db.scalar(
            select(func.count()).where(
                KnowledgeNote.space_id == space_id, KnowledgeNote.deleted_at.is_(None)
            )
        )
        if (count or 0) >= MAX_NOTES_PER_SPACE:
            raise _err(
                422,
                "SPACE_FULL",
                f"这个知识库已有 {MAX_NOTES_PER_SPACE} 篇笔记，请先整理或删除不需要的",
            )

    async def replay(
        self, instance: AgentInstance, key: uuid.UUID, digest: str
    ) -> KnowledgeNoteRevision | None:
        """智能体用同一个幂等键重试：返回第一次的改动；参数不同则报错。"""
        revision = await self.db.scalar(
            select(KnowledgeNoteRevision).where(
                KnowledgeNoteRevision.author_instance == instance.id,
                KnowledgeNoteRevision.idempotency_key == key,
            )
        )
        if revision is not None and revision.request_hash != digest:
            raise _err(409, "IDEMPOTENCY_KEY_REUSED", "同一个幂等键已用于不同的请求")
        return revision

    async def create(
        self, editor: Editor, fields: NoteFields, options: SaveOptions
    ) -> KnowledgeNote:
        if fields.space_id is None or fields.title is None:
            raise ValueError("新建笔记必须有知识库和标题")
        state = await self.space(editor.workspace_id, fields.space_id)
        note_type = fields.type or "record"
        if editor.is_agent:
            await self._check_agent_write(editor, state, None, note_type)
        elif note_type == "correction":
            raise _err(422, "SYSTEM_NOTE", "《收到的纠正》由工作台维护，不能新建这种类型")
        title = " ".join(fields.title.split())
        self._check_content(editor, fields, None)
        await self._scan(editor, fields, title)
        await self._space_capacity(state.space.id)
        if editor.instance is not None:
            await self._limit(editor.instance, "write")
        note = KnowledgeNote(
            workspace_id=editor.workspace_id,
            space_id=state.space.id,
            folder=_clean_folder(fields.folder or ""),
            title=title,
            title_key=links.normalize(title),
            aliases=_clean_list(fields.aliases or []),
            body=fields.body or "",
            type=note_type,
            tags=_clean_list(fields.tags or []),
            review_at=fields.review_at,
            pinned=bool(fields.pinned),
            created_by_user=None if editor.is_agent else editor.user_id,
            created_by_instance=editor.instance.id if editor.instance else None,
            updated_by_user=None if editor.is_agent else editor.user_id,
            updated_by_instance=editor.instance.id if editor.instance else None,
            source_kind=options.source_kind,
            version=1,
        )
        self.db.add(note)
        await self._flush_title(note)
        added, _ = _line_stats("", note.body)
        self._revision(note, editor, "create", options, added, 0)
        await self.relink(note, state.space)
        if editor.is_agent:
            await self.mark_similar(note)
        await self._audit(editor, "knowledge_note.created", note, {"space_id": str(note.space_id)})
        return note

    async def _flush_title(self, note: KnowledgeNote) -> None:
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            raise _err(
                409, "TITLE_TAKEN", "这个知识库里已经有同名的笔记", title=note.title
            ) from None

    def _revision(
        self,
        note: KnowledgeNote,
        editor: Editor | None,
        change: str,
        options: SaveOptions,
        added: int,
        removed: int,
    ) -> KnowledgeNoteRevision:
        revision = KnowledgeNoteRevision(
            workspace_id=note.workspace_id,
            note_id=note.id,
            version=note.version,
            title=note.title,
            body=note.body,
            properties={
                "space_id": str(note.space_id),
                "folder": note.folder,
                "type": note.type,
                "tags": list(note.tags),
                "aliases": list(note.aliases),
                "review_at": note.review_at.isoformat() if note.review_at else None,
            },
            change=change,
            author_user=editor.user_id if editor and not editor.is_agent else None,
            author_instance=editor.instance.id if editor and editor.instance else None,
            reason=options.reason,
            evidence=options.evidence,
            idempotency_key=options.idempotency_key,
            request_hash=options.request_hash,
            lines_added=added,
            lines_removed=removed,
        )
        self.db.add(revision)
        return revision

    async def update(
        self,
        editor: Editor,
        note: KnowledgeNote,
        version: int,
        fields: NoteFields,
        options: SaveOptions,
    ) -> KnowledgeNote:
        if note.deleted_at is not None:
            raise _err(409, "NOTE_DELETED", "这篇笔记在回收站里，先恢复再修改")
        if note.version != version:
            raise await self._conflict(note)
        target_space = fields.space_id or note.space_id
        state = await self.space(editor.workspace_id, target_space)
        if editor.is_agent:
            if fields.space_id is not None and fields.space_id != note.space_id:
                raise _err(422, "MOVE_NOT_ALLOWED", "智能体不能移动笔记；请在目标知识库新建一篇")
            await self._check_agent_write(editor, state, note, fields.type)
        elif fields.type == "correction" and note.type != "correction":
            raise _err(422, "SYSTEM_NOTE", "不能把笔记改成《收到的纠正》这种类型")
        self._check_content(editor, fields, note)
        title = " ".join(fields.title.split()) if fields.title is not None else note.title
        await self._scan(editor, fields, title)
        if editor.instance is not None:
            await self._limit(editor.instance, "write")
        previous_agent = note.updated_by_instance
        old_body, old_title = note.body, note.title
        if fields.space_id is not None and fields.space_id != note.space_id:
            await self._space_capacity(fields.space_id)
            note.space_id = fields.space_id
        note.title = title
        note.title_key = links.normalize(title)
        if fields.body is not None:
            note.body = fields.body
        if fields.type is not None:
            note.type = fields.type
        if fields.tags is not None:
            note.tags = _clean_list(fields.tags)
        if fields.folder is not None:
            note.folder = _clean_folder(fields.folder)
        if fields.aliases is not None:
            note.aliases = _clean_list(fields.aliases)
        if fields.review_set:
            note.review_at = fields.review_at
        if fields.pinned is not None:
            note.pinned = fields.pinned
        note.updated_by_user = None if editor.is_agent else editor.user_id
        note.updated_by_instance = editor.instance.id if editor.instance else None
        if editor.is_agent:
            note.source_kind = options.source_kind
            note.confirmed_at = note.confirmed_by = None  # 智能体改过，人要重新确认
        else:
            note.conflict_note = None  # 人改过：冲突提醒由人处理了
        note.version += 1
        await self._flush_title(note)
        added, removed = _line_stats(old_body, note.body)
        change = "rename" if title != old_title and note.body == old_body else "update"
        self._revision(note, editor, change, options, added, removed)
        await self.relink(note, state.space)
        if editor.is_agent:
            await self.mark_similar(note)
        elif previous_agent is not None:
            await self.write_correction(
                previous_agent, editor, note, state.space, "修改了", options.reason
            )
        await self._audit(editor, "knowledge_note.updated", note, {"version": note.version})
        return note

    async def _conflict(self, note: KnowledgeNote) -> ApiError:
        current = await self.note_out(note)
        return ApiError(
            409,
            "VERSION_CONFLICT",
            "笔记已被其他人或智能体修改，请合并后再保存",
            details={"current_version": note.version, "current": current.model_dump(mode="json")},
        )

    async def delete(
        self, editor: Editor, note: KnowledgeNote, version: int, options: SaveOptions
    ) -> KnowledgeNote:
        if note.deleted_at is not None:
            return note  # 已经在回收站：删除是幂等的
        if note.version != version:
            raise await self._conflict(note)
        state = await self.space(editor.workspace_id, note.space_id)
        if editor.instance is not None:
            await self._check_agent_write(editor, state, note, None, deleting=True)
            await self._limit(editor.instance, "delete")
        previous_agent = note.updated_by_instance
        now = _now()
        note.deleted_at = now
        note.purge_after = now + timedelta(days=TRASH_DAYS)
        note.deleted_by_user = None if editor.is_agent else editor.user_id
        note.deleted_by_instance = editor.instance.id if editor.instance else None
        note.version += 1
        self._revision(note, editor, "delete", options, 0, len(note.body.splitlines()))
        if not editor.is_agent and previous_agent is not None:
            await self.write_correction(
                previous_agent, editor, note, state.space, "删除了", options.reason
            )
        await self._audit(editor, "knowledge_note.deleted", note, {})
        return note

    async def restore(self, editor: Editor, note: KnowledgeNote) -> KnowledgeNote:
        if note.deleted_at is None:
            return note
        note.deleted_at = note.purge_after = None
        note.deleted_by_user = note.deleted_by_instance = None
        note.updated_by_user, note.updated_by_instance = editor.user_id, None
        note.version += 1
        await self._flush_title(note)
        self._revision(note, editor, "restore", SaveOptions(), len(note.body.splitlines()), 0)
        state = await self.space(editor.workspace_id, note.space_id)
        await self.relink(note, state.space)
        await self._audit(editor, "knowledge_note.restored", note, {})
        return note

    async def revert(
        self, editor: Editor, note: KnowledgeNote, version: int, to_version: int, reason: str
    ) -> KnowledgeNote:
        if note.deleted_at is not None:
            raise _err(409, "NOTE_DELETED", "这篇笔记在回收站里，先恢复")
        if note.version != version:
            raise await self._conflict(note)
        target = await self.db.scalar(
            select(KnowledgeNoteRevision)
            .where(
                KnowledgeNoteRevision.note_id == note.id,
                KnowledgeNoteRevision.version == to_version,
            )
            .order_by(KnowledgeNoteRevision.created_at.desc())
            .limit(1)
        )
        if target is None or to_version >= note.version or target.change == "delete":
            raise _err(422, "REVISION_NOT_FOUND", "找不到要退回的那一版")
        previous_agent = note.updated_by_instance
        old_body = note.body
        props = target.properties or {}
        note.title = target.title
        note.title_key = links.normalize(target.title)
        note.body = target.body
        note.type = str(props.get("type") or note.type)
        note.tags = list(props.get("tags") or [])
        note.aliases = list(props.get("aliases") or [])
        note.folder = str(props.get("folder") or "")
        review = props.get("review_at")
        note.review_at = date.fromisoformat(review) if isinstance(review, str) else None
        note.updated_by_user, note.updated_by_instance = editor.user_id, None
        note.conflict_note = None
        note.version += 1
        await self._flush_title(note)
        added, removed = _line_stats(old_body, note.body)
        options = SaveOptions(reason=reason or f"退回到第 {to_version} 版")
        self._revision(note, editor, "revert", options, added, removed)
        state = await self.space(editor.workspace_id, note.space_id)
        await self.relink(note, state.space)
        if previous_agent is not None:
            await self.write_correction(
                previous_agent, editor, note, state.space, "撤销了你对", reason, suffix="的修改"
            )
        await self._audit(editor, "knowledge_note.reverted", note, {"to_version": to_version})
        return note

    async def confirm(
        self, editor: Editor, note: KnowledgeNote, version: int, confirmed: bool
    ) -> KnowledgeNote:
        if note.version != version:
            raise await self._conflict(note)
        if confirmed:
            note.confirmed_by, note.confirmed_at = editor.user_id, _now()
            note.conflict_note = None
        else:
            note.confirmed_by = note.confirmed_at = None
        await self._audit(editor, "knowledge_note.confirmed", note, {"confirmed": confirmed})
        return note

    async def purge(self, editor: Editor, note: KnowledgeNote) -> None:
        if note.deleted_at is None:
            raise _err(409, "NOT_IN_TRASH", "只能彻底删除回收站里的笔记")
        await self._audit(editor, "knowledge_note.purged", note, {"title": note.title})
        await self.db.delete(note)

    async def _audit(
        self, editor: Editor, action: str, note: KnowledgeNote, metadata: dict[str, Any]
    ) -> None:
        meta = dict(metadata)
        actor = editor.user_id
        if editor.instance is not None:
            meta.update(instance_id=str(editor.instance.id), instance_name=editor.instance.name)
            actor = editor.instance.acting_user_id  # 与智能体的其他写操作一致：记为登记人
        await self.audit.record(
            action,
            workspace_ids=[note.workspace_id],
            actor_id=actor,
            resource_type="knowledge_note",
            resource_id=note.id,
            metadata=meta,
            client_ip=editor.client_ip,
        )

    # ================================================================ 链接

    async def relink(self, note: KnowledgeNote, space: KnowledgeSpace) -> None:
        """按正文重建这篇笔记的链接，并补上别处指向它（新标题、别名）的未解析链接。"""
        await self.db.execute(delete(KnowledgeLink).where(KnowledgeLink.source_note_id == note.id))
        spaces = await self.space_map(note.workspace_id)
        by_name = {links.normalize(s.name): s for s in spaces.values()}
        for ref in links.parse(note.body):
            target_id: uuid.UUID | None = None
            space_key = None
            target_key = ref.key
            if ref.resource_type is None:
                if ref.space_key and ref.space_key in by_name and ref.rest_key:
                    space_key, target_key = ref.space_key, ref.rest_key
                    target_id = await self._find(
                        note.workspace_id, target_key, by_name[ref.space_key].id
                    )
                else:
                    target_id = await self._find(note.workspace_id, ref.key, space.id)
                    if target_id is None:
                        target_id = await self._find(note.workspace_id, ref.key, None)
            self.db.add(
                KnowledgeLink(
                    workspace_id=note.workspace_id,
                    source_note_id=note.id,
                    target_note_id=target_id,
                    target_text=ref.text,
                    target_key=target_key,
                    target_space_key=space_key,
                    target_resource_type=ref.resource_type,
                    target_resource_id=ref.resource_id,
                )
            )
        if note.deleted_at is None:
            keys = [note.title_key, *(links.normalize(a) for a in note.aliases)]
            space_key = links.normalize(space.name)
            await self.db.execute(
                update(KnowledgeLink)
                .where(
                    KnowledgeLink.workspace_id == note.workspace_id,
                    KnowledgeLink.target_note_id.is_(None),
                    KnowledgeLink.target_resource_type.is_(None),
                    KnowledgeLink.source_note_id != note.id,
                    or_(
                        and_(
                            KnowledgeLink.target_key.in_(keys),
                            or_(
                                KnowledgeLink.target_space_key.is_(None),
                                KnowledgeLink.target_space_key == space_key,
                            ),
                        ),
                        (KnowledgeLink.target_space_key + "/" + KnowledgeLink.target_key).in_(keys),
                    ),
                )
                .values(target_note_id=note.id)
            )

    async def _find(
        self, workspace_id: uuid.UUID, key: str, space_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        stmt = (
            select(KnowledgeNote.id)
            .where(
                KnowledgeNote.workspace_id == workspace_id,
                KnowledgeNote.deleted_at.is_(None),
                or_(KnowledgeNote.title_key == key, _alias_match(key)),
            )
            .order_by(KnowledgeNote.updated_at.desc())
            .limit(1)
        )
        if space_id is not None:
            stmt = stmt.where(KnowledgeNote.space_id == space_id)
        return await self.db.scalar(stmt)

    async def mark_similar(self, note: KnowledgeNote) -> None:
        """写入后的重复提醒：同名或标题高度相似的笔记（方案 3.8 第 6 条）。

        只提醒、不阻止：两篇都带上“有冲突”标记，直到人编辑处理（人保存时清空）。
        另一篇只改标记，不算一次修改（不加版本、不改更新时间）。
        """
        row = (
            await self.db.execute(
                select(KnowledgeNote.id, KnowledgeNote.title, KnowledgeSpace.name)
                .join(KnowledgeSpace, KnowledgeSpace.id == KnowledgeNote.space_id)
                .where(
                    KnowledgeNote.workspace_id == note.workspace_id,
                    KnowledgeNote.id != note.id,
                    KnowledgeNote.deleted_at.is_(None),
                    or_(
                        KnowledgeNote.title_key == note.title_key,
                        func.similarity(KnowledgeNote.title, note.title) >= _SIMILAR,
                    ),
                )
                .order_by(func.similarity(KnowledgeNote.title, note.title).desc())
                .limit(1)
            )
        ).first()
        if row is None:
            note.conflict_note = None
            return
        other_id, other_title, other_space = row
        note.conflict_note = f"和《{other_title}》（{other_space}）标题相近，可能重复"
        space_name = await self.db.scalar(
            select(KnowledgeSpace.name).where(KnowledgeSpace.id == note.space_id)
        )
        await self.db.execute(
            update(KnowledgeNote)
            .where(KnowledgeNote.id == other_id, KnowledgeNote.conflict_note.is_(None))
            .values(
                conflict_note=f"和《{note.title}》（{space_name}）标题相近，可能重复",
                updated_at=KnowledgeNote.updated_at,
            )
            .execution_options(synchronize_session=False)
        )

    # ================================================================ 纠正回写

    async def write_correction(
        self,
        instance_id: uuid.UUID,
        editor: Editor,
        note: KnowledgeNote,
        space: KnowledgeSpace,
        verb: str,
        reason: str,
        *,
        suffix: str = "",
    ) -> None:
        """人修改、撤销或删除了智能体写的内容：写进它知识库里的《收到的纠正》。

        见方案 3.9 第 4 条。
        """
        own = await self.db.scalar(
            select(KnowledgeSpace).where(KnowledgeSpace.instance_id == instance_id)
        )
        if own is None:
            return
        line = (
            f"- {date.today().isoformat()} {editor.name or '管理员'}{verb}"
            f"《{note.title}》（{space.name}）{suffix}：{reason or '没有写原因'}"
        )
        target = await self.db.scalar(
            select(KnowledgeNote).where(
                KnowledgeNote.space_id == own.id,
                KnowledgeNote.type == "correction",
                KnowledgeNote.deleted_at.is_(None),
            )
        )
        if target is None:
            header = (
                "人修改、撤销或删除了你写的内容时，工作台把原因记在这里。"
                "下次写入知识库之前先读一遍，别再犯同样的错。\n"
            )
            target = KnowledgeNote(
                workspace_id=own.workspace_id,
                space_id=own.id,
                title=CORRECTIONS_TITLE,
                title_key=links.normalize(CORRECTIONS_TITLE),
                body=header + "\n" + line,
                type="correction",
                source_kind="system",
                version=1,
            )
            self.db.add(target)
            try:
                async with self.db.begin_nested():
                    await self.db.flush()
            except IntegrityError:
                target.title = CORRECTIONS_TITLE + "（工作台）"
                target.title_key = links.normalize(target.title)
                self.db.add(target)
                await self.db.flush()
            self._revision(target, None, "create", SaveOptions(source_kind="system"), 1, 0)
        else:
            lines = target.body.splitlines()
            head, entries = lines[:2], lines[2:]
            entries = [*entries, line][-CORRECTIONS_MAX_LINES:]
            target.body = "\n".join([*head, *entries])
            target.updated_by_user = target.updated_by_instance = None
            target.version += 1
            self._revision(target, None, "update", SaveOptions(source_kind="system"), 1, 0)
        own.corrections_unseen += 1

    # ================================================================ 版本

    async def revisions(self, note: KnowledgeNote) -> list[RevisionOut]:
        rows = (
            await self.db.scalars(
                select(KnowledgeNoteRevision)
                .where(KnowledgeNoteRevision.note_id == note.id)
                .order_by(
                    KnowledgeNoteRevision.version.desc(), KnowledgeNoteRevision.created_at.desc()
                )
                .limit(200)
            )
        ).all()
        names = await self.authors((r.author_user for r in rows), (r.author_instance for r in rows))
        return [self._revision_out(r, names) for r in rows]

    def _revision_out(
        self,
        revision: KnowledgeNoteRevision,
        names: tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]],
    ) -> RevisionOut:
        return RevisionOut(
            version=revision.version,
            change=revision.change,
            title=revision.title,
            author=self.author(revision.author_user, revision.author_instance, names),
            reason=revision.reason,
            evidence=[Evidence.model_validate(e) for e in revision.evidence],
            added=revision.lines_added,
            removed=revision.lines_removed,
            created_at=revision.created_at,
            updated_at=revision.updated_at,
        )

    async def revision_detail(self, note: KnowledgeNote, version: int) -> RevisionDetail:
        rows = (
            await self.db.scalars(
                select(KnowledgeNoteRevision)
                .where(
                    KnowledgeNoteRevision.note_id == note.id,
                    KnowledgeNoteRevision.version <= version,
                )
                .order_by(
                    KnowledgeNoteRevision.version.desc(), KnowledgeNoteRevision.created_at.desc()
                )
                .limit(2)
            )
        ).all()
        if not rows or rows[0].version != version:
            raise not_found()
        current = rows[0]
        previous = rows[1].body if len(rows) > 1 else ""
        diff: list[DiffLine] = []
        for line in difflib.ndiff(previous.splitlines(), current.body.splitlines()):
            if line.startswith(("+ ", "- ", "  ")):
                diff.append(DiffLine(sign=line[0], text=line[2:]))
        names = await self.authors([current.author_user], [current.author_instance])
        return RevisionDetail(
            **self._revision_out(current, names).model_dump(),
            body=current.body,
            properties=dict(current.properties or {}),
            diff=diff,
        )

    # ================================================================ 图谱

    async def graph(
        self,
        workspace_id: uuid.UUID,
        *,
        space_ids: Sequence[uuid.UUID] | None = None,
        center: uuid.UUID | None = None,
        depth: int = 1,
        include_resources: bool = True,
    ) -> GraphOut:
        spaces = await self.space_map(workspace_id)
        alive = and_(KnowledgeNote.workspace_id == workspace_id, KnowledgeNote.deleted_at.is_(None))
        total = int(await self.db.scalar(select(func.count()).where(alive)) or 0)
        note_ids: set[uuid.UUID] | None = None
        if center is not None:
            note_ids = {center}
            frontier = {center}
            for _ in range(max(1, min(depth, 2))):
                if not frontier:
                    break
                rows = (
                    await self.db.execute(
                        select(KnowledgeLink.source_note_id, KnowledgeLink.target_note_id).where(
                            KnowledgeLink.workspace_id == workspace_id,
                            or_(
                                KnowledgeLink.source_note_id.in_(frontier),
                                KnowledgeLink.target_note_id.in_(frontier),
                            ),
                        )
                    )
                ).all()
                found = {i for pair in rows for i in pair if i is not None}
                frontier = found - note_ids
                note_ids |= found
                if len(note_ids) > 200:
                    break
        stmt = select(KnowledgeNote).where(alive)
        if note_ids is not None:
            stmt = stmt.where(KnowledgeNote.id.in_(note_ids))
        if space_ids:
            stmt = stmt.where(KnowledgeNote.space_id.in_(space_ids))
        notes = (
            await self.db.scalars(
                stmt.order_by(KnowledgeNote.updated_at.desc()).limit(GRAPH_MAX_NODES + 1)
            )
        ).all()
        truncated = len(notes) > GRAPH_MAX_NODES
        notes = notes[:GRAPH_MAX_NODES]
        ids = {n.id for n in notes}
        link_rows = (
            await self.db.execute(
                select(
                    KnowledgeLink.source_note_id,
                    KnowledgeLink.target_note_id,
                    KnowledgeLink.target_resource_type,
                    KnowledgeLink.target_resource_id,
                ).where(KnowledgeLink.source_note_id.in_(ids))
            )
        ).all()
        edges: list[GraphEdge] = []
        degree: dict[uuid.UUID, int] = {}
        resources: dict[tuple[str, uuid.UUID], None] = {}
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for source, target, rtype, rid in link_rows:
            dest = (
                target if target in ids else (rid if include_resources and rtype and rid else None)
            )
            if dest is None or dest == source:
                continue
            pair = (source, dest) if str(source) < str(dest) else (dest, source)
            if pair in seen:
                continue
            seen.add(pair)
            if rtype and rid and target is None:
                resources[(rtype, rid)] = None
            edges.append(GraphEdge(source=source, target=dest))
            degree[source] = degree.get(source, 0) + 1
            degree[dest] = degree.get(dest, 0) + 1
        titles = await self._resource_titles(resources.keys())
        nodes = [
            GraphNode(
                id=n.id,
                kind="note",
                title=n.title,
                space_id=n.space_id,
                space_kind=spaces[n.space_id].kind,
                type=n.type,
                author_kind="agent"
                if n.updated_by_instance
                else ("user" if n.updated_by_user else "system"),
                confirmed=n.confirmed_at is not None,
                degree=degree.get(n.id, 0),
            )
            for n in notes
        ]
        for (kind, rid), _ in resources.items():
            if (kind, rid) not in titles:
                edges = [e for e in edges if e.target != rid and e.source != rid]
                continue
            nodes.append(
                GraphNode(
                    id=rid,
                    kind=kind,
                    title=titles[(kind, rid)],
                    space_id=None,
                    space_kind=None,
                    type=None,
                    author_kind=None,
                    confirmed=False,
                    degree=degree.get(rid, 0),
                )
            )
        return GraphOut(nodes=nodes, edges=edges, truncated=truncated, total_notes=total)

    # ================================================================ 回收站清理（Worker）

    async def purge_expired(self) -> int:
        result = await self.db.execute(
            delete(KnowledgeNote).where(
                KnowledgeNote.purge_after.is_not(None), KnowledgeNote.purge_after < _now()
            )
        )
        await self.db.execute(
            delete(KnowledgeReadStat).where(
                KnowledgeReadStat.day < date.today() - timedelta(days=400)
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


def require(access: WorkspaceAccess, minimum: Role) -> None:
    if not has_role(access.role, minimum):
        raise ApiError(403, "FORBIDDEN", "没有权限执行此操作")
