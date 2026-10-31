"""通知中心与 SSE 事件流（docs/DEVELOPMENT_SPEC.md 第 4、5.1、10 节，Sprint 5）。

约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 4、5 节。
通知只给当前用户看，按工作空间隔离；正文是纯文本，不含 Token、第三方 payload 原文。
SSE 只发轻量的失效通知，客户端收到后重新拉取数据（规范第 4 节）。
"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NotificationSeverity = Literal["info", "warning", "error"]
EventTopic = Literal[
    "overview",
    "tasks",
    "projects",
    "knowledge",
    "calendar",
    "developer",
    "integrations",
    "notifications",
    "operations",
    "automations",
    # V2（OC-1 契约第 11 节）：IM 会话与消息、实例状态、工具调用与审批
    "im",
    "agents",
    "ai",
]
MAX_MARK_IDS = 100


class NotificationOut(BaseModel):
    id: UUID
    type: str = Field(
        description="机器可读的类型，取值会随功能增加，例如 service_down、service_recovered、"
        "integration_unauthorized、integration_rate_limited、automation_failed、"
        "automation_succeeded；前端遇到不认识的类型按普通通知显示"
    )
    severity: NotificationSeverity
    title: str
    body: str = Field(description="纯文本；前端按文本显示，不解析 HTML / Markdown")
    link: str | None = Field(
        default=None, description="点开后跳转的工作台内部路径（以 / 开头），没有时为 null"
    )
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None = Field(default=None, description="下一页游标；没有更多数据时为 null")
    total_estimate: int | None = None
    unread_count: int = Field(description="当前用户在当前工作空间的未读数，与首页 summary 一致")


class NotificationMark(BaseModel):
    """把通知标为已读：给出 ids，或者 all=true 把全部未读标为已读，二者选一。"""

    model_config = ConfigDict(extra="forbid")

    ids: list[UUID] | None = Field(default=None, min_length=1, max_length=MAX_MARK_IDS)
    all: bool | None = Field(default=None, description="true：全部标为已读")

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.ids is None) == (self.all is not True):
            raise ValueError("ids 与 all=true 必须且只能给一个")
        return self


class NotificationMarkResult(BaseModel):
    updated: int = Field(description="这次新标为已读的条数（已经是已读的不计）")
    unread_count: int


class InvalidateEvent(BaseModel):
    """SSE `event: invalidate` 的 data（JSON）。只说明哪些数据变了，不带数据本身。"""

    workspace_id: UUID
    topics: list[EventTopic] = Field(min_length=1, description="需要重新拉取的数据范围")
    reset: bool = Field(
        default=False,
        description="true：无法按 Last-Event-ID 续传（事件已过期或服务重启），"
        "topics 为全部范围，客户端应重新拉取当前页面的全部数据",
    )
    at: datetime
