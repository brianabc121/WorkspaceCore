"""大模型聚合聊天页面接口的请求 / 响应模型（docs/v2/LLM_CHAT_PLAN.md 第 4.2 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.llm_bridge.schemas import LlmBridgeEnvironmentOut, Platform

OwnerKind = Literal["me", "user", "agent", "external"]
_REQUEST_ID = r"^[\w.\-:]+$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmChatContextOut(BaseModel):
    """会话的上下文（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.1）：官方程序报的数，没报的为空。"""

    used: int | None = Field(default=None, description="上下文现在用了多少 token")
    window: int | None = Field(default=None, description="上下文窗口多大（token）")
    percent: int | None = Field(default=None, description="已用百分比")
    compactions: int = Field(default=0, description="平台压缩过几次")
    last_compacted_at: float | None = Field(default=None, description="最近一次压缩的时间")
    last_trigger: str | None = Field(
        default=None, description="最近一次压缩怎么触发的：auto 平台自动 / manual 手动"
    )
    near_limit: bool = Field(
        default=False, description="快到平台自动压缩的位置了（已用比例到了 warn_percent）"
    )
    warn_percent: int | None = Field(default=None, description="提醒的位置（已用百分比）")


class LlmChatSessionOut(BaseModel):
    session_id: str
    platform: str
    mode: str
    model: str | None
    title: str
    status: str = Field(description="active / archived")
    origin: str = Field(description="bridge：经 MCP 管理器新建；external：官方程序里已有")
    owner: str | None = Field(description="谁建的（显示名）")
    owner_kind: OwnerKind = Field(
        description="me：当前用户；user：别的管理员；agent：智能体；external：官方程序里已有"
    )
    busy: bool = Field(description="有排队中或进行中的回合")
    turns: int = 0
    created_at: float
    updated_at: float
    environment: LlmBridgeEnvironmentOut | None = Field(
        default=None, description="开发会话用的环境；只在会话内容里给出"
    )
    source: dict[str, Any] | None = Field(
        default=None,
        description="出处：建会话时工作台记下的——哪个实例或人，智能体当时在执行哪些任务",
    )
    purged_at: float | None = Field(
        default=None, description="对话内容按保留期清掉的时间；没清过为空"
    )
    context: LlmChatContextOut | None = Field(
        default=None, description="上下文用了多少、压缩过几次；旧版本的管理器没有"
    )


class LlmChatSessionList(BaseModel):
    sessions: list[LlmChatSessionOut]
    next_offset: int | None = None
    sync_failed: str = Field(default="", description="同步官方程序里的会话时读不到的平台")


class LlmChatAttachmentOut(BaseModel):
    """随消息带上的图片 / 文件（input），或模型产出的图片 / 文件（output）。内容另外下载。"""

    id: str
    name: str
    media_type: str
    size: int
    direction: str = Field(description="input：人带上的；output：模型产出的")
    at: float | None = None


class LlmChatStepOut(BaseModel):
    kind: str = Field(description="message（中间说的话）/ command / files / notice")
    text: str
    at: float | None = None
    name: str | None = Field(default=None, description="工具名（kind 是 tool / tool_result 时）")
    truncated: bool = Field(default=False, description="内容太长，后面的没有保存")
    error: bool = Field(default=False, description="工具报了错")
    exit_code: int | None = Field(default=None, description="命令的退出码（平台给了才有）")
    output: str | None = Field(
        default=None, description="命令的输出（平台给了才有；太长时只有前面一段，truncated 为真）"
    )


class LlmChatResumeOut(BaseModel):
    """额度用完被打断后的自动续跑（docs/v2/LLM_BRIDGE_QUOTA_PLAN.md 第 3 节）。"""

    state: str | None = Field(
        description="pending：到点自动继续；forced：有人点了现在就试；done：已经续跑；"
        "cancelled：有人说不要自动继续；skipped：到点时没有发（开关关着、会话已归档等）；"
        "exhausted：自动续的次数用完了"
    )
    at: float | None = Field(description="打算什么时候继续（时间戳）")
    attempt: int = Field(default=0, description="这是第几次自动续跑的回合（0 = 人发的）")
    resumed_by: str | None = Field(default=None, description="续跑它的那个回合")


class LlmChatMessageOut(BaseModel):
    role: str = Field(
        description="user / assistant；context_compacted：平台在这一回合压缩了上下文，"
        "它前面的内容在模型那里只剩摘要；context_cleared：在这里清空了上下文，"
        "它之前的内容模型已经不记得"
    )
    turn_id: str | None = None
    text: str
    at: float | None = None
    by: str | None = Field(
        default=None, description="谁发的（user 消息）；谁清空的（context_cleared）"
    )
    cleared_turns: int | None = Field(
        default=None, description="context_cleared：清空的那段上下文里有几个回合"
    )
    mine: bool | None = Field(default=None, description="是不是当前用户发的（只有 user 消息有）")
    status: str | None = Field(
        default=None,
        description="回答所在回合的状态："
        "queued / running / completed / failed / cancelled / unknown / interrupted",
    )
    steps: list[LlmChatStepOut] = Field(
        default=[], description="过程：中间说的话、命令、改动的文件"
    )
    failure: dict[str, Any] | None = None
    ahead_in_session: int | None = Field(default=None, description="排队时前面还有几条")
    waiting: dict[str, Any] | None = Field(
        default=None,
        description="排队时在等什么：{reason: session, position} 会话里前面还有消息；"
        "{reason: quota, until, estimated} 平台因额度暂停；{reason: platform_slot} 等平台的名额",
    )
    partial: str | None = Field(
        default=None, description="进行中：最后一段完整的话之后、已经写出来的增量文字"
    )
    timing: dict[str, Any] | None = Field(
        default=None,
        description="回答所在回合的 queued_at / started_at / finished_at、"
        "waited_seconds（排了多久）、ran_seconds（做了多久）",
    )
    usage: dict[str, Any] | None = Field(
        default=None,
        description="这一回合的 token 数（官方程序报的，不是账单）；"
        "CodeBuddy 还带 credits：这一回合消耗的积分",
    )
    next_turn_id: str | None = Field(default=None, description="被打断或续跑时，接着做的是哪一回合")
    source: dict[str, Any] | None = Field(
        default=None, description="这一回合的出处：哪个实例或人，智能体当时在执行哪些任务"
    )
    resume: LlmChatResumeOut | None = Field(
        default=None, description="被额度打断的回合（status=interrupted）：什么时候自动继续"
    )
    attachments: list[LlmChatAttachmentOut] = Field(
        default=[], description="提问带上的图片 / 文件，或这一回合模型产出的图片 / 文件"
    )
    imported: bool = Field(default=False, description="官方程序里原有的内容（第一次打开时导入的）")
    trigger: str | None = Field(
        default=None, description="context_compacted：auto 平台自动压缩 / manual 手动压缩"
    )
    references: list[dict[str, Any]] = Field(
        default=[],
        description="user 消息：随这一问带上的知识库笔记（note_id、title、label、truncated）",
    )
    pre_tokens: int | None = Field(
        default=None, description="context_compacted：压缩前的 token 数（平台报了才有）"
    )
    post_tokens: int | None = Field(
        default=None, description="context_compacted：压缩后的 token 数（平台报了才有）"
    )
    delivery: str | None = Field(
        default=None,
        description="提问是怎么送出去的：turn 普通的一个回合；inserted 插进正在处理的回合；"
        "merged 中断上一个回合后合并重发；imported 导入的",
    )


class LlmChatDigestOut(BaseModel):
    """这个会话的纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2）。"""

    note_id: UUID | None = Field(description="纪要笔记；还没写过为空")
    note_title: str | None = None
    space_id: UUID | None = None
    space_name: str | None = None
    state: str = Field(
        description="active 照常更新；off 关掉了；converted 已转成正式笔记；edited 人改过，"
        "不再自动覆盖；deleted 删进了回收站；purged 随会话内容按保留期清掉"
    )
    turns_written: int = 0
    written_at: datetime | None = None
    reason: str | None = None


class LlmChatConversationOut(BaseModel):
    session: LlmChatSessionOut
    messages: list[LlmChatMessageOut]
    last_event_id: int = Field(description="读事件时从它之后开始")
    digest: LlmChatDigestOut | None = Field(
        default=None, description="这个会话的纪要；没写过、也没关掉时为空"
    )


class LlmChatEventOut(BaseModel):
    id: int
    turn_id: str | None
    kind: str = Field(
        description="queued / started / delta / message / command / files / notice / "
        "completed / failed / cancelled / unknown。delta 是逐字的增量：接在这一回合"
        "正在写的那一段后面；随后到的 message 是这一段的全文，替换掉攒起来的增量"
    )
    at: float
    text: str | None = None
    command: str | None = None
    paths: list[str] | None = None
    by: str | None = None
    reason: str | None = None


class LlmChatEventsOut(BaseModel):
    events: list[LlmChatEventOut]
    busy: bool = Field(description="还有回合没做完：继续读")
    last_id: int


class LlmChatModelOut(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    default: bool = False
    efforts: list[str] = []


class LlmChatOptionsOut(BaseModel):
    models: list[LlmChatModelOut]
    models_error: str = Field(default="", description="读不到模型列表时的原因（例如还没登录）")
    environments: list[LlmBridgeEnvironmentOut] = []


class LlmChatTurnOut(BaseModel):
    turn_id: str
    session_id: str
    status: str
    ahead_in_session: int | None = None
    resume: LlmChatResumeOut | None = None
    delivered: str | None = Field(
        default=None,
        description="这条消息实际怎么送出去的："
        "inserted 送进了正在处理的回合（turn_id 是那个回合）；"
        "merged 中断了正在处理的回合，和“接着刚才的做”合成新回合；"
        "queued 排队；started 直接开始。"
        "旧版本的管理器没有这一项",
    )


class LlmChatCreateIn(_In):
    platform: Platform
    mode: Literal["chat", "dev"] = "chat"
    model: str | None = Field(default=None, max_length=100)
    effort: str | None = Field(default=None, max_length=20)
    environment: str | None = Field(
        default=None, pattern=r"^be_[0-9a-f]{20}$", description="开发会话必填"
    )
    title: str = Field(default="", max_length=120)
    text: str | None = Field(
        default=None, min_length=1, max_length=100_000, description="第一条消息"
    )
    request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=_REQUEST_ID,
        description="有 text 时必填",
    )

    @model_validator(mode="after")
    def _text_needs_request_id(self) -> LlmChatCreateIn:
        if self.text is not None and self.request_id is None:
            raise ValueError("带第一条消息时要有 request_id")
        return self


class LlmChatCreatedOut(BaseModel):
    session: LlmChatSessionOut
    turn: LlmChatTurnOut | None = None
    send_error: str = Field(default="", description="会话建好了，但第一条消息没发出去的原因")


class LlmChatSendIn(_In):
    text: str = Field(min_length=1, max_length=100_000)
    request_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=_REQUEST_ID,
        description="幂等号：重试同一条消息时用同一个，不会重复发送",
    )
    takeover: bool = Field(
        default=False, description="发到不是自己建的会话时必须为 true（页面先让人确认）"
    )
    mode: Literal["queue", "insert"] = Field(
        default="queue",
        description="会话正在处理别的消息时：queue 排队（不打断）；insert 插入——"
        "送进正在处理的回合，平台做不到时中断它、合并重发",
    )
    attachments: list[Annotated[str, Field(pattern=r"^ba_[0-9a-f]{20}$")]] = Field(
        default=[], max_length=8, description="随这条消息带上的附件（先上传得到编号）"
    )
    knowledge_notes: list[UUID] = Field(
        default=[],
        max_length=3,
        description="随这条消息带上的知识库笔记（人挑的，最多 3 篇；"
        "docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 6.2）。只附在发给模型的内容后面，包装成资料；"
        "合计最多 6000 字，超出的截断。所在知识库不允许发给外部平台时 403",
    )


class LlmChatRenameIn(_In):
    title: str = Field(min_length=1, max_length=120)


class LlmChatModelIn(_In):
    model: str = Field(min_length=1, max_length=100, description="之后的回合用的模型")


class LlmChatClearIn(_In):
    takeover: bool = Field(
        default=False, description="清空不是自己建的会话时必须为 true（页面先让人确认）"
    )


class LlmChatClearedOut(BaseModel):
    session: LlmChatSessionOut
    cleared: bool = Field(
        description="这次清空了没有：上次清空之后还没发过消息时什么也不做，为 false"
    )
    cleared_at: float | None = Field(default=None, description="清空的时间")


# ---- 会话的消息队列（docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 4 节）----
class LlmChatQueueItem(BaseModel):
    turn_id: str
    status: str = Field(description="queued / running")
    text: str
    by: str | None = Field(description="谁发的")
    mine: bool = Field(description="是不是我发的")
    queued_at: float
    started_at: float | None


class LlmChatQueueOut(BaseModel):
    session_id: str
    running: LlmChatQueueItem | None = Field(description="正在处理的那一条")
    queued: list[LlmChatQueueItem] = Field(description="排队中的各条，按将要处理的顺序")


class LlmChatQueueEditIn(_In):
    text: str = Field(min_length=1, max_length=100_000)


class LlmChatQueueMoveIn(_In):
    to: Literal["first", "last"] = Field(description="调到队首或队尾")


# ---- 多模型同问（docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）----
MAX_GROUP_TARGETS = 6


class LlmChatTargetIn(_In):
    bridge_id: UUID = Field(description="用哪个 MCP 管理器（它上面登录的账号）")
    platform: Platform
    mode: Literal["chat", "dev"] = "chat"
    model: str | None = Field(default=None, max_length=100)
    effort: str | None = Field(default=None, max_length=20)
    environment: str | None = Field(
        default=None, pattern=r"^be_[0-9a-f]{20}$", description="开发会话必填"
    )


class LlmChatGroupCreateIn(_In):
    title: str = Field(default="", max_length=120, description="不填时取问题的开头")
    text: str = Field(min_length=1, max_length=100_000, description="同时发给每个目标的问题")
    request_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=_REQUEST_ID,
        description="幂等号：用同一个再建一次，得到的是同一组",
    )
    targets: list[LlmChatTargetIn] = Field(
        min_length=2, max_length=MAX_GROUP_TARGETS, description="2 ~ 6 个目标，可以跨 MCP 管理器"
    )
    send: bool = Field(
        default=True,
        description="false：只建好每个会话，问题先不发——页面要带附件时用："
        "先把附件传到每个会话，再用“向这一组继续发”带上附件发出",
    )


class LlmChatGroupMemberOut(BaseModel):
    position: int
    bridge_id: UUID | None = Field(description="MCP 管理器；它的登记被删除后为空")
    bridge_name: str
    bridge_enabled: bool = Field(description="管理器还在并且启用着")
    platform: str
    mode: str
    model: str | None
    session_id: str | None = Field(description="这个目标的会话；没建成时为空，原因在 error")
    error: str = Field(default="", description="会话没建成、或第一条消息没发出去的原因")


class LlmChatGroupOut(BaseModel):
    id: UUID
    title: str
    status: str = Field(description="active / archived")
    owner: str | None = Field(description="谁建的（显示名）")
    owner_kind: Literal["me", "user"]
    members: list[LlmChatGroupMemberOut]
    created_at: datetime
    updated_at: datetime


class LlmChatGroupList(BaseModel):
    groups: list[LlmChatGroupOut]


class LlmChatGroupResult(BaseModel):
    position: int
    turn: LlmChatTurnOut | None = None
    error: str = Field(default="", description="这一个没发出去的原因")


class LlmChatGroupSentOut(BaseModel):
    group: LlmChatGroupOut
    results: list[LlmChatGroupResult]


class LlmChatGroupSendIn(_In):
    text: str = Field(min_length=1, max_length=100_000)
    request_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=_REQUEST_ID,
        description="幂等号：重试用同一个，每个会话都不会重复发送",
    )
    takeover: bool = Field(default=False, description="不是自己建的一组要为 true（页面先让人确认）")
    attachments: dict[
        Annotated[int, Field(ge=0, lt=MAX_GROUP_TARGETS)],
        Annotated[list[Annotated[str, Field(pattern=r"^ba_[0-9a-f]{20}$")]], Field(max_length=8)],
    ] = Field(
        default={},
        description="随这个问题带上的附件：成员的位置（position）→ 传到它那个会话里的附件编号。"
        "附件属于会话，所以每个成员各传各的",
    )


# ---- 提问前用上知识库（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 6 节）----
class LlmChatNoteHit(BaseModel):
    id: UUID
    title: str
    space_id: UUID
    space_name: str
    label: str = Field(description="来源说明：人已确认 / 智能体写入 / 外部资料，和更新日期")
    excerpt: str = Field(description="正文开头一小段")
    external_allowed: bool = Field(description="所在知识库允许发给外部大模型平台")


class LlmChatSimilarSession(BaseModel):
    bridge_id: UUID
    bridge_name: str
    session_id: str
    title: str
    platform: str
    owner: str
    last_turn_at: datetime | None


class LlmChatRelatedOut(BaseModel):
    """输入框里的文字在知识库和以前的会话里找到的相近内容（只在工作台里找，不发给任何平台）。"""

    notes: list[LlmChatNoteHit]
    sessions: list[LlmChatSimilarSession]


# ---- 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2、5.3）----
class LlmBridgeDigestItem(LlmChatDigestOut):
    id: UUID
    bridge_id: UUID
    bridge_name: str
    session_id: str
    session_title: str
    owner: str = Field(description="会话是谁建的")
    owner_kind: str


class LlmBridgeDigestList(BaseModel):
    digests: list[LlmBridgeDigestItem]
    next_offset: int | None = None


class LlmBridgeDigestToggleIn(_In):
    enabled: bool = Field(description="false：关掉这个会话的纪要（已有的那篇不动）；true：重新打开")


class LlmBridgeDigestPromoteIn(_In):
    title: str | None = Field(default=None, min_length=1, max_length=200, description="新标题")
    folder: str | None = Field(
        default=None, max_length=200, description="放到哪个文件夹；默认根目录"
    )
    type: Literal["record", "decision", "lesson", "process", "runbook", "design", "plan"] = Field(
        default="record", description="转成哪种笔记"
    )
