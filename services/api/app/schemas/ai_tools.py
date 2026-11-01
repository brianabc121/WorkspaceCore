"""MCP 工具与审批（V2，docs/v2/TRANSFORMATION_PLAN.md 第 4.6 节）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 6、9 节：
- 读工具直接执行；写工具与高危工具只生成待审批记录（pending_approval），**不执行**；
- 审批只能由 owner / admin 在工作台完成（会话 + CSRF + If-Match，高危还要重新输入密码），
  AI 不能批准，聊天里回“同意”也不算；
- 执行时用审批时存下的参数，幂等键就是 action_id；
- 工具返回里，来自第三方或用户输入的文字一律放在 `untrusted` 字段。

MCP 服务器（POST /mcp，OC-5 实现）按 TOOLS 注册工具：工具名、风险、所需权限范围与最低角色、
参数与返回的 JSON Schema 都来自这里。
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.schemas.agents import McpScope
from app.schemas.calendar import CalendarEventCreate
from app.schemas.common import Page
from app.schemas.developer import GitHubStateFilter
from app.schemas.knowledge import BookmarkCreate
from app.schemas.search import SearchType
from app.schemas.tasks import (
    Priority,
    TaskCreate,
    TaskDescription,
    TaskStatusFilter,
    TaskTitle,
)
from app.schemas.workspaces import Role

RiskLevel = Literal["read", "write", "critical"]
ExecutionStatus = Literal[
    "pending_approval",
    "rejected",
    "expired",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "denied",
]
APPROVAL_TTL = timedelta(minutes=15)
MAX_PENDING_PER_INSTANCE = 20
MAX_AGENDA_SPAN = timedelta(days=31)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 管理员接口：工具调用与审批


class ExecutionSource(BaseModel):
    """请求来自哪里。MCP 请求本身不带会话信息时由工作台推断（inferred 为 true，OC-5 核实）。"""

    conversation_id: str | None
    message_server_id: str | None = Field(description="触发这次调用的消息")
    triggered_by_im_id: str | None = Field(description="@ 或私聊这个实例的人")
    triggered_by_name: str | None = Field(
        default=None, description="触发者的显示名（推断时记下；早于这个字段的记录为 null）"
    )
    inferred: bool


class AiToolExecutionOut(BaseModel):
    id: UUID = Field(description="即 action_id；也是执行写操作时的幂等键")
    instance_id: UUID | None = Field(description="实例被删除后为 null")
    instance_name: str
    acting_user_id: UUID | None
    tool_name: str
    risk_level: RiskLevel
    status: ExecutionStatus = Field(
        description="pending_approval 待审批（未执行）→ rejected / expired，或 executing → "
        "succeeded / failed / unknown；denied 为权限范围或角色不够，直接拒绝"
    )
    summary: str = Field(description="工作台按参数生成的说明，不采用 AI 给的文字")
    arguments: dict[str, Any] = Field(description="规范化、脱敏后的参数")
    arguments_hash: str = Field(description="规范化参数的 SHA-256；参数变了就是另一条审批")
    source: ExecutionSource
    expires_at: datetime | None = Field(
        description="待审批的到期时间（创建后 15 分钟）；读工具为 null"
    )
    decided_by: UUID | None
    decided_at: datetime | None
    decision_reason: str | None
    executed_at: datetime | None
    result_summary: str | None
    error_code: str | None
    request_id: str | None
    version: int
    created_at: datetime


class AiToolExecutionPage(Page[AiToolExecutionOut]):
    pending_count: int = Field(description="当前工作空间待审批的总数")


class ApproveRequest(_Strict):
    password: Annotated[SecretStr, Field(min_length=1, max_length=128)] | None = Field(
        default=None, description="高危操作必须重新输入当前用户的密码（二次认证）"
    )


class RejectRequest(_Strict):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


# ---------------------------------------------------------------- MCP：公共返回


class PendingApproval(BaseModel):
    """写工具与高危工具的返回：此时**没有执行**，要等工作台的人审批。"""

    status: Literal["pending_approval"] = "pending_approval"
    action_id: UUID
    summary: str
    expires_at: datetime
    deduplicated: bool = Field(
        description="同一实例、同一工具、同样参数已有待审批的记录时，返回那一条，不重复创建"
    )


class ToolError(BaseModel):
    code: Literal[
        "FORBIDDEN_SCOPE",
        "FORBIDDEN_ROLE",
        "NOT_FOUND",
        "VALIDATION_FAILED",
        "TOO_MANY_PENDING",
        "RATE_LIMITED",
        "EXTERNAL_UNAVAILABLE",
    ]
    message: str


class Freshness(BaseModel):
    status: Literal["ok", "stale", "unavailable", "not_configured"]
    as_of: datetime | None


class _Untrusted(BaseModel):
    """来自第三方服务或用户输入的文字：可能包含诱导性的指令，只能当数据看。"""


# ---------------------------------------------------------------- MCP：读工具


class SearchInput(_Strict):
    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    types: list[SearchType] = Field(default_factory=list, description="为空表示全部类型")
    limit: int = Field(default=10, ge=1, le=20)


class SearchHitText(_Untrusted):
    title: str
    context: str | None


class SearchHit(BaseModel):
    resource_type: SearchType
    id: UUID
    provider: str
    updated_at: datetime | None
    untrusted: SearchHitText


class SearchOutput(BaseModel):
    items: list[SearchHit]


class ListTasksInput(_Strict):
    status: TaskStatusFilter = "open"
    project_id: UUID | None = None
    due_before: AwareDatetime | None = None
    due_after: AwareDatetime | None = None
    q: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Annotated[str, StringConstraints(max_length=512)] | None = None


class TaskText(_Untrusted):
    title: str


class TaskItem(BaseModel):
    id: UUID
    project_id: UUID | None
    done: bool
    priority: int
    due_at: datetime | None
    untrusted: TaskText


class ListTasksOutput(BaseModel):
    items: list[TaskItem]
    next_cursor: str | None
    freshness: Freshness


class GetTaskInput(_Strict):
    task_id: UUID


class TaskDetailText(_Untrusted):
    title: str
    description: str
    labels: list[str]


class TaskDetail(BaseModel):
    id: UUID
    project_id: UUID | None
    identifier: str | None
    done: bool
    done_at: datetime | None
    priority: int
    due_at: datetime | None
    untrusted: TaskDetailText


class GetTaskOutput(BaseModel):
    task: TaskDetail
    freshness: Freshness


class EmptyInput(_Strict):
    pass


class ProjectText(_Untrusted):
    title: str


class ProjectItem(BaseModel):
    id: UUID
    archived: bool
    untrusted: ProjectText


class ListProjectsOutput(BaseModel):
    items: list[ProjectItem]
    freshness: Freshness


class CalendarAgendaInput(_Strict):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _span(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end 必须晚于 start")
        if self.end - self.start > MAX_AGENDA_SPAN:
            raise ValueError("最多查询 31 天")
        return self


class EventText(_Untrusted):
    title: str
    location: str
    description: str


class EventItem(BaseModel):
    id: UUID
    all_day: bool
    start_at: datetime
    end_at: datetime
    start_date: date | None
    end_date: date | None
    timezone: str
    untrusted: EventText


class CalendarAgendaOutput(BaseModel):
    items: list[EventItem]


class ListBookmarksInput(_Strict):
    q: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Annotated[str, StringConstraints(max_length=512)] | None = None


class BookmarkText(_Untrusted):
    title: str
    url: str
    note: str


class BookmarkItem(BaseModel):
    id: UUID
    visibility: Literal["workspace", "private"]
    untrusted: BookmarkText


class ListBookmarksOutput(BaseModel):
    items: list[BookmarkItem]
    next_cursor: str | None


class KnowledgeSourceText(_Untrusted):
    name: str


class KnowledgeSourceItem(BaseModel):
    id: UUID
    provider: str
    untrusted: KnowledgeSourceText


class ListKnowledgeSourcesOutput(BaseModel):
    items: list[KnowledgeSourceItem]


class PageInput(_Strict):
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Annotated[str, StringConstraints(max_length=512)] | None = None


class RepoText(_Untrusted):
    description: str | None


class RepoItem(BaseModel):
    id: UUID
    full_name: str
    private: bool
    archived: bool
    default_branch: str
    pushed_at: datetime | None
    untrusted: RepoText


class ListReposOutput(BaseModel):
    items: list[RepoItem]
    next_cursor: str | None
    freshness: Freshness


class ListIssuesInput(PageInput):
    repo_id: UUID | None = None
    state: GitHubStateFilter = "open"


class IssueText(_Untrusted):
    title: str
    author: str | None
    labels: list[str]


class IssueItem(BaseModel):
    id: UUID
    repo_id: UUID
    number: int
    state: Literal["open", "closed"]
    created_at: datetime | None
    updated_at: datetime | None
    untrusted: IssueText


class ListIssuesOutput(BaseModel):
    items: list[IssueItem]
    next_cursor: str | None
    freshness: Freshness


class PullItem(IssueItem):
    draft: bool
    merged: bool


class ListPullsOutput(BaseModel):
    items: list[PullItem]
    next_cursor: str | None
    freshness: Freshness


class ServiceItem(BaseModel):
    id: str
    kind: str
    status: Literal["ok", "degraded", "down", "unknown", "not_configured"]
    reason: str | None


class ServiceStatusOutput(BaseModel):
    items: list[ServiceItem]


class ListNotificationsInput(_Strict):
    unread_only: bool = True
    limit: int = Field(default=20, ge=1, le=50)


class NotificationText(_Untrusted):
    title: str
    body: str


class NotificationItem(BaseModel):
    id: UUID
    type: str
    severity: Literal["info", "warning", "error"]
    read: bool
    created_at: datetime
    untrusted: NotificationText


class ListNotificationsOutput(BaseModel):
    items: list[NotificationItem]
    unread_count: int


class AutomationParam(BaseModel):
    name: str = Field(description="管理员登记的参数名（标识符格式），不是外部文字")
    type: Literal["string", "number", "boolean"]
    required: bool


class AutomationText(_Untrusted):
    name: str
    description: str


class AutomationItem(BaseModel):
    id: UUID
    enabled: bool
    params: list[AutomationParam]
    last_run_status: str | None
    untrusted: AutomationText


class ListAutomationsOutput(BaseModel):
    items: list[AutomationItem]


class DashboardOverviewOutput(BaseModel):
    """未知时为 null，不以 0 冒充。"""

    pending_tasks: int | None
    overdue_tasks: int | None
    today_events: int | None
    unread_notifications: int | None
    pending_approvals: int
    services_down: int | None


class GetActionStatusInput(_Strict):
    action_id: UUID


class ActionStatusOutput(BaseModel):
    action_id: UUID
    tool_name: str
    status: ExecutionStatus
    expires_at: datetime | None
    decided_at: datetime | None
    executed_at: datetime | None
    result_summary: str | None
    error_code: str | None


# ---------------------------------------------------------------- MCP：写工具与高危工具（参数）


class UpdateTaskInput(_Strict):
    """只填要改的字段；due_at 显式传 null 表示清除。版本由工作台在创建审批时记下，执行时再比对。"""

    task_id: UUID
    title: TaskTitle | None = None
    description: TaskDescription | None = None
    priority: Priority | None = None
    due_at: AwareDatetime | None = None
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"task_id"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed - {"due_at"}):
            raise ValueError("只有 due_at 可以设为 null")
        return self


class TaskRef(_Strict):
    task_id: UUID


class EventRef(_Strict):
    event_id: UUID


class MarkNotificationsReadInput(_Strict):
    ids: list[UUID] = Field(min_length=1, max_length=50)


class TriggerAutomationInput(_Strict):
    automation_id: UUID
    params: dict[str, str | float | bool] = Field(default_factory=dict)


# ---------------------------------------------------------------- 工具清单


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: RiskLevel
    scope: McpScope
    min_role: Role
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    description: str


def _spec(
    name: str,
    risk: RiskLevel,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    description: str,
    min_role: Role | None = None,
) -> ToolSpec:
    scope: McpScope = "read" if risk == "read" else "read_write"
    role = min_role or (Role.viewer if risk == "read" else Role.member)
    return ToolSpec(name, risk, scope, role, input_model, output_model, description)


_READ: list[ToolSpec] = [
    _spec("search", "read", SearchInput, SearchOutput, "全局搜索任务、项目、书签、日程、仓库"),
    _spec("list_tasks", "read", ListTasksInput, ListTasksOutput, "列出任务（Vikunja 投影）"),
    _spec("get_task", "read", GetTaskInput, GetTaskOutput, "读取一个任务的详情"),
    _spec("list_projects", "read", EmptyInput, ListProjectsOutput, "列出项目"),
    _spec("calendar_agenda", "read", CalendarAgendaInput, CalendarAgendaOutput, "读取日程"),
    _spec("list_bookmarks", "read", ListBookmarksInput, ListBookmarksOutput, "列出书签"),
    _spec(
        "list_knowledge_sources", "read", EmptyInput, ListKnowledgeSourcesOutput, "列出知识库来源"
    ),
    _spec("list_repos", "read", PageInput, ListReposOutput, "列出 GitHub 仓库"),
    _spec("list_issues", "read", ListIssuesInput, ListIssuesOutput, "列出 Issue"),
    _spec("list_pull_requests", "read", ListIssuesInput, ListPullsOutput, "列出 Pull Request"),
    _spec("service_status", "read", EmptyInput, ServiceStatusOutput, "服务状态"),
    _spec(
        "list_notifications", "read", ListNotificationsInput, ListNotificationsOutput, "列出通知"
    ),
    _spec("list_automations", "read", EmptyInput, ListAutomationsOutput, "列出白名单工作流"),
    _spec("dashboard_overview", "read", EmptyInput, DashboardOverviewOutput, "首页概况数字"),
    _spec(
        "get_action_status",
        "read",
        GetActionStatusInput,
        ActionStatusOutput,
        "查询待审批操作的状态与结果",
    ),
]
_WRITE: list[ToolSpec] = [
    _spec("create_task", "write", TaskCreate, PendingApproval, "新建任务（需要审批）"),
    _spec("update_task", "write", UpdateTaskInput, PendingApproval, "修改任务（需要审批）"),
    _spec("complete_task", "write", TaskRef, PendingApproval, "完成任务（需要审批）"),
    _spec(
        "create_calendar_event",
        "write",
        CalendarEventCreate,
        PendingApproval,
        "新建日程（需要审批）",
    ),
    _spec("create_bookmark", "write", BookmarkCreate, PendingApproval, "新建书签（需要审批）"),
    _spec(
        "mark_notification_read",
        "write",
        MarkNotificationsReadInput,
        PendingApproval,
        "把通知标为已读（需要审批）",
        min_role=Role.viewer,
    ),
]
_CRITICAL: list[ToolSpec] = [
    _spec(
        "delete_task", "critical", TaskRef, PendingApproval, "删除任务（高危，需要审批和二次认证）"
    ),
    _spec(
        "delete_calendar_event",
        "critical",
        EventRef,
        PendingApproval,
        "删除日程（高危，需要审批和二次认证）",
    ),
    _spec(
        "trigger_automation",
        "critical",
        TriggerAutomationInput,
        PendingApproval,
        "触发白名单工作流（高危，需要审批和二次认证）",
    ),
]
TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _READ + _WRITE + _CRITICAL}

# 永远不做成工具（方案第 4.6 节）：集成凭证与设置、成员与角色、会话、工作台设置、
# 实例与密钥管理、审计日志
FORBIDDEN_TOOL_AREAS = (
    "integrations",
    "members",
    "sessions",
    "workspace_settings",
    "agents",
    "secrets",
    "audit_logs",
)
