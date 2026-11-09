"""任务状态闭环（T-0166，docs/v2/pages-redesign 规范第 4.2、4.3、5 节）：真实 PostgreSQL / Redis。

- 五种状态：从未开始 / 进行中 / 暂停 / 明确阻塞 / 新完成 / 个人已查看 / 重开再完成
- 查看状态按人隔离；轮次变化返回 409；重复标记幂等；viewer 可以标自己的已查看
- 阻塞：要原因、member 以上、已完成不能标、不停止执行段、阻塞时不能开始执行；完成清除阻塞
- 列表按状态筛选、状态计数与列表同一筛选；读取一页的查询次数不随条数增加
- 项目：五种状态计数（按人）、0 任务、需要关注、已归档、本周完成、最近动态与游标、静态路由
- 旧任务（completion_seq = 0）不会变成待查看
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.helpers import ClientFactory, add_workspace, csrf, run_sql
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def agent_done(env: Env, task_id: str) -> None:
    """模拟智能体完成（新的一轮，谁都还没查看；真实路径见 test_task_claims_api）。"""
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET done = true, done_at = now(), "
        "completion_seq = completion_seq + 1, version = version + 1 WHERE id = $1",
        uuid.UUID(task_id),
    )


async def task(env: Env, task_id: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    response = await (client or env.client).get(f"/api/v1/tasks/{task_id}", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def block(
    env: Env,
    current: dict[str, Any],
    blocked: bool,
    reason: str = "",
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    who = client or env.client
    return await who.patch(
        f"/api/v1/tasks/{current['id']}/block",
        json={"version": current["version"], "blocked": blocked, "reason": reason},
        headers={**env.headers(key=uuid.uuid4()), **csrf(who)},
    )


async def view(
    env: Env, current: dict[str, Any], client: httpx.AsyncClient | None = None
) -> httpx.Response:
    who = client or env.client
    return await who.put(
        f"/api/v1/tasks/{current['id']}/result-view",
        json={"completion_seq": current["completion_seq"]},
        headers={**env.headers(), **csrf(who)},
    )


async def start(env: Env, current: dict[str, Any]) -> dict[str, Any]:
    response = await env.client.post(
        f"/api/v1/tasks/{current['id']}/runs",
        json={},
        headers=env.headers(write=True, key=uuid.uuid4()),
    )
    assert response.status_code in {200, 201}, response.text
    run: dict[str, Any] = response.json()
    return run


async def test_lifecycle_states_and_personal_view(env: Env) -> None:
    work = await env.add_project("工作")
    item = await env.add_task(work["id"], "GitHub 集成验证")
    assert item["work_state"] == "todo" and item["completion_seq"] == 0
    assert item["result_viewed"] is True and item["active_run"] is None

    run = await start(env, item)
    current = await task(env, item["id"])
    assert current["work_state"] == "running"
    assert current["active_run"]["id"] == run["id"]

    # 暂停不是阻塞，回到待办
    paused = await env.client.post(
        f"/api/v1/tasks/{item['id']}/runs/{run['id']}/stop",
        json={"reason": "paused"},
        headers=env.headers(write=True),
    )
    assert paused.status_code == 200, paused.text
    assert (await task(env, item["id"]))["work_state"] == "todo"

    # 标记阻塞不停止正在执行的一段；阻塞优先于进行中
    run = await start(env, await task(env, item["id"]))
    current = await task(env, item["id"])
    blocked = await block(env, current, True, "等待补充 GitHub 仓库访问权限")
    assert blocked.status_code == 200, blocked.text
    data = blocked.json()
    assert data["work_state"] == "blocked"
    assert data["blocker"]["reason"] == "等待补充 GitHub 仓库访问权限"
    assert data["blocker"]["blocked_by"] == str(env.owner_id)
    assert data["active_run"]["id"] == run["id"]
    unblocked = await block(env, data, False)
    assert unblocked.status_code == 200 and unblocked.json()["work_state"] == "running"
    assert unblocked.json()["blocker"] is None

    # 本人手动完成：新的一轮，本人已查看；完成清除阻塞、结束执行段
    reblocked = await block(env, unblocked.json(), True, "又卡住了")
    assert reblocked.status_code == 200
    done = await env.update_task(item["id"], {"version": reblocked.json()["version"], "done": True})
    assert done.status_code == 200, done.text
    data = done.json()
    assert data["work_state"] == "done" and data["completion_seq"] == 1
    assert data["result_viewed"] is True and data["blocker"] is None and data["active_run"] is None
    kinds = [e["kind"] for e in (await env.get(f"/api/v1/tasks/{item['id']}/events"))["items"]]
    assert kinds[:4] == ["completed", "blocked", "unblocked", "blocked"]

    async with env.member("states-member@example.com", "member") as member:
        # 别人完成的：对另一个成员是“待查看”
        theirs = await task(env, item["id"], member)
        assert theirs["work_state"] == "awaiting_view" and theirs["result_viewed"] is False
        assert (await view(env, theirs, member)).json()["work_state"] == "done"

        # 重新打开：轮次不变；再完成（智能体）是新的一轮，两个人都要重新查看
        reopened = await env.update_task(item["id"], {"version": data["version"], "done": False})
        assert reopened.status_code == 200
        assert reopened.json()["work_state"] == "todo" and reopened.json()["completion_seq"] == 1
        await agent_done(env, item["id"])
        mine = await task(env, item["id"])
        assert mine["work_state"] == "awaiting_view" and mine["completion_seq"] == 2
        assert (await task(env, item["id"], member))["work_state"] == "awaiting_view"

        # 打开详情不会自动标记；只有显式标记，而且只影响自己
        assert (await task(env, item["id"]))["work_state"] == "awaiting_view"
        marked = await view(env, mine)
        assert marked.status_code == 200 and marked.json()["work_state"] == "done"
        assert (await task(env, item["id"], member))["work_state"] == "awaiting_view"
        again = await view(env, mine)
        assert again.status_code == 200 and again.json()["work_state"] == "done"

        # 改优先级之类的无关字段不产生新轮次
        latest = await task(env, item["id"])
        edited = await env.update_task(item["id"], {"version": latest["version"], "priority": 3})
        assert edited.json()["completion_seq"] == 2 and edited.json()["work_state"] == "done"


async def test_result_view_conflicts_and_viewer(env: Env) -> None:
    work = await env.add_project("工作")
    item = await env.add_task(work["id"], "部署检查报告")
    not_done = await view(env, {**item, "completion_seq": 1})
    assert not_done.status_code == 409
    assert not_done.json()["error"]["code"] == "COMPLETION_CHANGED"
    await agent_done(env, item["id"])
    first = await task(env, item["id"])
    await agent_done(env, item["id"])  # 又完成了一轮（例如重开后再完成）
    stale = await view(env, first)
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current"]["completion_seq"] == 2
    zero = await env.client.put(
        f"/api/v1/tasks/{item['id']}/result-view",
        json={"completion_seq": 0},
        headers=env.headers(write=True),
    )
    assert zero.status_code == 422

    async with env.member("states-viewer@example.com", "viewer") as viewer:
        mine = await task(env, item["id"], viewer)
        assert mine["work_state"] == "awaiting_view"
        marked = await view(env, mine, viewer)
        assert marked.status_code == 200 and marked.json()["work_state"] == "done"
        denied = await block(env, mine, True, "viewer 不能标阻塞", viewer)
        assert denied.status_code == 403
    assert (await task(env, item["id"]))["work_state"] == "awaiting_view"

    # 别的工作空间（同一个 owner）看不到这个任务，不能跨空间标记
    other = await add_workspace(env.database_url, env.owner_id, "另一个空间")
    response = await env.client.put(
        f"/api/v1/tasks/{item['id']}/result-view",
        json={"completion_seq": 2},
        headers={"X-Workspace-Id": str(other), **csrf(env.client)},
    )
    assert response.status_code == 404


async def test_block_rules(env: Env) -> None:
    work = await env.add_project("工作")
    item = await env.add_task(work["id"], "生产环境发布")
    no_reason = await block(env, item, True, "   ")
    assert no_reason.status_code == 422
    too_long = await block(env, item, True, "长" * 2001)
    assert too_long.status_code == 422
    stale = await block(env, {**item, "version": "999"}, True, "x")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    blocked = await block(env, item, True, "等待发布窗口")
    assert blocked.status_code == 200
    # 阻塞的任务不能开始执行
    started = await env.client.post(
        f"/api/v1/tasks/{item['id']}/runs",
        json={},
        headers=env.headers(write=True, key=uuid.uuid4()),
    )
    assert started.status_code == 409 and started.json()["error"]["code"] == "TASK_BLOCKED"
    # 同一个 Idempotency-Key 重放原样返回
    key = uuid.uuid4()
    body = {"version": blocked.json()["version"], "blocked": False, "reason": ""}
    headers = env.headers(write=True, key=key)
    once = await env.client.patch(f"/api/v1/tasks/{item['id']}/block", json=body, headers=headers)
    twice = await env.client.patch(f"/api/v1/tasks/{item['id']}/block", json=body, headers=headers)
    assert once.status_code == twice.status_code == 200
    assert twice.headers.get("Idempotent-Replayed") == "true"

    done = await env.add_task(work["id"], "已经完成的", done=True)
    refused = await block(env, done, True, "x")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "TASK_DONE"


async def test_list_filter_and_summary(env: Env) -> None:
    work = await env.add_project("工作")
    other = await env.add_project("其他")
    await env.add_task(work["id"], "待办甲")
    running = await env.add_task(work["id"], "进行中乙")
    await start(env, running)
    blocked = await env.add_task(work["id"], "阻塞丙")
    assert (await block(env, blocked, True, "等人")).status_code == 200
    awaiting = await env.add_task(work["id"], "待查看丁")
    await agent_done(env, awaiting["id"])
    await env.add_task(work["id"], "已完成戊", done=True)
    await env.add_task(other["id"], "别的项目")

    summary = await env.get("/api/v1/tasks/summary", project_id=work["id"])
    assert {k: summary[k] for k in ("todo", "running", "blocked", "awaiting_view", "done")} == {
        "todo": 1,
        "running": 1,
        "blocked": 1,
        "awaiting_view": 1,
        "done": 1,
    }
    assert summary["total"] == 5
    everything = await env.get("/api/v1/tasks/summary")
    assert everything["total"] == 6 and everything["todo"] == 2
    searched = await env.get("/api/v1/tasks/summary", q="阻塞")
    assert searched["total"] == 1 and searched["blocked"] == 1

    for state, title in [
        ("todo", "待办甲"),
        ("running", "进行中乙"),
        ("blocked", "阻塞丙"),
        ("awaiting_view", "待查看丁"),
        ("done", "已完成戊"),
    ]:
        items = await env.tasks(status="all", work_state=state, project_id=work["id"])
        assert [i["title"] for i in items] == [title], state
        assert items[0]["work_state"] == state
    # 与旧的 status 取交集
    assert await env.tasks(status="open", work_state="done") == []

    conflict = await env.client.get(
        "/api/v1/tasks",
        params={"assignee": "me", "assignee_user_id": str(env.owner_id)},
        headers=env.headers(),
    )
    assert conflict.status_code == 422
    bad = await env.client.get(
        "/api/v1/tasks", params={"work_state": "paused"}, headers=env.headers()
    )
    assert bad.status_code == 422


@contextmanager
def count_queries() -> Iterator[list[str]]:
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_listing_states_is_not_n_plus_one(env: Env) -> None:
    work = await env.add_project("批量")
    for index in range(50):
        await env.add_task(work["id"], f"任务 {index:02d}")
    with count_queries() as small:
        await env.tasks(limit=5)
    with count_queries() as large:
        await env.tasks(limit=50)
    assert len(large) - len(small) <= 2, (len(small), len(large))


async def test_projects_states_attention_summary_and_activity(env: Env) -> None:
    empty = await env.add_project("空项目")
    work = await env.add_project("Workspace 改版")
    archived = await env.add_project("旧项目", archived=True)
    todo = await env.add_task(work["id"], "待办")
    awaiting = await env.add_task(work["id"], "待查看")
    await agent_done(env, awaiting["id"])
    await env.add_task(work["id"], "已完成", done=True)
    assert (await block(env, todo, True, "等权限")).status_code == 200

    projects = {p["title"]: p for p in (await env.get("/api/v1/projects"))["items"]}
    assert projects["空项目"]["open_tasks"] == 0 and projects["空项目"]["done_tasks"] == 0
    assert projects["空项目"]["task_states"] == {
        "todo": 0,
        "running": 0,
        "awaiting_view": 0,
        "blocked": 0,
        "done": 0,
    }
    states = projects["Workspace 改版"]["task_states"]
    assert states["blocked"] == 1 and states["awaiting_view"] == 1 and states["done"] == 1
    # 待查看仍算已完成：进度不倒扣
    assert projects["Workspace 改版"]["done_tasks"] == 2
    assert projects["Workspace 改版"]["open_tasks"] == 1
    assert projects["Workspace 改版"]["last_activity_at"] >= work["updated_at"]

    attention = (await env.get("/api/v1/projects", attention_only="true"))["items"]
    assert [p["title"] for p in attention] == ["Workspace 改版"]
    only_archived = (await env.get("/api/v1/projects", archived_only="true"))["items"]
    assert [p["title"] for p in only_archived] == ["旧项目"]
    by_update = (await env.get("/api/v1/projects", sort="updated"))["items"]
    assert by_update[0]["title"] == "Workspace 改版"
    assert empty["id"] != archived["id"]

    now = datetime.now(UTC)
    window = {
        "completed_after": (now - timedelta(days=1)).isoformat(),
        "completed_before": (now + timedelta(days=1)).isoformat(),
    }
    summary = await env.get("/api/v1/projects/summary", **window)
    assert summary["active"] == 2 and summary["attention"] == 1 and summary["completed"] == 2
    past = await env.get(
        "/api/v1/projects/summary",
        completed_after=(now - timedelta(days=9)).isoformat(),
        completed_before=(now - timedelta(days=8)).isoformat(),
    )
    assert past["completed"] == 0
    reversed_window = await env.client.get(
        "/api/v1/projects/summary",
        params={
            "completed_after": window["completed_before"],
            "completed_before": window["completed_after"],
        },
        headers=env.headers(),
    )
    assert reversed_window.status_code == 422

    async with env.member("projects-member@example.com", "member") as member:
        theirs = await member.get(
            "/api/v1/projects", params={"attention_only": "true"}, headers=env.headers()
        )
        # 另一个成员也还没看过待查看的任务，同样需要关注；看过之后只剩阻塞
        assert [p["title"] for p in theirs.json()["items"]] == ["Workspace 改版"]

    # 最近动态：新的在前，带任务与项目名；游标翻页不重复
    activity = await env.get("/api/v1/projects/activity", limit=1)
    assert activity["items"][0]["kind"] == "blocked"
    assert activity["items"][0]["project_title"] == "Workspace 改版"
    assert activity["items"][0]["task_title"] == "待办"
    following = await env.get("/api/v1/projects/activity", limit=1, cursor=activity["next_cursor"])
    assert following["items"][0]["id"] != activity["items"][0]["id"]
    scoped = await env.get("/api/v1/projects/activity", project_id=empty["id"])
    assert scoped["items"] == [] and scoped["next_cursor"] is None
    broken = await env.client.get(
        "/api/v1/projects/activity", params={"cursor": "不是游标"}, headers=env.headers()
    )
    assert broken.status_code == 422


async def test_old_done_tasks_stay_done(env: Env) -> None:
    work = await env.add_project("历史")
    item = await env.add_task(work["id"], "上线前就完成的")
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET done = true, done_at = now(), completion_seq = 0 WHERE id = $1",
        uuid.UUID(item["id"]),
    )
    current = await task(env, item["id"])
    assert current["work_state"] == "done" and current["result_viewed"] is True
    progress = await env.get("/api/v1/tasks/progress")
    assert progress["counts"]["awaiting_view"] == 0
