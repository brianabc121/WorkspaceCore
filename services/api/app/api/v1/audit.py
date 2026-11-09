"""审计日志（owner / admin）。按 X-Workspace-Id 指定的工作空间返回，新的在前。

T-0197：服务端筛选、单条详情与受控 CSV 导出
（契约 docs/v2/ai-admin-redesign/…-contract.md 第 5 节）。
固定路径 /audit-logs/export 写在 /audit-logs/{entry_id} 之前。没有删除或修改审计记录的接口。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticated, client_ip, get_app_settings, get_db, workspace_admin
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit_query import AuditFilters, AuditQueryService
from app.domain.policy import WorkspaceAccess
from app.schemas.audit import AuditLogView
from app.schemas.common import Page, error_responses

router = APIRouter(tags=["audit"], dependencies=[Security(authenticated)])

Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]


def get_audit_query_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuditQueryService:
    return AuditQueryService(db, settings)


Service = Annotated[AuditQueryService, Depends(get_audit_query_service)]


def audit_filters(
    after: Annotated[
        datetime | None,
        Query(description="起点（含），必须带时区；与 before 一起给出"),
    ] = None,
    before: Annotated[
        datetime | None,
        Query(description="终点（不含），必须带时区，且晚于 after"),
    ] = None,
    actor_id: Annotated[
        UUID | None, Query(description="只看这个操作者；不能与 system_only=true 同时使用")
    ] = None,
    system_only: Annotated[bool, Query(description="只看系统事件（actor_id 为 null）")] = False,
    action: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=128,
            description="事件名精确匹配；不能与 action_prefix 同时使用",
        ),
    ] = None,
    action_prefix: Annotated[
        str | None,
        Query(min_length=1, max_length=128, description="事件名前缀（按字面，% 与 _ 不是通配符）"),
    ] = None,
    resource_type: Annotated[
        str | None, Query(min_length=1, max_length=64, description="对象类型精确匹配")
    ] = None,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="关键词（按字面、不区分大小写）：匹配 action、resource_type、resource_id、"
            "metadata 里的 name / title，以及内置事件的中文名；不搜 IP 哈希与其他 metadata",
        ),
    ] = None,
) -> AuditFilters:
    return AuditFilters(
        after=after,
        before=before,
        actor_id=actor_id,
        system_only=system_only,
        action=action,
        action_prefix=action_prefix,
        resource_type=resource_type,
        q=q,
    )


Filters = Annotated[AuditFilters, Depends(audit_filters)]

_read_errors = error_responses(401, 403, 404, 422)
_CSV = {
    "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
    "description": "UTF-8（带 BOM）CSV：id、created_at_utc、action、action_label、actor_id、"
    "actor_label、resource_type、resource_id、resource_current_name、metadata_json。"
    "以 = + - @、制表符或回车开头的单元格前面加 '，按文本显示；不含 IP、IP 哈希与凭据。"
    "Cache-Control: no-store",
}


@router.get(
    "/audit-logs",
    summary="工作空间审计日志（owner / admin）",
    description="新的在前。所有筛选都在服务端、分页之前生效；next_cursor 绑定工作空间与筛选条件，"
    "换了条件要从第一页重新加载（旧游标 422）。不返回全量总数（total_estimate 为 null）。"
    "列表与详情不写审计。",
    responses=_read_errors,
)
async def list_audit_logs(
    access: Admin,
    service: Service,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[AuditLogView]:
    return await service.list(access, filters, cursor=cursor, limit=limit)


@router.get(
    "/audit-logs/export",
    summary="导出审计日志为 CSV（owner / admin）",
    description="与列表同样的筛选；必须给出 after 与 before，最长 31 天，不接受 cursor 与 limit。"
    "读取请求时刻之前符合条件的记录，最多 10000 行：超出时 422 AUDIT_EXPORT_TOO_LARGE"
    "（不截断），请缩小范围。生成后记一条审计 audit.export_generated"
    "（只有时间范围、行数与过滤摘要，不含搜索词与文件内容）。",
    responses={200: _CSV, **_read_errors},
    response_class=Response,
    response_model=None,
)
async def export_audit_logs(
    request: Request, access: Admin, service: Service, filters: Filters
) -> Response:
    for name in ("cursor", "limit"):
        if name in request.query_params:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "请求参数校验失败",
                details={
                    "errors": [{"loc": ["query", name], "msg": "导出不分页", "type": "value_error"}]
                },
            )
    file = await service.export_csv(
        access, filters, client_ip=client_ip(request, request.app.state.settings)
    )
    return Response(
        content=file.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/audit-logs/{entry_id}",
    summary="一条审计记录（owner / admin）",
    description="不在当前工作空间（含不属于任何工作空间的记录）时 404。",
    responses=_read_errors,
)
async def get_audit_log(access: Admin, service: Service, entry_id: UUID) -> AuditLogView:
    return await service.detail(access, entry_id)
