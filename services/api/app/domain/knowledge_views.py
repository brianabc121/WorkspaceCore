"""知识库的列表与汇总查询（T-0098）：笔记列表、[[ 补全、变更记录、回收站、智能体的检索与指标。

写入路径和权限规则在 app/domain/knowledge_base.py。
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, Text, and_, case, func, literal, null, or_, select, union_all
from sqlalchemy.sql import ColumnElement

from app.domain import knowledge_links as links
from app.domain.knowledge_base import KnowledgeBase, SpaceState, _alias_match
from app.domain.tasks import decode_cursor, encode_cursor
from app.models import (
    AgentInstance,
    AiToolExecution,
    KnowledgeBriefRead,
    KnowledgeGuardEvent,
    KnowledgeNote,
    KnowledgeNoteRevision,
    KnowledgeReadStat,
    KnowledgeSpace,
    Project,
    ProjectAgent,
)
from app.schemas.knowledge_base import (
    AgentKnowledgeOut,
    AgentMetrics,
    AgentProjectOut,
    AgentWeek,
    ChangeOut,
    ChangePage,
    NotePage,
    NoteSuggestion,
    SpaceActivity,
    SpaceWeek,
    TrashItem,
    TrashPage,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _text_match(q: str) -> ColumnElement[bool]:
    pattern = f"%{_escape_like(q)}%"
    return or_(
        KnowledgeNote.title.ilike(pattern, escape="\\"),
        KnowledgeNote.body.ilike(pattern, escape="\\"),
        func.array_to_string(KnowledgeNote.aliases, " ").ilike(pattern, escape="\\"),
        func.array_to_string(KnowledgeNote.tags, " ").ilike(pattern, escape="\\"),
    )


def _bucket(q: str) -> Any:
    key = links.normalize(q)
    return case(
        (KnowledgeNote.title_key == key, 0),
        (KnowledgeNote.title.ilike(f"{_escape_like(q)}%", escape="\\"), 1),
        (KnowledgeNote.title.ilike(f"%{_escape_like(q)}%", escape="\\"), 2),
        else_=3,
    )


def excerpt(body: str, q: str | None, width: int = 160) -> str:
    plain = " ".join(links.strip_code(body).split())
    if not plain:
        return ""
    index = plain.lower().find(q.lower()) if q else -1
    if index < 0:
        return plain[:width] + ("…" if len(plain) > width else "")
    start = max(0, index - width // 3)
    end = min(len(plain), start + width)
    return ("…" if start else "") + plain[start:end] + ("…" if end < len(plain) else "")


class KnowledgeService(KnowledgeBase):
    # ---------------------------------------------------------------- 笔记列表（人）

    async def list_notes(
        self,
        workspace_id: uuid.UUID,
        *,
        space_id: uuid.UUID | None,
        folder: str | None,
        note_type: str | None,
        tag: str | None,
        author: str | None,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> NotePage:
        offset = decode_cursor(cursor)
        stmt = select(KnowledgeNote).where(
            KnowledgeNote.workspace_id == workspace_id, KnowledgeNote.deleted_at.is_(None)
        )
        if space_id is not None:
            stmt = stmt.where(KnowledgeNote.space_id == space_id)
        if folder is not None:
            stmt = stmt.where(KnowledgeNote.folder == folder)
        if note_type is not None:
            stmt = stmt.where(KnowledgeNote.type == note_type)
        if tag is not None:
            stmt = stmt.where(KnowledgeNote.tags.contains([tag]))
        if author == "agent":
            stmt = stmt.where(KnowledgeNote.updated_by_instance.is_not(None))
        elif author == "user":
            stmt = stmt.where(KnowledgeNote.updated_by_user.is_not(None))
        order: list[Any] = [KnowledgeNote.pinned.desc()]
        if q:
            stmt = stmt.where(_text_match(q))
            order = [_bucket(q), *order]
        stmt = stmt.order_by(*order, KnowledgeNote.updated_at.desc(), KnowledgeNote.id)
        rows = (await self.db.scalars(stmt.offset(offset).limit(limit + 1))).all()
        spaces = await self.space_map(workspace_id)
        items = await self.summaries(rows[:limit], spaces)
        return NotePage(
            items=items,
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    async def suggest(
        self, workspace_id: uuid.UUID, q: str, *, limit: int = 10
    ) -> list[NoteSuggestion]:
        """编辑器 [[ 补全：标题或别名包含输入的文字，前缀匹配在前。"""
        pattern = f"%{_escape_like(q)}%"
        stmt = (
            select(KnowledgeNote, KnowledgeSpace)
            .join(KnowledgeSpace, KnowledgeSpace.id == KnowledgeNote.space_id)
            .where(
                KnowledgeNote.workspace_id == workspace_id,
                KnowledgeNote.deleted_at.is_(None),
                or_(
                    KnowledgeNote.title.ilike(pattern, escape="\\"),
                    func.array_to_string(KnowledgeNote.aliases, " ").ilike(pattern, escape="\\"),
                ),
            )
            .order_by(_bucket(q), KnowledgeNote.updated_at.desc())
            .limit(limit)
        )
        return [
            NoteSuggestion(
                id=note.id,
                title=note.title,
                space_id=space.id,
                space_kind=space.kind,
                space_name=space.name,
            )
            for note, space in (await self.db.execute(stmt)).all()
        ]

    # ---------------------------------------------------------------- 回收站

    async def trash(self, workspace_id: uuid.UUID, *, cursor: str | None, limit: int) -> TrashPage:
        offset = decode_cursor(cursor)
        rows = (
            await self.db.scalars(
                select(KnowledgeNote)
                .where(
                    KnowledgeNote.workspace_id == workspace_id,
                    KnowledgeNote.deleted_at.is_not(None),
                )
                .order_by(KnowledgeNote.deleted_at.desc(), KnowledgeNote.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        spaces = await self.space_map(workspace_id)
        names = await self.authors(
            (n.deleted_by_user for n in rows), (n.deleted_by_instance for n in rows)
        )
        items = []
        for note in rows[:limit]:
            if note.deleted_at is None or note.purge_after is None:
                continue
            space = spaces[note.space_id]
            items.append(
                TrashItem(
                    id=note.id,
                    title=note.title,
                    space_id=space.id,
                    space_kind=space.kind,
                    space_name=space.name,
                    deleted_at=note.deleted_at,
                    deleted_by=self.author(
                        note.deleted_by_user, note.deleted_by_instance, names, system_if_empty=False
                    ),
                    purge_after=note.purge_after,
                )
            )
        return TrashPage(
            items=items,
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    # ---------------------------------------------------------------- 变更记录

    async def changes(
        self,
        workspace_id: uuid.UUID,
        *,
        space_id: uuid.UUID | None,
        instance_id: uuid.UUID | None,
        kind: str | None,
        cursor: str | None,
        limit: int,
    ) -> ChangePage:
        """笔记的每次改动与保护记录，按时间倒序（方案 3.8 第 8 条）。"""
        offset = decode_cursor(cursor)
        r = KnowledgeNoteRevision
        g = KnowledgeGuardEvent
        revisions = (
            select(
                r.id.label("id"),
                literal("revision").label("kind"),
                r.change.label("change"),
                null().cast(Text).label("guard"),
                r.note_id.label("note_id"),
                r.author_user.label("author_user"),
                r.author_instance.label("author_instance"),
                r.reason.label("reason"),
                literal("").label("detail"),
                r.version.label("version"),
                r.lines_added.label("added"),
                r.lines_removed.label("removed"),
                null().cast(Text).label("instance_name"),
                r.title.label("title"),
                r.created_at.label("created_at"),
            )
            .join(KnowledgeNote, KnowledgeNote.id == r.note_id)
            .where(r.workspace_id == workspace_id)
        )
        guards = select(
            g.id.label("id"),
            literal("guard").label("kind"),
            null().cast(Text).label("change"),
            g.kind.label("guard"),
            null().label("note_id"),
            null().label("author_user"),
            g.instance_id.label("author_instance"),
            literal("").label("reason"),
            g.detail.label("detail"),
            null().label("version"),
            literal(0).label("added"),
            literal(0).label("removed"),
            g.instance_name.label("instance_name"),
            g.note_title.label("title"),
            g.created_at.label("created_at"),
        ).where(g.workspace_id == workspace_id)
        if space_id is not None:
            revisions = revisions.where(KnowledgeNote.space_id == space_id)
            guards = guards.where(literal(False))
        if instance_id is not None:
            revisions = revisions.where(r.author_instance == instance_id)
            guards = guards.where(g.instance_id == instance_id)
        if kind == "guard":
            revisions = revisions.where(literal(False))
        elif kind is not None:
            revisions = revisions.where(r.change == kind)
            guards = guards.where(literal(False))
        feed = union_all(revisions, guards).subquery()
        rows = (
            await self.db.execute(
                select(feed)
                .order_by(feed.c.created_at.desc(), feed.c.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        note_ids = {row.note_id for row in page if row.note_id}
        notes: dict[uuid.UUID, KnowledgeNote] = {}
        latest: dict[uuid.UUID, int] = {}
        if note_ids:
            for note in (
                await self.db.scalars(select(KnowledgeNote).where(KnowledgeNote.id.in_(note_ids)))
            ).all():
                notes[note.id] = note
                latest[note.id] = note.version
        spaces = await self.space_map(workspace_id)
        names = await self.authors(
            (row.author_user for row in page), (row.author_instance for row in page)
        )
        items = []
        for row in page:
            found = notes.get(row.note_id) if row.note_id else None
            space = spaces.get(found.space_id) if found else None
            if row.kind == "guard":
                author = self.author(None, row.author_instance, names)
                if row.author_instance not in names[1]:
                    author = author.model_copy(update={"name": row.instance_name or author.name})
            else:
                author = self.author(row.author_user, row.author_instance, names)
            revertable = bool(
                found
                and found.deleted_at is None
                and row.kind == "revision"
                # 恢复的上一版是“删除”，退回没有意义：在回收站页面删除即可
                and row.change in ("update", "rename", "revert")
                and row.version == latest.get(found.id)
                and (row.version or 0) > 1
            )
            items.append(
                ChangeOut(
                    id=row.id,
                    kind=row.kind,
                    change=row.change,
                    guard=row.guard,
                    note_id=row.note_id,
                    note_title=row.title,
                    note_deleted=bool(found and found.deleted_at),
                    space_id=space.id if space else None,
                    space_kind=space.kind if space else None,
                    space_name=space.name if space else None,
                    author=author,
                    reason=row.reason or "",
                    detail=row.detail or "",
                    version=row.version,
                    added=row.added or 0,
                    removed=row.removed or 0,
                    revertable=revertable,
                    created_at=row.created_at,
                )
            )
        return ChangePage(
            items=items,
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    # ---------------------------------------------------------------- 智能体：检索

    async def readable_spaces(self, instance: AgentInstance) -> list[tuple[SpaceState, str]]:
        await self.ensure_spaces(instance.workspace_id)
        participation = await self.participation(instance.id)
        out = []
        for state in await self.spaces(instance.workspace_id):
            level = self.agent_level(state, instance.id, participation)
            if level != "none":
                out.append((state, level))
        return out

    async def agent_search(
        self,
        readable: Sequence[tuple[SpaceState, str]],
        *,
        q: str,
        space_ids: Sequence[uuid.UUID] | None,
        kinds: Sequence[str] | None,
        note_type: str | None,
        limit: int,
    ) -> list[KnowledgeNote]:
        allowed = [
            s.space.id
            for s, _ in readable
            if (not space_ids or s.space.id in space_ids) and (not kinds or s.space.kind in kinds)
        ]
        if not allowed:
            return []
        stmt = select(KnowledgeNote).where(
            KnowledgeNote.space_id.in_(allowed),
            KnowledgeNote.deleted_at.is_(None),
            or_(_text_match(q), _alias_match(links.normalize(q))),
        )
        if note_type is not None:
            stmt = stmt.where(KnowledgeNote.type == note_type)
        stmt = stmt.order_by(
            _bucket(q),
            KnowledgeNote.source_kind == "external",
            KnowledgeNote.updated_at.desc(),
        ).limit(limit)
        return list((await self.db.scalars(stmt)).all())

    # ---------------------------------------------------------------- 智能体知识库页

    async def agent_weeks(self, instance_id: uuid.UUID, own_space: uuid.UUID) -> list[AgentWeek]:
        weeks = _weeks()
        start = weeks[0]
        reads: dict[tuple[date, bool], int] = {}
        stat = KnowledgeReadStat
        week = func.date_trunc("week", stat.day).cast(Date)
        # 按知识库分组再在这里归到“自己的 / 别的”：GROUP BY 里不能有带参数的比较表达式
        for w, space, n in (
            await self.db.execute(
                select(week, KnowledgeNote.space_id, func.sum(stat.reads))
                .join(KnowledgeNote, KnowledgeNote.id == stat.note_id)
                .where(stat.instance_id == instance_id, stat.day >= start)
                .group_by(week, KnowledgeNote.space_id)
            )
        ).all():
            key = (w, space == own_space)
            reads[key] = reads.get(key, 0) + int(n or 0)
        r = KnowledgeNoteRevision
        wk = func.date_trunc("week", r.created_at).cast(Date)
        writes = {
            w: int(n)
            for w, n in (
                await self.db.execute(
                    select(wk, func.count())
                    .where(
                        r.author_instance == instance_id,
                        r.change.in_(("create", "update", "rename")),
                        r.created_at >= datetime.combine(start, datetime.min.time(), UTC),
                    )
                    .group_by(wk)
                )
            ).all()
        }
        return [
            AgentWeek(
                week_start=w,
                reads_own=reads.get((w, True), 0),
                reads_shared=reads.get((w, False), 0),
                writes=writes.get(w, 0),
            )
            for w in weeks
        ]

    async def space_activity(self, workspace_id: uuid.UUID, space_id: uuid.UUID) -> SpaceActivity:
        weeks = _weeks()
        r = KnowledgeNoteRevision
        wk = func.date_trunc("week", r.created_at).cast(Date)
        counts: dict[tuple[date, bool], int] = {}
        for w, by_agent, n in (
            await self.db.execute(
                select(wk, r.author_instance.is_not(None), func.count())
                .join(KnowledgeNote, KnowledgeNote.id == r.note_id)
                .where(
                    KnowledgeNote.workspace_id == workspace_id,
                    KnowledgeNote.space_id == space_id,
                    r.change.in_(("create", "update", "rename", "revert")),
                    or_(r.author_user.is_not(None), r.author_instance.is_not(None)),
                    r.created_at >= datetime.combine(weeks[0], datetime.min.time(), UTC),
                )
                .group_by(wk, r.author_instance.is_not(None))
            )
        ).all():
            counts[(w, bool(by_agent))] = int(n)
        return SpaceActivity(
            weeks=[
                SpaceWeek(
                    week_start=w, human=counts.get((w, False), 0), agent=counts.get((w, True), 0)
                )
                for w in weeks
            ]
        )

    async def agent_overview(self, instance: AgentInstance) -> AgentKnowledgeOut:
        own = await self.agent_space(instance)
        since = datetime.now(UTC) - timedelta(days=30)
        r = KnowledgeNoteRevision
        mine = r.author_instance == instance.id
        recent = r.created_at >= since

        async def count(stmt: Any) -> int:
            return int(await self.db.scalar(stmt) or 0)

        writes = await count(
            select(func.count()).where(mine, recent, r.change.in_(("create", "update", "rename")))
        )
        writes_shared = await count(
            select(func.count())
            .select_from(r)
            .join(KnowledgeNote, KnowledgeNote.id == r.note_id)
            .where(
                mine,
                recent,
                r.change.in_(("create", "update", "rename")),
                KnowledgeNote.space_id != own.id,
            )
        )
        deletes = await count(select(func.count()).where(mine, recent, r.change == "delete"))
        executions = AiToolExecution
        searches = await count(
            select(func.count()).where(
                executions.instance_id == instance.id,
                executions.tool_name == "knowledge_search",
                executions.created_at >= since,
            )
        )
        reads = await count(
            select(func.count()).where(
                executions.instance_id == instance.id,
                executions.tool_name.in_(("knowledge_get", "knowledge_brief")),
                executions.created_at >= since,
            )
        )
        read_by_others = await count(
            select(func.coalesce(func.sum(KnowledgeReadStat.reads), 0))
            .select_from(KnowledgeReadStat)
            .join(KnowledgeNote, KnowledgeNote.id == KnowledgeReadStat.note_id)
            .where(
                KnowledgeNote.created_by_instance == instance.id,
                KnowledgeReadStat.instance_id != instance.id,
                KnowledgeReadStat.day >= since.date(),
            )
        )
        corrected = await count(
            select(func.count())
            .select_from(r)
            .join(KnowledgeNote, KnowledgeNote.id == r.note_id)
            .where(
                recent,
                r.author_user.is_not(None),
                r.change.in_(("update", "revert", "delete")),
                KnowledgeNote.created_by_instance == instance.id,
            )
        )
        notes = await count(
            select(func.count()).where(
                KnowledgeNote.space_id == own.id, KnowledgeNote.deleted_at.is_(None)
            )
        )
        overdue = await count(
            select(func.count()).where(
                KnowledgeNote.created_by_instance == instance.id,
                KnowledgeNote.deleted_at.is_(None),
                KnowledgeNote.review_at < date.today(),
            )
        )
        blocked = await count(
            select(func.count()).where(
                KnowledgeGuardEvent.instance_id == instance.id,
                KnowledgeGuardEvent.created_at >= since,
            )
        )
        rows = (
            await self.db.execute(
                select(ProjectAgent, Project.title, KnowledgeBriefRead.version)
                .join(Project, Project.id == ProjectAgent.project_id)
                .outerjoin(
                    KnowledgeBriefRead,
                    and_(
                        KnowledgeBriefRead.project_id == ProjectAgent.project_id,
                        KnowledgeBriefRead.instance_id == ProjectAgent.instance_id,
                    ),
                )
                .where(ProjectAgent.instance_id == instance.id)
                .order_by(Project.title)
            )
        ).all()
        brief_versions: dict[uuid.UUID, int] = {}
        project_ids = [pa.project_id for pa, *_ in rows]
        if project_ids:
            for project_id, version in (
                await self.db.execute(
                    select(KnowledgeSpace.project_id, KnowledgeNote.version)
                    .join(KnowledgeNote, KnowledgeNote.space_id == KnowledgeSpace.id)
                    .where(
                        KnowledgeSpace.project_id.in_(project_ids),
                        KnowledgeNote.type == "brief",
                        KnowledgeNote.deleted_at.is_(None),
                    )
                )
            ).all():
                if project_id is not None:
                    brief_versions[project_id] = version
        weekly = await self.agent_weeks(instance.id, own.id)
        return AgentKnowledgeOut(
            weekly=weekly,
            instance_id=instance.id,
            name=instance.name,
            space_id=own.id,
            writes_enabled=own.writes_enabled,
            deletes_paused_at=own.deletes_paused_at,
            corrections_unseen=own.corrections_unseen,
            metrics=AgentMetrics(
                notes=notes,
                writes=writes,
                writes_shared=writes_shared,
                deletes=deletes,
                searches=searches,
                reads=reads,
                read_by_others=read_by_others,
                corrected=corrected,
                overdue_reviews=overdue,
                blocked=blocked,
            ),
            projects=[
                AgentProjectOut(
                    project_id=pa.project_id,
                    title=title,
                    access=pa.access,
                    brief_version_read=read_version,
                    brief_version=brief_versions.get(pa.project_id),
                )
                for pa, title, read_version in rows
            ],
        )


def _weeks(count: int = 8) -> list[date]:
    """近 count 周的周一（UTC），最早的在前；和 PostgreSQL 的 date_trunc('week') 一致。"""
    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=i) for i in range(count - 1, -1, -1)]
