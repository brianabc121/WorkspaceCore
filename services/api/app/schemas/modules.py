"""插拔式模块的启用状态（T-0159；docs/modules/README.md）。

state_version（应用中心 T-0210）是目标状态的版本，实际启停一次加 1；
version 是清单里的发布版本字符串，两者无关。
"""

from pydantic import BaseModel, ConfigDict, Field


class ModuleOut(BaseModel):
    key: str = Field(description="模块标识；接口前缀、表名前缀、工具名前缀都用它")
    name: str
    description: str
    version: str = Field(description="模块的发布版本（随工作台发布，不是在线更新的版本）")
    enabled: bool = Field(description="在当前工作空间是否启用")
    state_version: int = Field(
        ge=0,
        description="启用状态的版本：实际启用 / 停用一次加 1；没有记录时为 0（与 version 无关）",
    )
    default_enabled: bool = Field(description="没有启用 / 停用记录时的默认值")
    ready: bool = Field(description="部署前提是否满足；不满足时模块页面显示“未部署”")
    missing: list[str] = Field(description="没满足的部署前提（.env 里的变量名）")
    depends_on: list[str] = Field(description="依赖的模块：它们都启用时才能启用本模块")
    dependents: list[str] = Field(description="依赖本模块的模块：它们启用时不能停用本模块")
    tools: list[str] = Field(description="模块给智能体的工具")
    widgets: list[str] = Field(description="模块提供的首页小组件类型")
    can_manage: bool = Field(description="当前用户能否启用 / 停用（只有 owner）")


class ModuleList(BaseModel):
    items: list[ModuleOut]


class ModuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    expected_state_version: int | None = Field(
        default=None,
        ge=0,
        description="读到的 state_version；不是当前值时 409 VERSION_CONFLICT。"
        "新页面都带上；旧客户端可以省略（仍走同一把操作锁与依赖检查）",
    )
