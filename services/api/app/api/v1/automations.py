"""自动化（Sprint 5）：n8n 连接、白名单工作流、受控触发与执行记录。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可读，member 可触发（经策略校验），
admin 管理连接与白名单。n8n 的 API Key 与共享密钥只写不读。约定见
docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 6 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, Security, status

from app.api.deps import (
    authenticated,
    csrf_protected,
    workspace_admin,
    workspace_member,
    workspace_viewer,
)
from app.core.errors import not_implemented
from app.domain.policy import WorkspaceAccess
from app.schemas.automations import (
    AutomationConnectionOut,
    AutomationConnectionUpsert,
    AutomationCreate,
    AutomationOut,
    AutomationPage,
    AutomationRunOut,
    AutomationRunPage,
    AutomationUpdate,
    TriggerRequest,
)
from app.schemas.common import error_responses
from app.schemas.integrations import IntegrationTestResult

router = APIRouter(tags=["automations"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description="每次用户提交生成一个新的 UUID；网络重试与查询结果时复用同一个",
    ),
]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_admin_errors = error_responses(401, 403, 404, 409, 422)


# ---------------------------------------------------------------- n8n 连接（admin）
# 固定路径的接口要写在 /automations/{automation_id} 之前


@router.get(
    "/automations/connection",
    summary="n8n 连接（admin）",
    description="没有配置时 configured 为 false；API Key 与共享密钥从不返回。",
    responses=error_responses(401, 403, 404, 422),
)
async def get_automation_connection(access: Admin) -> AutomationConnectionOut:
    raise not_implemented("T-0037")


@router.put(
    "/automations/connection",
    summary="新建或修改 n8n 连接（admin，乐观并发）",
    dependencies=_write,
    responses=_admin_errors,
)
async def put_automation_connection(
    access: Admin, body: AutomationConnectionUpsert
) -> AutomationConnectionOut:
    raise not_implemented("T-0037")


@router.post(
    "/automations/connection/test",
    summary="测试 n8n 连接（admin）",
    description="检查可达（/healthz/readiness）、版本与 API Key（只读 scope）；"
    "不会触发任何工作流。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def test_automation_connection(access: Admin) -> IntegrationTestResult:
    raise not_implemented("T-0037")


# ---------------------------------------------------------------- 白名单工作流


@router.get(
    "/automations",
    summary="白名单工作流与最近一次执行（viewer）",
    responses=_read_errors,
)
async def list_automations(
    access: Viewer, cursor: Cursor = None, limit: Limit = 50
) -> AutomationPage:
    raise not_implemented("T-0037")


@router.post(
    "/automations",
    status_code=status.HTTP_201_CREATED,
    summary="登记白名单工作流（admin）",
    description="工作台会到 n8n 核对 workflow_id 存在；未激活的工作流可以登记，但不能触发。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 502, 503),
)
async def create_automation(access: Admin, body: AutomationCreate) -> AutomationOut:
    raise not_implemented("T-0037")


@router.patch(
    "/automations/{automation_id}",
    summary="修改白名单工作流（admin，乐观并发）",
    dependencies=_write,
    responses=_admin_errors,
)
async def update_automation(
    automation_id: UUID, access: Admin, body: AutomationUpdate
) -> AutomationOut:
    raise not_implemented("T-0037")


@router.delete(
    "/automations/{automation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="移出白名单（admin，乐观并发；执行记录保留）",
    dependencies=_write,
    responses=_admin_errors,
)
async def delete_automation(
    automation_id: UUID,
    access: Admin,
    version: Annotated[int, Query(ge=1, description="读取时的版本")],
) -> None:
    raise not_implemented("T-0037")


# ---------------------------------------------------------------- 触发与执行记录


@router.post(
    "/automations/{automation_id}/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    summary="触发白名单工作流（member，经策略校验，必须带 Idempotency-Key）",
    description="同一个 Idempotency-Key 的重复请求原样返回第一次的执行记录，不会重复触发。"
    "触发请求的结果未知时执行记录为 unknown，由对账确认。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 502, 503),
)
async def trigger_automation(
    automation_id: UUID,
    access: Member,
    key: IdempotencyKey,
    body: TriggerRequest,
) -> AutomationRunOut:
    raise not_implemented("T-0037")


@router.get(
    "/automations/{automation_id}/runs",
    summary="执行记录（viewer，按时间倒序）",
    responses=_read_errors,
)
async def list_automation_runs(
    automation_id: UUID, access: Viewer, cursor: Cursor = None, limit: Limit = 30
) -> AutomationRunPage:
    raise not_implemented("T-0037")
