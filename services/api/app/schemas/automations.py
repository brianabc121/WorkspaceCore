"""自动化：n8n 白名单工作流与受控触发（docs/DEVELOPMENT_SPEC.md 第 7.5 节，Sprint 5）。

约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 6 节；
n8n 的接口与安全边界见 docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md 第 5 节。
n8n 的 API Key 与 Webhook 共享密钥只写不读，从不下发到浏览器；执行记录只保存状态与错误摘要。
"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.schemas.integrations import BaseUrl, IntegrationStatus, SyncState

# 核验过的 n8n 版本（核验 ADR 第 2 节）
N8N_TESTED_VERSION = "2.45.7"
MAX_PARAMS = 20

RunStatus = Literal["queued", "running", "success", "error", "canceled", "unknown"]
ParamType = Literal["string", "number", "boolean"]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求必须带上读取时的版本")]
AutomationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
_WEBHOOK_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _webhook_path(value: str) -> str:
    if not _WEBHOOK_PATH.fullmatch(value):
        raise ValueError("Webhook 路径只能包含字母、数字、- 与 _，不要填写完整网址")
    return value


WebhookPath = Annotated[
    str,
    AfterValidator(_webhook_path),
    Field(description="n8n Webhook 节点的 path；工作台调用 POST <n8n>/webhook/<path>"),
]
N8nApiKey = Annotated[
    SecretStr,
    Field(
        min_length=20,
        max_length=4096,
        description="n8n 的 API Key，只勾选 workflow:list、workflow:read、execution:list、"
        "execution:read；只写不读",
    ),
]
TriggerSecret = Annotated[
    SecretStr,
    Field(
        min_length=16,
        max_length=512,
        description="Webhook 节点 Header Auth 凭据里的值（请求头 X-Workspace-Trigger）；只写不读",
    ),
]


# ---------------------------------------------------------------- n8n 连接（每个工作空间一个）


class AutomationConnectionOut(BaseModel):
    configured: bool = Field(description="false 时其余字段为默认值，页面引导管理员配置")
    base_url: str | None = Field(default=None, description="工作台访问 n8n 的地址（只给管理员看）")
    public_url: str | None = Field(default=None, description="浏览器打开 n8n 编辑器的地址")
    status: IntegrationStatus | None = None
    api_key_set: bool = False
    trigger_secret_set: bool = False
    tested_version: str = N8N_TESTED_VERSION
    instance_version: str | None = None
    last_tested_at: datetime | None = None
    sync: SyncState | None = Field(default=None, description="读取执行记录的同步状态")
    version: int | None = None


class AutomationConnectionUpsert(_Strict):
    """新建或修改 n8n 连接（admin）。新建时 base_url、api_key、trigger_secret 都必须给。"""

    version: Version | None = Field(default=None, description="修改时必填；新建时不填")
    base_url: BaseUrl | None = None
    public_url: BaseUrl | None = Field(default=None, description="不填时与 base_url 相同")
    api_key: N8nApiKey | None = None
    trigger_secret: TriggerSecret | None = None
    enabled: bool | None = Field(default=None, description="false 停用：不读取执行记录、拒绝触发")

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        if not (self.model_fields_set - {"version"}):
            raise ValueError("至少要修改一项")
        return self


# ---------------------------------------------------------------- 白名单工作流


class ParamSpec(_Strict):
    """触发参数的声明；工作台按它校验后才发给 n8n，未声明的参数一律拒绝。"""

    name: Annotated[str, StringConstraints(pattern=_PARAM_NAME.pattern)]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    type: ParamType
    required: bool = False
    max_length: int | None = Field(default=None, ge=1, le=2000, description="只用于 string")
    choices: list[str] | None = Field(
        default=None, min_length=1, max_length=50, description="只用于 string：限定可选值"
    )
    sensitive: bool = Field(
        default=False, description="true：参数值不写进审计与执行记录（例如邮箱、单号）"
    )


class AutomationOut(BaseModel):
    id: UUID
    name: str
    description: str
    workflow_id: str = Field(description="n8n 的工作流 ID")
    webhook_path: str
    params: list[ParamSpec]
    enabled: bool
    workflow_active: bool | None = Field(
        description="最近一次从 n8n 读到的工作流是否已激活；从未读到时为 null"
    )
    last_run: "AutomationRunOut | None" = None
    version: int
    created_at: datetime
    updated_at: datetime


class AutomationCreate(_Strict):
    name: AutomationName
    description: Annotated[str, StringConstraints(max_length=500)] = ""
    workflow_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{1,64}$")]
    webhook_path: WebhookPath
    params: list[ParamSpec] = Field(default_factory=list, max_length=MAX_PARAMS)
    enabled: bool = True


class AutomationUpdate(_Strict):
    version: Version
    name: AutomationName | None = None
    description: Annotated[str, StringConstraints(max_length=500)] | None = None
    webhook_path: WebhookPath | None = None
    params: list[ParamSpec] | None = Field(default=None, max_length=MAX_PARAMS)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        if not (self.model_fields_set - {"version"}):
            raise ValueError("至少要修改一项")
        return self


class AutomationPage(BaseModel):
    items: list[AutomationOut]
    next_cursor: str | None = None
    total_estimate: int | None = None
    connection: SyncState = Field(
        description="n8n 连接的状态：未配置为 not_configured，授权失效为 unauthorized"
    )


# ---------------------------------------------------------------- 触发与执行记录


class TriggerRequest(_Strict):
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="按 AutomationOut.params 的声明校验；多余或类型不对返回 422",
    )


class AutomationRunOut(BaseModel):
    id: UUID
    automation_id: UUID
    status: RunStatus = Field(
        description="queued 已受理；running 执行中；success / error / canceled 已结束；"
        "unknown 触发请求的结果未知（超时等），由对账确认，不会自动重发"
    )
    external_execution_id: str | None = Field(
        description="n8n 的执行 ID；还没对应上时为 null（核验 ADR 第 5.2 节）"
    )
    triggered_by: UUID | None
    error_summary: str | None = Field(
        description="失败原因的摘要（纯文本，最多 500 字）；不含第三方 payload 原文"
    )
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AutomationRunPage(BaseModel):
    items: list[AutomationRunOut]
    next_cursor: str | None = None
    total_estimate: int | None = None


AutomationOut.model_rebuild()
