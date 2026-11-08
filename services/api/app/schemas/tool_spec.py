"""智能体工具的描述（ToolSpec）。

单独放一个文件：模块（app/modules/<key>/tools.py）声明自己的工具时只依赖这里，
不必导入整份工具清单 app/schemas/ai_tools.py（那里会汇总各模块的工具，互相导入会成环）。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.schemas.agents import McpScope
from app.schemas.workspaces import Role

# knowledge：知识库工具，直接执行、不审批（docs/v2/KNOWLEDGE_PLAN.md 3.7，人类 2026-11-03 决定）
# direct：领取 / 放弃 / 完成任务，直接执行、不审批
# （docs/v2/AGENT_OPS_PLAN.md 第 7 节，人类 2026-11-04 决定）
RiskLevel = Literal["read", "write", "critical", "knowledge", "direct"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: RiskLevel
    scope: McpScope
    min_role: Role
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    description: str


def make_spec(
    name: str,
    risk: RiskLevel,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    description: str,
    min_role: Role | None = None,
) -> ToolSpec:
    """读工具的权限范围是 read、最低角色 viewer；其余是 read_write、member（可以另外指定角色）。"""
    scope: McpScope = "read" if risk == "read" else "read_write"
    role = min_role or (Role.viewer if risk == "read" else Role.member)
    return ToolSpec(name, risk, scope, role, input_model, output_model, description)
