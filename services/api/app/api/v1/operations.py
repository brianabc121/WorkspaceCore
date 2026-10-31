"""运维（Sprint 5）：服务状态与预设指标，只读。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间，viewer 可读：核心服务与监控是部署级的，
集成的状态只包含当前工作空间的集成。浏览器只拿到聚合结果，不接受 PromQL，也不返回内部地址。
约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 3 节。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import authenticated, get_operations_service, workspace_viewer
from app.domain.operations import OperationsService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.operations import MetricRange, MetricsOut, MetricViewId, ServiceStatusList

router = APIRouter(tags=["operations"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Service = Annotated[OperationsService, Depends(get_operations_service)]
_read_errors = error_responses(401, 404, 422)


@router.get(
    "/operations/services",
    summary="服务状态（viewer）",
    description="核心服务、可选的监控组件，以及当前工作空间的集成；"
    "没有部署的组件为 not_configured。",
    responses=_read_errors,
)
async def list_services(access: Viewer, service: Service) -> ServiceStatusList:
    return await service.services(access)


@router.get(
    "/operations/metrics",
    summary="预设指标（viewer）",
    description="按视图 ID 返回聚合好的数据点；Prometheus 不可用时 views 为空、sync 说明原因。",
    responses=_read_errors,
)
async def get_metrics(
    access: Viewer,
    service: Service,
    range_: Annotated[MetricRange, Query(alias="range")] = "1h",
    views: Annotated[
        list[MetricViewId] | None, Query(description="可重复；默认全部预设视图")
    ] = None,
) -> MetricsOut:
    return await service.metrics(range_, views or [])
