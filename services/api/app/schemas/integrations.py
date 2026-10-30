"""第三方集成（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 6–7 项、第 7 节）。本 Sprint 只有 Vikunja。

约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 4、10 节；Vikunja 的核验结果见
docs/decisions/2026-10-30-vikunja-2.7.1-verification.md。API Token 用 SecretStr 接收，只写不读，
任何响应、日志、审计里都不出现。
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
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

# 核验过的 Vikunja 版本与 API 版本（核验 ADR 第 2、3 节）
VIKUNJA_TESTED_VERSION = "2.7.1"
VIKUNJA_API_VERSION = "v2"

Provider = Literal["vikunja"]
IntegrationStatus = Literal["pending", "active", "unauthorized", "unavailable", "disabled"]
SyncStatus = Literal["ok", "stale", "unavailable", "unauthorized", "not_configured"]

IntegrationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求必须带上读取时的版本")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _base_url(value: str) -> str:
    """http(s)、有主机、不带用户名密码 / 查询串 / 片段；去掉末尾的 /。

    私有地址的 SSRF 防护（INTEGRATION_ALLOWED_HOSTS 与连接时的解析检查）
    在服务层做（契约第 4.3 节）。
    """
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("只允许 http:// 或 https:// 地址")
    if parts.username or parts.password:
        raise ValueError("地址里不能包含用户名或密码")
    if parts.query or parts.fragment:
        raise ValueError("地址里不能包含查询串或片段")
    return value.rstrip("/")


BaseUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    AfterValidator(_base_url),
]


def _api_token(value: SecretStr) -> SecretStr:
    if not value.get_secret_value().startswith("tk_"):
        raise ValueError("请填写 Vikunja 的 API Token（以 tk_ 开头）")
    return value


ApiToken = Annotated[
    SecretStr,
    Field(min_length=4, max_length=512, description="Vikunja API Token（tk_ 开头）；只写不读"),
    AfterValidator(_api_token),
]


class SyncState(BaseModel):
    """数据的新鲜度（契约第 10 节）；集成、项目列表、任务列表都返回。"""

    status: SyncStatus
    as_of: datetime | None = Field(description="最近一次成功同步的时间；从未成功时为 null")
    reason: str | None = Field(
        default=None, description="机器可读的原因，例如 not_synced；不含敏感信息"
    )


class WebhookRegistration(BaseModel):
    mode: Literal["auto", "unavailable"] = Field(
        description="auto：服务端为每个项目注册 Webhook；"
        "unavailable：Token 没有注册权限，只靠周期对账"
    )
    registered_projects: int = Field(ge=0)
    reason: str | None = None


class IntegrationOut(BaseModel):
    id: UUID
    provider: Provider
    name: str
    base_url: str = Field(description="工作台访问该服务的地址")
    public_url: str = Field(description="浏览器打开原生界面的地址")
    status: IntegrationStatus
    token_set: bool = Field(description="是否已保存 Token；Token 本身从不返回")
    token_expires_at: datetime | None
    tested_version: str = Field(description="工作台核验过的版本")
    api_version: str
    instance_version: str | None = Field(description="最近一次测试连接时实例报告的版本")
    last_tested_at: datetime | None
    sync: SyncState
    webhooks: WebhookRegistration
    version: int
    created_at: datetime
    updated_at: datetime


class IntegrationCreate(_Strict):
    provider: Provider
    name: IntegrationName
    base_url: BaseUrl
    public_url: BaseUrl | None = Field(default=None, description="不填时与 base_url 相同")
    api_token: ApiToken
    token_expires_at: AwareDatetime | None = Field(
        default=None, description="Token 的过期时间（在 Vikunja 创建 Token 时设置），用于到期提醒"
    )


class IntegrationUpdate(_Strict):
    """至少改一项；改 base_url 或 api_token 后状态回到 pending，需要重新测试连接。"""

    version: Version
    name: IntegrationName | None = None
    base_url: BaseUrl | None = None
    public_url: BaseUrl | None = Field(default=None, description="显式传 null 表示恢复为 base_url")
    api_token: ApiToken | None = None
    token_expires_at: AwareDatetime | None = None
    enabled: bool | None = Field(default=None, description="false 停用（不同步、拒绝写入）")

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        nullable = {"public_url", "token_expires_at"}
        if any(getattr(self, name) is None for name in changed - nullable):
            raise ValueError("只有 public_url 与 token_expires_at 可以设为 null")
        return self


class IntegrationCheck(BaseModel):
    name: Literal["reachable", "version", "token", "tasks_read", "webhooks"]
    ok: bool
    code: str | None = Field(default=None, description="未通过时的错误码；不含外部响应原文")


class IntegrationTestResult(BaseModel):
    ok: bool = Field(description="必需的检查全部通过（webhooks 是可选项，不影响 ok）")
    status: IntegrationStatus = Field(description="测试之后的集成状态")
    instance_version: str | None
    api_version: str | None
    tested_version: str
    compatible: bool = Field(description="实例版本与 tested_version 是否一致；不一致不阻止使用")
    checks: list[IntegrationCheck]
    warnings: list[str]


class SyncRequestOut(BaseModel):
    integration_id: UUID
    mode: Literal["full"]
    queued_at: datetime
