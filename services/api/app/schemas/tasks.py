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
TaskSort = Literal["due_at", "priority", "updated"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _dedupe(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(labels))


# ---------------------------------------------------------------- 项目


class ProjectOut(BaseModel):
    id: UUID
    title: str
    description: str
    archived: bool
    open_tasks: int = Field(description="未完成任务数")
    done_tasks: int = Field(description="已完成任务数")
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectPage(Page[ProjectOut]):
    pass


class ProjectCreate(_Strict):
    title: ProjectTitle
    description: ProjectDescription = ""


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
    version: str = Field(description="乐观并发用的不透明字符串；修改 / 删除时原样带回")
    created_at: datetime
    updated_at: datetime


class TaskPage(Page[TaskOut]):
    pass


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


class TaskUpdate(_Strict):
    """只发送要改的字段；due_at 显式传 null 表示清除截止时间，其余字段不能为 null。"""

    version: TaskVersion
    title: TaskTitle | None = None
    description: TaskDescription | None = None
    priority: Priority | None = None
    due_at: AwareDatetime | None = None
    done: bool | None = None
    project_id: UUID | None = Field(default=None, description="移动到其他项目")
    labels: Labels | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed - {"due_at"}):
            raise ValueError("只有 due_at 可以设为 null")
        if self.labels is not None:
            self.labels = _dedupe(self.labels)
        return self
