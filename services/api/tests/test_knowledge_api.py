"""知识入口：AFFiNE 外部地址与文档收藏（Sprint 4 契约第 3、4 节；A10、A11、A12）。"""

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
DOC = f"{AFFINE}/workspace/0f1e2d3c-aaaa-bbbb-cccc-000000000001/AbCdEf12"


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    make_client: ClientFactory

    def headers(
        self, *, write: bool = False, client: httpx.AsyncClient | None = None
    ) -> dict[str, str]:
        csrf_headers = csrf(client or self.client) if write else {}
        return {"X-Workspace-Id": self.workspace_id, **csrf_headers}

    async def source(self, base_url: str = AFFINE) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/knowledge/sources",
            json={"provider": "affine", "name": "AFFiNE", "base_url": base_url},
            headers=self.headers(write=True),
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def bookmark(
        self, client: httpx.AsyncClient | None = None, **body: Any
    ) -> httpx.Response:
        payload = {"url": DOC, "title": "设计稿", **body}
        return await (client or self.client).post(
            "/api/v1/knowledge/bookmarks",
            json=payload,
            headers=self.headers(write=True, client=client),
        )

    async def resources(
        self, client: httpx.AsyncClient | None = None, **params: Any
    ) -> dict[str, Any]:
        response = await (client or self.client).get(
            "/api/v1/knowledge/resources", params=params, headers=self.headers(client=client)
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def task(self, title: str) -> str:
        """直接写一条 Vikunja 任务投影（关联的目标）。"""
        integration_id = await run_sql(
            self.database_url,
            "INSERT INTO workspace.integrations"
            " (workspace_id, provider, name, base_url, public_url)"
            " VALUES ($1, 'vikunja', $2, 'http://vikunja:3456', 'https://tasks.example.com')"
            " ON CONFLICT (workspace_id, provider, name) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id",
            uuid.UUID(self.workspace_id),
            "Vikunja",
        )
        resource_id = await run_sql(
            self.database_url,
            "INSERT INTO workspace.external_resources"
            " (workspace_id, integration_id, resource_type, external_id, title, source_url)"
            " VALUES ($1, $2, 'task', $3, $4, 'https://tasks.example.com/tasks/1') RETURNING id",
            uuid.UUID(self.workspace_id),
            integration_id,
            str(uuid.uuid4()),
            title,
        )
        return str(resource_id)


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client() as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url, make_client)


async def _login_member(env: Env, role: str) -> uuid.UUID:
    user_id = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user_id, role)
    return user_id


# ---------------------------------------------------------------- 知识来源


async def test_source_is_configured_once_and_never_fetched(env: Env) -> None:
    empty = await env.client.get("/api/v1/knowledge/sources", headers=env.headers())
    assert empty.json()["items"] == []
    source = await env.source(f"{AFFINE}/")
    assert source["base_url"] == AFFINE
    assert source["tested_version"] == "0.27.4"
    assert source["bookmark_count"] == 0
    second = await env.client.post(
        "/api/v1/knowledge/sources",
        json={"provider": "affine", "name": "另一个", "base_url": "https://docs.example.com"},
        headers=env.headers(write=True),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "LIMIT_EXCEEDED"
    # 知识来源不出现在集成列表里（契约第 3 节）
    integrations = await env.client.get("/api/v1/integrations", headers=env.headers())
    assert integrations.json()["items"] == []


async def test_metadata_address_is_rejected(env: Env) -> None:
    """A12：云元数据地址不能作为 AFFiNE 地址。"""
    response = await env.client.post(
        "/api/v1/knowledge/sources",
        json={"provider": "affine", "name": "AFFiNE", "base_url": "http://169.254.169.254"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 422
    assert "169.254.169.254" not in response.text


async def test_only_admins_manage_sources(env: Env) -> None:
    await _login_member(env, "member")
    async with env.make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        response = await bob.post(
            "/api/v1/knowledge/sources",
            json={"provider": "affine", "name": "AFFiNE", "base_url": AFFINE},
            headers=env.headers(write=True, client=bob),
        )
        assert response.status_code == 403
        listed = await bob.get("/api/v1/knowledge/sources", headers=env.headers(client=bob))
        assert listed.status_code == 200


async def test_changing_the_address_moves_every_link(env: Env) -> None:
    source = await env.source()
    created = await env.bookmark(url=f"{DOC}?mode=page")
    assert created.status_code == 201, created.text
    moved = await env.client.patch(
        f"/api/v1/knowledge/sources/{source['id']}",
        json={"version": source["version"], "base_url": "https://docs.example.com/affine"},
        headers=env.headers(write=True),
    )
    assert moved.status_code == 200
    assert moved.json()["bookmark_count"] == 1
    [item] = (await env.resources())["items"]
    assert item["url"] == (
        "https://docs.example.com/affine/workspace/0f1e2d3c-aaaa-bbbb-cccc-000000000001"
        "/AbCdEf12?mode=page"
    )
    stale = await env.client.patch(
        f"/api/v1/knowledge/sources/{source['id']}",
        json={"version": source["version"], "name": "旧版本"},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


async def test_deleting_the_source_deletes_its_bookmarks(env: Env) -> None:
    source = await env.source()
    assert (await env.bookmark()).status_code == 201
    assert (await env.bookmark(visibility="private")).status_code == 201
    deleted = await env.client.delete(
        f"/api/v1/knowledge/sources/{source['id']}",
        params={"version": source["version"]},
        headers=env.headers(write=True),
    )
    assert deleted.status_code == 204
    assert (await env.resources())["items"] == []
    left = await run_sql(env.database_url, "SELECT count(*) FROM workspace.bookmarks")
    assert left == 0
    audit = await run_sql(
        env.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs"
        " WHERE action = 'knowledge_source.deleted'",
    )
    assert '"bookmarks": 2' in audit


# ---------------------------------------------------------------- 收藏


async def test_bookmark_requires_a_source(env: Env) -> None:
    response = await env.bookmark()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INTEGRATION_UNAVAILABLE"
    assert response.json()["error"]["details"]["reason"] == "not_configured"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("https://evil.example.com/workspace/ws/doc", "host_mismatch"),
        ("http://169.254.169.254/workspace/ws/doc", "scheme_not_allowed"),
        (f"{AFFINE}/workspace/ws/all", "not_a_document"),
        (f"{AFFINE}/workspace/ws/doc%2F..", "invalid_id"),
        ("https://user:pw@affine.example.com/workspace/ws/doc", "credentials_not_allowed"),
    ],
)
async def test_invalid_links_explain_why(env: Env, url: str, reason: str) -> None:
    await env.source()
    response = await env.bookmark(url=url)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["details"]["reason"] == reason
    assert error["details"]["errors"][0]["loc"] == ["body", "url"]
    assert url not in response.text


async def test_bookmark_crud_and_normalized_url(env: Env) -> None:
    await env.source()
    created = await env.bookmark(
        url=f"{DOC}?refreshKey=1&mode=edgeless#top", note="第二版", title="架构图"
    )
    assert created.status_code == 201
    bookmark = created.json()
    assert bookmark["url"] == f"{DOC}?mode=edgeless"
    assert bookmark["link_kind"] == "workspace_doc"
    assert bookmark["doc_id"] == "AbCdEf12"
    assert bookmark["visibility"] == "workspace"
    path = f"/api/v1/knowledge/bookmarks/{bookmark['id']}"
    updated = await env.client.patch(
        path,
        json={"version": bookmark["version"], "title": "架构图 v2"},
        headers=env.headers(write=True),
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == bookmark["version"] + 1
    found = await env.resources(q="v2")
    assert [b["title"] for b in found["items"]] == ["架构图 v2"]
    assert (await env.resources(q="不存在"))["items"] == []
    stale = await env.client.delete(
        path, params={"version": bookmark["version"]}, headers=env.headers(write=True)
    )
    assert stale.status_code == 409
    gone = await env.client.delete(
        path, params={"version": updated.json()["version"]}, headers=env.headers(write=True)
    )
    assert gone.status_code == 204
    audit = await run_sql(
        env.database_url,
        "SELECT string_agg(metadata_redacted_json::text, ' ') FROM workspace.audit_logs"
        " WHERE action LIKE 'bookmark.%'",
    )
    assert "架构图" not in audit and "affine.example.com" not in audit and "第二版" not in audit


async def test_duplicates_follow_visibility(env: Env) -> None:
    await env.source()
    first = await env.bookmark()
    again = await env.bookmark(url=f"{DOC}?mode=page")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "BOOKMARK_EXISTS"
    assert again.json()["error"]["details"]["existing_id"] == first.json()["id"]
    # 同一文档还可以有一条自己的私有收藏
    mine = await env.bookmark(visibility="private")
    assert mine.status_code == 201
    assert (await env.bookmark(visibility="private")).status_code == 409


async def test_private_bookmarks_are_invisible_to_others(env: Env) -> None:
    """A11 / S4-01：私有收藏只有创建者能看到，别人访问与不存在一样。"""
    await env.source()
    await _login_member(env, "admin")
    private = (await env.bookmark(visibility="private", title="我的笔记")).json()
    async with env.make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        assert (await env.resources(client=bob))["items"] == []
        assert (await env.resources(client=bob, visibility="private"))["items"] == []
        path = f"/api/v1/knowledge/bookmarks/{private['id']}"
        for response in [
            await bob.patch(
                path,
                json={"version": private["version"], "title": "偷看"},
                headers=env.headers(write=True, client=bob),
            ),
            await bob.delete(
                path,
                params={"version": private["version"]},
                headers=env.headers(write=True, client=bob),
            ),
        ]:
            assert response.status_code == 404
        # 别人的私有收藏不影响去重，也就不会暴露它的存在
        theirs = await env.bookmark(client=bob, visibility="private")
        assert theirs.status_code == 201
        shared = await env.bookmark(client=bob)
        assert shared.status_code == 201
        # 自己已经有这篇文档的私有收藏：共享的那条不能再改成私有
        hide = await bob.patch(
            f"/api/v1/knowledge/bookmarks/{shared.json()['id']}",
            json={"version": 1, "visibility": "private"},
            headers=env.headers(write=True, client=bob),
        )
        assert hide.status_code == 409
        assert hide.json()["error"]["details"]["existing_id"] == theirs.json()["id"]
    owner_view = await env.resources()
    assert {b["title"] for b in owner_view["items"]} == {"我的笔记", "设计稿"}
    # 自己的私有收藏可以公开，但 Bob 已经共享了同一篇文档
    publish = await env.client.patch(
        f"/api/v1/knowledge/bookmarks/{private['id']}",
        json={"version": private["version"], "visibility": "workspace"},
        headers=env.headers(write=True),
    )
    assert publish.status_code == 409
    assert publish.json()["error"]["code"] == "BOOKMARK_EXISTS"


async def test_shared_bookmark_visibility_is_owned_by_its_creator(env: Env) -> None:
    await env.source()
    await _login_member(env, "member")
    shared = (await env.bookmark()).json()
    async with env.make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        path = f"/api/v1/knowledge/bookmarks/{shared['id']}"
        hide = await bob.patch(
            path,
            json={"version": shared["version"], "visibility": "private"},
            headers=env.headers(write=True, client=bob),
        )
        assert hide.status_code == 403
        rename = await bob.patch(
            path,
            json={"version": shared["version"], "title": "改个名"},
            headers=env.headers(write=True, client=bob),
        )
        assert rename.status_code == 200


async def test_viewers_and_other_workspaces_cannot_write(env: Env) -> None:
    await env.source()
    await _login_member(env, "viewer")
    shared = (await env.bookmark()).json()
    async with env.make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        assert (await env.bookmark(client=bob)).status_code == 403
        assert len((await env.resources(client=bob))["items"]) == 1
    # 另一个工作空间的成员：看不到，也改不了
    outsider_id = await add_user(env.database_url, "eve@example.com", "eve password 123456")
    other_ws = await add_workspace(env.database_url, outsider_id, "别人的空间")
    async with env.make_client() as eve:
        login = await eve.post(
            "/api/v1/auth/login",
            json={"email": "eve@example.com", "password": "eve password 123456"},
        )
        assert login.status_code == 200
        foreign = {"X-Workspace-Id": env.workspace_id, **csrf(eve)}
        assert (await eve.get("/api/v1/knowledge/resources", headers=foreign)).status_code == 404
        own = {"X-Workspace-Id": str(other_ws), **csrf(eve)}
        patched = await eve.patch(
            f"/api/v1/knowledge/bookmarks/{shared['id']}",
            json={"version": shared["version"], "title": "x"},
            headers=own,
        )
        assert patched.status_code == 404


async def test_links_to_tasks_disappear_with_the_task(env: Env) -> None:
    """A10：被关联的任务在 Vikunja 里删除（tombstone）后，收藏里不留僵尸引用。"""
    await env.source()
    task_id = await env.task("准备发布")
    other_id = await env.task("写周报")
    created = await env.bookmark(linked_resource_ids=[task_id, other_id])
    assert created.status_code == 201
    assert [link["title"] for link in created.json()["links"]] == ["准备发布", "写周报"]
    linked = await env.resources(linked_resource_id=task_id)
    assert [b["id"] for b in linked["items"]] == [created.json()["id"]]
    missing = await env.bookmark(
        url=f"{AFFINE}/share/ws/doc2", linked_resource_ids=[str(uuid.uuid4())]
    )
    assert missing.status_code == 422
    await run_sql(
        env.database_url,
        "UPDATE workspace.external_resources SET deleted_at = now() WHERE id = $1",
        uuid.UUID(task_id),
    )
    [item] = (await env.resources())["items"]
    assert [link["title"] for link in item["links"]] == ["写周报"]
    assert (await env.resources(linked_resource_id=task_id))["items"] == []
    tombstoned = await env.bookmark(url=f"{AFFINE}/share/ws/doc3", linked_resource_ids=[task_id])
    assert tombstoned.status_code == 422
    replaced = await env.client.patch(
        f"/api/v1/knowledge/bookmarks/{item['id']}",
        json={"version": item["version"], "linked_resource_ids": []},
        headers=env.headers(write=True),
    )
    assert replaced.json()["links"] == []


async def test_pagination_and_sorting(env: Env) -> None:
    await env.source()
    for i in range(3):
        response = await env.bookmark(url=f"{AFFINE}/workspace/ws/doc{i}", title=f"文档 {i}")
        assert response.status_code == 201
    first = await env.resources(limit=2, sort="title")
    assert [b["title"] for b in first["items"]] == ["文档 0", "文档 1"]
    second = await env.resources(limit=2, sort="title", cursor=first["next_cursor"])
    assert [b["title"] for b in second["items"]] == ["文档 2"]
    assert second["next_cursor"] is None
    bad = await env.client.get(
        "/api/v1/knowledge/resources", params={"cursor": "%%%"}, headers=env.headers()
    )
    assert bad.status_code == 422


async def test_overview_reports_the_knowledge_module(env: Env) -> None:
    before = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert before.json()["modules"]["knowledge"]["reason"] == "not_configured"
    await env.source()
    after = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert after.json()["modules"]["knowledge"]["status"] == "ok"
    assert after.json()["modules"]["calendar"]["status"] == "ok"
