"""外部项目与任务（Sprint 3 契约第 5 ~ 8 节）。

- 读：本地投影 + `sync` 新鲜度，不访问 Vikunja。
- 写：先登记幂等记录（第 7 节），再调用 Vikunja，成功后更新投影；修改 / 删除前读取 Vikunja 上的
  当前任务比较 version（第 6.3 节：Vikunja 写入不校验 If-Match）。
- 结果未知（写请求已发出但超时 / 中断，或对方返回 5xx）：记录 unknown；用同一个键重发时先查询结果，
  绝不盲目重建（规范 4.1）。
"""

import base64
import binascii
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import idempotency, notifications, projection
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.connectors import (
    ACTIVE_STATUSES,
    ConnectorRuntime,
    api_error,
    integration_status_after,
    sync_state,
)
from app.domain.policy import WorkspaceAccess, not_found
from app.integrations.base import ConnectorError
from app.integrations.vikunja import PROVIDER, TaskConnector, VikunjaTask
from app.models import ExternalResource, IdempotencyRequest, Integration
from app.repositories import integrations as integration_repo
from app.repositories import projection as repo
from app.schemas.integrations import SyncState
from app.schemas.tasks import (
    ProjectPage,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskSort,
    TaskStatusFilter,
    TaskUpdate,
)

logger = logging.getLogger("app.tasks")

# 查询结果未知的创建时，把“发出时间”往前放宽一点，抵消两边时钟的偏差
_CLOCK_SKEW = timedelta(seconds=30)


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


class TaskService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: ConnectorRuntime) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 读取（投影）

    async def _integration(self, access: WorkspaceAccess) -> Integration | None:
        return await integration_repo.get_provider(self.db, access.workspace.id, PROVIDER)

    async def sync(self, access: WorkspaceAccess) -> SyncState:
        integration = await self._integration(access)
        cursor = (
            await integration_repo.get_cursor(self.db, integration.id, repo.TASK)
            if integration
            else None
        )
        return sync_state(
            integration, cursor, stale_after_seconds=self.settings.sync_stale_after_seconds
        )

    async def list_projects(
        self,
        access: WorkspaceAccess,
        *,
        integration_id: uuid.UUID | None,
        include_archived: bool,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> ProjectPage:
        offset = decode_cursor(cursor)
        rows = await repo.list_projects(
            self.db,
            workspace_id=access.workspace.id,
            integration_id=integration_id,
            include_archived=include_archived,
            q=q,
            offset=offset,
            limit=limit + 1,
        )
        return ProjectPage(
            items=[projection.project_out(row) for row in rows[:limit]],
            next_cursor=_page_cursor(offset, limit, len(rows)),
            total_estimate=None,
            sync=await self.sync(access),
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
            items=[projection.task_out(row) for row in rows[:limit]],
            next_cursor=_page_cursor(offset, limit, len(rows)),
            total_estimate=None,
            sync=await self.sync(access),
        )

    async def get_task(self, access: WorkspaceAccess, task_id: uuid.UUID) -> TaskOut:
        return projection.task_out(await self._task(access, task_id))

    async def _task(self, access: WorkspaceAccess, task_id: uuid.UUID) -> ExternalResource:
        row = await repo.get_live(
            self.db, workspace_id=access.workspace.id, resource_type=repo.TASK, resource_id=task_id
        )
        if row is None:
            raise not_found()
        return row

    async def _project(self, access: WorkspaceAccess, project_id: uuid.UUID) -> ExternalResource:
        row = await repo.get_live(
            self.db,
            workspace_id=access.workspace.id,
            resource_type=repo.PROJECT,
            resource_id=project_id,
        )
        if row is None:
            raise not_found()
        return row

    # ---------------------------------------------------------------- 写入的公共部分

    async def _writable(self, access: WorkspaceAccess) -> tuple[Integration, TaskConnector]:
        integration = await self._integration(access)
        account = (
            await integration_repo.get_account(self.db, integration.id) if integration else None
        )
        if integration is None or account is None or integration.status not in ACTIVE_STATUSES:
            raise ApiError(
                409, "INTEGRATION_UNAVAILABLE", "Vikunja 集成未配置、已停用或尚未测试通过"
            )
        return integration, self.runtime.vikunja(integration, account)

    async def _begin(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        key: uuid.UUID,
        *,
        method: str,
        path: str,
        body: Any,
        operation: dict[str, Any],
    ) -> idempotency.Begun:
        return await idempotency.begin(
            self.db,
            workspace_id=access.workspace.id,
            actor_id=context.user.id,
            key=key,
            request_fingerprint=idempotency.fingerprint(access.workspace.id, method, path, body),
            context=operation,
        )

    async def _reload(self, record: IdempotencyRequest) -> IdempotencyRequest:
        """begin 提交过一次，重新锁住记录再写结果。"""
        locked = await integration_repo.get_idempotency(
            self.db, actor_id=record.actor_id, key=record.idempotency_key, lock=True
        )
        if locked is None:
            raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "请求记录已失效，请重新提交")
        return locked

    async def _external_failure(
        self,
        record: IdempotencyRequest,
        integration: Integration,
        error: ConnectorError,
        *,
        resource: ExternalResource | None = None,
    ) -> ApiError:
        """按契约第 7、8 节记下结果并返回要抛出的错误（调用方负责 raise）。"""
        old_status = integration.status
        integration.status = integration_status_after(error, old_status)
        await notifications.integration_status_changed(self.db, integration, old_status)
        problem = api_error(error)
        sent_but_unclear = error.kind == "UNAVAILABLE" and not error.not_sent
        if error.kind == "TIMEOUT_UNKNOWN" or sent_but_unclear:
            # 请求已发出、结果不确定（超时、5xx、无法解析的响应）：保留记录，
            # 重发同一个键时先查询结果
            idempotency.mark_unknown(record)
        elif error.not_sent or error.kind == "RATE_LIMITED" or _token_rejected(error):
            # 确定没有被处理（没发出、限流、Token 被拒）：删掉记录，
            # 问题解决后可以用同一个键重试
            await idempotency.discard(self.db, record)
        else:
            if error.kind == "NOT_FOUND" and resource is not None:
                await repo.tombstone(self.db, resource)
            idempotency.fail(record, problem)
        await self.db.commit()
        return problem

    def _healthy(self, integration: Integration) -> None:
        if integration.status in {"unavailable", "unauthorized"}:
            integration.status = "active"

    async def _audit(
        self,
        action: str,
        context: AuthContext,
        access: WorkspaceAccess,
        resource_id: uuid.UUID,
        metadata: dict[str, Any],
    ) -> None:
        await self.audit.record(
            action,
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="task",
            resource_id=resource_id,
            metadata=metadata,
            client_ip=context.client_ip,
        )

    async def _abandon(self, begun: idempotency.Begun) -> None:
        """登记之后、调用外部之前就失败了（资源不存在、集成不可用）：不留下记录。"""
        if begun.state == "new":
            await idempotency.discard(self.db, await self._reload(begun.record))
            await self.db.commit()
        else:
            await self.db.rollback()

    @staticmethod
    def _replay(begun: idempotency.Begun) -> WriteResult:
        status_code, body = idempotency.replay(begun.record)
        return WriteResult(status_code, body, replayed=True)

    # ---------------------------------------------------------------- 创建

    async def create_task(
        self, context: AuthContext, access: WorkspaceAccess, key: uuid.UUID, body: TaskCreate
    ) -> WriteResult:
        begun = await self._begin(
            context,
            access,
            key,
            method="POST",
            path="/api/v1/tasks",
            body=body.model_dump(mode="json"),
            operation={"op": "create"},
        )
        if begun.state == "replay":
            return self._replay(begun)
        try:
            project = await self._project(access, body.project_id)
            integration, client = await self._writable(access)
        except ApiError:
            await self._abandon(begun)
            raise
        if begun.state == "unknown":
            return await self._resolve_create(
                context, access, begun.record, integration, client, body, project
            )
        try:
            created = await client.create_task(
                projection.external_project_id(project),
                title=body.title,
                description=body.description,
                priority=body.priority,
                due_at=body.due_at,
            )
        except ConnectorError as error:
            record = await self._reload(begun.record)
            raise await self._external_failure(record, integration, error) from None
        record = await self._reload(begun.record)
        return await self._created(context, access, record, integration, created)

    async def _created(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        record: IdempotencyRequest,
        integration: Integration,
        task: VikunjaTask,
    ) -> WriteResult:
        self._healthy(integration)
        resource = await projection.save_task(self.db, integration, task)
        out = projection.task_out(resource).model_dump(mode="json")
        idempotency.succeed(record, 201, out)
        await self._audit("task.created", context, access, resource.id, {"external_id": task.id})
        await self.db.commit()
        return WriteResult(201, out)

    async def _resolve_create(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        record: IdempotencyRequest,
        integration: Integration,
        client: TaskConnector,
        body: TaskCreate,
        project: ExternalResource,
    ) -> WriteResult:
        try:
            found = await client.find_created_tasks(
                projection.external_project_id(project),
                title=body.title,
                created_since=idempotency.started_at(record) - _CLOCK_SKEW,
            )
        except ConnectorError as error:
            await self.db.rollback()
            raise api_error(error if error.kind != "TIMEOUT_UNKNOWN" else _unavailable()) from None
        if found:
            # 可能找到多个同名任务：取最早创建的那个（最接近这次请求）
            task = min(found, key=lambda t: (t.created or t.updated, t.id))
            return await self._created(context, access, record, integration, task)
        problem = idempotency.not_applied()
        idempotency.fail(record, problem)
        await self.db.commit()
        raise problem

    # ---------------------------------------------------------------- 修改

    async def update_task(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        key: uuid.UUID,
        body: TaskUpdate,
    ) -> WriteResult:
        begun = await self._begin(
            context,
            access,
            key,
            method="PATCH",
            path=f"/api/v1/tasks/{task_id}",
            body=body.model_dump(mode="json", exclude_unset=True),
            operation={"op": "update"},
        )
        if begun.state == "replay":
            return self._replay(begun)
        try:
            resource = await self._task(access, task_id)
            changes = await self._changes(access, body)
            integration, client = await self._writable(access)
        except ApiError:
            await self._abandon(begun)
            raise
        record = begun.record
        try:
            live = await client.get_task(projection.external_task_id(resource))
        except ConnectorError as error:
            record = await self._reload(record) if begun.state == "new" else record
            raise await self._read_failure(record, integration, error, resource) from None
        if begun.state == "unknown":
            # 结果未知：看看改动是否已经生效
            if _applied(live, changes):
                return await self._updated(context, access, record, integration, live, changes)
            problem = idempotency.not_applied()
            idempotency.fail(record, problem)
            await self.db.commit()
            raise problem
        if live.version != body.version:
            raise await self._conflict(await self._reload(record), integration, live) from None
        try:
            updated = await client.update_task(live.id, changes)
        except ConnectorError as error:
            record = await self._reload(record)
            raise await self._external_failure(
                record, integration, error, resource=resource
            ) from None
        record = await self._reload(record)
        return await self._updated(context, access, record, integration, updated, changes)

    async def _changes(self, access: WorkspaceAccess, body: TaskUpdate) -> dict[str, Any]:
        """把请求里的字段换成 Vikunja 的字段（project_id 换成外部项目 ID）。"""
        changes: dict[str, Any] = {}
        for name in sorted(body.model_fields_set - {"version"}):
            value = getattr(body, name)
            if name == "project_id":
                changes["project_id"] = projection.external_project_id(
                    await self._project(access, value)
                )
            else:
                changes[name] = value
        return changes

    async def _updated(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        record: IdempotencyRequest,
        integration: Integration,
        task: VikunjaTask,
        changes: dict[str, Any],
    ) -> WriteResult:
        self._healthy(integration)
        resource = await projection.save_task(self.db, integration, task)
        out = projection.task_out(resource).model_dump(mode="json")
        idempotency.succeed(record, 200, out)
        await self._audit("task.updated", context, access, resource.id, {"fields": sorted(changes)})
        await self.db.commit()
        return WriteResult(200, out)

    async def _conflict(
        self, record: IdempotencyRequest, integration: Integration, live: VikunjaTask
    ) -> ApiError:
        resource = await projection.save_task(self.db, integration, live)
        current = projection.task_out(resource).model_dump(mode="json")
        problem = ApiError(
            409,
            "VERSION_CONFLICT",
            "任务已被其他操作修改，请刷新后重试",
            details={"current_version": live.version, "current": current},
        )
        idempotency.fail(record, problem)
        await self.db.commit()
        return problem

    async def _read_failure(
        self,
        record: IdempotencyRequest,
        integration: Integration,
        error: ConnectorError,
        resource: ExternalResource,
    ) -> ApiError:
        """写之前的读取失败：还没有改动任何东西。"""
        if error.kind == "NOT_FOUND":
            await repo.tombstone(self.db, resource)
            problem = api_error(error)
            idempotency.fail(record, problem)
            await self.db.commit()
            return problem
        old_status = integration.status
        integration.status = integration_status_after(error, old_status)
        await notifications.integration_status_changed(self.db, integration, old_status)
        if record.status == "processing":
            await idempotency.discard(self.db, record)
        await self.db.commit()
        return api_error(error if error.kind != "TIMEOUT_UNKNOWN" else _unavailable())

    # ---------------------------------------------------------------- 删除

    async def delete_task(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        task_id: uuid.UUID,
        key: uuid.UUID,
        version: str,
    ) -> WriteResult:
        begun = await self._begin(
            context,
            access,
            key,
            method="DELETE",
            path=f"/api/v1/tasks/{task_id}",
            body={"version": version},
            operation={"op": "delete"},
        )
        if begun.state == "replay":
            return self._replay(begun)
        try:
            resource = await repo.get_any(
                self.db,
                workspace_id=access.workspace.id,
                resource_type=repo.TASK,
                resource_id=task_id,
            )
            if resource is None:
                raise not_found()
            integration, client = await self._writable(access)
        except ApiError:
            await self._abandon(begun)
            raise
        record = begun.record if begun.state == "unknown" else await self._reload(begun.record)
        if resource.deleted_at is not None:
            # 投影已经知道它被删除了：删除是幂等的
            return await self._deleted(context, access, record, integration, resource)
        try:
            live = await client.get_task(projection.external_task_id(resource))
        except ConnectorError as error:
            if error.kind == "NOT_FOUND":
                return await self._deleted(context, access, record, integration, resource)
            raise await self._read_failure(record, integration, error, resource) from None
        if begun.state == "unknown":
            problem = idempotency.not_applied()  # 任务还在：上一次的删除没有生效
            idempotency.fail(record, problem)
            await self.db.commit()
            raise problem
        if live.version != version:
            raise await self._conflict(record, integration, live) from None
        try:
            await client.delete_task(live.id)
        except ConnectorError as error:
            if error.kind == "NOT_FOUND":
                return await self._deleted(context, access, record, integration, resource)
            raise await self._external_failure(
                record, integration, error, resource=resource
            ) from None
        return await self._deleted(context, access, record, integration, resource)

    async def _deleted(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        record: IdempotencyRequest,
        integration: Integration,
        resource: ExternalResource,
    ) -> WriteResult:
        self._healthy(integration)
        await repo.tombstone(self.db, resource)
        idempotency.succeed(record, 204, None)
        await self._audit(
            "task.deleted", context, access, resource.id, {"external_id": resource.external_id}
        )
        await self.db.commit()
        return WriteResult(204, None)


def _token_rejected(error: ConnectorError) -> bool:
    return error.kind == "AUTH_FAILED" and not error.resource_level


def _unavailable() -> ConnectorError:
    return ConnectorError("UNAVAILABLE", "Vikunja 暂时不可用", reason="read_failed")


def _applied(task: VikunjaTask, changes: dict[str, Any]) -> bool:
    """结果未知的修改是否已经生效：逐项比较 Vikunja 上的当前值。

    截止时间按秒比较（发给 Vikunja 时已截到秒）；描述忽略首尾空白（Markdown 往返可能增减换行）。
    误判为“未生效”只会让用户再改一次，不会造成重复数据。
    """
    current: dict[str, Any] = {
        "title": task.title,
        "description": task.description.strip(),
        "priority": task.priority,
        "due_at": _seconds(task.due_at),
        "done": task.done,
        "project_id": task.project_id,
    }
    expected = dict(changes)
    if "description" in expected:
        expected["description"] = str(expected["description"]).strip()
    if "due_at" in expected:
        expected["due_at"] = _seconds(expected["due_at"])
    return all(current.get(name) == value for name, value in expected.items())


def _seconds(value: datetime | None) -> datetime | None:
    return value.replace(microsecond=0) if value is not None else None
