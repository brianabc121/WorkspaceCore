"""通知中心（Sprint 5）：当前用户在当前工作空间的通知与已读状态。

用 X-Workspace-Id 指定工作空间，任何成员（含 viewer）都能看、能标记自己的通知。
约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 4 节。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import authenticated, csrf_protected, workspace_viewer
from app.core.errors import not_implemented
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.notifications import NotificationMark, NotificationMarkResult, NotificationPage

router = APIRouter(tags=["notifications"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get(
    "/notifications",
    summary="我的通知（按时间倒序）",
    responses=error_responses(401, 404, 422),
)
async def list_notifications(
    access: Viewer,
    unread_only: bool = False,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> NotificationPage:
    raise not_implemented("T-0036")


@router.patch(
    "/notifications",
    summary="把通知标为已读（ids 或 all=true）",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 422),
)
async def mark_notifications(access: Viewer, body: NotificationMark) -> NotificationMarkResult:
    raise not_implemented("T-0036")
