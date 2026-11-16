"""提问前用上知识库（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 6 节）。

- 相似提示（6.1）：输入框里的文字在知识库和以前的会话里找相近的，只在工作台里做，不发给任何平台。
- 带上笔记（6.2）：由人或智能体挑选，工作台不自动塞（`KNOWLEDGE_PLAN.md` 3.8 第 7 条不改）。
  每一篇都检查：在这个工作空间、没删、会话纪要以外（人挑的候选里）、所在知识库允许发给外部平台、
  智能体对它有读权限。合计有上限，超出的截断；发出前过密钥扫描；每次带了什么记审计。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import secret_scan
from app.domain.knowledge_base import DIGEST_TYPE
from app.domain.knowledge_views import KnowledgeService
from app.models import AgentInstance, KnowledgeNote, KnowledgeSpace
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeSession

# 人挑的：一条消息最多 3 篇；智能体：最多 5 篇。正文合计最多 6000 字（6.2：带多了反而差）
HUMAN_MAX_NOTES = 3
AGENT_MAX_NOTES = 5
MAX_CHARS = 6000
# 固定带着的参考资料（6.3）：每个回合都算钱，合计最多 4000 字，最多 5 篇
PINNED_MAX_NOTES = 5
PINNED_MAX_CHARS = 4000
MAX_TOKENS = 12
_SPLIT = re.compile(r"[\s,.;:!?()\[\]{}<>\"'`~@#$%^&*+=|\\/，。；：！？、（）【】《》“”‘’…—·]+")
_CJK = re.compile(r"[㐀-鿿豈-﫿]+")


def tokens(text: str) -> list[str]:
    """把一句话拆成用来找相近内容的词：英文按单词（3 个字母以上），中文按相邻的两个字。

    不用分词库和向量：只是“看着像”的提示，人自己判断。最多 12 个，不重复。"""
    out: list[str] = []
    for part in _SPLIT.split(text.lower()):
        if not part:
            continue
        for run in _CJK.findall(part):
            out += [run] if len(run) <= 2 else [run[i : i + 2] for i in range(len(run) - 1)]
        latin = _CJK.sub(" ", part).split()
        out += [w for w in latin if len(w) >= 3]
    return list(dict.fromkeys(out))[:MAX_TOKENS]


def _escape(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _score(columns: Sequence[Any], words: list[str], weights: Sequence[int]) -> Any:
    """命中了几个词（标题里的权重高一些）。"""
    parts = []
    for word in words:
        pattern = f"%{_escape(word)}%"
        for column, weight in zip(columns, weights, strict=True):
            parts.append(case((column.ilike(pattern, escape="\\"), weight), else_=0))
    return sum(parts[1:], parts[0]).cast(Integer) if parts else None


def _needed(words: list[str]) -> int:
    """至少要命中几个词才算相近：一两个词时全中，多了要中三成。"""
    return len(words) if len(words) <= 2 else max(2, round(len(words) * 0.3))


def note_label(note: KnowledgeNote) -> str:
    """附出去的每篇笔记前面那行的来源说明：谁写的、什么时候更新。"""
    if note.type == DIGEST_TYPE:
        who = "会话纪要，未确认"
    elif note.source_kind == "external":
        who = "外部资料"
    elif note.updated_by_user is not None:
        who = "人已确认"
    elif note.updated_by_instance is not None:
        who = "智能体写入"
    else:
        who = "工作台"
    return f"{who} · {note.updated_at:%Y-%m-%d} 更新"


def _rank(note: KnowledgeNote) -> int:
    """排序沿用知识库的：人已确认的在前，外部来源的在后，会话纪要最后。"""
    if note.type == DIGEST_TYPE:
        return 3
    if note.source_kind == "external":
        return 2
    return 0 if note.updated_by_user is not None else 1


@dataclass(frozen=True)
class NoteHit:
    note: KnowledgeNote
    space: KnowledgeSpace
    score: int


async def find_notes(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    text: str,
    *,
    limit: int,
    external_only: bool,
    space_ids: Sequence[uuid.UUID] | None = None,
) -> list[NoteHit]:
    """按输入的文字找相近的笔记（不含会话纪要）。external_only：只要允许发给外部平台的知识库里的。"""
    words = tokens(text)
    if not words:
        return []
    score = _score([KnowledgeNote.title, KnowledgeNote.body], words, (2, 1))
    stmt = (
        select(KnowledgeNote, KnowledgeSpace, score.label("score"))
        .join(KnowledgeSpace, KnowledgeSpace.id == KnowledgeNote.space_id)
        .where(
            KnowledgeNote.workspace_id == workspace_id,
            KnowledgeNote.deleted_at.is_(None),
            KnowledgeNote.type != DIGEST_TYPE,
            or_(
                *(
                    column.ilike(f"%{_escape(w)}%", escape="\\")
                    for w in words
                    for column in (KnowledgeNote.title, KnowledgeNote.body)
                )
            ),
        )
        .order_by(score.desc(), KnowledgeNote.updated_at.desc())
        .limit(limit * 4)
    )
    if external_only:
        stmt = stmt.where(KnowledgeSpace.external_llm_allowed.is_(True))
    if space_ids is not None:
        stmt = stmt.where(KnowledgeNote.space_id.in_(space_ids))
    needed = _needed(words)
    hits = [
        NoteHit(note, space, int(points or 0))
        for note, space, points in (await db.execute(stmt)).all()
        if int(points or 0) >= needed
    ]
    hits.sort(key=lambda h: (_rank(h.note), -h.score))
    return hits[:limit]


async def similar_sessions(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    text: str,
    *,
    limit: int,
    owner_id: str | None = None,
    exclude: str | None = None,
) -> list[tuple[LlmBridgeSession, str]]:
    """以前问过的相近问题：会话索引里标题相近的会话（标题就是第一条消息）。返回（会话，管理器名）。"""
    words = tokens(text)
    if not words:
        return []
    score = _score([LlmBridgeSession.title], words, (1,))
    stmt = (
        select(LlmBridgeSession, LlmBridge.name, score.label("score"))
        .join(LlmBridge, LlmBridge.id == LlmBridgeSession.bridge_id)
        .where(
            LlmBridgeSession.workspace_id == workspace_id,
            or_(*(LlmBridgeSession.title.ilike(f"%{_escape(w)}%", escape="\\") for w in words)),
        )
        .order_by(score.desc(), LlmBridgeSession.updated_at.desc())
        .limit(limit + 1)
    )
    if owner_id is not None:
        stmt = stmt.where(LlmBridgeSession.owner_id == owner_id)
    if exclude:
        stmt = stmt.where(LlmBridgeSession.session_id != exclude)
    needed = _needed(words)
    return [
        (row, name)
        for row, name, points in (await db.execute(stmt)).all()
        if int(points or 0) >= needed
    ][:limit]


class ReferenceProblem(Exception):
    """带上的笔记有问题：code 是工作台的错误码，message 给人或智能体看。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class References:
    """要带给会话桥的参考资料（llm_send 的 references）和审计用的清单。"""

    items: list[dict[str, Any]]
    note_ids: list[str]
    truncated: bool


async def build(
    db: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    note_ids: Sequence[uuid.UUID],
    *,
    instance: AgentInstance | None = None,
    limit: int | None = None,
    max_chars: int = MAX_CHARS,
) -> References | None:
    """按挑好的笔记编号取出内容，检查后排成参考资料。没挑就是 None。

    人（instance 为空）：管理员能读所有知识库。智能体：只能带它有读权限的知识库里的笔记。
    都要求笔记所在的知识库允许发给外部大模型平台。会话纪要只有智能体明确带编号时才能带（标“未确认”）。"""
    ids = list(dict.fromkeys(note_ids))
    if not ids:
        return None
    limit = limit or (HUMAN_MAX_NOTES if instance is None else AGENT_MAX_NOTES)
    if len(ids) > limit:
        raise ReferenceProblem("VALIDATION_FAILED", f"一条消息最多带 {limit} 篇笔记")
    rows = {
        note.id: (note, space)
        for note, space in (
            await db.execute(
                select(KnowledgeNote, KnowledgeSpace)
                .join(KnowledgeSpace, KnowledgeSpace.id == KnowledgeNote.space_id)
                .where(
                    KnowledgeNote.workspace_id == workspace_id,
                    KnowledgeNote.id.in_(ids),
                    KnowledgeNote.deleted_at.is_(None),
                )
            )
        ).all()
    }
    readable: set[uuid.UUID] | None = None
    if instance is not None:
        kb = KnowledgeService(db, settings)
        readable = {state.space.id for state, _ in await kb.readable_spaces(instance)}
    picked: list[tuple[KnowledgeNote, KnowledgeSpace]] = []
    for note_id in ids:
        found = rows.get(note_id)
        if found is None or (readable is not None and found[1].id not in readable):
            raise ReferenceProblem("NOT_FOUND", f"没有这篇笔记，或你没有读它的权限：{note_id}")
        note, space = found
        if instance is None and note.type == DIGEST_TYPE:
            raise ReferenceProblem(
                "VALIDATION_FAILED", f"《{note.title}》是未确认的会话纪要，不能作为参考资料带上"
            )
        if not space.external_llm_allowed:
            raise ReferenceProblem(
                "FORBIDDEN_CAPABILITY",
                f"知识库「{space.name}」设置成不允许发给外部大模型平台，《{note.title}》不能带上",
            )
        picked.append(found)
    budget, truncated, items = max_chars, False, []
    for note, _ in picked:
        body = note.body.strip()
        if len(body) > budget:
            body, truncated = body[: max(budget, 0)], True
        budget -= len(body)
        if secret_scan.scan(note.title, body):
            raise ReferenceProblem(
                "VALIDATION_FAILED",
                f"《{note.title}》里像有令牌、私钥一类的密钥，没有带上；去掉之后再带",
            )
        items.append(
            {
                "note_id": str(note.id),
                "title": note.title[:200],
                "label": note_label(note),
                "text": body,
            }
        )
    return References(items=items, note_ids=[str(n.id) for n, _ in picked], truncated=truncated)
