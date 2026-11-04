"""执行记录的底层操作与 token 归属（T-0104，docs/v2/TIMELINE_PLAN.md 第 2.3、2.4 节）。

放在单独的模块里：任务服务（完成任务时结束这段）、实例服务（停用时中断）、Worker 都要用，
它们之间不互相依赖。

**token 按时间归属**：接入包每分钟上报一次，工作台把增量记到收到上报的那一分钟
（`agent_usage_minutes`）。
一段执行用的 token = 这个实例在这段时间里每分钟增量之和：
- 上报有约一分钟的延迟（这一分钟的用量下一分钟才报上来），所以一分钟 m 的增量归给与
  [m − 1 分钟, m + 1 分钟) 有重叠的段；
- 同一实例同时有几段覆盖同一分钟，就平均分（余数给先开始的段），这段标为 `shared`（界面显示“≈”）；
- 这一时段里实例的其他对话（闲聊、每晚复盘）也算进去，界面注明“按时间归属”。
"""

import logging
import uuid
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import agent_watch
from app.models import AgentInstance, AgentUsageMinute, AiToolExecution, Task, TaskRun

logger = logging.getLogger("app.timeline")

LAG = timedelta(minutes=1)
# 实例离线超过这么久，正在执行的段记为中断（方案 2.3）
OFFLINE_INTERRUPT_AFTER = timedelta(minutes=10)
# 等待审批：这些风险级别的工具调用要人批准（读工具和知识库工具直接执行）
APPROVAL_RISKS = ("write", "critical")

Tokens = tuple[int, int, int, int]
ZERO: Tokens = (0, 0, 0, 0)


def _now() -> datetime:
    return datetime.now(UTC)


def add(a: Tokens, b: Tokens) -> Tokens:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


def split(tokens: Tokens, parts: int) -> list[Tokens]:
    """平均分成 parts 份，余数依次给前面的几份；合计不变。"""
    if parts <= 1:
        return [tokens]
    shares: list[list[int]] = [[0, 0, 0, 0] for _ in range(parts)]
    for column, value in enumerate(tokens):
        base, rest = divmod(value, parts)
        for i in range(parts):
            shares[i][column] = base + (1 if i < rest else 0)
    return [(s[0], s[1], s[2], s[3]) for s in shares]


# ---------------------------------------------------------------- 开始与结束


async def open_run(db: AsyncSession, task_id: uuid.UUID, *, lock: bool = False) -> TaskRun | None:
    query = select(TaskRun).where(TaskRun.task_id == task_id, TaskRun.ended_at.is_(None))
    if lock:
        query = query.with_for_update()
    run: TaskRun | None = await db.scalar(query)
    return run


def end(run: TaskRun, reason: str, *, by: uuid.UUID | None, at: datetime | None = None) -> None:
    """结束一段（数据失效事件由 ORM 的 after_flush 自动登记）。"""
    moment = at or _now()
    run.ended_at = max(moment, run.started_at)
    run.end_reason = reason
    run.ended_by = by


async def end_open_run(
    db: AsyncSession,
    task: Task,
    reason: str,
    *,
    by: uuid.UUID | None,
    at: datetime | None = None,
) -> TaskRun | None:
    """结束这个任务正在执行的一段（完成任务、改了执行者时）。没有就什么也不做。"""
    run = await open_run(db, task.id, lock=True)
    if run is not None:
        end(run, reason, by=by, at=at)
    return run


async def interrupt_instance_runs(
    db: AsyncSession, instance_id: uuid.UUID, *, at: datetime | None = None
) -> int:
    """实例被停用：它正在执行的段记为中断。调用方提交。"""
    rows = (
        await db.scalars(
            select(TaskRun)
            .where(TaskRun.instance_id == instance_id, TaskRun.ended_at.is_(None))
            .with_for_update()
        )
    ).all()
    for run in rows:
        end(run, "interrupted", by=None, at=at)
    return len(rows)


async def interrupt_stale_runs(db: AsyncSession, redis: Redis, now: datetime | None = None) -> int:
    """Worker 每分钟：实例被停用、删除，或离线超过 10 分钟，正在执行的段记为中断。

    离线的段结束在离线开始的时间（Redis 里 agent_watch 记下的），而不是发现的时间。调用方提交。
    """
    moment = now or _now()
    rows = (
        await db.execute(
            select(TaskRun, AgentInstance)
            .outerjoin(AgentInstance, AgentInstance.id == TaskRun.instance_id)
            .where(TaskRun.executor_kind == "instance", TaskRun.ended_at.is_(None))
            .with_for_update(of=TaskRun, skip_locked=True)
        )
    ).all()
    ended = 0
    for run, instance in rows:
        if instance is None:
            end(run, "interrupted", by=None, at=moment)
        elif instance.status == "disabled":
            end(run, "interrupted", by=None, at=instance.disabled_at or moment)
        elif instance.status in ("offline", "pending"):
            since = await agent_watch.offline_since(redis, instance, moment)
            if moment - since < OFFLINE_INTERRUPT_AFTER:
                continue
            end(run, "interrupted", by=None, at=since)
        else:
            continue
        ended += 1
    if ended:
        logger.info("task runs interrupted", extra={"count": ended})
    return ended


# ---------------------------------------------------------------- token 归属


@dataclass
class Attribution:
    tokens: Tokens = ZERO
    shared: bool = False
    # (分钟, 这一段分到的 token)，按时间顺序
    minutes: list[tuple[datetime, Tokens]] = field(default_factory=list)


MinuteRows = dict[uuid.UUID, list[tuple[datetime, Tokens]]]


async def load_minutes(
    db: AsyncSession, instance_ids: Iterable[uuid.UUID], lo: datetime, hi: datetime
) -> MinuteRows:
    ids = sorted(set(instance_ids))
    found: MinuteRows = defaultdict(list)
    if not ids:
        return found
    rows = await db.execute(
        select(
            AgentUsageMinute.instance_id,
            AgentUsageMinute.minute,
            AgentUsageMinute.input,
            AgentUsageMinute.output,
            AgentUsageMinute.cache_read,
            AgentUsageMinute.cache_write,
        )
        .where(
            AgentUsageMinute.instance_id.in_(ids),
            AgentUsageMinute.minute >= lo,
            AgentUsageMinute.minute < hi,
        )
        .order_by(AgentUsageMinute.instance_id, AgentUsageMinute.minute)
    )
    for instance_id, minute, i, o, cr, cw in rows:
        found[instance_id].append((minute, (i, o, cr, cw)))
    return found


def attribute(
    runs: Sequence[TaskRun], minutes: MinuteRows, now: datetime
) -> dict[uuid.UUID, Attribution]:
    """把每个实例每分钟的增量分给覆盖这一分钟的段（见模块说明）。

    runs 应包含同一实例在这段时间里的**所有**段（不只是要显示的），平均分才准确。
    """
    result: dict[uuid.UUID, Attribution] = {r.id: Attribution() for r in runs}
    by_instance: dict[uuid.UUID, list[TaskRun]] = defaultdict(list)
    for run in runs:
        if run.executor_kind == "instance" and run.instance_id is not None:
            by_instance[run.instance_id].append(run)
    for instance_id, instance_runs in by_instance.items():
        rows = minutes.get(instance_id, [])
        if not rows:
            continue
        times = [m for m, _ in rows]
        covering: dict[int, list[TaskRun]] = defaultdict(list)
        for run in sorted(instance_runs, key=lambda r: (r.started_at, r.id)):
            stop = run.ended_at or now
            first = bisect_right(times, run.started_at - LAG)
            last = bisect_left(times, stop + LAG)
            for index in range(first, last):
                covering[index].append(run)
        for index in sorted(covering):
            owners = covering[index]
            minute, tokens = rows[index]
            for run, share in zip(owners, split(tokens, len(owners)), strict=True):
                item = result[run.id]
                item.tokens = add(item.tokens, share)
                item.minutes.append((minute, share))
                if len(owners) > 1:
                    item.shared = True
    return result


async def runs_overlapping(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    instance_ids: Iterable[uuid.UUID],
    lo: datetime,
    hi: datetime,
) -> list[TaskRun]:
    """这些实例在 [lo, hi) 里的所有段（平均分时要用到其他项目的段）。"""
    ids = sorted(set(instance_ids))
    if not ids:
        return []
    return list(
        (
            await db.scalars(
                select(TaskRun).where(
                    TaskRun.workspace_id == workspace_id,
                    TaskRun.instance_id.in_(ids),
                    TaskRun.started_at < hi + LAG,
                    or_(TaskRun.ended_at.is_(None), TaskRun.ended_at > lo - LAG),
                )
            )
        ).all()
    )


# ---------------------------------------------------------------- 等待审批


@dataclass(frozen=True)
class Wait:
    execution_id: uuid.UUID
    instance_id: uuid.UUID
    tool: str
    start: datetime
    end: datetime | None


def _wait_end(row: AiToolExecution) -> datetime | None:
    if row.status == "pending_approval":
        return None
    for value in (row.decided_at, row.expires_at if row.status == "expired" else None):
        if value is not None:
            return value
    return row.executed_at or row.created_at


async def load_waits(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    instance_ids: Iterable[uuid.UUID],
    lo: datetime,
    hi: datetime,
) -> dict[uuid.UUID, list[Wait]]:
    ids = sorted(set(instance_ids))
    found: dict[uuid.UUID, list[Wait]] = defaultdict(list)
    if not ids:
        return found
    rows = (
        await db.scalars(
            select(AiToolExecution)
            .where(
                AiToolExecution.workspace_id == workspace_id,
                AiToolExecution.instance_id.in_(ids),
                AiToolExecution.risk_level.in_(APPROVAL_RISKS),
                AiToolExecution.status != "denied",
                AiToolExecution.created_at < hi,
                or_(
                    AiToolExecution.status == "pending_approval",
                    func.coalesce(
                        AiToolExecution.decided_at,
                        AiToolExecution.executed_at,
                        AiToolExecution.expires_at,
                        AiToolExecution.created_at,
                    )
                    > lo,
                ),
            )
            .order_by(AiToolExecution.created_at)
            .limit(5000)
        )
    ).all()
    for row in rows:
        if row.instance_id is None:  # pragma: no cover - 条件里已排除
            continue
        found[row.instance_id].append(
            Wait(row.id, row.instance_id, row.tool_name, row.created_at, _wait_end(row))
        )
    return found


def waits_in(run: TaskRun, waits: dict[uuid.UUID, list[Wait]], now: datetime) -> list[Wait]:
    """落在这段里的等待审批，裁到这段的起止。"""
    if run.instance_id is None:
        return []
    stop = run.ended_at or now
    inside: list[Wait] = []
    for wait in waits.get(run.instance_id, []):
        wait_end = wait.end or now
        if wait.start >= stop or wait_end <= run.started_at:
            continue
        end_at = None if wait.end is None and run.ended_at is None else min(wait_end, stop)
        start_at = max(wait.start, run.started_at)
        inside.append(Wait(wait.execution_id, wait.instance_id, wait.tool, start_at, end_at))
    return inside


def mark_started(task: Task, at: datetime) -> None:
    """第一次开始执行：记下 started_at（不改 version，不和正在编辑任务的人冲突）。"""
    if task.started_at is None:
        task.started_at = at
