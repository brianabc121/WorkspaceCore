"""回合结束后叫醒发起的智能体（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 4.1 的 A1）。

智能体发送时带 notify=true、回合又没在等待时间内做完，就记一条“盯着这个回合”。后台任务隔十几秒
向管理器问一次这些回合的状态（只问状态，不取内容）：结束了就给这个实例排一次唤醒。
唤醒的提示词里只有回合编号和状态，没有模型写的内容——内容要智能体自己用工具取。
至少叫一次：轮询保留作兜底，智能体收到后按编号取结果，重复收到也不会做两遍。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import agent_wakeups
from app.domain.connectors import ConnectorRuntime
from app.domain.sync import SyncContext
from app.integrations.base import ConnectorError
from app.models import AgentInstance
from app.models.im import AgentWakeup
from app.modules import service as modules
from app.modules.llm_bridge.client import BridgeCallError
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeTurnWatch
from app.modules.llm_bridge.service import LlmBridgeService

logger = logging.getLogger(__name__)

EXPIRE_AFTER = timedelta(days=7)  # 盯了这么久还没结束的不再盯
MAX_LISTED = 20  # 一次唤醒里最多列这么多个回合
_ITEM = re.compile(r"bt_[0-9a-f]{20}（[a-z_]+）")
_FINAL = ("completed", "failed", "unknown", "cancelled")


async def add(
    db: AsyncSession, instance: AgentInstance, bridge_id: uuid.UUID, session_id: str, turn_id: str
) -> None:
    """开始盯一个回合；同一个回合重复登记不算。调用方负责提交。"""
    await db.execute(
        insert(LlmBridgeTurnWatch)
        .values(
            workspace_id=instance.workspace_id,
            bridge_id=bridge_id,
            instance_id=instance.id,
            session_id=session_id,
            first_turn_id=turn_id,
            turn_id=turn_id,
        )
        .on_conflict_do_nothing(index_elements=["bridge_id", "first_turn_id"])
    )


def _prompt(items: list[str]) -> str:
    listed = "、".join(items[:MAX_LISTED])
    more = f"等 {len(items)} 个" if len(items) > MAX_LISTED else ""
    return (
        f"[大模型会话] 你让大模型做的事有结果了：{listed}{more}。"
        "用 llm_bridge_turn_get 按回合编号取结果；括号里是状态，"
        "interrupted 之外的状态都不会再变。"
    )


async def _wake(db: AsyncSession, watch: LlmBridgeTurnWatch, outcome: str) -> None:
    """给这个实例排一次唤醒；已经有一条还没执行的，就把这个回合加进去。"""
    instance = await db.get(AgentInstance, watch.instance_id)
    if instance is None or instance.status == "disabled":
        return
    item = f"{watch.first_turn_id}（{outcome}）"
    pending = await db.scalar(
        select(AgentWakeup)
        .where(
            AgentWakeup.instance_id == watch.instance_id,
            AgentWakeup.source == "llm_bridge",
            AgentWakeup.status == "pending",
        )
        .with_for_update()
    )
    if pending is not None:
        items = _ITEM.findall(pending.prompt)
        if item not in items:
            pending.prompt = _prompt([*items, item])[:2000]
        return
    wakeup_id = uuid.uuid4()
    db.add(
        AgentWakeup(
            id=wakeup_id,
            workspace_id=watch.workspace_id,
            instance_id=watch.instance_id,
            source="llm_bridge",
            target=None,
            session_key=agent_wakeups.session_key(None, "", wakeup_id),
            prompt=_prompt([item]),
            run_at=datetime.now(UTC),
        )
    )


def _decide(state: dict[str, Any]) -> tuple[str, str | None]:
    """这个回合的状态意味着什么：("wait", None) 接着盯；("follow", 回合) 改盯接着做的那一回合；
    ("done", 结果) 叫醒。"""
    status = str(state.get("status") or "missing")
    nxt = state.get("next_turn_id")
    if status in ("queued", "running"):
        return "wait", None
    if status == "interrupted":
        if isinstance(nxt, str) and nxt:
            return "follow", nxt
        # 额度用完、在等自动续跑：续跑的回合出现后会带上 next_turn_id
        if state.get("resume_state") in ("pending", "forced"):
            return "wait", None
        return "done", "interrupted"
    if status in _FINAL:
        return "done", status
    return "done", "missing"


async def check_bridge(
    db: AsyncSession, settings: Settings, runtime: ConnectorRuntime, bridge_id: uuid.UUID
) -> int:
    """看一个管理器上盯着的回合，结束了的叫醒发起的实例；返回叫了几个。连不上时抛出。"""
    service = LlmBridgeService(db, settings, runtime)
    bridge = await db.get(LlmBridge, bridge_id)
    if bridge is None or not bridge.enabled:
        return 0
    open_ids = list(
        await db.scalars(
            select(LlmBridgeTurnWatch.turn_id).where(
                LlmBridgeTurnWatch.bridge_id == bridge_id, LlmBridgeTurnWatch.done_at.is_(None)
            )
        )
    )
    if not open_ids:
        return 0
    client = await service.client(bridge)
    await db.commit()  # 等管理器回答时不占着事务
    data = await client.admin("POST", "/turns/status", {"ids": sorted(set(open_ids))})
    states = data.get("turns") if isinstance(data, dict) else None
    if not isinstance(states, dict):
        return 0
    now, woke = datetime.now(UTC), 0
    watches = (
        await db.scalars(
            select(LlmBridgeTurnWatch)
            .where(LlmBridgeTurnWatch.bridge_id == bridge_id, LlmBridgeTurnWatch.done_at.is_(None))
            .with_for_update()
        )
    ).all()
    for watch in watches:
        state = states.get(watch.turn_id)
        if not isinstance(state, dict):
            continue
        action, value = _decide(state)
        if action == "wait" and now - watch.created_at > EXPIRE_AFTER:
            action, value = "done", "expired"
        if action == "follow" and value:
            watch.turn_id = value
        elif action == "done" and value:
            watch.done_at, watch.outcome = now, value
            if value != "expired":
                await _wake(db, watch, value)
                woke += 1
    await db.commit()
    return woke


async def watch_tick(ctx: dict[str, Any]) -> int:
    context: SyncContext = ctx["sync"]
    total = 0
    async with context.sessions() as db:
        bridges = (
            await db.execute(
                select(LlmBridge.id, LlmBridge.workspace_id)
                .where(
                    LlmBridge.enabled.is_(True),
                    LlmBridge.id.in_(
                        select(LlmBridgeTurnWatch.bridge_id).where(
                            LlmBridgeTurnWatch.done_at.is_(None)
                        )
                    ),
                )
                .distinct()
            )
        ).all()
        for bridge_id, workspace_id in bridges:
            if not await modules.is_enabled(db, workspace_id, "llm_bridge"):
                continue
            try:
                total += await check_bridge(db, context.settings, context.runtime, bridge_id)
            except (BridgeCallError, ConnectorError) as error:
                await db.rollback()
                logger.info(
                    "llm_bridge watch: bridge %s skipped: %s", bridge_id, type(error).__name__
                )
    return total
