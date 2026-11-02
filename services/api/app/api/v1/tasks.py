"""工作台自己的项目与任务（T-0082；之前是 Vikunja 的投影）。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可读，member 及以上可写。
任务的写接口必须带 Idempotency-Key（UUID），语义沿用
docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 7 节；
项目的写接口用整数 version 做乐观并发。
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
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectProgressList,
    ProjectUpdate,
    ProjectVersion,
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
_write = [Depends(csrf_protected)]
_write_errors = error_responses(401, 403, 404, 409, 422)
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
    summary="项目列表（viewer）",
    description="默认不含已归档的项目；每个项目带未完成 / 已完成任务数。",
    responses=_read_errors,
)
async def list_projects(
    access: Viewer,
    service: Service,
    include_archived: bool = False,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> ProjectPage:
    return await service.list_projects(
        access, include_archived=include_archived, q=q, cursor=cursor, limit=limit
    )


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    summary="新建项目（member）",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def create_project(
    access: Member, context: Context, service: Service, body: ProjectCreate
) -> ProjectOut:
    return await service.create_project(context, access, body)


@router.get(
    "/projects/progress",
    summary="项目进度（viewer）",
    description="未归档项目的未完成 / 已完成任务数，供首页“项目进度”组件使用；"
    "有未完成任务的项目在前。",
    responses=_read_errors,
)
async def project_progress(
    access: Viewer, service: Service, limit: Annotated[int, Query(ge=1, le=20)] = 5
) -> ProjectProgressList:
    return await service.project_progress(access, limit=limit)


@router.get(
    "/projects/{project_id}",
    summary="项目详情（viewer）",
    responses=_read_errors,
)
async def get_project(access: Viewer, service: Service, project_id: UUID) -> ProjectOut:
    return await service.get_project(access, project_id)


@router.patch(
    "/projects/{project_id}",
    summary="修改项目（member，乐观并发）",
    description="改名、改描述、归档 / 取消归档；"
    "version 与当前版本不一致时返回 409 VERSION_CONFLICT，details.current 为最新的项目。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_project(
    access: Member, context: Context, service: Service, project_id: UUID, body: ProjectUpdate
) -> ProjectOut:
    return await service.update_project(context, access, project_id, body)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除项目及它的全部任务（member，乐观并发；前端先二次确认）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_project(
    access: Member,
    context: Context,
    service: Service,
    project_id: UUID,
    version: Annotated[ProjectVersion, Query()],
) -> None:
    await service.delete_project(context, access, project_id, version)


@router.get(
    "/tasks",
    summary="任务列表（viewer）",
    description="看板视图由前端按 done 分列。",
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
    description=_REPLAY,
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
    summary="任务详情（viewer）",
    responses=_read_errors,
)
async def get_task(access: Viewer, service: Service, task_id: UUID) -> TaskOut:
    return await service.get_task(access, task_id)


@router.patch(
    "/tasks/{task_id}",
    summary="修改任务（member，必须带 Idempotency-Key 与 version）",
    description=_REPLAY + "version 与当前版本不一致时返回 409 VERSION_CONFLICT，"
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
    description=_REPLAY + "任务已经不存在时同样返回 204。",
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
