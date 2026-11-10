"""应用中心的管理投影（T-0210，方案 docs/v2/app-center/ 接口契约第 2、3 节）。

全部只给 owner / admin（成员仍用 GET /modules 取开关）。四类状态分开：
启用（module.enabled）、部署（module.ready / missing）、健康（health）、外部配置同步（sync）。
声明能力（工具、组件、定时任务）是清单里的声明，不是“已授权给所有实例”的数量。
不输出 Settings 的值、凭据、导入路径、内部地址或异常正文。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.modules import ModuleOut
from app.schemas.operations import ServiceStatusOut
from app.schemas.tool_spec import RiskLevel

HealthState = Literal["ok", "degraded", "down", "unknown", "not_configured", "not_applicable"]
SyncState = Literal[
    "not_required",
    "unrecorded",
    "pending",
    "running",
    "succeeded",
    "failed",
    "unknown",
    "superseded",
]
SyncReason = Literal[
    "hook_failed",
    "config_render_failed",
    "timeout",
    "interrupted",
    "stale_revision",
    "busy",
    "result_unverified",
    "not_deployed",
    "application_not_active",
]
SyncTrigger = Literal["toggle", "retry"]


class ModuleToolOut(BaseModel):
    name: str
    risk: RiskLevel = Field(
        description="工具的风险级别（read 只读）；是否可用仍看实例权限与审批策略"
    )


class ModuleCronOut(BaseModel):
    id: str = Field(description="任务名（不含导入路径）")
    kind: Literal["scheduled"] = "scheduled"
    schedule_summary: str = Field(description="运行频率的中文说明")


class ModuleRelationOut(BaseModel):
    key: str
    name: str
    enabled: bool = Field(description="在当前工作空间是否启用")


class ModuleServiceOut(BaseModel):
    id: str = Field(description="服务检查的标识（运维页的服务 id）")
    name: str


class ModuleCapabilities(BaseModel):
    tools: list[ModuleToolOut]
    widgets: list[str] = Field(description="首页小组件类型")
    crons: list[ModuleCronOut]
    event_topics: list[str]
    depends_on: list[ModuleRelationOut]
    dependents: list[ModuleRelationOut]
    services: list[ModuleServiceOut] = Field(description="声明的外部服务（部署级）")
    has_sync_hook: bool = Field(description="启用 / 停用后是否要同步外部配置")


class ModuleHealthOut(BaseModel):
    state: HealthState = Field(
        description="not_applicable 没有声明外部服务；not_configured 没部署；unknown 还没检测过；"
        "ok / degraded / down 来自最近一次检测（看 checked_at 与 stale）"
    )
    checked_at: datetime | None = Field(description="最近一次检测的时间；从未检测过为 null")
    stale: bool = Field(description="检测结果已超过缓存时间，不是新鲜的探测")
    checks: list[ServiceStatusOut]


class ModuleSyncOut(BaseModel):
    state: SyncState = Field(
        description="not_required 没有同步钩子；unrecorded 当前目标没有同步记录；"
        "succeeded 钩子明确报告已应用；failed / unknown 需要处理（开关本身已保存）"
    )
    state_version: int = Field(description="当前目标状态的版本")
    attempt_id: UUID | None
    target_enabled: bool | None
    trigger: SyncTrigger | None
    reason: SyncReason | None = Field(description="固定类别，不含异常正文")
    started_at: datetime | None
    finished_at: datetime | None


class ModuleActions(BaseModel):
    can_toggle: bool = Field(description="只有 owner")
    can_retry_sync: bool = Field(description="owner，且有同步钩子、当前同步失败 / 未知 / 未记录")
    can_check_health: bool = Field(description="owner / admin，模块启用且声明了外部服务")


class ModuleManagementOut(BaseModel):
    module: ModuleOut
    host_api: int
    capabilities: ModuleCapabilities
    health: ModuleHealthOut
    sync: ModuleSyncOut
    attention: bool = Field(
        description="需要处理：同步失败 / 未知，或已启用但未部署、外部服务 down / degraded"
    )
    actions: ModuleActions


class ModuleManagementSummary(BaseModel):
    registered: int = Field(description="已注册的模块数（不含核心功能与自定义页面）")
    enabled: int
    attention: int


class ModuleManagementList(BaseModel):
    items: list[ModuleManagementOut]
    summary: ModuleManagementSummary = Field(description="基于全部已注册模块，与搜索筛选无关")
    generated_at: datetime


class ModuleReconcileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state_version: int = Field(ge=0, description="读到的 state_version")
