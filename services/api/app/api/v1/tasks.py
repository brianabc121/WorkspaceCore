"""外部项目与任务（Sprint 3）：读本地投影，写经 Vikunja Connector。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可读，member 及以上可写。
写接口必须带 Idempotency-Key（UUID），语义见
docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 7 节；外部错误的映射见第 8 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, Security, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_task_service,
    workspace_member,
    workspace_viewer,
)
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.domain.tasks import TaskService, WriteResult
from app.schemas.common import error_responses
from app.schemas.tasks import (
    ProjectPage,
    ProjectProgressList,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskSort,
    TaskStatusFilter,
    TaskUpdate,
    TaskVersion,
)

router = APIRouter(tags=["tasks"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[TaskService, Depends(get_task_service)]
IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description="每次用户提交生成一个新的 UUID；网络重试与查询结果时复用同一个",
    ),
]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_read_errors = error_responses(401, 404, 422)
# 写操作还可能遇到 Vikunja 的鉴权失败、不可用与结果未知（契约第 8 节）
_write = [Depends(csrf_protected)]
_write_errors = error_responses(401, 403, 404, 409, 422, 502, 503, 504)
_REPLAY = (
    "同一个 Idempotency-Key 的重复请求原样返回第一次的结果，响应头带 Idempotent-Replayed: true。"
)


def _respond(result: WriteResult) -> Response:
    """写接口的结果；重放时带 Idempotent-Replayed（契约第 7 节）。"""
    headers = {"Idempotent-Replayed": "true"} if result.replayed else None
    if result.body is None:
        return Response(status_code=result.status_code, headers=headers)
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)


@router.get(
    "/projects",
    summary="外部项目列表（viewer，读本地投影）",
    responses=_read_errors,
)
async def list_projects(
    access: Viewer,
    service: Service,
    integration_id: UUID | None = None,
    include_archived: bool = False,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> ProjectPage:
    return await service.list_projects(
        access,
        integration_id=integration_id,
        include_archived=include_archived,
        q=q,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/projects/progress",
    summary="项目进度（viewer，读本地投影）",
    description="未归档项目的未完成 / 已完成任务数，供首页“项目进度”组件使用；"
    "有未完成任务的项目在前。",
    responses=_read_errors,
)
async def project_progress(
    access: Viewer, service: Service, limit: Annotated[int, Query(ge=1, le=20)] = 5
) -> ProjectProgressList:
    return await service.project_progress(access, limit=limit)


@router.get(
    "/tasks",
    summary="任务列表（viewer，读本地投影）",
    description="sync 表示投影的新鲜度；看板视图由前端按 done 分列。",
    responses=_read_errors,
)
async def list_tasks(
    access: Viewer,
    service: Service,
    status_filter: Annotated[TaskStatusFilter, Query(alias="status")] = "open",
    project_id: UUID | None = None,
    priority: Annotated[list[Annotated[int, Field(ge=0, le=5)]] | None, Query()] = None,
    due_before: Annotated[AwareDatetime | None, Query()] = None,
    due_after: Annotated[AwareDatetime | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    sort: TaskSort = "due_at",
    cursor: Cursor = None,
    limit: Limit = 30,
) -> TaskPage:
    return await service.list_tasks(
        access,
        status=status_filter,
        project_id=project_id,
        priorities=priority,
        due_before=due_before,
        due_after=due_after,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/tasks",
    status_code=status.HTTP_201_CREATED,
    summary="创建任务（member，必须带 Idempotency-Key）",
    description=_REPLAY
    + "请求已发出但结果未知时返回 504 RESULT_UNKNOWN，用同一个键重发即可查询结果。",
    response_model=TaskOut,
    dependencies=_write,
    responses=_write_errors,
)
async def create_task(
    access: Member,
    context: Context,
    service: Service,
    idempotency_key: IdempotencyKey,
    body: TaskCreate,
) -> Response:
    return _respond(await service.create_task(context, access, idempotency_key, body))


@router.get(
    "/tasks/{task_id}",
    summary="任务详情（viewer，读本地投影）",
    responses=_read_errors,
)
async def get_task(access: Viewer, service: Service, task_id: UUID) -> TaskOut:
    return await service.get_task(access, task_id)


@router.patch(
    "/tasks/{task_id}",
    summary="修改任务（member，必须带 Idempotency-Key 与 version）",
    description=_REPLAY + "version 与 Vikunja 上的当前版本不一致时返回 409 VERSION_CONFLICT，"
    "details.current 为最新的任务。",
    response_model=TaskOut,
    dependencies=_write,
    responses=_write_errors,
)
async def update_task(
    access: Member,
    context: Context,
    service: Service,
    task_id: UUID,
    idempotency_key: IdempotencyKey,
    body: TaskUpdate,
) -> Response:
    return _respond(await service.update_task(context, access, task_id, idempotency_key, body))


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除任务（member，必须带 Idempotency-Key 与 version；前端先二次确认）",
    description=_REPLAY + "Vikunja 里已经不存在时同样返回 204。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_task(
    access: Member,
    context: Context,
    service: Service,
    task_id: UUID,
    idempotency_key: IdempotencyKey,
    version: Annotated[TaskVersion, Query()],
) -> Response:
    return _respond(await service.delete_task(context, access, task_id, idempotency_key, version))
