"""审计日志（owner / admin）。按 X-Workspace-Id 指定的工作空间返回，新的在前。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticated, get_db, workspace_admin
from app.domain import audit
from app.domain.policy import WorkspaceAccess
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page, error_responses

router = APIRouter(tags=["audit"])


@router.get(
    "/audit-logs",
    summary="工作空间审计日志（owner / admin）",
    dependencies=[Security(authenticated)],
    responses=error_responses(401, 403, 404, 422),
)
async def list_audit_logs(
    access: Annotated[WorkspaceAccess, Depends(workspace_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[AuditLogOut]:
    return await audit.list_page(db, access.workspace.id, limit=limit, cursor=cursor)
