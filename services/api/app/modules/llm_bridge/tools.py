"""大模型会话桥给智能体的工具（docs/v2/LLM_BRIDGE_PLAN.md 第 9 节，docs/AGENTS.md 例外九）。

读工具走 MCP 读工具的通用流程；写工具（新建会话、发送、取消、归档）风险等级是 direct：
管理员给实例打开“大模型会话权限”后直接执行、不审批，由 tool_handlers.run 处理。
这里只放工具描述，供 app/schemas/ai_tools.py 汇总（不能导入它）。
"""

from pydantic import BaseModel

from app.modules.llm_bridge.tool_schemas import (
    DESCRIPTIONS,
    BridgeToolOutput,
    ChannelInput,
    QueueInput,
    SendInput,
    SessionArchiveInput,
    SessionCreateInput,
    SessionGetInput,
    SessionListInput,
    StatusInput,
    TurnCancelInput,
    TurnGetInput,
)
from app.schemas.tool_spec import RiskLevel, ToolSpec, make_spec

_TOOLS: tuple[tuple[str, RiskLevel, type[BaseModel]], ...] = (
    ("llm_bridge_status", "read", StatusInput),
    ("llm_bridge_models", "read", ChannelInput),
    ("llm_bridge_environments", "read", ChannelInput),
    ("llm_bridge_session_list", "read", SessionListInput),
    ("llm_bridge_session_get", "read", SessionGetInput),
    ("llm_bridge_turn_get", "read", TurnGetInput),
    ("llm_bridge_queue", "read", QueueInput),
    ("llm_bridge_session_create", "direct", SessionCreateInput),
    ("llm_bridge_send", "direct", SendInput),
    ("llm_bridge_turn_cancel", "direct", TurnCancelInput),
    ("llm_bridge_session_archive", "direct", SessionArchiveInput),
)

SPECS: tuple[ToolSpec, ...] = tuple(
    make_spec(name, risk, input_model, BridgeToolOutput, DESCRIPTIONS[name])
    for name, risk, input_model in _TOOLS
)
WRITE_TOOL_NAMES = frozenset(name for name, risk, _ in _TOOLS if risk == "direct")
