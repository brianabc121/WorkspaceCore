# ruff: noqa: F811 - 测试函数的参数名与导入的 fixture 同名是 pytest 的用法
"""应用中心的管理投影（T-0210，docs/v2/app-center/ AC-1）。

- 只有 owner / admin 能读管理信息、检测、看操作记录；成员仍然只用 GET /modules；
- GET 只读缓存，不探测外部服务；ready（部署）与 health（健康）分开；
- 检测只探测清单声明的服务，停用时拒绝，没部署时不出站，限频；
- 声明的能力（工具、定时任务、组件）原样来自清单，不输出导入路径与设置值；
- 操作记录精确按模块 key 过滤，不混入前缀相同的模块与其他工作空间，游标绑定模块。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.modules import registry
from app.modules.base import CronSpec, ModuleManifest
from app.modules.management import aggregate_health, schedule_summary, sync_view
from app.modules.service import ModuleState
from app.schemas.operations import ServiceStatusOut
from tests.fake_trendradar import URL, FakeTrendRadar
from tests.helpers import add_member, add_user, add_workspace, csrf, run_sql
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_news_api import (  # noqa: F401 - 复用早报测试的环境（fixture 按名字注入）
    env,
    fake_dns,
    login,
    save,
    trendradar,
)

NEWS_TOOLS = {
    "news_latest",
    "news_by_date",
    "news_search",
    "news_trending",
    "news_rss",
    "news_briefing",
    "news_related",
}


async def _get(env: ImEnv, path: str, expect: int = 200, **headers: str) -> Any:
    response = await env.client.get(path, headers={**env.headers(), **headers})
    assert response.status_code == expect, response.text
    return response.json()


async def _post(env: ImEnv, path: str, body: Any = None, expect: int = 200) -> Any:
    response = await env.client.post(path, json=body, headers=env.headers(write=True))
    assert response.status_code == expect, response.text
    return response


async def _management(env: ImEnv) -> dict[str, Any]:
    data: dict[str, Any] = await _get(env, "/api/v1/modules/management")
    return data


async def _news(env: ImEnv) -> dict[str, Any]:
    data: dict[str, Any] = await _get(env, "/api/v1/modules/news/management")
    return data


async def _toggle(env: ImEnv, key: str, enabled: bool, expect: int = 200) -> Any:
    response = await env.client.patch(
        f"/api/v1/modules/{key}", json={"enabled": enabled}, headers=env.headers(write=True)
    )
    assert response.status_code == expect, response.text
    return response.json()


# ---------------------------------------------------------------- 纯函数


def _status(state: str) -> ServiceStatusOut:
    return ServiceStatusOut.model_validate(
        {"id": "x", "name": "x", "kind": "integration", "status": state, "checked_at": None}
    )


def test_health_aggregation() -> None:
    assert aggregate_health([]) == "not_applicable"
    assert aggregate_health([_status("not_configured")] * 2) == "not_configured"
    assert aggregate_health([_status("ok"), _status("down")]) == "down"
    assert aggregate_health([_status("ok"), _status("degraded")]) == "degraded"
    assert aggregate_health([_status("ok"), _status("not_configured")]) == "degraded"
    assert aggregate_health([_status("ok")] * 2) == "ok"
    assert aggregate_health([_status("ok"), _status("unknown")]) == "unknown"


def test_schedule_summary() -> None:
    assert schedule_summary({"second": 33, "timeout": 120, "unique": True}) == "每分钟"
    assert schedule_summary({"minute": 5}) == "每小时第 5 分钟"
    assert schedule_summary({"hour": 3, "minute": 30}) == "每天 03:30"
    assert schedule_summary({"hour": {1, 2}, "minute": 0}) == "按计划运行"
    assert schedule_summary({"weekday": 0, "hour": 3, "minute": 0}) == "按计划运行"


def test_sync_view_uses_only_the_current_target() -> None:
    hooked = ModuleManifest(key="demo", name="d", description="d", on_toggle="x.y:z")
    plain = ModuleManifest(key="demo", name="d", description="d")
    now = datetime.now(UTC)
    state = ModuleState(True, 3)
    assert sync_view(plain, state, None, now).state == "not_required"
    assert sync_view(hooked, state, None, now).state == "unrecorded"

    class Attempt:
        id = uuid.uuid4()
        state_version = 2
        target_enabled = True
        trigger = "toggle"
        state = "failed"
        reason = "hook_failed"
        started_at = now - timedelta(minutes=10)
        finished_at = now
        created_at = started_at

    old: Any = Attempt()
    # 旧版本的失败只是历史：当前目标没有记录
    assert sync_view(hooked, state, old, now).state == "unrecorded"
    old.state_version, old.state, old.reason, old.finished_at = 3, "running", None, None
    view = sync_view(hooked, state, old, now)
    assert (view.state, view.reason) == ("unknown", "interrupted")
    old.started_at = now
    assert sync_view(hooked, state, old, now).state == "running"


# ---------------------------------------------------------------- 列表与详情


async def test_management_projection_is_read_only_and_declarative(
    env: ImEnv, trendradar: FakeTrendRadar
) -> None:
    before = len(trendradar.calls)
    data = await _management(env)
    assert len(trendradar.calls) == before  # GET 不探测外部服务
    assert data["summary"] == {"registered": len(registry.MODULES), "enabled": 1, "attention": 0}
    news = next(item for item in data["items"] if item["module"]["key"] == "news")
    assert news == await _news(env)
    assert news["module"]["state_version"] == 0 and news["module"]["version"] == "1.0.0"
    assert news["host_api"] == 1
    capabilities = news["capabilities"]
    assert {t["name"] for t in capabilities["tools"]} == NEWS_TOOLS
    assert {t["risk"] for t in capabilities["tools"]} == {"read"}
    assert capabilities["crons"] == [
        {"id": "news_tick", "kind": "scheduled", "schedule_summary": "每分钟"}
    ]
    assert capabilities["widgets"] == ["news_briefing"]
    assert capabilities["event_topics"] == ["news"]
    assert capabilities["services"] == [{"id": "trendradar", "name": "TrendRadar（新闻早报）"}]
    assert capabilities["has_sync_hook"] is True
    # 部署满足（ready）不等于健康：还没检测过
    assert news["module"]["ready"] is True
    assert news["health"]["state"] == "unknown" and news["health"]["checked_at"] is None
    assert news["health"]["checks"][0]["reason"] == "not_checked"
    # 默认启用、没有启停过：有同步钩子但没有记录
    assert news["sync"]["state"] == "unrecorded" and news["sync"]["attempt_id"] is None
    assert news["attention"] is False
    assert news["actions"] == {
        "can_toggle": True,
        "can_retry_sync": True,
        "can_check_health": True,
    }
    text = str(data)
    for leaked in (URL, "app.modules", "hooks:on_toggle", "trendradar_mcp_url"):
        assert leaked not in text


async def test_management_permissions_and_scope(env: ImEnv) -> None:
    workspace = uuid.UUID(env.workspace_id)
    admin = await add_user(env.database_url, "admin@example.com", "admin password 1234")
    member = await add_user(env.database_url, "member@example.com", "member password 1234")
    await add_member(env.database_url, workspace, admin, "admin")
    await add_member(env.database_url, workspace, member, "member")
    other = await add_workspace(env.database_url, env.owner_id, "另一个工作空间")
    await _toggle(env, "news", False)

    # 另一个工作空间：独立的状态与记录
    elsewhere = await _get(env, "/api/v1/modules/news/management", **{"X-Workspace-Id": str(other)})
    assert elsewhere["module"]["enabled"] is True and elsewhere["module"]["state_version"] == 0
    page = await _get(env, "/api/v1/modules/news/activity", **{"X-Workspace-Id": str(other)})
    assert page["items"] == []

    await login(env, "admin@example.com", "admin password 1234")
    news = await _news(env)
    assert news["module"]["can_manage"] is False
    assert news["actions"]["can_toggle"] is False and news["actions"]["can_retry_sync"] is False
    assert (await _get(env, "/api/v1/modules/news/activity"))["items"]
    response = await _post(
        env, "/api/v1/modules/news/reconcile", {"expected_state_version": 1}, expect=403
    )
    assert response.json()["error"]["code"] == "FORBIDDEN"

    await login(env, "member@example.com", "member password 1234")
    assert (await _get(env, "/api/v1/modules"))["items"][0]["key"] == "news"
    for path in (
        "/api/v1/modules/management",
        "/api/v1/modules/activity",
        "/api/v1/modules/news/management",
        "/api/v1/modules/news/activity",
    ):
        await _get(env, path, expect=403)
    await _post(env, "/api/v1/modules/news/health-check", expect=403)


async def test_unknown_module_is_not_found(env: ImEnv) -> None:
    await _get(env, "/api/v1/modules/nope/management", expect=404)
    await _get(env, "/api/v1/modules/nope/activity", expect=404)
    await _post(env, "/api/v1/modules/nope/health-check", expect=404)
    await _post(env, "/api/v1/modules/nope/reconcile", {"expected_state_version": 0}, expect=404)
    await _get(env, "/api/v1/modules/Bad-Key/management", expect=422)
    # 请求体只接受 expected_state_version
    await _post(
        env, "/api/v1/modules/news/reconcile", {"expected_state_version": 0, "x": 1}, expect=422
    )


# ---------------------------------------------------------------- 检测


async def test_health_check_probes_only_declared_services(
    env: ImEnv, trendradar: FakeTrendRadar
) -> None:
    before = len(trendradar.calls)
    checked = (await _post(env, "/api/v1/modules/news/health-check")).json()
    assert checked["state"] == "ok" and checked["stale"] is False
    assert checked["checks"][0]["id"] == "trendradar" and checked["checked_at"]
    assert [name for name, _ in trendradar.calls[before:]] == ["get_system_status"]
    # GET 读到刚才的检测结果（缓存），不再探测
    calls = len(trendradar.calls)
    assert (await _news(env))["health"]["state"] == "ok"
    assert len(trendradar.calls) == calls

    trendradar.down = True
    down = (await _post(env, "/api/v1/modules/news/health-check")).json()
    assert down["state"] == "down" and down["checks"][0]["reason"]
    data = await _management(env)
    assert data["summary"]["attention"] == 1
    assert data["items"][0]["attention"] is True

    await _post(env, "/api/v1/modules/news/health-check")
    limited = await _post(env, "/api/v1/modules/news/health-check", expect=429)
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) >= 1

    # 停用后不能检测；停用的模块不因为健康 down 算“需处理”
    await _toggle(env, "news", False)
    refused = await _post(env, "/api/v1/modules/news/health-check", expect=409)
    assert refused.json()["error"]["code"] == "MODULE_DISABLED_FOR_CHECK"
    news = await _news(env)
    assert news["actions"]["can_check_health"] is False
    assert news["health"]["state"] == "down"  # 最近一次检测的结果仍然显示
    assert news["attention"] is False


@pytest.fixture
async def bare(
    make_client: Any, migrated_database_url: str, tmp_path: Path
) -> AsyncIterator[ImEnv]:
    """没有部署 TrendRadar 的环境（TRENDRADAR_MCP_URL 为空）。"""
    async with im_env(
        make_client, migrated_database_url, news_config_dir=str(tmp_path / "trendradar")
    ) as env:
        yield env


async def test_not_deployed_module_can_still_be_enabled(bare: ImEnv) -> None:
    transport_calls: list[str] = []
    original = bare.app.state.connectors.transport

    async def spy(request: Any) -> Any:
        transport_calls.append(request.url.host)
        return await original.handle_async_request(request)

    bare.app.state.connectors.transport.handle_async_request = spy  # type: ignore[method-assign]
    news = await _news(bare)
    assert news["module"]["ready"] is False and news["module"]["missing"] == ["TRENDRADAR_MCP_URL"]
    assert news["health"]["state"] == "not_configured"
    assert news["attention"] is True  # 启用了却没部署
    checked = (await _post(bare, "/api/v1/modules/news/health-check")).json()
    assert checked["state"] == "not_configured"
    assert checked["checks"][0]["reason"] == "not_deployed"
    assert transport_calls == []  # 没部署不出站

    # 缺部署前提不阻止启用：停用再启用都可以，同步记为“不需要”
    await _toggle(bare, "news", False)
    enabled = await _toggle(bare, "news", True)
    assert enabled["enabled"] is True and enabled["ready"] is False
    news = await _news(bare)
    assert (news["sync"]["state"], news["sync"]["reason"]) == ("not_required", "not_deployed")
    assert news["module"]["state_version"] == 2


# ---------------------------------------------------------------- 操作记录


async def test_activity_is_exact_per_module_and_workspace(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = ModuleManifest(
        key="news_digest",
        name="早报摘要",
        description="测试用的模块（key 以 news 开头）",
        crons=(CronSpec("tests.test_module_management:schedule_summary", {"minute": 5}),),
    )
    modules = (*registry.MODULES, digest)
    monkeypatch.setattr(registry, "MODULES", modules)
    monkeypatch.setattr(registry, "BY_KEY", {m.key: m for m in modules})
    other = await add_workspace(env.database_url, env.owner_id, "另一个工作空间")
    await save(env)  # 打开早报推送：启停时钩子会重写配置文件（同步成功）

    await _toggle(env, "news", False)
    await _toggle(env, "news", True)
    await _toggle(env, "news_digest", True)
    response = await env.client.patch(
        "/api/v1/modules/news",
        json={"enabled": False},
        headers={"X-Workspace-Id": str(other), **csrf(env.client)},
    )
    assert response.status_code == 200, response.text
    # 名字里带 news 的其他资源
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.audit_logs (workspace_id, action, resource_type, resource_id) "
        "VALUES ($1, 'news.settings_updated', 'news_settings', 'news')",
        uuid.UUID(env.workspace_id),
    )

    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        query = "?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = await _get(env, "/api/v1/modules/news/activity" + query)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        first_cursor = cursor
    assert {i["resource_type"] for i in items} == {"module"}
    assert {i["resource_id"] for i in items} == {"news"}
    actions = [i["action"] for i in items]
    assert actions.count("module.disabled") == 1 and actions.count("module.enabled") == 1
    assert actions.count("module.sync.succeeded") == 2
    assert all(i["action_label"] for i in items)
    assert "total_estimate" in page and page["total_estimate"] is None

    # 游标绑定模块：拿到别的模块上用 422
    wrong = await env.client.get(
        f"/api/v1/modules/news_digest/activity?cursor={first_cursor}", headers=env.headers()
    )
    assert wrong.status_code == 422
    digest_page = await _get(env, "/api/v1/modules/news_digest/activity")
    assert [i["action"] for i in digest_page["items"]] == ["module.enabled"]
    everything = await _get(env, "/api/v1/modules/activity?limit=100")
    assert {i["resource_id"] for i in everything["items"]} == {"news", "news_digest"}
    assert len(everything["items"]) == len(items) + 1

    digest_view = await _get(env, "/api/v1/modules/news_digest/management")
    assert digest_view["capabilities"]["crons"] == [
        {"id": "schedule_summary", "kind": "scheduled", "schedule_summary": "每小时第 5 分钟"}
    ]
    assert digest_view["sync"]["state"] == "not_required"
    assert digest_view["health"]["state"] == "not_applicable"
    assert digest_view["actions"]["can_check_health"] is False
    checked = (await _post(env, "/api/v1/modules/news_digest/health-check")).json()
    assert checked == {"state": "not_applicable", "checked_at": None, "stale": False, "checks": []}
