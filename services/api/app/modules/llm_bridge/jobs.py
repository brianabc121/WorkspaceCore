"""大模型客户端的定时任务：同步会话索引（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 3.4）。

每分钟向每个启用的 MCP 管理器取“上次同步之后变了的会话”（不含对话内容），更新
llm_bridge_sessions。管理器连不上、版本太旧（没有这个接口）就跳过，下一分钟再试。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import notifications
from app.domain.connectors import ConnectorRuntime
from app.domain.sync import SyncContext
from app.integrations.base import ConnectorError
from app.modules import service as modules
from app.modules.llm_bridge.client import BridgeCallError
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeSession
from app.modules.llm_bridge.service import LlmBridgeService

logger = logging.getLogger(__name__)

PAGE = 200
MAX_PAGES = 10  # 一次最多追 2000 个会话的变化，剩下的下一分钟接着追
USER_PREFIX = "user:"


def _time(value: Any) -> datetime | None:
    return datetime.fromtimestamp(float(value), UTC) if value else None


def owner_kind(owner_id: str | None) -> str:
    if not owner_id:
        return "external"
    return "user" if owner_id.startswith(USER_PREFIX) else "agent"


def _row(bridge: LlmBridge, item: dict[str, Any]) -> dict[str, Any]:
    owner_id = item.get("owner_id")
    created = _time(item.get("created_at")) or datetime.now(UTC)
    return {
        "workspace_id": bridge.workspace_id,
        "bridge_id": bridge.id,
        "session_id": str(item["session_id"]),
        "platform": str(item.get("platform") or ""),
        "mode": str(item.get("mode") or ""),
        "model": item.get("model"),
        "title": str(item.get("title") or "")[:200],
        "status": str(item.get("status") or "active"),
        "origin": str(item.get("origin") or "bridge"),
        "owner_kind": owner_kind(owner_id),
        "owner_id": owner_id,
        "owner_label": str(item.get("owner") or "")[:80],
        "source": item.get("source") if isinstance(item.get("source"), dict) else None,
        "busy": bool(item.get("busy")),
        "turns_total": int(item.get("turns_total") or 0),
        "turns_failed": int(item.get("turns_failed") or 0),
        "turns_interrupted": int(item.get("turns_interrupted") or 0),
        "tokens": int(item.get("tokens") or 0),
        "created_at": created,
        "updated_at": _time(item.get("updated_at")) or created,
        "last_turn_at": _time(item.get("last_turn_at")),
        "synced_at": datetime.now(UTC),
    }


async def _notify_failures(db: AsyncSession, bridge: LlmBridge, rows: list[dict[str, Any]]) -> None:
    """智能体的会话里多了失败（或结果未知）的回合：通知管理员，链接直接到那个会话。"""
    agent_rows = [r for r in rows if r["owner_kind"] == "agent" and r["turns_failed"] > 0]
    if not agent_rows:
        return
    known = dict(
        (
            await db.execute(
                select(LlmBridgeSession.session_id, LlmBridgeSession.turns_failed).where(
                    LlmBridgeSession.bridge_id == bridge.id,
                    LlmBridgeSession.session_id.in_([r["session_id"] for r in agent_rows]),
                )
            )
        ).all()
    )
    for row in agent_rows:
        if row["turns_failed"] <= known.get(row["session_id"], 0):
            continue
        await notifications.notify_admins(
            db,
            bridge.workspace_id,
            type_="llm_bridge.turn_failed",
            severity="warning",
            title=f"智能体「{row['owner_label'] or '未知'}」的大模型回合失败了",
            body=f"{row['title'] or '（没有标题）'}（{row['platform']}，{bridge.name}）",
            link=f"/llm_bridge?m={bridge.id}&s={row['session_id']}",
            key=f"{row['session_id']}:{row['turns_failed']}",
        )


async def _notify_pauses(db: AsyncSession, bridge_id: uuid.UUID, pauses: Any) -> None:
    """平台因额度用完暂停了：通知管理员一次；恢复后清掉记号，下次暂停再通知。"""
    bridge = await db.get(LlmBridge, bridge_id)
    if bridge is None or not isinstance(pauses, list):
        return
    current: dict[str, Any] = {}
    for pause in pauses:
        if (
            isinstance(pause, dict)
            and isinstance(pause.get("platform"), str)
            and pause.get("until")
        ):
            current[pause["platform"]] = pause
    notified = dict(bridge.pause_notified or {})
    changed = False
    for platform, pause in current.items():
        until = round(float(pause["until"]))
        if notified.get(platform) == until:
            continue
        when = f"{datetime.fromtimestamp(until, UTC):%m-%d %H:%M} UTC"
        guess = "（平台没有给出重置时间，这是估计的）" if pause.get("estimated") else ""
        await notifications.notify_admins(
            db,
            bridge.workspace_id,
            type_="llm_bridge.quota_paused",
            severity="warning",
            title=f"{platform} 的额度用完了，「{bridge.name}」上这个平台已暂停",
            body=f"暂停到 {when}{guess}；到时间后自动恢复，被打断的会话会自动继续。",
            link="/llm_bridge?view=manage",
            key=f"{bridge.id}:{platform}:{until}",
        )
        notified[platform] = until
        changed = True
    for platform in [p for p in notified if p not in current]:
        del notified[platform]
        changed = True
    if changed:
        await db.execute(
            update(LlmBridge)
            .where(LlmBridge.id == bridge_id)
            .values(pause_notified=notified, updated_at=LlmBridge.updated_at)
        )
        await db.commit()


async def sync_bridge(
    db: AsyncSession, settings: Settings, runtime: ConnectorRuntime, bridge_id: uuid.UUID
) -> int:
    """把一个管理器上变了的会话同步进索引，返回同步了多少个。连不上时抛出。"""
    service = LlmBridgeService(db, settings, runtime)
    bridge = await db.get(LlmBridge, bridge_id)
    if bridge is None or not bridge.enabled:
        return 0
    client, rev, done = await service.client(bridge), bridge.index_rev, 0
    await db.commit()  # 等管理器回答时不占着事务
    backfill = rev == 0
    for page in range(MAX_PAGES):
        data = await client.admin("GET", f"/index?after={rev}&limit={PAGE}")
        if page == 0 and isinstance(data, dict):
            await _notify_pauses(db, bridge_id, data.get("pauses"))
        items = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            break
        bridge = await db.get(LlmBridge, bridge_id)
        if bridge is None:  # 同步到一半登记被删了
            return done
        rows = [_row(bridge, i) for i in items if isinstance(i, dict) and i.get("session_id")]
        if rows and not backfill:  # 第一次同步是补历史，不为旧的失败发通知
            await _notify_failures(db, bridge, rows)
        if rows:
            statement = insert(LlmBridgeSession).values(rows)
            keep = ("workspace_id", "bridge_id", "session_id", "created_at")
            statement = statement.on_conflict_do_update(
                index_elements=["bridge_id", "session_id"],
                set_={k: statement.excluded[k] for k in rows[0] if k not in keep},
            )
            await db.execute(statement)
        rev = int(data.get("last_rev") or rev)
        # 只动同步进度，不算“改了这个管理器的登记”
        await db.execute(
            update(LlmBridge)
            .where(LlmBridge.id == bridge_id)
            .values(index_rev=rev, updated_at=LlmBridge.updated_at)
        )
        await db.commit()
        done += len(rows)
        if len(items) < PAGE:
            break
    return done


async def index_tick(ctx: dict[str, Any]) -> int:
    context: SyncContext = ctx["sync"]
    total = 0
    async with context.sessions() as db:
        bridges = (
            await db.execute(
                select(LlmBridge.id, LlmBridge.workspace_id).where(LlmBridge.enabled.is_(True))
            )
        ).all()
        for bridge_id, workspace_id in bridges:
            if not await modules.is_enabled(db, workspace_id, "llm_bridge"):
                continue
            try:
                total += await sync_bridge(db, context.settings, context.runtime, bridge_id)
            except (BridgeCallError, ConnectorError) as error:
                await db.rollback()
                logger.info(
                    "llm_bridge index: bridge %s skipped: %s", bridge_id, type(error).__name__
                )
    return total
