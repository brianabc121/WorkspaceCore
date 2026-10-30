"""仪表盘、Widget 与首页聚合的接口集成测试（T-0015）：真实 PostgreSQL（已迁移）+ Redis。

覆盖契约 ADR 的约定与 Sprint 2 后端验收：默认仪表盘、权限（A03）、布局保存与版本（A04）、
版本冲突、组件增删改、Registry 校验、跨工作空间、导入、切换首页、数量上限、未知类型的读取、
模块失败或超时时的降级、审计，以及并发写入。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest

from app.domain.overview import ModuleResult, unavailable
from app.schemas.dashboards import ModuleStatus, TaskBrief
from tests.helpers import (
    BOB,
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    csrf,
    run_sql,
)

pytestmark = pytest.mark.integration

LAYOUT = {"desktop": {"x": 0, "y": 0, "w": 4, "h": 3}, "mobile": {"y": 0, "h": 3}}
VIEWER = {"email": "viewer@example.com", "password": "viewer password 123"}


@dataclass
class Home:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def overview(self) -> dict[str, Any]:
        response = await self.client.get("/api/v1/dashboard/overview", headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data


@pytest.fixture
async def home(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Home]:
    async with make_client() as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Home(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


def app_of(client: httpx.AsyncClient) -> Any:
    return cast(httpx.ASGITransport, client._transport).app


def widget(**overrides: Any) -> dict[str, Any]:
    return {"widget_type": "note", "config": {"text": "你好"}, "layout": LAYOUT, **overrides}


async def test_setup_creates_the_default_dashboard_with_starter_widgets(home: Home) -> None:
    data = await home.overview()
    assert data["dashboard"]["is_default"] is True
    assert data["dashboard"]["title"] == "首页"
    assert [w["widget_type"] for w in data["widgets"]] == [
        "note",
        "quick_links",
        "todo_summary",
        "today_tasks",
    ]
    assert data["modules"] == {
        "tasks": {
            "status": "unavailable",
            "as_of": None,
            "retryable": False,
            "reason": "not_configured",
        }
    }
    assert data["summary"] == {
        "pending_tasks": None,
        "active_projects": None,
        "unread_notifications": None,
    }
    assert data["today_tasks"] == []
    listed = (await home.client.get("/api/v1/dashboards", headers=home.headers())).json()
    assert [d["id"] for d in listed["items"]] == [data["dashboard"]["id"]]


async def test_dashboard_endpoints_require_a_session(make_client: ClientFactory) -> None:
    async with make_client() as anonymous:
        headers = {"X-Workspace-Id": str(uuid.uuid4())}
        for path in ("/api/v1/dashboard/overview", "/api/v1/dashboards"):
            response = await anonymous.get(path, headers=headers)
            assert response.status_code == 401


async def test_viewer_can_read_but_not_write(home: Home, make_client: ClientFactory) -> None:
    """A03：viewer 修改 Widget 返回 403。"""
    data = await home.overview()
    dashboard, first = data["dashboard"], data["widgets"][0]
    viewer_id = await add_user(home.database_url, VIEWER["email"], VIEWER["password"])
    await add_member(home.database_url, uuid.UUID(home.workspace_id), viewer_id, "viewer")
    async with make_client() as viewer:
        assert (await viewer.post("/api/v1/auth/login", json=VIEWER)).status_code == 200
        read = {"X-Workspace-Id": home.workspace_id}
        write = {**read, **csrf(viewer)}
        assert (await viewer.get("/api/v1/dashboard/overview", headers=read)).status_code == 200
        widgets_path = f"/api/v1/dashboards/{dashboard['id']}/widgets"
        assert (await viewer.get(widgets_path, headers=read)).status_code == 200
        attempts = [
            viewer.patch(
                f"/api/v1/widgets/{first['id']}",
                json={"widget_type": "note", "config": {"text": "改"}, "version": first["version"]},
                headers=write,
            ),
            viewer.delete(f"/api/v1/widgets/{first['id']}", headers=write),
            viewer.post(widgets_path, json=widget(), headers=write),
            viewer.patch(
                f"/api/v1/dashboards/{dashboard['id']}",
                json={"version": dashboard["version"], "title": "改"},
                headers=write,
            ),
            viewer.post("/api/v1/dashboards", json={"title": "x"}, headers=write),
        ]
        for call in attempts:
            response = await call
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"
    assert (await home.overview())["widgets"][0]["config"] == first["config"]


async def test_layout_save_round_trip_and_version_conflict(home: Home) -> None:
    """A04：保存布局后刷新，布局一致、版本号递增；旧版本再保存返回 409。"""
    data = await home.overview()
    dashboard = data["dashboard"]
    placements = [
        {
            "widget_id": w["id"],
            "layout": {
                "desktop": {"x": 0, "y": index * 4, "w": 12, "h": 4},
                "mobile": {"y": index, "h": 4},
            },
        }
        for index, w in enumerate(reversed(data["widgets"]))
    ]
    path = f"/api/v1/dashboards/{dashboard['id']}"
    saved = await home.client.patch(
        path,
        json={"version": dashboard["version"], "layout": placements},
        headers=home.headers(write=True),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == dashboard["version"] + 1

    reloaded = await home.overview()
    assert reloaded["dashboard"]["version"] == dashboard["version"] + 1
    assert {w["id"]: w["layout"] for w in reloaded["widgets"]} == {
        p["widget_id"]: p["layout"] for p in placements
    }
    assert [w["id"] for w in reloaded["widgets"]] == [p["widget_id"] for p in placements]

    stale = await home.client.patch(
        path,
        json={"version": dashboard["version"], "layout": placements},
        headers=home.headers(write=True),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"] == {"current_version": dashboard["version"] + 1}

    current = dashboard["version"] + 1
    for bad in (
        placements[:-1],
        [*placements, placements[0]],
        [*placements[:-1], {**placements[0], "widget_id": str(uuid.uuid4())}],
    ):
        response = await home.client.patch(
            path, json={"version": current, "layout": bad}, headers=home.headers(write=True)
        )
        assert response.status_code == 422
    assert (await home.overview())["dashboard"]["version"] == current


async def test_widget_create_update_delete(home: Home) -> None:
    data = await home.overview()
    dashboard = data["dashboard"]
    created = await home.client.post(
        f"/api/v1/dashboards/{dashboard['id']}/widgets",
        json=widget(
            widget_type="quick_links",
            config={"links": [{"title": "仓库", "url": "https://github.com/"}]},
        ),
        headers=home.headers(write=True),
    )
    assert created.status_code == 201, created.text
    new = created.json()
    assert new["version"] == 1
    assert new["config"] == {"links": [{"title": "仓库", "url": "https://github.com/"}]}
    assert (await home.overview())["dashboard"]["version"] == dashboard["version"] + 1

    path = f"/api/v1/widgets/{new['id']}"
    body = {"widget_type": "quick_links", "config": {"links": []}, "version": 1}
    updated = await home.client.patch(path, json=body, headers=home.headers(write=True))
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["config"] == {"links": []}

    stale = await home.client.patch(path, json=body, headers=home.headers(write=True))
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"current_version": 2}
    retyped = await home.client.patch(
        path,
        json={"widget_type": "note", "config": {"text": "x"}, "version": 2},
        headers=home.headers(write=True),
    )
    assert retyped.status_code == 422

    deleted = await home.client.delete(path, headers=home.headers(write=True))
    assert deleted.status_code == 204
    assert (await home.overview())["dashboard"]["version"] == dashboard["version"] + 2
    gone = await home.client.patch(
        path, json={**body, "version": 2}, headers=home.headers(write=True)
    )
    assert gone.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        widget(widget_type="unknown_widget"),
        widget(config={"text": "x", "extra": 1}),
        widget(layout={**LAYOUT, "desktop": {"x": 10, "y": 0, "w": 4, "h": 3}}),
        widget(layout={"desktop": LAYOUT["desktop"]}),
        widget(
            widget_type="quick_links",
            config={"links": [{"title": "x", "url": "javascript:alert(1)"}]},
        ),
        widget(widget_type="today_tasks", config={"limit": 0}),
        widget(config={"text": "x" * 2001}),
    ],
    ids=[
        "unknown-type",
        "extra-field",
        "outside-grid",
        "missing-mobile",
        "js-url",
        "bad-limit",
        "long-note",
    ],
)
async def test_invalid_widgets_are_rejected(home: Home, body: dict[str, Any]) -> None:
    dashboard = (await home.overview())["dashboard"]
    response = await home.client.post(
        f"/api/v1/dashboards/{dashboard['id']}/widgets", json=body, headers=home.headers(write=True)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "javascript" not in response.text
    assert len((await home.overview())["widgets"]) == 4


async def test_resources_of_other_workspaces_are_not_found(
    home: Home, make_client: ClientFactory
) -> None:
    bob_id = await add_user(home.database_url, BOB["email"], BOB["password"])
    bob_workspace = str(await add_workspace(home.database_url, bob_id, "Bob 的空间"))
    async with make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        bob_headers = {"X-Workspace-Id": bob_workspace, **csrf(bob)}
        # bob 的工作空间是直接写库创建的：第一次访问首页时兜底补建默认仪表盘
        bob_home = (await bob.get("/api/v1/dashboard/overview", headers=bob_headers)).json()
        assert bob_home["dashboard"]["is_default"] is True

        owner_data = await home.overview()
        owner_dashboard, owner_widget = owner_data["dashboard"], owner_data["widgets"][0]
        attempts = [
            # 用自己的工作空间头访问别人的资源
            bob.get(f"/api/v1/dashboards/{owner_dashboard['id']}/widgets", headers=bob_headers),
            bob.patch(
                f"/api/v1/dashboards/{owner_dashboard['id']}",
                json={"version": owner_dashboard["version"], "title": "改"},
                headers=bob_headers,
            ),
            bob.patch(
                f"/api/v1/widgets/{owner_widget['id']}",
                json={"widget_type": "note", "config": {}, "version": owner_widget["version"]},
                headers=bob_headers,
            ),
            bob.delete(f"/api/v1/widgets/{owner_widget['id']}", headers=bob_headers),
            # 用别人的工作空间头
            bob.get(
                "/api/v1/dashboard/overview",
                headers={"X-Workspace-Id": home.workspace_id},
            ),
            home.client.get(
                f"/api/v1/dashboards/{bob_home['dashboard']['id']}/widgets", headers=home.headers()
            ),
        ]
        for call in attempts:
            response = await call
            assert response.status_code == 404, response.text
    assert (await home.overview())["widgets"] == owner_data["widgets"]


async def test_import_creates_a_dashboard_atomically_and_can_become_default(home: Home) -> None:
    original = (await home.overview())["dashboard"]
    imported = await home.client.post(
        "/api/v1/dashboards",
        json={
            "title": "导入的布局",
            "widgets": [widget(), widget(widget_type="todo_summary", config={})],
        },
        headers=home.headers(write=True),
    )
    assert imported.status_code == 201, imported.text
    new = imported.json()
    assert new["is_default"] is False
    widgets = await home.client.get(
        f"/api/v1/dashboards/{new['id']}/widgets", headers=home.headers()
    )
    # 两个组件布局相同，顺序不确定，按集合比较
    assert sorted(w["widget_type"] for w in widgets.json()["items"]) == ["note", "todo_summary"]

    broken = await home.client.post(
        "/api/v1/dashboards",
        json={"title": "坏的", "widgets": [widget(), widget(widget_type="nope")]},
        headers=home.headers(write=True),
    )
    assert broken.status_code == 422
    listed = (await home.client.get("/api/v1/dashboards", headers=home.headers())).json()
    assert [d["title"] for d in listed["items"]] == ["首页", "导入的布局"]

    switched = await home.client.patch(
        f"/api/v1/dashboards/{new['id']}",
        json={"version": new["version"], "is_default": True},
        headers=home.headers(write=True),
    )
    assert switched.status_code == 200
    assert switched.json()["is_default"] is True
    data = await home.overview()
    assert data["dashboard"]["id"] == new["id"]
    listed = (await home.client.get("/api/v1/dashboards", headers=home.headers())).json()
    old = next(d for d in listed["items"] if d["id"] == original["id"])
    assert old["is_default"] is False
    assert old["version"] == original["version"] + 1


async def test_limits(home: Home) -> None:
    dashboard = (await home.overview())["dashboard"]
    path = f"/api/v1/dashboards/{dashboard['id']}/widgets"
    for _ in range(50 - 4):
        assert (
            await home.client.post(path, json=widget(), headers=home.headers(write=True))
        ).status_code == 201
    over = await home.client.post(path, json=widget(), headers=home.headers(write=True))
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "LIMIT_EXCEEDED"

    for index in range(20 - 1):
        created = await home.client.post(
            "/api/v1/dashboards", json={"title": f"第 {index} 个"}, headers=home.headers(write=True)
        )
        assert created.status_code == 201
    over = await home.client.post(
        "/api/v1/dashboards", json={"title": "多余"}, headers=home.headers(write=True)
    )
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "LIMIT_EXCEEDED"


async def test_unknown_types_in_storage_do_not_break_the_page(home: Home) -> None:
    """DoD：未知 widget type 安全报错——服务端照常返回，由前端显示错误卡片。"""
    dashboard = (await home.overview())["dashboard"]
    await run_sql(
        home.database_url,
        "INSERT INTO workspace.widgets (dashboard_id, widget_type, config_json, layout_json)"
        " VALUES ($1, 'legacy_widget', '{\"anything\": [1, 2]}'::jsonb,"
        " '{\"broken\": true}'::jsonb)",
        uuid.UUID(dashboard["id"]),
    )
    data = await home.overview()
    legacy = next(w for w in data["widgets"] if w["widget_type"] == "legacy_widget")
    assert legacy["config"] == {"anything": [1, 2]}
    assert legacy["layout"]["desktop"]["y"] == 999  # 无法解析的布局放到最下方
    assert len(data["widgets"]) == 5


class _FailingProvider:
    name = "github"
    timeout_seconds = 1.0

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        raise RuntimeError("secret upstream detail")


class _SlowProvider:
    name = "calendar"
    timeout_seconds = 0.05

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        await asyncio.sleep(1)
        return ModuleResult(status=unavailable("never", retryable=False))


class _HealthyTasks:
    name = "tasks"
    timeout_seconds = 1.0

    async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
        return ModuleResult(
            status=ModuleStatus(status="ok", as_of=None, retryable=False, reason=None),
            summary={"pending_tasks": 3, "active_projects": 1},
            today_tasks=[
                TaskBrief(id="t1", title="写周报", due_at=None, project_title=None, source_url=None)
            ],
        )


async def test_failing_or_slow_modules_only_degrade_themselves(home: Home) -> None:
    """DoD：部分 Widget 失败不影响整页——失败 / 超时的模块标为 unavailable，其余照常。"""
    app_of(home.client).state.module_providers = [
        _HealthyTasks(),
        _FailingProvider(),
        _SlowProvider(),
    ]
    data = await home.overview()
    assert data["modules"]["tasks"]["status"] == "ok"
    assert data["summary"]["pending_tasks"] == 3
    assert [t["title"] for t in data["today_tasks"]] == ["写周报"]
    assert data["modules"]["github"] == {
        "status": "unavailable",
        "as_of": None,
        "retryable": True,
        "reason": "error",
    }
    assert data["modules"]["calendar"]["reason"] == "timeout"
    assert "secret upstream detail" not in str(data)
    assert len(data["widgets"]) == 4


async def test_overview_respects_the_total_time_budget(make_client: ClientFactory) -> None:
    class _Hanging:
        name = "tasks"
        timeout_seconds = 30.0

        async def fetch(self, workspace_id: uuid.UUID) -> ModuleResult:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    async with make_client(dashboard_overview_timeout_seconds=0.2) as client:
        setup = (await client.post("/api/v1/setup", json=OWNER)).json()
        app_of(client).state.module_providers = [_Hanging()]
        started = asyncio.get_running_loop().time()
        response = await client.get(
            "/api/v1/dashboard/overview",
            headers={"X-Workspace-Id": setup["workspaces"][0]["id"]},
        )
        elapsed = asyncio.get_running_loop().time() - started
    assert response.status_code == 200
    assert response.json()["modules"]["tasks"]["reason"] == "timeout"
    assert elapsed < 5


async def test_dashboard_writes_are_audited_without_content(home: Home) -> None:
    data = await home.overview()
    dashboard, note = data["dashboard"], data["widgets"][0]
    await home.client.patch(
        f"/api/v1/widgets/{note['id']}",
        json={"widget_type": "note", "config": {"text": "私密内容"}, "version": note["version"]},
        headers=home.headers(write=True),
    )
    created = (
        await home.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/widgets",
            json=widget(),
            headers=home.headers(write=True),
        )
    ).json()
    await home.client.delete(f"/api/v1/widgets/{created['id']}", headers=home.headers(write=True))
    current = (await home.overview())["dashboard"]
    await home.client.patch(
        f"/api/v1/dashboards/{dashboard['id']}",
        json={"version": current["version"], "title": "我的首页"},
        headers=home.headers(write=True),
    )
    logs = await home.client.get("/api/v1/audit-logs", headers=home.headers())
    entries = logs.json()["items"]
    assert [e["action"] for e in entries[:4]] == [
        "dashboard.updated",
        "widget.deleted",
        "widget.created",
        "widget.updated",
    ]
    assert entries[0]["metadata"] == {"fields": ["title"]}
    assert entries[3]["metadata"]["widget_type"] == "note"
    assert "私密内容" not in logs.text


async def test_concurrent_writes_are_serialized(home: Home) -> None:
    data = await home.overview()
    dashboard = data["dashboard"]
    placements = [{"widget_id": w["id"], "layout": w["layout"]} for w in data["widgets"]]
    path = f"/api/v1/dashboards/{dashboard['id']}"
    saves = await asyncio.gather(
        *[
            home.client.patch(
                path,
                json={"version": dashboard["version"], "layout": placements},
                headers=home.headers(write=True),
            )
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in saves) == [200, 409]

    others = [
        (
            await home.client.post(
                "/api/v1/dashboards", json={"title": f"备选 {i}"}, headers=home.headers(write=True)
            )
        ).json()
        for i in range(2)
    ]
    switches = await asyncio.gather(
        *[
            home.client.patch(
                f"/api/v1/dashboards/{d['id']}",
                json={"version": d["version"], "is_default": True},
                headers=home.headers(write=True),
            )
            for d in others
        ]
    )
    assert [r.status_code for r in switches] == [200, 200]
    listed = (await home.client.get("/api/v1/dashboards", headers=home.headers())).json()
    assert sum(d["is_default"] for d in listed["items"]) == 1
