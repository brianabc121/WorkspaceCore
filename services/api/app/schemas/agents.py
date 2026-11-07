"""OpenClaw 实例、接入凭证、密钥库（V2，docs/v2/TRANSFORMATION_PLAN.md 第 4.1 ~ 4.3 节）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节：
- 管理员接口（/api/v1/agents/...）只给 owner；
- 实例凭证、客户端证书私钥只在接入包里出现一次，任何接口都不返回；
- 密钥只写不读：管理员接口从不返回明文，只有实例经接入入口取用（/agent-api/v1/secrets/resolve）。
"""

from datetime import datetime
from ipaddress import ip_address
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.schemas.common import Page
from app.schemas.host import AgentLoad, HostCurrent, HostPoint
from app.schemas.mail import AgentMailOut

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
    project_admin: "ProjectAdminOut" = Field(
        description="项目管理权限（T-0112）：打开后它能新建项目、把智能体拉进它建的项目"
    )
    im_media: "ProjectAdminOut" = Field(
        description="附件权限（T-0119）：打开后它能经 im_send 发图片、文件、视频（默认关闭）"
    )
    stop_others: "ProjectAdminOut" = Field(
        description="终止其他智能体任务的权限（T-0126）：打开后它能用 stop_agent_run 叫停别的智能体"
    )
    mail: AgentMailOut = Field(
        description="邮件权限（T-0129）：查询邮件默认打开；发送邮件默认关闭，打开时要选收件人范围"
    )
    bundle_outdated: bool = Field(
        default=False,
        description="已部署的实例还在用旧接入包，或插件没打工作台的补丁（上报的插件版本不是"
        " 0.3.3+myworkspace.1，T-0120）：重新签发接入包或经 SSH 重新部署",
    )
    credentials: list[AgentCredentialOut] = Field(description="有效与最近吊销的凭证，新的在前")
    secret_grants: list[SecretGrantOut]
    server_id: UUID | None = Field(
        default=None,
        description="所在的服务器（T-0144）：按上报的服务器指纹自动识别，或管理员指定",
    )
    server_manual: bool = Field(default=False, description="管理员手动指定过所在服务器")
    limits: "AgentLimits" = Field(
        default_factory=lambda: AgentLimits(mem_limit_mb=None, cpus=None),
        description="OpenClaw 容器的资源上限（T-0145，D2）：默认不限制，改了重新部署后生效",
    )
    version: int
    created_at: datetime
    updated_at: datetime


class AgentLimits(BaseModel):
    mem_limit_mb: int | None = Field(description="内存上限（MiB）；null 表示不限制")
    cpus: float | None = Field(description="CPU 上限（核，可以是小数）；null 表示不限制")


class AgentLimitsPut(_Strict):
    """设置资源上限（owner / admin）。写进接入包的 compose.yaml，重新部署这个实例后生效。"""

    mem_limit_mb: Annotated[int, Field(ge=256, le=1 << 20)] | None
    cpus: Annotated[float, Field(ge=0.1, le=256)] | None
    version: Version


class ProjectAdminOut(BaseModel):
    enabled: bool
    granted_by: UUID | None = Field(description="打开的人；他做智能体建的项目群的群主")
    granted_at: datetime | None


class ProjectAdminPut(_Strict):
    """打开或关闭项目管理权限（owner / admin）。关闭后它已经建的项目和拉进来的成员保留。"""

    enabled: bool
    version: Version


AgentInstanceOut.model_rebuild()


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


# ---------------------------------------------------------------- SSH 自动部署（T-0089）


def _host_labels(value: str) -> str:
    """域名每一段 1 ~ 63 个字符、不以连字符开头或结尾（IP 地址不检查）。"""
    try:
        ip_address(value)
    except ValueError:
        for label in value.rstrip(".").split("."):
            if not 1 <= len(label) <= 63 or label.startswith("-") or label.endswith("-"):
                raise ValueError("不是有效的主机名") from None
    return value


SshHost = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=1,
        max_length=253,
        # 域名、IPv4 或 IPv6（具体的地址检查在探测时做）
        pattern=r"^[A-Za-z0-9.:-]+$",
    ),
    AfterValidator(_host_labels),
]
SshUsername = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=32, pattern=r"^[A-Za-z_][A-Za-z0-9._-]*$"
    ),
]


class AgentSshTargetSet(_Strict):
    """填写或更换目标服务器：工作台只读取它的主机公钥（不登录），等 owner 核对指纹后确认。"""

    host: SshHost = Field(description="服务器的 IP 或域名")
    port: int = Field(default=22, ge=1, le=65535)
    username: SshUsername = Field(description="SSH 账号；之后用 sudo 安装 Docker（需要时）")
    version: Version | None = Field(
        default=None, description="已经有目标服务器时必填（更换），为当前版本号"
    )


class AgentSshConfirm(_Strict):
    version: Version
    fingerprint: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] = Field(
        description="页面上显示、owner 核对过的 SHA256 指纹；与探测结果不一致时拒绝"
    )


class AgentSshTargetOut(BaseModel):
    host: str
    address: str = Field(description="探测时解析出的地址；之后都连这个地址")
    port: int
    username: str
    host_key_algorithm: str
    host_key_fingerprint: str = Field(description="SHA256 指纹，格式与 ssh-keygen -lf 相同")
    host_key_confirmed: bool
    host_key_confirmed_at: datetime | None
    client_key_installed: bool = Field(description="是否已经在这台服务器上装了工作台的专用公钥")
    client_key_fingerprint: str | None = Field(
        default=None, description="专用公钥的 SHA256 指纹（服务器上 authorized_keys 里的那一行）"
    )
    install_dir: str | None = Field(default=None, description="接入包在服务器上的目录")
    installed_at: datetime | None = Field(default=None, description="最近一次安装成功的时间")
    version: int
    created_at: datetime
    updated_at: datetime


class AgentSshInstall(_Strict):
    """开始自动安装。登录密码只用于这一次（登录、装专用公钥、需要时用 sudo 装 Docker），不保存。"""

    version: Version = Field(description="目标服务器的当前版本号")
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="SSH 登录密码；已经装了专用公钥、且 Docker 可以直接使用时可以不填",
    )


class AgentSshUninstall(_Strict):
    """卸载：只用专用密钥。停止容器、删掉工作台写入的定时任务；可选删除接入包目录。"""

    version: Version = Field(description="目标服务器的当前版本号")
    remove_files: bool = Field(
        default=False,
        description="同时删除服务器上的接入包目录（含实例凭证、OpenClaw 的会话与记忆），不可恢复",
    )


class AgentSshRevoke(_Strict):
    """吊销专用密钥：从服务器的 authorized_keys 删掉专用公钥，再删除工作台保存的私钥。"""

    version: Version = Field(description="目标服务器的当前版本号")
    force: bool = Field(
        default=False,
        description="服务器连不上（或专用密钥已经登录不了）时仍然删除工作台保存的私钥；"
        "服务器上的那一行公钥要手动删除",
    )


SshJobStepKey = Literal[
    "connect",
    "inspect",
    "authorize_key",
    "docker",
    "reconnect",
    "upload",
    "pull",
    "plugin",
    "start",
    "schedule",
    "online",
    "reflection",
    "stop",
    "unschedule",
    "remove_files",
    # 运行服务器的部署（T-0136）：以 root 运行接入包里的 install.sh
    "install",
]


class AgentSshJobStep(BaseModel):
    key: SshJobStepKey
    status: Literal["pending", "running", "done", "skipped", "failed"]
    detail: str | None = None


class AgentSshJobOut(BaseModel):
    id: UUID
    kind: Literal["prepare", "install", "uninstall"] = Field(
        description="prepare 准备服务器（装专用公钥、装 Docker）；install 部署一个实例；"
        "uninstall 卸载一个实例"
    )
    status: Literal["running", "succeeded", "failed", "interrupted"]
    steps: list[AgentSshJobStep]
    log: str = Field(description="命令输出的末尾（最多 64 KB），密码、令牌、私钥已替换成 ***")
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime


class AgentSshState(BaseModel):
    target: AgentSshTargetOut | None
    job: AgentSshJobOut | None = Field(default=None, description="最近一次安装或卸载任务")
    warning: str | None = Field(
        default=None, description="需要 owner 手动处理的事项（例如强制吊销时服务器上留下的公钥）"
    )


# ---------------------------------------------------------------- 远程运维动作（T-0114）

OpsAction = Literal["status", "logs", "restart", "up", "prune", "report", "containers"]
# 实例级与服务器级（T-0145）：清理无用镜像、查看全部容器作用于整台服务器
InstanceOpsAction = Literal["status", "logs", "restart", "up", "report"]
ServerOpsAction = Literal["prune", "containers"]
OpsService = Literal["openclaw", "tunnel", "image-proxy"]
OpsStatus = Literal["running", "succeeded", "failed", "interrupted"]


class AgentOpsRun(_Strict):
    """对一个实例执行固定的运维动作（AGENT_OPS_PLAN 6.1）。命令写死，不接受任何命令文本。"""

    action: InstanceOpsAction = Field(
        description="status 查看状态、logs 查看日志、restart 重启 OpenClaw、"
        "up 重新拉起这个实例的全部服务、report 立即上报一次（用量与服务器状态）；"
        "清理镜像在服务器上做"
    )
    service: OpsService | None = Field(
        default=None, description="只用于 logs：看哪个服务的最近 200 行（默认 openclaw）"
    )
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="restart / up / prune 要再输一次自己的登录密码（二次认证）",
    )


class ServerOpsRun(_Strict):
    """对整台服务器执行固定的运维动作（T-0145）：查看全部容器、清理无用镜像。"""

    action: ServerOpsAction = Field(
        description="containers 查看这台服务器上所有工作台项目的容器与资源占用、prune 清理无用镜像"
    )
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="prune 要再输一次自己的登录密码（二次认证）",
    )


class AgentOpsActionOut(BaseModel):
    id: UUID
    action: OpsAction
    instance_id: UUID | None = Field(default=None, description="实例级动作的实例；服务器级为 null")
    service: OpsService | None
    status: OpsStatus
    exit_code: int | None = Field(description="最后一条命令的退出码；连不上或超时时为空")
    output: str | None = Field(
        description="脱敏后的输出（令牌、密钥、Authorization、私钥块已替换成 ***，最多 64 KB）；"
        "列表里为 null，详情接口里才有"
    )
    truncated: bool = Field(description="输出超过 64 KB，只保留了末尾")
    error_code: str | None
    error_message: str | None
    started_by: UUID | None
    started_at: datetime
    finished_at: datetime | None
    expires_at: datetime = Field(description="记录与输出保留到这个时间（7 天）")


class AgentOpsState(BaseModel):
    available: bool = Field(description="部署到了一台 SSH 服务器、专用密钥还在：可以执行运维动作")
    reason: Literal["not_deployed", "key_missing"] | None = Field(
        description="不可用的原因：not_deployed 还没部署到服务器"
        "（手动接入的实例先部署到一台服务器）；key_missing 这台服务器的专用密钥已吊销"
    )
    running: AgentOpsActionOut | None = Field(
        description="这台服务器上进行中的动作（同一台服务器同时只有一个）"
    )
    items: list[AgentOpsActionOut] = Field(description="最近 20 次（7 天内），最近的在前")


# ---------------------------------------------------------------- 唤醒（T-0121）

WakeupRepeat = Literal["none", "daily", "weekdays", "weekly"]


class WakeupClaimItem(BaseModel):
    id: UUID
    session_key: str = Field(description="openclaw agent --session-key 的值")
    message: str = Field(description="这一轮交给智能体的消息（说明 + 提示词）")


class WakeupClaimResponse(BaseModel):
    items: list[WakeupClaimItem] = Field(description="到期、刚领取的唤醒（最多 5 个）")


class WakeupResult(_Strict):
    status: Literal["done", "failed"]
    error: Annotated[str, StringConstraints(max_length=300)] | None = None


class WakeupResultResponse(BaseModel):
    id: UUID
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    run_at: datetime = Field(description="重复的唤醒是下一次的时间")


class AgentWakeupCreate(_Strict):
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    target: (
        Annotated[
            str,
            StringConstraints(min_length=6, max_length=72, pattern=r"^(user|group):[A-Za-z0-9_]+$"),
        ]
        | None
    ) = Field(default=None, description="在哪个会话里跑；null 是独立会话")
    run_at: AwareDatetime
    repeat: WakeupRepeat = "none"
    time_zone: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "UTC"


class AgentWakeupOut(BaseModel):
    id: UUID
    source: Literal["agent", "admin", "issue"]
    created_by: UUID | None
    target: str | None
    prompt: str
    run_at: datetime
    repeat: WakeupRepeat
    time_zone: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    runs: int
    last_run_at: datetime | None
    last_error: str | None
    created_at: datetime


class WakeupTarget(BaseModel):
    target: str
    label: str


class AgentWakeupList(BaseModel):
    items: list[AgentWakeupOut] = Field(description="待执行的在前，其余按时间倒序，最多 50 个")
    targets: list[WakeupTarget] = Field(description="可以选的会话：和管理员的私聊、它所在的群")


# ---------------------------------------------------------------- 终止正在执行的任务（T-0126）

RunStopStatus = Literal["pending", "running", "done", "failed", "expired"]


class AgentRunStopCreate(_Strict):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    task_id: UUID | None = Field(
        default=None, description="它正在执行的任务；给了就把这一段记为中断"
    )


class AgentRunStopOut(BaseModel):
    id: UUID
    instance_id: UUID
    requested_by: UUID | None
    requested_by_instance: UUID | None
    task_id: UUID | None
    reason: str
    status: RunStopStatus = Field(
        description="pending 等实例领取；running 实例在中止；done 已中止；failed 失败；"
        "expired 10 分钟没被领取（实例不在线或接入包太旧，只有软停止生效）"
    )
    soft_until: datetime
    aborted: int | None = Field(description="实例报回中止了几个会话")
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class AgentRunStopList(BaseModel):
    items: list[AgentRunStopOut] = Field(description="最近 20 次，新的在前")


class StopClaimItem(BaseModel):
    id: UUID


class StopClaimResponse(BaseModel):
    items: list[StopClaimItem] = Field(
        description="待执行的终止令；实例中止所有正在运行的会话后逐个报回"
    )


class StopResult(_Strict):
    status: Literal["done", "failed"]
    aborted: int = Field(0, ge=0, le=1000)
    error: Annotated[str, StringConstraints(max_length=300)] | None = None


class StopResultResponse(BaseModel):
    id: UUID
    status: RunStopStatus


# ---------------------------------------------------------------- OpenClaw 服务器（T-0145、T-0147）

ServerName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class AgentServerCreate(_Strict):
    """添加一台服务器：工作台只读取它的主机公钥（不登录），等 owner 核对指纹后确认。"""

    name: ServerName
    host: SshHost = Field(description="服务器的 IP 或域名")
    port: int = Field(default=22, ge=1, le=65535)
    username: SshUsername = Field(description="SSH 账号；之后用 sudo 安装 Docker（需要时）")


class AgentServerUpdate(_Strict):
    name: ServerName
    version: Version


class AgentServerAddress(_Strict):
    """更换地址或账号：重新读取主机公钥，要重新确认；已经装了专用公钥时先吊销。"""

    host: SshHost
    port: int = Field(default=22, ge=1, le=65535)
    username: SshUsername
    version: Version


class AgentServerPrepare(_Strict):
    """准备服务器：用一次登录密码装上工作台的专用公钥、需要时用 sudo 安装 Docker。密码不保存。"""

    version: Version
    password: SecretStr = Field(min_length=1, max_length=1024)


class ServerSshOut(BaseModel):
    host: str
    address: str = Field(description="探测时解析出的地址；之后都连这个地址")
    port: int
    username: str
    host_key_algorithm: str
    host_key_fingerprint: str = Field(description="SHA256 指纹，格式与 ssh-keygen -lf 相同")
    host_key_confirmed: bool
    host_key_confirmed_at: datetime | None
    client_key_installed: bool = Field(description="这台服务器上装了工作台的专用公钥")
    client_key_fingerprint: str | None
    prepared_at: datetime | None = Field(description="准备好（专用公钥、Docker 可用）的时间")


class AgentServerOut(BaseModel):
    id: UUID
    name: str
    kind: Literal["auto", "ssh"] = Field(
        description="auto 按上报的服务器指纹自动识别（手动部署的实例）；ssh 管理员添加、经 SSH 部署"
    )
    ssh: ServerSshOut | None
    reported_at: datetime | None = Field(description="最近一次有实例上报整机数据的时间")
    stale: bool = Field(description="超过 3 分钟没有任何实例上报")
    cores: int | None
    cpu_percent: float | None
    load15: float | None
    mem_total: int | None
    mem_used_percent: float | None
    disk_used_percent: float | None = Field(description="两个分区里较高的已用百分比")
    instances_total: int
    instances_online: int
    alerts: list[Literal["disk_low", "memory_low", "load_high", "not_reporting"]]
    version: int
    created_at: datetime
    updated_at: datetime


class UnassignedInstance(BaseModel):
    id: UUID
    name: str
    status: InstanceStatus


class AgentServerList(BaseModel):
    items: list[AgentServerOut]
    unassigned: list[UnassignedInstance] = Field(description="还没有归属任何服务器的实例")


class RunningTaskRef(BaseModel):
    id: UUID
    title: str


class ServerAgentRow(BaseModel):
    """服务器详情里每个智能体一行。"""

    instance_id: UUID
    name: str
    status: InstanceStatus
    deployed: bool = Field(description="经 SSH 部署在这台服务器上（不是只按指纹识别的）")
    install_dir: str | None
    mem_limit_mb: int | None
    cpus: float | None
    load: AgentLoad | None = Field(description="这个智能体自己的当前用量；没上报过为 null")
    alerts: list[Literal["mem_near_limit", "restarting", "cpu_hog"]]
    tokens_1h: int = Field(description="最近一小时的 token 用量（输入 + 输出）")
    running_task: RunningTaskRef | None = Field(
        description="正在执行的任务（时间轴上没结束的一段）"
    )


class AgentServerDetail(BaseModel):
    server: AgentServerOut
    current: HostCurrent | None = Field(description="整机的当前值")
    range: Literal["24h", "7d", "30d"]
    points: list[HostPoint] = Field(description="整机每小时的平均与峰值")
    agents: list[ServerAgentRow]
    job: AgentSshJobOut | None = Field(description="这台服务器上最近一次 SSH 任务")
    warning: str | None = None


class AgentDeployIn(_Strict):
    """把实例部署到一台 SSH 服务器（只替换这个实例自己的目录与 Compose 项目）。"""

    server_id: UUID
    version: Version = Field(description="实例的当前版本号")
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="服务器还没准备（没装专用公钥）时必填，只用这一次；准备好的服务器不用填",
    )


class AgentUndeployIn(_Strict):
    version: Version = Field(description="实例的当前版本号")
    remove_files: bool = Field(
        default=False,
        description="同时删除服务器上这个实例的目录（含实例凭证、OpenClaw 的会话与记忆），不可恢复",
    )


class AgentServerAssign(_Strict):
    """手动指定实例所在的服务器（手动部署的实例）；null 表示改回按指纹自动识别。"""

    server_id: UUID | None
    version: Version


class AgentDeploymentOut(BaseModel):
    server_id: UUID
    install_dir: str | None
    installed_at: datetime | None
    version: int


class AgentDeploymentState(BaseModel):
    deployment: AgentDeploymentOut | None
    server: AgentServerOut | None = Field(description="所在的服务器（部署或自动识别、手动指定）")
    job: AgentSshJobOut | None = Field(description="这个实例最近一次部署或卸载任务")
    capacity_warning: str | None = Field(
        default=None, description="部署前的容量提醒（D4：不设硬上限，剩余内存或磁盘不多时提醒）"
    )
