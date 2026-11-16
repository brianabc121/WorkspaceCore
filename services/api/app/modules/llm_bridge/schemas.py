"""大模型会话桥页面接口的请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["chatgpt", "codex", "claude", "workbuddy"]
Slot = Literal["openai", "claude", "workbuddy"]
ALL_PLATFORMS: tuple[str, ...] = ("chatgpt", "codex", "claude", "workbuddy")
Sandbox = Literal["read_only", "workspace_write", "exec"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmBridgeIn(_In):
    name: str = Field(min_length=1, max_length=40, description="显示名，也是工具里 bridge 参数的值")
    base_url: str = Field(
        min_length=8, max_length=300, description="会话桥的地址，例如 http://10.0.0.5:8787"
    )
    token: str = Field(min_length=32, max_length=200, description="会话桥的管理令牌")
    platforms: list[Platform] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
        description="这个 MCP 管理器控制的平台。填了就在登记时写进会话桥，同时验证地址和令牌："
        "连不上或令牌不对时不登记；不填则不验证，会话桥保持原来的设置",
    )


class LlmBridgePatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    base_url: str | None = Field(default=None, min_length=8, max_length=300)
    token: str | None = Field(default=None, min_length=32, max_length=200)
    enabled: bool | None = None


class LlmBridgeInstallOut(BaseModel):
    """工作台部署出去的 MCP 管理器在哪（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md）。"""

    target_kind: Literal["agent_server", "runner_host"] = Field(
        description="agent_server：智能体服务器；runner_host：容器服务器"
    )
    target_id: UUID | None = Field(description="服务器已从工作台移除时为空")
    server_name: str
    mode: str = Field(
        description="same_host：加入工作台的内部网络；tunnel：只监听服务器本机，经 SSH 通道访问"
    )
    port: int | None = Field(description="tunnel：服务器本机地址上的端口")
    needs_sudo: bool = Field(description="重新部署、重启、卸载时要填一次服务器的登录密码")
    outdated: bool = Field(description="工作台带的构建文件比部署的新：可以重新部署来升级")
    deployed_at: datetime


class LlmBridgeOut(BaseModel):
    id: UUID
    name: str
    base_url: str
    enabled: bool
    has_token: bool
    deployment: LlmBridgeInstallOut | None = Field(
        default=None,
        description="工作台一键部署的管理器：部署在哪台智能体服务器上；手动添加的为空",
    )
    created_at: datetime
    updated_at: datetime


class LlmBridgeLocalIn(_In):
    name: str = Field(default="本机", min_length=1, max_length=40, description="显示名")
    platforms: list[Platform] = Field(
        min_length=1, max_length=4, description="这个 MCP 管理器控制的平台"
    )


class LlmBridgeLocalOut(BaseModel):
    """随工作台一起启动的那个 MCP 管理器（“本机部署”，T-0238）。"""

    available: bool = Field(description="这个工作台随带着本机的 MCP 管理器，可以直接添加")
    bridge_id: UUID | None = Field(description="这个工作空间已经添加了它：对应的管理器")
    taken: bool = Field(
        description="已经被别的工作空间添加：一个管理器的账号和会话只归一个工作空间，这里不能再加"
    )


class LlmBridgeList(BaseModel):
    bridges: list[LlmBridgeOut]


class LlmBridgeAccountOut(BaseModel):
    slot: str
    label: str
    enabled: bool = Field(
        default=True,
        description="这个 MCP 管理器是否控制这个账号对应的平台；"
        "不控制时不能登录，已有的登录可以退出",
    )
    state: str = Field(description="logged_in / logged_out / mismatch / unavailable")
    identity: str | None = Field(
        description="账号标识（打码）；用密钥或令牌登录时会话桥看不到是哪个账号，为空"
    )
    registered: bool = Field(
        default=False,
        description="这座会话桥是否已经登记过这个平台的账号（退出登录后仍然保留，“更换账号”才清掉）",
    )
    method: str | None = Field(description="登录方式；没有登录时为空")
    detail: str
    first_login_at: float | None
    login_methods: list[str]
    running: int


class LlmBridgeChannelOut(BaseModel):
    platform: str
    mode: str
    slot: str
    enabled: bool = Field(default=True, description="这个 MCP 管理器是否控制这个平台")
    available: bool
    note: str


class LlmBridgeSettings(BaseModel):
    concurrency: int = Field(
        ge=1, le=16, description="每个平台同时进行的会话数（超过的排队等名额）"
    )
    min_start_interval_seconds: int = Field(
        default=0,
        ge=0,
        le=60,
        description="同一个平台两次开始之间至少隔几秒；旧版本的管理器没有这一项",
    )
    turn_timeout_seconds: int = Field(ge=30, le=21600)
    send_per_hour: int = Field(ge=1, le=10000, description="每个实例每小时的发送次数")
    max_text_chars: int = Field(ge=1, le=1_000_000)
    claude_subscription_ack: bool = Field(description="已确认了解 Claude 订阅登录的合规风险")
    auto_resume: bool = Field(
        default=False,
        description="额度用完被打断的会话，到重置时间后自动发一句“继续”；旧版本的管理器没有这一项",
    )
    auto_resume_max: int = Field(default=3, ge=1, le=10, description="同一次中断最多自动续几次")
    resume_text: str = Field(default="", description="自动续跑时发的那句话")
    retention_days: int = Field(
        default=0,
        ge=0,
        le=3650,
        description="对话内容保留多少天（按会话最后活动算）；0 是一直保留。到期只清内容："
        "会话、回合的状态和用量留着。旧版本的管理器没有这一项",
    )
    platforms: list[str] = Field(
        default=list(ALL_PLATFORMS), description="这个 MCP 管理器控制的平台"
    )


class LlmBridgeSettingsPatch(_In):
    concurrency: int | None = Field(default=None, ge=1, le=16)
    min_start_interval_seconds: int | None = Field(default=None, ge=0, le=60)
    turn_timeout_seconds: int | None = Field(default=None, ge=30, le=21600)
    send_per_hour: int | None = Field(default=None, ge=1, le=10000)
    max_text_chars: int | None = Field(default=None, ge=1, le=1_000_000)
    claude_subscription_ack: bool | None = None
    auto_resume: bool | None = None
    auto_resume_max: int | None = Field(default=None, ge=1, le=10)
    resume_text: str | None = Field(default=None, min_length=1, max_length=200)
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    platforms: list[Platform] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
        description="这个 MCP 管理器控制的平台（至少一个）。取消勾选的平台不能登录、"
        "不能新建会话和发送；已有的登录和会话记录保留",
    )


class LlmBridgeLoadOut(BaseModel):
    running: int = Field(description="这个平台进行中的会话数")
    waiting: int = Field(description="在排队等名额的会话数")
    limit: int = Field(description="同时进行的上限")


class LlmBridgeQuotaWindowOut(BaseModel):
    kind: str = Field(description="哪个窗口：five_hour / weekly / 平台给的别的名字")
    used_percent: float | None = Field(description="已用百分比；重置时间已过或平台没给时为空")
    resets_at: float | None = Field(description="什么时候重置（时间戳）")
    reset_passed: bool = Field(description="重置时间已经过了：等下一次回合确认新的用量")
    status: str = Field(description="ok / warning / exceeded")
    plan: str | None = Field(default=None, description="套餐（平台给了才有）")
    observed_at: float = Field(description="什么时候看到的")


class LlmBridgeQuotaOut(BaseModel):
    """一个平台的额度（docs/v2/LLM_BRIDGE_QUOTA_PLAN.md）。

    平台给的是已用百分比，不是剩余 token 数。
    """

    windows: list[LlmBridgeQuotaWindowOut]
    can_refresh: bool = Field(
        description="能不能随时向平台查（Codex 能；Claude 只在跑回合时顺带报出来）"
    )
    reports_quota: bool = Field(
        default=True,
        description="平台给不给额度（已用百分比、重置时间）。"
        "CodeBuddy 的官方程序不给：windows 一直是空的，只有 tokens_5h 和 credits_5h",
    )
    paused_until: float | None = Field(description="因为额度用完暂停到什么时候；没暂停为空")
    pause_estimated: bool = Field(description="平台没说什么时候重置，暂停的时间是估计的")
    waiting_resume: int = Field(description="有几个被打断的会话在等自动续跑")
    tokens_5h: int = Field(description="最近 5 小时经这个管理器用掉的 token（管理器自己记的）")
    credits_5h: float | None = Field(
        default=None,
        description="最近 5 小时经这个管理器消耗的积分（各回合官方程序报的数相加）。"
        "只有报积分的平台（CodeBuddy）有；是消耗掉的，不是剩余的",
    )


class LlmBridgeQuotaList(BaseModel):
    platforms: dict[str, LlmBridgeQuotaOut]


class LlmBridgeConnectivityTargetOut(BaseModel):
    host: str = Field(description="平台的地址（域名）")
    label: str = Field(description="这个地址是做什么用的")
    ok: bool = Field(description="可用：连得上，平台也正常应答")
    state: str = Field(
        description="ok 可用；blocked 连得上但被平台拒绝（这台服务器的 IP 或地区不被允许）；"
        "unreachable 连不上"
    )
    latency_ms: int | None = Field(description="从发起到收到应答的毫秒数；连不上时为空")
    http_status: int | None = Field(default=None, description="平台回的 HTTP 状态码")
    error: str | None = Field(default=None, description="不可用时的说明")


class LlmBridgeConnectivitySlotOut(BaseModel):
    ok: bool = Field(description="这个平台在管理器所在的服务器上能不能用")
    need: str = Field(description="all：列出的地址都要可用；any：有一个可用就行")
    checked_at: float = Field(description="什么时候测的")
    targets: list[LlmBridgeConnectivityTargetOut]


class LlmBridgeConnectivityOut(BaseModel):
    """管理器所在的服务器到各平台的连通性与延迟（T-0240）。

    键是账号槽位：openai / claude / workbuddy。
    """

    supported: bool = Field(description="这个管理器的版本支持检测；旧版本为 false")
    slots: dict[str, LlmBridgeConnectivitySlotOut]


class LlmBridgeStatusOut(BaseModel):
    reachable: bool
    error: str = Field(default="", description="连不上时的原因")
    version: str = ""
    platforms: list[str] = Field(default=[], description="这个 MCP 管理器控制的平台")
    accounts: list[LlmBridgeAccountOut] = []
    channels: list[LlmBridgeChannelOut] = []
    queued: int = 0
    running: int = 0
    load: dict[str, LlmBridgeLoadOut] = Field(
        default={}, description="每个平台的进行中 / 等待 / 上限；旧版本的管理器没有"
    )
    quota: dict[str, LlmBridgeQuotaOut] = Field(
        default={}, description="每个平台的额度与暂停情况；旧版本的管理器没有"
    )
    settings: LlmBridgeSettings | None = None


class LlmBridgeLoginIn(_In):
    method: Literal["device_code", "api_key", "oauth_token", "existing", "web_cn", "web_intl"] = (
        Field(
            description="web_cn / web_intl：CodeBuddy的网页登录，国内站 / 国际站——"
            "返回 pending 和一个网址，人在自己的浏览器里打开并登录"
        )
    )
    api_key: str | None = Field(default=None, max_length=500)
    oauth_token: str | None = Field(default=None, max_length=2000)


class LlmBridgeLoginOut(BaseModel):
    state: str = Field(description="pending / done / failed / none")
    verification_url: str | None = None
    user_code: str | None = None
    detail: str = ""


class LlmBridgeAccountStateOut(BaseModel):
    state: str
    identity: str | None
    detail: str


class LlmBridgeResetOut(BaseModel):
    archived_sessions: int


class LlmBridgeEnvironmentIn(_In):
    platform: Platform
    mode: Literal["dev", "cloud"] = "dev"
    name: str = Field(min_length=1, max_length=60)
    repo_url: str | None = Field(default=None, max_length=500, description="只支持 https://")
    branch: str | None = Field(default=None, max_length=200)
    sandbox: Sandbox = "read_only"
    network: bool = False
    description: str = Field(default="", max_length=300)
    enabled: bool = True


class LlmBridgeEnvironmentOut(BaseModel):
    id: str
    platform: str
    mode: str
    name: str
    enabled: bool
    repo_url: str | None
    branch: str | None
    sandbox: str
    network: bool
    description: str


class LlmBridgeEnvironmentList(BaseModel):
    environments: list[LlmBridgeEnvironmentOut]


class LlmBridgeSessionOut(BaseModel):
    session_id: str
    platform: str
    mode: str
    model: str | None
    title: str
    status: str
    origin: str
    owner: str | None
    created_at: float
    updated_at: float
    turns: int = 0


class LlmBridgeSessionList(BaseModel):
    sessions: list[LlmBridgeSessionOut]
    sync_failed: str = ""


class LlmBridgeTurnOut(BaseModel):
    turn_id: str
    status: str
    text: str
    result: str | None = None
    failure: dict[str, Any] | None = None
    queued_at: float
    started_at: float | None = None
    finished_at: float | None = None


class LlmBridgeTurnList(BaseModel):
    turns: list[LlmBridgeTurnOut]


class LlmBridgeGrantIn(_In):
    enabled: bool
    can_write: bool = False
    can_account: bool = False
    can_takeover: bool = False
    platforms: list[Platform] = Field(default=[], description="允许的平台；空表示都允许")
    bridge_ids: list[UUID] | None = Field(
        default=None,
        max_length=100,
        description="允许用的 MCP 管理器；不填（null）表示都允许，包括以后新增的；"
        "空列表表示一个都不允许",
    )
    environment_ids: list[Annotated[str, Field(pattern=r"^be_[0-9a-f]{20}$")]] = Field(
        default=[],
        max_length=200,
        description="允许用的开发环境（环境编号）；空列表表示一个都不能用，"
        "这个实例就不能新建开发会话",
    )


class LlmBridgeGrantOut(BaseModel):
    instance_id: UUID
    instance_name: str
    enabled: bool
    can_write: bool
    can_account: bool
    can_takeover: bool
    platforms: list[str]
    bridge_ids: list[UUID] | None = Field(
        description="允许用的 MCP 管理器；null 表示都允许，空列表表示一个都不允许"
    )
    environment_ids: list[str] = Field(description="允许用的开发环境；空列表表示一个都不能用")


class LlmBridgeGrantList(BaseModel):
    grants: list[LlmBridgeGrantOut]


class LlmBridgeIndexSessionOut(BaseModel):
    """会话索引里的一条（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 3.4）：不含提问和回答。"""

    bridge_id: UUID
    bridge_name: str
    session_id: str
    platform: str
    mode: str
    model: str | None
    title: str
    status: str = Field(description="active / archived")
    origin: str = Field(description="bridge：经管理器新建；external：官方程序里已有")
    owner_kind: str = Field(description="agent / user / external")
    owner_id: str | None = Field(description="实例的 id，或 user:<用户 id>；external 为空")
    owner: str = Field(description="建会话时的名字")
    source: dict[str, Any] | None = Field(
        description="出处：建会话时工作台记下的——哪个实例或人，智能体当时在执行哪些任务"
    )
    busy: bool
    turns_total: int
    turns_failed: int = Field(description="失败和结果未知的回合数")
    turns_interrupted: int
    tokens: int = Field(description="累计 token：官方程序报的数相加，不是账单")
    created_at: datetime
    updated_at: datetime
    last_turn_at: datetime | None


class LlmBridgeIndexList(BaseModel):
    sessions: list[LlmBridgeIndexSessionOut]
    next_offset: int | None
    sync_failed: list[str] = Field(description="这次没同步上的 MCP 管理器的名字（sync=true 时）")


class LlmBridgeIndexOwnerOut(BaseModel):
    owner_kind: str
    owner_id: str | None
    owner: str
    sessions: int


class LlmBridgeIndexOwnerList(BaseModel):
    owners: list[LlmBridgeIndexOwnerOut]


class LlmBridgeSearchHitOut(BaseModel):
    session: LlmBridgeIndexSessionOut | None = Field(
        description="索引里的这个会话；索引还没同步到时为空（只有下面的编号）"
    )
    bridge_id: UUID
    bridge_name: str
    session_id: str
    turn_id: str | None
    role: str = Field(description="user：在提问里；assistant：在回答里")
    snippet: str = Field(description="这段文字前后的一小段")
    at: float | None


class LlmBridgeSearchOut(BaseModel):
    hits: list[LlmBridgeSearchHitOut]
    failed: list[str] = Field(description="没搜到的 MCP 管理器的名字（连不上或版本太旧）")


class LlmBridgeUsageRowOut(BaseModel):
    day: str = Field(description="哪一天（UTC），YYYY-MM-DD")
    bridge_id: UUID
    bridge_name: str
    platform: str
    owner_kind: str = Field(description="agent / user / system（自动续跑）/ external")
    owner_id: str | None
    owner: str
    turns: int
    failed: int = Field(description="失败和结果未知的回合数")
    interrupted: int
    tokens: int = Field(description="官方程序报的 token 数相加，不是账单；没报的回合算 0")
    credits: float | None = Field(
        default=None, description="消耗的积分相加（CodeBuddy）；没有回合报积分为空"
    )


class LlmBridgeUsageOut(BaseModel):
    days: int
    rows: list[LlmBridgeUsageRowOut]
    failed: list[str] = Field(description="没取到的 MCP 管理器的名字")
