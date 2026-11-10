"""中转站管理：模型连接、模型目录、检测、授权、迁移分类、智能体主模型绑定（T-0200 ~ T-0205）。

约定见 docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md 与
docs/decisions/2026-11-09-model-connections.md：

- 管理接口（/api/v1/model-connections、/api/v1/agents/instances/{id}/model-binding）只给 owner；
- API Key 只写不读：任何接口都不返回明文、密文、credential_ref 或内部 secret_id；
- 实例接口（/agent-api/v1/model-config、/model-config/applied、/model-credentials/resolve）
  只给实例凭证，且只能读写自己的绑定。
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
)

ConnectionType = Literal["official", "relay"]
OfficialProvider = Literal["openai", "anthropic", "google"]
Protocol = Literal["openai_compatible", "anthropic_native", "gemini_native"]
ModelScope = Literal["selected", "all_available"]
ConfigurationState = Literal["draft", "ready", "needs_review"]
MigrationState = Literal[
    "native", "needs_configuration", "needs_classification", "managed", "non_model"
]
SyncState = Literal["complete", "partial", "unsupported", "failed"]
ModelSource = Literal["manual", "discovered"]
ModelAvailability = Literal["listed", "verified"]
CheckMode = Literal["metadata", "inference"]
CheckStatus = Literal["passed", "failed", "rate_limited", "unsupported", "unknown"]
CheckReason = Literal[
    "unauthorized",
    "forbidden",
    "timeout",
    "unreachable",
    "tls_error",
    "invalid_response",
    "model_not_available",
    "protocol_unsupported",
    "models_endpoint_unsupported",
    "destination_not_allowed",
    "credential_unreadable",
    "stale_revision",
]
ApplyState = Literal[
    "legacy_active", "pending", "applying", "applied", "failed", "unknown", "manual_required"
]
JobState = Literal["queued", "running", "succeeded", "failed", "unknown", "manual_required"]
ClassifyPurpose = Literal["model", "non_model"]

MAX_MODELS_PER_CONNECTION = 1000
API_KEY_MAX = 8192
# metadata 检测结果超过这个时间页面标记“结果可能已过期”（不自动变成失败）
CHECK_FRESH_SECONDS = 15 * 60

Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求带上读取时的版本")]
ConnectionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    Field(description="工作空间内唯一；纯文本"),
]
ConnectionDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=500), Field(description="纯文本")
]
BaseUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    Field(
        description="协议基地址（不含 /chat/completions 等单次请求路径）。必须是 HTTPS；"
        "不能带用户名密码、查询参数或片段。尾斜杠会被去掉"
    ),
]
ModelId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f\x7f]+$"),
    Field(description="服务商的模型 ID，原样保存大小写；不能含空白与控制字符"),
]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)]
ApiKey = Annotated[SecretStr, Field(min_length=1, max_length=API_KEY_MAX)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 输出


class ConnectionCheckOut(BaseModel):
    id: UUID
    connection_id: UUID
    config_revision: int = Field(description="检测时连接的配置修订号；与当前不同表示结果已过时")
    mode: CheckMode = Field(
        description="metadata 只证明认证与元数据接口可用；inference 才是对某个模型的真实推理请求"
    )
    model_id: str | None
    status: CheckStatus
    reason: CheckReason | None
    checked_at: datetime
    latency_ms: int | None
    retry_at: datetime | None = Field(
        description="服务商限流时按 Retry-After 算出的可重试时间；没有合法值时为 null"
    )


class ModelConnectionOut(BaseModel):
    """连接的元数据。绝不输出 Key 明文、密文、credential_ref 或内部 secret_id。"""

    id: UUID
    workspace_id: UUID
    name: str
    description: str
    connection_type: ConnectionType | None = Field(description="迁移草稿可能为 null")
    official_provider: OfficialProvider | None
    protocol: Protocol | None
    base_url: str | None
    credential_configured: bool = Field(description="已保存可解密的 Key；不表示服务商接受这个 Key")
    credential_version: int = Field(description="Key 的版本，更换时加 1")
    model_scope: ModelScope
    configuration_state: ConfigurationState = Field(
        description="ready：类型、已开放的协议、URL、可解密 Key、非空明确模型集合都齐全"
        "（不表示服务商已接受推理请求）"
    )
    enabled: bool = Field(description="管理员启停，与检测结果无关")
    migration_state: MigrationState
    legacy_source_id: UUID | None = Field(
        description="从旧密钥库导入时的原密钥 ID，只用于追溯，不能拿来取 Key"
    )
    model_count: int = Field(ge=0)
    grant_count: int = Field(ge=0)
    binding_count: int = Field(ge=0, description="以它为目标或已应用连接的实例数")
    config_revision: int
    version: int
    sync_state: SyncState | None
    models_synced_at: datetime | None
    last_check: ConnectionCheckOut | None = Field(
        description="最近一次与当前配置修订号一致的检测；配置变化后为 null"
    )
    created_at: datetime
    updated_at: datetime


class ModelConnectionPage(BaseModel):
    items: list[ModelConnectionOut]
    next_cursor: str | None = None
    total: int = Field(ge=0, description="符合筛选条件的全部连接数（不是当前页条数）")


class ModelConnectionSummary(BaseModel):
    """整个工作空间的统计，不受分页或筛选影响；non_model 不计入。"""

    total: int = Field(ge=0)
    check_passed: int = Field(
        ge=0,
        description="ready 且启用、15 分钟内对当前配置修订号检测通过的连接数"
        "（metadata 或 inference）",
    )
    check_passed_inference: int = Field(ge=0, description="其中最近一次是推理检测通过的")
    needs_attention: int = Field(
        ge=0, description="待完善 / 待归类 / 需要复核 / 最近检测失败或限流的连接数"
    )
    unique_granted_instances: int = Field(ge=0, description="至少有一个连接授权的实例数（去重）")
    non_model: int = Field(ge=0, description="归类为非模型凭据的兼容项数")


class OfficialPreset(BaseModel):
    provider: OfficialProvider
    label: str
    protocol: Protocol
    base_url: str = Field(description="经核验的官方协议基地址")
    source: str = Field(description="核验来源（官方文档地址）")


class ModelConnectionCapabilities(BaseModel):
    enabled: bool = Field(description="中转站管理是否启用（部署开关）")
    supported_protocols: list[Protocol] = Field(
        description="已核验并开放的协议；未核验的不出现，前端不显示"
    )
    official_presets: list[OfficialPreset]
    limits: dict[str, int] = Field(description="例如 max_models、check_per_minute")


class ConnectionModelOut(BaseModel):
    id: UUID
    model_id: str
    display_name: str
    source: ModelSource
    availability: ModelAvailability = Field(
        description="listed：服务商列表里有或手工填写；verified：真实推理检测通过过"
    )
    last_seen_at: datetime | None
    version: int


class ConnectionModelPage(BaseModel):
    items: list[ConnectionModelOut]
    next_cursor: str | None = None
    total: int = Field(ge=0)
    synced_at: datetime | None
    sync_state: SyncState | None
    discovered: list[str] = Field(
        description="最近一次完整同步到的模型 ID（最多 1000 个），供“指定模型”勾选；没有同步过为空"
    )


class ModelSyncResult(BaseModel):
    sync_state: SyncState
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    total: int = Field(ge=0)
    kept_previous: bool = Field(
        description="同步不完整或失败时保留上一次完整快照（不会默默移除已绑定的模型）"
    )
    reason: CheckReason | None
    retry_at: datetime | None
    discovered: list[str] = Field(
        description="这次完整同步到的模型 ID；不完整或失败时为上一次的快照"
    )
    kept_in_use: list[str] = Field(
        description="服务商这次没有再列出、但正被智能体使用而保留的模型 ID"
        "（全部可用模型范围下不会默默移除；请先给这些智能体换模型）",
    )
    connection: ModelConnectionOut


class GrantInstanceOut(BaseModel):
    instance_id: UUID
    instance_name: str
    instance_status: str
    created_at: datetime
    bound: bool = Field(description="这个实例以本连接为目标或已应用连接")


class ConnectionGrantPage(BaseModel):
    items: list[GrantInstanceOut]
    next_cursor: str | None = None
    total: int = Field(ge=0)


class ConnectionActivityOut(BaseModel):
    """创建 / 配置 / 更换 Key / 授权 / 取用 / 应用事件的脱敏投影。"""

    id: UUID
    kind: Literal["audit", "access"]
    action: str = Field(description="审计事件名，或 credential.resolved（取用记录）")
    at: datetime
    actor_id: UUID | None
    instance_id: UUID | None
    instance_name: str | None
    result: str | None = Field(description="取用记录的结果：ok / not_found / rate_limited")
    value_version: int | None


class ConnectionActivityPage(BaseModel):
    items: list[ConnectionActivityOut]
    next_cursor: str | None = None


class ImpactOut(BaseModel):
    """停用 / 删除 / 移除模型 / 撤销授权会影响到的实例。"""

    instances: list[GrantInstanceOut]
    bound_instances: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)


class MigrationEntryOut(BaseModel):
    """旧密钥库的导入项（连接映射）。不含 Key。"""

    entry_id: UUID = Field(description="迁移映射 ID（即连接 ID）")
    connection: ModelConnectionOut
    legacy_name: str
    legacy_kind: Literal["model_api_key", "api_token", "generic"]
    legacy_grant_refs: list[str] = Field(description="原有授权的 ref_id（OpenClaw 配置里的引用名）")


class MigrationEntryPage(BaseModel):
    items: list[MigrationEntryOut]
    next_cursor: str | None = None
    total: int = Field(ge=0)
    counts: dict[str, int] = Field(
        description="needs_configuration / needs_classification / managed / non_model / "
        "needs_review / unmapped（还没导入的旧密钥数）"
    )


# ---------------------------------------------------------------- 输入


class ModelInput(_Strict):
    model_id: ModelId
    display_name: DisplayName | None = None


class ModelConnectionCreate(_Strict):
    name: ConnectionName
    description: ConnectionDescription = ""
    connection_type: ConnectionType | None = None
    official_provider: OfficialProvider | None = None
    protocol: Protocol | None = None
    base_url: BaseUrl | None = None
    api_key: ApiKey = Field(description="只写；保存后任何接口都不再返回")
    model_scope: ModelScope = "selected"
    models: Annotated[list[ModelInput], Field(max_length=MAX_MODELS_PER_CONNECTION)] = []
    save_as_draft: bool = Field(
        default=False,
        description="true：允许 URL / 协议 / 模型暂缺，保存为草稿且默认停用；"
        "false：必须配置完整（ready），默认启用",
    )


class ModelConnectionUpdate(_Strict):
    """省略的字段不变；null 只能用于草稿的可空字段。不含 api_key（用 rotate-key）。"""

    version: Version
    name: ConnectionName | None = None
    description: ConnectionDescription | None = None
    connection_type: ConnectionType | None = None
    official_provider: OfficialProvider | None = None
    protocol: Protocol | None = None
    base_url: BaseUrl | None = None
    enabled: bool | None = None
    confirm_impact: bool = Field(
        default=False,
        description="停用会影响已授权 / 已绑定的实例时，先返回 409 与影响摘要；"
        "确认后带 true 再提交",
    )


class ModelConnectionKeyRotate(_Strict):
    version: Version
    api_key: ApiKey


class ModelsReplace(_Strict):
    version: Version
    model_scope: ModelScope
    models: Annotated[list[ModelInput], Field(max_length=MAX_MODELS_PER_CONNECTION)]


class ModelSyncRequest(_Strict):
    version: Version


class ConnectionCheckRequest(_Strict):
    version: Version
    mode: CheckMode
    model_id: ModelId | None = Field(
        default=None, description="inference 必填；会向服务商发一个很短的请求，可能产生少量费用"
    )


class GrantsReplace(_Strict):
    version: Version
    instance_ids: Annotated[list[UUID], Field(max_length=500)]


class MigrationRun(_Strict):
    """在页面上执行旧密钥导入（与命令 app.commands.migrate_model_connections 相同的逻辑）。"""

    mode: Literal["dry_run", "apply", "report"] = Field(
        description="dry_run 只读预览；apply 按批导入（可以重复执行）；report 只读对账"
    )


class MigrationBatchOut(BaseModel):
    batch: int
    scanned: int
    mapped: int
    already_mapped: int
    failed: int
    status: str = Field(description="committed / rolled_back / read_only")
    error: str | None = Field(description="错误类别（不含异常原文）")


class MigrationRunOut(BaseModel):
    """只有计数与错误类别，不含 Key、密文或名称。"""

    mode: Literal["dry_run", "apply", "report"]
    scanned: int
    mapped: int = Field(description="本次新建（dry_run 是将新建）的映射数")
    already_mapped: int
    needs_configuration: int
    needs_classification: int
    unreadable: int
    failed: int
    batches: list[MigrationBatchOut]
    reconciliation: dict[str, Any] | None = Field(
        default=None, description="report 才有：映射、授权、绑定、取用记录的对账计数"
    )


class MigrationClassify(_Strict):
    version: Version
    purpose: ClassifyPurpose


# ---------------------------------------------------------------- 绑定（owner）


class AgentModelBindingOut(BaseModel):
    instance_id: UUID
    connection_id: UUID | None = Field(description="目标连接；解除绑定后为 null")
    model_id: str | None
    desired_revision: int = Field(description="目标每变一次加 1")
    applied_connection_id: UUID | None = Field(description="实例确认已应用的连接")
    applied_model_id: str | None
    applied_revision: int | None
    applied_at: datetime | None
    revoked: bool = Field(description="已解除绑定：后续模型 Key 取用一律拒绝")
    apply_state: ApplyState
    last_error_code: str | None
    job_id: UUID | None
    version: int
    connection_name: str | None = Field(description="目标连接的名称（显示用）")
    applied_connection_name: str | None


class LegacyModelInfo(BaseModel):
    """没有原生绑定、仍按旧配置运行的实例：只给旧授权的引用名，不猜连接与模型。"""

    legacy_refs: list[str] = Field(description="这个实例在旧密钥库里的授权引用名")


class ModelBindingView(BaseModel):
    binding: AgentModelBindingOut | None
    apply_state: ApplyState | None = Field(
        description="有绑定时同 binding.apply_state；没有绑定、仍按旧配置运行时为 legacy_active"
    )
    legacy: LegacyModelInfo | None = Field(
        description="apply_state 为 legacy_active 时的旧配置信息；有原生绑定时为 null"
    )


class ModelBindingPut(_Strict):
    version: Version | None = Field(
        default=None, description="首次绑定传 null；已有绑定时必须带当前版本"
    )
    connection_id: UUID
    model_id: ModelId


class ModelBindingApply(_Strict):
    version: Version


class ModelApplyJobOut(BaseModel):
    id: UUID
    instance_id: UUID
    state: JobState
    desired_revision: int
    attempt: int
    last_error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ModelAssignmentRow(BaseModel):
    """管理页“OpenClaw 连接分配”表的一行。"""

    instance_id: UUID
    instance_name: str
    instance_status: str
    binding: AgentModelBindingOut | None
    legacy: LegacyModelInfo | None


class ModelAssignmentPage(BaseModel):
    items: list[ModelAssignmentRow]
    next_cursor: str | None = None
    total: int = Field(ge=0)


class PickerConnectionOut(BaseModel):
    """选择器里的连接：只列启用且配置完整的。"""

    id: UUID
    name: str
    connection_type: ConnectionType
    protocol: Protocol
    base_url: str
    model_count: int
    granted: bool = Field(description="已授权给这个实例（新建实例时恒为 false）")


class PickerConnectionPage(BaseModel):
    items: list[PickerConnectionOut]
    next_cursor: str | None = None
    total: int = Field(ge=0)


class NewInstanceModelChoice(_Strict):
    """新部署时选择的主连接与模型：选择即授权给即将创建的实例（同一事务）。"""

    connection_id: UUID
    model_id: ModelId


# ---------------------------------------------------------------- 实例接口（/agent-api/v1）


class AgentModelConfigOut(BaseModel):
    """实例自己的非敏感模型配置（GET /agent-api/v1/model-config）。不含 Key。

    state=active：按 provider_key / base_url / model_id 等字段写入受管 provider 与主模型；
    state=revoked：已解除绑定，移除受管 provider（连接相关字段为 null）。
    apply_key 变了就要重新应用（目标修订号 + 应用任务的尝试次数）。"""

    state: Literal["active", "revoked"]
    binding_version: int
    desired_revision: int
    apply_key: str = Field(description="<desired_revision>.<attempt>；与上次应用的不同就重新应用")
    job_state: JobState | None = Field(description="当前目标的应用任务状态")
    managed_prefix: str = Field(description="受管 provider 键的前缀（只改这些 provider）")
    connection_id: UUID | None
    provider_key: str | None = Field(
        description="OpenClaw 配置里 models.providers 的键（由连接 ID 生成）"
    )
    api: str | None = Field(description="OpenClaw 的 provider api 值（例如 openai-completions）")
    protocol: Protocol | None
    base_url: str | None
    model_id: str | None
    model_name: str | None
    primary: str | None = Field(
        description="agents.defaults.model.primary 的值：<provider_key>/<model_id>"
    )
    secret_ref_id: str | None = Field(description="apiKey 的 exec SecretRef id（由连接 ID 生成）")
    config_revision: int | None
    fingerprint: str = Field(description="受管配置片段的摘要")


class ModelConfigApplied(_Strict):
    binding_version: Annotated[int, Field(ge=1)]
    desired_revision: Annotated[int, Field(ge=1)]
    result: Literal["applied", "failed", "manual_required"]
    reason: Annotated[str, StringConstraints(max_length=64, pattern=r"^[a-z0-9_]+$")] | None = (
        Field(default=None, description="失败类别（固定的小写代码），不含远端原始输出")
    )
    observed_at: AwareDatetime | None = None


class ModelConfigAppliedOut(BaseModel):
    """回报的结果：只回状态，不回连接或内部字段。"""

    apply_state: ApplyState
    desired_revision: int
    applied_revision: int | None


class ModelCredentialResolveRequest(_Strict):
    ids: Annotated[list[Annotated[str, StringConstraints(max_length=64)]], Field(max_length=8)]


class ModelCredentialError(BaseModel):
    code: Literal["NOT_FOUND"]


class ModelCredentialResolveResponse(BaseModel):
    values: dict[str, str]
    errors: dict[str, ModelCredentialError]
