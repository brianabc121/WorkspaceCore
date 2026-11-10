"""插拔式模块的启用状态与应用中心（T-0159；T-0210，docs/v2/app-center/）。

- GET /modules：工作空间成员都能看（前端据此显示侧栏和页面）；
- PATCH /modules/{key}：只有 owner 能启用 / 停用；依赖不满足 409 MODULE_DEPENDENCY，
  带 expected_state_version 且已过期 409 VERSION_CONFLICT，正在操作 409 MODULE_BUSY；
- 应用中心（owner / admin 读；检测 owner / admin；重试同步只有 owner）：
  /modules/management、/modules/activity、/modules/{key}/management、/modules/{key}/activity、
  POST /modules/{key}/health-check、POST /modules/{key}/reconcile。
  静态路径写在 /modules/{key} 之前。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Security
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    deployment_health,
    get_app_settings,
    get_connectors,
    get_db,
    get_redis,
    workspace_admin,
    workspace_owner,
    workspace_viewer,
)
from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess
from app.modules.management import ModuleManagementService
from app.modules.service import ModuleService
from app.schemas.audit import AuditLogView
from app.schemas.common import Page, error_responses
from app.schemas.module_management import (
    ModuleHealthOut,
    ModuleManagementList,
    ModuleManagementOut,
    ModuleReconcileIn,
)
from app.schemas.modules import ModuleList, ModuleOut, ModuleUpdate

router = APIRouter(tags=["modules"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]


def get_module_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    runtime: Annotated[ConnectorRuntime, Depends(get_connectors)],
) -> ModuleService:
    return ModuleService(db, settings, runtime)


def get_management_service(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    runtime: Annotated[ConnectorRuntime, Depends(get_connectors)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ModuleManagementService:
    return ModuleManagementService(db, settings, runtime, deployment_health(request.app), redis)


Service = Annotated[ModuleService, Depends(get_module_service)]
Management = Annotated[ModuleManagementService, Depends(get_management_service)]
ModuleKey = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,31}$", description="模块 key")]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=512)]
_ACTIVITY = (
    "新的在前；精确匹配 resource_type=module 与 resource_id=模块 key（启用 / 停用、同步与重试）。"
    "next_cursor 绑定工作空间与模块，换模块要从第一页重新加载（旧游标 422）；不返回总数。"
)


@router.get(
    "/modules",
    summary="模块列表与在当前工作空间的启用状态（viewer）",
    responses=error_responses(401, 404),
)
async def list_modules(access: Viewer, service: Service) -> ModuleList:
    return await service.list(access)


@router.get(
    "/modules/management",
    summary="应用中心：所有模块的管理信息（owner / admin）",
    description="不分页；summary 基于全部已注册模块。只读注册表、数据库与健康缓存，"
    "不会为了这个请求去探测外部服务（health 看 checked_at 与 stale）。",
    responses=error_responses(401, 403, 404),
)
async def list_module_management(access: Admin, service: Management) -> ModuleManagementList:
    return await service.list(access)


@router.get(
    "/modules/activity",
    summary="应用中心：所有模块的操作记录（owner / admin）",
    description=_ACTIVITY.replace("与 resource_id=模块 key", "（含已移除的模块）"),
    responses=error_responses(401, 403, 404, 422),
)
async def list_module_activity(
    access: Admin, service: Management, limit: Limit = 30, cursor: Cursor = None
) -> Page[AuditLogView]:
    return await service.activity(access, None, cursor=cursor, limit=limit)


@router.get(
    "/modules/{key}/management",
    summary="应用中心：一个模块的管理信息（owner / admin）",
    description="不探测外部服务；未知模块 404。",
    responses=error_responses(401, 403, 404, 422),
)
async def get_module_management(
    key: ModuleKey, access: Admin, service: Management
) -> ModuleManagementOut:
    return await service.get(access, key)


@router.get(
    "/modules/{key}/activity",
    summary="应用中心：一个模块的操作记录（owner / admin）",
    description=_ACTIVITY + "未知模块 404（已移除模块的记录请用 /modules/activity）。",
    responses=error_responses(401, 403, 404, 422),
)
async def list_one_module_activity(
    key: ModuleKey, access: Admin, service: Management, limit: Limit = 30, cursor: Cursor = None
) -> Page[AuditLogView]:
    return await service.activity(access, key, cursor=cursor, limit=limit)


@router.post(
    "/modules/{key}/health-check",
    summary="应用中心：检测模块声明的外部服务（owner / admin）",
    description="只探测这个模块清单声明的服务（不接受地址或参数）；每个服务最多 10 秒，"
    "同时最多 3 个；每个工作空间的每个模块每分钟最多 3 次（超过 429，带 Retry-After）。"
    "模块已停用 409 MODULE_DISABLED_FOR_CHECK；没有声明服务返回 not_applicable；"
    "没部署返回 not_configured，不请求外部地址。检测失败也返回 200（state=down）。",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 409, 422, 429),
)
async def check_module_health(
    key: ModuleKey, access: Admin, service: Management
) -> ModuleHealthOut:
    return await service.check_health(access, key)


@router.post(
    "/modules/{key}/reconcile",
    summary="应用中心：按当前目标重试同步（owner）",
    description="不改启用状态、不自动启用依赖；与启用 / 停用共用工作空间的操作锁。"
    "同步结果（成功、失败、未知）都在 200 的 sync 里。expected_state_version 已过期 "
    "409 VERSION_CONFLICT；正在操作 409 MODULE_BUSY；模块没有同步钩子时返回 not_required，"
    "不写同步记录。",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def reconcile_module(
    key: ModuleKey,
    body: ModuleReconcileIn,
    access: Owner,
    service: Management,
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ModuleManagementOut:
    return await service.reconcile(
        access, key, body.expected_state_version, client_ip(request, settings)
    )


@router.patch(
    "/modules/{key}",
    summary="启用或停用一个模块（owner）",
    description="停用不删除模块的数据，再启用就恢复。缺少部署前提（ready=false）也可以启用。"
    "依赖的模块没启用、或有启用的模块依赖它时返回 409 MODULE_DEPENDENCY；"
    "带 expected_state_version 且与当前不一致时 409 VERSION_CONFLICT；"
    "同一工作空间正在启用 / 停用或重试同步时 409 MODULE_BUSY。"
    "目标没变时不写审计、不执行同步；同步失败不回滚开关（结果见 /modules/{key}/management）。",
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
    return await service.update(
        access,
        key,
        body.enabled,
        client_ip(request, settings),
        expected_state_version=body.expected_state_version,
    )
