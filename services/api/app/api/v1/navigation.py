"""侧栏布局（T-0161）：成员都能读，只有 owner 能改。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_db,
    workspace_owner,
    workspace_viewer,
)
from app.core.config import Settings
from app.domain.navigation import NavigationService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.navigation import NavigationIn, NavigationOut

router = APIRouter(tags=["navigation"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]


def get_navigation_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> NavigationService:
    return NavigationService(db, settings)


Service = Annotated[NavigationService, Depends(get_navigation_service)]


@router.get(
    "/navigation",
    summary="侧栏布局：自定义分组、页面挪分组、零代码页面（viewer）",
    description="零代码页面只返回当前用户角色能看到的。还没改过时返回空布局，version=0。",
    responses=error_responses(401, 404),
)
async def get_navigation(access: Viewer, service: Service) -> NavigationOut:
    return await service.get(access)


@router.put(
    "/navigation",
    summary="保存侧栏布局（owner，整份替换）",
    description="version 与当前不一致返回 409 VERSION_CONFLICT。外部链接只允许 http(s)；"
    "嵌入页只允许部署设置 EMBED_ORIGINS 里的来源；笔记、仪表盘必须属于当前工作空间。",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def put_navigation(
    body: NavigationIn,
    access: Owner,
    service: Service,
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> NavigationOut:
    return await service.update(access, body, client_ip(request, settings))
