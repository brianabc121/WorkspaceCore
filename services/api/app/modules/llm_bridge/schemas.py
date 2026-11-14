"""大模型会话桥页面接口的请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
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
    settings: LlmBridgeSettings | None = None


class LlmBridgeLoginIn(_In):
    method: Literal["device_code", "api_key", "oauth_token", "existing"]
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


class LlmBridgeGrantList(BaseModel):
    grants: list[LlmBridgeGrantOut]
