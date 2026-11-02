"""工作台自己的项目与任务接口（T-0082）：真实 PostgreSQL / Redis。

覆盖 docs/decisions/2026-11-02-remove-vikunja.md：
- 项目：新建 / 改名 / 归档 / 删除（连带任务）、version 冲突、列表的任务数与搜索
- 任务：读取的过滤 / 排序 / 分页，写入的 Idempotency-Key
  （重放 A06、换内容复用 A07、并发的同一个请求），
  修改 / 删除的 version 比较、删除的幂等、完成时间、标签、移动到其他项目
- 权限：viewer 只读、member 可写、其他工作空间看不到；审计
- 首页的任务模块与项目进度
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from tests.helpers import BOB, ClientFactory, add_user, add_workspace, asyncpg_dsn, run_sql
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


@pytest.fixture
async def ready(env: Env) -> Env:
    """有两个项目、一个任务的工作空间。"""
    work = await env.add_project("工作", description="上班的事")
    await env.add_project("生活")
    await env.add_task(work["id"], "写周报", priority=3, description="**本周**", labels=["周报"])
    return env


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


async def audit_count(env: Env, action: str) -> int:
    count: int = await run_sql(
        env.database_url, "SELECT count(*) FROM workspace.audit_logs WHERE action = $1", action
    )
    return count


# ---------------------------------------------------------------- 项目


async def test_create_rename_archive_and_list_projects(env: Env) -> None:
    created = await env.create_project({"title": "  工作  ", "description": "上班的事"})
    assert created.status_code == 201, created.text
    project = created.json()
    assert (project["title"], project["description"], project["archived"]) == (
        "工作",
        "上班的事",
        False,
    )
    assert (project["version"], project["open_tasks"], project["done_tasks"]) == (1, 0, 0)
    assert await audit_count(env, "project.created") == 1

    renamed = await env.update_project(project["id"], {"version": 1, "title": "上班"})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["title"], renamed.json()["version"]) == ("上班", 2)
    assert renamed.json()["updated_at"] >= project["updated_at"]

    archived = await env.update_project(project["id"], {"version": 2, "archived": True})
    assert archived.status_code == 200 and archived.json()["archived"] is True
    assert (await env.get("/api/v1/projects"))["items"] == []  # 默认不含已归档
    listed = await env.get("/api/v1/projects", include_archived=True)
    assert [p["title"] for p in listed["items"]] == ["上班"]
    assert (await env.get(f"/api/v1/projects/{project['id']}"))["archived"] is True
    assert await audit_count(env, "project.updated") == 2


async def test_project_validation_and_conflicts(env: Env) -> None:
    invalid: list[dict[str, Any]] = [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 121},
        {"title": "a", "x": 1},
    ]
    for body in invalid:
        response = await env.create_project(body)
        assert response.status_code == 422, body
    project = await env.add_project("工作")
    updates: list[dict[str, Any]] = [
        {"version": 1},
        {"version": 1, "title": None},
        {"version": 0, "title": "a"},
    ]
    for body in updates:
        response = await env.update_project(project["id"], body)
        assert response.status_code == 422, body
    assert (
        await env.update_project(project["id"], {"version": 1, "title": "新"})
    ).status_code == 200
    stale = await env.update_project(project["id"], {"version": 1, "title": "旧"})
    assert stale.status_code == 409 and error_code(stale) == "VERSION_CONFLICT"
    details = stale.json()["error"]["details"]
    assert details["current_version"] == 2 and details["current"]["title"] == "新"
    missing = await env.update_project(str(uuid.uuid4()), {"version": 1, "title": "x"})
    assert missing.status_code == 404
    assert (
        await env.client.get(f"/api/v1/projects/{uuid.uuid4()}", headers=env.headers())
    ).status_code == 404


async def test_project_lists_count_tasks_search_and_paginate(env: Env) -> None:
    work = await env.add_project("工作")
    await env.add_project("生活")
    await env.add_project("100%_完成")
    await env.add_task(work["id"], "写周报")
    await env.add_task(work["id"], "评审")
    await env.add_task(work["id"], "上线", done=True)
    items = (await env.get("/api/v1/projects"))["items"]
    assert [p["title"] for p in items] == ["100%_完成", "工作", "生活"]
    counts = {p["title"]: (p["open_tasks"], p["done_tasks"]) for p in items}
    assert counts == {"100%_完成": (0, 0), "工作": (2, 1), "生活": (0, 0)}
    assert (await env.get(f"/api/v1/projects/{work['id']}"))["open_tasks"] == 2
    assert [p["title"] for p in (await env.get("/api/v1/projects", q="工"))["items"]] == ["工作"]
    assert [p["title"] for p in (await env.get("/api/v1/projects", q="%_"))["items"]] == [
        "100%_完成"
    ]  # LIKE 通配符被转义
    first = await env.get("/api/v1/projects", limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = await env.get("/api/v1/projects", limit=2, cursor=first["next_cursor"])
    assert [p["title"] for p in second["items"]] == ["生活"] and second["next_cursor"] is None


async def test_delete_project_removes_its_tasks(ready: Env) -> None:
    projects = await ready.projects()
    project = await ready.get(f"/api/v1/projects/{projects['工作']}")
    task = await ready.task_by_title("写周报")
    stale = await ready.delete_project(project["id"], project["version"] + 1)
    assert stale.status_code == 409 and error_code(stale) == "VERSION_CONFLICT"
    deleted = await ready.delete_project(project["id"], project["version"])
    assert deleted.status_code == 204
    assert await ready.projects() == {"生活": projects["生活"]}
    missing = await ready.client.get(f"/api/v1/tasks/{task['id']}", headers=ready.headers())
    assert missing.status_code == 404
    assert await ready.tasks(status="all") == []
    assert (await ready.delete_project(project["id"], project["version"])).status_code == 404
    metadata = await run_sql(
        ready.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs"
        " WHERE action = 'project.deleted'",
    )
    assert '"tasks_deleted": 1' in metadata


async def test_project_progress_counts_open_and_done_tasks(env: Env) -> None:
    """首页“项目进度”组件（T-0064）：有未完成任务的项目在前，归档项目不显示。"""
    work = await env.add_project("工作")
    life = await env.add_project("生活")
    await env.add_project("空项目")
    old = await env.add_project("旧项目")
    for title in ("周报", "评审", "部署"):
        await env.add_task(work["id"], title)
    await env.add_task(work["id"], "已上线", done=True)
    await env.add_task(life["id"], "买菜")
    await env.add_task(life["id"], "交水费", done=True)
    await env.add_task(life["id"], "体检", done=True)
    await env.add_task(old["id"], "旧任务")
    await env.update_project(old["id"], {"version": old["version"], "archived": True})
    page = await env.get("/api/v1/projects/progress")
    rows = [(p["title"], p["open_tasks"], p["done_tasks"]) for p in page["items"]]
    assert rows == [("工作", 3, 1), ("生活", 1, 2), ("空项目", 0, 0)]
    limited = await env.get("/api/v1/projects/progress", limit=1)
    assert [p["title"] for p in limited["items"]] == ["工作"]
    too_many = await env.client.get(
        "/api/v1/projects/progress", params={"limit": 21}, headers=env.headers()
    )
    assert too_many.status_code == 422


# ---------------------------------------------------------------- 任务读取


async def test_task_fields(ready: Env) -> None:
    [task] = (await ready.get("/api/v1/tasks"))["items"]
    assert task["title"] == "写周报"
    assert task["description"] == "**本周**"  # Markdown 原文
    assert (task["priority"], task["labels"], task["done"], task["done_at"]) == (
        3,
        ["周报"],
        False,
        None,
    )
    assert task["version"] == "1"
    assert task["project_id"] == (await ready.projects())["工作"]
    assert "source_url" not in task and "external_id" not in task
    assert await ready.get(f"/api/v1/tasks/{task['id']}") == task


async def test_task_filters_sort_and_pagination(env: Env) -> None:
    work = await env.add_project("工作")
    life = await env.add_project("生活")
    now = datetime.now(UTC)
    await env.add_task(work["id"], "明天", priority=1, due_at=(now + timedelta(days=1)).isoformat())
    await env.add_task(work["id"], "下周", priority=5, due_at=(now + timedelta(days=7)).isoformat())
    await env.add_task(work["id"], "无截止", priority=2)
    await env.add_task(life["id"], "买菜 100%_", priority=4, due_at=now.isoformat())
    await env.add_task(life["id"], "已完成", done=True)

    assert [t["title"] for t in await env.tasks()] == ["买菜 100%_", "明天", "下周", "无截止"]
    assert [t["title"] for t in await env.tasks(status="done")] == ["已完成"]
    assert len(await env.tasks(status="all")) == 5
    assert [t["title"] for t in await env.tasks(sort="priority")] == [
        "下周",
        "买菜 100%_",
        "无截止",
        "明天",
    ]
    newest = (await env.tasks(sort="updated", status="all"))[0]
    assert newest["title"] == "已完成"
    assert {t["title"] for t in await env.tasks(project_id=life["id"])} == {"买菜 100%_"}
    assert {t["title"] for t in await env.tasks(priority=[1, 5])} == {"明天", "下周"}
    assert [t["title"] for t in await env.tasks(q="100%_")] == ["买菜 100%_"]  # LIKE 通配符被转义
    assert [t["title"] for t in await env.tasks(q="%")] == ["买菜 100%_"]
    soon = await env.tasks(due_before=(now + timedelta(days=2)).isoformat())
    assert {t["title"] for t in soon} == {"买菜 100%_", "明天"}
    later = await env.tasks(due_after=(now + timedelta(days=2)).isoformat())
    assert {t["title"] for t in later} == {"下周"}

    titles: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = await env.get("/api/v1/tasks", **params)
        titles += [t["title"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert titles == ["买菜 100%_", "明天", "下周", "无截止"]

    bad = await env.client.get(
        "/api/v1/tasks", params={"cursor": "not-a-cursor"}, headers=env.headers()
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["loc"] == ["query", "cursor"]


async def test_reads_on_an_empty_workspace(env: Env) -> None:
    page = await env.get("/api/v1/tasks")
    assert page == {"items": [], "next_cursor": None, "total_estimate": None}
    assert (await env.get("/api/v1/projects"))["items"] == []
    missing = await env.client.get(f"/api/v1/tasks/{uuid.uuid4()}", headers=env.headers())
    assert missing.status_code == 404


# ---------------------------------------------------------------- 创建与 Idempotency-Key


async def test_create_task_and_replay_with_the_same_key(ready: Env) -> None:
    """A06：同一个 Idempotency-Key 重放不重复创建。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    due = datetime(2026, 11, 3, 18, 0, tzinfo=UTC).isoformat()
    body = {
        "project_id": projects["工作"],
        "title": "交报告",
        "priority": 4,
        "due_at": due,
        "labels": ["月报", " 月报 ", "紧急"],
    }
    first = await ready.create_task(body, key)
    assert first.status_code == 201, first.text
    assert "Idempotent-Replayed" not in first.headers
    created = first.json()
    assert (created["title"], created["priority"]) == ("交报告", 4)
    assert created["due_at"] == "2026-11-03T18:00:00Z"
    assert created["labels"] == ["月报", "紧急"]  # 去掉空白后重复的只保留一个
    assert created["project_id"] == projects["工作"]
    replay = await ready.create_task(body, key)
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == created
    assert (await ready.titles()).count("交报告") == 1
    assert await audit_count(ready, "task.created") == 2  # 夹具里的一个 + 这一个


async def test_reusing_a_key_for_a_different_request_is_rejected(ready: Env) -> None:
    """A07：同一个键换了请求内容返回 409。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "第一次"}
    assert (await ready.create_task(body, key)).status_code == 201
    reused = await ready.create_task({**body, "title": "第二次"}, key)
    assert reused.status_code == 409
    assert error_code(reused) == "IDEMPOTENCY_KEY_REUSED"
    assert "第二次" not in await ready.titles()


async def test_writes_require_an_idempotency_key(ready: Env) -> None:
    projects = await ready.projects()
    response = await ready.client.post(
        "/api/v1/tasks",
        json={"project_id": projects["工作"], "title": "没有键"},
        headers=ready.headers(write=True),
    )
    assert response.status_code == 422
    invalid = await ready.client.post(
        "/api/v1/tasks",
        json={"project_id": projects["工作"], "title": "坏键"},
        headers={**ready.headers(write=True), "Idempotency-Key": "abc"},
    )
    assert invalid.status_code == 422
    assert await ready.titles() == ["写周报"]


async def test_task_validation(ready: Env) -> None:
    projects = await ready.projects()
    base = {"project_id": projects["工作"]}
    bodies: list[dict[str, Any]] = [
        {**base, "title": ""},
        {**base, "title": "x" * 251},
        {**base, "title": "a", "priority": 6},
        {**base, "title": "a", "due_at": "2026-11-03T18:00:00"},  # 必须带时区
        {**base, "title": "a", "labels": ["x"] * 21},
        {**base, "title": "a", "labels": ["x" * 51]},
        {**base, "title": "a", "assignee": "bob"},  # 单人版没有负责人
    ]
    for body in bodies:
        response = await ready.create_task(body)
        assert response.status_code == 422, body


async def test_concurrent_duplicate_is_in_progress(ready: Env) -> None:
    """同一个键的另一个请求还没提交：这一个在唯一约束上等待，对方提交后返回“仍在处理”。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "慢慢创建"}
    conn = await asyncpg.connect(asyncpg_dsn(ready.database_url))
    try:
        other = conn.transaction()
        await other.start()
        await conn.execute(
            "INSERT INTO workspace.idempotency_requests (workspace_id, actor_id, idempotency_key,"
            " request_fingerprint, status, context_json, expires_at)"
            " VALUES ($1, $2, $3, 'other', 'processing', '{}', now() + interval '1 hour')",
            uuid.UUID(ready.workspace_id),
            ready.owner_id,
            key,
        )
        request = asyncio.create_task(ready.create_task(body, key))
        for _ in range(100):  # 等这个请求停在唯一约束上
            waiting = await run_sql(
                ready.database_url, "SELECT count(*) FROM pg_locks WHERE NOT granted"
            )
            if waiting:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("请求没有等待唯一约束")
        await other.commit()
        duplicate = await asyncio.wait_for(request, timeout=10)
    finally:
        await conn.close()
    assert duplicate.status_code == 409, duplicate.text
    assert error_code(duplicate) == "IDEMPOTENCY_IN_PROGRESS"
    assert duplicate.headers["Retry-After"] == "1"
    assert "慢慢创建" not in await ready.titles()  # 这一个的写入随事务回滚


async def test_left_over_processing_record_is_replaced(ready: Env) -> None:
    """T-0082 之前留下的“处理中”记录（Vikunja 结果未知）：作废，按新请求执行。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    await run_sql(
        ready.database_url,
        "INSERT INTO workspace.idempotency_requests (workspace_id, actor_id, idempotency_key,"
        " request_fingerprint, status, context_json, expires_at)"
        " VALUES ($1, $2, $3, 'old', 'processing', '{}', now() + interval '1 hour')",
        uuid.UUID(ready.workspace_id),
        ready.owner_id,
        key,
    )
    body = {"project_id": projects["工作"], "title": "旧键新请求"}
    reused = await ready.create_task(body, key)
    assert reused.status_code == 409 and error_code(reused) == "IDEMPOTENCY_KEY_REUSED"
    await run_sql(
        ready.database_url,
        "UPDATE workspace.idempotency_requests SET expires_at = now() - interval '1 minute'"
        " WHERE idempotency_key = $1",
        key,
    )
    created = await ready.create_task(body, key)
    assert created.status_code == 201, created.text
    assert (await ready.create_task(body, key)).headers["Idempotent-Replayed"] == "true"


async def test_create_in_an_unknown_project_is_404_and_replayed(ready: Env) -> None:
    key = uuid.uuid4()
    body = {"project_id": str(uuid.uuid4()), "title": "没有项目"}
    first = await ready.create_task(body, key)
    assert first.status_code == 404
    again = await ready.create_task(body, key)
    assert again.status_code == 404  # 确定的失败也记下来，重放同样的错误
    assert await ready.titles() == ["写周报"]


# ---------------------------------------------------------------- 修改与删除


async def test_update_task_checks_the_version(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    updated = await ready.update_task(
        task["id"], {"version": task["version"], "title": "写月报", "labels": ["月报"]}
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["title"], body["labels"], body["version"]) == ("写月报", ["月报"], "2")
    assert body["description"] == "**本周**"  # 没发送的字段不变
    stale = await ready.update_task(task["id"], {"version": task["version"], "title": "旧"})
    assert stale.status_code == 409 and error_code(stale) == "VERSION_CONFLICT"
    details = stale.json()["error"]["details"]
    assert details["current_version"] == "2" and details["current"]["title"] == "写月报"
    patches: list[dict[str, Any]] = [
        {"version": "2"},
        {"version": "2", "title": None},
        {"version": "2", "priority": None},
        {"version": "2", "labels": None},
    ]
    for invalid in patches:
        assert (await ready.update_task(task["id"], invalid)).status_code == 422, invalid
    cleared = await ready.update_task(
        task["id"], {"version": "2", "due_at": "2026-11-03T10:00:00+08:00"}
    )
    assert cleared.json()["due_at"] == "2026-11-03T02:00:00Z"
    cleared = await ready.update_task(task["id"], {"version": "3", "due_at": None})
    assert cleared.status_code == 200 and cleared.json()["due_at"] is None
    missing = await ready.update_task(str(uuid.uuid4()), {"version": "1", "title": "x"})
    assert missing.status_code == 404
    metadata = await run_sql(
        ready.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs"
        " WHERE action = 'task.updated' ORDER BY created_at LIMIT 1",
    )
    assert '"fields": ["labels", "title"]' in metadata


async def test_done_sets_and_clears_the_done_time(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    done = await ready.update_task(task["id"], {"version": task["version"], "done": True})
    assert done.status_code == 200
    assert done.json()["done"] is True and done.json()["done_at"] is not None
    reopened = await ready.update_task(task["id"], {"version": "2", "done": False})
    assert reopened.json()["done"] is False and reopened.json()["done_at"] is None


async def test_update_replay_returns_the_first_result(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    key = uuid.uuid4()
    body = {"version": task["version"], "priority": 5}
    first = await ready.update_task(task["id"], body, key)
    assert first.status_code == 200
    replay = await ready.update_task(task["id"], body, key)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    assert (await ready.task_by_title("写周报"))["version"] == "2"  # 只改了一次


async def test_move_task_to_another_project(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    projects = await ready.projects()
    moved = await ready.update_task(
        task["id"], {"version": task["version"], "project_id": projects["生活"]}
    )
    assert moved.status_code == 200 and moved.json()["project_id"] == projects["生活"]
    unknown = await ready.update_task(task["id"], {"version": "2", "project_id": str(uuid.uuid4())})
    assert unknown.status_code == 404
    assert (await ready.task_by_title("写周报"))["version"] == "2"  # 失败没有改动


async def test_delete_task_is_idempotent(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    stale = await ready.delete_task(task["id"], "7")
    assert stale.status_code == 409 and error_code(stale) == "VERSION_CONFLICT"
    key = uuid.uuid4()
    deleted = await ready.delete_task(task["id"], task["version"], key)
    assert deleted.status_code == 204
    replay = await ready.delete_task(task["id"], task["version"], key)
    assert replay.status_code == 204 and replay.headers["Idempotent-Replayed"] == "true"
    again = await ready.delete_task(task["id"], task["version"])  # 新的键：已经不存在也是 204
    assert again.status_code == 204
    assert await ready.tasks(status="all") == []
    assert await audit_count(ready, "task.deleted") == 1


# ---------------------------------------------------------------- 权限


async def test_viewer_reads_member_writes(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    projects = await ready.projects()
    async with ready.member("viewer@example.com", "viewer") as viewer:
        headers = {"X-Workspace-Id": ready.workspace_id, "X-CSRF-Token": viewer.cookies["ws_csrf"]}
        assert (await viewer.get("/api/v1/tasks", headers=headers)).status_code == 200
        assert (await viewer.get("/api/v1/projects", headers=headers)).status_code == 200
        denied = [
            await viewer.post(
                "/api/v1/tasks",
                json={"project_id": projects["工作"], "title": "viewer"},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            ),
            await viewer.delete(
                f"/api/v1/tasks/{task['id']}",
                params={"version": task["version"]},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            ),
            await viewer.post("/api/v1/projects", json={"title": "viewer"}, headers=headers),
            await viewer.patch(
                f"/api/v1/projects/{projects['工作']}",
                json={"version": 1, "title": "viewer"},
                headers=headers,
            ),
            await viewer.delete(
                f"/api/v1/projects/{projects['工作']}", params={"version": 1}, headers=headers
            ),
        ]
        assert [r.status_code for r in denied] == [403] * 5
    assert await ready.titles() == ["写周报"]
    assert set(await ready.projects()) == {"工作", "生活"}
    async with ready.member("member@example.com", "member") as member:
        headers = {"X-Workspace-Id": ready.workspace_id, "X-CSRF-Token": member.cookies["ws_csrf"]}
        created = await member.post(
            "/api/v1/tasks",
            json={"project_id": projects["工作"], "title": "member"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 201, created.text
        project = await member.post("/api/v1/projects", json={"title": "成员的"}, headers=headers)
        assert project.status_code == 201, project.text


async def test_other_workspaces_cannot_see_or_use_projects_and_tasks(
    ready: Env, make_client: ClientFactory
) -> None:
    task = await ready.task_by_title("写周报")
    projects = await ready.projects()
    bob_id = await add_user(ready.database_url, BOB["email"], BOB["password"])
    bob_workspace = await add_workspace(ready.database_url, bob_id, "Bob 的空间")
    async with make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        own = {"X-Workspace-Id": str(bob_workspace), "X-CSRF-Token": bob.cookies["ws_csrf"]}
        assert (await bob.get(f"/api/v1/tasks/{task['id']}", headers=own)).status_code == 404
        assert (
            await bob.get(f"/api/v1/projects/{projects['工作']}", headers=own)
        ).status_code == 404
        assert (await bob.get("/api/v1/tasks", headers=own)).json()["items"] == []
        assert (await bob.get("/api/v1/projects", headers=own)).json()["items"] == []
        # 用别人的项目 ID 在自己的空间里建任务：项目不在这个空间里
        foreign_project = await bob.post(
            "/api/v1/tasks",
            json={"project_id": projects["工作"], "title": "越权"},
            headers={**own, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert foreign_project.status_code == 404
        renamed = await bob.patch(
            f"/api/v1/projects/{projects['工作']}",
            json={"version": 1, "title": "越权"},
            headers=own,
        )
        assert renamed.status_code == 404
        foreign = {"X-Workspace-Id": ready.workspace_id}
        assert (await bob.get("/api/v1/tasks", headers=foreign)).status_code == 404
    assert (await ready.get(f"/api/v1/projects/{projects['工作']}"))["title"] == "工作"


# ---------------------------------------------------------------- 首页任务模块


async def test_overview_tasks_module(env: Env) -> None:
    work = await env.add_project("工作")
    archived = await env.add_project("归档", archived=True)
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=1, microsecond=0)
    await env.add_task(work["id"], "今天", due_at=start.isoformat())
    await env.add_task(work["id"], "逾期", due_at=(now - timedelta(days=3)).isoformat())
    await env.add_task(work["id"], "以后", due_at=(now + timedelta(days=3)).isoformat())
    await env.add_task(work["id"], "完成了", done=True, due_at=now.isoformat())
    await env.add_task(archived["id"], "归档项目里的", due_at=(now + timedelta(days=9)).isoformat())
    overview = await env.get("/api/v1/dashboard/overview")
    module = overview["modules"]["tasks"]
    assert (module["status"], module["retryable"]) == ("ok", True)
    assert overview["summary"]["pending_tasks"] == 4
    assert overview["summary"]["active_projects"] == 1
    today = overview["today_tasks"]
    assert [t["title"] for t in today] == ["逾期", "今天"]
    assert {t["project_title"] for t in today} == {"工作"}
    assert all("source_url" not in t for t in today)
