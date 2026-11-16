"""会话纪要与“提问前用上知识库”的接口（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 5、6 节）。

都登记在模块的 router 上（deps.py）；owner / admin 可用，写操作带 CSRF。
- 相近内容：输入框里的文字在知识库和以前的会话里找相近的（只在工作台里找，不发给任何平台）。
- 会话纪要：列表、关掉 / 打开某个会话的纪要、现在整理一次、转成正式笔记、删除（进回收站）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Query, status
from sqlalchemy import and_, select

from app.api.deps import csrf_protected
from app.core.errors import ApiError
from app.domain.knowledge_base import (
    DIGEST_TYPE,
    Editor,
    KnowledgeBase,
    NoteFields,
    SaveOptions,
)
from app.domain.knowledge_views import excerpt
from app.models import KnowledgeNote, KnowledgeSpace, User
from app.modules.llm_bridge import digests, jobs, memory
from app.modules.llm_bridge.chat_schemas import (
    LlmBridgeDigestItem,
    LlmBridgeDigestList,
    LlmBridgeDigestPromoteIn,
    LlmBridgeDigestToggleIn,
    LlmChatDigestOut,
    LlmChatNoteHit,
    LlmChatRelatedOut,
    LlmChatSimilarSession,
)
from app.modules.llm_bridge.deps import Admin, router
from app.modules.llm_bridge.deps import Service as Bridges
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeDigest, LlmBridgeSession
from app.schemas.common import error_responses

SessionId = Annotated[str, Path(pattern=r"^bs_[0-9a-f]{20}$")]
write = [Depends(csrf_protected)]
read_errors = error_responses(401, 403, 404, 503)
write_errors = error_responses(401, 403, 404, 409, 422, 503)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/chat/related",
    summary="聊天：知识库和以前的会话里相近的内容（admin）",
    description="输入框里停顿一下时用：按输入的文字在知识库（不含会话纪要）和所有 MCP 管理器的会话"
    "标题里找相近的。只在工作台里找，不发给任何平台。notes 也是“参考知识库”的候选："
    "external_allowed=false 的不能带上。session_id：不列出当前这个会话。",
    responses=read_errors,
)
async def llm_chat_related(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    q: Annotated[str, Query(min_length=1, max_length=2000)],
    session_id: Annotated[str | None, Query(pattern=r"^bs_[0-9a-f]{20}$")] = None,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> LlmChatRelatedOut:
    await bridges.bridge(access.workspace.id, bridge_id)
    db = bridges.db
    notes = await memory.find_notes(db, access.workspace.id, q, limit=limit, external_only=False)
    sessions = await memory.similar_sessions(
        db, access.workspace.id, q, limit=3, exclude=session_id
    )
    return LlmChatRelatedOut(
        notes=[
            LlmChatNoteHit(
                id=hit.note.id,
                title=hit.note.title,
                space_id=hit.space.id,
                space_name=hit.space.name,
                label=memory.note_label(hit.note),
                excerpt=excerpt(hit.note.body, None, 120),
                external_allowed=hit.space.external_llm_allowed,
            )
            for hit in notes
        ],
        sessions=[
            LlmChatSimilarSession(
                bridge_id=row.bridge_id,
                bridge_name=name,
                session_id=row.session_id,
                title=row.title,
                platform=row.platform,
                owner=row.owner_label,
                last_turn_at=row.last_turn_at,
            )
            for row, name in sessions
        ],
    )


# ---------------------------------------------------------------- 会话纪要


async def digest_out(db: Bridges, digest: LlmBridgeDigest | None) -> LlmChatDigestOut | None:
    return (await _digest_outs(db, [digest]))[0] if digest is not None else None


async def _digest_outs(
    bridges: Bridges, rows: list[LlmBridgeDigest | None]
) -> list[LlmChatDigestOut | None]:
    db = bridges.db
    note_ids = [r.note_id for r in rows if r is not None and r.note_id]
    space_ids = [r.space_id for r in rows if r is not None and r.space_id]
    titles = (
        dict(
            (
                await db.execute(
                    select(KnowledgeNote.id, KnowledgeNote.title).where(
                        KnowledgeNote.id.in_(note_ids)
                    )
                )
            ).all()
        )
        if note_ids
        else {}
    )
    spaces = (
        dict(
            (
                await db.execute(
                    select(KnowledgeSpace.id, KnowledgeSpace.name).where(
                        KnowledgeSpace.id.in_(space_ids)
                    )
                )
            ).all()
        )
        if space_ids
        else {}
    )
    out: list[LlmChatDigestOut | None] = []
    for row in rows:
        if row is None:
            out.append(None)
            continue
        out.append(
            LlmChatDigestOut(
                note_id=row.note_id,
                note_title=titles.get(row.note_id) if row.note_id else None,
                space_id=row.space_id,
                space_name=spaces.get(row.space_id) if row.space_id else None,
                state=row.state,
                turns_written=row.turns_written,
                written_at=row.written_at,
                reason=row.reason,
            )
        )
    return out


async def find_digest(
    bridges: Bridges, bridge_id: uuid.UUID, session_id: str
) -> LlmBridgeDigest | None:
    return await bridges.db.scalar(
        select(LlmBridgeDigest).where(
            LlmBridgeDigest.bridge_id == bridge_id, LlmBridgeDigest.session_id == session_id
        )
    )


@router.get(
    "/llm_bridge/digests",
    summary="会话纪要列表（admin）",
    description="每个大模型会话一篇，工作台按会话记录整理（不调模型），写在知识库的“会话纪要”文件夹，"
    "标为未确认。state 筛选：active / off / converted / edited / deleted / purged。",
    responses=read_errors,
)
async def llm_bridge_digests(
    access: Admin,
    bridges: Bridges,
    state: str | None = None,
    bridge_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LlmBridgeDigestList:
    db = bridges.db
    stmt = (
        select(LlmBridgeDigest, LlmBridge.name, LlmBridgeSession)
        .join(LlmBridge, LlmBridge.id == LlmBridgeDigest.bridge_id)
        .outerjoin(
            LlmBridgeSession,
            and_(
                LlmBridgeSession.bridge_id == LlmBridgeDigest.bridge_id,
                LlmBridgeSession.session_id == LlmBridgeDigest.session_id,
            ),
        )
        .where(LlmBridgeDigest.workspace_id == access.workspace.id)
        .order_by(LlmBridgeDigest.updated_at.desc(), LlmBridgeDigest.id)
        .offset(offset)
        .limit(limit + 1)
    )
    if state:
        stmt = stmt.where(LlmBridgeDigest.state == state)
    if bridge_id:
        stmt = stmt.where(LlmBridgeDigest.bridge_id == bridge_id)
    rows = (await db.execute(stmt)).all()
    outs = await _digest_outs(bridges, [r[0] for r in rows[:limit]])
    items = []
    for (digest, name, session), out in zip(rows[:limit], outs, strict=True):
        assert out is not None  # noqa: S101
        items.append(
            LlmBridgeDigestItem(
                **out.model_dump(),
                id=digest.id,
                bridge_id=digest.bridge_id,
                bridge_name=name,
                session_id=digest.session_id,
                session_title=session.title if session else "",
                owner=session.owner_label if session else "",
                owner_kind=session.owner_kind if session else "external",
            )
        )
    return LlmBridgeDigestList(
        digests=items, next_offset=offset + limit if len(rows) > limit else None
    )


async def _bridge_and_session(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: str
) -> tuple[LlmBridge, LlmBridgeSession]:
    bridge = await bridges.bridge(access.workspace.id, bridge_id)
    db = bridges.db
    row = await db.scalar(
        select(LlmBridgeSession).where(
            LlmBridgeSession.bridge_id == bridge.id, LlmBridgeSession.session_id == session_id
        )
    )
    if row is None:  # 索引里还没有（刚建的会话）：先同步一次
        try:
            await jobs.sync_bridge(db, bridges.settings, bridges.runtime, bridge.id)
        except Exception as error:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "MCP 管理器暂时连不上") from error
        bridge = await bridges.bridge(access.workspace.id, bridge_id)
        row = await db.scalar(
            select(LlmBridgeSession).where(
                LlmBridgeSession.bridge_id == bridge.id, LlmBridgeSession.session_id == session_id
            )
        )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "没有这个会话")
    return bridge, row


@router.put(
    "/llm_bridge/digests/{bridge_id}/{session_id}",
    summary="关掉或重新打开一个会话的纪要（admin）",
    description="enabled=false：之后不再为这个会话写纪要，已有的那篇不动（要删就用 DELETE）；"
    "enabled=true：重新打开（转成正式笔记、删掉的也会重新开始，下次触发时新写一篇）。",
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_digest_toggle(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    body: LlmBridgeDigestToggleIn,
) -> LlmChatDigestOut:
    bridge, _ = await _bridge_and_session(access, bridges, bridge_id, session_id)
    db = bridges.db
    digest = await find_digest(bridges, bridge.id, session_id)
    if digest is None:
        digest = LlmBridgeDigest(
            workspace_id=access.workspace.id, bridge_id=bridge.id, session_id=session_id
        )
        db.add(digest)
    if body.enabled:
        if digest.state != "active":
            digest.state = "active"
            if digest.note_id is None or await _gone(bridges, digest.note_id):
                digest.note_id, digest.turns_written, digest.compactions_seen = None, 0, 0
    else:
        digest.state = "off"
    await bridges.audit(
        access,
        "digest_enabled" if body.enabled else "digest_disabled",
        bridge.id,
        {"bridge": bridge.name, "session": session_id},
    )
    await db.commit()
    out = await digest_out(bridges, await find_digest(bridges, bridge.id, session_id))
    assert out is not None  # noqa: S101
    return out


async def _gone(bridges: Bridges, note_id: uuid.UUID) -> bool:
    note = await bridges.db.get(KnowledgeNote, note_id)
    return note is None or note.deleted_at is not None or note.type != DIGEST_TYPE


@router.post(
    "/llm_bridge/digests/{bridge_id}/{session_id}/write",
    summary="现在整理一次这个会话的纪要（admin）",
    description="不等触发条件，按管理器现在的记录整理一次（不调模型、不花 token）。"
    "关掉了、已转成正式笔记、人改过的 409；管理器设置里关掉了纪要也 409。",
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_digest_write(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> LlmChatDigestOut:
    bridge, row = await _bridge_and_session(access, bridges, bridge_id, session_id)
    if not digests.enabled(bridge):
        raise ApiError(409, "DIGEST_DISABLED", "这个 MCP 管理器的设置里关掉了会话纪要")
    existing = await find_digest(bridges, bridge.id, session_id)
    if existing is not None and existing.state in digests.STOPPED:
        raise ApiError(409, "DIGEST_STOPPED", "这个会话的纪要已经关掉或不再自动更新")
    if row.origin != "bridge" or row.turns_total < 1:
        raise ApiError(409, "CONFLICT", "这个会话还没有经工作台发过消息，没有可整理的")
    try:
        written = await digests.write(
            bridges.db, bridges.settings, bridges.runtime, bridge, row, "manual"
        )
    except Exception as error:
        await bridges.db.rollback()
        raise ApiError(503, "EXTERNAL_UNAVAILABLE", "MCP 管理器暂时连不上，没有整理") from error
    if written is None:
        raise ApiError(409, "CONFLICT", "这次没有写成：知识库里的纪要满了，或者内容不能保存")
    out = await digest_out(bridges, await find_digest(bridges, bridge_id, session_id))
    assert out is not None  # noqa: S101
    return out


async def _editor(access: Admin, bridges: Bridges) -> Editor:
    user = await bridges.db.get(User, access.user_id) if access.user_id else None
    if user is None:
        raise ApiError(403, "FORBIDDEN_ROLE", "只有登录的管理员能做这件事")
    return Editor(
        workspace_id=access.workspace.id, user_id=user.id, user_name=user.display_name or ""
    )


async def _digest_note(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: str
) -> tuple[LlmBridge, LlmBridgeDigest, KnowledgeNote]:
    bridge = await bridges.bridge(access.workspace.id, bridge_id)
    digest = await find_digest(bridges, bridge.id, session_id)
    note = (
        await bridges.db.get(KnowledgeNote, digest.note_id) if digest and digest.note_id else None
    )
    if digest is None or note is None or note.deleted_at is not None:
        raise ApiError(404, "NOT_FOUND", "这个会话还没有纪要，或纪要已经删除")
    return bridge, digest, note


@router.post(
    "/llm_bridge/digests/{bridge_id}/{session_id}/promote",
    summary="把会话纪要转成正式笔记（admin）",
    description="由人确认：改成普通笔记（默认 record，放到根目录），作者记成这个人；"
    "之后不再自动更新。纪要已经不是纪要（转过了）时 409。",
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_digest_promote(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    body: LlmBridgeDigestPromoteIn,
) -> LlmChatDigestOut:
    bridge, digest, note = await _digest_note(access, bridges, bridge_id, session_id)
    if note.type != DIGEST_TYPE:
        raise ApiError(409, "CONFLICT", "这篇已经是正式笔记了")
    kb = KnowledgeBase(bridges.db, bridges.settings)
    fields = NoteFields(
        title=body.title or note.title.replace("会话纪要：", "", 1),
        type=body.type,
        folder=body.folder or "",
    )
    await kb.update(
        await _editor(access, bridges),
        note,
        note.version,
        fields,
        SaveOptions(reason="从会话纪要转成正式笔记（人确认）"),
    )
    digest.state = "converted"
    await bridges.audit(
        access,
        "digest_promoted",
        bridge.id,
        {"bridge": bridge.name, "session": session_id, "note_id": str(note.id)},
    )
    await bridges.db.commit()
    out = await digest_out(bridges, await find_digest(bridges, bridge.id, session_id))
    assert out is not None  # noqa: S101
    return out


@router.delete(
    "/llm_bridge/digests/{bridge_id}/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除会话纪要（admin）",
    description="纪要笔记放进知识库的回收站（30 天内可以恢复），之后不再为这个会话写纪要。",
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_digest_delete(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> None:
    bridge, digest, note = await _digest_note(access, bridges, bridge_id, session_id)
    kb = KnowledgeBase(bridges.db, bridges.settings)
    await kb.delete(
        await _editor(access, bridges), note, note.version, SaveOptions(reason="删除会话纪要")
    )
    digest.state = "deleted"
    digest.updated_at = datetime.now(UTC)
    await bridges.audit(
        access,
        "digest_deleted",
        bridge.id,
        {"bridge": bridge.name, "session": session_id, "note_id": str(note.id)},
    )
    await bridges.db.commit()
