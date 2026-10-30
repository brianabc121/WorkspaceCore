"""仪表盘、Widget 与首页聚合（Sprint 2）。

所有端点都是工作空间范围的资源，用 X-Workspace-Id 指定工作空间：
viewer 可读，member 及以上可写（A03）；路径里的仪表盘 / 组件不属于该工作空间时返回 404。
约定见 docs/decisions/2026-10-30-sprint-2-dashboard-contract.md。

T-0014 只声明契约：登录、CSRF、成员与角色校验已经生效，处理函数在 T-0015 实现之前返回 501。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Response, Security, status

from app.api.deps import authenticated, csrf_protected, workspace_member, workspace_viewer
from app.core.errors import not_implemented
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
async def get_dashboard_overview(access: Viewer) -> DashboardOverview:
    raise not_implemented("T-0015")


@router.get(
    "/dashboards",
    summary="工作空间的仪表盘列表（viewer）",
    responses=_read_errors,
)
async def list_dashboards(access: Viewer) -> Page[DashboardOut]:
    raise not_implemented("T-0015")


@router.post(
    "/dashboards",
    status_code=status.HTTP_201_CREATED,
    summary="创建仪表盘，可一并导入组件（member）",
    dependencies=_write,
    responses=_write_errors,
)
async def create_dashboard(access: Member, body: DashboardCreate) -> DashboardOut:
    raise not_implemented("T-0015")


@router.patch(
    "/dashboards/{dashboard_id}",
    summary="修改标题、设为首页或保存布局（member，乐观并发）",
    description="version 与当前版本不一致时返回 409 VERSION_CONFLICT，"
    "details.current_version 为最新版本。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_dashboard(
    dashboard_id: UUID, access: Member, body: DashboardUpdate
) -> DashboardOut:
    raise not_implemented("T-0015")


@router.get(
    "/dashboards/{dashboard_id}/widgets",
    summary="仪表盘的组件（viewer）",
    responses=_read_errors,
)
async def list_widgets(dashboard_id: UUID, access: Viewer) -> Page[WidgetOut]:
    raise not_implemented("T-0015")


@router.post(
    "/dashboards/{dashboard_id}/widgets",
    status_code=status.HTTP_201_CREATED,
    summary="添加组件（member）；仪表盘版本递增",
    dependencies=_write,
    responses=_write_errors,
)
async def create_widget(
    dashboard_id: UUID, access: Member, body: Annotated[WidgetCreate, Body()]
) -> WidgetOut:
    raise not_implemented("T-0015")


@router.patch(
    "/widgets/{widget_id}",
    summary="修改组件配置（member，乐观并发）",
    description="version 与当前版本不一致时返回 409 VERSION_CONFLICT；"
    "widget_type 必须与现有类型相同。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_widget(
    widget_id: UUID, access: Member, body: Annotated[WidgetUpdate, Body()]
) -> WidgetOut:
    raise not_implemented("T-0015")


@router.delete(
    "/widgets/{widget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除组件（member）；仪表盘版本递增",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def delete_widget(widget_id: UUID, access: Member) -> None:
    raise not_implemented("T-0015")
