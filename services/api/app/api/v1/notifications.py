"""通知中心（Sprint 5）：当前用户在当前工作空间的通知与已读状态。

用 X-Workspace-Id 指定工作空间，任何成员（含 viewer）都能看、能标记自己的通知。
约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 4 节。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_notification_service,
    workspace_viewer,
)
from app.domain.notifications import NotificationService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.notifications import NotificationMark, NotificationMarkResult, NotificationPage

router = APIRouter(tags=["notifications"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Service = Annotated[NotificationService, Depends(get_notification_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get(
    "/notifications",
    summary="我的通知（按时间倒序）",
    responses=error_responses(401, 404, 422),
)
async def list_notifications(
    access: Viewer,
    service: Service,
    unread_only: bool = False,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> NotificationPage:
    return await service.list(access, unread_only=unread_only, cursor=cursor, limit=limit)


@router.patch(
    "/notifications",
    summary="把通知标为已读（ids 或 all=true）",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 422),
)
async def mark_notifications(
    access: Viewer, service: Service, body: NotificationMark
) -> NotificationMarkResult:
    return await service.mark(access, body)
