"""外部项目与任务的投影（docs/DEVELOPMENT_SPEC.md 第 4.1、4.2、6.3 节）。

约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 3、5、6、7 节：`id` 是投影行的 UUID，
外部 ID（`vikunja:task:42`）只在 `external_id` 里；读接口读本地投影并带 `sync` 新鲜度，
写接口经 Connector 写入 Vikunja，必须带 `Idempotency-Key`。
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
from app.schemas.integrations import SyncState

TASK_TITLE_MAX = 250
TASK_DESCRIPTION_MAX = 10_000

Priority = Annotated[
    int,
    Field(
        ge=0, le=5, description="0 未设置、1 低、2 中、3 高、4 紧急、5 立即（核验 ADR 第 5.1 节）"
    ),
]
TaskTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TASK_TITLE_MAX)
]
TaskDescription = Annotated[
    str,
    StringConstraints(max_length=TASK_DESCRIPTION_MAX),
    Field(description="Markdown 文本；前端按纯文本显示，不渲染 HTML"),
]
TaskVersion = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
    Field(description="读取时的 version，原样带回；与 Vikunja 上的当前版本不一致时返回 409"),
]
TaskStatusFilter = Literal["open", "done", "all"]
TaskSort = Literal["due_at", "priority", "updated"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 项目


class ProjectOut(BaseModel):
    id: UUID = Field(description="投影 UUID；创建任务时的 project_id 用它")
    integration_id: UUID
    external_id: str = Field(description="例如 vikunja:project:7；只用于显示与排错")
    title: str
    archived: bool
    source_url: str = Field(description="Vikunja 原生界面中的地址")
    source_updated_at: datetime | None
    synced_at: datetime


class ProjectPage(Page[ProjectOut]):
    sync: SyncState


# ---------------------------------------------------------------- 任务


class TaskOut(BaseModel):
    id: UUID = Field(description="投影 UUID")
    integration_id: UUID
    project_id: UUID | None = Field(description="所属项目的投影 UUID；项目尚未同步时为 null")
    external_id: str = Field(description="例如 vikunja:task:42")
    identifier: str | None = Field(description="Vikunja 的任务标识，例如 PROJ-12")
    title: str
    description: str = Field(description="Markdown 文本；前端按纯文本显示，不渲染 HTML")
    done: bool
    done_at: datetime | None
    priority: int = Field(ge=0, le=5)
    due_at: datetime | None
    labels: list[str] = Field(description="标签标题；本 Sprint 只读")
    source_url: str = Field(description="Vikunja 原生界面中的地址")
    version: str = Field(description="乐观并发用的不透明字符串；修改 / 删除时原样带回")
    source_updated_at: datetime | None
    synced_at: datetime


class TaskPage(Page[TaskOut]):
    sync: SyncState


class TaskCreate(_Strict):
    project_id: UUID
    title: TaskTitle
    description: TaskDescription = ""
    priority: Priority = 0
    due_at: AwareDatetime | None = None


class TaskUpdate(_Strict):
    """只发送要改的字段；due_at 显式传 null 表示清除截止时间，其余字段不能为 null。"""

    version: TaskVersion
    title: TaskTitle | None = None
    description: TaskDescription | None = None
    priority: Priority | None = None
    due_at: AwareDatetime | None = None
    done: bool | None = None
    project_id: UUID | None = Field(default=None, description="移动到其他项目")

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed - {"due_at"}):
            raise ValueError("只有 due_at 可以设为 null")
        return self


# ---------------------------------------------------------------- Webhook


class WebhookAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
