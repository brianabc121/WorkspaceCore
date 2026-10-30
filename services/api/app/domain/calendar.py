"""本地日历的业务：区间查询、新建 / 修改 / 删除、乐观并发与审计。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 5 节。
start_at / end_at 一律存 UTC：全天事件按 timezone 换算成当地 0 点，所以区间查询只需比较这两列。
权限（viewer 读 / member 写、工作空间归属）由路由层先行检查，这里再按工作空间限定每一条查询。
"""

import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.dashboards import version_conflict
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.tasks import decode_cursor, encode_cursor
from app.models import CalendarEvent
from app.schemas.calendar import (
    MAX_QUERY_SPAN,
    CalendarEventCreate,
    CalendarEventOut,
    CalendarEventUpdate,
    check_times,
)
from app.schemas.common import Page


def _invalid(loc: list[str], message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": loc, "msg": message, "type": "value_error"}]},
    )


def _local_midnight(day: date, timezone: str) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def _out(event: CalendarEvent) -> CalendarEventOut:
    return CalendarEventOut.model_validate(event, from_attributes=True)


def _apply_times(
    event: CalendarEvent,
    *,
    all_day: bool,
    start_at: datetime | None,
    end_at: datetime | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str,
) -> None:
    event.all_day = all_day
    event.timezone = timezone
    if all_day:
        if start_date is None or end_date is None:  # check_times 已保证，不会发生
            raise ValueError("全天事件缺少日期")
        event.start_date, event.end_date = start_date, end_date
        event.start_at = _local_midnight(start_date, timezone)
        event.end_at = _local_midnight(end_date, timezone)
    else:
        if start_at is None or end_at is None:
            raise ValueError("定时事件缺少时间")
        event.start_date = event.end_date = None
        event.start_at, event.end_at = start_at.astimezone(UTC), end_at.astimezone(UTC)


class CalendarService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.audit = AuditTrail(db, settings)

    async def list_events(
        self,
        access: WorkspaceAccess,
        *,
        start: datetime,
        end: datetime,
        cursor: str | None,
        limit: int,
    ) -> Page[CalendarEventOut]:
        if end <= start:
            raise _invalid(["query", "end"], "end 必须晚于 start")
        if end - start > MAX_QUERY_SPAN:
            raise _invalid(["query", "end"], "查询区间不能超过 92 天")
        offset = decode_cursor(cursor)
        rows = list(
            await self.db.scalars(
                select(CalendarEvent)
                .where(
                    CalendarEvent.workspace_id == access.workspace.id,
                    CalendarEvent.start_at < end,
                    CalendarEvent.end_at > start,
                )
                .order_by(CalendarEvent.start_at, CalendarEvent.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        return Page(
            items=[_out(e) for e in rows[:limit]],
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    async def create_event(
        self, context: AuthContext, access: WorkspaceAccess, body: CalendarEventCreate
    ) -> CalendarEventOut:
        event = CalendarEvent(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            owner_id=context.user.id,
            title=body.title,
            description=body.description,
            location=body.location,
        )
        _apply_times(
            event,
            all_day=body.all_day,
            start_at=body.start_at,
            end_at=body.end_at,
            start_date=body.start_date,
            end_date=body.end_date,
            timezone=body.timezone,
        )
        self.db.add(event)
        await self.db.flush()
        await self.audit.record(
            "calendar_event.created",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="calendar_event",
            resource_id=event.id,
            metadata={"all_day": body.all_day},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(event)
        return _out(event)

    async def update_event(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        event_id: uuid.UUID,
        body: CalendarEventUpdate,
    ) -> CalendarEventOut:
        event = await self._locked(access, event_id)
        if event.version != body.version:
            raise version_conflict(event.version)
        fields = body.model_fields_set - {"version"}

        # 合并后的时间再校验一次：没改的字段沿用原值，换了事件类型时旧的那一对不再适用
        all_day = body.all_day if body.all_day is not None else event.all_day
        same_kind = all_day == event.all_day
        if all_day:
            start_date = body.start_date or (event.start_date if same_kind else None)
            end_date = body.end_date or (event.end_date if same_kind else None)
            start_at, end_at = body.start_at, body.end_at
        else:
            start_at = body.start_at or (event.start_at if same_kind else None)
            end_at = body.end_at or (event.end_at if same_kind else None)
            start_date, end_date = body.start_date, body.end_date
        try:
            check_times(all_day, start_at, end_at, start_date, end_date)
        except ValueError as error:
            raise _invalid(["body"], str(error)) from None

        for name in ("title", "description", "location"):
            if (value := getattr(body, name)) is not None:
                setattr(event, name, value)
        _apply_times(
            event,
            all_day=all_day,
            start_at=start_at,
            end_at=end_at,
            start_date=start_date,
            end_date=end_date,
            timezone=body.timezone or event.timezone,
        )
        event.version += 1
        await self.audit.record(
            "calendar_event.updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="calendar_event",
            resource_id=event.id,
            metadata={"fields": sorted(fields)},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(event)
        return _out(event)

    async def delete_event(
        self, context: AuthContext, access: WorkspaceAccess, event_id: uuid.UUID, version: int
    ) -> None:
        event = await self._locked(access, event_id)
        if event.version != version:
            raise version_conflict(event.version)
        await self.db.delete(event)
        await self.audit.record(
            "calendar_event.deleted",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="calendar_event",
            resource_id=event_id,
            metadata={},
            client_ip=context.client_ip,
        )
        await self.db.commit()

    async def _locked(self, access: WorkspaceAccess, event_id: uuid.UUID) -> CalendarEvent:
        event = await self.db.scalar(
            select(CalendarEvent)
            .where(CalendarEvent.id == event_id, CalendarEvent.workspace_id == access.workspace.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if event is None:
            raise not_found()
        return event
