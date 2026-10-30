"""本地日历（Sprint 4）：工作台是主源，写入在一个事务里完成，不需要 Idempotency-Key。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可读，member 及以上可写。约定见
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 5 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Security, status
from pydantic import AwareDatetime

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_calendar_service,
    workspace_member,
    workspace_viewer,
)
from app.domain.auth import AuthContext
from app.domain.calendar import CalendarService
from app.domain.policy import WorkspaceAccess
from app.schemas.calendar import CalendarEventCreate, CalendarEventOut, CalendarEventUpdate, Version
from app.schemas.common import Page, error_responses

router = APIRouter(tags=["calendar"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[CalendarService, Depends(get_calendar_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)


@router.get(
    "/calendar/events",
    summary="区间内的日程（viewer）",
    description="返回与 [start, end) 有重叠的事件，按开始时间排序；start、end 必须带时区，"
    "跨度不超过 92 天，否则返回 422。",
    responses=_read_errors,
)
async def list_events(
    access: Viewer,
    service: Service,
    start: Annotated[AwareDatetime, Query()],
    end: Annotated[AwareDatetime, Query()],
    cursor: Cursor = None,
    limit: Limit = 30,
) -> Page[CalendarEventOut]:
    return await service.list_events(access, start=start, end=end, cursor=cursor, limit=limit)


@router.post(
    "/calendar/events",
    status_code=status.HTTP_201_CREATED,
    summary="新建日程（member）",
    description="定时事件填 start_at / end_at（带时区），"
    "全天事件填 start_date / end_date（结束不含）；"
    "V1 不支持重复事件。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_event(
    access: Member, context: Context, service: Service, body: CalendarEventCreate
) -> CalendarEventOut:
    return await service.create_event(context, access, body)


@router.patch(
    "/calendar/events/{event_id}",
    summary="修改日程（member，乐观并发）",
    dependencies=_write,
    responses=_write_errors,
)
async def update_event(
    access: Member, context: Context, service: Service, event_id: UUID, body: CalendarEventUpdate
) -> CalendarEventOut:
    return await service.update_event(context, access, event_id, body)


@router.delete(
    "/calendar/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除日程（member，前端先二次确认）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_event(
    access: Member,
    context: Context,
    service: Service,
    event_id: UUID,
    version: Annotated[Version, Query()],
) -> Response:
    await service.delete_event(context, access, event_id, version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
