"""仪表盘、Widget 与首页聚合（Sprint 2）。

所有端点都是工作空间范围的资源，用 X-Workspace-Id 指定工作空间：
viewer 可读，member 及以上可写（A03）；路径里的仪表盘 / 组件不属于该工作空间时返回 404。
约定见 docs/decisions/2026-10-30-sprint-2-dashboard-contract.md。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Response, Security, status

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_dashboard_service,
    workspace_member,
    workspace_viewer,
)
from app.domain.auth import AuthContext
from app.domain.dashboards import DashboardService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import Page, error_responses
from app.schemas.dashboards import (
    DashboardCreate,
    DashboardOut,
    DashboardOverview,
    DashboardUpdate,
    WidgetCreate,
    WidgetOut,
    WidgetUpdate,
)

router = APIRouter(tags=["dashboards"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[DashboardService, Depends(get_dashboard_service)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)


@router.get(
    "/dashboard/overview",
    summary="首页聚合：默认仪表盘、组件与各模块状态（viewer）",
    description="不同步等待外部服务；单个模块失败只把该模块标为 unavailable / stale，"
    "不影响其余内容。",
    responses=_read_errors,
)
async def get_dashboard_overview(access: Viewer, service: Service) -> DashboardOverview:
    return await service.overview(access)


@router.get(
    "/dashboards",
    summary="工作空间的仪表盘列表（viewer）",
    responses=_read_errors,
)
async def list_dashboards(access: Viewer, service: Service) -> Page[DashboardOut]:
    items = await service.list_dashboards(access)
    return Page(items=items, next_cursor=None, total_estimate=len(items))


@router.post(
    "/dashboards",
    status_code=status.HTTP_201_CREATED,
    summary="创建仪表盘，可一并导入组件（member）",
    dependencies=_write,
    responses=_write_errors,
)
async def create_dashboard(
    access: Member, context: Context, service: Service, body: DashboardCreate
) -> DashboardOut:
    return await service.create_dashboard(context, access, body)


@router.patch(
    "/dashboards/{dashboard_id}",
    summary="修改标题、设为首页或保存布局（member，乐观并发）",
    description="version 与当前版本不一致时返回 409 VERSION_CONFLICT，"
    "details.current_version 为最新版本。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_dashboard(
    dashboard_id: UUID, access: Member, context: Context, service: Service, body: DashboardUpdate
) -> DashboardOut:
    return await service.update_dashboard(context, access, dashboard_id, body)


@router.get(
    "/dashboards/{dashboard_id}/widgets",
    summary="仪表盘的组件（viewer）",
    responses=_read_errors,
)
async def list_widgets(dashboard_id: UUID, access: Viewer, service: Service) -> Page[WidgetOut]:
    items = await service.list_widgets(access, dashboard_id)
    return Page(items=items, next_cursor=None, total_estimate=len(items))


@router.post(
    "/dashboards/{dashboard_id}/widgets",
    status_code=status.HTTP_201_CREATED,
    summary="添加组件（member）；仪表盘版本递增",
    dependencies=_write,
    responses=_write_errors,
)
async def create_widget(
    dashboard_id: UUID,
    access: Member,
    context: Context,
    service: Service,
    body: Annotated[WidgetCreate, Body()],
) -> WidgetOut:
    return await service.create_widget(context, access, dashboard_id, body)


@router.patch(
    "/widgets/{widget_id}",
    summary="修改组件配置（member，乐观并发）",
    description="version 与当前版本不一致时返回 409 VERSION_CONFLICT；"
    "widget_type 必须与现有类型相同。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_widget(
    widget_id: UUID,
    access: Member,
    context: Context,
    service: Service,
    body: Annotated[WidgetUpdate, Body()],
) -> WidgetOut:
    return await service.update_widget(context, access, widget_id, body)


@router.delete(
    "/widgets/{widget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除组件（member）；仪表盘版本递增",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def delete_widget(
    widget_id: UUID, access: Member, context: Context, service: Service
) -> None:
    await service.delete_widget(context, access, widget_id)
