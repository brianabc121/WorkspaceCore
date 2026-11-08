"""插拔式模块的启用状态（T-0159；docs/modules/README.md）。

- GET：工作空间成员都能看（前端据此显示侧栏和页面）；
- PATCH：只有 owner 能启用 / 停用；依赖不满足时 409 MODULE_DEPENDENCY。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_connectors,
    get_db,
    workspace_owner,
    workspace_viewer,
)
from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess
from app.modules.service import ModuleService
from app.schemas.common import error_responses
from app.schemas.modules import ModuleList, ModuleOut, ModuleUpdate

router = APIRouter(tags=["modules"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]


def get_module_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    runtime: Annotated[ConnectorRuntime, Depends(get_connectors)],
) -> ModuleService:
    return ModuleService(db, settings, runtime)


Service = Annotated[ModuleService, Depends(get_module_service)]
ModuleKey = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,31}$", description="模块 key")]


@router.get(
    "/modules",
    summary="模块列表与在当前工作空间的启用状态（viewer）",
    responses=error_responses(401, 404),
)
async def list_modules(access: Viewer, service: Service) -> ModuleList:
    return await service.list(access)


@router.patch(
    "/modules/{key}",
    summary="启用或停用一个模块（owner）",
    description="停用不删除模块的数据，再启用就恢复。"
    "依赖的模块没启用、或有启用的模块依赖它时返回 409。",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def update_module(
    key: ModuleKey,
    body: ModuleUpdate,
    access: Owner,
    service: Service,
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ModuleOut:
    return await service.update(access, key, body.enabled, client_ip(request, settings))
