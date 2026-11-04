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
from app.schemas.knowledge_tools import DESCRIPTIONS as KNOWLEDGE_DESCRIPTIONS
from app.schemas.knowledge_tools import (
    KnowledgeBriefInput,
    KnowledgeBriefOutput,
    KnowledgeDeleteInput,
    KnowledgeDeleteOutput,
    KnowledgeGetInput,
    KnowledgeGetOutput,
    KnowledgeListInput,
    KnowledgeListOutput,
    KnowledgeSearchInput,
    KnowledgeSearchOutput,
    KnowledgeWriteInput,
    KnowledgeWriteOutput,
)
from app.schemas.search import AgentSearchType
from app.schemas.tasks import (
    Priority,
    TaskCreate,
    TaskDescription,
    TaskStatusFilter,
    TaskTitle,
)
from app.schemas.workspaces import Role

# knowledge：知识库工具，直接执行、不审批（docs/v2/KNOWLEDGE_PLAN.md 3.7，人类 2026-11-03 决定）
# direct：领取 / 放弃 / 完成任务，直接执行、不审批
# （docs/v2/AGENT_OPS_PLAN.md 第 7 节，人类 2026-11-04 决定）
RiskLevel = Literal["read", "write", "critical", "knowledge", "direct"]
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
    types: list[AgentSearchType] = Field(
        default_factory=list, description="为空表示全部类型；知识库笔记用 knowledge_search"
    )
    limit: int = Field(default=10, ge=1, le=20)


class SearchHitText(_Untrusted):
    title: str
    context: str | None


class SearchHit(BaseModel):
    resource_type: AgentSearchType
    id: UUID
    provider: str
    updated_at: datetime | None
    untrusted: SearchHitText


class SearchOutput(BaseModel):
    items: list[SearchHit]


class ListTasksInput(_Strict):
    status: TaskStatusFilter = "open"
    project_id: UUID | None = None
    assigned_to_me: bool = Field(default=False, description="只看执行者是你的任务")
    claimable: bool = Field(
        default=False, description="只看你现在能用 claim_task 领取的任务（可自由接取、在范围内）"
    )
    due_before: AwareDatetime | None = None
    due_after: AwareDatetime | None = None
    q: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Annotated[str, StringConstraints(max_length=512)] | None = None


class TaskText(_Untrusted):
    title: str
    assignee_name: str | None = Field(
        default=None, description="执行者的显示名（成员只给显示名）；没有执行者时为 null"
    )


class TaskClaim(BaseModel):
    """接取方式（T-0111）。"""

    mode: Literal["assigned", "open"] = Field(
        description="assigned 由管理员指派；open 可自由接取（没有执行者时用 claim_task 领取）"
    )
    scope: Literal["all", "project"] = Field(
        description="自由接取的范围：全部智能体 / 只限项目成员"
    )
    claimable: bool = Field(description="你现在能领取（可自由接取、没有执行者、没完成、在范围内）")


class TaskItem(BaseModel):
    id: UUID
    project_id: UUID | None
    done: bool
    priority: int
    due_at: datetime | None
    assignee: Literal["me", "member", "instance", "none"] = Field(
        description="执行者：me 是你自己；member 是工作空间成员；instance 是别的智能体；none 没有"
    )
    claim: TaskClaim
    planned_start_at: datetime | None
    started_at: datetime | None
    running: bool = Field(description="现在有正在执行的一段")
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
    assignee_name: str | None = None
    last_reopen_reason: str | None = Field(
        default=None, description="最近一次被管理员打回的原因；没被打回过为 null"
    )


class TaskDetail(BaseModel):
    id: UUID
    project_id: UUID | None
    done: bool
    done_at: datetime | None
    priority: int
    due_at: datetime | None
    assignee: Literal["me", "member", "instance", "none"]
    claim: TaskClaim
    planned_start_at: datetime | None
    started_at: datetime | None
    running: bool
    untrusted: TaskDetailText


class TaskBrief(BaseModel):
    """任务所在项目的简报（T-0100，知识库方案 3.9 第 1 条）。"""

    project_id: UUID
    version: int = Field(description="简报的最新版本")
    read_version: int | None = Field(description="你读过的版本；没读过为 null")
    unread: bool = Field(description="为 true 时，先用 knowledge_brief 读最新的简报再做这个任务")


class GetTaskOutput(BaseModel):
    task: TaskDetail
    brief: TaskBrief | None = Field(
        default=None, description="任务所在项目有简报、而且你参与这个项目时才有"
    )
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


class DueTaskText(_Untrusted):
    title: str
    project: str


class DueTaskItem(BaseModel):
    """这段时间里有截止时间、还没完成的任务（日历上的待办，T-0111）。"""

    id: UUID
    project_id: UUID
    due_at: datetime
    assigned_to_me: bool
    claimable: bool
    untrusted: DueTaskText


class CalendarAgendaOutput(BaseModel):
    items: list[EventItem]
    due_tasks: list[DueTaskItem] = Field(
        description="这段时间里有截止时间、还没完成的任务（最多 100 个，按截止时间排序）"
    )


class HostDiskStatus(BaseModel):
    total: int
    free: int
    used_percent: float


class HostDay(BaseModel):
    """最近 24 小时的概况（每小时平均里的最高值）。"""

    cpu_max: float | None
    load_max: float | None
    mem_max: float | None
    disk_max: float | None = Field(description="两个分区里较高的已用百分比")


class GetHostStatusOutput(BaseModel):
    """你自己所在服务器的状态（T-0113）：整台服务器的数值，不是只有你的容器。"""

    reported: bool = Field(description="收到过上报；false 时让管理员重新部署接入包")
    reported_at: datetime | None
    stale: bool = Field(description="超过 3 分钟没有上报")
    cores: int | None
    cpu_percent: float | None
    load: list[float] | None = Field(description="1 / 5 / 15 分钟负载")
    mem_total: int | None
    mem_available: int | None
    data_disk: HostDiskStatus | None = Field(description="接入包目录（会话、记忆）所在的分区")
    root_disk: HostDiskStatus | None = Field(description="Docker 数据目录所在的分区")
    rx_rate: int | None = Field(description="下载速率（字节 / 秒）")
    tx_rate: int | None = Field(description="上传速率（字节 / 秒）")
    last_24h: HostDay
    alerts: list[Literal["disk_low", "memory_low", "load_high", "not_reporting"]]


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


class ReleaseTaskInput(_Strict):
    task_id: UUID
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] = Field(description="为什么放弃（写进任务的动态，管理员能看到）")


class CompleteTaskInput(_Strict):
    task_id: UUID
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = Field(
        default="", description="完成说明（可选，写进任务的动态）"
    )


class CreateProjectInput(_Strict):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    instance_ids: list[UUID] = Field(
        default_factory=list,
        max_length=20,
        description="一开始就拉进项目的其他智能体（list_peers 里的 id）；你自己会自动加入",
    )


class AddProjectAgentsInput(_Strict):
    project_id: UUID = Field(description="你自己建的项目")
    instance_ids: list[UUID] = Field(min_length=1, max_length=20)


class CreateProjectOutput(BaseModel):
    status: Literal["created"] = "created"
    project_id: UUID
    agents: list[UUID] = Field(description="项目的智能体成员（含你自己）")
    im_group: Literal["none", "pending", "ready"] = Field(
        description="项目群：ready 已建好；pending 正在建（OpenIM 不可用时稍后重试）；"
        "none 没有部署 IM"
    )


class AddProjectAgentsOutput(BaseModel):
    project_id: UUID
    added: list[UUID] = Field(description="这次新加入的智能体")
    already: list[UUID] = Field(description="之前就是成员的智能体")


class AgentText(_Untrusted):
    name: str
    description: str


class AgentItem(BaseModel):
    id: UUID
    status: Literal["pending", "online", "offline", "disabled"]
    idle: bool = Field(description="现在没有正在执行的任务")
    can_write: bool = Field(description="scope 为 read_write（能领取、完成任务）")
    untrusted: AgentText


class ListAgentsOutput(BaseModel):
    items: list[AgentItem]


# ---------------------------------------------------------------- IM 会话目录（T-0117）

# im_send 等工具的目标：user:<IM 用户 ID>（本工作空间的管理员）或 group:<群 ID>
ImTarget = Annotated[
    str, StringConstraints(min_length=6, max_length=72, pattern=r"^(user|group):[A-Za-z0-9_]+$")
]


class ImNameText(BaseModel):
    name: str


class ImMe(BaseModel):
    im_user_id: str
    im_ready: bool = Field(description="IM 账号已就绪；为 false 时还不能收发消息")
    untrusted: ImNameText


class ImDirectTarget(BaseModel):
    target: str = Field(description="传给 im_send 的目标")
    im_user_id: str
    role: Literal["owner", "admin"]
    conversation_id: str
    last_message_at: datetime | None
    replies_reach_you: bool | None = Field(
        description="他回你的私聊你收不收得到（在不在接入包的白名单里）；null 表示无法判断"
    )
    hint: str | None = Field(default=None, description="工作台给的提示（不是用户写的）")
    untrusted: ImNameText


class ImGroupMemberText(BaseModel):
    name: str


class ImGroupMemberItem(BaseModel):
    im_user_id: str
    kind: Literal["admin", "agent"]
    is_you: bool
    untrusted: ImGroupMemberText


class ImGroupRules(BaseModel):
    relay_enabled: bool = Field(description="允许智能体在群里 @ 其他智能体（接力）")
    max_per_minute: int = Field(description="每个智能体在这个群里每分钟最多发几条")
    max_agent_streak: int = Field(description="管理员不发言时，智能体最多连续发几条，之后暂停")


class ImGroupState(BaseModel):
    paused: bool = Field(description="智能体连续发言已达上限，暂停中；管理员发言后恢复")
    agent_streak: int
    remaining_this_minute: int


class ImGroupText(BaseModel):
    name: str
    project_title: str | None


class ImGroupTarget(BaseModel):
    target: str = Field(description="传给 im_send 的目标")
    group_id: str
    conversation_id: str
    project_id: UUID | None
    rules: ImGroupRules
    state: ImGroupState
    members: list[ImGroupMemberItem]
    mention_required: bool = Field(
        True, description="群里只有 @ 你的消息你才会收到；要别人回你，就 @ 他"
    )
    last_message_at: datetime | None
    untrusted: ImGroupText


class ImListTargetsInput(_Strict):
    kind: Literal["all", "direct", "group"] = "all"
    query: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = Field(
        default=None, description="按名字筛选（人名、群名、项目名）"
    )


class ImListTargetsOutput(BaseModel):
    me: ImMe
    direct: list[ImDirectTarget] = Field(description="能私聊的人：本工作空间的管理员")
    groups: list[ImGroupTarget] = Field(description="你现在所在的群")
    recent_rejections: int = Field(
        0, description="最近 24 小时你发的消息被拒收的条数（详情用 im_outbox）"
    )
    sends_left_this_hour: int = Field(description="这个小时还能用 im_send 发几条")
    notes: list[str]


class ImReadMessagesInput(_Strict):
    target: ImTarget
    limit: int = Field(20, ge=1, le=50)
    before: UUID | None = Field(default=None, description="翻页：只读这条消息之前的")


class ImAttachmentText(BaseModel):
    name: str | None


class ImAttachment(BaseModel):
    kind: Literal["image", "file", "video"]
    ref: str | None = Field(description="im_media:<ID>，可以在 im_send 里转发；没有时为 null")
    mime: str | None
    size: int | None
    untrusted: ImAttachmentText


class ImMessageText(BaseModel):
    sender_name: str
    text: str | None
    truncated: bool = Field(description="正文超过 2000 字，只给了开头")


class ImMessageItem(BaseModel):
    id: UUID
    sent_at: datetime
    sender_im_id: str
    sender_kind: Literal["admin", "agent", "system"]
    is_you: bool
    kind: Literal["text", "image", "file", "video", "other"]
    mentions: list[str] = Field(description="这条消息 @ 了谁（IM 用户 ID）")
    status: str
    attachments: list[ImAttachment]
    untrusted: ImMessageText


class ImReadMessagesOutput(BaseModel):
    target: str
    conversation_id: str
    items: list[ImMessageItem] = Field(description="按时间从早到晚")
    has_more: bool
    next_before: UUID | None = Field(description="还有更早的消息时，传给 before 继续读")


# ---------------------------------------------------------------- 主动发消息（T-0118）

OutboxStatus = Literal[
    "scheduled", "queued", "sending", "sent", "rejected", "failed", "unknown", "cancelled"
]
ClientKey = Annotated[
    str, StringConstraints(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
]


class ImSendInput(_Strict):
    target: ImTarget = Field(description="im_list_targets 给的 target")
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None
    mention: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        default_factory=list,
        max_length=20,
        description="群里要 @ 的人（IM 用户 ID，来自 im_list_targets 的成员）；私聊必须为空",
    )
    send_at: AwareDatetime | None = Field(
        default=None, description="定时发送（最早 1 分钟后，最远 30 天）；不给就立即发"
    )
    client_key: ClientKey | None = Field(
        default=None,
        description="幂等键：重试同一次发送时用同一个键，不会发出两次；不给时按内容自动生成",
    )
    if_rate_limited: Literal["queue", "fail"] = Field(
        "queue", description="遇到每分钟上限时：queue 排队到限额恢复后发（最多等 10 分钟）"
    )


class ImSendResultItem(BaseModel):
    message_id: UUID = Field(description="发件记录 ID（用于 im_outbox、im_cancel）")
    part: int
    kind: Literal["text", "image", "file", "video"]
    status: OutboxStatus
    sent_at: datetime | None
    next_attempt_at: datetime | None = Field(description="排队、定时、重试中的预计时间")
    code: int | None = Field(description="被拒收时的拒收码")
    reason: str | None
    retryable: bool = Field(description="换个时间或改一下内容后可以再发")
    retry_after_seconds: int | None


class ImSendOutput(BaseModel):
    status: Literal["sent", "queued", "scheduled", "rejected", "failed", "partial"]
    target: str
    client_key: str
    replayed: bool = Field(description="同一个幂等键之前已经发过，这里返回的是那一次的结果")
    messages: list[ImSendResultItem]
    hint: str | None = None


class ImCancelInput(_Strict):
    message_ids: list[UUID] = Field(min_length=1, max_length=20)


class ImCancelOutput(BaseModel):
    cancelled: list[UUID]
    not_cancellable: list[UUID] = Field(description="已经发出、已结束或不是你的")


class ImOutboxInput(_Strict):
    status: Literal["all", "pending", "sent", "rejected", "failed"] = "all"
    since: AwareDatetime | None = None
    limit: int = Field(20, ge=1, le=50)


class ImOutboxText(BaseModel):
    text: str | None


class ImOutboxItem(BaseModel):
    message_id: UUID
    target: str
    kind: Literal["text", "image", "file", "video"]
    status: OutboxStatus
    source: Literal["tool", "reply_retry", "wakeup"]
    created_at: datetime
    sent_at: datetime | None
    next_attempt_at: datetime | None
    code: int | None
    reason: str | None
    untrusted: ImOutboxText


class ImRejectedReply(BaseModel):
    conversation_id: str
    code: int
    reason: str
    retried: bool = Field(description="工作台已在限额恢复后补发")
    created_at: datetime
    untrusted: ImOutboxText


class ImOutboxOutput(BaseModel):
    items: list[ImOutboxItem] = Field(description="你经 im_send 发的消息，最近的在前")
    rejected_replies: list[ImRejectedReply] = Field(
        description="最近 7 天你的自动回复被拒收的记录，最近的在前"
    )


class ClaimTaskOutput(BaseModel):
    status: Literal["claimed"] = "claimed"
    task_id: UUID
    already_yours: bool = Field(description="之前已经是你的任务（重复调用）")
    joined_project: bool = Field(
        description="这次领取让你加入了任务所在的项目（能读写项目知识库，并被拉进项目群）"
    )
    next_step: str


class ReleaseTaskOutput(BaseModel):
    status: Literal["released"] = "released"
    task_id: UUID


class CompleteTaskOutput(BaseModel):
    status: Literal["completed"] = "completed"
    task_id: UUID
    done_at: datetime | None
    already_done: bool = Field(description="任务之前已经是完成状态（重复调用）")


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


def _knowledge(name: str, input_model: type[BaseModel], output_model: type[BaseModel]) -> ToolSpec:
    """知识库工具：读的风险等级是 read；写和删除是 knowledge（直接执行，权限范围 read_write）。"""
    risk: RiskLevel = "knowledge" if name in ("knowledge_write", "knowledge_delete") else "read"
    return _spec(name, risk, input_model, output_model, KNOWLEDGE_DESCRIPTIONS[name])


_READ: list[ToolSpec] = [
    _spec("search", "read", SearchInput, SearchOutput, "全局搜索任务、项目、书签、日程、仓库"),
    _spec(
        "list_tasks",
        "read",
        ListTasksInput,
        ListTasksOutput,
        "列出任务；assigned_to_me 只看分给你的，claimable 只看你能领取的",
    ),
    _spec("get_task", "read", GetTaskInput, GetTaskOutput, "读取一个任务的详情"),
    _spec("list_projects", "read", EmptyInput, ListProjectsOutput, "列出项目"),
    _spec(
        "calendar_agenda",
        "read",
        CalendarAgendaInput,
        CalendarAgendaOutput,
        "读取日程，以及这段时间里有截止时间、还没完成的任务（日历上的待办）。"
        "要新增待办：建一个带截止时间的任务（create_task 的 due_at），或建一个全天日程"
        "（create_calendar_event 的 all_day）",
    ),
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
        "get_host_status",
        "read",
        EmptyInput,
        GetHostStatusOutput,
        "读你自己所在服务器的状态（CPU、负载、内存、磁盘、网速与最近 24 小时概况），"
        "用来判断磁盘满、负载高这类问题；只能看你自己",
    ),
    _spec(
        "im_list_targets",
        "read",
        ImListTargetsInput,
        ImListTargetsOutput,
        "列出你能主动发消息的对象：能私聊的管理员、你所在的群（成员、规则、是否暂停、"
        "这一分钟还能发几条）。主动发消息前先用它找目标（target）",
    ),
    _spec(
        "im_read_messages",
        "read",
        ImReadMessagesInput,
        ImReadMessagesOutput,
        "读你自己会话里最近的消息（和管理员的私聊、你所在的群里入群之后的消息），"
        "主动发言前先看看大家在聊什么",
    ),
    _spec(
        "im_outbox",
        "read",
        ImOutboxInput,
        ImOutboxOutput,
        "查看你经 im_send 发的消息的状态（已发出、排队、定时、被拒收及原因），"
        "以及最近被拒收的自动回复",
    ),
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
_DIRECT: list[ToolSpec] = [
    _spec(
        "claim_task",
        "direct",
        TaskRef,
        ClaimTaskOutput,
        "领取一个可自由接取的任务（直接生效）：先用 get_task 看描述，符合你的能力再领取；"
        "你必须空闲（没有正在执行的任务），先到先得",
    ),
    _spec(
        "release_task",
        "direct",
        ReleaseTaskInput,
        ReleaseTaskOutput,
        "放弃你自己领取的任务（直接生效，要写原因）；管理员指派的任务不能放弃",
    ),
    _spec(
        "complete_task",
        "direct",
        CompleteTaskInput,
        CompleteTaskOutput,
        "把执行者是你的任务标为完成（直接生效，可以附完成说明）；管理员觉得有问题会打回",
    ),
]
# 项目管理（T-0112）：只有管理员打开了项目管理权限的实例才列出、才能调用
_PROJECT_ADMIN: list[ToolSpec] = [
    _spec(
        "create_project",
        "direct",
        CreateProjectInput,
        CreateProjectOutput,
        "新建项目（直接生效，要管理员先给你开项目管理权限）：会建项目知识库和 IM 项目群，"
        "你自己自动加入；可以同时拉其他智能体进来。每小时最多 5 个",
    ),
    _spec(
        "add_to_project",
        "direct",
        AddProjectAgentsInput,
        AddProjectAgentsOutput,
        "把其他智能体拉进你自己建的项目（直接生效）：它们得到项目知识库读写权限并进项目群。"
        "每小时最多 20 次",
    ),
    _spec(
        "list_peers",
        "direct",
        EmptyInput,
        ListAgentsOutput,
        "列出本工作空间的智能体（名字、能力说明、在线、是否空闲），用来决定拉谁进项目",
    ),
]
# 主动发消息（T-0118）：直接执行、不审批（docs/AGENTS.md 第 8 节例外四）
_IM_DIRECT: list[ToolSpec] = [
    _spec(
        "im_send",
        "direct",
        ImSendInput,
        ImSendOutput,
        "主动发消息给管理员或你所在的群（直接发出，结果当场返回：已发出、排队或被拒收及原因）。"
        "目标用 im_list_targets 给的 target；群里要别人回你就 mention 他。"
        "重试同一次发送时用同一个 client_key。每小时最多 60 条",
    ),
    _spec(
        "im_cancel",
        "direct",
        ImCancelInput,
        ImCancelOutput,
        "取消你排队中或定时的消息（已经发出的不能取消）",
    ),
]
_KNOWLEDGE: list[ToolSpec] = [
    _knowledge("knowledge_search", KnowledgeSearchInput, KnowledgeSearchOutput),
    _knowledge("knowledge_get", KnowledgeGetInput, KnowledgeGetOutput),
    _knowledge("knowledge_list", KnowledgeListInput, KnowledgeListOutput),
    _knowledge("knowledge_brief", KnowledgeBriefInput, KnowledgeBriefOutput),
    _knowledge("knowledge_write", KnowledgeWriteInput, KnowledgeWriteOutput),
    _knowledge("knowledge_delete", KnowledgeDeleteInput, KnowledgeDeleteOutput),
]
TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in _READ + _WRITE + _CRITICAL + _DIRECT + _PROJECT_ADMIN + _IM_DIRECT + _KNOWLEDGE
}
KNOWLEDGE_TOOL_NAMES = frozenset(spec.name for spec in _KNOWLEDGE)
DIRECT_TOOL_NAMES = frozenset(spec.name for spec in _DIRECT)
PROJECT_ADMIN_TOOL_NAMES = frozenset(spec.name for spec in _PROJECT_ADMIN)
IM_DIRECT_TOOL_NAMES = frozenset(spec.name for spec in _IM_DIRECT)

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
