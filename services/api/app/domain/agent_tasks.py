"""智能体领取、放弃、完成任务的工具（T-0111，docs/v2/AGENT_OPS_PLAN.md 第 3.3 ~ 3.5 节）。

`claim_task`、`release_task`、`complete_task` **直接执行、不审批**（风险等级 direct；
人类 2026-11-04 决定，`docs/AGENTS.md` 第 8 节例外二）。保护措施：
- 仍要经过权限范围与角色检查（scope 为 read_write、登记人角色够 member），不够的直接拒绝并留记录；
- 每次调用都留执行记录（“AI 工具执行”里标为直接执行）、写审计、通知管理员；
- 领取：每个实例每小时 10 次；先锁住实例（事务级咨询锁）再锁住任务这一行：
  同一个实例并发领取只成功一个（空闲才能领）；同一个任务并发领取先到先得，
  晚到的返回 TASK_ALREADY_CLAIMED；
- 只能放弃自己领取的（自由接取的）任务，只能完成执行者是自己的任务；
- 管理员随时可以打回、改派、停用实例。

公共的判断与消息在 app/domain/task_claims.py。
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import notifications, project_groups, task_claims, task_runs
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.audit import AuditTrail
from app.domain.ratelimit import WindowLimiter
from app.integrations.openim import OpenIMClient
from app.models import AgentInstance, Project, Task, TaskRun
from app.schemas.ai_tools import (
    ClaimTaskOutput,
    CompleteTaskInput,
    CompleteTaskOutput,
    ReleaseTaskInput,
    ReleaseTaskOutput,
    TaskRef,
    ToolSpec,
)

logger = logging.getLogger("app.agent_tasks")

CLAIMS_PER_HOUR = 10

After = Callable[[], Awaitable[None]]


async def _nothing() -> None:
    return None


@dataclass
class _Ctx:
    db: AsyncSession
    settings: Settings
    redis: Redis | None
    im: OpenIMClient | None
    requester: Requester

    @property
    def instance(self) -> AgentInstance:
        return self.requester.instance


def _now() -> datetime:
    return datetime.now(UTC)


async def _task(ctx: _Ctx, task_id: uuid.UUID) -> Task:
    task: Task | None = await ctx.db.scalar(
        select(Task)
        .where(Task.id == task_id, Task.workspace_id == ctx.requester.workspace.id)
        .with_for_update()
    )
    if task is None:
        raise ToolCallError("NOT_FOUND", "任务不存在")
    return task


async def _record(ctx: _Ctx, action: str, task: Task, extra: dict[str, Any] | None = None) -> None:
    await AuditTrail(ctx.db, ctx.settings).record(
        action,
        workspace_ids=[task.workspace_id],
        actor_id=None,
        resource_type="task",
        resource_id=task.id,
        metadata={"instance_id": str(ctx.instance.id), **(extra or {})},
    )


async def _notify(ctx: _Ctx, task: Task, kind: str, title: str) -> None:
    await notifications.notify_admins(
        ctx.db,
        task.workspace_id,
        type_=f"task.{kind}",
        severity="info",
        title=title,
        body=task.title,
        link="/tasks",
        key=f"{task.id}:{task.version}",
    )


# ---------------------------------------------------------------- 领取


async def _claim(ctx: _Ctx, args: TaskRef) -> tuple[ClaimTaskOutput, After]:
    db, instance = ctx.db, ctx.instance
    instance_id, instance_name = instance.id, instance.name
    if ctx.redis is not None:
        limiter = WindowLimiter(ctx.redis, 3600)
        if await limiter.hit(f"task_claims:{instance_id}", CLAIMS_PER_HOUR) is not None:
            raise ToolCallError("RATE_LIMITED", "领取太频繁：每个实例每小时最多领取 10 次")
    # 同一个实例的领取串行：空闲判断与开段在同一把锁里
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"task-claim:{instance_id}"},
    )
    task = await _task(ctx, args.task_id)
    if task.assignee_instance_id == instance_id and not task.done:
        return (
            ClaimTaskOutput(
                task_id=task.id,
                already_yours=True,
                joined_project=False,
                next_step="这个任务已经是你的了：用 readme_get 读任务 README，"
                "做完后用 readme_update 更新“进展与交接”，再用 complete_task。",
            ),
            _nothing,
        )
    if task.done or task.claim_mode != "open":
        raise ToolCallError(
            "TASK_NOT_CLAIMABLE", "这个任务不能自由接取（由管理员指派，或已经完成）"
        )
    if task.assignee_user_id is not None or task.assignee_instance_id is not None:
        raise ToolCallError("TASK_ALREADY_CLAIMED", "这个任务已经被领取了")
    if not await task_claims.in_scope(db, task, instance_id):
        raise ToolCallError("TASK_NOT_CLAIMABLE", "这个任务只限项目成员领取")
    if await task_claims.busy(db, instance_id):
        raise ToolCallError("AGENT_BUSY", "你还有正在执行的任务：先完成或放弃它，再领取新的任务")
    now = _now()
    task.assignee_instance_id = instance_id
    task.version += 1
    db.add(
        TaskRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            executor_kind="instance",
            instance_id=instance_id,
            executor_name=instance_name,
            started_at=now,
            started_by=None,
        )
    )
    task_runs.mark_started(task, now)
    project = await db.get(Project, task.project_id, with_for_update=True)
    joined = False
    if project is not None:
        joined = bool(await project_groups.add_agents(db, project, [instance_id], added_by=None))
    db.add(
        task_claims.event(
            task, "claimed", actor_kind="instance", name=instance_name, instance_id=instance_id
        )
    )
    await _record(ctx, "task.claimed", task, {"joined_project": joined})
    await _notify(ctx, task, "claimed", f"「{instance_name}」领取了任务")
    await db.flush()
    title, project_id = task.title, task.project_id

    async def after() -> None:
        await task_claims.group_notice(db, ctx.im, project_id, f"{instance_name} 领取了《{title}》")
        if joined and ctx.im is not None:
            await project_groups.ProjectGroupSync(db, ctx.settings, ctx.im).sync(project_id)

    # 领取成功直接带上任务 README（T-0122，方案 9.6）
    from app.domain import readme_tools
    from app.domain.readme import Readmes

    note = await Readmes(db, ctx.settings).ensure_task(task)
    return (
        ClaimTaskOutput(
            task_id=task.id,
            already_yours=False,
            joined_project=joined,
            readme=readme_tools.doc(note),
            next_step="已领取：先读下面的任务 README 和项目简报，做完后用 readme_update 更新"
            "“进展与交接”，再用 complete_task。",
        ),
        after,
    )


# ---------------------------------------------------------------- 放弃


async def _release(ctx: _Ctx, args: ReleaseTaskInput) -> tuple[ReleaseTaskOutput, After]:
    db, instance = ctx.db, ctx.instance
    instance_id, instance_name = instance.id, instance.name
    task = await _task(ctx, args.task_id)
    if task.assignee_instance_id != instance_id or task.done:
        raise ToolCallError("TASK_NOT_YOURS", "只能放弃你自己领取的、还没完成的任务")
    if task.claim_mode != "open":
        raise ToolCallError(
            "TASK_NOT_RELEASABLE", "这是管理员指派给你的任务，不能放弃；有问题请在单聊里跟管理员说"
        )
    await task_runs.end_open_run(db, task, "paused", by=None)
    task.assignee_instance_id = None
    task.version += 1
    db.add(
        task_claims.event(
            task,
            "released",
            actor_kind="instance",
            name=instance_name,
            instance_id=instance_id,
            note=args.reason,
        )
    )
    await _record(ctx, "task.released", task)
    await _notify(ctx, task, "released", f"「{instance_name}」放弃了任务")
    await db.flush()
    task_id, title, project_id = task.id, task.title, task.project_id

    async def after() -> None:
        await task_claims.group_notice(db, ctx.im, project_id, f"{instance_name} 放弃了《{title}》")
        await task_claims.announce(db, ctx.settings, ctx.redis, ctx.im, task_id)

    return ReleaseTaskOutput(task_id=task_id), after


# ---------------------------------------------------------------- 完成


async def _complete(ctx: _Ctx, args: CompleteTaskInput) -> tuple[CompleteTaskOutput, After]:
    db, instance = ctx.db, ctx.instance
    instance_id, instance_name = instance.id, instance.name
    task = await _task(ctx, args.task_id)
    if task.assignee_instance_id != instance_id:
        raise ToolCallError("TASK_NOT_YOURS", "只能完成执行者是你自己的任务")
    if task.done:
        return (
            CompleteTaskOutput(task_id=task.id, done_at=task.done_at, already_done=True),
            _nothing,
        )
    now = _now()
    task.done = True
    task.done_at = now
    await task_runs.end_open_run(db, task, "done", by=None, at=now)
    task.version += 1
    db.add(
        task_claims.event(
            task,
            "completed",
            actor_kind="instance",
            name=instance_name,
            instance_id=instance_id,
            note=args.note,
        )
    )
    await _record(ctx, "task.completed", task)
    await _notify(ctx, task, "completed", f"「{instance_name}」完成了任务")
    await db.flush()
    title, project_id = task.title, task.project_id

    async def after() -> None:
        await task_claims.group_notice(db, ctx.im, project_id, f"{instance_name} 完成了《{title}》")

    return CompleteTaskOutput(task_id=task.id, done_at=now, already_done=False), after


_HANDLERS: dict[str, Callable[[_Ctx, Any], Awaitable[tuple[BaseModel, After]]]] = {
    "claim_task": _claim,
    "release_task": _release,
    "complete_task": _complete,
}
TASK_TOOL_NAMES = frozenset(_HANDLERS)


def _summary(name: str, arguments: dict[str, Any]) -> str:
    verb = {"claim_task": "领取任务", "release_task": "放弃任务", "complete_task": "完成任务"}[name]
    return f"{verb} {arguments.get('task_id', '')}"


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
    im: OpenIMClient | None,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = parsed.model_dump(mode="json")
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    instance_id = requester.instance.id
    ctx = _Ctx(service.db, service.settings, service.redis, im, requester)
    try:
        output, after = await _HANDLERS[spec.name](ctx, parsed)
    except (ToolCallError, ApiError) as error:
        await service.db.rollback()
        failure = (
            error
            if isinstance(error, ToolCallError)
            else ToolCallError("VALIDATION_FAILED", error.message)
        )
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            await service.record_knowledge(
                await service.requester(instance),
                spec,
                arguments,
                ok=False,
                summary=_summary(spec.name, arguments),
                error_code=failure.code,
                request_id=request_id,
            )
        raise failure from None
    # 执行记录与改动一起提交
    await service.record_knowledge(
        requester,
        spec,
        arguments,
        ok=True,
        summary=_summary(spec.name, arguments),
        request_id=request_id,
    )
    try:
        await after()
    except Exception:  # pragma: no cover - 通知失败不影响结果
        logger.exception("task tool follow-up failed", extra={"tool": spec.name})
    return output
