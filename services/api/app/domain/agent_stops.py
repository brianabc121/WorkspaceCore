"""终止智能体正在执行的任务（T-0126，docs/v2/AGENT_MESSAGING_PLAN.md 第 7.3 节）。

智能体有时会在一件事上反复调用工具停不下来。工作台连不进实例的网关（只监听本机），所以分两层：

- 软停止（立即）：下令后 5 分钟内，这个实例调用工作台的任何 MCP 工具都返回 RUN_STOPPED，
  说明是谁、为什么叫停、要它停下并结束这一轮（Redis 里记一个键，MCP 每次调用前查一下）。
- 硬停止（1 分钟内）：接入包每分钟运行 `stop-runs.mjs`，经 `/agent-api/v1/stops/claim`
  领取终止令，在容器里对 `sessions.list({activeOnly: true})` 列出的会话调
  `sessions.abort({key, clearQueued: true})`（OpenClaw 2026.9.36 的网关 RPC，核验记录 6.2），
  再报回中止了几个会话；报回后解除软停止。
  10 分钟没被领取记为过期（实例不在线或接入包太旧，只有软停止生效），领取后 5 分钟没有结果记为失败。

谁能下令：管理员随时可以；智能体要管理员按实例打开“终止其他智能体任务”的权限（默认关闭，D20），
打开后 `list_agent_runs` / `stop_agent_run` 直接执行（`docs/AGENTS.md` 第 8 节例外六）：
只能叫停本工作空间里别的实例，不能叫停自己，每小时 10 次，写执行记录、审计并通知管理员。
指定了任务时，它在时间轴上正在执行的这一段记为“中断”，任务仍归原执行者（D22）。
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.domain import notifications, task_runs
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.audit import AuditTrail
from app.domain.ratelimit import WindowLimiter
from app.models import AgentInstance, AgentRunStop, Task, TaskRun
from app.schemas.ai_tools import (
    AgentRunItem,
    AgentText,
    EmptyInput,
    ListAgentRunsOutput,
    RunningTask,
    RunningTaskText,
    StopAgentRunInput,
    StopAgentRunOutput,
    ToolSpec,
)

logger = logging.getLogger("app.agent_stops")

SOFT_WINDOW = timedelta(minutes=5)
PENDING_EXPIRE = timedelta(minutes=10)
RUNNING_STALE = timedelta(minutes=5)
KEEP = timedelta(days=30)
STOPS_PER_HOUR = 10
CLAIMS_PER_MINUTE = 6
KEY = "agent_stop:{instance_id}"
OPEN = ("pending", "running")


def _now() -> datetime:
    return datetime.now(UTC)


def _key(instance_id: uuid.UUID) -> str:
    return KEY.format(instance_id=instance_id)


def stopped_message(by: str, reason: str) -> str:
    return (
        f"{by}叫停了你正在执行的任务：{reason}。立刻停下当前的工作，不要再调用工具；"
        "需要的话用一两句话说明做到哪了，然后结束这一轮。"
    )


# ------------------------------------------------------------ 下令


async def create(
    db: AsyncSession,
    target: AgentInstance,
    *,
    reason: str,
    task_id: uuid.UUID | None,
    by_user: uuid.UUID | None,
    by_instance: AgentInstance | None,
) -> AgentRunStop:
    """建终止令；指定了任务就把它正在执行的一段记为中断。调用方写审计、提交，再调 arm()。"""
    if by_instance is not None and by_instance.id == target.id:
        raise ApiError(422, "VALIDATION_FAILED", "不能叫停自己")
    if target.status == "disabled" or target.disabled_at is not None:
        raise ApiError(409, "CONFLICT", "这个实例已经停用")
    if task_id is not None:
        task = await db.get(Task, task_id)
        if task is None or task.workspace_id != target.workspace_id:
            raise ApiError(404, "NOT_FOUND", "任务不存在")
        run = await task_runs.open_run(db, task.id, lock=True)
        if run is not None and run.instance_id == target.id:
            task_runs.end(run, "interrupted", by=by_user)
        elif task.assignee_instance_id != target.id:
            raise ApiError(422, "VALIDATION_FAILED", "这个任务不是它在执行")
    now = _now()
    stop = AgentRunStop(
        workspace_id=target.workspace_id,
        instance_id=target.id,
        requested_by=by_user,
        requested_by_instance=by_instance.id if by_instance is not None else None,
        task_id=task_id,
        reason=reason,
        soft_until=now + SOFT_WINDOW,
    )
    db.add(stop)
    await db.flush()
    return stop


async def arm(redis: Redis | None, stop: AgentRunStop, by: str) -> None:
    """提交之后打开软停止：MCP 每次调用前查这个键。"""
    if redis is None:
        return
    seconds = max(1, int((stop.soft_until - _now()).total_seconds()))
    payload = json.dumps({"id": str(stop.id), "by": by, "reason": stop.reason}, ensure_ascii=False)
    await redis.set(_key(stop.instance_id), payload, ex=seconds)


async def soft_stop(db: AsyncSession, redis: Redis | None, instance_id: uuid.UUID) -> str | None:
    """这个实例现在是否处在软停止里；是就返回给它看的说明。"""
    if redis is not None:
        raw = await redis.get(_key(instance_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return stopped_message(str(data.get("by") or "管理员"), str(data.get("reason") or ""))
        except (TypeError, ValueError):
            return stopped_message("管理员", "")
    stop = await db.scalar(
        select(AgentRunStop)
        .where(
            AgentRunStop.instance_id == instance_id,
            AgentRunStop.status.in_(OPEN),
            AgentRunStop.soft_until > _now(),
        )
        .order_by(AgentRunStop.created_at.desc())
        .limit(1)
    )
    return stopped_message("管理员", stop.reason) if stop is not None else None


# ------------------------------------------------------------ 实例领取与报结果（/stops）


async def _expire(db: AsyncSession, instance_id: uuid.UUID, now: datetime) -> None:
    rows = await db.scalars(
        select(AgentRunStop)
        .where(AgentRunStop.instance_id == instance_id, AgentRunStop.status.in_(OPEN))
        .with_for_update(skip_locked=True)
    )
    for stop in rows:
        if stop.status == "pending" and stop.created_at < now - PENDING_EXPIRE:
            stop.status, stop.finished_at = "expired", now
        elif stop.status == "running" and (stop.claimed_at or now) < now - RUNNING_STALE:
            stop.status, stop.finished_at = "failed", now
            stop.error = "领取后 5 分钟没有报回结果"


async def claim(
    db: AsyncSession, redis: Redis | None, instance: AgentInstance
) -> list[AgentRunStop]:
    if redis is not None:
        retry = await WindowLimiter(redis, 60).hit(
            f"agent_stop_claim:{instance.id}", CLAIMS_PER_MINUTE
        )
        if retry is not None:
            raise ApiError(429, "RATE_LIMITED", "领取太频繁", headers={"Retry-After": str(retry)})
    now = _now()
    await _expire(db, instance.id, now)
    pending = list(
        await db.scalars(
            select(AgentRunStop)
            .where(AgentRunStop.instance_id == instance.id, AgentRunStop.status == "pending")
            .order_by(AgentRunStop.created_at)
            .with_for_update(skip_locked=True)
        )
    )
    for stop in pending:
        stop.status, stop.claimed_at = "running", now
    await db.commit()
    return pending


async def report(
    db: AsyncSession,
    redis: Redis | None,
    instance: AgentInstance,
    stop_id: uuid.UUID,
    *,
    ok: bool,
    aborted: int,
    error: str | None,
) -> AgentRunStop:
    stop = await db.scalar(
        select(AgentRunStop)
        .where(AgentRunStop.id == stop_id, AgentRunStop.instance_id == instance.id)
        .with_for_update()
    )
    if stop is None:
        raise ApiError(404, "NOT_FOUND", "终止令不存在")
    if stop.status == "running":
        stop.status = "done" if ok else "failed"
        stop.aborted = aborted
        stop.error = None if ok else (error or "失败")[:300]
        stop.finished_at = _now()
    still_open = await db.scalar(
        select(AgentRunStop.id).where(
            AgentRunStop.instance_id == instance.id,
            AgentRunStop.status.in_(OPEN),
            AgentRunStop.id != stop.id,
        )
    )
    await db.commit()
    # 网关已经中止了正在运行的会话：解除软停止，它下一次收到消息时照常工作
    if ok and still_open is None and redis is not None:
        await redis.delete(_key(instance.id))
    return stop


# ------------------------------------------------------------ 查询与清理


async def recent(db: AsyncSession, instance_id: uuid.UUID) -> list[AgentRunStop]:
    rows = await db.scalars(
        select(AgentRunStop)
        .where(AgentRunStop.instance_id == instance_id)
        .order_by(AgentRunStop.created_at.desc())
        .limit(20)
    )
    return list(rows)


async def purge(db: AsyncSession, now: datetime | None = None) -> int:
    """结束 30 天以上的终止令删除（Worker 每天；调用方提交）。"""
    cutoff = (now or _now()) - KEEP
    result = await db.execute(
        delete(AgentRunStop).where(
            AgentRunStop.status.in_(("done", "failed", "expired")),
            AgentRunStop.created_at < cutoff,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def runs_overview(
    db: AsyncSession, workspace_id: uuid.UUID, me: uuid.UUID
) -> ListAgentRunsOutput:
    instances = (
        await db.scalars(
            select(AgentInstance)
            .where(AgentInstance.workspace_id == workspace_id)
            .order_by(AgentInstance.name, AgentInstance.id)
            .limit(200)
        )
    ).all()
    ids = [i.id for i in instances]
    running: dict[uuid.UUID, list[RunningTask]] = {i: [] for i in ids}
    if ids:
        rows = await db.execute(
            select(TaskRun.instance_id, TaskRun.task_id, TaskRun.started_at, Task.title)
            .join(Task, Task.id == TaskRun.task_id)
            .where(TaskRun.instance_id.in_(ids), TaskRun.ended_at.is_(None))
            .order_by(TaskRun.started_at)
        )
        for instance_id, task_id, started_at, title in rows:
            if instance_id is None:
                continue
            running.setdefault(instance_id, []).append(
                RunningTask(
                    task_id=task_id, started_at=started_at, untrusted=RunningTaskText(title=title)
                )
            )
    stopping: set[uuid.UUID] = set()
    if ids:
        stopping = set(
            (
                await db.scalars(
                    select(AgentRunStop.instance_id).where(
                        AgentRunStop.instance_id.in_(ids),
                        AgentRunStop.status.in_(OPEN),
                        AgentRunStop.soft_until > _now(),
                    )
                )
            ).all()
        )
    return ListAgentRunsOutput(
        items=[
            AgentRunItem(
                instance_id=i.id,
                status=i.status,
                last_seen_at=i.last_seen_at,
                running_tasks=running.get(i.id, []),
                stopping=i.id in stopping,
                is_you=i.id == me,
                untrusted=AgentText(name=i.name, description=i.description),
            )
            for i in instances
        ]
    )


# ------------------------------------------------------------ MCP 工具（例外六）


async def _limit(redis: Redis | None, instance_id: uuid.UUID) -> None:
    if redis is None:
        return
    if (
        await WindowLimiter(redis, 3600).hit(f"agent_stops:{instance_id}", STOPS_PER_HOUR)
        is not None
    ):
        raise ToolCallError("RATE_LIMITED", "每小时最多叫停 10 次")


async def _list(
    service: AiToolService, requester: Requester, args: EmptyInput
) -> tuple[BaseModel, str, Callable[[], Awaitable[None]]]:
    out = await runs_overview(service.db, requester.workspace.id, requester.instance.id)

    async def nothing() -> None:
        return None

    return out, "列出智能体正在执行的任务", nothing


async def _stop(
    service: AiToolService, requester: Requester, args: StopAgentRunInput
) -> tuple[BaseModel, str, Callable[[], Awaitable[None]]]:
    db, me = service.db, requester.instance
    await _limit(service.redis, me.id)
    target = await db.scalar(
        select(AgentInstance).where(
            AgentInstance.id == args.instance_id,
            AgentInstance.workspace_id == requester.workspace.id,
        )
    )
    if target is None:
        raise ToolCallError("NOT_FOUND", "没有这个智能体；用 list_agent_runs 查看")
    try:
        stop = await create(
            db, target, reason=args.reason, task_id=args.task_id, by_user=None, by_instance=me
        )
    except ApiError as error:
        code = "NOT_FOUND" if error.status_code == 404 else "VALIDATION_FAILED"
        raise ToolCallError(code, error.message) from None
    me_name, target_name = me.name, target.name
    await AuditTrail(db, service.settings).record(
        "agents.run.stop_requested",
        workspace_ids=[requester.workspace.id],
        actor_id=None,
        resource_type="agent_instance",
        resource_id=target.id,
        metadata={
            "by_instance": str(me.id),
            "task_id": str(args.task_id) if args.task_id else None,
        },
    )
    await notifications.notify_admins(
        db,
        requester.workspace.id,
        type_="agent.run_stopped_by_agent",
        severity="warning",
        title=f"「{me_name}」叫停了「{target_name}」正在执行的任务",
        body=args.reason,
        link="/agents",
        key=str(stop.id),
    )
    await db.flush()
    out = StopAgentRunOutput(
        stop_id=stop.id,
        soft_until=stop.soft_until,
        note=f"已叫停「{target_name}」：它接下来调用工作台工具会被拒绝，1 分钟内中止正在运行的会话",
    )

    async def after() -> None:
        await arm(service.redis, stop, f"智能体「{me_name}」")

    return out, f"叫停「{target_name}」", after


_HANDLERS: dict[str, Any] = {"list_agent_runs": _list, "stop_agent_run": _stop}


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
    if denied is None and not requester.instance.stop_others_enabled:
        denied = "FORBIDDEN_CAPABILITY"
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        message = (
            "管理员还没有给你开“终止其他智能体任务”的权限"
            if denied == "FORBIDDEN_CAPABILITY"
            else "这个实例没有权限调用这个工具"
        )
        raise ToolCallError(denied, message)
    instance_id = requester.instance.id
    try:
        output, summary, after = await _HANDLERS[spec.name](service, requester, parsed)
    except ToolCallError as error:
        await service.db.rollback()
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            await service.record_knowledge(
                await service.requester(instance),
                spec,
                arguments,
                ok=False,
                summary=f"{spec.name}：{error.code}",
                error_code=error.code,
                request_id=request_id,
            )
        raise
    await service.record_knowledge(
        requester, spec, arguments, ok=True, summary=summary, request_id=request_id
    )
    await after()
    result: BaseModel = output
    return result


__all__ = [
    "arm",
    "claim",
    "create",
    "purge",
    "recent",
    "report",
    "run",
    "runs_overview",
    "soft_stop",
    "stopped_message",
]
