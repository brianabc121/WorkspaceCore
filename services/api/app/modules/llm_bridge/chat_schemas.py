"""大模型聚合聊天页面接口的请求 / 响应模型（docs/v2/LLM_CHAT_PLAN.md 第 4.2 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.llm_bridge.schemas import LlmBridgeEnvironmentOut, Platform

OwnerKind = Literal["me", "user", "agent", "external"]
_REQUEST_ID = r"^[\w.\-:]+$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class LlmChatSessionList(BaseModel):
    sessions: list[LlmChatSessionOut]
    next_offset: int | None = None
    sync_failed: str = Field(default="", description="同步官方程序里的会话时读不到的平台")


class LlmChatStepOut(BaseModel):
    kind: str = Field(description="message（中间说的话）/ command / files / notice")
    text: str
    at: float | None = None


class LlmChatMessageOut(BaseModel):
    role: str = Field(description="user / assistant")
    turn_id: str | None = None
    text: str
    at: float | None = None
    by: str | None = Field(default=None, description="谁发的（只有 user 消息有）")
    mine: bool | None = Field(default=None, description="是不是当前用户发的（只有 user 消息有）")
    status: str | None = Field(
        default=None,
        description="回答所在回合的状态："
        "queued / running / completed / failed / cancelled / unknown",
    )
    steps: list[LlmChatStepOut] = Field(
        default=[], description="过程：中间说的话、命令、改动的文件"
    )
    failure: dict[str, Any] | None = None
    ahead_in_session: int | None = Field(default=None, description="排队时前面还有几条")


class LlmChatConversationOut(BaseModel):
    session: LlmChatSessionOut
    messages: list[LlmChatMessageOut]
    last_event_id: int = Field(description="读事件时从它之后开始")


class LlmChatEventOut(BaseModel):
    id: int
    turn_id: str | None
    kind: str = Field(
        description="queued / started / message / command / files / notice / "
        "completed / failed / cancelled / unknown"
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


class LlmChatRenameIn(_In):
    title: str = Field(min_length=1, max_length=120)


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
