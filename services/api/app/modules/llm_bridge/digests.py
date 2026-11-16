"""会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）：把大模型会话的重点留进知识库。

- 不用模型：管理器按自己存的记录给出素材（每一问的原话、回答的开头一段、改了哪些文件、跑了哪些命令、
  没做完的回合），这里按固定的几栏排成 Markdown，不做判断，所以不会“编”。
- 什么时候写：会话有 3 个以上回合，并且出现下面任一情况——平台压缩了一次；会话归档；
  会话 24 小时没有新回合；人点了“带着摘要另开会话”（第 2 期）。不到 3 个回合的不写。
- 一个会话一篇，放在“会话纪要”文件夹，类型 digest（未确认）：检索排在最后，
  默认不进检索候选和图谱，智能体不能写这种类型。同一个会话多次触发时整篇按记录重新排一遍
  （固定的几栏），每次留一个版本。
- 放在哪个知识库：会话关联了项目的放进项目知识库；智能体发起的放进它自己的知识库；
  人发起的放进公共知识库。
- 保护（5.3）：每一回合先过密钥扫描，命中的回合只留一句说明；
  每篇最多 20 KB（超出的只留最近的回合）；每个知识库的纪要有总量上限，超了停写并通知管理员；
  人改过、转成正式笔记、删掉的纪要不再自动写；
  会话内容按保留期清掉时纪要一起放进回收站（管理器设置里可以关）；
  每次写入记审计 llm_bridge.digest_written。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import notifications, secret_scan
from app.domain.audit import AuditTrail
from app.domain.connectors import ConnectorRuntime
from app.domain.knowledge_base import (
    DIGEST_FOLDER,
    DIGEST_MAX_BYTES,
    DIGEST_TYPE,
    KnowledgeBase,
)
from app.integrations.base import ConnectorError
from app.models import KnowledgeNote, KnowledgeNoteRevision, KnowledgeSpace
from app.modules.llm_bridge.client import BridgeCallError
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeDigest, LlmBridgeSession
from app.modules.llm_bridge.service import LlmBridgeService

logger = logging.getLogger(__name__)

MIN_TURNS = 3  # 不到 3 个回合的会话不写
IDLE = timedelta(hours=24)  # 多久没有新回合算“告一段落”
MAX_PER_TICK = 10  # 每个管理器每分钟最多写几篇（每篇要向管理器取一次素材）
MAX_DIGESTS_PER_SPACE = 300  # 每个知识库的纪要上限，超了停写并通知管理员
STOPPED = ("off", "converted", "edited", "deleted", "purged")
REASONS = {
    "compaction": "平台压缩了上下文",
    "archived": "会话归档",
    "idle": "24 小时没有新回合",
    "continued": "带着摘要另开会话",
    "manual": "管理员要求现在整理",
}
_BLOCKED_LINE = "（这一回合的内容因为含有密钥没有记录）"


def enabled(bridge: LlmBridge) -> bool:
    """管理器设置里纪要开没开（同步会话索引时带回来的；没同步过按默认开）。"""
    return bool((bridge.digest_settings or {}).get("enabled", True))


def follows_retention(bridge: LlmBridge) -> bool:
    return bool((bridge.digest_settings or {}).get("follow_retention", True))


def session_link(bridge_id: uuid.UUID, session_id: str, turn_id: str | None = None) -> str:
    turn = f"&t={turn_id}" if turn_id else ""
    return f"/llm_bridge?m={bridge_id}&s={session_id}{turn}"


def _when(stamp: Any) -> str:
    if not stamp:
        return ""
    return f"{datetime.fromtimestamp(float(stamp), UTC):%Y-%m-%d %H:%M} UTC"


def _code_list(items: list[str], limit: int) -> str:
    shown = [f"`{i.replace('`', "'")}`" for i in items[:limit]]
    more = f" 等 {len(items)} 项" if len(items) > limit else ""
    return "、".join(shown) + more


STATUS_TEXT = {
    "completed": "完成",
    "failed": "失败",
    "unknown": "结果未知",
    "interrupted": "被打断",
}


def _turn_block(index: int, turn: dict[str, Any], bridge_id: uuid.UUID, session_id: str) -> str:
    """回合清单里的一项。每一回合先过密钥扫描：命中就只留一句说明，不写内容。"""
    status = STATUS_TEXT.get(str(turn.get("status")), str(turn.get("status") or ""))
    head = (
        f"### 第 {index} 回合（{_when(turn.get('at'))}，{status}）"
        f" [打开]({session_link(bridge_id, session_id, turn.get('turn_id'))})"
    )
    question = str(turn.get("question") or "")
    answer = str(turn.get("answer_head") or "")
    failure = str(turn.get("failure") or "")
    files = [str(f) for f in turn.get("files") or []]
    commands = [str(c) for c in turn.get("commands") or []]
    if secret_scan.scan(question, answer, failure, *files, *commands):
        return f"{head}\n\n{_BLOCKED_LINE}"
    lines = [head, ""]
    if turn.get("compacted"):
        lines += ["*平台在这一回合压缩了上下文：之前的内容在模型那里只剩摘要。*", ""]
    by = f"（{turn['by']}）" if turn.get("by") else ""
    lines += [f"**问**{by}：", "", _quote(question), ""]
    if answer:
        lines += ["**答（开头一段）**：", "", _quote(answer), ""]
    if failure:
        lines += [f"**没做完的原因**：{failure}", ""]
    if files:
        lines.append(f"- 改动的文件：{_code_list(files, 10)}")
    if commands:
        lines.append(f"- 跑过的命令：{_code_list(commands, 5)}")
    return "\n".join(lines).rstrip()


def _quote(text: str) -> str:
    """原话按引用块放，不当成笔记自己的标题、列表来排版。"""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines()) or "> "


def render(
    material: dict[str, Any], bridge: LlmBridge, session_id: str, reason: str
) -> tuple[str, int]:
    """素材 → 纪要正文（固定的几栏）。返回（正文，写进去的回合数）。超过 20 KB 时只留最近的回合。"""
    session = material.get("session") or {}
    turns = [t for t in material.get("turns") or [] if isinstance(t, dict)]
    total = int(material.get("turns_total") or len(turns))
    files: list[str] = []
    commands: list[str] = []
    failed: list[str] = []
    clean: list[dict[str, Any]] = []
    for index, turn in enumerate(turns, start=total - len(turns) + 1):
        texts = [str(turn.get(k) or "") for k in ("question", "answer_head", "failure")]
        secret = secret_scan.scan(*texts, *map(str, turn.get("files") or []))
        if not secret:
            files += [str(f) for f in turn.get("files") or []]
            commands += [str(c) for c in turn.get("commands") or []]
            if turn.get("failure"):
                failed.append(f"- 第 {index} 回合{STATUS_TEXT.get(str(turn.get('status')), '')}")
        clean.append(turn)
    title = str(session.get("title") or "（没有标题）")
    owner = str(session.get("owner") or "—")
    context = session.get("context") or {}
    header = [
        "> **会话纪要（未确认）**：工作台按大模型会话的记录整理，没有经过模型，也没有经人确认；"
        "检索时排在最后，默认不会带进提问。值得长期留下的，请转成正式笔记。",
        ">",
        f"> 会话：[{title}]({session_link(bridge.id, session_id)}) · "
        f"{session.get('platform', '')} · {session.get('model') or '平台默认模型'} · "
        f"{owner}建的 · MCP 管理器「{bridge.name}」",
        f"> 一共 {total} 个回合；压缩过 {int(context.get('compactions') or 0)} 次；"
        f"这次整理：{REASONS.get(reason, reason)}（{_when(material.get('generated_at'))}）",
    ]
    sections = [
        "\n".join(header),
        "## 结论和决定\n\n（平台压缩会话时写出的摘要会并进这里；目前还没有。"
        "下面的回合清单是原始记录。）",
        "## 没解决的问题\n\n"
        + ("\n".join(failed) if failed else "（记录里没有失败或中断的回合。）"),
        "## 涉及的文件和命令\n\n"
        + (
            "\n".join(
                part
                for part in (
                    files and f"- 文件：{_code_list(list(dict.fromkeys(files)), 30)}",
                    commands and f"- 命令：{_code_list(list(dict.fromkeys(commands)), 15)}",
                )
                if part
            )
            or "（没有改动文件或运行命令。）"
        ),
        "## 后续要做的\n\n（由人或智能体在转成正式笔记时补上。）",
    ]
    blocks = [
        _turn_block(index, turn, bridge.id, session_id)
        for index, turn in enumerate(clean, start=total - len(clean) + 1)
    ]
    while True:
        dropped = total - len(blocks)
        note = f"\n\n（更早的 {dropped} 个回合没有列出，打开会话查看全部。）" if dropped else ""
        body = "\n\n".join([*sections, "## 回合清单" + note, *blocks]).strip() + "\n"
        if len(body.encode()) <= DIGEST_MAX_BYTES or not blocks:
            return body, total
        blocks.pop(0)


@dataclass
class Candidate:
    row: LlmBridgeSession
    digest: LlmBridgeDigest | None
    reason: str


def _reason(row: LlmBridgeSession, digest: LlmBridgeDigest | None, now: datetime) -> str | None:
    """这个会话现在该不该写（或更新）纪要；该写就给出原因。"""
    if row.origin != "bridge" or row.turns_total < MIN_TURNS:
        return None
    if digest is not None and digest.state in STOPPED:
        return None
    written = digest.turns_written if digest else 0
    seen = digest.compactions_seen if digest else 0
    if row.compactions > seen:
        return "compaction"
    if row.turns_total <= written:
        return None
    if row.status == "archived":
        return "archived"
    if row.last_turn_at is not None and now - row.last_turn_at >= IDLE and not row.busy:
        return "idle"
    return None


async def _candidates(db: AsyncSession, bridge: LlmBridge) -> list[Candidate]:
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(LlmBridgeSession, LlmBridgeDigest)
            .outerjoin(
                LlmBridgeDigest,
                and_(
                    LlmBridgeDigest.bridge_id == LlmBridgeSession.bridge_id,
                    LlmBridgeDigest.session_id == LlmBridgeSession.session_id,
                ),
            )
            .where(
                LlmBridgeSession.bridge_id == bridge.id,
                LlmBridgeSession.origin == "bridge",
                LlmBridgeSession.turns_total >= MIN_TURNS,
                or_(
                    LlmBridgeDigest.id.is_(None),
                    and_(
                        LlmBridgeDigest.state == "active",
                        or_(
                            LlmBridgeSession.turns_total > LlmBridgeDigest.turns_written,
                            LlmBridgeSession.compactions > LlmBridgeDigest.compactions_seen,
                        ),
                    ),
                ),
            )
            .order_by(LlmBridgeSession.updated_at)
            .limit(500)
        )
    ).all()
    out = []
    for row, digest in rows:
        reason = _reason(row, digest, now)
        if reason is not None:
            out.append(Candidate(row, digest, reason))
    return out


async def target_space(
    db: AsyncSession, settings: Settings, row: LlmBridgeSession
) -> KnowledgeSpace | None:
    """纪要放在哪个知识库：关联了项目的放项目库，智能体的放它自己的库，人的放公共库。"""
    await KnowledgeBase(db, settings).ensure_spaces(row.workspace_id)
    source = row.source or {}
    for task in source.get("tasks") or []:
        project = task.get("project_id") if isinstance(task, dict) else None
        try:
            project_id = uuid.UUID(str(project)) if project else None
        except ValueError:
            project_id = None
        if project_id is None:
            continue
        space = await db.scalar(
            select(KnowledgeSpace).where(
                KnowledgeSpace.workspace_id == row.workspace_id,
                KnowledgeSpace.project_id == project_id,
            )
        )
        if space is not None:
            return space
    if row.owner_kind == "agent" and row.owner_id:
        try:
            instance_id = uuid.UUID(row.owner_id)
        except ValueError:
            instance_id = None
        if instance_id is not None:
            space = await db.scalar(
                select(KnowledgeSpace).where(
                    KnowledgeSpace.workspace_id == row.workspace_id,
                    KnowledgeSpace.instance_id == instance_id,
                )
            )
            if space is not None:
                return space
    return await db.scalar(
        select(KnowledgeSpace).where(
            KnowledgeSpace.workspace_id == row.workspace_id, KnowledgeSpace.kind == "public"
        )
    )


async def _refresh_state(db: AsyncSession, digest: LlmBridgeDigest) -> KnowledgeNote | None:
    """纪要笔记现在怎样了：被删、被转成正式笔记、被人改过的，就不再自动写。返回还能写的笔记。"""
    if digest.note_id is None:
        if digest.written_at is not None:  # 写过、笔记却没了（彻底删除）
            digest.state = "deleted"
        return None
    note = await db.get(KnowledgeNote, digest.note_id)
    if note is None or note.deleted_at is not None:
        digest.state = "deleted"
        return None
    if note.type != DIGEST_TYPE:
        digest.state = "converted"
        return None
    edited = await db.scalar(
        select(func.count()).where(
            KnowledgeNoteRevision.note_id == note.id,
            KnowledgeNoteRevision.author_user.is_not(None),
        )
    )
    if edited:
        digest.state = "edited"
        return None
    return note


def _title(material: dict[str, Any], session_id: str) -> str:
    session = material.get("session") or {}
    title = " ".join(str(session.get("title") or "没有标题").split())[:80]
    return f"会话纪要：{title}（{session_id[-6:]}）"


async def write(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime,
    bridge: LlmBridge,
    row: LlmBridgeSession,
    reason: str,
) -> LlmBridgeDigest | None:
    """取素材、排好、写进知识库（新建或合并进原来那篇）。不该写或写不了时返回 None。

    连不上管理器时抛出（调用方跳过，下一分钟再试）。"""
    digest = await db.scalar(
        select(LlmBridgeDigest).where(
            LlmBridgeDigest.bridge_id == bridge.id, LlmBridgeDigest.session_id == row.session_id
        )
    )
    note: KnowledgeNote | None = None
    if digest is not None:
        if digest.state in STOPPED:
            return None
        note = await _refresh_state(db, digest)
        if digest.state != "active":
            await db.commit()
            return None
    service = LlmBridgeService(db, settings, runtime)
    client = await service.client(bridge)
    bridge_id, workspace_id, session_id = bridge.id, bridge.workspace_id, row.session_id
    await db.commit()  # 等管理器回答时不占着事务
    material = await client.admin("GET", f"/sessions/{session_id}/digest")
    if not isinstance(material, dict):
        return None
    bridge = await db.get(LlmBridge, bridge_id) or bridge
    row = (
        await db.scalar(
            select(LlmBridgeSession).where(
                LlmBridgeSession.bridge_id == bridge_id, LlmBridgeSession.session_id == session_id
            )
        )
        or row
    )
    if digest is not None:
        digest = await db.get(LlmBridgeDigest, digest.id)
        note = await db.get(KnowledgeNote, digest.note_id) if digest and digest.note_id else None
    space = (
        await db.get(KnowledgeSpace, note.space_id)
        if note
        else await target_space(db, settings, row)
    )
    if space is None:
        return None
    if note is None:
        count = await db.scalar(
            select(func.count()).where(
                KnowledgeNote.space_id == space.id,
                KnowledgeNote.type == DIGEST_TYPE,
                KnowledgeNote.deleted_at.is_(None),
            )
        )
        if (count or 0) >= MAX_DIGESTS_PER_SPACE:
            await notifications.notify_admins(
                db,
                workspace_id,
                type_="llm_bridge.digests_full",
                severity="warning",
                title=f"知识库「{space.name}」的会话纪要满了，已停止写入新的纪要",
                body=f"每个知识库最多 {MAX_DIGESTS_PER_SPACE} 篇会话纪要。"
                "删掉不需要的（进回收站），或把有用的转成正式笔记后会接着写。",
                link="/llm_bridge?view=digests",
                key=str(space.id),
            )
            await db.commit()
            return None
    body, turns = render(material, bridge, session_id, reason)
    kb = KnowledgeBase(db, settings)
    titles = [_title(material, session_id), f"会话纪要（{session_id}）"]
    for attempt, title in enumerate(titles):
        try:
            note = await kb.system_write(
                space,
                note,
                title=title,
                body=body,
                note_type=DIGEST_TYPE,
                folder=DIGEST_FOLDER,
                reason=f"会话纪要：{REASONS.get(reason, reason)}",
                audit={"by": "会话纪要", "bridge_id": str(bridge_id), "session_id": session_id},
            )
            break
        except ApiError as error:
            # 同名的笔记已经有了（人建的）：换成带完整会话号的标题再试一次
            if error.code == "TITLE_TAKEN" and attempt == 0:
                continue
            await db.rollback()
            logger.info("digest for %s not written: %s", session_id, error.code)
            return None
    if note is None:
        return None
    if digest is None:
        digest = LlmBridgeDigest(
            workspace_id=workspace_id, bridge_id=bridge_id, session_id=session_id
        )
        db.add(digest)
    digest.note_id, digest.space_id, digest.state = note.id, space.id, "active"
    digest.turns_written = max(turns, row.turns_total)
    digest.compactions_seen = row.compactions
    digest.reason, digest.written_at = reason, datetime.now(UTC)
    await AuditTrail(db, settings).record(
        "llm_bridge.digest_written",
        workspace_ids=[workspace_id],
        actor_id=None,
        resource_type="llm_bridge",
        resource_id=bridge_id,
        metadata={
            "bridge": bridge.name,
            "session_id": session_id,
            "note_id": str(note.id),
            "space_id": str(space.id),
            "turns": turns,
            "reason": reason,
        },
    )
    await db.commit()
    return digest


async def purge_follow(db: AsyncSession, settings: Settings, bridge: LlmBridge) -> int:
    """会话内容按保留期清掉了：纪要跟着放进回收站（管理器设置里可以关，5.3）。返回处理了几篇。"""
    if not follows_retention(bridge):
        return 0
    rows = (
        await db.execute(
            select(LlmBridgeDigest, LlmBridgeSession.purged_at)
            .join(
                LlmBridgeSession,
                and_(
                    LlmBridgeSession.bridge_id == LlmBridgeDigest.bridge_id,
                    LlmBridgeSession.session_id == LlmBridgeDigest.session_id,
                ),
            )
            .where(
                LlmBridgeDigest.bridge_id == bridge.id,
                LlmBridgeDigest.state.in_(("active", "off", "edited")),
                LlmBridgeSession.purged_at.is_not(None),
            )
        )
    ).all()
    kb = KnowledgeBase(db, settings)
    done = 0
    for digest, _ in rows:
        note = await db.get(KnowledgeNote, digest.note_id) if digest.note_id else None
        if note is not None and note.deleted_at is None and note.type == DIGEST_TYPE:
            await kb.trash_system(note, reason="会话内容按保留期清除，纪要随之放进回收站")
        digest.state = "purged"
        done += 1
    if done:
        await db.commit()
    return done


async def tick(
    db: AsyncSession, settings: Settings, runtime: ConnectorRuntime, bridge_id: uuid.UUID
) -> int:
    """同步会话索引之后调用：该写的纪要写一批（每个管理器每分钟最多 MAX_PER_TICK 篇）。"""
    bridge = await db.get(LlmBridge, bridge_id)
    if bridge is None or not bridge.enabled:
        return 0
    await purge_follow(db, settings, bridge)
    if not enabled(bridge):
        return 0
    written = 0
    for candidate in (await _candidates(db, bridge))[:MAX_PER_TICK]:
        bridge = await db.get(LlmBridge, bridge_id)
        if bridge is None:
            break
        try:
            if await write(db, settings, runtime, bridge, candidate.row, candidate.reason):
                written += 1
        except (BridgeCallError, ConnectorError) as error:
            await db.rollback()
            logger.info("digest: bridge %s skipped: %s", bridge_id, type(error).__name__)
            break
    return written
