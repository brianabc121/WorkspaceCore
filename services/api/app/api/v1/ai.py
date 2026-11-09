"""工具调用记录与审批（V2）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 6 节：
- owner / admin 看全部、审批；viewer / member 只看以自己身份执行的记录；
- 批准与拒绝要会话 + CSRF + If-Match（GET 返回的 ETag，即 version）；高危操作还要重新输入密码；
- 实例凭证不能调用这些接口，AI 不能批准。
实现：OC-5（T-0057），领域逻辑在 app/domain/ai_tools.py。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, Security

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_ai_tool_service,
    workspace_admin,
    workspace_viewer,
)
from app.domain.ai_tools import AiToolService, parse_if_match, to_out
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.schemas.ai_tools import (
    AiExecutionSummaryOut,
    AiToolExecutionOut,
    AiToolExecutionPage,
    ApproveRequest,
    ExecutionPeriod,
    ExecutionSort,
    ExecutionStatus,
    RejectRequest,
    RiskLevel,
)
from app.schemas.common import error_responses

router = APIRouter(tags=["ai"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
IfMatch = Annotated[
    str,
    Header(
        alias="If-Match",
        pattern=r'^(W/)?"?[0-9]{1,18}"?$',
        description="GET 返回的 ETag（就是 version）；与当前版本不一致时返回 409 VERSION_CONFLICT",
    ),
]
Risks = Annotated[list[RiskLevel] | None, Query()]
ToolName = Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_]+$")]
Period = Annotated[
    ExecutionPeriod,
    Query(
        description="按 created_at 只看这段时间内发起的调用（相对服务端当前时间，UTC）；"
        "缺省 all 不限"
    ),
]
Service = Annotated[AiToolService, Depends(get_ai_tool_service)]
Context = Annotated[AuthContext, Security(authenticated)]
_write = [Depends(csrf_protected)]


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


@router.get(
    "/ai/tool-executions",
    summary="工具调用记录与待审批（viewer；member 及以下只看自己的）",
    description="sort=expires_asc 按到期时间升序（没有到期时间的排最后，同分按创建时间、ID 升序），"
    "缺省 created_desc 按创建时间倒序。next_cursor 绑定当前的筛选、时间范围、排序与用户，"
    "换了条件再用旧游标返回 422；不带 period / sort 时旧格式游标仍可用。"
    "pending_count 是当前用户可见范围内未过期的待审批总数，不随筛选变化。",
    responses=error_responses(401, 404, 422),
)
async def list_tool_executions(
    access: Viewer,
    service: Service,
    status_filter: Annotated[list[ExecutionStatus] | None, Query(alias="status")] = None,
    risk_level: Risks = None,
    instance_id: UUID | None = None,
    tool_name: ToolName = None,
    period: Period = "all",
    sort: Annotated[ExecutionSort, Query(description="排序，缺省 created_desc")] = "created_desc",
    cursor: Cursor = None,
    limit: Limit = 50,
) -> AiToolExecutionPage:
    return await service.list(
        access,
        statuses=list(status_filter) if status_filter else None,
        risks=list(risk_level) if risk_level else None,
        instance_id=instance_id,
        tool_name=tool_name,
        cursor=cursor,
        limit=limit,
        period=period,
        sort=sort,
    )


# 静态路径要在 /{execution_id} 之前注册
@router.get(
    "/ai/tool-executions/summary",
    summary="审批汇总与可选筛选项（viewer；member 及以下只算自己的）",
    description="待审批、3 分钟内到期、高危待审批与执行异常（failed / unknown）的数量，"
    "都只算 period 内发起、并符合 instance_id / risk_level / tool_name 的调用；"
    "不接收 status。instances / tools 只按可见范围与 period 给出，不受其他条件影响。"
    "只读：不批准、不执行，也不触发过期清理（已到期的按 as_of 排除）。",
    responses=error_responses(401, 404, 422),
)
async def get_tool_execution_summary(
    access: Viewer,
    service: Service,
    period: Period = "all",
    risk_level: Risks = None,
    instance_id: UUID | None = None,
    tool_name: ToolName = None,
) -> AiExecutionSummaryOut:
    return await service.summary(
        access,
        period=period,
        instance_id=instance_id,
        risks=list(risk_level) if risk_level else None,
        tool_name=tool_name,
    )


@router.get(
    "/ai/tool-executions/{execution_id}",
    summary="一次工具调用的详情（响应头 ETag 即 version）",
    responses=error_responses(401, 404, 422),
)
async def get_tool_execution(
    access: Viewer, service: Service, execution_id: UUID, response: Response
) -> AiToolExecutionOut:
    row = await service.get(access, execution_id)
    _etag(response, row.version)
    return to_out(row)


@router.post(
    "/ai/tool-executions/{execution_id}/approve",
    summary="批准并执行（owner / admin）",
    description="只有 pending_approval 且未过期的记录能批准（否则 409 EXECUTION_NOT_PENDING）。"
    "高危操作没有带密码返回 403 STEP_UP_REQUIRED，密码错误返回 403 STEP_UP_FAILED。"
    "执行用审批时存下的参数，幂等键是这条记录的 ID。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 429),
)
async def approve_tool_execution(
    access: Admin,
    context: Context,
    service: Service,
    execution_id: UUID,
    if_match: IfMatch,
    body: ApproveRequest,
    response: Response,
) -> AiToolExecutionOut:
    row = await service.approve(context, access, execution_id, parse_if_match(if_match), body)
    _etag(response, row.version)
    return to_out(row)


@router.post(
    "/ai/tool-executions/{execution_id}/reject",
    summary="拒绝（owner / admin）",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def reject_tool_execution(
    access: Admin,
    context: Context,
    service: Service,
    execution_id: UUID,
    if_match: IfMatch,
    body: RejectRequest,
    response: Response,
) -> AiToolExecutionOut:
    row = await service.reject(context, access, execution_id, parse_if_match(if_match), body.reason)
    _etag(response, row.version)
    return to_out(row)
