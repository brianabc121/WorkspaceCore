"""首页改版（T-0163，docs/v2/HOME_PLAN.md）：真实 PostgreSQL / Redis。

- 任务进展：四种状态（待查看 / 阻塞 / 进行中 / 待办，T-0166 起与任务页同一判定）的数量、
  排序、筛选，归档项目与已查看的不算；待查看按当前用户
- 我的待办：assignee=me；新建任务可以直接指派给自己（必须是成员）
- 默认首页：新工作空间是效果图的五个组件；恢复默认首页
（标为已查看、阻塞的完整规则见 test_task_work_states.py）
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.dashboard_defaults import starter_widgets
from tests.helpers import ClientFactory, csrf, run_sql
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration

HOME_TYPES = ["work_summary", "task_progress", "news_briefing", "my_todos", "calendar_agenda"]


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def agent_done(env: Env, task_id: str) -> None:
    """模拟智能体用 complete_task 完成：新的完成轮次（智能体那条路径见 test_task_claims_api）。"""
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET done = true, done_at = now(), "
        "completion_seq = completion_seq + 1, version = version + 1 WHERE id = $1",
        uuid.UUID(task_id),
    )


async def block(env: Env, task: dict[str, Any], reason: str) -> dict[str, Any]:
    response = await env.client.patch(
        f"/api/v1/tasks/{task['id']}/block",
        json={"version": task["version"], "blocked": True, "reason": reason},
        headers=env.headers(write=True, key=uuid.uuid4()),
    )
    assert response.status_code == 200, response.text
    updated: dict[str, Any] = response.json()
    return updated


async def start_run(env: Env, task: dict[str, Any]) -> None:
    updated = await env.update_task(
        task["id"], {"version": task["version"], "assignee_user_id": str(env.owner_id)}
    )
    assert updated.status_code == 200, updated.text
    response = await env.client.post(
        f"/api/v1/tasks/{task['id']}/runs",
        json={},
        headers=env.headers(write=True, key=uuid.uuid4()),
    )
    assert response.status_code in {200, 201}, response.text


async def progress(env: Env, **params: Any) -> dict[str, Any]:
    return await env.get("/api/v1/tasks/progress", **params)


async def test_progress_statuses_counts_order_and_filters(env: Env) -> None:
    work = await env.add_project("工作")
    old = await env.add_project("旧项目")
    todo = await env.add_task(work["id"], "整理本周工作计划")
    running = await env.add_task(work["id"], "新闻早报整理")
    review = await env.add_task(work["id"], "首页改版方案")
    blocked = await env.add_task(work["id"], "GitHub 集成验证")
    await env.add_task(work["id"], "已经确认过的", done=True)
    await env.add_task(old["id"], "归档项目里的任务")
    await env.update_project(old["id"], {"version": old["version"], "archived": True})

    await start_run(env, running)
    await agent_done(env, review["id"])
    await block(env, blocked, "等待补充 GitHub 仓库访问权限")

    data = await progress(env)
    assert data["counts"] == {"all": 4, "todo": 1, "running": 1, "awaiting_view": 1, "blocked": 1}
    assert data["attention"] == 2
    statuses = [(item["title"], item["status"]) for item in data["items"]]
    assert statuses == [
        ("首页改版方案", "awaiting_view"),
        ("GitHub 集成验证", "blocked"),
        ("新闻早报整理", "running"),
        ("整理本周工作计划", "todo"),
    ]
    by_title = {item["title"]: item for item in data["items"]}
    blocker = by_title["GitHub 集成验证"]["blocker"]
    assert blocker["reason"] == "等待补充 GitHub 仓库访问权限"
    assert blocker["blocked_by"] == str(env.owner_id) and blocker["blocked_by_name"]
    assert by_title["新闻早报整理"]["executor"]["kind"] == "user"
    assert by_title["新闻早报整理"]["executor"]["id"] == str(env.owner_id)
    assert by_title["新闻早报整理"]["project_title"] == "工作"
    assert by_title["整理本周工作计划"]["executor"] is None
    assert todo["id"] == by_title["整理本周工作计划"]["id"]

    only_blocked = await progress(env, status="blocked")
    assert [item["title"] for item in only_blocked["items"]] == ["GitHub 集成验证"]
    assert only_blocked["counts"]["all"] == 4
    limited = await progress(env, limit=2)
    assert [item["status"] for item in limited["items"]] == ["awaiting_view", "blocked"]

    # 阻塞优先于进行中；标为已查看后待查看的离开首页
    current = await env.task_by_title("新闻早报整理")
    await block(env, current, "等一下")
    assert (await progress(env))["counts"]["blocked"] == 2
    seen = await env.task_by_title("首页改版方案")
    viewed = await env.client.put(
        f"/api/v1/tasks/{seen['id']}/result-view",
        json={"completion_seq": seen["completion_seq"]},
        headers=env.headers(write=True),
    )
    assert viewed.status_code == 200, viewed.text
    assert (await progress(env))["counts"]["awaiting_view"] == 0


async def test_progress_validation_and_viewer_access(env: Env) -> None:
    bad = await env.client.get(
        "/api/v1/tasks/progress", params={"status": "done"}, headers=env.headers()
    )
    assert bad.status_code == 422
    too_many = await env.client.get(
        "/api/v1/tasks/progress", params={"limit": 21}, headers=env.headers()
    )
    assert too_many.status_code == 422
    async with env.member("home-viewer@example.com", "viewer") as viewer:
        response = await viewer.get("/api/v1/tasks/progress", headers=env.headers())
        assert response.status_code == 200
        assert response.json()["counts"]["all"] == 0


async def test_my_todos_and_create_with_assignee(env: Env) -> None:
    work = await env.add_project("个人计划")
    mine = await env.add_task(work["id"], "确认首页改版方案", assignee_user_id=str(env.owner_id))
    assert mine["assignee_user_id"] == str(env.owner_id)
    await env.add_task(work["id"], "别人的")
    await env.add_task(work["id"], "已经做完的", assignee_user_id=str(env.owner_id), done=True)
    items = (await env.get("/api/v1/tasks", assignee="me"))["items"]
    assert [item["title"] for item in items] == ["确认首页改版方案"]
    stranger = await env.create_task(
        {"project_id": work["id"], "title": "x", "assignee_user_id": str(uuid.uuid4())}
    )
    assert stranger.status_code == 422
    async with env.member("home-member@example.com", "member") as member:
        theirs = await member.get("/api/v1/tasks", params={"assignee": "me"}, headers=env.headers())
        assert theirs.status_code == 200 and theirs.json()["items"] == []


async def test_new_workspace_home_and_reset(env: Env) -> None:
    overview = await env.get("/api/v1/dashboard/overview")
    dashboard = overview["dashboard"]
    widgets = sorted(overview["widgets"], key=lambda w: HOME_TYPES.index(w["widget_type"]))
    assert [w["widget_type"] for w in widgets] == HOME_TYPES
    expected = [w.model_dump(mode="json") for w in starter_widgets()]
    assert [
        {"layout": w["layout"], "widget_type": w["widget_type"], "config": w["config"]}
        for w in widgets
    ] == expected

    # 改一改再恢复
    removed = await env.client.delete(
        f"/api/v1/widgets/{widgets[0]['id']}", headers=env.headers(write=True)
    )
    assert removed.status_code == 204
    current = (await env.get("/api/v1/dashboard/overview"))["dashboard"]
    stale = await env.client.post(
        f"/api/v1/dashboards/{dashboard['id']}/reset",
        json={"version": dashboard["version"]},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    reset = await env.client.post(
        f"/api/v1/dashboards/{dashboard['id']}/reset",
        json={"version": current["version"]},
        headers=env.headers(write=True),
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["version"] == current["version"] + 1
    after = await env.get("/api/v1/dashboard/overview")
    assert sorted(w["widget_type"] for w in after["widgets"]) == sorted(HOME_TYPES)
    async with env.member("home-reset-viewer@example.com", "viewer") as viewer:
        denied = await viewer.post(
            f"/api/v1/dashboards/{dashboard['id']}/reset",
            json={"version": reset.json()["version"]},
            headers={**env.headers(), **csrf(viewer)},
        )
        assert denied.status_code == 403
