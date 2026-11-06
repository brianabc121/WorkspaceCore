"""唤醒（T-0121，docs/v2/AGENT_MESSAGING_PLAN.md 第 7.2 节）。

智能体只在收到消息时运行。唤醒让工作台到点叫醒它跑一轮，让它能主动做事（例如到群里发进展）：

- 来源：智能体用 `schedule_wakeup` 给自己安排（直接执行，`docs/AGENTS.md` 第 8 节例外四；
  每个实例最多 20 个待执行，最早 5 分钟后、最远 30 天）；管理员在实例页建
  （最多 50 个待执行，可以每天、工作日、每周重复）。
- 会话：`target` 是 `im_list_targets` 的目标时，在那个会话里跑（看得到之前的消息），
  会话键与 openclaw-channel 0.3.3 的写法一致
  （`openim:group:<群 ID>`、`openim:<发送者 IM ID>`，小写）；不给就是独立会话。
- 执行：接入包每分钟运行 `wakeup.mjs`，经 `/agent-api/v1/wakeups/claim` 领取到期的
  （领取即标为 running），在容器里 `openclaw agent --session-key <键> --message-file <消息>`，
  再报回结果。重复的唤醒报回后排下一次。领取后 15 分钟没有结果记为失败
  （结果未知，不重跑，免得做两遍）。结束的记录保留 30 天。
- 消息：唤醒的提示词放在最后，前面一行说明是谁、什么时候安排的，免得智能体把它当成别人发来的消息。
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.domain.agent_im import resolve_target
from app.domain.ai_tools import ToolCallError
from app.domain.ratelimit import WindowLimiter
from app.models import AgentInstance, AgentWakeup
from app.schemas.ai_tools import (
    CancelWakeupInput,
    CancelWakeupOutput,
    ImOutboxText,
    ScheduleWakeupInput,
    ScheduleWakeupOutput,
    WakeupItem,
)

AGENT_MAX_PENDING = 20
ADMIN_MAX_PENDING = 50
AGENT_MIN_DELAY = timedelta(minutes=5)
AGENT_MAX_DELAY = timedelta(days=30)
ADMIN_MAX_DELAY = timedelta(days=366)
CLAIM_LIMIT = 5
CLAIMS_PER_MINUTE = 6
STALE_AFTER = timedelta(minutes=15)
KEEP = timedelta(days=30)
ERROR_CHARS = 300
OPEN = ("pending", "running")


def _now() -> datetime:
    return datetime.now(UTC)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(422, "VALIDATION_FAILED", "不认识的时区") from None


def next_run(run_at: datetime, repeat: str, time_zone: str, after: datetime) -> datetime | None:
    """重复的唤醒下一次在什么时候：按时区里的同一个钟点（跨夏令时也不变），排到 after 之后。"""
    if repeat == "none":
        return None
    tz = zone(time_zone)
    local = run_at.astimezone(tz)
    while True:
        day = local.date() + timedelta(days=7 if repeat == "weekly" else 1)
        while repeat == "weekdays" and day.weekday() >= 5:
            day += timedelta(days=1)
        local = datetime.combine(day, local.timetz().replace(tzinfo=None), tzinfo=tz)
        if local.astimezone(UTC) > after:
            return local.astimezone(UTC)


def session_key(target_kind: str | None, ident: str, wakeup_id: uuid.UUID) -> str:
    """与 openclaw-channel 0.3.3 dist/inbound.js 的 baseSessionKey 一致：群 `openim:group:<群 ID>`，
    私聊 `openim:<对方的 IM ID>`，都转小写；独立会话用唤醒自己的键（核验记录 V4）。"""
    if target_kind == "group":
        return f"openim:group:{ident}".lower()
    if target_kind == "user":
        return f"openim:{ident}".lower()
    return f"myworkspace-wakeup-{wakeup_id.hex}"


def message_for(wakeup: AgentWakeup, by: str) -> str:
    """交给智能体的这一轮消息：说明在前，提示词在最后。"""
    when = wakeup.run_at.astimezone(zone(wakeup.time_zone)).strftime("%Y-%m-%d %H:%M")
    where = f"在 {wakeup.target} 的会话里" if wakeup.target else "在一个独立会话里"
    return (
        f"[工作台唤醒] 这是{by}安排在 {when}（{wakeup.time_zone}）的唤醒，{where}运行，"
        "不是别人刚发来的消息。要发消息用 im_send（目标用 im_list_targets 查）；"
        "没什么要做的就直接结束。\n\n"
        f"{wakeup.prompt}"
    )


def item(wakeup: AgentWakeup) -> WakeupItem:
    return WakeupItem(
        wakeup_id=wakeup.id,
        source=wakeup.source,
        target=wakeup.target,
        run_at=wakeup.run_at,
        repeat=wakeup.repeat,
        status=wakeup.status,
        untrusted=ImOutboxText(text=wakeup.prompt),
    )


async def pending_items(db: AsyncSession, instance_id: uuid.UUID) -> list[WakeupItem]:
    rows = await db.scalars(
        select(AgentWakeup)
        .where(AgentWakeup.instance_id == instance_id, AgentWakeup.status.in_(OPEN))
        .order_by(AgentWakeup.run_at)
        .limit(50)
    )
    return [item(w) for w in rows]


async def _count_open(db: AsyncSession, instance_id: uuid.UUID, source: str) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(AgentWakeup)
        .where(
            AgentWakeup.instance_id == instance_id,
            AgentWakeup.source == source,
            AgentWakeup.status.in_(OPEN),
        )
    )
    return int(count or 0)


async def _target_key(
    db: AsyncSession, instance: AgentInstance, target: str | None, wakeup_id: uuid.UUID
) -> str:
    if target is None:
        return session_key(None, "", wakeup_id)
    resolved = await resolve_target(db, instance, target)
    if resolved.kind == "group" and resolved.group is not None:
        return session_key("group", resolved.group.group_id, wakeup_id)
    # 私聊：插件按“发来消息的人”建会话，即那位管理员的 IM ID
    return session_key("user", resolved.recv_id, wakeup_id)


# ------------------------------------------------------------ 智能体自己安排（MCP）


async def schedule(
    db: AsyncSession, instance: AgentInstance, parsed: ScheduleWakeupInput
) -> ScheduleWakeupOutput:
    now = _now()
    if parsed.at is not None:
        run_at = parsed.at.astimezone(UTC)
    else:
        run_at = now + timedelta(minutes=parsed.in_minutes or 0)
    if run_at < now + AGENT_MIN_DELAY - timedelta(seconds=30):
        raise ToolCallError("VALIDATION_FAILED", "唤醒最早 5 分钟后；更早的事现在就做")
    if run_at > now + AGENT_MAX_DELAY:
        raise ToolCallError("VALIDATION_FAILED", "唤醒最远 30 天后")
    if await _count_open(db, instance.id, "agent") >= AGENT_MAX_PENDING:
        raise ToolCallError(
            "RATE_LIMITED", "你已经有 20 个待执行的唤醒；先用 cancel_wakeup 取消用不着的"
        )
    wakeup_id = uuid.uuid4()
    key = await _target_key(db, instance, parsed.target, wakeup_id)
    wakeup = AgentWakeup(
        id=wakeup_id,
        workspace_id=instance.workspace_id,
        instance_id=instance.id,
        source="agent",
        target=parsed.target,
        session_key=key,
        prompt=parsed.prompt,
        run_at=run_at,
    )
    db.add(wakeup)
    await db.commit()
    return ScheduleWakeupOutput(
        wakeup_id=wakeup_id,
        run_at=run_at,
        target=parsed.target,
        pending=await _count_open(db, instance.id, "agent"),
    )


async def cancel_own(
    db: AsyncSession, instance: AgentInstance, parsed: CancelWakeupInput
) -> CancelWakeupOutput:
    wakeup = await db.scalar(
        select(AgentWakeup)
        .where(AgentWakeup.id == parsed.wakeup_id, AgentWakeup.instance_id == instance.id)
        .with_for_update()
    )
    if wakeup is None or wakeup.source != "agent":
        raise ToolCallError("NOT_FOUND", "没有这个唤醒，或它是管理员建的（只能取消你自己安排的）")
    if wakeup.status != "pending":
        raise ToolCallError("CONFLICT", "这个唤醒已经开始或结束，不能取消")
    wakeup.status = "cancelled"
    await db.commit()
    return CancelWakeupOutput(wakeup_id=wakeup.id)


# ------------------------------------------------------------ 管理员（实例页）


async def create_by_admin(
    db: AsyncSession,
    instance: AgentInstance,
    *,
    user_id: uuid.UUID | None,
    prompt: str,
    target: str | None,
    run_at: datetime,
    repeat: str,
    time_zone: str,
) -> AgentWakeup:
    zone(time_zone)
    now = _now()
    run_at = run_at.astimezone(UTC)
    if run_at < now - timedelta(minutes=1):
        raise ApiError(422, "VALIDATION_FAILED", "唤醒时间已经过了")
    if run_at > now + ADMIN_MAX_DELAY:
        raise ApiError(422, "VALIDATION_FAILED", "唤醒最远一年后")
    if await _count_open(db, instance.id, "admin") >= ADMIN_MAX_PENDING:
        raise ApiError(409, "LIMIT_REACHED", "这个实例已经有 50 个待执行的唤醒")
    wakeup_id = uuid.uuid4()
    try:
        key = await _target_key(db, instance, target, wakeup_id)
    except ToolCallError as error:
        raise ApiError(422, "VALIDATION_FAILED", error.message) from None
    wakeup = AgentWakeup(
        id=wakeup_id,
        workspace_id=instance.workspace_id,
        instance_id=instance.id,
        source="admin",
        created_by=user_id,
        target=target,
        session_key=key,
        prompt=prompt,
        run_at=run_at,
        repeat=repeat,
        time_zone=time_zone,
    )
    db.add(wakeup)
    return wakeup


async def cancel_by_admin(
    db: AsyncSession, instance_id: uuid.UUID, wakeup_id: uuid.UUID
) -> AgentWakeup:
    wakeup = await db.scalar(
        select(AgentWakeup)
        .where(AgentWakeup.id == wakeup_id, AgentWakeup.instance_id == instance_id)
        .with_for_update()
    )
    if wakeup is None:
        raise ApiError(404, "NOT_FOUND", "唤醒不存在")
    if wakeup.status not in OPEN:
        raise ApiError(409, "CONFLICT", "这个唤醒已经结束")
    # 正在跑的这一轮停不下来；重复的唤醒不再排下一次
    wakeup.status = "cancelled"
    return wakeup


async def list_for_admin(db: AsyncSession, instance_id: uuid.UUID) -> list[AgentWakeup]:
    rows = await db.scalars(
        select(AgentWakeup)
        .where(AgentWakeup.instance_id == instance_id)
        .order_by(AgentWakeup.status.in_(OPEN).desc(), AgentWakeup.run_at.desc())
        .limit(50)
    )
    return list(rows)


# ------------------------------------------------------------ 实例领取与报结果（/wakeups）


async def _expire_stale(db: AsyncSession, instance_id: uuid.UUID, now: datetime) -> None:
    """领取后太久没有结果：记为失败（结果未知，不重跑）；重复的排下一次。"""
    stale = await db.scalars(
        select(AgentWakeup)
        .where(
            AgentWakeup.instance_id == instance_id,
            AgentWakeup.status == "running",
            AgentWakeup.claimed_at < now - STALE_AFTER,
        )
        .with_for_update(skip_locked=True)
    )
    for wakeup in stale:
        _finish(wakeup, ok=False, error="领取后 15 分钟没有报回结果（结果未知，不重跑）", now=now)


def _finish(wakeup: AgentWakeup, *, ok: bool, error: str | None, now: datetime) -> None:
    wakeup.runs += 1
    wakeup.last_run_at = wakeup.claimed_at or now
    wakeup.last_error = None if ok else (error or "失败")[:ERROR_CHARS]
    wakeup.claimed_at = None
    following = next_run(wakeup.run_at, wakeup.repeat, wakeup.time_zone, now)
    if following is not None and wakeup.status != "cancelled":
        wakeup.run_at = following
        wakeup.status = "pending"
    elif wakeup.status != "cancelled":
        wakeup.status = "done" if ok else "failed"


async def claim(
    db: AsyncSession, redis: Redis | None, instance: AgentInstance
) -> list[tuple[AgentWakeup, str]]:
    if redis is not None:
        retry = await WindowLimiter(redis, 60).hit(
            f"agent_wakeup_claim:{instance.id}", CLAIMS_PER_MINUTE
        )
        if retry is not None:
            raise ApiError(429, "RATE_LIMITED", "领取太频繁", headers={"Retry-After": str(retry)})
    now = _now()
    await _expire_stale(db, instance.id, now)
    if instance.disabled_at is not None:
        await db.commit()
        return []
    due = list(
        await db.scalars(
            select(AgentWakeup)
            .where(
                AgentWakeup.instance_id == instance.id,
                AgentWakeup.status == "pending",
                AgentWakeup.run_at <= now,
            )
            .order_by(AgentWakeup.run_at)
            .limit(CLAIM_LIMIT)
            .with_for_update(skip_locked=True)
        )
    )
    claimed: list[tuple[AgentWakeup, str]] = []
    for wakeup in due:
        wakeup.status = "running"
        wakeup.claimed_at = now
        by = "你自己" if wakeup.source == "agent" else "管理员"
        claimed.append((wakeup, message_for(wakeup, by)))
    await db.commit()
    return claimed


async def report(
    db: AsyncSession, instance: AgentInstance, wakeup_id: uuid.UUID, *, ok: bool, error: str | None
) -> AgentWakeup:
    wakeup = await db.scalar(
        select(AgentWakeup)
        .where(AgentWakeup.id == wakeup_id, AgentWakeup.instance_id == instance.id)
        .with_for_update()
    )
    if wakeup is None:
        raise ApiError(404, "NOT_FOUND", "唤醒不存在")
    if wakeup.status == "running" or (wakeup.status == "cancelled" and wakeup.claimed_at):
        _finish(wakeup, ok=ok, error=error, now=_now())
    await db.commit()
    return wakeup


async def purge(db: AsyncSession, now: datetime | None = None) -> int:
    """结束 30 天以上的记录删除（Worker 每天；调用方提交）。"""
    cutoff = (now or _now()) - KEEP
    result = await db.execute(
        delete(AgentWakeup).where(
            AgentWakeup.status.in_(("done", "failed", "cancelled")),
            AgentWakeup.updated_at < cutoff,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "cancel_by_admin",
    "cancel_own",
    "claim",
    "create_by_admin",
    "list_for_admin",
    "next_run",
    "pending_items",
    "purge",
    "report",
    "schedule",
    "session_key",
]
