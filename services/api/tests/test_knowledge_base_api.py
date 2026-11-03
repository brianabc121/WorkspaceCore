"""知识库的人用接口（T-0098，docs/v2/KNOWLEDGE_PLAN.md 第 3、5 节）：真实 PostgreSQL / Redis。

覆盖：三类知识库的自动创建、笔记的新建 / 修改 / 乐观并发 / 删除 / 恢复 / 彻底删除、版本与差异、
撤销到某一版、链接与反向链接（含后建笔记补上链接、别名）、密钥拦截、权限（viewer / member / admin、
其他工作空间）、全局搜索、图谱、变更记录、参与项目的智能体。
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.helpers import add_user, add_workspace, csrf
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def spaces(env: Env, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    response = await (client or env.client).get("/api/v1/knowledge/spaces", headers=env.headers())
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def public_space(env: Env) -> dict[str, Any]:
    return next(s for s in await spaces(env) if s["kind"] == "public")


async def new_note(
    env: Env, space_id: str, title: str, body: str = "", **fields: Any
) -> httpx.Response:
    return await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": space_id, "title": title, "body": body, **fields},
        headers=env.headers(write=True),
    )


async def note(env: Env, title: str, body: str = "", **fields: Any) -> dict[str, Any]:
    space = await public_space(env)
    response = await new_note(env, space["id"], title, body, **fields)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def patch(env: Env, note_id: str, **fields: Any) -> httpx.Response:
    return await env.client.patch(
        f"/api/v1/knowledge/notes/{note_id}", json=fields, headers=env.headers(write=True)
    )


async def test_spaces_are_created_for_workspace_projects_and_agents(env: Env) -> None:
    initial = await spaces(env)
    assert [s["kind"] for s in initial] == ["public"]
    assert initial[0]["agent_access"] == "read_write" and initial[0]["notes"] == 0
    project = await env.add_project("工作台 V2")
    created = await env.client.post(
        "/api/v1/agents/instances",
        json={
            "name": "ops-claw",
            "network_mode": "intranet",
            "scope": "read_write",
            "max_role": "member",
        },
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    listed = {s["kind"]: s for s in await spaces(env)}
    assert (
        listed["project"]["project_id"] == project["id"]
        and listed["project"]["name"] == "工作台 V2"
    )
    assert listed["agent"]["instance_id"] == created.json()["id"]
    assert listed["agent"]["agent_access"] == "read" and listed["agent"]["writes_enabled"] is True
    # 项目改名：知识库名跟着改
    renamed = await env.client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": project["version"], "title": "工作台 V3"},
        headers=env.headers(write=True),
    )
    assert renamed.status_code == 200, renamed.text
    assert {s["kind"]: s for s in await spaces(env)}["project"]["name"] == "工作台 V3"


async def test_note_lifecycle_with_versions_and_conflicts(env: Env) -> None:
    created = await note(
        env, "发布流程", "## 步骤\n1. 备份\n", tags=["发布", "发布"], folder="/流程/"
    )
    assert created["version"] == 1 and created["tags"] == ["发布"] and created["folder"] == "流程"
    assert created["created_by"]["kind"] == "user" and created["source_kind"] == "human"
    space = await public_space(env)
    assert (await new_note(env, space["id"], "发布流程")).json()["error"]["code"] == "TITLE_TAKEN"

    updated = await patch(
        env, created["id"], version=1, body="## 步骤\n1. 备份\n2. 发布\n", reason="补一步"
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2 and updated.json()["reason"] == "补一步"
    stale = await patch(env, created["id"], version=1, body="旧的")
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "VERSION_CONFLICT"
    assert (
        error["details"]["current_version"] == 2
        and "2. 发布" in error["details"]["current"]["body"]
    )

    revisions = await env.get(f"/api/v1/knowledge/notes/{created['id']}/revisions")
    assert [(r["version"], r["change"], r["added"]) for r in revisions["items"]] == [
        (2, "update", 1),
        (1, "create", 2),
    ]
    detail = await env.get(f"/api/v1/knowledge/notes/{created['id']}/revisions/2")
    assert {"sign": "+", "text": "2. 发布"} in detail["diff"]

    reverted = await env.client.post(
        f"/api/v1/knowledge/notes/{created['id']}/revert",
        json={"version": 2, "to_version": 1, "reason": "那一步还没定"},
        headers=env.headers(write=True),
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["version"] == 3 and "2. 发布" not in reverted.json()["body"]

    deleted = await env.client.delete(
        f"/api/v1/knowledge/notes/{created['id']}",
        params={"version": 3},
        headers=env.headers(write=True),
    )
    assert deleted.status_code == 204
    trash = await env.get("/api/v1/knowledge/trash")
    assert [t["title"] for t in trash["items"]] == ["发布流程"]
    assert (await env.get("/api/v1/knowledge/notes"))["items"] == []
    # 回收站里的笔记不能直接改
    assert (await patch(env, created["id"], version=4, body="x")).json()["error"][
        "code"
    ] == "NOTE_DELETED"
    restored = await env.client.post(
        f"/api/v1/knowledge/notes/{created['id']}/restore", headers=env.headers(write=True)
    )
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None
    changes = await env.get("/api/v1/knowledge/changes")
    assert [c["change"] for c in changes["items"]] == [
        "restore",
        "delete",
        "revert",
        "update",
        "create",
    ]
    # 恢复的上一版是删除：不提供撤销；更早的改动已经不是最新版本
    assert [c["revertable"] for c in changes["items"]] == [False] * 5
    assert changes["items"][0]["author"]["kind"] == "user"


async def test_links_backlinks_and_late_targets(env: Env) -> None:
    source = await note(
        env, "发布前检查清单", "先看 [[发布流程]]，再看 [[迁移约定|迁移]] 和 [[不存在的笔记]]。"
    )
    links = {link["text"]: link for link in source["links"]}
    assert links["发布流程"]["note_id"] is None  # 还没建
    target = await note(env, "发布流程", "说明")
    # 后建的笔记补上了指向它的链接，并出现在反向链接里
    refreshed = await env.get(f"/api/v1/knowledge/notes/{source['id']}")
    assert {link["text"]: link["note_id"] for link in refreshed["links"]}["发布流程"] == target[
        "id"
    ]
    got = await env.get(f"/api/v1/knowledge/notes/{target['id']}")
    assert [b["title"] for b in got["backlinks"]] == ["发布前检查清单"]
    assert "[[发布流程]]" in got["backlinks"][0]["snippet"]
    # 别名也能被链接
    aliased = await note(env, "数据库迁移约定", "规则", aliases=["迁移约定"])
    refreshed = await env.get(f"/api/v1/knowledge/notes/{source['id']}")
    assert {link["text"]: link["note_id"] for link in refreshed["links"]}["迁移约定"] == aliased[
        "id"
    ]
    graph = await env.get("/api/v1/knowledge/graph")
    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert (source["id"], target["id"]) in edges or (target["id"], source["id"]) in edges
    assert graph["total_notes"] == 3 and graph["truncated"] is False
    local = await env.get(f"/api/v1/knowledge/notes/{target['id']}/graph")
    assert {n["title"] for n in local["nodes"]} == {"发布流程", "发布前检查清单"}
    suggestions = await env.get("/api/v1/knowledge/notes/suggest", q="发布")
    assert [s["title"] for s in suggestions["items"]][:2] == ["发布流程", "发布前检查清单"]


async def test_resource_links_point_to_tasks_and_projects(env: Env) -> None:
    project = await env.add_project("工作台 V2")
    task = await env.add_task(project["id"], "发布 11 月版本")
    linked = await note(
        env, "发布记录", f"任务 [[task:{task['id']}]]，项目 [[project:{project['id']}]]"
    )
    titles = {link["resource_type"]: link["title"] for link in linked["links"]}
    assert titles == {"task": "发布 11 月版本", "project": "工作台 V2"}
    graph = await env.get("/api/v1/knowledge/graph")
    assert {n["kind"] for n in graph["nodes"]} == {"note", "task", "project"}


async def test_secrets_are_rejected_and_never_stored(env: Env) -> None:
    space = await public_space(env)
    rejected = await new_note(env, space["id"], "GitHub 同步", "令牌：ghp_" + "a" * 36)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "SECRET_DETECTED"
    assert rejected.json()["error"]["details"]["kinds"] == ["GitHub 令牌", "密码或密钥的明文"]
    assert (await env.get("/api/v1/knowledge/notes"))["items"] == []


async def test_global_search_finds_notes_until_they_are_deleted(env: Env) -> None:
    created = await note(env, "值班制度", "每周轮换，夜间告警由值班的人处理")
    found = await env.get("/api/v1/search", q="夜间告警")
    assert [(i["resource_type"], i["title"], i["context"]) for i in found["items"]] == [
        ("note", "值班制度", "公共知识库")
    ]
    deleted = await env.client.delete(
        f"/api/v1/knowledge/notes/{created['id']}",
        params={"version": 1},
        headers=env.headers(write=True),
    )
    assert deleted.status_code == 204
    assert (await env.get("/api/v1/search", q="夜间告警"))["items"] == []


async def test_roles_and_workspace_isolation(env: Env) -> None:
    created = await note(env, "安全红线", "不开 exec")
    async with env.member("viewer@example.com", "viewer") as viewer:
        headers = {"X-Workspace-Id": env.workspace_id, **csrf(viewer)}
        read = await viewer.get(f"/api/v1/knowledge/notes/{created['id']}", headers=headers)
        assert read.status_code == 200
        space = (await viewer.get("/api/v1/knowledge/spaces", headers=headers)).json()["items"][0]
        write = await viewer.post(
            "/api/v1/knowledge/notes",
            json={"space_id": space["id"], "title": "x"},
            headers=headers,
        )
        assert write.status_code == 403
    async with env.member("member@example.com", "member") as member:
        headers = {"X-Workspace-Id": env.workspace_id, **csrf(member)}
        deleted = await member.delete(
            f"/api/v1/knowledge/notes/{created['id']}", params={"version": 1}, headers=headers
        )
        assert deleted.status_code == 204
        # 彻底删除只有 owner / admin
        purge = await member.delete(f"/api/v1/knowledge/trash/{created['id']}", headers=headers)
        assert purge.status_code == 403
        settings = await member.patch(
            f"/api/v1/knowledge/spaces/{space['id']}",
            json={"version": space["version"], "agent_access": "read"},
            headers=headers,
        )
        assert settings.status_code == 403
    purged = await env.client.delete(
        f"/api/v1/knowledge/trash/{created['id']}", headers=env.headers(write=True)
    )
    assert purged.status_code == 204
    assert (
        await env.client.get(f"/api/v1/knowledge/notes/{created['id']}", headers=env.headers())
    ).status_code == 404

    other = await note(env, "只在这个空间", "内容")
    outsider = await add_user(env.database_url, "eve@example.com", "eve password 123456")
    other_ws = await add_workspace(env.database_url, outsider, "别人的空间")
    async with env.make_client() as eve:
        login = await eve.post(
            "/api/v1/auth/login",
            json={"email": "eve@example.com", "password": "eve password 123456"},
        )
        assert login.status_code == 200
        own = {"X-Workspace-Id": str(other_ws), **csrf(eve)}
        assert (
            await eve.get(f"/api/v1/knowledge/notes/{other['id']}", headers=own)
        ).status_code == 404
        foreign = {"X-Workspace-Id": env.workspace_id, **csrf(eve)}
        assert (await eve.get("/api/v1/knowledge/spaces", headers=foreign)).status_code == 404


async def test_space_settings_and_project_participants(env: Env) -> None:
    await env.add_project("OpenClaw 接入")
    instance = await env.client.post(
        "/api/v1/agents/instances",
        json={
            "name": "monitor-claw",
            "network_mode": "intranet",
            "scope": "read_write",
            "max_role": "member",
        },
        headers=env.headers(write=True),
    )
    assert instance.status_code == 201
    instance_id = instance.json()["id"]
    listed = {s["kind"]: s for s in await spaces(env)}
    project_space, public, agent = listed["project"], listed["public"], listed["agent"]
    added = await env.client.put(
        f"/api/v1/knowledge/spaces/{project_space['id']}/agents/{instance_id}",
        json={"access": "read"},
        headers=env.headers(write=True),
    )
    assert added.status_code == 200, added.text
    assert [(a["name"], a["access"]) for a in added.json()["agents"]] == [("monitor-claw", "read")]
    # 只有项目库能设参与的智能体
    wrong = await env.client.put(
        f"/api/v1/knowledge/spaces/{public['id']}/agents/{instance_id}",
        json={"access": "read"},
        headers=env.headers(write=True),
    )
    assert wrong.status_code == 422
    removed = await env.client.delete(
        f"/api/v1/knowledge/spaces/{project_space['id']}/agents/{instance_id}",
        headers=env.headers(write=True),
    )
    assert removed.json()["agents"] == []
    # 智能体库只能设 read / none；写入开关只对智能体库
    bad = await env.client.patch(
        f"/api/v1/knowledge/spaces/{agent['id']}",
        json={"version": agent["version"], "agent_access": "read_write"},
        headers=env.headers(write=True),
    )
    assert bad.status_code == 422
    ok = await env.client.patch(
        f"/api/v1/knowledge/spaces/{agent['id']}",
        json={"version": agent["version"], "agent_access": "none", "writes_enabled": False},
        headers=env.headers(write=True),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["agent_access"] == "none" and ok.json()["writes_enabled"] is False
    stale = await env.client.patch(
        f"/api/v1/knowledge/spaces/{agent['id']}",
        json={"version": agent["version"], "writes_enabled": True},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    overview = await env.get(f"/api/v1/knowledge/agents/{instance_id}")
    assert overview["writes_enabled"] is False and overview["metrics"]["notes"] == 0
