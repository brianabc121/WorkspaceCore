"""OpenClaw 实例、接入凭证、密钥库（V2，docs/v2/TRANSFORMATION_PLAN.md 第 4.1 ~ 4.3 节）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节：
- 管理员接口（/api/v1/agents/...）只给 owner；
- 实例凭证、客户端证书私钥只在接入包里出现一次，任何接口都不返回；
- 密钥只写不读：管理员接口从不返回明文，只有实例经接入入口取用（/agent-api/v1/secrets/resolve）。
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.schemas.common import Page

NetworkMode = Literal["intranet", "public"]
InstanceStatus = Literal["pending", "online", "offline", "disabled"]
McpScope = Literal["read", "read_write"]
MaxRole = Literal["admin", "member", "viewer"]
CredentialKind = Literal["token", "client_cert"]
SecretKind = Literal["model_api_key", "api_token", "generic"]
SecretAccessResult = Literal["ok", "not_found", "rate_limited"]

MAX_SECRET_GRANTS = 32
MAX_RESOLVE_IDS = 32
SECRET_VALUE_MAX = 8192

InstanceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
InstanceDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=500),
    Field(description="所在服务器等说明；纯文本"),
]
ImNickname = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
    Field(description="IM 里显示的昵称"),
]
SecretName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$"
    ),
    Field(description="工作空间内唯一，只用于显示"),
]
RefId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
    Field(description="OpenClaw 配置里 SecretRef 的 id，例如 openai_api_key"),
]
SecretValue = Annotated[
    SecretStr,
    Field(min_length=1, max_length=SECRET_VALUE_MAX, description="明文只写不读"),
]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求带上读取时的版本")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 实例


class AgentCredentialOut(BaseModel):
    """凭证的元数据；令牌只存哈希，证书只存指纹，这里都不返回原文。"""

    id: UUID
    kind: CredentialKind
    ingress: NetworkMode = Field(description="凭证只在这个接入入口有效")
    token_prefix: str | None = Field(description="令牌前 12 个字符，用于辨认；证书为 null")
    cert_fingerprint: str | None = Field(
        description="客户端证书 SHA-256 指纹（十六进制）；令牌为 null"
    )
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None = Field(description="已吊销或已过轮换宽限期时不为 null")
    last_used_at: datetime | None
    last_used_ip: str | None


class SecretGrantOut(BaseModel):
    secret_id: UUID
    secret_name: str
    ref_id: str


class AgentInstanceOut(BaseModel):
    id: UUID
    name: str
    description: str
    network_mode: NetworkMode
    status: InstanceStatus = Field(
        description="pending 待接入；online / offline 以 OpenIM 上下线回调为准；disabled 已停用"
    )
    im_ready: bool = Field(description="OpenIM 账号与好友关系已就绪；为 false 时后台会重试")
    im_user_id: str = Field(description="OpenIM 用户 ID：oc_ 加实例 ID（32 位十六进制）")
    im_nickname: str
    scope: McpScope = Field(
        description="MCP 权限范围：read 只读；read_write 可以发起写操作（仍需审批）"
    )
    max_role: MaxRole = Field(
        description="MCP 调用的最高角色；实际角色取它与登记人角色中较低的一个"
    )
    acting_user_id: UUID | None = Field(description="MCP 调用以这个用户的身份执行（登记的 owner）")
    reported_version: str | None = Field(description="实例上报的 OpenClaw 版本")
    plugin_version: str | None = Field(description="实例上报的 openclaw-channel 版本")
    last_seen_at: datetime | None
    bundle_issued_at: datetime | None = Field(
        description="最近一次生成接入包的时间；从未生成为 null"
    )
    im_token_expires_at: datetime | None = Field(
        default=None,
        description="最近一次签发的 IM 令牌何时到期（实例每次启动时领取）；从未签发为 null",
    )
    disabled_at: datetime | None
    credentials: list[AgentCredentialOut] = Field(description="有效与最近吊销的凭证，新的在前")
    secret_grants: list[SecretGrantOut]
    version: int
    created_at: datetime
    updated_at: datetime


class AgentInstancePage(Page[AgentInstanceOut]):
    agents_enabled: bool = Field(description="工作空间的全局总开关；为 false 时所有实例都被拒绝")


class AgentInstanceCreate(_Strict):
    name: InstanceName
    description: InstanceDescription = ""
    network_mode: NetworkMode
    scope: McpScope = "read"
    max_role: MaxRole = "member"
    im_nickname: ImNickname | None = Field(default=None, description="不填时用 name")


class AgentInstanceUpdate(_Strict):
    """只发送要改的字段，都不能为 null。网络模式不能修改：换模式请新建实例。"""

    version: Version
    name: InstanceName | None = None
    description: InstanceDescription | None = None
    scope: McpScope | None = None
    max_role: MaxRole | None = None
    im_nickname: ImNickname | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        return self


class VersionBody(_Strict):
    version: Version


class BundleRequest(_Strict):
    previous: Literal["keep_24h", "revoke_now"] = Field(
        default="keep_24h",
        description="已有的有效凭证：keep_24h 再保留 24 小时（轮换）；revoke_now 立即吊销",
    )
    version: Version


class SecretGrantIn(_Strict):
    secret_id: UUID
    ref_id: RefId


class SecretGrantsReplace(_Strict):
    """整体替换这个实例的密钥授权；ref_id 不能重复。"""

    version: Version
    grants: list[SecretGrantIn] = Field(max_length=MAX_SECRET_GRANTS)

    @model_validator(mode="after")
    def _unique_refs(self) -> Self:
        refs = [grant.ref_id for grant in self.grants]
        if len(refs) != len(set(refs)):
            raise ValueError("ref_id 不能重复")
        return self


class AgentSettingsOut(BaseModel):
    agents_enabled: bool
    version: int
    updated_at: datetime | None
    updated_by: UUID | None


class AgentSettingsUpdate(_Strict):
    version: Version
    agents_enabled: bool


# ---------------------------------------------------------------- 密钥库


class SecretGrantRef(BaseModel):
    instance_id: UUID
    instance_name: str
    ref_id: str


class AgentSecretOut(BaseModel):
    """从不包含明文。"""

    id: UUID
    name: str
    kind: SecretKind
    description: str
    value_version: int = Field(description="当前值的版本；每次轮换加 1，只下发当前版本")
    last_rotated_at: datetime
    grants: list[SecretGrantRef]
    version: int = Field(description="这条记录的乐观并发版本号（与 value_version 无关）")
    created_at: datetime
    updated_at: datetime


class AgentSecretPage(Page[AgentSecretOut]):
    pass


class AgentSecretCreate(_Strict):
    name: SecretName
    kind: SecretKind = "generic"
    description: InstanceDescription = ""
    value: SecretValue


class AgentSecretUpdate(_Strict):
    version: Version
    name: SecretName | None = None
    description: InstanceDescription | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        return self


class AgentSecretRotate(_Strict):
    version: Version
    value: SecretValue


class SecretAccessOut(BaseModel):
    id: UUID
    instance_id: UUID | None = Field(description="实例被删除后为 null")
    instance_name: str
    ref_id: str
    secret_id: UUID | None = Field(description="没有授权或不存在时为 null")
    value_version: int | None
    result: SecretAccessResult
    source_ip: str | None
    at: datetime


class SecretAccessPage(Page[SecretAccessOut]):
    pass


# ---------------------------------------------------------------- 实例接口（/agent-api/v1）


class SecretResolveRequest(_Strict):
    ids: list[RefId] = Field(min_length=1, max_length=MAX_RESOLVE_IDS)


class SecretResolveError(BaseModel):
    code: Literal["NOT_FOUND"] = Field(description="不区分“不存在”和“没有授权”")


class SecretResolveResponse(BaseModel):
    """与 OpenClaw exec SecretRef 的返回结构对应（取密钥小程序再加上 protocolVersion）。"""

    values: dict[str, str]
    errors: dict[str, SecretResolveError]


class ImTokenResponse(BaseModel):
    token: str = Field(description="OpenIM 用户令牌；签发会踢掉同一实例正在用的旧连接（OC-0 F1）")
    im_user_id: str
    platform_id: int = Field(description="固定为 5（Web），与 openclaw-channel 一致")
    expires_at: datetime | None = Field(description="OpenIM 返回的到期时间；无法确定时为 null")
    ws_addr: str | None = Field(description="要覆盖接入包里的消息网关地址时才给出")
    api_addr: str | None = Field(description="要覆盖接入包里的 REST 地址时才给出")


class HeartbeatRequest(_Strict):
    openclaw_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    plugin_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None


class HeartbeatResponse(BaseModel):
    server_time: datetime
