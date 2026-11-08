"""插拔式模块的启用状态（T-0159；docs/modules/README.md）。"""

from pydantic import BaseModel, ConfigDict, Field


class ModuleOut(BaseModel):
    key: str = Field(description="模块标识；接口前缀、表名前缀、工具名前缀都用它")
    name: str
    description: str
    version: str
    enabled: bool = Field(description="在当前工作空间是否启用")
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
