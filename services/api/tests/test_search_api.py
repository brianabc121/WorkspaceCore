"""跨资源搜索（T-0029；Sprint 4 契约第 8 节；A10、A11）。

索引由数据库触发器维护：这里通过接口（收藏、日程）和直接写投影（模拟 Vikunja / GitHub 同步）
产生数据，再验证搜索结果、可见性与删除后的表现。
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

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

AFFINE = "https://affine.example.com"


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    make_client: ClientFactory

    def headers(
        self, *, write: bool = False, client: httpx.AsyncClient | None = None
    ) -> dict[str, str]:
        return {
            "X-Workspace-Id": self.workspace_id,
            **(csrf(client or self.client) if write else {}),
        }

    async def search(
        self, q: str, client: httpx.AsyncClient | None = None, **params: Any
    ) -> list[dict[str, Any]]:
        response = await (client or self.client).get(
            "/api/v1/search", params={"q": q, **params}, headers=self.headers(client=client)
        )
        assert response.status_code == 200, response.text
        items: list[dict[str, Any]] = response.json()["items"]
        return items

    async def post(
        self, path: str, body: dict[str, Any], client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        response = await (client or self.client).post(
            path, json=body, headers=self.headers(write=True, client=client)
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def integration(self, provider: str, workspace_id: str | None = None) -> uuid.UUID:
        value: uuid.UUID = await run_sql(
            self.database_url,
            "INSERT INTO workspace.integrations"
            " (workspace_id, provider, name, base_url, public_url, status)"
            " VALUES ($1, $2, $2, 'https://example.com', 'https://example.com', 'active')"
            " RETURNING id",
            uuid.UUID(workspace_id or self.workspace_id),
            provider,
        )
        return value

    async def resource(
        self,
        integration_id: uuid.UUID,
        resource_type: str,
        title: str,
        metadata: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        url: str = "https://example.com/x",
    ) -> uuid.UUID:
        value: uuid.UUID = await run_sql(
            self.database_url,
            "INSERT INTO workspace.external_resources (workspace_id, integration_id, resource_type,"
            " external_id, title, source_url, metadata_json, source_updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now()) RETURNING id",
            uuid.UUID(workspace_id or self.workspace_id),
            integration_id,
            resource_type,
            str(uuid.uuid4()),
            title,
            url,
            json.dumps(metadata or {}),
        )
        return value


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client() as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url, make_client)


async def test_every_resource_type_is_searchable(env: Env) -> None:
    vikunja = await env.integration("vikunja")
    project = await env.resource(vikunja, "project", "发布计划")
    await env.resource(
        vikunja,
        "task",
        "准备发布说明",
        {
            "project_resource_id": str(project),
            "description": "Deploying the servers",
            "labels": ["发布"],
        },
        url="https://tasks.example.com/tasks/1",
    )
    github = await env.integration("github")
    repo = await env.resource(github, "repo", "octo/app", {"description": "主仓库"})
    await env.resource(
        github,
        "issue",
        "发布失败",
        {"repo_resource_id": str(repo), "repo_full_name": "octo/app", "labels": ["bug"]},
    )
    await env.resource(github, "run", "发布流水线", {"repo_resource_id": str(repo)})  # 运行不进索引
    await env.post(
        "/api/v1/knowledge/sources", {"provider": "affine", "name": "AFFiNE", "base_url": AFFINE}
    )
    await env.post(
        "/api/v1/knowledge/bookmarks",
        {"url": f"{AFFINE}/workspace/ws/doc1", "title": "发布手册", "note": "上线前检查"},
    )
    await env.post(
        "/api/v1/calendar/events",
        {
            "title": "发布评审",
            "timezone": "Asia/Shanghai",
            "start_at": "2026-11-02T10:00:00+08:00",
            "end_at": "2026-11-02T11:00:00+08:00",
        },
    )
    results = await env.search("发布")
    by_type = {r["resource_type"]: r for r in results}
    assert set(by_type) == {"task", "project", "issue", "bookmark", "event"}
    assert by_type["task"]["context"] == "发布计划"  # 任务带上项目名
    assert by_type["task"]["provider"] == "vikunja"
    assert by_type["task"]["source_url"] == "https://tasks.example.com/tasks/1"
    assert by_type["issue"]["context"] == "octo/app" and by_type["issue"]["provider"] == "github"
    assert by_type["bookmark"]["source_url"] == f"{AFFINE}/workspace/ws/doc1"
    assert by_type["bookmark"]["provider"] == "affine"
    assert by_type["event"]["context"] == "2026-11-02" and by_type["event"]["source_url"] is None
    # 英文全文检索带词干：deploy 命中 Deploying（只在描述里，match 为 text）
    [hit] = await env.search("deploy")
    assert hit["title"] == "准备发布说明" and hit["match"] == "text"
    assert [r["resource_type"] for r in await env.search("发布", types=["event", "bookmark"])] in (
        ["bookmark", "event"],
        ["event", "bookmark"],
    )


async def test_ranking_prefers_title_matches(env: Env) -> None:
    vikunja = await env.integration("vikunja")
    await env.resource(vikunja, "task", "写周报的模板", {"description": ""})
    await env.resource(vikunja, "task", "周报", {"description": ""})
    await env.resource(vikunja, "task", "整理文档", {"description": "顺便写周报"})
    await env.resource(vikunja, "task", "周报汇总", {"description": ""})
    titles = [r["title"] for r in await env.search("周报")]
    assert titles == ["周报", "周报汇总", "写周报的模板", "整理文档"]
    assert [r["match"] for r in await env.search("周报")][-1] == "text"
    page = await env.client.get(
        "/api/v1/search", params={"q": "周报", "limit": 2}, headers=env.headers()
    )
    assert len(page.json()["items"]) == 2 and page.json()["next_cursor"]
    rest = await env.client.get(
        "/api/v1/search",
        params={"q": "周报", "limit": 2, "cursor": page.json()["next_cursor"]},
        headers=env.headers(),
    )
    assert [r["title"] for r in rest.json()["items"]] == ["写周报的模板", "整理文档"]
    assert await env.search("100%") == []  # LIKE 通配符按字面处理


async def test_deleted_resources_leave_the_index(env: Env) -> None:
    """A10：源对象删除（tombstone / 删除收藏 / 删除日程）后搜索不再返回。"""
    vikunja = await env.integration("vikunja")
    task = await env.resource(vikunja, "task", "临时任务", {"description": ""})
    await env.post(
        "/api/v1/knowledge/sources", {"provider": "affine", "name": "AFFiNE", "base_url": AFFINE}
    )
    bookmark = await env.post(
        "/api/v1/knowledge/bookmarks", {"url": f"{AFFINE}/share/ws/doc9", "title": "临时文档"}
    )
    event = await env.post(
        "/api/v1/calendar/events",
        {
            "title": "临时会议",
            "timezone": "UTC",
            "all_day": True,
            "start_date": "2026-11-03",
            "end_date": "2026-11-04",
        },
    )
    assert len(await env.search("临时")) == 3
    await run_sql(
        env.database_url,
        "UPDATE workspace.external_resources SET deleted_at = now() WHERE id = $1",
        task,
    )
    gone = await env.client.delete(
        f"/api/v1/knowledge/bookmarks/{bookmark['id']}",
        params={"version": bookmark["version"]},
        headers=env.headers(write=True),
    )
    assert gone.status_code == 204
    removed = await env.client.delete(
        f"/api/v1/calendar/events/{event['id']}",
        params={"version": event["version"]},
        headers=env.headers(write=True),
    )
    assert removed.status_code == 204
    assert await env.search("临时") == []
    left = await run_sql(env.database_url, "SELECT count(*) FROM workspace.search_documents")
    assert left == 0


async def test_visibility_and_workspace_isolation(env: Env) -> None:
    """A11：另一个工作空间的数据、别人的私有收藏都不返回。"""
    await env.post(
        "/api/v1/knowledge/sources", {"provider": "affine", "name": "AFFiNE", "base_url": AFFINE}
    )
    await env.post(
        "/api/v1/knowledge/bookmarks",
        {"url": f"{AFFINE}/workspace/ws/secret", "title": "私人日记", "visibility": "private"},
    )
    await env.post(
        "/api/v1/knowledge/bookmarks", {"url": f"{AFFINE}/workspace/ws/open", "title": "公开日记"}
    )
    bob_id = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), bob_id, "viewer")
    other_ws = await add_workspace(env.database_url, bob_id, "Bob 的空间")
    other_integration = await env.integration("vikunja", str(other_ws))
    await env.resource(
        other_integration, "task", "别人的日记", {"description": ""}, workspace_id=str(other_ws)
    )
    owner_titles = {r["title"] for r in await env.search("日记")}
    assert owner_titles == {"私人日记", "公开日记"}
    async with env.make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        bob_titles = {r["title"] for r in await env.search("日记", client=bob)}
        assert bob_titles == {"公开日记"}  # viewer 能搜，但看不到别人的私有收藏
        own = await bob.get(
            "/api/v1/search", params={"q": "日记"}, headers={"X-Workspace-Id": str(other_ws)}
        )
        assert {r["title"] for r in own.json()["items"]} == {"别人的日记"}
    # 收藏改为私有、创建者被删除后，对任何人都不可见
    await run_sql(
        env.database_url,
        "UPDATE workspace.bookmarks SET owner_id = NULL WHERE visibility = 'private'",
    )
    assert {r["title"] for r in await env.search("日记")} == {"公开日记"}


async def test_source_url_follows_the_knowledge_source(env: Env) -> None:
    source = await env.post(
        "/api/v1/knowledge/sources", {"provider": "affine", "name": "AFFiNE", "base_url": AFFINE}
    )
    await env.post(
        "/api/v1/knowledge/bookmarks", {"url": f"{AFFINE}/workspace/ws/doc1", "title": "架构图"}
    )
    moved = await env.client.patch(
        f"/api/v1/knowledge/sources/{source['id']}",
        json={"version": source["version"], "base_url": "https://docs.example.com"},
        headers=env.headers(write=True),
    )
    assert moved.status_code == 200
    [hit] = await env.search("架构")
    assert hit["source_url"] == "https://docs.example.com/workspace/ws/doc1"


async def test_index_can_be_rebuilt(env: Env) -> None:
    vikunja = await env.integration("vikunja")
    await env.resource(vikunja, "task", "重建测试", {"description": ""})
    await run_sql(env.database_url, "DELETE FROM workspace.search_documents")
    assert await env.search("重建") == []
    count = await run_sql(env.database_url, "SELECT workspace.search_rebuild()")
    assert count == 1
    assert [r["title"] for r in await env.search("重建")] == ["重建测试"]
