"""知识库接口（T-0098，docs/v2/KNOWLEDGE_PLAN.md 第 3、5 节）。

- 工作空间范围，用 X-Workspace-Id 指定；viewer 可读，member 及以上可编辑笔记，
  owner / admin 管理知识库设置、参与的智能体、智能体的写入开关、彻底删除。
- 笔记的修改、删除、撤销用整数 version 做乐观并发：与当前版本不一致时返回 409 VERSION_CONFLICT，
  details.current 是最新的笔记（编辑器据此提示合并）。
- 正文是 Markdown，按不可信内容处理：前端渲染时消毒。
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import Page

NOTE_TITLE_MAX = 200
NOTE_BODY_MAX_HUMAN = 256 * 1024
NOTE_BODY_MAX_AGENT = 20 * 1024
FOLDER_MAX = 200
TAGS_MAX = 20
TAG_MAX = 40
ALIASES_MAX = 10
REASON_MAX = 500
TRASH_DAYS = 30

SpaceKind = Literal["public", "project", "agent"]
AgentAccess = Literal["read_write", "read", "none"]
ParticipantAccess = Literal["read_write", "read"]
NoteType = Literal[
    "overview",
    "brief",
    "decision",
    "rule",
    "process",
    "runbook",
    "lesson",
    "review",
    "preference",
    "glossary",
    "record",
    "plan",
    "risk",
    "design",
    "template",
    "correction",
    "readme",
]
SourceKind = Literal["human", "observation", "task", "conversation", "external", "system"]
ChangeKind = Literal["create", "update", "delete", "restore", "revert", "rename"]
GuardKind = Literal["secret_blocked", "delete_paused", "rate_limited", "writes_disabled"]

NoteTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_TITLE_MAX)
]
NoteBody = Annotated[
    str,
    StringConstraints(max_length=NOTE_BODY_MAX_HUMAN),
    Field(description="Markdown；人写的上限 256 KB，智能体每次写入 20 KB（按 UTF-8 字节计）"),
]
Folder = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=FOLDER_MAX),
    Field(description="文件夹路径，用 / 分隔；空字符串表示根目录"),
]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TAG_MAX)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=REASON_MAX)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Author(BaseModel):
    kind: Literal["user", "agent", "system"] = Field(
        description="user 人；agent OpenClaw 实例；system 工作台自己（例如《收到的纠正》）"
    )
    id: UUID | None = Field(description="用户或实例的 ID；已删除时为 null")
    name: str


# ---------------------------------------------------------------- 知识库


class SpaceAgentOut(BaseModel):
    instance_id: UUID
    name: str
    access: ParticipantAccess
    brief_version_read: int | None = Field(
        description="它最近读到的项目简报版本；没读过为 null（方案 3.10）"
    )
    added_at: datetime


class SpaceOut(BaseModel):
    id: UUID
    kind: SpaceKind
    name: str
    description: str
    project_id: UUID | None
    instance_id: UUID | None
    archived: bool = Field(
        description="项目已归档，或项目 / 实例已删除：智能体只读（人仍可编辑或删除）"
    )
    agent_access: AgentAccess = Field(
        description="公共库：所有智能体的权限；智能体库：其他智能体的权限；项目库看 agents"
    )
    writes_enabled: bool = Field(description="智能体库：这个智能体能否写入和删除")
    deletes_paused_at: datetime | None = Field(
        description="智能体库：删除超过上限被自动暂停的时间（方案 3.8 第 4 条）"
    )
    notes: int = Field(description="未删除的笔记数")
    last_updated_at: datetime | None
    brief_note_id: UUID | None = Field(description="项目库：项目简报笔记")
    brief_version: int | None
    agents: list[SpaceAgentOut] = Field(description="项目库：参与的智能体；其他知识库为空")
    version: int


class SpaceList(BaseModel):
    items: list[SpaceOut]


class SpaceUpdate(_Strict):
    version: int
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    agent_access: AgentAccess | None = Field(
        default=None, description="公共库可选三种；智能体库只能 read / none；项目库不可设置"
    )
    writes_enabled: bool | None = Field(default=None, description="只对智能体库")
    resume_deletes: bool | None = Field(default=None, description="true：解除自动暂停的删除")


class ParticipantPut(_Strict):
    access: ParticipantAccess = "read_write"


# ---------------------------------------------------------------- 笔记


class NoteSummary(BaseModel):
    id: UUID
    space_id: UUID
    space_kind: SpaceKind
    space_name: str
    title: str
    type: NoteType
    folder: str
    tags: list[str]
    pinned: bool
    conflict: bool = Field(description="有冲突或重复提醒")
    source_kind: SourceKind
    updated_by: Author
    updated_at: datetime
    version: int


class NotePage(Page[NoteSummary]):
    pass


class LinkOut(BaseModel):
    text: str = Field(description="链接里写的文字")
    note_id: UUID | None = Field(description="指向的笔记；未建立的笔记或资源链接为 null")
    title: str | None
    space_name: str | None
    deleted: bool = Field(description="指向的笔记在回收站里")
    resource_type: Literal["task", "project", "bookmark"] | None
    resource_id: UUID | None


class BacklinkOut(BaseModel):
    note_id: UUID
    title: str
    space_id: UUID
    space_kind: SpaceKind
    space_name: str
    snippet: str = Field(description="链接所在的那一段文字（不可信内容，按纯文本显示）")


class AgentReads(BaseModel):
    instance_id: UUID | None
    name: str
    reads: int


class UsageOut(BaseModel):
    reads_30d: int = Field(description="近 30 天被智能体读取的次数")
    by_agent: list[AgentReads]
    last_read_at: date | None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["task", "project", "note", "conversation", "message", "bookmark"]
    id: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class NoteOut(NoteSummary):
    body: str
    aliases: list[str]
    review_at: date | None
    conflict_note: str | None
    created_by: Author
    created_at: datetime
    deleted_at: datetime | None
    purge_after: datetime | None
    reason: str = Field(description="最近一次改动的原因")
    evidence: list[Evidence] = Field(description="最近一次改动的依据（只有 id）")
    links: list[LinkOut]
    backlinks: list[BacklinkOut]
    usage: UsageOut


class NoteCreate(_Strict):
    space_id: UUID
    title: NoteTitle
    body: NoteBody = ""
    type: NoteType = "record"
    tags: Annotated[list[Tag], Field(max_length=TAGS_MAX)] = []
    folder: Folder = ""
    aliases: Annotated[list[NoteTitle], Field(max_length=ALIASES_MAX)] = []
    review_at: date | None = None
    pinned: bool = False
    reason: Reason = ""


class NoteUpdate(_Strict):
    version: int
    space_id: UUID | None = Field(default=None, description="移到另一个知识库")
    title: NoteTitle | None = None
    body: NoteBody | None = None
    type: NoteType | None = None
    tags: Annotated[list[Tag], Field(max_length=TAGS_MAX)] | None = None
    folder: Folder | None = None
    aliases: Annotated[list[NoteTitle], Field(max_length=ALIASES_MAX)] | None = None
    review_at: date | None = None
    pinned: bool | None = None
    reason: Reason = Field(
        default="", description="改动说明；改了智能体写的内容时会写回它的《收到的纠正》"
    )


class RevertRequest(_Strict):
    version: int = Field(description="笔记的当前版本")
    to_version: int = Field(description="退回到哪一版的内容")
    reason: Reason = ""


class NoteSuggestion(BaseModel):
    id: UUID
    title: str
    space_id: UUID
    space_kind: SpaceKind
    space_name: str


class NoteSuggestionList(BaseModel):
    items: list[NoteSuggestion]


# ---------------------------------------------------------------- 版本


class RevisionOut(BaseModel):
    version: int
    change: ChangeKind
    title: str
    author: Author
    reason: str
    evidence: list[Evidence]
    added: int = Field(description="与上一版相比增加的行数")
    removed: int
    created_at: datetime
    updated_at: datetime


class RevisionList(BaseModel):
    items: list[RevisionOut]


class DiffLine(BaseModel):
    sign: Literal["+", "-", " "]
    text: str


class RevisionDetail(RevisionOut):
    body: str
    properties: dict[str, object]
    diff: list[DiffLine] = Field(description="与上一版的逐行差异（第一版全部是 +）")


# ---------------------------------------------------------------- 图谱


class GraphNode(BaseModel):
    id: UUID
    kind: Literal["note", "task", "project", "bookmark", "file"] = Field(
        description="file 是图片（只有 files=true 时返回，T-0103）"
    )
    title: str
    space_id: UUID | None
    space_kind: SpaceKind | None
    type: NoteType | None
    author_kind: Literal["user", "agent", "system"] | None
    degree: int


class GraphEdge(BaseModel):
    source: UUID
    target: UUID


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = Field(description="超过 2000 篇笔记时按最近修改与链接数截断")
    total_notes: int


# ---------------------------------------------------------------- 变更记录与回收站


class ChangeOut(BaseModel):
    id: UUID
    kind: Literal["revision", "guard"]
    change: ChangeKind | None
    guard: GuardKind | None
    note_id: UUID | None
    note_title: str | None
    note_deleted: bool
    space_id: UUID | None
    space_kind: SpaceKind | None
    space_name: str | None
    author: Author
    reason: str
    detail: str = Field(description="保护记录的说明；不含被拦下的内容")
    version: int | None
    added: int
    removed: int
    revertable: bool = Field(description="可以撤销这次改动（退回上一版）")
    created_at: datetime


class ChangePage(Page[ChangeOut]):
    pass


class TrashItem(BaseModel):
    id: UUID
    title: str
    space_id: UUID
    space_kind: SpaceKind
    space_name: str
    deleted_at: datetime
    deleted_by: Author
    purge_after: datetime


class TrashPage(Page[TrashItem]):
    pass


# ---------------------------------------------------------------- 智能体知识库


class AgentProjectOut(BaseModel):
    project_id: UUID
    title: str
    access: ParticipantAccess
    brief_version_read: int | None
    brief_version: int | None


class AgentMetrics(BaseModel):
    """近 30 天（方案 3.9 第 5 条）。"""

    notes: int = Field(description="它自己知识库里的笔记数（当前）")
    writes: int = Field(description="写入与修改的次数（所有知识库）")
    writes_shared: int = Field(description="其中写进项目和公共知识库的次数")
    deletes: int
    searches: int
    reads: int
    read_by_others: int = Field(description="它写的笔记被其他智能体读取的次数")
    corrected: int = Field(description="它写的内容被人修改或撤销的次数")
    overdue_reviews: int = Field(description="它写的、过了复核日期的笔记数（当前）")
    blocked: int = Field(description="被保护措施拦下或暂停的次数")


class AgentWeek(BaseModel):
    """一周（周一开始）的读写次数（T-0100，智能体主页的趋势图）。"""

    week_start: date
    reads_own: int = Field(description="读自己知识库的次数")
    reads_shared: int = Field(description="读项目、公共与别的智能体知识库的次数")
    writes: int = Field(description="新建、修改、改名的次数（所有知识库）")


class SpaceWeek(BaseModel):
    week_start: date
    human: int = Field(description="人新建、修改、改名、退回的次数")
    agent: int = Field(description="智能体新建、修改、改名的次数")


class SpaceActivity(BaseModel):
    """近 8 周的修改次数（T-0100，知识库主页的趋势图），最早的在前。"""

    weeks: list[SpaceWeek]


class AgentKnowledgeOut(BaseModel):
    instance_id: UUID
    name: str
    space_id: UUID
    writes_enabled: bool
    deletes_paused_at: datetime | None
    corrections_unseen: int
    metrics: AgentMetrics
    projects: list[AgentProjectOut]
    weekly: list[AgentWeek] = Field(description="近 8 周，最早的在前")


class FileOut(BaseModel):
    """上传的图片（T-0103）。在正文里写 `![说明](url)` 引用；读取经工作台，按工作空间与角色校验。"""

    id: UUID
    url: str = Field(description="/api/v1/knowledge/files/<id>")
    mime: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    size: int = Field(description="字节数（JPEG 去掉元数据之后）")
    width: int
    height: int
    created_at: datetime


class ReadmeOut(BaseModel):
    """项目或任务的 README（T-0122，方案第 9 节）：知识库里的一篇 readme 笔记。"""

    note: NoteOut
    project_id: UUID
    task_id: UUID | None
    auto_sections: list[str] = Field(
        description="还由工作台自动更新的部分（标记没被删掉的）；页面上标“自动更新”"
    )
