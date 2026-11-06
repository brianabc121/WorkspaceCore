"""项目 Issues 的接口模型（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 第 8 节）。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

IssueKind = Literal[
    "question", "proposal", "bug", "contract", "handoff", "review", "blocker", "other"
]
IssueState = Literal["open", "closed"]
CloseReason = Literal["resolved", "wontfix", "duplicate"]
IssueEventKind = Literal[
    "opened",
    "closed",
    "reopened",
    "assigned",
    "unassigned",
    "labeled",
    "unlabeled",
    "linked",
    "unlinked",
    "retitled",
    "locked",
    "unlocked",
    "mentioned",
]
ActorKind = Literal["user", "instance", "system"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Body = Annotated[str, StringConstraints(max_length=20_000)]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(BaseModel):
    kind: ActorKind
    id: UUID | None = Field(description="用户 ID 或实例 ID；系统为空")
    name: str


class IssueTaskRef(BaseModel):
    id: UUID
    title: str
    done: bool


class PullRef(BaseModel):
    """正文和评论里粘贴的 GitHub PR 链接。

    仓库已接入工作台的 GitHub 集成时显示同步到的状态（只读，不访问 GitHub）。
    """

    url: str
    title: str
    state: str | None
    merged: bool


class ProjectIssueOut(BaseModel):
    id: UUID
    project_id: UUID
    number: int
    title: str
    kind: IssueKind
    labels: list[str]
    state: IssueState
    close_reason: CloseReason | None
    duplicate_of: UUID | None
    locked: bool
    author: Actor
    assignees: list[Actor]
    tasks: list[IssueTaskRef]
    comment_count: int
    unread: bool = Field(description="对当前查看的人：有没读过的动态")
    agent_paused: bool = Field(
        description="连续 10 条评论来自智能体，智能体暂停评论（人评论后恢复）"
    )
    closed_at: datetime | None
    last_activity_at: datetime
    created_at: datetime
    version: int


class IssueList(BaseModel):
    items: list[ProjectIssueOut]
    open_count: int
    closed_count: int


class CommentOut(BaseModel):
    id: UUID
    reply_to: UUID | None
    body: str | None = Field(description="被隐藏的评论为空")
    author: Actor
    edited_at: datetime | None
    hidden: bool
    created_at: datetime


class IssueEventOut(BaseModel):
    id: UUID
    kind: IssueEventKind
    actor: Actor
    data: dict[str, str | int | None]
    created_at: datetime


class IssueDetail(ProjectIssueOut):
    body: str
    comments: list[CommentOut]
    events: list[IssueEventOut]
    pulls: list[PullRef]
    can_close: bool = Field(description="当前查看的人能不能关闭 / 重新打开")
    can_moderate: bool = Field(description="owner / admin：锁定、隐藏评论、删除")


class IssueCreate(_Strict):
    title: Title
    body: Body = ""
    kind: IssueKind = "other"
    labels: Annotated[list[Label], Field(max_length=10)] = []
    assignee_user_ids: Annotated[list[UUID], Field(max_length=20)] = []
    assignee_instance_ids: Annotated[list[UUID], Field(max_length=20)] = []
    task_ids: Annotated[list[UUID], Field(max_length=20)] = []
    use_template: bool = Field(default=False, description="正文为空时套用这种类型的模板")


class IssueUpdate(_Strict):
    version: int
    title: Title | None = None
    body: Body | None = None
    kind: IssueKind | None = None
    labels: Annotated[list[Label], Field(max_length=10)] | None = None
    state: IssueState | None = None
    close_reason: CloseReason | None = None
    duplicate_of: int | None = Field(default=None, description="重复时写明重复哪一个（编号）")
    locked: bool | None = None
    assignee_user_ids: Annotated[list[UUID], Field(max_length=20)] | None = None
    assignee_instance_ids: Annotated[list[UUID], Field(max_length=20)] | None = None
    task_ids: Annotated[list[UUID], Field(max_length=20)] | None = None

    @model_validator(mode="after")
    def _close(self) -> "IssueUpdate":
        if self.state == "closed" and self.close_reason is None:
            raise ValueError("关闭时要选原因：resolved / wontfix / duplicate")
        if self.close_reason == "duplicate" and self.duplicate_of is None:
            raise ValueError("重复时写明重复哪一个 Issue（编号）")
        return self


class CommentCreate(_Strict):
    body: CommentBody
    reply_to: UUID | None = None


class CommentUpdate(_Strict):
    body: CommentBody


class IssueTemplate(BaseModel):
    kind: IssueKind
    label: str
    body: str


class IssueTemplates(BaseModel):
    items: list[IssueTemplate]


class IssueNoticesPut(_Strict):
    enabled: bool
