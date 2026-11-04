"""任务时间轴（T-0104，docs/v2/TIMELINE_PLAN.md 第 2.3 ~ 2.5 节）。

- 执行记录：一段执行的开始、结束、结束原因；实例执行的段带 token（按时间归属，见 `tokens_shared`）
  和等待审批的时间；
- 时间轴：轨道（执行者或项目）、片段（计划 / 执行段）、token 用量分桶与概况；
- 用量上报：接入包每分钟把 OpenClaw 每个会话当天的累计 token 报上来（只收数字与会话的 SHA-256）。
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

RunEndReason = Literal["done", "paused", "interrupted", "cancelled"]
StopReason = Literal["paused", "interrupted", "cancelled"]
ExecutorKind = Literal["user", "instance"]
TimelineGroup = Literal["executor", "project"]
TrackKind = Literal["user", "instance", "unassigned", "project", "former"]

MAX_USAGE_SESSIONS = 500
MAX_TOKENS = 10**13


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenCount(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_write: int
    total: int = Field(description="四项之和")


class UsagePoint(BaseModel):
    start: datetime = Field(description="这一桶的开始（UTC，按 bucket_minutes 对齐）")
    tokens: TokenCount


class ApprovalWait(BaseModel):
    """这个实例的写操作从提交到有结果（批准、拒绝、过期）之间的时间。"""

    execution_id: UUID
    tool: str
    start: datetime
    end: datetime | None = Field(description="还在等待时为空")


class TaskRunOut(BaseModel):
    id: UUID
    task_id: UUID
    executor_kind: ExecutorKind
    user_id: UUID | None
    instance_id: UUID | None
    executor_name: str = Field(description="开始时执行者的名字（成员离开、实例被删除后仍保留）")
    started_at: datetime
    ended_at: datetime | None = Field(description="还在执行时为空")
    end_reason: RunEndReason | None
    started_by: UUID | None
    ended_by: UUID | None = Field(description="系统结束（实例停用 / 离线、改了执行者）时为空")
    tokens: TokenCount | None = Field(
        description="这段时间里这个实例上报的 token 增量之和（按时间归属）；"
        "人的任务与没有上报时为空"
    )
    tokens_shared: bool = Field(description="同一实例同时有几段在执行时平均分，界面标“≈”")
    usage_reported: bool = Field(description="这个实例上报过用量（旧接入包、上报失败时为 false）")
    waits: list[ApprovalWait] = Field(description="落在这段里的等待审批")
    series: list[UsagePoint] = Field(
        default_factory=list, description="每 10 分钟的用量（只在执行记录接口里返回）"
    )


class TaskRunList(BaseModel):
    items: list[TaskRunOut]


class TaskRunStart(_Strict):
    dispatch: bool = Field(
        default=False,
        description="true：执行者是实例时交给它执行——以当前管理员的 IM 账号给它发一条固定格式的消息"
        "（owner / admin）；false：人的任务开始执行（member 及以上）",
    )


class TaskRunStop(_Strict):
    reason: StopReason = Field(
        description="paused 暂停（人的任务）/ interrupted 中断 / cancelled 取消；"
        "实例执行的段要 owner / admin"
    )


class AssigneeUser(BaseModel):
    id: UUID
    display_name: str


class AssigneeInstance(BaseModel):
    id: UUID
    name: str
    status: str = Field(description="pending / online / offline（停用的实例不列出）")


class AssigneeOptions(BaseModel):
    """任务可以指派的执行者：本工作空间的成员与没有停用的实例。"""

    users: list[AssigneeUser]
    instances: list[AssigneeInstance]


# ---------------------------------------------------------------- 时间轴


class TimelineTrack(BaseModel):
    key: str = Field(description="片段用它指向轨道，例如 instance:<id>、user:<id>、project:<id>")
    kind: TrackKind
    id: UUID | None
    name: str
    status: str | None = Field(description="实例的状态（pending / online / offline / disabled）")


class TimelineTask(BaseModel):
    id: UUID
    project_id: UUID
    project_title: str
    title: str
    done: bool
    done_at: datetime | None
    due_at: datetime | None
    planned_start_at: datetime | None
    started_at: datetime | None
    assignee_user_id: UUID | None
    assignee_instance_id: UUID | None
    running: bool = Field(description="有一段正在执行")


class TimelineSegment(BaseModel):
    kind: Literal["planned", "run"]
    task_id: UUID
    track: str
    start: datetime | None = Field(
        description="计划片段：计划开始；只有截止时间时为空（画截止标记）"
    )
    end: datetime | None = Field(
        description="执行段：还在执行时为空；计划片段：截止时间，没有截止时间时为空"
    )
    run: TaskRunOut | None = Field(description="执行段的详情；计划片段为空")


class TimelineUsage(BaseModel):
    scope: Literal["workspace", "project"] = Field(
        description="workspace：本工作空间所有实例上报的全部 token（含不在任务里的对话）；"
        "project：只算这个项目的执行段按时间归属到的 token"
    )
    bucket_minutes: int
    reported: bool = Field(description="有实例上报过用量；为 false 时界面显示“未上报”")
    buckets: list[UsagePoint] = Field(description="只列出有用量的桶")
    total: TokenCount


class TimelineSummary(BaseModel):
    running: int = Field(description="正在执行的段数")
    done: int = Field(description="时间范围内完成的任务数")
    tokens: int = Field(description="时间范围内的 token 合计（范围同 usage.scope）")
    overdue: int = Field(description="现在已过截止时间、还没完成的任务数")


class TimelineOut(BaseModel):
    range_start: datetime
    range_end: datetime
    now: datetime
    group: TimelineGroup
    project_id: UUID | None
    tracks: list[TimelineTrack]
    tasks: list[TimelineTask]
    segments: list[TimelineSegment]
    usage: TimelineUsage
    summary: TimelineSummary
    truncated: bool = Field(description="任务太多，只返回了最近的一部分")


# ---------------------------------------------------------------- 用量上报（实例）

SessionHash = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
    Field(description="OpenClaw 会话 key 的 SHA-256（十六进制小写）；工作台不收会话 key 本身"),
]
TokenValue = Annotated[int, Field(ge=0, le=MAX_TOKENS)]


class SessionTokens(_Strict):
    input: TokenValue
    output: TokenValue
    cache_read: TokenValue
    cache_write: TokenValue


class SessionUsage(_Strict):
    session: SessionHash
    usage: SessionTokens | None = Field(
        description="这个会话当天的累计值；OpenClaw 正在刷新缓存时为 null，这次跳过"
    )


class UsageReport(_Strict):
    day: date = Field(description="累计值所属的日期（UTC）；只收今天和昨天")
    sessions: list[SessionUsage] = Field(max_length=MAX_USAGE_SESSIONS)


class UsageReportResult(BaseModel):
    accepted: int = Field(description="记下的会话数")
    skipped: int = Field(description="usage 为 null 跳过的会话数")
    tokens: int = Field(description="这次算出的增量合计")
    baseline: bool = Field(description="这个实例第一次上报：只记基准，不把之前的用量算进来")
