"""智能体的知识库工具（MCP，docs/v2/KNOWLEDGE_PLAN.md 3.7、3.8）。

由 app/domain/mcp_tools.py 调用：参数校验 → 权限范围与角色（AiToolService.check）→ 执行 → 写一条
ai_tool_executions 记录（风险等级 knowledge）。写工具不进审批队列，直接执行；保护措施在
app/domain/knowledge_base.py（知识库权限、简报只有人能改、密钥拦截、限流与自动暂停、版本）。
"""

import uuid
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.core.errors import ApiError
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.knowledge_base import Editor, NoteFields, SaveOptions, SpaceState, request_hash
from app.domain.knowledge_views import KnowledgeService, excerpt
from app.models import AgentInstance, KnowledgeBriefRead, KnowledgeNote, KnowledgeSpace
from app.schemas.ai_tools import ToolSpec
from app.schemas.knowledge_tools import (
    BacklinkRef,
    BriefOut,
    BriefText,
    KnowledgeBriefInput,
    KnowledgeBriefOutput,
    KnowledgeDeleteInput,
    KnowledgeDeleteOutput,
    KnowledgeGetInput,
    KnowledgeGetOutput,
    KnowledgeListInput,
    KnowledgeListOutput,
    KnowledgeNoteOut,
    KnowledgeSearchInput,
    KnowledgeSearchOutput,
    KnowledgeWriteInput,
    KnowledgeWriteOutput,
    LinkRef,
    NoteHit,
    NoteHitText,
    NoteItem,
    NoteItemText,
    NoteText,
    Origin,
    SpaceItem,
    SpaceName,
    SpaceRef,
)

KNOWLEDGE_TOOLS = (
    "knowledge_search",
    "knowledge_get",
    "knowledge_list",
    "knowledge_brief",
    "knowledge_write",
    "knowledge_delete",
)


def _tool_error(error: ApiError) -> ToolCallError:
    if error.status_code == 404:
        return ToolCallError("NOT_FOUND", "要访问的知识库或笔记不存在，或你没有权限")
    if error.status_code == 422 and error.code == "VALIDATION_FAILED":
        return ToolCallError("VALIDATION_FAILED", error.message)
    return ToolCallError(error.code, error.message)


class _Context:
    def __init__(self, kb: KnowledgeService, instance: AgentInstance) -> None:
        self.kb = kb
        self.instance = instance
        self.levels: dict[uuid.UUID, str] = {}
        self.spaces: dict[uuid.UUID, KnowledgeSpace] = {}
        self.archived: dict[uuid.UUID, bool] = {}
        self.readable_states: list[tuple[SpaceState, str]] = []

    async def load(self) -> None:
        self.readable_states = await self.kb.readable_spaces(self.instance)
        for state, level in self.readable_states:
            self.levels[state.space.id] = level
            self.spaces[state.space.id] = state.space
            self.archived[state.space.id] = state.archived

    def ref(self, space_id: uuid.UUID) -> SpaceRef:
        space = self.spaces[space_id]
        return SpaceRef(
            id=space.id,
            kind=space.kind,
            access=self.levels[space_id],
            untrusted=SpaceName(name=space.name),
        )

    def readable(self, note: KnowledgeNote) -> bool:
        return note.deleted_at is None and note.space_id in self.levels

    async def origin(self, note: KnowledgeNote) -> Origin:
        names = await self.kb.authors([], [note.updated_by_instance])
        by = (
            "agent" if note.updated_by_instance else ("human" if note.updated_by_user else "system")
        )
        return Origin(
            by=by,
            agent_name=names[1].get(note.updated_by_instance) if note.updated_by_instance else None,
            confirmed=note.confirmed_at is not None,
            conflict=note.conflict_note is not None,
            external=note.source_kind == "external",
        )

    async def notices(self) -> list[str]:
        own = await self.kb.agent_space(self.instance)
        out = []
        if own.corrections_unseen:
            out.append(
                f"你有 {own.corrections_unseen} 条新的纠正：先用 knowledge_get 读你知识库里的"
                "《收到的纠正》，写入前避免同样的错误。"
            )
        if not own.writes_enabled:
            out.append("管理员关闭了你的写入，现在只能读。")
        if own.deletes_paused_at is not None:
            out.append("你的删除被自动暂停了，等管理员解除后才能再删除。")
        # 参与的项目里简报更新了而它还没读（方案 3.10）：最多提示 3 个
        stale = [b for b in await self.kb.brief_status(self.instance.id) if b.unread]
        for b in stale[:3]:
            seen = f"你读过第 {b.read} 版" if b.read else "你还没读过"
            # 不带项目名：提示是工作台写的固定文字，人填的内容只出现在 untrusted 里
            out.append(
                f"你参与的项目 {b.project_id} 的简报已更新到第 {b.version} 版（{seen}）："
                f"处理这个项目的任务前，先用 knowledge_brief 读。"
            )
        return out


async def _search(ctx: _Context, args: KnowledgeSearchInput) -> KnowledgeSearchOutput:
    notes = await ctx.kb.agent_search(
        ctx.readable_states,
        q=args.query,
        space_ids=args.space_ids,
        kinds=args.kinds,
        note_type=args.type,
        limit=args.limit,
    )
    items = [
        NoteHit(
            id=note.id,
            space=ctx.ref(note.space_id),
            type=note.type,
            version=note.version,
            updated_at=note.updated_at,
            origin=await ctx.origin(note),
            untrusted=NoteHitText(title=note.title, snippet=excerpt(note.body, args.query)),
        )
        for note in notes
    ]
    return KnowledgeSearchOutput(items=items, notices=await ctx.notices())


async def _get(ctx: _Context, args: KnowledgeGetInput) -> KnowledgeGetOutput:
    note: KnowledgeNote | None
    if args.note_id is not None:
        note = await ctx.kb.db.get(KnowledgeNote, args.note_id)
        if note is not None and note.workspace_id != ctx.instance.workspace_id:
            note = None
    else:
        if args.space_id not in ctx.levels or args.title is None or args.space_id is None:
            raise ToolCallError("NOT_FOUND", "要访问的知识库或笔记不存在，或你没有权限")
        note = await ctx.kb.note_by_title(args.space_id, args.title)
    if note is None or not ctx.readable(note):
        raise ToolCallError("NOT_FOUND", "要访问的知识库或笔记不存在，或你没有权限")
    spaces = await ctx.kb.space_map(ctx.instance.workspace_id)
    out_links = await ctx.kb.out_links(note, spaces)
    backlinks = [b for b in await ctx.kb.backlinks(note, spaces) if b.space_id in ctx.levels]
    await ctx.kb.record_read(ctx.instance, note)
    own = await ctx.kb.agent_space(ctx.instance)
    if note.space_id == own.id and note.type == "correction" and own.corrections_unseen:
        own.corrections_unseen = 0
    await ctx.kb.db.commit()
    result = KnowledgeNoteOut(
        id=note.id,
        space=ctx.ref(note.space_id),
        type=note.type,
        version=note.version,
        updated_at=note.updated_at,
        review_at=note.review_at,
        origin=await ctx.origin(note),
        untrusted=NoteText(
            title=note.title,
            body=note.body,
            aliases=list(note.aliases),
            tags=list(note.tags),
            folder=note.folder,
            conflict_note=note.conflict_note,
            links=[
                LinkRef(
                    note_id=link.note_id
                    if link.note_id and (await _visible(ctx, link.note_id))
                    else None,
                    text=link.text,
                )
                for link in out_links
            ],
            backlinks=[BacklinkRef(note_id=b.note_id, title=b.title) for b in backlinks],
        ),
    )
    return KnowledgeGetOutput(item=result, notices=await ctx.notices())


async def _visible(ctx: _Context, note_id: uuid.UUID) -> bool:
    note = await ctx.kb.db.get(KnowledgeNote, note_id)
    return note is not None and ctx.readable(note)


async def _list(ctx: _Context, args: KnowledgeListInput) -> KnowledgeListOutput:
    if args.space_id is None:
        outs = await ctx.kb.space_outs([state for state, _ in ctx.readable_states])
        spaces = [
            SpaceItem(
                **ctx.ref(o.id).model_dump(),
                notes=o.notes,
                archived=o.archived,
                project_id=o.project_id,
            )
            for o in outs
        ]
        return KnowledgeListOutput(
            spaces=spaces, notes=[], has_more=False, notices=await ctx.notices()
        )
    if args.space_id not in ctx.levels:
        raise ToolCallError("NOT_FOUND", "要访问的知识库或笔记不存在，或你没有权限")
    stmt = select(KnowledgeNote).where(
        KnowledgeNote.space_id == args.space_id, KnowledgeNote.deleted_at.is_(None)
    )
    if args.folder is not None:
        stmt = stmt.where(KnowledgeNote.folder == args.folder)
    rows = (
        await ctx.kb.db.scalars(
            stmt.order_by(KnowledgeNote.folder, KnowledgeNote.title)
            .offset(args.offset)
            .limit(args.limit + 1)
        )
    ).all()
    notes = [
        NoteItem(
            id=n.id,
            type=n.type,
            version=n.version,
            updated_at=n.updated_at,
            untrusted=NoteItemText(title=n.title, folder=n.folder),
        )
        for n in rows[: args.limit]
    ]
    return KnowledgeListOutput(
        spaces=[], notes=notes, has_more=len(rows) > args.limit, notices=await ctx.notices()
    )


async def _brief(ctx: _Context, args: KnowledgeBriefInput) -> KnowledgeBriefOutput:
    space_id = next(
        (
            sid
            for sid, s in ctx.spaces.items()
            if s.kind == "project" and s.project_id == args.project_id
        ),
        None,
    )
    if space_id is None:
        raise ToolCallError("NOT_FOUND", "没有这个项目，或你没有参与这个项目")
    note = await ctx.kb.db.scalar(
        select(KnowledgeNote).where(
            KnowledgeNote.space_id == space_id,
            KnowledgeNote.type == "brief",
            KnowledgeNote.deleted_at.is_(None),
        )
    )
    brief = None
    if note is not None:
        stmt = pg_insert(KnowledgeBriefRead).values(
            workspace_id=ctx.instance.workspace_id,
            project_id=args.project_id,
            instance_id=ctx.instance.id,
            version=note.version,
        )
        await ctx.kb.db.execute(
            stmt.on_conflict_do_update(
                index_elements=["project_id", "instance_id"],
                set_={"version": note.version, "read_at": stmt.excluded.read_at},
            )
        )
        await ctx.kb.record_read(ctx.instance, note)
        await ctx.kb.db.commit()
        brief = BriefOut(
            note_id=note.id,
            version=note.version,
            updated_at=note.updated_at,
            untrusted=BriefText(title=note.title, body=note.body),
        )
    return KnowledgeBriefOutput(
        project_id=args.project_id, space_id=space_id, brief=brief, notices=await ctx.notices()
    )


async def _write(ctx: _Context, args: KnowledgeWriteInput) -> KnowledgeWriteOutput:
    kb, instance = ctx.kb, ctx.instance
    digest = request_hash(args.model_dump(mode="json", exclude={"idempotency_key"}))
    existing = await kb.replay(instance, args.idempotency_key, digest)
    if existing is not None:
        return KnowledgeWriteOutput(
            note_id=existing.note_id,
            version=existing.version,
            created=existing.change == "create",
            replayed=True,
            notices=await ctx.notices(),
        )
    editor = Editor(workspace_id=instance.workspace_id, instance=instance)
    fields = NoteFields(
        title=args.title,
        body=args.body,
        type=args.type,
        tags=args.tags,
        folder=args.folder,
        aliases=args.aliases,
        review_at=args.review_at,
        review_set=True,
    )
    options = SaveOptions(
        reason=args.reason,
        evidence=[e.model_dump() for e in args.evidence],
        source_kind=args.source_kind,
        idempotency_key=args.idempotency_key,
        request_hash=digest,
    )
    if args.note_id is None:
        fields.space_id = args.space_id
        note = await kb.create(editor, fields, options)
        created = True
    else:
        note = await kb.note(instance.workspace_id, args.note_id, lock=True)
        if note.space_id != args.space_id or note.space_id not in ctx.levels:
            raise ToolCallError("NOT_FOUND", "要访问的知识库或笔记不存在，或你没有权限")
        note = await kb.update(editor, note, args.base_version or 0, fields, options)
        created = False
    note_id, version, conflict = note.id, note.version, note.conflict_note
    await kb.db.commit()
    notices = await ctx.notices()
    if conflict is not None:
        notices.insert(0, f"已保存。{conflict}：请用 knowledge_get 看看那篇，能合并就合并到一篇。")
    return KnowledgeWriteOutput(
        note_id=note_id,
        version=version,
        created=created,
        replayed=False,
        notices=notices,
    )


async def _delete(ctx: _Context, args: KnowledgeDeleteInput) -> KnowledgeDeleteOutput:
    kb, instance = ctx.kb, ctx.instance
    digest = request_hash(args.model_dump(mode="json", exclude={"idempotency_key"}))
    existing = await kb.replay(instance, args.idempotency_key, digest)
    if existing is not None:
        note = await kb.db.get(KnowledgeNote, existing.note_id)
        return KnowledgeDeleteOutput(
            note_id=existing.note_id,
            purge_after=note.purge_after if note else None,
            replayed=True,
            notices=await ctx.notices(),
        )
    note = await kb.note(instance.workspace_id, args.note_id, lock=True)
    if note.space_id not in ctx.levels:
        raise ToolCallError("NOT_FOUND", "要访问的知识库或笔记不存在，或你没有权限")
    editor = Editor(workspace_id=instance.workspace_id, instance=instance)
    options = SaveOptions(
        reason=args.reason, idempotency_key=args.idempotency_key, request_hash=digest
    )
    note = await kb.delete(editor, note, args.base_version, options)
    purge_after = note.purge_after
    await kb.db.commit()
    return KnowledgeDeleteOutput(
        note_id=args.note_id, purge_after=purge_after, replayed=False, notices=await ctx.notices()
    )


_HANDLERS: dict[str, Any] = {
    "knowledge_search": _search,
    "knowledge_get": _get,
    "knowledge_list": _list,
    "knowledge_brief": _brief,
    "knowledge_write": _write,
    "knowledge_delete": _delete,
}


def _display(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """记录里只留能看懂这次调用的字段；正文不进记录（变更记录和版本里有）。"""
    if name == "knowledge_write":
        keep = ("space_id", "note_id", "base_version", "title", "type", "reason", "source_kind")
        return {k: arguments.get(k) for k in keep if arguments.get(k) is not None}
    return arguments


def _summary(name: str, arguments: dict[str, Any]) -> str:
    if name == "knowledge_write":
        verb = "修改" if arguments.get("note_id") else "新建"
        return f"知识库：{verb}《{arguments.get('title', '')}》"
    if name == "knowledge_delete":
        return "知识库：删除一篇笔记（进回收站）"
    return f"读取：{name}"


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = parsed.model_dump(mode="json")
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(
            requester, spec, _display(spec.name, arguments), denied, request_id=request_id
        )
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    instance_id = requester.instance.id
    kb = KnowledgeService(service.db, service.settings, service.redis)
    ctx = _Context(kb, requester.instance)
    try:
        await ctx.load()
        output: BaseModel = await _HANDLERS[spec.name](ctx, parsed)
    except (ToolCallError, ApiError, IntegrityError) as error:
        await service.db.rollback()
        if isinstance(error, IntegrityError):
            # 并发的同一个幂等键：先完成的那次已经写入
            error = ToolCallError(
                "IDEMPOTENCY_IN_PROGRESS", "同一个请求仍在处理，请稍后用同一个键重试"
            )
        failure = _tool_error(error) if isinstance(error, ApiError) else error
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            requester = await service.requester(instance)
            await service.record_knowledge(
                requester,
                spec,
                _display(spec.name, arguments),
                ok=False,
                summary=_summary(spec.name, arguments),
                error_code=failure.code,
                request_id=request_id,
            )
        raise failure from None
    instance = await service.db.get(AgentInstance, instance_id)
    if instance is not None:
        requester = await service.requester(instance)
        await service.record_knowledge(
            requester,
            spec,
            _display(spec.name, arguments),
            ok=True,
            summary=_summary(spec.name, arguments),
            request_id=request_id,
        )
    return output
