"""模块注册表：工作台有哪些插拔式模块（docs/modules/README.md）。

新增模块：在 MODULES 里加一行（``make module-new`` 会自动加）。这里只导入各模块的清单
（纯数据），模块的其余代码在用到对应扩展点时才导入。
"""

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from app.modules.base import CronSpec, ModuleManifest, ServiceCheckSpec, validate
from app.modules.base import resolve as resolve
from app.modules.llm_bridge.manifest import MANIFEST as LLM_BRIDGE
from app.modules.news.manifest import MANIFEST as NEWS

if TYPE_CHECKING:
    from fastapi import APIRouter

    from app.schemas.tool_spec import ToolSpec

MODULES: tuple[ModuleManifest, ...] = (
    NEWS,
    LLM_BRIDGE,
    # module-new: 新模块的清单加在这一行上面（make module-new 会自动插入）
)
BY_KEY: Mapping[str, ModuleManifest] = {m.key: m for m in MODULES}


def get(key: str) -> ModuleManifest | None:
    return BY_KEY.get(key)


def problems() -> list[str]:
    """所有清单的格式问题与相互关系问题（重复的 key、找不到的依赖、循环依赖）。"""
    found: list[str] = []
    if len(BY_KEY) != len(MODULES):
        found.append("模块 key 有重复")
    for manifest in MODULES:
        found.extend(validate(manifest))
        for dependency in manifest.depends_on:
            if dependency not in BY_KEY:
                found.append(f"{manifest.key}: 依赖的模块 {dependency} 不存在")
    for manifest in MODULES:
        if manifest.key in _closure(manifest.key):
            found.append(f"{manifest.key}: 依赖关系有循环")
    return found


def _closure(key: str) -> set[str]:
    """key 直接或间接依赖的模块（不含自己，除非有循环）。"""
    seen: set[str] = set()
    stack = list(BY_KEY[key].depends_on) if key in BY_KEY else []
    while stack:
        current = stack.pop()
        if current in seen or current not in BY_KEY:
            continue
        seen.add(current)
        stack.extend(BY_KEY[current].depends_on)
    return seen


def dependents(key: str) -> list[str]:
    """直接依赖 key 的模块。"""
    return [m.key for m in MODULES if key in m.depends_on]


def import_models() -> None:
    """导入各模块的 ORM 模型（app.models 在末尾调用）。"""
    for manifest in MODULES:
        if manifest.models:
            importlib.import_module(manifest.models)


def routers() -> list[tuple[ModuleManifest, "APIRouter"]]:
    return [(m, resolve(m.router)) for m in MODULES if m.router]


def internal_routers() -> list[tuple[ModuleManifest, "APIRouter"]]:
    return [(m, resolve(m.internal_router)) for m in MODULES if m.internal_router]


@cache
def tool_specs() -> tuple["ToolSpec", ...]:
    return tuple(spec for m in MODULES if m.tools for spec in resolve(m.tools))


@cache
def tool_owners() -> Mapping[str, str]:
    """工具名 → 所属模块的 key。"""
    return {spec.name: m.key for m in MODULES if m.tools for spec in resolve(m.tools)}


@cache
def direct_runners() -> Mapping[str, Callable[..., Any]]:
    """模块里风险等级为 direct 的工具名 → 处理函数（清单的 direct_tools）。"""
    runners: dict[str, Callable[..., Any]] = {}
    for manifest in MODULES:
        if manifest.tools and manifest.direct_tools:
            run = resolve(manifest.direct_tools)
            runners.update({s.name: run for s in resolve(manifest.tools) if s.risk == "direct"})
    return runners


def read_handlers() -> dict[str, Callable[..., Any]]:
    handlers: dict[str, Callable[..., Any]] = {}
    for manifest in MODULES:
        if manifest.tool_handlers:
            handlers.update(resolve(manifest.tool_handlers))
    return handlers


def cron_specs() -> list[CronSpec]:
    return [cron for m in MODULES for cron in m.crons]


def service_checks() -> list[tuple[ModuleManifest, ServiceCheckSpec]]:
    return [(m, check) for m in MODULES for check in m.service_checks]


def service_names() -> dict[str, str]:
    return {check.service_id: check.name for m in MODULES for check in m.service_checks}


@cache
def secret_path_params() -> Mapping[str, frozenset[str]]:
    return {
        path: frozenset(params) for m in MODULES for path, params in m.secret_path_params.items()
    }


def sealed_fields() -> tuple[Any, ...]:
    return tuple(sealed for m in MODULES if m.sealed_fields for sealed in resolve(m.sealed_fields))


@dataclass(frozen=True)
class Requirement:
    setting: str
    env: str


def missing_requirements(manifest: ModuleManifest, settings: Any) -> list[Requirement]:
    """没满足的部署前提（Settings 里对应的值为空）。"""
    return [
        Requirement(name, name.upper())
        for name in manifest.requires
        if not getattr(settings, name, None)
    ]
