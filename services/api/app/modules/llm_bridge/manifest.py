"""大模型会话桥模块的清单（docs/v2/LLM_BRIDGE_PLAN.md；模块写法见 docs/modules/README.md）。

会话桥是独立部署的服务（services/llm-bridge、infra/llm-bridge）；地址和令牌由 owner 在页面上登记，
所以没有部署前提（requires）。
"""

from app.modules.base import ModuleManifest

MANIFEST = ModuleManifest(
    key="llm_bridge",
    name="大模型会话桥",
    description="让智能体经会话桥向 ChatGPT / Codex / Claude / WorkBuddy 发指令并管理会话。"
    "依赖独立部署的会话桥。",
    version="0.1.0",
    # 新模块默认停用：owner 在“应用中心”启用
    default_enabled=False,
    models="app.modules.llm_bridge.models",
    router="app.modules.llm_bridge.api:router",
    tools="app.modules.llm_bridge.tools:SPECS",
    tool_handlers="app.modules.llm_bridge.tool_handlers:HANDLERS",
    # 按实例授权（docs/AGENTS.md 例外九）：没打开“大模型会话权限”的实例看不到也调用不了
    tools_for_instance="app.modules.llm_bridge.tool_handlers:visible_tools",
    direct_tools="app.modules.llm_bridge.tool_handlers:run",
    sealed_fields="app.modules.llm_bridge.sealed:SEALED_FIELDS",
)
