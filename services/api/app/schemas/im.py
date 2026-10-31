"""管理员端 IM（V2，docs/v2/TRANSFORMATION_PLAN.md 第 4.4、4.5 节），由工作台后端中转 OpenIM。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5 节：
- 浏览器只调用这里的接口，从不直连 OpenIM；
- 会话、消息、置顶、免打扰属于“当前用户 + 工作空间”，只给 owner / admin；
- 消息内容是纯文本，前端按文本显示；外部图片链接不自动加载。
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import Page

ConversationKind = Literal["single", "group", "notice"]
MessageKind = Literal["text", "image", "approval_card", "system", "unsupported"]
MessageStatus = Literal["sending", "sent", "failed", "unknown"]
GroupMemberRole = Literal["owner", "member"]

MAX_PINS_PER_CONVERSATION = 10
MAX_TEXT_LENGTH = 4000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")

ConversationId = Annotated[
    str,
    StringConstraints(min_length=4, max_length=160, pattern=r"^(si|sg|sn)_[A-Za-z0-9_]+$"),
    Field(description="OpenIM 会话 ID：si_ 单聊、sg_ 群聊、sn_ 通知"),
]
GroupId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$")]
ImUserId = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
]
ServerMsgId = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
]
GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Announcement = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
ContactGroupName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求带上读取时的版本")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 状态与角标


class ImStatus(BaseModel):
    available: bool = Field(description="OpenIM 可用；不可用时只能看已投影的历史")
    account_ready: bool = Field(description="当前用户的 IM 账号已创建")
    im_user_id: str | None = Field(
        description="当前用户的 IM 用户 ID：ws_ 加用户 ID；没有账号时为 null"
    )


class ImBadges(BaseModel):
    im_unread: int = Field(description="未读消息数，不含免打扰的会话")
    pending_approvals: int = Field(description="当前工作空间待审批的工具调用数")


# ---------------------------------------------------------------- 消息


class ImImage(BaseModel):
    media_id: UUID | None = Field(
        description="图片在 OpenIM 对象存储里时，经 GET /im/media/{media_id} 显示（带登录校验）"
    )
    external_url: str | None = Field(
        description="外部图片链接：前端不自动加载，只显示为链接并提示“外部图片”"
    )
    width: int | None
    height: int | None


class ImMessageOut(BaseModel):
    server_msg_id: str | None = Field(description="OpenIM 的消息 ID；发送中的本地消息为 null")
    client_msg_id: str
    conversation_id: str
    seq: int | None = Field(description="会话内序号；投影补齐之前为 null")
    sender_im_id: str
    sender_instance_id: UUID | None = Field(description="发送人是实例时为实例 ID")
    kind: MessageKind
    text: str | None = Field(description="text 的正文；system 的说明；纯文本")
    image: ImImage | None
    at_im_ids: list[str]
    approval_execution_id: UUID | None = Field(
        description="审批卡片对应的工具调用；前端渲染成卡片，调用 /ai/tool-executions 接口"
    )
    status: MessageStatus
    sent_at: datetime
    pinned: bool


class ImMessagePage(Page[ImMessageOut]):
    """按 sent_at 倒序；next_cursor 取更早的消息。"""


class ImMessageSend(_Strict):
    kind: Literal["text", "image"]
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT_LENGTH)] | None = None
    upload_id: UUID | None = Field(default=None, description="POST /im/uploads 返回的 upload_id")
    at_im_ids: list[ImUserId] = Field(
        default_factory=list, max_length=20, description="群聊里 @ 的成员；单聊必须为空"
    )

    @model_validator(mode="after")
    def _content(self) -> Self:
        if self.kind == "text" and (self.text is None or self.upload_id is not None):
            raise ValueError("文字消息只填 text")
        if self.kind == "image" and (self.upload_id is None or self.text is not None):
            raise ValueError("图片消息只填 upload_id")
        return self


class ImUploadOut(BaseModel):
    upload_id: UUID
    mime: str
    size: int
    width: int | None
    height: int | None
    expires_at: datetime = Field(description="到期前没有用于发消息的上传会被删除")


# ---------------------------------------------------------------- 会话


class ImMessagePreview(BaseModel):
    kind: MessageKind
    text_preview: str = Field(description="最多 100 个字符的纯文本；图片为“[图片]”")
    sender_im_id: str
    sent_at: datetime


class ImConversationOut(BaseModel):
    conversation_id: str
    kind: ConversationKind
    title: str = Field(description="实例昵称、群名，或“审批通知”")
    peer_instance_id: UUID | None
    group_id: str | None
    pinned: bool
    pinned_at: datetime | None
    muted: bool = Field(description="免打扰：不发通知、不计入顶栏未读数，消息照常接收")
    unread_count: int
    read_seq: int
    max_seq: int
    last_message: ImMessagePreview | None
    last_message_at: datetime | None


class ImConversationPage(Page[ImConversationOut]):
    """置顶的在前（按 pinned_at 倒序），其余按 last_message_at 倒序。"""

    unread_total: int = Field(description="不含免打扰的会话")


class ImConversationOpen(_Strict):
    instance_id: UUID


class ImConversationUpdate(_Strict):
    pinned: bool | None = None
    muted: bool | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("字段不能为 null")
        return self


class ImReadRequest(_Strict):
    seq: int = Field(ge=0, description="读到的序号；只会增加，小于当前已读序号时不变")


class ImPinCreate(_Strict):
    server_msg_id: ServerMsgId


class ImMessagePinOut(BaseModel):
    server_msg_id: str
    pinned_by: UUID | None
    pinned_at: datetime
    message: ImMessageOut


class ImMessagePinList(BaseModel):
    items: list[ImMessagePinOut] = Field(max_length=MAX_PINS_PER_CONVERSATION)


# ---------------------------------------------------------------- 群


class ImGroupPolicyOut(BaseModel):
    relay_enabled: bool = Field(description="允许接力：关闭后只有管理员的消息能触发实例回复")
    max_agent_streak: int = Field(description="管理员不发言时，实例最多连续发几条（默认 8）")
    max_per_minute: int = Field(description="单个实例在这个群每分钟最多几条（默认 6）")
    paused: bool = Field(description="当前是否因连续条数达到上限而暂停接力")
    version: int


class ImGroupPolicyUpdate(_Strict):
    version: Version
    relay_enabled: bool
    max_agent_streak: int = Field(ge=1, le=50)
    max_per_minute: int = Field(ge=1, le=60)


class ImGroupMemberOut(BaseModel):
    im_user_id: str
    instance_id: UUID | None
    user_id: UUID | None
    nickname: str
    role: GroupMemberRole
    online: bool | None = Field(description="实例的在线状态；用户为 null")


class ImGroupOut(BaseModel):
    group_id: str
    conversation_id: str
    name: str
    announcement: str
    owner_im_id: str
    members: list[ImGroupMemberOut]
    policy: ImGroupPolicyOut
    dismissed: bool
    created_at: datetime


class _GroupMembersIn(_Strict):
    instance_ids: list[UUID] = Field(default_factory=list, max_length=50)
    user_ids: list[UUID] = Field(
        default_factory=list, max_length=20, description="同一工作空间的其他 owner / admin"
    )


class ImGroupCreate(_GroupMembersIn):
    name: GroupName
    announcement: Announcement = ""
    relay_enabled: bool = True

    @model_validator(mode="after")
    def _has_instance(self) -> Self:
        if not self.instance_ids:
            raise ValueError("群里至少要有一个实例")
        return self


class ImGroupUpdate(_Strict):
    name: GroupName | None = None
    announcement: Announcement | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("字段不能为 null")
        return self


class ImGroupMembersAdd(_GroupMembersIn):
    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.instance_ids and not self.user_ids:
            raise ValueError("至少邀请一个成员")
        return self


# ---------------------------------------------------------------- 通讯录与分组


class ImContactOut(BaseModel):
    instance_id: UUID
    im_user_id: str
    nickname: str
    status: Literal["pending", "online", "offline", "disabled"]
    contact_group_ids: list[UUID]
    conversation_id: str = Field(description="与当前用户的单聊会话 ID")


class ImContactList(BaseModel):
    items: list[ImContactOut]


class ImContactGroupOut(BaseModel):
    id: UUID
    name: str
    sort_order: int
    instance_ids: list[UUID]
    version: int


class ImContactGroupList(BaseModel):
    items: list[ImContactGroupOut]


class ImContactGroupCreate(_Strict):
    name: ContactGroupName
    sort_order: int = Field(default=0, ge=0, le=10_000)


class ImContactGroupUpdate(_Strict):
    version: Version
    name: ContactGroupName | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        return self


class ImContactGroupMembersReplace(_Strict):
    version: Version
    instance_ids: list[UUID] = Field(max_length=500)
