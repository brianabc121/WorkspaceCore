"""工作空间：列表、详情、设置与成员管理。非成员一律按不存在处理（404），防止枚举。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, Security, status

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_auth_service,
    get_member_service,
    get_workspace_service,
)
from app.domain.auth import AuthContext, AuthService
from app.domain.members import MemberService
from app.domain.workspaces import WorkspaceService
from app.schemas.common import Page, error_responses
from app.schemas.members import MemberCreate, MemberOut, MemberUpdate
from app.schemas.workspaces import WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get(
    "",
    summary="我所属的工作空间",
    responses=error_responses(401),
)
async def list_workspaces(
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Page[WorkspaceOut]:
    items = await service.workspaces(context.user)
    return Page(items=items, next_cursor=None, total_estimate=len(items))


@router.get(
    "/{workspace_id}",
    summary="工作空间详情（viewer 及以上）",
    dependencies=[Security(authenticated)],
    responses=error_responses(401, 404),
)
async def get_workspace(
    workspace_id: UUID,
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> WorkspaceOut:
    return await service.get(context, workspace_id)


@router.patch(
    "/{workspace_id}",
    summary="修改工作空间设置（admin 及以上）",
    dependencies=[Security(authenticated), Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 422),
)
async def update_workspace(
    workspace_id: UUID,
    body: WorkspaceUpdate,
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> WorkspaceOut:
    return await service.update(context, workspace_id, name=body.name)


# ---------------------------------------------------------------- 成员（T-0064）

Members = Annotated[MemberService, Depends(get_member_service)]
Context = Annotated[AuthContext, Security(authenticated)]
_write = [Security(authenticated), Depends(csrf_protected)]


@router.get(
    "/{workspace_id}/members",
    summary="成员列表（admin 及以上）",
    responses=error_responses(401, 403, 404),
)
async def list_members(workspace_id: UUID, context: Context, service: Members) -> Page[MemberOut]:
    items = await service.members(context, workspace_id)
    return Page(items=items, next_cursor=None, total_estimate=len(items))


@router.post(
    "/{workspace_id}/members",
    status_code=status.HTTP_201_CREATED,
    summary="添加成员（admin 及以上；授予 admin 只能由 owner）",
    description="已有账号按邮箱加入；没有账号时同时提供 display_name 与 password（初始密码），"
    "创建账号后加入，响应头 `X-Account-Created: true`。"
    "没有账号又没给初始密码时返回 404 `USER_NOT_FOUND`。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def add_member(
    workspace_id: UUID, body: MemberCreate, response: Response, context: Context, service: Members
) -> MemberOut:
    member, created = await service.add(context, workspace_id, body)
    if created:
        response.headers["X-Account-Created"] = "true"
    return member


@router.patch(
    "/{workspace_id}/members/{user_id}",
    summary="修改成员角色（admin 管理 member / viewer；涉及 admin 只能由 owner）",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def update_member(
    workspace_id: UUID, user_id: UUID, body: MemberUpdate, context: Context, service: Members
) -> MemberOut:
    return await service.update(context, workspace_id, user_id, body)


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除成员（owner 不能移除；移除 admin 只能由 owner）",
    dependencies=_write,
    responses=error_responses(401, 403, 404),
)
async def remove_member(
    workspace_id: UUID, user_id: UUID, context: Context, service: Members
) -> None:
    await service.remove(context, workspace_id, user_id)
