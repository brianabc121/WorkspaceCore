"""工作空间：列表、详情与设置。非成员一律按不存在处理（404），防止枚举。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Security

from app.api.deps import authenticated, csrf_protected
from app.core.errors import not_implemented
from app.schemas.common import Page, error_responses
from app.schemas.workspaces import WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get(
    "",
    summary="我所属的工作空间",
    dependencies=[Security(authenticated)],
    responses=error_responses(401, 501),
)
async def list_workspaces() -> Page[WorkspaceOut]:
    raise not_implemented("T-0010")


@router.get(
    "/{workspace_id}",
    summary="工作空间详情（viewer 及以上）",
    dependencies=[Security(authenticated)],
    responses=error_responses(401, 404, 501),
)
async def get_workspace(workspace_id: UUID) -> WorkspaceOut:
    raise not_implemented("T-0011")


@router.patch(
    "/{workspace_id}",
    summary="修改工作空间设置（admin 及以上）",
    dependencies=[Security(authenticated), Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 422, 501),
)
async def update_workspace(workspace_id: UUID, body: WorkspaceUpdate) -> WorkspaceOut:
    raise not_implemented("T-0011")
