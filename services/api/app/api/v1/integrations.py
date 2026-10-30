"""第三方集成：Vikunja（Sprint 3）与 GitHub（Sprint 4）的配置、测试连接与手动同步。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可查看，admin 及以上可配置。
Token 只写不读。约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 4 节与
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 6 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Security, status

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_integration_service,
    workspace_admin,
    workspace_viewer,
)
from app.core.errors import not_implemented
from app.domain.auth import AuthContext
from app.domain.integrations import IntegrationService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import Page, error_responses
from app.schemas.integrations import (
    AnyIntegrationCreate,
    GitHubIntegrationCreate,
    IntegrationOut,
    IntegrationTestResult,
    IntegrationUpdate,
    SyncRequestOut,
)

router = APIRouter(tags=["integrations"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[IntegrationService, Depends(get_integration_service)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)


@router.get(
    "/integrations",
    summary="工作空间的集成列表（viewer）",
    description="不含任何凭证；status 与 sync 表示连接状态与数据新鲜度。",
    responses=_read_errors,
)
async def list_integrations(access: Viewer, service: Service) -> Page[IntegrationOut]:
    items = await service.list_integrations(access)
    return Page(items=items, next_cursor=None, total_estimate=len(items))


@router.post(
    "/integrations",
    status_code=status.HTTP_201_CREATED,
    summary="新建 Vikunja 或 GitHub 集成（admin）",
    description="请求体按 provider 区分。保存后状态为 pending，需要测试连接；"
    "每个工作空间最多 1 个同类集成（第 2 个返回 409 LIMIT_EXCEEDED）。"
    "Vikunja 的 base_url 解析到私有地址且不在部署允许列表里时返回 422；"
    "GitHub 的地址固定为 api.github.com，只同步 repositories 中列出的仓库。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_integration(
    access: Admin, context: Context, service: Service, body: AnyIntegrationCreate
) -> IntegrationOut:
    if isinstance(body, GitHubIntegrationCreate):
        raise not_implemented("T-0028")
    return await service.create(context, access, body)


@router.patch(
    "/integrations/{integration_id}",
    summary="修改集成（admin，乐观并发）",
    description="version 不一致时返回 409 VERSION_CONFLICT；"
    "改 base_url 或 api_token 后状态回到 pending。api_token 的前缀必须与集成的 provider 一致；"
    "base_url / public_url 只用于 Vikunja，repositories 只用于 GitHub，不适用时返回 422。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_integration(
    access: Admin,
    context: Context,
    service: Service,
    integration_id: UUID,
    body: IntegrationUpdate,
) -> IntegrationOut:
    return await service.update(context, access, integration_id, body)


@router.post(
    "/integrations/{integration_id}/test",
    summary="测试连接与权限（admin）",
    description="逐项检查可达性、实例版本、Token、任务读权限与 Webhook 注册权限；"
    "不返回外部响应原文。测试本身失败也返回 200，结果在 checks 里。"
    "测试通过后立即做一次全量同步。",
    dependencies=_write,
    responses=_write_errors,
)
async def test_integration(
    access: Admin, context: Context, service: Service, integration_id: UUID
) -> IntegrationTestResult:
    return await service.test(context, access, integration_id)


@router.post(
    "/integrations/{integration_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    summary="排队一次全量同步（admin）",
    description="已有同步在运行时返回 409 SYNC_IN_PROGRESS；集成未测试通过或已停用时返回 409 "
    "INTEGRATION_UNAVAILABLE。",
    dependencies=_write,
    responses=_write_errors,
)
async def sync_integration(
    access: Admin, context: Context, service: Service, integration_id: UUID
) -> SyncRequestOut:
    return await service.request_sync(context, access, integration_id)
