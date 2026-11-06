"""工作台自己的项目与任务（T-0082，docs/decisions/2026-11-02-remove-vikunja.md）。

单人版：没有负责人、指派、评论。viewer 可读，member 及以上可写。
- 任务的写接口沿用 Sprint 3 的约定：必须带 `Idempotency-Key`，`version` 是不透明字符串（原样带回）；
- 项目的写接口用整数 `version` 做乐观并发，不需要 Idempotency-Key。
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.schemas.common import Page

TASK_TITLE_MAX = 250
TASK_DESCRIPTION_MAX = 10_000
PROJECT_TITLE_MAX = 120
PROJECT_DESCRIPTION_MAX = 2_000
LABELS_MAX = 20
LABEL_MAX = 50

Priority = Annotated[
    int,
    Field(ge=0, le=5, description="0 未设置、1 低、2 中、3 高、4 紧急、5 立即"),
]
TaskTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TASK_TITLE_MAX)
]
TaskDescription = Annotated[
    str,
    StringConstraints(max_length=TASK_DESCRIPTION_MAX),
    Field(description="纯文本（可以写 Markdown）；前端按纯文本显示，不渲染 HTML"),
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LABEL_MAX)]
Labels = Annotated[
    list[Label], Field(max_length=LABELS_MAX, description="标签；重复的只保留一个，按给出的顺序")
]
TaskVersion = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
    Field(description="读取时的 version，原样带回；与当前版本不一致时返回 409"),
]
ProjectTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PROJECT_TITLE_MAX)
]
ProjectDescription = Annotated[str, StringConstraints(max_length=PROJECT_DESCRIPTION_MAX)]
ProjectVersion = Annotated[int, Field(ge=1, description="读取时的版本；不一致时返回 409")]
TaskStatusFilter = Literal["open", "done", "all"]
ClaimMode = Literal["assigned", "open"]
ClaimScope = Literal["all", "project"]
TaskSort = Literal["due_at", "priority", "updated"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _dedupe(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(labels))


# ---------------------------------------------------------------- 项目

ProjectGroupStatus = Literal["none", "pending", "ready"]
InstanceStatus = Literal["pending", "online", "offline", "disabled"]
MAX_PROJECT_AGENTS = 50


class ProjectAgentBrief(BaseModel):
    """项目的智能体成员（T-0110）：在项目知识库里有权限，有项目群时也在群里。"""

    instance_id: UUID
    name: str
    status: InstanceStatus


class ProjectImGroup(BaseModel):
    """项目群（T-0110，docs/v2/AGENT_OPS_PLAN.md 第 2.2 节）。"""

    status: ProjectGroupStatus = Field(
        description="none 没有项目群；pending 正在建群或同步成员（OpenIM 不可用时每分钟重试）；"
        "ready 已建好"
    )
    group_id: str | None = Field(description="OpenIM 的群 ID；还没建好时为 null")
    conversation_id: str | None = Field(description="群会话 ID（sg_<群 ID>）")
    error: str | None = Field(
        description="pending 时上一次失败的原因（错误类别，如 unavailable、not_configured）"
    )


class ProjectOut(BaseModel):
    id: UUID
    title: str
    description: str
    archived: bool
    open_tasks: int = Field(description="未完成任务数")
    done_tasks: int = Field(description="已完成任务数")
    agents: list[ProjectAgentBrief] = Field(description="项目的智能体成员（T-0110）")
    im_group: ProjectImGroup
    issue_notices: bool = Field(
        default=False, description="Issue 新开 / 关闭时在项目群里提示一行（T-0123，默认关闭）"
    )
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectPage(Page[ProjectOut]):
    pass


class ProjectCreate(_Strict):
    title: ProjectTitle
    description: ProjectDescription = ""
    instance_ids: list[UUID] = Field(
        default_factory=list,
        max_length=MAX_PROJECT_AGENTS,
        description="一开始就加入项目的智能体（只限 owner / admin；本工作空间里没有停用的实例）",
    )
    im_group: bool = Field(
        default=False,
        description="同时建 IM 项目群，群主是自己（只限 owner / admin；需要部署 OpenIM）",
    )

    @model_validator(mode="after")
    def _unique_instances(self) -> Self:
        self.instance_ids = list(dict.fromkeys(self.instance_ids))
        return self


class ProjectUpdate(_Strict):
    """只发送要改的字段；archived 为 true 归档（列表默认不显示，任务保留）。"""

    version: ProjectVersion
    title: ProjectTitle | None = None
    description: ProjectDescription | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        return self


class ProjectAgentOut(BaseModel):
    instance_id: UUID
    name: str
    description: str = Field(description="实例的说明（能力说明）")
    status: InstanceStatus
    access: Literal["read_write", "read"] = Field(description="对项目知识库的权限")
    in_group: bool = Field(description="是否已在项目群里")


class ProjectAgentList(BaseModel):
    items: list[ProjectAgentOut]
    im_group: ProjectImGroup


class ProjectProgressOut(BaseModel):
    """首页“项目进度”组件（规范 §8.2 ProjectProgress；T-0064）。"""

    project_id: UUID
    title: str
    open_tasks: int
    done_tasks: int


class ProjectProgressList(BaseModel):
    items: list[ProjectProgressOut]


# ---------------------------------------------------------------- 任务


class TaskOut(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str = Field(description="纯文本；前端按纯文本显示，不渲染 HTML")
    done: bool
    done_at: datetime | None
    priority: int = Field(ge=0, le=5)
    due_at: datetime | None
    labels: list[str]
    planned_start_at: datetime | None = Field(description="计划开始时间（T-0104）")
    started_at: datetime | None = Field(description="第一次开始执行的时间；由执行记录维护")
    assignee_user_id: UUID | None = Field(description="执行者是工作空间成员时的用户 ID")
    assignee_instance_id: UUID | None = Field(
        description="执行者是 OpenClaw 实例时的实例 ID；与 assignee_user_id 二选一"
    )
    claim_mode: ClaimMode = Field(
        description="接取方式（T-0111）：assigned 由管理员指派；open 可自由接取（空闲的智能体领取）"
    )
    claim_scope: ClaimScope = Field(
        description="自由接取的范围：all 全部智能体 / project 只限项目成员"
    )
    claimable: bool = Field(description="现在能被领取：open、没有执行者、没有完成")
    version: str = Field(description="乐观并发用的不透明字符串；修改 / 删除时原样带回")
    created_at: datetime
    updated_at: datetime


class TaskPage(Page[TaskOut]):
    pass


TaskEventKind = Literal["opened", "claimed", "released", "completed", "reopened"]


class TaskEventOut(BaseModel):
    """任务的动态（T-0111）：设为自由接取、领取、放弃、完成、打回。"""

    id: UUID
    kind: TaskEventKind
    actor_kind: Literal["user", "instance", "system"]
    actor_name: str
    note: str = Field(description="放弃 / 打回的原因或完成说明；来自人或智能体的文字，按纯文本显示")
    created_at: datetime


class TaskEventList(BaseModel):
    items: list[TaskEventOut]


class TaskReopen(_Strict):
    """打回（owner / admin）：已完成的任务回到进行中，原因会发给执行的智能体。"""

    version: TaskVersion
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class TaskCreate(_Strict):
    project_id: UUID
    title: TaskTitle
    description: TaskDescription = ""
    priority: Priority = 0
    due_at: AwareDatetime | None = None
    labels: Labels = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_labels(self) -> Self:
        self.labels = _dedupe(self.labels)
        return self


# 可以显式设为 null 的字段：清除截止时间、计划开始时间、执行者
NULLABLE_TASK_FIELDS = frozenset(
    {"due_at", "planned_start_at", "assignee_user_id", "assignee_instance_id"}
)


class TaskUpdate(_Strict):
    """只发送要改的字段。

    due_at、planned_start_at、assignee_user_id、assignee_instance_id 显式传 null 表示清除，
    其余字段不能为 null。执行者二选一：设了其中一个，另一个自动清除；两个都给非空值时返回 422。
    改执行者时，正在执行的一段记为中断（T-0104）。
    """

    version: TaskVersion
    title: TaskTitle | None = None
    description: TaskDescription | None = None
    priority: Priority | None = None
    due_at: AwareDatetime | None = None
    planned_start_at: AwareDatetime | None = None
    done: bool | None = None
    project_id: UUID | None = Field(default=None, description="移动到其他项目")
    labels: Labels | None = None
    assignee_user_id: UUID | None = Field(default=None, description="执行者：工作空间成员")
    assignee_instance_id: UUID | None = Field(
        default=None, description="执行者：本工作空间的 OpenClaw 实例（不能是已停用的）"
    )
    claim_mode: ClaimMode | None = Field(
        default=None,
        description="接取方式（只限 owner / admin）：设为 open 时任务必须没有执行者、没有完成，"
        "工作台会通知符合条件的智能体；设了执行者时自动改回 assigned",
    )
    claim_scope: ClaimScope | None = Field(
        default=None, description="自由接取的范围（只限 owner / admin）"
    )

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed - NULLABLE_TASK_FIELDS):
            raise ValueError("只有 due_at、planned_start_at 与执行者可以设为 null")
        if self.assignee_user_id is not None and self.assignee_instance_id is not None:
            raise ValueError("执行者只能是一个成员或一个实例")
        if self.labels is not None:
            self.labels = _dedupe(self.labels)
        return self
