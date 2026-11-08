# ruff: noqa: F811 - 测试函数的参数名与导入的 fixture 同名是 pytest 的用法
"""插拔式模块（T-0159 / T-0160；docs/modules/README.md）。

- 注册表一致性：清单格式、接口前缀、工具名、事件主题、小组件类型、脱敏路径、启用检查；
- 启用 / 停用接口：只有 owner；审计；依赖关系；
- 停用后：模块接口 404 MODULE_DISABLED、工具不再列出也不能调用、定时任务和接收地址不再生效；
  再启用数据还在。
"""

import inspect
import re
import uuid
from typing import Any, get_args

import pytest
from fastapi import FastAPI

from app.domain.mcp_read_tools import READ_HANDLERS
from app.modules import registry
from app.modules.base import ModuleManifest, resolve, validate
from app.modules.news import agent as news_agent
from app.schemas.ai_tools import TOOLS
from app.schemas.dashboards import WidgetCreate
from app.schemas.notifications import EventTopic
from tests.helpers import add_member, add_user, run_sql, tool_error
from tests.test_agent_projects_api import tool_names
from tests.test_im_accounts_callbacks import ImEnv
from tests.test_news_api import (  # noqa: F401 - 复用早报测试的环境（fixture 按名字注入）
    env,
    fake_dns,
    ingest_token,
    login,
    push,
    save,
    settings,
    trendradar,
)
from tests.test_task_claims_api import agent, call

# ---------------------------------------------------------------- 注册表一致性


def _widget_types() -> set[str]:
    union = get_args(WidgetCreate)[0]
    return {get_args(model.model_fields["widget_type"].annotation)[0] for model in get_args(union)}


def test_registry_has_no_problems() -> None:
    assert registry.problems() == []


def test_validate_reports_bad_manifests() -> None:
    bad = ModuleManifest(
        key="Bad-Key",
        name="",
        description="",
        host_api=99,
        router="no colon here",
        secret_path_params={"/internal/other/{token}": ("{token}",)},
    )
    found = "\n".join(validate(bad))
    for fragment in ("key 只能是", "host_api=99", "不能为空", "导入路径", "不属于这个模块"):
        assert fragment in found


def test_module_contributions_follow_the_rules(app: FastAPI) -> None:
    topics = set(get_args(EventTopic))
    widgets = _widget_types()
    for manifest in registry.MODULES:
        key = manifest.key
        for target in manifest.targets():
            assert resolve(target) is not None, target
        for _, router in [r for r in registry.routers() if r[0] is manifest]:
            for route in router.routes:
                path = getattr(route, "path", "")
                assert path == f"/{key}" or path.startswith(f"/{key}/"), path
        internal_paths = set()
        for _, router in [r for r in registry.internal_routers() if r[0] is manifest]:
            for route in router.routes:
                path = getattr(route, "path", "")
                assert path.startswith(f"/internal/{key}/"), path
                internal_paths.add(path)
        for path in manifest.secret_path_params:
            assert path in internal_paths or path.startswith(f"/api/v1/{key}/"), path
        owned = [name for name, owner in registry.tool_owners().items() if owner == key]
        for name in owned:
            assert name.startswith(f"{key}_"), name
            assert name in TOOLS
            if TOOLS[name].risk == "read":
                assert name in READ_HANDLERS, name
        assert set(manifest.event_topics) <= topics
        assert set(manifest.widgets) <= widgets
        for cron in manifest.crons:
            assert inspect.iscoroutinefunction(resolve(cron.target)), cron.target
        for sealed in registry.sealed_fields():
            assert hasattr(sealed.model, sealed.column)


def test_module_jobs_and_service_checks_are_registered() -> None:
    from app.jobs.worker import WorkerSettings

    names = {job.name for job in WorkerSettings.cron_jobs}
    assert "cron:news_tick" in names
    assert registry.service_names() == {"trendradar": "TrendRadar（新闻早报）"}


# ---------------------------------------------------------------- 启用 / 停用


async def _modules(env: ImEnv) -> dict[str, dict[str, Any]]:
    response = await env.client.get("/api/v1/modules", headers=env.headers())
    assert response.status_code == 200, response.text
    return {item["key"]: item for item in response.json()["items"]}


async def _toggle(env: ImEnv, key: str, enabled: bool, expect: int = 200) -> dict[str, Any]:
    response = await env.client.patch(
        f"/api/v1/modules/{key}", json={"enabled": enabled}, headers=env.headers(write=True)
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_list_describes_each_module(env: ImEnv) -> None:
    news = (await _modules(env))["news"]
    assert news["enabled"] is True and news["default_enabled"] is True
    assert news["ready"] is True and news["missing"] == []
    assert news["can_manage"] is True
    assert "news_latest" in news["tools"] and news["widgets"] == ["news_briefing"]


async def test_disabling_news_turns_off_pages_tools_jobs_and_ingest(env: ImEnv) -> None:
    _, agent_token = await agent(env, "甲")
    await save(env)
    before = await settings(env)
    token = await ingest_token(env)
    assert "news_latest" in await tool_names(env, agent_token)

    disabled = await _toggle(env, "news", False)
    assert disabled["enabled"] is False

    page = await env.client.get("/api/v1/news/today", headers=env.headers())
    assert page.status_code == 404
    assert page.json()["error"]["code"] == "MODULE_DISABLED"
    assert not {n for n in await tool_names(env, agent_token) if n.startswith("news_")}
    refused = tool_error(await call(env, agent_token, "news_latest", {}))
    assert refused["code"] == "NOT_FOUND"
    assert (await push(env, token, "当前榜单", "停用后不收")).status_code == 404
    async with env.app.state.sessionmaker() as db:
        stats = await news_agent.tick(
            db, env.app.state.settings, env.app.state.connectors, env.app.state.redis
        )
    assert stats == {}
    audit = await run_sql(
        env.database_url,
        "SELECT action || ':' || resource_id FROM workspace.audit_logs "
        "WHERE action LIKE 'module.%' ORDER BY created_at",
    )
    assert audit == "module.disabled:news"

    # 再启用：设置原样还在，工具和接收地址恢复
    assert (await _toggle(env, "news", True))["enabled"] is True
    assert await settings(env) == before
    assert "news_latest" in await tool_names(env, agent_token)
    assert (await push(env, token, "当前榜单", "恢复了")).json() == {"result": "ok"}


async def test_every_module_route_checks_that_the_module_is_enabled(env: ImEnv) -> None:
    """宿主给模块的每个接口都加了启用检查：停用后，不管参数和请求体对不对，都是 MODULE_DISABLED。"""
    for manifest in registry.MODULES:
        await _toggle(env, manifest.key, False)
    paths = env.app.openapi()["paths"]
    checked = 0
    for manifest in registry.MODULES:
        prefix = f"/api/v1/{manifest.key}"
        for path, operations in paths.items():
            if path != prefix and not path.startswith(prefix + "/"):
                continue
            url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
            for method in operations:
                write = method != "get"
                response = await env.client.request(
                    method.upper(),
                    url,
                    json={} if write else None,
                    headers=env.headers(write=write),
                )
                assert response.status_code == 404, (method, path, response.text)
                assert response.json()["error"]["code"] == "MODULE_DISABLED", (method, path)
                checked += 1
    assert checked >= 10


async def test_toggling_twice_is_idempotent(env: ImEnv) -> None:
    await _toggle(env, "news", False)
    await _toggle(env, "news", False)
    audit = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'module.disabled'",
    )
    assert audit == 1


async def test_only_the_owner_can_toggle(env: ImEnv) -> None:
    workspace = uuid.UUID(env.workspace_id)
    admin = await add_user(env.database_url, "admin@example.com", "admin password 1234")
    viewer = await add_user(env.database_url, "viewer@example.com", "viewer password 1234")
    await add_member(env.database_url, workspace, admin, "admin")
    await add_member(env.database_url, workspace, viewer, "viewer")

    await login(env, "admin@example.com", "admin password 1234")
    assert (await _modules(env))["news"]["can_manage"] is False
    await _toggle(env, "news", False, expect=403)

    await login(env, "viewer@example.com", "viewer password 1234")
    assert (await _modules(env))["news"]["enabled"] is True
    await _toggle(env, "news", False, expect=403)


async def test_unknown_or_malformed_module_keys(env: ImEnv) -> None:
    await _toggle(env, "nope", True, expect=404)
    await _toggle(env, "Bad-Key", True, expect=422)
    response = await env.client.patch(
        "/api/v1/modules/news",
        json={"enabled": False, "extra": 1},
        headers=env.headers(write=True),
    )
    assert response.status_code == 422


async def test_dependencies_block_enable_and_disable(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = ModuleManifest(
        key="news_digest", name="早报摘要", description="测试用的依赖模块", depends_on=("news",)
    )
    modules = (*registry.MODULES, digest)
    monkeypatch.setattr(registry, "MODULES", modules)
    monkeypatch.setattr(registry, "BY_KEY", {m.key: m for m in modules})

    assert (await _modules(env))["news"]["dependents"] == ["news_digest"]
    await _toggle(env, "news_digest", True)
    blocked = await _toggle(env, "news", False, expect=409)
    assert blocked["error"]["code"] == "MODULE_DEPENDENCY"
    await _toggle(env, "news_digest", False)
    await _toggle(env, "news", False)
    blocked = await _toggle(env, "news_digest", True, expect=409)
    assert "新闻早报" in blocked["error"]["message"]
