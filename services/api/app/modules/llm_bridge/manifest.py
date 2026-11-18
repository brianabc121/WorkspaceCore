"""大模型会话桥模块的清单（docs/v2/LLM_BRIDGE_PLAN.md；模块写法见 docs/modules/README.md）。

会话桥是独立部署的服务（services/llm-bridge、infra/llm-bridge）；地址和令牌由 owner 在页面上登记，
所以没有部署前提（requires）。
"""

from app.modules.base import CronSpec, ModuleManifest

MANIFEST = ModuleManifest(
    key="llm_bridge",
    # 页面叫“大模型客户端管理”：一个 MCP 管理器就是一座登记的会话桥（方案第 19 节）
    name="大模型客户端",
    description="和 Codex / Claude / CodeBuddy 对话、查看智能体开的会话；"
    "管理一个或多个 MCP 管理器：每个管理器控制选定的平台、单独登录一套账号，"
    "智能体经它发指令并管理会话。"
    "每个管理器是独立部署的容器（会话桥）。",
    version="0.1.0",
    # 默认启用（人类 2026-11-15 要求）：没有启用 / 停用记录的工作空间直接有入口，
    # owner 可以在“应用中心”停用。启用只是有了页面和接口：没有添加 MCP 管理器、
    # 没有给实例打开“大模型会话权限”时，智能体什么都用不了
    default_enabled=True,
    models="app.modules.llm_bridge.models",
    router="app.modules.llm_bridge.api:router",
    tools="app.modules.llm_bridge.tools:SPECS",
    tool_handlers="app.modules.llm_bridge.tool_handlers:HANDLERS",
    # 按实例授权（docs/AGENTS.md 例外九）：没打开“大模型会话权限”的实例看不到也调用不了
    tools_for_instance="app.modules.llm_bridge.tool_handlers:visible_tools",
    direct_tools="app.modules.llm_bridge.tool_handlers:run",
    sealed_fields="app.modules.llm_bridge.sealed:SEALED_FIELDS",
    # 远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md）：容器服务器托管进程的输出转给管理器
    runner_proc_sink="app.modules.llm_bridge.remote:SINK",
    # 每分钟同步一次会话索引（哪个管理器上有哪些会话，不含对话内容）
    crons=(
        CronSpec(
            "app.modules.llm_bridge.jobs:index_tick",
            {"second": 17, "timeout": 120, "unique": True},
        ),
        # 每 15 秒看一次智能体要“结束后叫我”的回合；没有在盯的回合时什么都不做
        CronSpec(
            "app.modules.llm_bridge.watches:watch_tick",
            {"second": {5, 20, 35, 50}, "timeout": 60, "unique": True},
        ),
        # 远程开发容器：每分钟一次，跑大约 50 秒（长轮询管理器的远程操作、建等着的容器、补发结束）
        CronSpec(
            "app.modules.llm_bridge.remote:remote_tick",
            {"second": 0, "timeout": 75, "unique": True},
        ),
    ),
)
