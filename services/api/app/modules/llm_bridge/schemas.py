"""大模型会话桥页面接口的请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["chatgpt", "codex", "claude", "workbuddy"]
Slot = Literal["openai", "claude", "workbuddy"]
Sandbox = Literal["read_only", "workspace_write", "exec"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BridgeIn(_In):
    name: str = Field(min_length=1, max_length=40, description="显示名，也是工具里 bridge 参数的值")
    base_url: str = Field(
        min_length=8, max_length=300, description="会话桥的地址，例如 http://10.0.0.5:8787"
    )
    token: str = Field(min_length=32, max_length=200, description="会话桥的管理令牌")


class BridgePatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    base_url: str | None = Field(default=None, min_length=8, max_length=300)
    token: str | None = Field(default=None, min_length=32, max_length=200)
    enabled: bool | None = None


class BridgeOut(BaseModel):
    id: UUID
    name: str
    base_url: str
    enabled: bool
    has_token: bool
    created_at: datetime
    updated_at: datetime


class BridgeList(BaseModel):
    bridges: list[BridgeOut]


class AccountOut(BaseModel):
    slot: str
    label: str
    state: str = Field(description="logged_in / logged_out / mismatch / unavailable")
    identity: str | None = Field(description="账号标识（打码）")
    method: str | None
    detail: str
    first_login_at: float | None
    login_methods: list[str]
    running: int


class ChannelOut(BaseModel):
    platform: str
    mode: str
    slot: str
    available: bool
    note: str


class BridgeSettings(BaseModel):
    concurrency: int = Field(ge=1, le=16, description="每个账号同时进行的回合数")
    turn_timeout_seconds: int = Field(ge=30, le=21600)
    send_per_hour: int = Field(ge=1, le=10000, description="每个实例每小时的发送次数")
    max_text_chars: int = Field(ge=1, le=1_000_000)
    claude_subscription_ack: bool = Field(description="已确认了解 Claude 订阅登录的合规风险")


class BridgeSettingsPatch(_In):
    concurrency: int | None = Field(default=None, ge=1, le=16)
    turn_timeout_seconds: int | None = Field(default=None, ge=30, le=21600)
    send_per_hour: int | None = Field(default=None, ge=1, le=10000)
    max_text_chars: int | None = Field(default=None, ge=1, le=1_000_000)
    claude_subscription_ack: bool | None = None


class BridgeStatusOut(BaseModel):
    reachable: bool
    error: str = Field(default="", description="连不上时的原因")
    version: str = ""
    accounts: list[AccountOut] = []
    channels: list[ChannelOut] = []
    queued: int = 0
    running: int = 0
    settings: BridgeSettings | None = None


class LoginIn(_In):
    method: Literal["device_code", "api_key", "oauth_token", "existing"]
    api_key: str | None = Field(default=None, max_length=500)
    oauth_token: str | None = Field(default=None, max_length=2000)


class LoginOut(BaseModel):
    state: str = Field(description="pending / done / failed / none")
    verification_url: str | None = None
    user_code: str | None = None
    detail: str = ""


class AccountStateOut(BaseModel):
    state: str
    identity: str | None
    detail: str


class ResetOut(BaseModel):
    archived_sessions: int


class EnvironmentIn(_In):
    platform: Platform
    mode: Literal["dev", "cloud"] = "dev"
    name: str = Field(min_length=1, max_length=60)
    repo_url: str | None = Field(default=None, max_length=500, description="只支持 https://")
    branch: str | None = Field(default=None, max_length=200)
    sandbox: Sandbox = "read_only"
    network: bool = False
    description: str = Field(default="", max_length=300)
    enabled: bool = True


class EnvironmentOut(BaseModel):
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


class EnvironmentList(BaseModel):
    environments: list[EnvironmentOut]


class SessionOut(BaseModel):
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


class SessionList(BaseModel):
    sessions: list[SessionOut]
    sync_failed: str = ""


class TurnOut(BaseModel):
    turn_id: str
    status: str
    text: str
    result: str | None = None
    failure: dict[str, Any] | None = None
    queued_at: float
    started_at: float | None = None
    finished_at: float | None = None


class TurnList(BaseModel):
    turns: list[TurnOut]


class GrantIn(_In):
    enabled: bool
    can_write: bool = False
    can_account: bool = False
    can_takeover: bool = False
    platforms: list[Platform] = Field(default=[], description="允许的平台；空表示都允许")


class GrantOut(BaseModel):
    instance_id: UUID
    instance_name: str
    enabled: bool
    can_write: bool
    can_account: bool
    can_takeover: bool
    platforms: list[str]


class GrantList(BaseModel):
    grants: list[GrantOut]
