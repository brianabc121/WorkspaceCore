"""工作台自己的项目与任务（T-0082，docs/decisions/2026-11-02-remove-vikunja.md）。

- 读写都在工作台数据库里完成，没有外部系统，也就没有“结果未知”。
- 任务的写接口沿用 Sprint 3 的约定：必须带 Idempotency-Key（前端的重试、AI 审批执行都依赖它）。
  这里在**同一个事务**里检查键、执行写入并记下结果：并发的同一个请求在唯一约束上失败，按“仍在处理”回应；
  重复请求原样返回第一次的结果（包括 404 / 409 这类确定的失败）。
- 乐观并发：任务的 version 对外是不透明字符串（内部是整数），项目的 version 是整数。
- 删除项目时它的任务一起删除；收藏指向它们的关联、搜索索引由数据库触发器清理（迁移 0020）。
- 项目的智能体成员与项目群（T-0110）：见 app/domain/project_groups.py。写入提交后立即试一次
  建群 / 同步，失败的由 Worker 每分钟重试。
"""

import base64
import binascii
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import idempotency, project_groups, task_claims, task_runs, task_state
from app.domain.audit import AuditTrail
from app.domain.auth import Actor
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess, has_role, not_found
from app.integrations.openim import OpenIMClient
from app.models import (
    AgentInstance,
    IdempotencyRequest,
    ImGroup,
    Project,
    Task,
    TaskEvent,
    User,
    WorkspaceMember,
)
from app.repositories import integrations as integration_repo
from app.repositories import tasks as repo
from app.schemas.tasks import (
    MAX_PROJECT_AGENTS,
    ProgressCounts,
    ProgressExecutor,
    ProgressFilter,
    ProjectActivityItem,
    ProjectActivityPage,
    ProjectAgentBrief,
    ProjectAgentList,
    ProjectAgentOut,
    ProjectCreate,
    ProjectImGroup,
    ProjectOut,
    ProjectPage,
    ProjectProgressList,
    ProjectProgressOut,
    ProjectSummary,
    ProjectTaskStates,
    ProjectUpdate,
    TaskActiveRun,
    TaskBlock,
    TaskBlocker,
    TaskCreate,
    TaskEventList,
    TaskEventOut,
    TaskOut,
    TaskPage,
    TaskProgressItem,
    TaskProgressOut,
    TaskResultView,
    TaskSort,
    TaskStatusFilter,
    TaskSummary,
    TaskUpdate,
    WorkStateFilter,
)
from app.schemas.workspaces import Role


@dataclass(frozen=True)
class WriteResult:
    status_code: int
    body: dict[str, Any] | None
    replayed: bool = False


def _bad_cursor() -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": ["query", "cursor"], "msg": "游标无效", "type": "value_error"}]
        },
    )


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise _bad_cursor() from None
    offset = data.get("o") if isinstance(data, dict) else None
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1_000_000:
        raise _bad_cursor()
    return offset


def _encode_activity_cursor(created_at: datetime, event_id: uuid.UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "id": str(event_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_activity_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """项目动态的游标：上一页最后一条的 (时间, ID)。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        moment = datetime.fromisoformat(str(data["t"]))
        if moment.tzinfo is None:
            raise ValueError("naive")
        return moment, uuid.UUID(str(data["id"]))
    except (binascii.Error, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise _bad_cursor() from None


def _page_cursor(offset: int, limit: int, fetched: int) -> str | None:
    return encode_cursor(offset + limit) if fetched > limit else None


def _blocker_out(blocker: task_state.Blocker) -> TaskBlocker:
    return TaskBlocker(
        reason=blocker.reason,
        blocked_at=blocker.blocked_at,
        blocked_by=blocker.blocked_by,
        blocked_by_name=blocker.blocked_by_name,
    )


def task_out(task: Task, state: task_state.TaskState | None = None) -> TaskOut:
    """任务的对外形状。state 是当前用户的状态（task_state.load_states）；
    没有用户上下文的地方（例如智能体工具的冲突详情）不传，按任务本身的字段给出保守值。"""
    if state is None:
        state = task_state.TaskState(
            work_state=task_state.classify(task, viewed=task.completion_seq == 0, running=False),
            result_viewed=task.completion_seq == 0,
            active_run=None,
            blocker=task_state.Blocker(
                reason=task.blocked_reason or "",
                blocked_at=task.blocked_at,
                blocked_by=task.blocked_by,
                blocked_by_name=None,
            )
            if task.blocked_at is not None
            else None,
        )
    return TaskOut(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        done=task.done,
        done_at=task.done_at,
        priority=task.priority,
        due_at=task.due_at,
        labels=list(task.labels),
        planned_start_at=task.planned_start_at,
        started_at=task.started_at,
        assignee_user_id=task.assignee_user_id,
        assignee_instance_id=task.assignee_instance_id,
        claim_mode=task.claim_mode,
        claim_scope=task.claim_scope,
        claimable=task_claims.claimable(task),
        work_state=state.work_state,
        completion_seq=task.completion_seq,
        result_viewed=state.result_viewed,
        active_run=TaskActiveRun(
            id=state.active_run.id,
            executor_kind="instance" if state.active_run.executor_kind == "instance" else "user",
            executor_name=state.active_run.executor_name,
            started_at=state.active_run.started_at,
        )
        if state.active_run is not None
        else None,
        blocker=_blocker_out(state.blocker) if state.blocker is not None else None,
        version=str(task.version),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def project_out(
    project: Project,
    counts: tuple[int, int] = (0, 0),
    agents: Sequence[project_groups.Member] = (),
    group: ImGroup | None = None,
    states: dict[str, int] | None = None,
    latest_task: datetime | None = None,
) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        description=project.description,
        archived=project.archived,
        open_tasks=counts[0],
        done_tasks=counts[1],
        task_states=ProjectTaskStates(
            **{key: (states or {}).get(key, 0) for key in task_state.WORK_STATES}
        )
        if states is not None
        else None,
        last_activity_at=max(project.updated_at, latest_task)
        if latest_task is not None
        else project.updated_at,
        agents=[
            ProjectAgentBrief(
                instance_id=m.instance.id,
                name=m.instance.name,
                status=m.instance.status,
            )
            for m in agents
        ],
        im_group=ProjectImGroup.model_validate(project_groups.group_outs(project, group)),
        issue_notices=bool(project.issue_notices),
        version=project.version,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _viewer(access: WorkspaceAccess) -> uuid.UUID:
    """按谁的查看状态判定任务状态：当前用户；没有用户上下文时按“谁都还没查看”。"""
    return access.user_id or task_state.NO_USER


def _admin_only() -> ApiError:
    return ApiError(403, "FORBIDDEN", "只有 owner / admin 能管理项目的智能体和项目群")


def _version_conflict(current: Any, current_version: str | int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current_version, "current": current},
    )


def _in_progress() -> ApiError:
    return ApiError(
        409,
        "IDEMPOTENCY_IN_PROGRESS",
        "同一个请求仍在处理，请稍后再查询",
        headers={"Retry-After": "1"},
    )


def _reused() -> ApiError:
    return ApiError(409, "IDEMPOTENCY_KEY_REUSED", "同一个 Idempotency-Key 已用于不同的请求")


def _bad_assignee(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", field], "msg": message, "type": "value_error"}]},
    )


ASSIGNEE_FIELDS = ("assignee_user_id", "assignee_instance_id")


Write = Callable[[], Awaitable[WriteResult]]


class TaskService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: ConnectorRuntime,
        im: OpenIMClient | None = None,
        redis: Redis | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.im = im
        self.redis = redis
        self.audit = AuditTrail(db, settings)
        # 这次请求里设为自由接取的任务：提交之后通知符合条件的智能体（重放的请求不会再通知）
        self.opened: list[uuid.UUID] = []

    # ---------------------------------------------------------------- 读取

    async def list_projects(
        self,
        access: WorkspaceAccess,
        *,
        include_archived: bool,
        q: str | None,
        cursor: str | None,
        limit: int,
        archived_only: bool = False,
        attention_only: bool = False,
        sort: str = "title",
    ) -> ProjectPage:
        offset = decode_cursor(cursor)
        rows = await repo.list_projects(
            self.db,
            workspace_id=access.workspace.id,
            include_archived=include_archived,
            q=q,
            offset=offset,
            limit=limit + 1,
            archived_only=archived_only,
            attention_for=_viewer(access) if attention_only else None,
            sort=sort,
        )
        page = rows[:limit]
        return ProjectPage(
            items=await self._project_outs(page, _viewer(access)),
            next_cursor=_page_cursor(offset, limit, len(rows)),
            total_estimate=None,
        )

    async def _project_outs(
        self, projects: Sequence[Project], user_id: uuid.UUID | None = None
    ) -> list[ProjectOut]:
        ids = [p.id for p in projects]
        counts = await repo.task_counts_by_project(self.db, ids)
        agents = await project_groups.members(self.db, ids)
        groups = await project_groups.live_groups(self.db, ids)
        states: dict[uuid.UUID, dict[str, int]] | None = None
        latest: dict[uuid.UUID, datetime] = {}
        if user_id is not None:
            states, latest = await repo.project_task_states(self.db, ids, user_id)
        return [
            project_out(
                p,
                counts.get(p.id, (0, 0)),
                agents.get(p.id, []),
                groups.get(p.id),
                states.get(p.id, {}) if states is not None else None,
                latest.get(p.id),
            )
            for p in projects
        ]

    async def get_project(self, access: WorkspaceAccess, project_id: uuid.UUID) -> ProjectOut:
        project = await self._project(access, project_id)
        return (await self._project_outs([project], _viewer(access)))[0]

    async def project_summary(
        self,
        access: WorkspaceAccess,
        *,
        completed_after: datetime,
        completed_before: datetime,
    ) -> ProjectSummary:
        """项目页顶部的三个数（T-0166，规范第 5 节）。"""
        if completed_before <= completed_after:
            raise ApiError(422, "VALIDATION_FAILED", "completed_before 必须晚于 completed_after")
        active, attention, completed = await repo.project_summary(
            self.db,
            access.workspace.id,
            _viewer(access),
            completed_after=completed_after,
            completed_before=completed_before,
        )
        return ProjectSummary(
            active=active, attention=attention, completed=completed, as_of=datetime.now(UTC)
        )

    async def project_activity(
        self,
        access: WorkspaceAccess,
        *,
        project_id: uuid.UUID | None,
        cursor: str | None,
        limit: int,
    ) -> ProjectActivityPage:
        """项目页“最近动态”（T-0166）：任务动态（设为可领取、领取、完成、打回、阻塞……），新的在前。"""
        if project_id is not None:
            await self._project(access, project_id)
        before = _decode_activity_cursor(cursor) if cursor else None
        rows = await repo.project_activity(
            self.db, access.workspace.id, project_id=project_id, before=before, limit=limit + 1
        )
        page = rows[:limit]
        items = [
            ProjectActivityItem(
                id=event.id,
                kind=event.kind,
                actor_kind=event.actor_kind,
                actor_name=event.actor_name,
                note=event.note,
                task_id=event.task_id,
                task_title=task_title,
                project_id=pid,
                project_title=project_title,
                created_at=event.created_at,
            )
            for event, task_title, pid, project_title in page
        ]
        next_cursor = (
            _encode_activity_cursor(page[-1][0].created_at, page[-1][0].id)
            if len(rows) > limit and page
            else None
        )
        return ProjectActivityPage(items=items, next_cursor=next_cursor)

    async def project_progress(self, access: WorkspaceAccess, *, limit: int) -> ProjectProgressList:
        rows = await repo.project_progress(self.db, access.workspace.id, limit=limit)
        return ProjectProgressList(
            items=[
                ProjectProgressOut(project_id=row.id, title=row.title, open_tasks=o, done_tasks=d)
                for row, o, d in rows
            ]
        )

    async def list_tasks(
        self,
        access: WorkspaceAccess,
        *,
        status: TaskStatusFilter,
        project_id: uuid.UUID | None,
        priorities: Sequence[int] | None,
        due_before: datetime | None,
        due_after: datetime | None,
        q: str | None,
        sort: TaskSort,
        cursor: str | None,
        limit: int,
        assignee_instance_id: uuid.UUID | None = None,
        claimable_for: uuid.UUID | None = None,
        assignee_user_id: uuid.UUID | None = None,
        work_state: WorkStateFilter | None = None,
    ) -> TaskPage:
        offset = decode_cursor(cursor)
        rows = await repo.list_tasks(
            self.db,
            workspace_id=access.workspace.id,
            status=status,
            project_id=project_id,
            priorities=priorities,
            due_before=due_before,
            due_after=due_after,
            q=q,
            sort=sort,
            offset=offset,
            limit=limit + 1,
            assignee_instance_id=assignee_instance_id,
            claimable_for=claimable_for,
            assignee_user_id=assignee_user_id,
            work_state=work_state,
            user_id=_viewer(access),
        )
        return TaskPage(
            items=await self._outs(rows[:limit], _viewer(access)),
            next_cursor=_page_cursor(offset, limit, len(rows)),
            total_estimate=None,
        )

    async def _outs(self, tasks: Sequence[Task], user_id: uuid.UUID) -> list[TaskOut]:
        """一页任务及它们对这个用户的状态（固定三次查询，不随条数增加）。"""
        states = await task_state.load_states(self.db, tasks, user_id)
        return [task_out(task, states[task.id]) for task in tasks]

    async def _out(self, task: Task, user_id: uuid.UUID) -> TaskOut:
        return (await self._outs([task], user_id))[0]

    async def get_task(self, access: WorkspaceAccess, task_id: uuid.UUID) -> TaskOut:
        return await self._out(await self._task(access, task_id), _viewer(access))

    async def summary(
        self,
        access: WorkspaceAccess,
        *,
        project_id: uuid.UUID | None,
        priorities: Sequence[int] | None,
        due_before: datetime | None,
        due_after: datetime | None,
        q: str | None,
        assignee_user_id: uuid.UUID | None = None,
        assignee_instance_id: uuid.UUID | None = None,
    ) -> TaskSummary:
        """任务页筛选标签的计数（T-0166）：与列表同样的筛选，不套用所选状态。"""
        conditions = repo.task_conditions(
            workspace_id=access.workspace.id,
            project_id=project_id,
            priorities=priorities,
            due_before=due_before,
            due_after=due_after,
            q=q,
            assignee_user_id=assignee_user_id,
            assignee_instance_id=assignee_instance_id,
        )
        counts = await repo.state_counts(self.db, conditions, _viewer(access))
        values = {key: counts.get(key, 0) for key in task_state.WORK_STATES}
        return TaskSummary(**values, total=sum(values.values()), as_of=datetime.now(UTC))

    async def progress(
        self, access: WorkspaceAccess, *, status: ProgressFilter, limit: int
    ) -> TaskProgressOut:
        """首页“任务进展”（T-0163；T-0166 起与任务页同一判定）：四种状态的数量与一页条目。"""
        counts = await repo.progress_counts(self.db, access.workspace.id, _viewer(access))
        rows = await repo.progress_items(
            self.db, access.workspace.id, _viewer(access), status=status, limit=limit
        )
        states = await task_state.load_states(self.db, [row[0] for row in rows], _viewer(access))
        items: list[TaskProgressItem] = []
        for task, project_title, state, user_name, instance_name in rows:
            executor: ProgressExecutor | None = None
            if task.assignee_user_id is not None:
                executor = ProgressExecutor(
                    kind="user", id=task.assignee_user_id, name=user_name or ""
                )
            elif task.assignee_instance_id is not None:
                executor = ProgressExecutor(
                    kind="instance", id=task.assignee_instance_id, name=instance_name or ""
                )
            blocker = states[task.id].blocker
            items.append(
                TaskProgressItem(
                    id=task.id,
                    title=task.title,
                    project_id=task.project_id,
                    project_title=project_title,
                    status=state,
                    executor=executor,
                    due_at=task.due_at,
                    updated_at=task.updated_at,
                    blocker=_blocker_out(blocker) if blocker is not None else None,
                )
            )
        return TaskProgressOut(
            counts=ProgressCounts(all=sum(counts.values()), **counts),
            attention=counts["awaiting_view"] + counts["blocked"],
            items=items,
        )

    async def _task(
        self, access: WorkspaceAccess, task_id: uuid.UUID, *, lock: bool = False
    ) -> Task:
        task = await repo.get_task(self.db, access.workspace.id, task_id, lock=lock)
        if task is None:
            raise not_found()
        return task

    async def _project(
        self, access: WorkspaceAccess, project_id: uuid.UUID, *, lock: bool = False
    ) -> Project:
        project = await repo.get_project(self.db, access.workspace.id, project_id, lock=lock)
        if project is None:
            raise not_found()
        return project

    async def _audit(
        self,
        action: str,
        context: Actor,
        access: WorkspaceAccess,
        resource_type: str,
        resource_id: uuid.UUID,
        metadata: dict[str, Any],
    ) -> None:
        await self.audit.record(
            action,
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            client_ip=context.client_ip,
        )

    # ---------------------------------------------------------------- 幂等（任务写接口）

    async def _idempotent(
        self,
        context: Actor,
        access: WorkspaceAccess,
        key: uuid.UUID,
        *,
        method: str,
        path: str,
        body: Any,
        write: Write,
    ) -> WriteResult:
        fingerprint = idempotency.fingerprint(access.workspace.id, method, path, body)
        now = datetime.now(UTC)
        record = await integration_repo.get_idempotency(
            self.db, actor_id=context.user.id, key=key, lock=True
        )
        if record is not None and record.expires_at > now:
            if record.request_fingerprint != fingerprint:
                await self.db.rollback()
                raise _reused()
            if record.status in {"succeeded", "failed"}:
                await self.db.commit()
                status_code, stored = idempotency.replay(record)
                return WriteResult(status_code, stored, replayed=True)
        if record is not None:
            # 过期的记录，或 T-0082 之前留下的未完成记录（Vikunja 写入结果未知）：
            # 作废，按新请求执行
            await integration_repo.delete_idempotency(self.db, record.id)
        record = IdempotencyRequest(
            workspace_id=access.workspace.id,
            actor_id=context.user.id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status="processing",
            context_json={"op": method.lower(), "started_at": now.isoformat()},
            expires_at=now + idempotency.RETENTION,
        )
        try:
            # 写入放在保存点里：失败时只撤销这次写入，
            # 不让会话里已加载的对象（调用方的）失效
            async with self.db.begin_nested():
                result = await write()
        except ApiError as error:
            if error.status_code not in {404, 409}:
                raise
            # 确定的失败（对象不存在、版本冲突）：记下来，重放时返回同样的错误
            idempotency.fail(record, error)
            self.db.add(record)
            await self._commit_record()
            raise
        idempotency.succeed(record, result.status_code, result.body)
        self.db.add(record)
        await self._commit_record()
        return result

    async def _commit_record(self) -> None:
        try:
            await self.db.commit()
        except IntegrityError:
            # 并发的同一个请求先完成了：这一个的写入随事务回滚
            await self.db.rollback()
            raise _in_progress() from None

    # ---------------------------------------------------------------- 任务写入

    async def create_task(
        self, context: Actor, access: WorkspaceAccess, key: uuid.UUID, body: TaskCreate
    ) -> WriteResult:
        async def write() -> WriteResult:
            await self._project(access, body.project_id)
            if body.assignee_user_id is not None:
                await self._check_member(access, body.assignee_user_id)
            task = Task(
                workspace_id=access.workspace.id,
                project_id=body.project_id,
                title=body.title,
                description=body.description,
                priority=body.priority,
                due_at=body.due_at,
                labels=body.labels,
                assignee_user_id=body.assignee_user_id,
                created_by=context.user.id,
            )
            self.db.add(task)
            await self.db.flush()
            await self.db.refresh(task)
            await self._audit("task.created", context, access, "task", task.id, {})
            out = await self._out(task, context.user.id)
            return WriteResult(201, out.model_dump(mode="json"))

        return await self._idempotent(
            context,
            access,
            key,
            method="POST",
            path="/api/v1/tasks",
            body=body.model_dump(mode="json"),
            write=write,
        )

    async def update_task(
        self,
        context: Actor,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        key: uuid.UUID,
        body: TaskUpdate,
        *,
        self_viewed: bool = True,
    ) -> WriteResult:
        """修改任务。self_viewed：本人手动完成时同时把这一轮记为本人已查看（T-0166）；
        智能体的完成经审批执行时传 False，审批人仍会看到“待查看”。"""

        async def write() -> WriteResult:
            task = await self._task(access, task_id, lock=True)
            if str(task.version) != body.version:
                current = (await self._out(task, context.user.id)).model_dump(mode="json")
                raise _version_conflict(current, str(task.version))
            fields = sorted(body.model_fields_set - {"version"})
            claim_fields = {"claim_mode", "claim_scope"} & set(fields)
            if claim_fields and not has_role(access.role, Role.admin):
                raise ApiError(403, "FORBIDDEN", "只有 owner / admin 能设置任务的接取方式")
            if body.project_id is not None and body.project_id != task.project_id:
                await self._project(access, body.project_id)
            before = (task.assignee_user_id, task.assignee_instance_id)
            was_open = task.claim_mode == "open"
            await self._check_assignee(access, body)
            now = datetime.now(UTC)
            done_change: bool | None = None
            for name in fields:
                value = getattr(body, name)
                if name == "done":
                    if value != task.done:
                        done_change = bool(value)
                    continue
                setattr(task, name, value)
            if done_change is True:
                # 完成：新的一轮、清除阻塞、结束正在执行的一段、记“完成”动态（T-0166：
                # 人与智能体的完成入口都这样做；智能体的任务经审批完成时也走这里）
                task_state.mark_done(task, now)
                await task_runs.end_open_run(self.db, task, "done", by=context.user.id, at=now)
                self.db.add(await self._user_event(task, "completed", context.user.id))
            elif done_change is False:
                task_state.mark_reopened(task)
                self.db.add(await self._user_event(task, "reopened", context.user.id))
            # 执行者二选一：设了一个，另一个清除
            if body.assignee_user_id is not None:
                task.assignee_instance_id = None
            if body.assignee_instance_id is not None:
                task.assignee_user_id = None
            if (task.assignee_user_id, task.assignee_instance_id) != before:
                # 改了执行者：原执行者正在执行的一段记为中断
                await task_runs.end_open_run(
                    self.db, task, "interrupted", by=context.user.id, at=now
                )
            await self._claim_mode(context, task, body, was_open=was_open)
            task.version += 1
            await self.db.flush()
            await self.db.refresh(task)
            if done_change is True and self_viewed:
                await task_state.mark_viewed(self.db, task, context.user.id)
            await self._audit("task.updated", context, access, "task", task.id, {"fields": fields})
            out = await self._out(task, context.user.id)
            return WriteResult(200, out.model_dump(mode="json"))

        return await self._idempotent(
            context,
            access,
            key,
            method="PATCH",
            path=f"/api/v1/tasks/{task_id}",
            body=body.model_dump(mode="json", exclude_unset=True),
            write=write,
        )

    async def _user_event(
        self, task: Task, kind: str, user_id: uuid.UUID, note: str = ""
    ) -> TaskEvent:
        user = await self.db.get(User, user_id)
        return task_claims.event(
            task,
            kind,
            actor_kind="user",
            name=user.display_name if user is not None else "",
            user_id=user_id,
            note=note,
        )

    async def block_task(
        self,
        context: Actor,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        key: uuid.UUID,
        body: TaskBlock,
    ) -> WriteResult:
        """标记 / 解除阻塞（T-0166，member）。标记不会停止正在执行的一段，解除也不会重新开始；
        已完成的任务不能标阻塞（409 TASK_DONE）。状态没变时原样返回（不加 version）。"""

        async def write() -> WriteResult:
            task = await self._task(access, task_id, lock=True)
            if str(task.version) != body.version:
                current = (await self._out(task, context.user.id)).model_dump(mode="json")
                raise _version_conflict(current, str(task.version))
            if body.blocked and task.done:
                raise ApiError(409, "TASK_DONE", "已完成的任务不能标记阻塞")
            now = datetime.now(UTC)
            changed = False
            if body.blocked and (task.blocked_at is None or task.blocked_reason != body.reason):
                task_state.set_block(task, body.reason, context.user.id, now)
                self.db.add(await self._user_event(task, "blocked", context.user.id, body.reason))
                changed = True
            elif not body.blocked and task.blocked_at is not None:
                task_state.clear_block(task)
                self.db.add(await self._user_event(task, "unblocked", context.user.id))
                changed = True
            if changed:
                task.version += 1
                await self.db.flush()
                await self.db.refresh(task)
                await self._audit(
                    "task.blocked" if body.blocked else "task.unblocked",
                    context,
                    access,
                    "task",
                    task.id,
                    {},
                )
            out = await self._out(task, context.user.id)
            return WriteResult(200, out.model_dump(mode="json"))

        return await self._idempotent(
            context,
            access,
            key,
            method="PATCH",
            path=f"/api/v1/tasks/{task_id}/block",
            body=body.model_dump(mode="json"),
            write=write,
        )

    async def view_result(
        self, access: WorkspaceAccess, task_id: uuid.UUID, body: TaskResultView
    ) -> TaskOut:
        """标为已查看（T-0166，viewer 也可以）：只标当前用户、当前这一轮。

        轮次已变化（重新打开、又完成了一次）或任务现在没完成时返回 409 COMPLETION_CHANGED，
        details.current 是最新的任务，不会把新结果误标为已查看。重复调用同一轮是幂等的。
        """
        if access.user_id is None:
            raise ApiError(403, "FORBIDDEN", "只有成员本人能标记已查看")
        task = await self._task(access, task_id, lock=True)
        if not task.done or task.completion_seq != body.completion_seq:
            current = (await self._out(task, _viewer(access))).model_dump(mode="json")
            await self.db.rollback()
            raise ApiError(
                409,
                "COMPLETION_CHANGED",
                "任务的完成结果已经变化，请看最新结果后再标记",
                details={"current": current},
            )
        await task_state.mark_viewed(self.db, task, access.user_id)
        await self.db.flush()
        out = await self._out(task, _viewer(access))
        await self.db.commit()
        return out

    async def _claim_mode(
        self, context: Actor, task: Task, body: TaskUpdate, *, was_open: bool
    ) -> None:
        """接取方式（T-0111）：设了执行者就回到指派；设为自由接取时任务必须没有执行者、没有完成。"""
        assigned = body.assignee_user_id is not None or body.assignee_instance_id is not None
        if assigned and body.claim_mode != "open":
            task.claim_mode = "assigned"
        if task.claim_mode != "open":
            return
        if task.done or task.assignee_user_id is not None or task.assignee_instance_id is not None:
            raise ApiError(
                409,
                "TASK_NOT_OPENABLE",
                "已完成或已有执行者的任务不能设为自由接取：先把执行者改回未分配",
            )
        if not was_open:
            task.claim_opened_by = context.user.id
            user = await self.db.get(User, context.user.id)
            self.db.add(
                task_claims.event(
                    task,
                    "opened",
                    actor_kind="user",
                    name=user.display_name if user is not None else "",
                    user_id=context.user.id,
                )
            )
            self.opened.append(task.id)

    async def announce_opened(self) -> None:
        """提交之后：通知符合条件的智能体有新的可领取任务（尽力而为）。"""
        for task_id in self.opened:
            await task_claims.announce(self.db, self.settings, self.redis, self.im, task_id)
        self.opened.clear()

    async def list_events(self, access: WorkspaceAccess, task_id: uuid.UUID) -> TaskEventList:
        task = await self._task(access, task_id)
        rows = (
            await self.db.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task.id)
                .order_by(TaskEvent.created_at.desc(), TaskEvent.id)
                .limit(100)
            )
        ).all()
        return TaskEventList(
            items=[
                TaskEventOut(
                    id=r.id,
                    kind=r.kind,
                    actor_kind=r.actor_kind,
                    actor_name=r.actor_name,
                    note=r.note,
                    created_at=r.created_at,
                )
                for r in rows
            ]
        )

    async def _check_member(self, access: WorkspaceAccess, user_id: uuid.UUID) -> None:
        member = await self.db.scalar(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == access.workspace.id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if member is None:
            raise _bad_assignee("assignee_user_id", "不是本工作空间的成员")

    async def _check_assignee(self, access: WorkspaceAccess, body: TaskUpdate) -> None:
        """执行者必须是本工作空间的成员，或本工作空间里没有停用的实例。"""
        if body.assignee_user_id is not None:
            await self._check_member(access, body.assignee_user_id)
        if body.assignee_instance_id is not None:
            instance = await self.db.scalar(
                select(AgentInstance).where(
                    AgentInstance.workspace_id == access.workspace.id,
                    AgentInstance.id == body.assignee_instance_id,
                )
            )
            if instance is None:
                raise _bad_assignee("assignee_instance_id", "不是本工作空间的实例")
            if instance.status == "disabled":
                raise _bad_assignee("assignee_instance_id", "这个实例已停用")

    async def delete_task(
        self,
        context: Actor,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        key: uuid.UUID,
        version: str,
    ) -> WriteResult:
        async def write() -> WriteResult:
            task = await repo.get_task(self.db, access.workspace.id, task_id, lock=True)
            if task is None:
                return WriteResult(204, None)  # 已经不存在：删除是幂等的
            if str(task.version) != version:
                current = (await self._out(task, context.user.id)).model_dump(mode="json")
                raise _version_conflict(current, str(task.version))
            # 任务的 README 进回收站（T-0122，方案 9.2）
            from app.domain.readme import Readmes

            await Readmes(self.db, self.settings).trash_task(task.id)
            await self.db.delete(task)
            await self._audit("task.deleted", context, access, "task", task_id, {})
            return WriteResult(204, None)

        return await self._idempotent(
            context,
            access,
            key,
            method="DELETE",
            path=f"/api/v1/tasks/{task_id}",
            body={"version": version},
            write=write,
        )

    # ---------------------------------------------------------------- 项目写入

    async def create_project(
        self, context: Actor, access: WorkspaceAccess, body: ProjectCreate
    ) -> ProjectOut:
        if (body.instance_ids or body.im_group) and not has_role(access.role, Role.admin):
            raise _admin_only()
        instances = await self._agents(access, body.instance_ids)
        project = Project(
            workspace_id=access.workspace.id,
            title=body.title,
            description=body.description,
            created_by=context.user.id,
        )
        self.db.add(project)
        await self.db.flush()
        await project_groups.add_agents(
            self.db, project, [i.id for i in instances], added_by=context.user.id
        )
        wants_group = body.im_group and self.settings.openim_enabled
        if wants_group:
            project_groups.request_group(project, context.user.id)
        await self._audit(
            "project.created",
            context,
            access,
            "project",
            project.id,
            {"agents": len(instances), "im_group": wants_group},
        )
        await self.db.commit()
        project_id = project.id
        await self.sync_group(project_id)
        return await self.get_project(access, project_id)

    async def _agents(
        self, access: WorkspaceAccess, instance_ids: Sequence[uuid.UUID]
    ) -> list[AgentInstance]:
        """本工作空间、没有停用的实例；不是的返回 422。"""
        if not instance_ids:
            return []
        found = {
            i.id: i
            for i in await self.db.scalars(
                select(AgentInstance).where(
                    AgentInstance.workspace_id == access.workspace.id,
                    AgentInstance.id.in_(instance_ids),
                )
            )
        }
        bad = [str(i) for i in instance_ids if i not in found or found[i].status == "disabled"]
        if bad:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "有实例不存在或已停用",
                details={
                    "errors": [
                        {
                            "loc": ["body", "instance_ids"],
                            "msg": ",".join(bad),
                            "type": "value_error",
                        }
                    ]
                },
            )
        return [found[i] for i in instance_ids]

    async def sync_group(self, project_id: uuid.UUID) -> None:
        """写入提交之后立即试一次建群 / 同步；失败的（以及没有 IM 客户端时）留给 Worker。"""
        if self.im is None:
            return
        await project_groups.ProjectGroupSync(self.db, self.settings, self.im).sync(project_id)

    async def update_project(
        self,
        context: Actor,
        access: WorkspaceAccess,
        project_id: uuid.UUID,
        body: ProjectUpdate,
    ) -> ProjectOut:
        project = await self._project(access, project_id, lock=True)
        if project.version != body.version:
            counts = await repo.task_counts_by_project(self.db, [project.id])
            current = project_out(project, counts.get(project.id, (0, 0))).model_dump(mode="json")
            raise _version_conflict(current, project.version)
        fields = sorted(body.model_fields_set - {"version"})
        for name in fields:
            setattr(project, name, getattr(body, name))
        project.version += 1
        # 改名、改说明、归档：项目群的群名与公告随之更新
        project_groups.mark_dirty(project)
        await self._audit(
            "project.updated", context, access, "project", project.id, {"fields": fields}
        )
        await self.db.commit()
        await self.sync_group(project_id)
        return await self.get_project(access, project_id)

    async def delete_project(
        self, context: Actor, access: WorkspaceAccess, project_id: uuid.UUID, version: int
    ) -> None:
        project = await self._project(access, project_id, lock=True)
        if project.version != version:
            counts = await repo.task_counts_by_project(self.db, [project.id])
            current = project_out(project, counts.get(project.id, (0, 0))).model_dump(mode="json")
            raise _version_conflict(current, project.version)
        counts = await repo.task_counts_by_project(self.db, [project.id])
        open_tasks, done_tasks = counts.get(project.id, (0, 0))
        await project_groups.dismiss_for_project(self.db, self.im, project)
        await self.db.delete(project)  # 任务由外键级联删除
        await self._audit(
            "project.deleted",
            context,
            access,
            "project",
            project_id,
            {"tasks_deleted": open_tasks + done_tasks},
        )
        await self.db.commit()

    # ---------------------------------------------------------------- 项目的智能体成员与项目群

    async def list_project_agents(
        self, access: WorkspaceAccess, project_id: uuid.UUID
    ) -> ProjectAgentList:
        project = await self._project(access, project_id)
        found = (await project_groups.members(self.db, [project.id]))[project.id]
        group = (await project_groups.live_groups(self.db, [project.id])).get(project.id)
        in_group = (
            (await project_groups.group_instance_ids(self.db, [group.id]))[group.id]
            if group is not None
            else set()
        )
        return ProjectAgentList(
            items=[
                ProjectAgentOut(
                    instance_id=m.instance.id,
                    name=m.instance.name,
                    description=m.instance.description,
                    status=m.instance.status,
                    access=m.access,
                    in_group=m.instance.id in in_group,
                )
                for m in found
            ],
            im_group=ProjectImGroup.model_validate(project_groups.group_outs(project, group)),
        )

    async def add_project_agent(
        self,
        context: Actor,
        access: WorkspaceAccess,
        project_id: uuid.UUID,
        instance_id: uuid.UUID,
    ) -> ProjectAgentList:
        if not has_role(access.role, Role.admin):
            raise _admin_only()
        project = await self._project(access, project_id, lock=True)
        instance = (await self._agents(access, [instance_id]))[0]
        if await project_groups.member_count(self.db, project.id) >= MAX_PROJECT_AGENTS:
            raise ApiError(422, "VALIDATION_FAILED", f"一个项目最多 {MAX_PROJECT_AGENTS} 个智能体")
        added = await project_groups.add_agents(
            self.db, project, [instance.id], added_by=context.user.id
        )
        if added:
            await self._audit(
                "project.agent_added",
                context,
                access,
                "project",
                project.id,
                {"instance_id": str(instance.id)},
            )
        await self.db.commit()
        await self.sync_group(project_id)
        return await self.list_project_agents(access, project_id)

    async def remove_project_agent(
        self,
        context: Actor,
        access: WorkspaceAccess,
        project_id: uuid.UUID,
        instance_id: uuid.UUID,
    ) -> ProjectAgentList:
        """移出项目：不再能读写项目知识库，也移出项目群；它已经领取的任务不受影响。"""
        if not has_role(access.role, Role.admin):
            raise _admin_only()
        project = await self._project(access, project_id, lock=True)
        if await project_groups.remove_agent(self.db, project, instance_id):
            await self._audit(
                "project.agent_removed",
                context,
                access,
                "project",
                project.id,
                {"instance_id": str(instance_id)},
            )
        await self.db.commit()
        await self.sync_group(project_id)
        return await self.list_project_agents(access, project_id)

    async def request_project_group(
        self, context: Actor, access: WorkspaceAccess, project_id: uuid.UUID
    ) -> ProjectOut:
        """建项目群（旧项目补建）或立即重试；已建好的重新同步一次。"""
        if not has_role(access.role, Role.admin):
            raise _admin_only()
        if not self.settings.openim_enabled:
            raise ApiError(409, "IM_NOT_CONFIGURED", "没有部署 OpenIM，不能建项目群")
        project = await self._project(access, project_id, lock=True)
        first = project.im_group_status == "none"
        project_groups.request_group(project, context.user.id)
        if first:
            await self._audit(
                "project.im_group_requested", context, access, "project", project.id, {}
            )
        await self.db.commit()
        await self.sync_group(project_id)
        return await self.get_project(access, project_id)
