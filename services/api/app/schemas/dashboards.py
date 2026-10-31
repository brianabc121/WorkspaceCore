"""仪表盘、Widget 与首页聚合（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 8–9 项、第 6.2 节、第 8.2 节）。

约定见 docs/decisions/2026-10-30-sprint-2-dashboard-contract.md。

写入用的 Widget 模型按 `widget_type` 区分（discriminated union）：每种类型有自己的配置模型，
未知类型或多余字段返回 422。读取时 `widget_type` 是普通字符串、`config` 是原样的对象——
库里可能存在当前版本已不再支持的类型，前端必须按自己的 Registry 再校验一次并安全地显示错误卡片。
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

GRID_COLUMNS = 12
MAX_WIDGETS_PER_DASHBOARD = 50
MAX_DASHBOARDS_PER_WORKSPACE = 20

DashboardTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求必须带上读取时的版本")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 布局


class DesktopPosition(_Strict):
    """桌面端 12 列栅格中的位置与大小。"""

    x: int = Field(ge=0, le=GRID_COLUMNS - 1)
    y: int = Field(ge=0, le=999)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def _fits_grid(self) -> Self:
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError(f"x + w 不能超过 {GRID_COLUMNS}")
        return self


class MobilePosition(_Strict):
    """移动端单列：按 y 从小到大排列。"""

    y: int = Field(ge=0, le=999)
    h: int = Field(ge=1, le=24)


class WidgetLayout(_Strict):
    desktop: DesktopPosition
    mobile: MobilePosition


# ---------------------------------------------------------------- Widget 配置（Registry）


def _http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("只允许 http:// 或 https:// 链接")
    return value


HttpUrlText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    AfterValidator(_http_url),
]


class QuickLink(_Strict):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    url: HttpUrlText


class QuickLinksConfig(_Strict):
    links: list[QuickLink] = Field(default_factory=list, max_length=20)


class NoteConfig(_Strict):
    text: Annotated[str, StringConstraints(max_length=2000)] = Field(
        default="", description="纯文本，前端按文本显示，不解析 HTML / Markdown"
    )


class TodoSummaryConfig(_Strict):
    pass


class TodayTasksConfig(_Strict):
    limit: int = Field(default=5, ge=1, le=20)


class CalendarAgendaConfig(_Strict):
    """Sprint 4：组件自己调用 GET /calendar/events（Sprint 4 契约第 9.2 节）。"""

    days: int = Field(default=7, ge=1, le=14, description="从现在起显示多少天")
    limit: int = Field(default=5, ge=1, le=20)


class RecentDocsConfig(_Strict):
    limit: int = Field(default=5, ge=1, le=20)


class GitHubActivityConfig(_Strict):
    kind: Literal["runs", "pulls", "issues"] = Field(
        default="runs", description="显示最近的 Actions 运行、PR 或 Issue"
    )
    limit: int = Field(default=5, ge=1, le=20)


class ServiceHealthConfig(_Strict):
    """Sprint 5：组件调用 GET /operations/services（Sprint 5 契约第 7.2 节）。"""

    limit: int = Field(default=8, ge=1, le=20)


class ApiLatencyConfig(_Strict):
    """Sprint 5：组件调用 GET /operations/metrics，显示一个延迟分位。"""

    range: Literal["1h", "6h", "24h"] = "1h"
    quantile: Literal["p50", "p95", "p99"] = "p95"


class NotificationsConfig(_Strict):
    """Sprint 5：组件调用 GET /notifications。"""

    limit: int = Field(default=5, ge=1, le=20)
    unread_only: bool = True


class _WidgetBody(_Strict):
    layout: WidgetLayout


class QuickLinksWidgetCreate(_WidgetBody):
    widget_type: Literal["quick_links"]
    config: QuickLinksConfig = Field(default_factory=QuickLinksConfig)


class NoteWidgetCreate(_WidgetBody):
    widget_type: Literal["note"]
    config: NoteConfig = Field(default_factory=NoteConfig)


class TodoSummaryWidgetCreate(_WidgetBody):
    widget_type: Literal["todo_summary"]
    config: TodoSummaryConfig = Field(default_factory=TodoSummaryConfig)


class TodayTasksWidgetCreate(_WidgetBody):
    widget_type: Literal["today_tasks"]
    config: TodayTasksConfig = Field(default_factory=TodayTasksConfig)


class CalendarAgendaWidgetCreate(_WidgetBody):
    widget_type: Literal["calendar_agenda"]
    config: CalendarAgendaConfig = Field(default_factory=CalendarAgendaConfig)


class RecentDocsWidgetCreate(_WidgetBody):
    widget_type: Literal["recent_docs"]
    config: RecentDocsConfig = Field(default_factory=RecentDocsConfig)


class GitHubActivityWidgetCreate(_WidgetBody):
    widget_type: Literal["github_activity"]
    config: GitHubActivityConfig = Field(default_factory=GitHubActivityConfig)


class ServiceHealthWidgetCreate(_WidgetBody):
    widget_type: Literal["service_health"]
    config: ServiceHealthConfig = Field(default_factory=ServiceHealthConfig)


class ApiLatencyWidgetCreate(_WidgetBody):
    widget_type: Literal["api_latency"]
    config: ApiLatencyConfig = Field(default_factory=ApiLatencyConfig)


class NotificationsWidgetCreate(_WidgetBody):
    widget_type: Literal["notifications"]
    config: NotificationsConfig = Field(default_factory=NotificationsConfig)


WidgetCreate = Annotated[
    QuickLinksWidgetCreate
    | NoteWidgetCreate
    | TodoSummaryWidgetCreate
    | TodayTasksWidgetCreate
    | CalendarAgendaWidgetCreate
    | RecentDocsWidgetCreate
    | GitHubActivityWidgetCreate
    | ServiceHealthWidgetCreate
    | ApiLatencyWidgetCreate
    | NotificationsWidgetCreate,
    Field(discriminator="widget_type"),
]


class _WidgetUpdateBody(_Strict):
    version: Version


class QuickLinksWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["quick_links"]
    config: QuickLinksConfig


class NoteWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["note"]
    config: NoteConfig


class TodoSummaryWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["todo_summary"]
    config: TodoSummaryConfig


class TodayTasksWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["today_tasks"]
    config: TodayTasksConfig


class CalendarAgendaWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["calendar_agenda"]
    config: CalendarAgendaConfig


class RecentDocsWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["recent_docs"]
    config: RecentDocsConfig


class GitHubActivityWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["github_activity"]
    config: GitHubActivityConfig


class ServiceHealthWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["service_health"]
    config: ServiceHealthConfig


class ApiLatencyWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["api_latency"]
    config: ApiLatencyConfig


class NotificationsWidgetUpdate(_WidgetUpdateBody):
    widget_type: Literal["notifications"]
    config: NotificationsConfig


WidgetUpdate = Annotated[
    QuickLinksWidgetUpdate
    | NoteWidgetUpdate
    | TodoSummaryWidgetUpdate
    | TodayTasksWidgetUpdate
    | CalendarAgendaWidgetUpdate
    | RecentDocsWidgetUpdate
    | GitHubActivityWidgetUpdate
    | ServiceHealthWidgetUpdate
    | ApiLatencyWidgetUpdate
    | NotificationsWidgetUpdate,
    Field(
        discriminator="widget_type",
        description="修改配置；widget_type 必须与现有类型相同（类型不能修改）",
    ),
]


class WidgetOut(BaseModel):
    id: UUID
    dashboard_id: UUID
    widget_type: str = Field(
        description="当前版本内置：quick_links、note、todo_summary、today_tasks、"
        "calendar_agenda、recent_docs、github_activity、service_health、api_latency、notifications；"
        "也可能是已不再支持的旧类型，前端应显示安全的错误卡片"
    )
    config: dict[str, Any] = Field(description="该类型的配置；前端按自己的 Registry 再校验一次")
    layout: WidgetLayout
    version: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- 仪表盘


class DashboardOut(BaseModel):
    id: UUID
    title: str
    is_default: bool = Field(description="首页使用的仪表盘；每个工作空间恰好一个")
    owner_id: UUID | None = Field(description="创建者；用户被删除后为 null")
    version: int = Field(description="布局、组件增删、标题与默认标记的任何变化都会递增")
    created_at: datetime
    updated_at: datetime


class DashboardCreate(_Strict):
    title: DashboardTitle
    widgets: list[WidgetCreate] = Field(
        default_factory=list,
        max_length=MAX_WIDGETS_PER_DASHBOARD,
        description="可选：创建时一并加入的组件（用于导入布局 JSON），整体在一个事务里完成",
    )


class WidgetPlacement(_Strict):
    widget_id: UUID
    layout: WidgetLayout


class DashboardUpdate(_Strict):
    version: Version
    title: DashboardTitle | None = None
    is_default: Literal[True] | None = Field(
        default=None, description="设为首页；原默认仪表盘会自动取消默认，只能设为 true"
    )
    layout: list[WidgetPlacement] | None = Field(
        default=None,
        max_length=MAX_WIDGETS_PER_DASHBOARD,
        description="保存布局：必须恰好列出该仪表盘当前的全部组件",
    )


# ---------------------------------------------------------------- 首页聚合（规范 6.2）

ModuleState = Literal["ok", "stale", "unavailable", "unauthorized"]


class ModuleStatus(BaseModel):
    status: ModuleState
    as_of: datetime | None = Field(description="数据的时间点；从未成功获取过时为 null")
    retryable: bool = Field(description="稍后重试是否可能恢复")
    reason: str | None = Field(
        default=None,
        description="机器可读的原因，例如 not_configured、timeout、error；不含敏感信息",
    )
    retry_at: datetime | None = Field(
        default=None,
        description="被第三方限流时预计恢复的时间（reason 为 rate_limited）；其余情况为 null",
    )


class OverviewSummary(BaseModel):
    """未知时为 null，不以 0 冒充（规范第 16 节 A17：不把空数据当正常）。"""

    pending_tasks: int | None
    active_projects: int | None
    unread_notifications: int | None


class TaskBrief(BaseModel):
    """今日任务条目；字段在 Sprint 3 接入 Vikunja 时可能扩展（只增不删）。"""

    id: str
    title: str
    due_at: datetime | None
    project_title: str | None
    source_url: str | None


class DashboardOverview(BaseModel):
    dashboard: DashboardOut
    widgets: list[WidgetOut]
    summary: OverviewSummary
    modules: dict[str, ModuleStatus] = Field(
        description="按模块名索引：tasks（Sprint 3）、calendar、knowledge、github（Sprint 4）、"
        "operations、automations（Sprint 5）；单个模块失败只影响对应组件"
    )
    today_tasks: list[TaskBrief]
    generated_at: datetime
