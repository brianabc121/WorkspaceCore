"""工作台自己的项目与任务（T-0082，docs/decisions/2026-11-02-remove-vikunja.md）。

- 读写都在工作台数据库里完成，没有外部系统，也就没有“结果未知”。
- 任务的写接口沿用 Sprint 3 的约定：必须带 Idempotency-Key（前端的重试、AI 审批执行都依赖它）。
  这里在**同一个事务**里检查键、执行写入并记下结果：并发的同一个请求在唯一约束上失败，按“仍在处理”回应；
  重复请求原样返回第一次的结果（包括 404 / 409 这类确定的失败）。
- 乐观并发：任务的 version 对外是不透明字符串（内部是整数），项目的 version 是整数。
- 删除项目时它的任务一起删除；收藏指向它们的关联、搜索索引由数据库触发器清理（迁移 0020）。
"""

import base64
import binascii
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import idempotency, task_runs
from app.domain.audit import AuditTrail
from app.domain.auth import Actor
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess, not_found
from app.models import AgentInstance, IdempotencyRequest, Project, Task, WorkspaceMember
from app.repositories import integrations as integration_repo
from app.repositories import tasks as repo
from app.schemas.tasks import (
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectProgressList,
    ProjectProgressOut,
    ProjectUpdate,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskSort,
    TaskStatusFilter,
    TaskUpdate,
)


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


def _page_cursor(offset: int, limit: int, fetched: int) -> str | None:
    return encode_cursor(offset + limit) if fetched > limit else None


def task_out(task: Task) -> TaskOut:
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
        version=str(task.version),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def project_out(project: Project, counts: tuple[int, int] = (0, 0)) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        description=project.description,
        archived=project.archived,
        open_tasks=counts[0],
        done_tasks=counts[1],
        version=project.version,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


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
    def __init__(self, db: AsyncSession, settings: Settings, runtime: ConnectorRuntime) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 读取

    async def list_projects(
        self,
        access: WorkspaceAccess,
        *,
        include_archived: bool,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> ProjectPage:
        offset = decode_cursor(cursor)
        rows = await repo.list_projects(
            self.db,
            workspace_id=access.workspace.id,
            include_archived=include_archived,
            q=q,
            offset=offset,
            limit=limit + 1,
        )
        page = rows[:limit]
        counts = await repo.task_counts_by_project(self.db, (p.id for p in page))
        return ProjectPage(
            items=[project_out(p, counts.get(p.id, (0, 0))) for p in page],
            next_cursor=_page_cursor(offset, limit, len(rows)),
            total_estimate=None,
        )

    async def get_project(self, access: WorkspaceAccess, project_id: uuid.UUID) -> ProjectOut:
        project = await self._project(access, project_id)
        counts = await repo.task_counts_by_project(self.db, [project.id])
        return project_out(project, counts.get(project.id, (0, 0)))

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
        )
        return TaskPage(
            items=[task_out(row) for row in rows[:limit]],
            next_cursor=_page_cursor(offset, limit, len(rows)),
            total_estimate=None,
        )

    async def get_task(self, access: WorkspaceAccess, task_id: uuid.UUID) -> TaskOut:
        return task_out(await self._task(access, task_id))

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
            task = Task(
                workspace_id=access.workspace.id,
                project_id=body.project_id,
                title=body.title,
                description=body.description,
                priority=body.priority,
                due_at=body.due_at,
                labels=body.labels,
                created_by=context.user.id,
            )
            self.db.add(task)
            await self.db.flush()
            await self.db.refresh(task)
            await self._audit("task.created", context, access, "task", task.id, {})
            return WriteResult(201, task_out(task).model_dump(mode="json"))

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
    ) -> WriteResult:
        async def write() -> WriteResult:
            task = await self._task(access, task_id, lock=True)
            if str(task.version) != body.version:
                current = task_out(task).model_dump(mode="json")
                raise _version_conflict(current, str(task.version))
            fields = sorted(body.model_fields_set - {"version"})
            if body.project_id is not None and body.project_id != task.project_id:
                await self._project(access, body.project_id)
            before = (task.assignee_user_id, task.assignee_instance_id)
            await self._check_assignee(access, body)
            now = datetime.now(UTC)
            for name in fields:
                value = getattr(body, name)
                if name == "done" and value != task.done:
                    task.done_at = now if value else None
                    if value:
                        # 完成任务：正在执行的一段随之结束（智能体的任务经审批完成时也走这里）
                        await task_runs.end_open_run(
                            self.db, task, "done", by=context.user.id, at=now
                        )
                setattr(task, name, value)
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
            task.version += 1
            await self.db.flush()
            await self.db.refresh(task)
            await self._audit("task.updated", context, access, "task", task.id, {"fields": fields})
            return WriteResult(200, task_out(task).model_dump(mode="json"))

        return await self._idempotent(
            context,
            access,
            key,
            method="PATCH",
            path=f"/api/v1/tasks/{task_id}",
            body=body.model_dump(mode="json", exclude_unset=True),
            write=write,
        )

    async def _check_assignee(self, access: WorkspaceAccess, body: TaskUpdate) -> None:
        """执行者必须是本工作空间的成员，或本工作空间里没有停用的实例。"""
        if body.assignee_user_id is not None:
            member = await self.db.scalar(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == access.workspace.id,
                    WorkspaceMember.user_id == body.assignee_user_id,
                )
            )
            if member is None:
                raise _bad_assignee("assignee_user_id", "不是本工作空间的成员")
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
                current = task_out(task).model_dump(mode="json")
                raise _version_conflict(current, str(task.version))
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
        project = Project(
            workspace_id=access.workspace.id,
            title=body.title,
            description=body.description,
            created_by=context.user.id,
        )
        self.db.add(project)
        await self.db.flush()
        await self._audit("project.created", context, access, "project", project.id, {})
        await self.db.commit()
        await self.db.refresh(project)
        return project_out(project)

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
        await self._audit(
            "project.updated", context, access, "project", project.id, {"fields": fields}
        )
        await self.db.commit()
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
