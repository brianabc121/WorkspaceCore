"""本地日历（docs/DEVELOPMENT_SPEC.md 第 4.2 节、第 5.1 节第 13 项）：工作台是主源。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 5 节：定时事件用带时区的
start_at / end_at，全天事件用 start_date / end_date（结束不含）；V1 不支持重复事件（rrule）。
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

MAX_EVENT_SPAN = timedelta(days=92)
MAX_QUERY_SPAN = timedelta(days=92)

EventTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
EventDescription = Annotated[
    str, StringConstraints(max_length=5000), Field(description="纯文本；前端按文本显示")
]
EventLocation = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求必须带上读取时的版本")]


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("请使用 IANA 时区名，例如 Asia/Shanghai") from error
    return value


TimeZoneName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    AfterValidator(_timezone),
    Field(description="IANA 时区名，例如 Asia/Shanghai；全天事件按它换算，前端按它显示"),
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def check_times(
    all_day: bool,
    start_at: datetime | None,
    end_at: datetime | None,
    start_date: date | None,
    end_date: date | None,
) -> None:
    """一种时间字段成对出现、另一种不出现；结束晚于开始，持续不超过 92 天。

    修改时服务端对合并后的结果再调用一次。
    """
    if all_day:
        if start_date is None or end_date is None or start_at is not None or end_at is not None:
            raise ValueError("全天事件只填 start_date 与 end_date")
        span = end_date - start_date
    else:
        if start_at is None or end_at is None or start_date is not None or end_date is not None:
            raise ValueError("定时事件只填 start_at 与 end_at")
        span = end_at - start_at
    if span <= timedelta(0):
        raise ValueError("结束必须晚于开始")
    if span > MAX_EVENT_SPAN:
        raise ValueError("事件不能超过 92 天")


class CalendarEventOut(BaseModel):
    id: UUID
    title: str
    description: str = Field(description="纯文本；前端按文本显示")
    location: str
    all_day: bool
    start_at: datetime = Field(description="开始（UTC）；全天事件为 timezone 当地 0 点")
    end_at: datetime = Field(description="结束（UTC，不含）")
    start_date: date | None = Field(description="全天事件的开始日期；定时事件为 null")
    end_date: date | None = Field(description="全天事件的结束日期（不含）；定时事件为 null")
    timezone: str
    owner_id: UUID | None = Field(description="创建者；用户被删除后为 null")
    version: int
    created_at: datetime
    updated_at: datetime


class CalendarEventCreate(_Strict):
    title: EventTitle
    description: EventDescription = ""
    location: EventLocation = ""
    timezone: TimeZoneName
    all_day: bool = False
    start_at: AwareDatetime | None = Field(default=None, description="定时事件的开始（带时区）")
    end_at: AwareDatetime | None = Field(default=None, description="定时事件的结束（带时区，不含）")
    start_date: date | None = Field(default=None, description="全天事件的开始日期")
    end_date: date | None = Field(default=None, description="全天事件的结束日期（不含）")

    @model_validator(mode="after")
    def _times(self) -> Self:
        check_times(self.all_day, self.start_at, self.end_at, self.start_date, self.end_date)
        return self


class CalendarEventUpdate(_Strict):
    """只发送要改的字段，都不能为 null；改 all_day 时同时给出对应的一对时间。

    合并后的结果由服务端再校验。
    """

    version: Version
    title: EventTitle | None = None
    description: EventDescription | None = None
    location: EventLocation | None = None
    timezone: TimeZoneName | None = None
    all_day: bool | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        if "all_day" in changed:
            pair = {"start_date", "end_date"} if self.all_day else {"start_at", "end_at"}
            if not pair <= changed:
                raise ValueError("修改 all_day 时要同时给出对应的开始与结束")
        return self
