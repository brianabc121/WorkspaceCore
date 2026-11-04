"""任务时间轴：执行记录、交给智能体执行、用量上报、时间轴。

T-0104，docs/v2/TIMELINE_PLAN.md 第 2 节。

**开始一段**（`POST /tasks/{id}/runs`，带 Idempotency-Key）：
- 人的任务：member 及以上；任务没有执行者时，当前用户成为执行者；
- 交给实例执行（`dispatch: true`）：owner / admin。工作台以**当前管理员自己的 IM 账号**给实例发一条
  固定格式的消息（接入包的 IM 渠道只放行管理员，所以不能由工作台代发），发出后开一段；
  - 消息的 Idempotency-Key 由请求的键推出：同一个请求重试不会发两条；
  - IM 结果不确定（504 RESULT_UNKNOWN）时不开段，调用方用同一个键重试，由 IM 接口查清结果后再开；
- 同一个请求重试（同一个键）原样返回第一次开的那一段。

**结束一段**：人点暂停 / 结束；标为完成时结束（`TaskService.update_task`，
智能体经审批完成也走这里）；改执行者时中断；实例停用、删除、离线超过 10 分钟时中断
（`task_runs`）。**不新增 MCP 写工具**。

**用量上报**（`POST /agent-api/v1/usage`，实例凭证）：按（实例、日期、会话）记住上次的累计值，
增量 = 新值 − 旧值；值变小（会话被删除或压缩）就以新值为准、这次记 0；`usage` 为 null 跳过；
实例第一次上报只记基准，不把之前的用量都算到这一分钟。
"""

import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events, task_runs
from app.domain.audit import AuditTrail
from app.domain.auth import Actor
from app.domain.im_service import ImService
from app.domain.policy import WorkspaceAccess, has_role, not_found
from app.domain.ratelimit import WindowLimiter
from app.domain.tasks import WriteResult
from app.integrations.openim import OpenIMClient
from app.models import (
    AgentInstance,
    AgentUsageCursor,
    AgentUsageMinute,
    ImMessage,
    Project,
    Task,
    TaskRun,
    User,
    WorkspaceMember,
)
from app.schemas.im import ImMessageSend
from app.schemas.timeline import (
    ApprovalWait,
    AssigneeInstance,
    AssigneeOptions,
    AssigneeUser,
    TaskRunList,
    TaskRunOut,
    TaskRunStart,
    TaskRunStop,
    TimelineGroup,
    TimelineOut,
    TimelineSegment,
    TimelineSummary,
    TimelineTask,
    TimelineTrack,
    TimelineUsage,
    TokenCount,
    UsagePoint,
    UsageReport,
    UsageReportResult,
)
from app.schemas.workspaces import Role

logger = logging.getLogger("app.timeline")

# 消息的 Idempotency-Key 由请求的键推出（uuid5），同一个请求重试不会发两条
_DISPATCH_NAMESPACE = uuid.UUID("5b1f8c62-3c0e-4f43-9d0f-6d1c1f0f7a14")
RUNS_LIMIT = 100
SERIES_MINUTES = 10
MAX_RANGE = timedelta(days=31)
MAX_TIMELINE_RUNS = 2000
MAX_TIMELINE_TASKS = 1000
MAX_BUCKETS = 300
BUCKET_CHOICES = (5, 10, 15, 30, 60, 120, 180, 360, 720, 1440)
USAGE_REPORTS_PER_MINUTE = 6
USAGE_RETENTION = timedelta(days=90)
CURSOR_RETENTION_DAYS = 3
_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def token_count(tokens: task_runs.Tokens) -> TokenCount:
    return TokenCount(
        input=tokens[0],
        output=tokens[1],
        cache_read=tokens[2],
        cache_write=tokens[3],
        total=sum(tokens),
    )


def _floor(moment: datetime, minutes: int) -> datetime:
    step = minutes * 60
    seconds = int((moment - _ORIGIN).total_seconds()) // step * step
    return _ORIGIN + timedelta(seconds=seconds)


def bucket_minutes(start: datetime, end: datetime) -> int:
    span = (end - start).total_seconds() / 60
    for choice in BUCKET_CHOICES:
        if span / choice <= MAX_BUCKETS:
            return choice
    return BUCKET_CHOICES[-1]


def _series(minutes: Iterable[tuple[datetime, task_runs.Tokens]], step: int) -> list[UsagePoint]:
    buckets: dict[datetime, task_runs.Tokens] = {}
    for minute, tokens in minutes:
        key = _floor(minute, step)
        buckets[key] = task_runs.add(buckets.get(key, task_runs.ZERO), tokens)
    return [
        UsagePoint(start=key, tokens=token_count(value))
        for key, value in sorted(buckets.items())
        if sum(value)
    ]


def dispatch_text(task: Task) -> str:
    due = (
        f"截止时间：{task.due_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M')}（UTC）"
        if task.due_at
        else "没有截止时间"
    )
    return (
        f"请执行工作台任务「{task.title}」\n"
        f"task_id: {task.id}\n"
        f"{due}\n"
        "先用 get_task 工具读任务详情；做完后用 complete_task 工具把它标为完成（需要管理员审批）。"
    )


async def describe_runs(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    runs: Sequence[TaskRun],
    now: datetime,
    *,
    series: bool = False,
) -> list[TaskRunOut]:
    """执行记录的输出：token（按时间归属）、等待审批，需要时带每 10 分钟的用量。"""
    instance_runs = [r for r in runs if r.executor_kind == "instance" and r.instance_id]
    instance_ids = {r.instance_id for r in instance_runs if r.instance_id is not None}
    attribution: dict[uuid.UUID, task_runs.Attribution] = {}
    waits: dict[uuid.UUID, list[task_runs.Wait]] = {}
    reported: set[uuid.UUID] = set()
    if instance_ids:
        lo = min(r.started_at for r in instance_runs)
        hi = max(r.ended_at or now for r in instance_runs)
        everyone = {
            r.id: r
            for r in await task_runs.runs_overlapping(db, workspace_id, instance_ids, lo, hi)
        }
        everyone.update({r.id: r for r in instance_runs})
        minutes = await task_runs.load_minutes(
            db, instance_ids, lo - 2 * task_runs.LAG, hi + 2 * task_runs.LAG
        )
        attribution = task_runs.attribute(list(everyone.values()), minutes, now)
        waits = await task_runs.load_waits(db, workspace_id, instance_ids, lo, hi)
        reported = set(
            (
                await db.scalars(
                    select(AgentInstance.id).where(
                        AgentInstance.id.in_(instance_ids),
                        AgentInstance.usage_reported_at.is_not(None),
                    )
                )
            ).all()
        )
    out: list[TaskRunOut] = []
    for run in runs:
        item = attribution.get(run.id, task_runs.Attribution())
        is_reported = run.instance_id in reported
        out.append(
            TaskRunOut(
                id=run.id,
                task_id=run.task_id,
                executor_kind=run.executor_kind,
                user_id=run.user_id,
                instance_id=run.instance_id,
                executor_name=run.executor_name,
                started_at=run.started_at,
                ended_at=run.ended_at,
                end_reason=run.end_reason,
                started_by=run.started_by,
                ended_by=run.ended_by,
                tokens=token_count(item.tokens)
                if run.executor_kind == "instance" and is_reported
                else None,
                tokens_shared=item.shared,
                usage_reported=is_reported,
                waits=[
                    ApprovalWait(execution_id=w.execution_id, tool=w.tool, start=w.start, end=w.end)
                    for w in task_runs.waits_in(run, waits, now)
                ],
                series=_series(item.minutes, SERIES_MINUTES) if series else [],
            )
        )
    return out


def _task_done() -> ApiError:
    return ApiError(409, "TASK_DONE", "任务已经完成")


def _run_open() -> ApiError:
    return ApiError(409, "RUN_ALREADY_OPEN", "这个任务已经有一段正在执行")


def _reused() -> ApiError:
    return ApiError(409, "IDEMPOTENCY_KEY_REUSED", "同一个 Idempotency-Key 已用于不同的请求")


class RunService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None,
        im: OpenIMClient | None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.im = im
        self.audit = AuditTrail(db, settings)

    async def _task(
        self, access: WorkspaceAccess, task_id: uuid.UUID, *, lock: bool = False
    ) -> Task:
        query = select(Task).where(Task.workspace_id == access.workspace.id, Task.id == task_id)
        if lock:
            query = query.with_for_update()
        task: Task | None = await self.db.scalar(query)
        if task is None:
            raise not_found()
        return task

    async def _out(self, run: TaskRun, *, series: bool = False) -> TaskRunOut:
        return (await describe_runs(self.db, run.workspace_id, [run], _now(), series=series))[0]

    async def _audit(
        self, action: str, context: Actor, access: WorkspaceAccess, run: TaskRun
    ) -> None:
        await self.audit.record(
            action,
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="task",
            resource_id=run.task_id,
            metadata={
                "run_id": str(run.id),
                "executor_kind": run.executor_kind,
                **({"end_reason": run.end_reason} if run.end_reason else {}),
            },
            client_ip=context.client_ip,
        )

    # ---------------------------------------------------------------- 读取

    async def list_runs(self, access: WorkspaceAccess, task_id: uuid.UUID) -> TaskRunList:
        task = await self._task(access, task_id)
        rows = (
            await self.db.scalars(
                select(TaskRun)
                .where(TaskRun.task_id == task.id)
                .order_by(TaskRun.started_at.desc(), TaskRun.id)
                .limit(RUNS_LIMIT)
            )
        ).all()
        return TaskRunList(
            items=await describe_runs(self.db, access.workspace.id, rows, _now(), series=True)
        )

    # ---------------------------------------------------------------- 开始

    async def _replay(
        self, access: WorkspaceAccess, task_id: uuid.UUID, key: uuid.UUID, dispatch: bool
    ) -> WriteResult | None:
        run: TaskRun | None = await self.db.scalar(select(TaskRun).where(TaskRun.start_key == key))
        if run is None:
            return None
        if (
            run.workspace_id != access.workspace.id
            or run.task_id != task_id
            or (run.executor_kind == "instance") != dispatch
        ):
            raise _reused()
        return WriteResult(201, (await self._out(run)).model_dump(mode="json"), replayed=True)

    async def start(
        self,
        context: Actor,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        key: uuid.UUID,
        body: TaskRunStart,
    ) -> WriteResult:
        replay = await self._replay(access, task_id, key, body.dispatch)
        if replay is not None:
            return replay
        if body.dispatch:
            return await self._dispatch(context, access, task_id, key)
        task = await self._task(access, task_id, lock=True)
        if task.done:
            raise _task_done()
        if task.assignee_instance_id is not None:
            raise ApiError(409, "AGENT_TASK", "执行者是智能体：请用“交给它执行”")
        if await task_runs.open_run(self.db, task.id) is not None:
            raise _run_open()
        if task.assignee_user_id is None:
            # 没有执行者时，点“开始”的人成为执行者（不改 version）
            task.assignee_user_id = context.user.id
        user = await self.db.get(User, task.assignee_user_id)
        now = _now()
        run = TaskRun(
            workspace_id=access.workspace.id,
            task_id=task.id,
            executor_kind="user",
            user_id=task.assignee_user_id,
            executor_name=user.display_name if user is not None else "已删除的成员",
            started_at=now,
            started_by=context.user.id,
            start_key=key,
        )
        self.db.add(run)
        task_runs.mark_started(task, now)
        return await self._commit_start(context, access, run, key, "task.run.started")

    async def _commit_start(
        self, context: Actor, access: WorkspaceAccess, run: TaskRun, key: uuid.UUID, action: str
    ) -> WriteResult:
        task_id, dispatch = run.task_id, run.executor_kind == "instance"
        try:
            await self.db.flush()
            await self._audit(action, context, access, run)
            await self.db.commit()
        except IntegrityError:
            # 并发的同一个请求先开了这一段，或另一个请求先开了别的段
            await self.db.rollback()
            replay = await self._replay(access, task_id, key, dispatch)
            if replay is not None:
                return replay
            raise _run_open() from None
        return WriteResult(201, (await self._out(run)).model_dump(mode="json"))

    async def _dispatch(
        self, context: Actor, access: WorkspaceAccess, task_id: uuid.UUID, key: uuid.UUID
    ) -> WriteResult:
        if not has_role(access.role, Role.admin):
            raise ApiError(403, "FORBIDDEN", "只有 owner / admin 能把任务交给智能体执行")
        task = await self._task(access, task_id)
        if task.done:
            raise _task_done()
        instance = (
            await self.db.scalar(
                select(AgentInstance).where(
                    AgentInstance.workspace_id == access.workspace.id,
                    AgentInstance.id == task.assignee_instance_id,
                )
            )
            if task.assignee_instance_id is not None
            else None
        )
        if instance is None:
            raise ApiError(409, "NO_AGENT_ASSIGNEE", "先把执行者设成一个实例")
        if instance.status == "disabled":
            raise ApiError(409, "AGENT_DISABLED", "这个实例已停用")
        message_key = uuid.uuid5(_DISPATCH_NAMESPACE, str(key))
        tried = await self.db.scalar(
            select(ImMessage.id).where(ImMessage.idempotency_key == message_key)
        )
        if tried is None and instance.status != "online":
            raise ApiError(409, "AGENT_OFFLINE", "这个实例现在不在线，消息发不到；等它上线再交给它")
        if await task_runs.open_run(self.db, task.id) is not None:
            raise _run_open()
        text = dispatch_text(task)
        instance_id, instance_name = instance.id, instance.name
        im = ImService(self.db, self.settings, self.redis, self.im)
        conversation = await im.open_conversation(access, instance_id)
        # 发不出去（503 / 409）或结果不确定（504）时抛出，不开段；同一个键重试时 IM 接口会查清结果
        await im.send_message(
            access,
            conversation.conversation_id,
            ImMessageSend(kind="text", text=text, at_im_ids=[]),
            message_key,
            path=f"/api/v1/tasks/{task_id}/runs",
        )
        task = await self._task(access, task_id, lock=True)
        if await task_runs.open_run(self.db, task.id) is not None:
            raise _run_open()
        now = _now()
        run = TaskRun(
            workspace_id=access.workspace.id,
            task_id=task.id,
            executor_kind="instance",
            instance_id=instance_id,
            executor_name=instance_name,
            started_at=now,
            started_by=context.user.id,
            start_key=key,
        )
        self.db.add(run)
        task_runs.mark_started(task, now)
        return await self._commit_start(context, access, run, key, "task.run.dispatched")

    # ---------------------------------------------------------------- 结束

    async def stop(
        self,
        context: Actor,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        run_id: uuid.UUID,
        body: TaskRunStop,
    ) -> TaskRunOut:
        """结束一段；已经结束的原样返回（重试安全）。"""
        run: TaskRun | None = await self.db.scalar(
            select(TaskRun)
            .where(
                TaskRun.id == run_id,
                TaskRun.task_id == task_id,
                TaskRun.workspace_id == access.workspace.id,
            )
            .with_for_update()
        )
        if run is None:
            raise not_found()
        if run.executor_kind == "instance" and not has_role(access.role, Role.admin):
            raise ApiError(403, "FORBIDDEN", "只有 owner / admin 能结束智能体执行的段")
        if run.ended_at is None:
            task_runs.end(run, body.reason, by=context.user.id)
            await self._audit("task.run.stopped", context, access, run)
            await self.db.commit()
        else:
            await self.db.commit()
        return await self._out(run)

    async def assignees(self, access: WorkspaceAccess) -> AssigneeOptions:
        """执行者的候选（实例接口只给 owner，这里给任务编辑器用；时间轴本来也显示这些名字）。"""
        users = (
            await self.db.execute(
                select(User.id, User.display_name)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .where(WorkspaceMember.workspace_id == access.workspace.id)
                .order_by(User.display_name, User.id)
            )
        ).all()
        instances = (
            await self.db.scalars(
                select(AgentInstance)
                .where(
                    AgentInstance.workspace_id == access.workspace.id,
                    AgentInstance.status != "disabled",
                )
                .order_by(AgentInstance.name, AgentInstance.id)
            )
        ).all()
        return AssigneeOptions(
            users=[AssigneeUser(id=uid, display_name=name) for uid, name in users],
            instances=[AssigneeInstance(id=i.id, name=i.name, status=i.status) for i in instances],
        )

    # ---------------------------------------------------------------- 时间轴

    async def timeline(
        self,
        access: WorkspaceAccess,
        *,
        start: datetime,
        end: datetime,
        project_id: uuid.UUID | None,
        group: TimelineGroup,
    ) -> TimelineOut:
        if end <= start or end - start > MAX_RANGE:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "请求参数校验失败",
                details={
                    "errors": [
                        {
                            "loc": ["query", "to"],
                            "msg": "to 要晚于 from，范围最长 31 天",
                            "type": "value_error",
                        }
                    ]
                },
            )
        ws = access.workspace.id
        if project_id is not None:
            exists = await self.db.scalar(
                select(Project.id).where(Project.workspace_id == ws, Project.id == project_id)
            )
            if exists is None:
                raise not_found()
        now = _now()
        project_filter: list[ColumnElement[bool]] = (
            [Task.project_id == project_id] if project_id is not None else []
        )

        run_rows = (
            await self.db.scalars(
                select(TaskRun)
                .join(Task, Task.id == TaskRun.task_id)
                .where(
                    TaskRun.workspace_id == ws,
                    TaskRun.started_at < end,
                    or_(TaskRun.ended_at.is_(None), TaskRun.ended_at > start),
                    *project_filter,
                )
                .order_by(TaskRun.started_at.desc())
                .limit(MAX_TIMELINE_RUNS + 1)
            )
        ).all()
        truncated = len(run_rows) > MAX_TIMELINE_RUNS
        runs = list(run_rows[:MAX_TIMELINE_RUNS])
        first = func.coalesce(Task.planned_start_at, Task.due_at)
        last = func.coalesce(Task.due_at, Task.planned_start_at)
        planned_rows = (
            await self.db.scalars(
                select(Task)
                .where(
                    Task.workspace_id == ws,
                    Task.done.is_(False),
                    Task.started_at.is_(None),
                    first.is_not(None),
                    first < end,
                    last >= start,
                    *project_filter,
                )
                .order_by(first)
                .limit(MAX_TIMELINE_TASKS + 1)
            )
        ).all()
        truncated = truncated or len(planned_rows) > MAX_TIMELINE_TASKS
        planned = list(planned_rows[:MAX_TIMELINE_TASKS])

        task_ids = {r.task_id for r in runs} | {t.id for t in planned}
        rows = (
            (
                await self.db.execute(
                    select(Task, Project.title)
                    .join(Project, Project.id == Task.project_id)
                    .where(Task.id.in_(task_ids))
                )
            ).all()
            if task_ids
            else []
        )
        tasks = {task.id: (task, title) for task, title in rows}
        running_ids = (
            set(
                (
                    await self.db.scalars(
                        select(TaskRun.task_id).where(
                            TaskRun.task_id.in_(task_ids), TaskRun.ended_at.is_(None)
                        )
                    )
                ).all()
            )
            if task_ids
            else set()
        )

        described = {r.id: r for r in await describe_runs(self.db, ws, runs, now)}
        tracks = await self._tracks(access, group, project_id, runs, tasks)
        segments: list[TimelineSegment] = []
        for run in sorted(runs, key=lambda r: (r.started_at, r.id)):
            task, _ = tasks[run.task_id]
            segments.append(
                TimelineSegment(
                    kind="run",
                    task_id=run.task_id,
                    track=_run_track(run, task, group),
                    start=run.started_at,
                    end=run.ended_at,
                    run=described[run.id],
                )
            )
        for task in planned:
            segments.append(
                TimelineSegment(
                    kind="planned",
                    task_id=task.id,
                    track=_task_track(task, group),
                    start=task.planned_start_at,
                    end=task.due_at,
                    run=None,
                )
            )

        usage = await self._usage(ws, start, end, project_id, runs, now)
        summary = await self._summary(ws, start, end, project_filter, now, usage)
        return TimelineOut(
            range_start=start,
            range_end=end,
            now=now,
            group=group,
            project_id=project_id,
            tracks=tracks,
            tasks=[
                TimelineTask(
                    id=task.id,
                    project_id=task.project_id,
                    project_title=title,
                    title=task.title,
                    done=task.done,
                    done_at=task.done_at,
                    due_at=task.due_at,
                    planned_start_at=task.planned_start_at,
                    started_at=task.started_at,
                    assignee_user_id=task.assignee_user_id,
                    assignee_instance_id=task.assignee_instance_id,
                    running=task.id in running_ids,
                )
                for task, title in sorted(tasks.values(), key=lambda pair: pair[0].title)
            ],
            segments=segments,
            usage=usage,
            summary=summary,
            truncated=truncated,
        )

    async def _tracks(
        self,
        access: WorkspaceAccess,
        group: TimelineGroup,
        project_id: uuid.UUID | None,
        runs: Sequence[TaskRun],
        tasks: dict[uuid.UUID, tuple[Task, str]],
    ) -> list[TimelineTrack]:
        if group == "project":
            projects = {task.project_id: title for task, title in tasks.values()}
            return [
                TimelineTrack(key=f"project:{pid}", kind="project", id=pid, name=name, status=None)
                for pid, name in sorted(projects.items(), key=lambda item: item[1])
            ]
        instance_ids = {r.instance_id for r in runs if r.instance_id is not None}
        instance_ids |= {
            t.assignee_instance_id for t, _ in tasks.values() if t.assignee_instance_id
        }
        query = select(AgentInstance).where(AgentInstance.workspace_id == access.workspace.id)
        if project_id is not None:
            query = query.where(AgentInstance.id.in_(instance_ids))
        else:
            # 看全部项目时，没有任务的实例也给一条轨道（停用的除外）
            query = query.where(
                or_(AgentInstance.status != "disabled", AgentInstance.id.in_(instance_ids))
            )
        instances = (
            (await self.db.scalars(query)).all() if (instance_ids or project_id is None) else []
        )
        user_ids = {r.user_id for r in runs if r.user_id is not None}
        user_ids |= {t.assignee_user_id for t, _ in tasks.values() if t.assignee_user_id}
        users = (
            (await self.db.scalars(select(User).where(User.id.in_(user_ids)))).all()
            if user_ids
            else []
        )
        tracks = [
            TimelineTrack(
                key=f"instance:{i.id}", kind="instance", id=i.id, name=i.name, status=i.status
            )
            for i in sorted(instances, key=lambda i: (i.status == "disabled", i.name))
        ]
        tracks += [
            TimelineTrack(
                key=f"user:{u.id}", kind="user", id=u.id, name=u.display_name, status=None
            )
            for u in sorted(users, key=lambda u: u.display_name)
        ]
        former: dict[str, str] = {}
        for run in runs:
            key = _run_track(run, tasks[run.task_id][0], "executor")
            if key.startswith("former:"):
                former[key] = run.executor_name
        tracks += [
            TimelineTrack(key=key, kind="former", id=None, name=name, status=None)
            for key, name in sorted(former.items())
        ]
        if any(
            t.assignee_user_id is None and t.assignee_instance_id is None and t.started_at is None
            for t, _ in tasks.values()
        ):
            tracks.append(
                TimelineTrack(
                    key="unassigned", kind="unassigned", id=None, name="未分配", status=None
                )
            )
        return tracks

    async def _usage(
        self,
        workspace_id: uuid.UUID,
        start: datetime,
        end: datetime,
        project_id: uuid.UUID | None,
        runs: Sequence[TaskRun],
        now: datetime,
    ) -> TimelineUsage:
        step = bucket_minutes(start, end)
        reported = (
            await self.db.scalar(
                select(func.count())
                .select_from(AgentInstance)
                .where(
                    AgentInstance.workspace_id == workspace_id,
                    AgentInstance.usage_reported_at.is_not(None),
                )
            )
            or 0
        ) > 0
        if project_id is None:
            bucket = func.date_bin(timedelta(minutes=step), AgentUsageMinute.minute, _ORIGIN)
            rows = await self.db.execute(
                select(
                    bucket,
                    func.sum(AgentUsageMinute.input),
                    func.sum(AgentUsageMinute.output),
                    func.sum(AgentUsageMinute.cache_read),
                    func.sum(AgentUsageMinute.cache_write),
                )
                .where(
                    AgentUsageMinute.workspace_id == workspace_id,
                    AgentUsageMinute.minute >= start,
                    AgentUsageMinute.minute < end,
                )
                .group_by(bucket)
                .order_by(bucket)
            )
            points = [
                (moment, (int(i or 0), int(o or 0), int(cr or 0), int(cw or 0)))
                for moment, i, o, cr, cw in rows
            ]
            buckets = [
                UsagePoint(start=moment, tokens=token_count(tokens))
                for moment, tokens in points
                if sum(tokens)
            ]
            total = task_runs.ZERO
            for _, tokens in points:
                total = task_runs.add(total, tokens)
            return TimelineUsage(
                scope="workspace",
                bucket_minutes=step,
                reported=reported,
                buckets=buckets,
                total=token_count(total),
            )
        # 按项目：只算这个项目的执行段按时间归属到的 token（平均分时考虑这个实例的所有段）
        instance_runs = [r for r in runs if r.executor_kind == "instance" and r.instance_id]
        instance_ids = {r.instance_id for r in instance_runs if r.instance_id is not None}
        shares: list[tuple[datetime, task_runs.Tokens]] = []
        if instance_ids:
            everyone = {
                r.id: r
                for r in await task_runs.runs_overlapping(
                    self.db, workspace_id, instance_ids, start, end
                )
            }
            everyone.update({r.id: r for r in instance_runs})
            minutes = await task_runs.load_minutes(self.db, instance_ids, start, end)
            attribution = task_runs.attribute(list(everyone.values()), minutes, now)
            for run in instance_runs:
                shares.extend(attribution[run.id].minutes)
        total = task_runs.ZERO
        for _, tokens in shares:
            total = task_runs.add(total, tokens)
        return TimelineUsage(
            scope="project",
            bucket_minutes=step,
            reported=reported,
            buckets=_series(shares, step),
            total=token_count(total),
        )

    async def _summary(
        self,
        workspace_id: uuid.UUID,
        start: datetime,
        end: datetime,
        project_filter: list[ColumnElement[bool]],
        now: datetime,
        usage: TimelineUsage,
    ) -> TimelineSummary:
        running = await self.db.scalar(
            select(func.count())
            .select_from(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.workspace_id == workspace_id, TaskRun.ended_at.is_(None), *project_filter
            )
        )
        done = await self.db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.workspace_id == workspace_id,
                Task.done.is_(True),
                Task.done_at >= start,
                Task.done_at < end,
                *project_filter,
            )
        )
        overdue = await self.db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.workspace_id == workspace_id,
                Task.done.is_(False),
                Task.due_at < now,
                *project_filter,
            )
        )
        return TimelineSummary(
            running=int(running or 0),
            done=int(done or 0),
            tokens=usage.total.total,
            overdue=int(overdue or 0),
        )


def _task_track(task: Task, group: TimelineGroup) -> str:
    if group == "project":
        return f"project:{task.project_id}"
    if task.assignee_instance_id is not None:
        return f"instance:{task.assignee_instance_id}"
    if task.assignee_user_id is not None:
        return f"user:{task.assignee_user_id}"
    return "unassigned"


def _run_track(run: TaskRun, task: Task, group: TimelineGroup) -> str:
    if group == "project":
        return f"project:{task.project_id}"
    if run.executor_kind == "instance" and run.instance_id is not None:
        return f"instance:{run.instance_id}"
    if run.executor_kind == "user" and run.user_id is not None:
        return f"user:{run.user_id}"
    # 成员离开、实例被删除：按开始时记下的名字单独一条轨道
    return f"former:{run.executor_kind}:{run.executor_name}"


# ---------------------------------------------------------------- 用量上报


class UsageService:
    def __init__(self, db: AsyncSession, redis: Redis | None) -> None:
        self.db = db
        self.redis = redis

    async def report(self, instance: AgentInstance, body: UsageReport) -> UsageReportResult:
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 60).hit(
                f"agent_rate:usage:{instance.id}", USAGE_REPORTS_PER_MINUTE
            )
            if retry is not None:
                raise ApiError(
                    429, "RATE_LIMITED", "上报过于频繁", headers={"Retry-After": str(retry)}
                )
        now = _now()
        today = now.date()
        if body.day not in (today, today - timedelta(days=1)):
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "请求参数校验失败",
                details={
                    "errors": [
                        {
                            "loc": ["body", "day"],
                            "msg": "只收今天和昨天（UTC）",
                            "type": "value_error",
                        }
                    ]
                },
            )
        # 同一实例的上报串行处理：锁住实例这一行
        locked = await self.db.scalar(
            select(AgentInstance).where(AgentInstance.id == instance.id).with_for_update()
        )
        if locked is None:  # pragma: no cover - 认证之后实例不会消失
            raise not_found()
        baseline = locked.usage_reported_at is None
        sessions: dict[str, task_runs.Tokens] = {}
        skipped = 0
        for item in body.sessions:
            if item.usage is None:
                skipped += 1
                continue
            u = item.usage
            sessions[item.session] = (u.input, u.output, u.cache_read, u.cache_write)
        previous = await self._cursors(locked.id, body.day, sessions)
        delta = task_runs.ZERO
        for session, tokens in sessions.items():
            old = previous.get(session)
            if old is None:
                increment = task_runs.ZERO if baseline else tokens
            else:
                # 值变小（会话被删除或压缩）：以新值为准，这次记 0
                increment = (
                    max(tokens[0] - old[0], 0),
                    max(tokens[1] - old[1], 0),
                    max(tokens[2] - old[2], 0),
                    max(tokens[3] - old[3], 0),
                )
            delta = task_runs.add(delta, increment)
        if sessions:
            statement = pg_insert(AgentUsageCursor).values(
                [
                    {
                        "instance_id": locked.id,
                        "day": body.day,
                        "session_hash": session,
                        "input": tokens[0],
                        "output": tokens[1],
                        "cache_read": tokens[2],
                        "cache_write": tokens[3],
                        "updated_at": now,
                    }
                    for session, tokens in sorted(sessions.items())
                ]
            )
            await self.db.execute(
                statement.on_conflict_do_update(
                    index_elements=["instance_id", "day", "session_hash"],
                    set_={
                        "input": statement.excluded.input,
                        "output": statement.excluded.output,
                        "cache_read": statement.excluded.cache_read,
                        "cache_write": statement.excluded.cache_write,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )
        if sum(delta):
            minute = _floor(now, 1)
            statement = pg_insert(AgentUsageMinute).values(
                instance_id=locked.id,
                minute=minute,
                workspace_id=locked.workspace_id,
                input=delta[0],
                output=delta[1],
                cache_read=delta[2],
                cache_write=delta[3],
            )
            await self.db.execute(
                statement.on_conflict_do_update(
                    index_elements=["instance_id", "minute"],
                    set_={
                        "input": AgentUsageMinute.input + statement.excluded.input,
                        "output": AgentUsageMinute.output + statement.excluded.output,
                        "cache_read": AgentUsageMinute.cache_read + statement.excluded.cache_read,
                        "cache_write": AgentUsageMinute.cache_write
                        + statement.excluded.cache_write,
                    },
                )
            )
            events.mark(self.db, locked.workspace_id, "timeline")
        locked.usage_reported_at = now
        await self.db.commit()
        return UsageReportResult(
            accepted=len(sessions), skipped=skipped, tokens=sum(delta), baseline=baseline
        )

    async def _cursors(
        self, instance_id: uuid.UUID, day: date, sessions: dict[str, task_runs.Tokens]
    ) -> dict[str, task_runs.Tokens]:
        if not sessions:
            return {}
        rows = await self.db.execute(
            select(
                AgentUsageCursor.session_hash,
                AgentUsageCursor.input,
                AgentUsageCursor.output,
                AgentUsageCursor.cache_read,
                AgentUsageCursor.cache_write,
            ).where(
                AgentUsageCursor.instance_id == instance_id,
                AgentUsageCursor.day == day,
                AgentUsageCursor.session_hash.in_(list(sessions)),
            )
        )
        return {session: (i, o, cr, cw) for session, i, o, cr, cw in rows}


async def purge_usage(db: AsyncSession, now: datetime | None = None) -> tuple[int, int]:
    """Worker 每小时：删掉 90 天前的每分钟用量、3 天前的累计值。调用方提交。"""
    moment = now or _now()
    minutes = await db.execute(
        delete(AgentUsageMinute).where(AgentUsageMinute.minute < moment - USAGE_RETENTION)
    )
    cursors = await db.execute(
        delete(AgentUsageCursor).where(
            AgentUsageCursor.day < moment.date() - timedelta(days=CURSOR_RETENTION_DAYS)
        )
    )
    return int(getattr(minutes, "rowcount", 0) or 0), int(getattr(cursors, "rowcount", 0) or 0)
