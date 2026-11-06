"""Gmail 邮件的接口模型（T-0128，docs/v2/MAIL_PLAN.md）。

邮件的发件人、主题、正文、附件名都是外部输入：页面按纯文字显示（不渲染 HTML）。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

MailAccountStatus = Literal["active", "needs_reconnect", "disconnected"]
MailSendStatus = Literal["sending", "sent", "failed", "unknown"]
Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
Recipients = Annotated[list[Address], Field(max_length=20)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MailSettingsOut(BaseModel):
    configured: bool = Field(description="已经填了 OAuth 客户端")
    client_id: str | None
    client_secret_set: bool
    redirect_uri: str = Field(description="在 Google Cloud 的 OAuth 客户端里登记的授权回调地址")
    version: int | None


class MailSettingsPut(_Strict):
    client_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=10, max_length=200)
    ]
    client_secret: SecretStr | None = Field(
        default=None, description="第一次必填；之后不填表示保持不变"
    )
    version: int | None = None


class MailConnectIn(_Strict):
    login_hint: Address | None = Field(default=None, description="预先填好要连接的 Gmail 地址")


class MailConnectOut(BaseModel):
    authorize_url: str = Field(description="浏览器打开这个地址去 Google 授权；10 分钟内有效")


class MailAgentRef(BaseModel):
    id: UUID
    name: str


class MailAgentList(BaseModel):
    """可以分配邮箱的智能体实例（admin 在 设置 → 邮件 里勾选用；只有 ID 与名字）。"""

    items: list[MailAgentRef]


class MailAccountOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    status: MailAccountStatus
    can_read: bool = Field(description="授权里有 gmail.readonly")
    can_send: bool = Field(description="授权里有 gmail.send")
    agents: list[MailAgentRef] = Field(
        description="分配了这个邮箱的智能体实例（T-0132）；没分配的智能体看不到这个邮箱"
    )
    agent_signature: bool = Field(description="智能体发的信末尾加署名行")
    last_error: str | None
    connected_at: datetime
    version: int


class MailAccountList(BaseModel):
    items: list[MailAccountOut]


class MailAccountPatch(_Strict):
    version: int
    agent_signature: bool | None = None


class MailAccountAgentsPut(_Strict):
    """把邮箱分配给哪些智能体实例（整组替换，T-0132）。"""

    version: int = Field(description="邮箱的 version")
    instance_ids: Annotated[list[UUID], Field(max_length=200)]


class MailLabelOut(BaseModel):
    id: str
    name: str
    type: str
    unread: int | None = Field(description="未读数；Gmail 没给时为空")


class MailLabelList(BaseModel):
    items: list[MailLabelOut]


class MailAttachmentOut(BaseModel):
    attachment_id: str | None
    filename: str
    mime_type: str
    size: int


class MailSummary(BaseModel):
    id: str
    thread_id: str
    sender: str = Field(description="From 头（原样，外部输入）")
    to: str
    cc: str
    subject: str
    date: datetime | None
    snippet: str
    unread: bool
    labels: list[str]


class MailMessageOut(MailSummary):
    text: str = Field(description="纯文字正文；只有 HTML 时已转成纯文字")
    truncated: bool
    attachments: list[MailAttachmentOut]


class MailList(BaseModel):
    items: list[MailSummary]
    next_page_token: str | None
    estimate: int


class MailThreadOut(BaseModel):
    thread_id: str
    messages: list[MailMessageOut]


class MailSendIn(_Strict):
    to: Annotated[list[Address], Field(min_length=1, max_length=20)]
    cc: Recipients = []
    bcc: Recipients = []
    subject: Annotated[str, StringConstraints(max_length=200)] = Field(
        default="", description="回复时可以不填，用原信的主题加 Re:"
    )
    body: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    reply_to_message_id: (
        Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")] | None
    ) = None


class MailSendOut(BaseModel):
    id: UUID
    account_id: UUID | None
    actor_kind: Literal["user", "instance"]
    actor_name: str
    sender: str
    to: list[str]
    cc: list[str]
    bcc_count: int
    subject: str
    status: MailSendStatus
    error_code: str | None
    gmail_message_id: str | None
    gmail_thread_id: str | None
    created_at: datetime
    finished_at: datetime | None


class MailSendList(BaseModel):
    items: list[MailSendOut]


class AgentMailAccountRef(BaseModel):
    id: UUID
    email: str
    status: MailAccountStatus
    can_send: bool = Field(description="授权里有 gmail.send")


class AgentMailOut(BaseModel):
    """实例的邮件开关（T-0129）与分配给它的邮箱（T-0132）。"""

    accounts: list[AgentMailAccountRef] = Field(
        description="分配给这个实例的邮箱；邮件工具只看得到这些邮箱"
    )
    read_enabled: bool
    send_enabled: bool
    send_scope: Literal["any", "listed"] | None
    recipients: list[str]
    send_by: UUID | None
    send_at: datetime | None
    version: int


class AgentMailPut(_Strict):
    version: int
    read_enabled: bool
    send_enabled: bool
    send_scope: Literal["any", "listed"] | None = Field(
        default=None, description="打开发送时必选：any 任何地址 / listed 只限下列地址或域名"
    )
    recipients: Annotated[list[Address], Field(max_length=50)] = Field(
        default=[], description="地址（a@b.com）或域名（@b.com）；send_scope=listed 时用"
    )
    account_ids: Annotated[list[UUID], Field(max_length=50)] | None = Field(
        default=None, description="分配给这个实例的邮箱（整组替换）；不填表示不变"
    )
