"""Sprint 2 契约阶段的接口行为（T-0014）：真实 PostgreSQL（已迁移）+ Redis。

处理函数在 T-0015 实现之前返回 501；这里先固定实现之前就必须成立的部分：
未登录 401、非成员 404、viewer 写入 403（A03）、CSRF，
以及请求体校验（未知 widget type、布局越界、危险链接）。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf

pytestmark = pytest.mark.integration

LAYOUT = {"desktop": {"x": 0, "y": 0, "w": 4, "h": 3}, "mobile": {"y": 0, "h": 3}}
VIEWER = {"email": "viewer@example.com", "password": "viewer password 123"}


@pytest.fixture
async def owner(make_client: ClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as client:
        assert (await client.post("/api/v1/setup", json=OWNER)).status_code == 201
        yield client


async def workspace_of(client: httpx.AsyncClient) -> str:
    workspace_id: str = (await client.get("/api/v1/workspaces")).json()["items"][0]["id"]
    return workspace_id


def widget(**overrides: Any) -> dict[str, Any]:
    return {"widget_type": "note", "config": {"text": "你好"}, "layout": LAYOUT, **overrides}


async def test_dashboard_endpoints_require_a_session(make_client: ClientFactory) -> None:
    async with make_client() as anonymous:
        headers = {"X-Workspace-Id": str(uuid.uuid4())}
        for path in ("/api/v1/dashboard/overview", "/api/v1/dashboards"):
            response = await anonymous.get(path, headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_non_members_get_404(owner: httpx.AsyncClient) -> None:
    response = await owner.get(
        "/api/v1/dashboard/overview", headers={"X-Workspace-Id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_member_requests_reach_the_unimplemented_handlers(owner: httpx.AsyncClient) -> None:
    headers = {"X-Workspace-Id": await workspace_of(owner), **csrf(owner)}
    dashboard = str(uuid.uuid4())
    calls = [
        owner.get("/api/v1/dashboard/overview", headers=headers),
        owner.get("/api/v1/dashboards", headers=headers),
        owner.post("/api/v1/dashboards", json={"title": "工作"}, headers=headers),
        owner.patch(f"/api/v1/dashboards/{dashboard}", json={"version": 1}, headers=headers),
        owner.get(f"/api/v1/dashboards/{dashboard}/widgets", headers=headers),
        owner.post(f"/api/v1/dashboards/{dashboard}/widgets", json=widget(), headers=headers),
        owner.patch(
            f"/api/v1/widgets/{uuid.uuid4()}",
            json={"widget_type": "note", "config": {"text": "改"}, "version": 1},
            headers=headers,
        ),
        owner.delete(f"/api/v1/widgets/{uuid.uuid4()}", headers=headers),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 501, response.text
        assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"


async def test_viewer_cannot_write(
    owner: httpx.AsyncClient, make_client: ClientFactory, migrated_database_url: str
) -> None:
    """A03 的基础：viewer 的写请求在进入处理函数之前就被拒绝。"""
    workspace = await workspace_of(owner)
    viewer_id = await add_user(migrated_database_url, VIEWER["email"], VIEWER["password"])
    await add_member(migrated_database_url, uuid.UUID(workspace), viewer_id, "viewer")
    async with make_client() as viewer:
        assert (await viewer.post("/api/v1/auth/login", json=VIEWER)).status_code == 200
        headers = {"X-Workspace-Id": workspace, **csrf(viewer)}
        read = await viewer.get("/api/v1/dashboards", headers=headers)
        assert read.status_code == 501  # viewer 可以读（处理函数尚未实现）
        writes = [
            viewer.post("/api/v1/dashboards", json={"title": "x"}, headers=headers),
            viewer.patch(
                f"/api/v1/widgets/{uuid.uuid4()}",
                json={"widget_type": "note", "config": {}, "version": 1},
                headers=headers,
            ),
            viewer.delete(f"/api/v1/widgets/{uuid.uuid4()}", headers=headers),
        ]
        for call in writes:
            response = await call
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_writes_require_csrf(owner: httpx.AsyncClient) -> None:
    headers = {"X-Workspace-Id": await workspace_of(owner)}
    response = await owner.post("/api/v1/dashboards", json={"title": "x"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


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
    ],
    ids=["unknown-type", "extra-field", "outside-grid", "missing-mobile", "js-url", "bad-limit"],
)
async def test_invalid_widgets_are_rejected(owner: httpx.AsyncClient, body: dict[str, Any]) -> None:
    headers = {"X-Workspace-Id": await workspace_of(owner), **csrf(owner)}
    response = await owner.post(
        f"/api/v1/dashboards/{uuid.uuid4()}/widgets", json=body, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "javascript" not in response.text  # 422 不回显输入
