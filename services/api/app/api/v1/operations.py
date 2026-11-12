"""运维（Sprint 5）：服务状态与预设指标，只读。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间，viewer 可读：核心服务与监控是部署级的，
集成的状态只包含当前工作空间的集成。浏览器只拿到聚合结果，不接受 PromQL，也不返回内部地址。
约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 3 节。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Security

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_file_service,
    get_operations_service,
    workspace_admin,
    workspace_viewer,
)
from app.domain import knowledge_files
from app.domain.knowledge_files import FileService
from app.domain.operations import OperationsService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.operations import (
    MetricRange,
    MetricsOut,
    MetricViewId,
    ServiceStatusList,
    StorageStatus,
)

router = APIRouter(tags=["operations"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Files = Annotated[FileService, Depends(get_file_service)]
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


# ---------------------------------------------------------------- 知识库图片存储（T-0103）


@router.get(
    "/operations/storage",
    summary="知识库图片存储的状态（owner / admin）",
    description="这个工作空间的主存储、副本（在“设置 → 图片存储”里配置）、最近一次自检、"
    "用量与配额、待复制数量。不返回密钥；本地存储不返回路径。",
    responses=error_responses(401, 403, 404, 422),
)
async def storage_status(request: Request, access: Admin, files: Files) -> StorageStatus:
    stores = await files.stores(access.workspace.id)
    return await knowledge_files.storage_status(
        files.db, stores, request.app.state.redis, access.workspace.id
    )


@router.post(
    "/operations/storage/check",
    summary="知识库图片存储自检（owner / admin）",
    description="对这个工作空间的主存储与副本各写一个探测对象、读回、删除；结果也显示在运维页。",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 422),
)
async def check_storage(request: Request, access: Admin, files: Files) -> StorageStatus:
    redis = request.app.state.redis
    stores = await files.stores(access.workspace.id)
    checks = await knowledge_files.run_checks(stores, redis, access.workspace.id)
    return await knowledge_files.storage_status(
        files.db, stores, redis, access.workspace.id, checks
    )
