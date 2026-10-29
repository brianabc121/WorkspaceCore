"""审计日志（owner / admin）。按 X-Workspace-Id 指定的工作空间返回，新的在前。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import authenticated, workspace_context
from app.core.errors import not_implemented
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page, error_responses

router = APIRouter(tags=["audit"])


@router.get(
    "/audit-logs",
    summary="工作空间审计日志（owner / admin）",
    dependencies=[Security(authenticated)],
    responses=error_responses(401, 403, 404, 422, 501),
)
async def list_audit_logs(
    workspace_id: Annotated[UUID, Depends(workspace_context)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[AuditLogOut]:
    raise not_implemented("T-0011")
