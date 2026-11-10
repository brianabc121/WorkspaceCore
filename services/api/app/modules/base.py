"""插拔式模块的清单（docs/modules/README.md，方案见 docs/v2/MODULES_PLAN.md）。

清单是数据，不是逻辑：只写名字、开关和“到哪里去找”的导入路径（``"包.模块:属性"``）。
导入清单不会连数据库，也不会导入模块的其余代码；宿主（``app/modules/registry.py``）
在需要某个扩展点时才解析这些路径。这样清单可以被任何地方安全地导入，不会出现循环导入。
"""

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

# 宿主提供的扩展点版本。清单的 host_api 不等于它时启动失败（扩展点有不兼容变化时加一）
HOST_API = 1
KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,31}")
_TARGET = re.compile(r"[a-z_][a-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*")
_MODULE_PATH = re.compile(r"[a-z_][a-z0-9_.]*")


@dataclass(frozen=True)
class CronSpec:
    """定时任务。target 指向 ``async def job(ctx: dict[str, Any]) -> int | None``（arq 的 ctx）。

    schedule 原样交给 arq 的 ``cron()``：second、minute、hour、timeout、unique、run_at_startup 等。
    任务自己只处理启用了这个模块的工作空间（``app.modules.service.is_enabled``）。
    """

    target: str
    schedule: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceCheckSpec:
    """运维页的服务检查（部署级的外部服务，例如 TrendRadar）。

    probe 指向 ``def make(settings, runtime)``，返回 None 表示没有部署（显示“未配置”），
    否则返回 ``(探测函数, 超时秒数)``：探测函数是 ``async def probe() -> None``，连不上就抛异常。
    """

    service_id: str
    name: str
    probe: str


@dataclass(frozen=True)
class HookOutcome:
    """on_toggle 钩子的结果（应用中心 T-0210）：同步记录按它写，不再“没抛异常就算成功”。

    - applied：确实把外部配置协调到了最新目标（例如新闻早报写好了 TrendRadar 配置）；
    - not_required：这次不需要做什么（没部署、这个工作空间没打开相关功能），reason 说明原因；
    - failed：做了但没成功，reason 是固定类别（不放异常正文）。
    旧钩子返回 None 仍然可以调用，记为“结果未确认”（unknown / result_unverified），不当作成功。
    """

    state: Literal["applied", "not_required", "failed"]
    reason: str | None = None

    @classmethod
    def applied(cls) -> "HookOutcome":
        return cls("applied")

    @classmethod
    def not_required(cls, reason: str) -> "HookOutcome":
        return cls("not_required", reason)

    @classmethod
    def failed(cls, reason: str) -> "HookOutcome":
        return cls("failed", reason)


@dataclass(frozen=True)
class ModuleManifest:
    """一个模块的清单。字段含义与填写方法见 docs/modules/README.md 第 3 节。"""

    key: str
    name: str
    description: str
    version: str = "1.0.0"
    host_api: int = HOST_API
    # 工作空间没有显式启用 / 停用记录时是否启用
    default_enabled: bool = False
    # 依赖的其他模块：它们都启用时才能启用本模块；有启用的模块依赖本模块时不能停用
    depends_on: tuple[str, ...] = ()
    # 部署前提：Settings 里的属性名，为空表示没部署（应用中心提示，不阻止启用）
    requires: tuple[str, ...] = ()
    # 模块的 ORM 模型所在的 Python 模块（被 app.models 导入，Alembic 与测试都能看到）
    models: str | None = None
    # 页面接口：挂到 /api/v1，路径必须以 /<key> 开头；宿主给每个路由加启用检查
    router: str | None = None
    # 内部接口（不经 /api/v1，Caddy 不转发）：路径必须以 /internal/<key> 开头，
    # 模块自己负责认证和启用检查
    internal_router: str | None = None
    # 访问日志里换成 *** 的路径参数：{路由模板: (参数, ...)}，
    # 例如 {"/internal/x/{token}": ("{token}",)}
    secret_path_params: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # 智能体工具：ToolSpec 列表（名字以 <key>_ 开头）、读工具处理函数表、额外的可见条件
    tools: str | None = None
    tool_handlers: str | None = None
    # ``async def visible(db, settings, workspace_id) -> bool``：模块启用之后还要满足的条件
    tools_visible: str | None = None
    crons: tuple[CronSpec, ...] = ()
    # 实时事件主题（必须在 app.schemas.notifications.EventTopic 里）
    event_topics: tuple[str, ...] = ()
    # 首页小组件类型（必须在 app.schemas.dashboards 的组件类型里，前端注册表也要有）
    widgets: tuple[str, ...] = ()
    # 凭证轮换（app/domain/credential_rotation.py）要处理的加密列：指向 SealedField 元组
    sealed_fields: str | None = None
    service_checks: tuple[ServiceCheckSpec, ...] = ()
    # 启用 / 停用之后调用（开关已提交）：
    # ``async def on_toggle(db, settings, runtime, workspace_id, enabled) -> HookOutcome``，
    # 例如新闻早报停用后重新生成 TrendRadar 配置文件、不再推送。钩子要按数据库里的最新状态协调、
    # 可以重复执行；失败不回滚开关，结果写进同步记录，owner 可以在应用中心重试（T-0210）
    on_toggle: str | None = None

    def targets(self) -> list[str]:
        """清单里所有 ``包.模块:属性`` 形式的导入路径（一致性检查用）。"""
        found = [
            self.router,
            self.internal_router,
            self.tools,
            self.tool_handlers,
            self.tools_visible,
            self.sealed_fields,
            self.on_toggle,
            *(c.target for c in self.crons),
            *(s.probe for s in self.service_checks),
        ]
        return [t for t in found if t is not None]


def validate(manifest: ModuleManifest) -> list[str]:
    """清单自身的格式问题（不导入模块代码）；空列表表示没问题。"""
    problems: list[str] = []
    key = manifest.key
    if not KEY_PATTERN.fullmatch(key):
        problems.append(f"{key}: key 只能是小写字母开头的小写字母、数字、下划线，2~32 个字符")
    if manifest.host_api != HOST_API:
        problems.append(f"{key}: host_api={manifest.host_api}，宿主是 {HOST_API}")
    if not manifest.name.strip() or not manifest.description.strip():
        problems.append(f"{key}: name 和 description 不能为空")
    for target in manifest.targets():
        if not _TARGET.fullmatch(target):
            problems.append(f"{key}: 导入路径 {target!r} 应写成 '包.模块:属性'")
    if manifest.models is not None and not _MODULE_PATH.fullmatch(manifest.models):
        problems.append(f"{key}: models 应写成 Python 模块路径")
    for path, params in manifest.secret_path_params.items():
        if not path.startswith(f"/internal/{key}/") and not path.startswith(f"/api/v1/{key}/"):
            problems.append(f"{key}: 脱敏路径 {path} 不属于这个模块")
        for param in params:
            if f"/{param}" not in path:
                problems.append(f"{key}: 脱敏参数 {param} 不在路径 {path} 里")
    for check in manifest.service_checks:
        if not KEY_PATTERN.fullmatch(check.service_id):
            problems.append(f"{key}: 服务检查的 service_id {check.service_id!r} 格式不对")
    return problems


@cache
def resolve(target: str) -> Any:
    """解析 ``包.模块:属性``；结果缓存（每个进程只导入一次）。"""
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)
