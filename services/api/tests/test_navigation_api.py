"""侧栏布局（T-0161）：自定义分组、页面挪分组、零代码页面。"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql

EMBED = "https://grafana.example.com"


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str

    def headers(self, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def get(self) -> dict[str, Any]:
        response = await self.client.get("/api/v1/navigation", headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def put(self, body: dict[str, Any], expect: int = 200) -> dict[str, Any]:
        response = await self.client.put(
            "/api/v1/navigation", json=body, headers=self.headers(write=True)
        )
        assert response.status_code == expect, response.text
        data: dict[str, Any] = response.json()
        return data


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(embed_origins=f"{EMBED} https://bad.example.com/path") as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


def _page(**fields: Any) -> dict[str, Any]:
    return {"id": "x", "title": "x", "section": "apps", **fields}


def layout(**overrides: Any) -> dict[str, Any]:
    return {
        "version": 0,
        "sections": [{"id": "research", "title": "研究"}],
        "pages": [
            {
                "id": "docs",
                "title": "官方文档",
                "section": "custom-research",
                "kind": "link",
                "url": "https://docs.example.com/guide",
            },
            {
                "id": "grafana",
                "title": "监控",
                "section": "tools",
                "kind": "embed",
                "url": f"{EMBED}/d/abc",
                "min_role": "admin",
            },
            {
                "id": "tasks",
                "title": "我的任务",
                "section": "apps",
                "kind": "page",
                "path": "/tasks",
            },
        ],
        "placements": {"/news": "custom-research"},
        "section_order": ["workspace", "custom-research", "apps", "tools", "admin"],
        **overrides,
    }


async def test_empty_layout_by_default(env: Env) -> None:
    data = await env.get()
    assert data["version"] == 0 and data["sections"] == [] and data["pages"] == []
    assert data["embed_origins"] == [EMBED]  # 带路径的来源被忽略
    assert data["can_manage"] is True


async def test_owner_saves_and_roles_filter_pages(env: Env) -> None:
    saved = await env.put(layout())
    assert saved["version"] == 1
    assert [p["id"] for p in saved["pages"]] == ["docs", "grafana", "tasks"]
    assert saved["placements"] == {"/news": "custom-research"}
    audit = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'navigation.updated'",
    )
    assert audit == 1

    # 版本冲突
    stale = await env.put(layout(), expect=409)
    assert stale["error"]["code"] == "VERSION_CONFLICT"

    # member 看不到 min_role=admin 的页面，也不能改
    workspace = uuid.UUID(env.workspace_id)
    member = await add_user(env.database_url, "member@example.com", "member password 1234")
    await add_member(env.database_url, workspace, member, "member")
    login = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "member password 1234"},
    )
    assert login.status_code == 200
    seen = await env.get()
    assert [p["id"] for p in seen["pages"]] == ["docs", "tasks"]
    assert seen["can_manage"] is False
    await env.put(layout(version=1), expect=403)


@pytest.mark.parametrize(
    ("page", "message"),
    [
        ({"kind": "link", "url": "javascript:alert(1)"}, "http(s)"),
        ({"kind": "link", "url": "https://user:pw@example.com"}, "账号或密码"),
        ({"kind": "embed", "url": "https://evil.example.com"}, "EMBED_ORIGINS"),
        ({"kind": "embed", "url": "http://testserver/tasks"}, "工作台自己"),
        ({"kind": "note", "target_id": str(uuid.uuid4())}, "笔记不存在"),
        ({"kind": "dashboard", "target_id": str(uuid.uuid4())}, "仪表盘不存在"),
    ],
)
async def test_page_targets_are_checked(env: Env, page: dict[str, Any], message: str) -> None:
    body = layout(pages=[{"id": "x", "title": "x", "section": "apps", **page}])
    error = await env.put(body, expect=422)
    assert message in str(error["error"]["details"])


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "pages": [
                {
                    "id": "x",
                    "title": "x",
                    "section": "custom-nope",
                    "kind": "link",
                    "url": "https://a.example.com",
                }
            ]
        },
        {
            "pages": [
                {
                    "id": "x",
                    "title": "x",
                    "section": "apps",
                    "kind": "page",
                    "path": "//evil.example.com",
                }
            ]
        },
        {"pages": [{"id": "x", "title": "x", "section": "apps", "kind": "link", "path": "/tasks"}]},
        {"sections": [{"id": "a", "title": "一"}, {"id": "a", "title": "二"}]},
        {"placements": {"https://evil.example.com": "apps"}},
        {"section_order": ["custom-ghost"]},
        {"sections": [{"id": "Bad Id", "title": "x"}]},
        {"extra": True},
    ],
)
async def test_layout_shape_is_validated(env: Env, overrides: dict[str, Any]) -> None:
    await env.put(layout(**overrides), expect=422)


async def test_dashboard_page_must_belong_to_the_workspace(env: Env) -> None:
    dashboards = await env.client.get("/api/v1/dashboards", headers=env.headers())
    dashboard_id = dashboards.json()["items"][0]["id"]
    page = {"id": "board", "title": "看板", "section": "apps", "kind": "dashboard"}
    saved = await env.put(layout(pages=[{**page, "target_id": dashboard_id}]))
    assert saved["pages"][0]["target_id"] == dashboard_id
