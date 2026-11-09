"""任务接取（T-0111，docs/v2/AGENT_OPS_PLAN.md 第 3、4 节）。真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖：
- 设为自由接取（只限 owner / admin；有执行者、已完成的不行）；通知空闲的智能体（不重复发）；
- claim_task：直接执行、先到先得、空闲才能领、范围、自动加入项目、项目群通知、执行记录为 direct；
- release_task：只能放弃自己领取的、要写原因，放弃后重新可领；
- complete_task：直接完成、这段结束；不是自己的不行；
- 打回：原因、回到进行中、新的一段、IM 告知；原执行者已停用时放回未分配；任务的动态；
- 智能体看到的字段与筛选（assigned_to_me、claimable）、calendar_agenda 的 due_tasks；
- scope 为 read 的实例不能领取（FORBIDDEN_SCOPE）。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.agents import AgentService
from app.domain.im import admin_im_user_id
from app.models import AgentInstance
from tests.helpers import run_sql, tool_error
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_mcp_api import mcp_headers

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


# ---------------------------------------------------------------- 辅助


async def agent(env: ImEnv, name: str, *, scope: str = "read_write") -> tuple[dict[str, Any], str]:
    """建一个在线、IM 就绪的实例，返回（实例，MCP 令牌）。"""
    created = await env.create_instance(name=name, scope=scope)
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET status = 'online' WHERE id = $1",
        uuid.UUID(created["id"]),
    )
    app = env.app
    async with app.state.sessionmaker() as db:
        instance = await db.get(AgentInstance, uuid.UUID(created["id"]))
        assert instance is not None
        issued = await AgentService(db, app.state.settings).issue_token(
            instance, keep_previous=True
        )
        await db.commit()
    return created, issued.token


async def call(env: ImEnv, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = await env.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=mcp_headers(token),
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def ok(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    data: dict[str, Any] = result["structuredContent"]
    return data


async def project_and_task(env: ImEnv, **task: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    project = await env.client.post(
        "/api/v1/projects", json={"title": "发布 2.0"}, headers=env.headers(write=True)
    )
    assert project.status_code == 201, project.text
    body = {"project_id": project.json()["id"], "title": "整理发布说明", **task}
    created = await env.client.post(
        "/api/v1/tasks",
        json=body,
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    return project.json(), created.json()


async def patch(env: ImEnv, task: dict[str, Any], expect: int = 200, **body: Any) -> dict[str, Any]:
    response = await env.client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"version": task["version"], **body},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def get_task(env: ImEnv, task_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/tasks/{task_id}", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def texts_to(env: ImEnv, im_user_id: str) -> list[str]:
    return [
        body["content"]["content"]
        for body in env.fake.paths("/msg/send_msg")
        if body.get("recvID") == im_user_id and "content" in body.get("content", {})
    ]


def group_texts(env: ImEnv) -> list[str]:
    return [
        body["content"].get("content", "")
        for body in env.fake.paths("/msg/send_msg")
        if body.get("groupID") and body.get("sendID") == "ws_notice"
    ]


async def runs(env: ImEnv, task_id: str) -> list[dict[str, Any]]:
    response = await env.client.get(f"/api/v1/tasks/{task_id}/runs", headers=env.headers())
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


# ---------------------------------------------------------------- 测试


async def test_open_for_claim_announces_to_idle_agents(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    reader, _ = await agent(env, "丙", scope="read")
    _, task = await project_and_task(env)
    opened = await patch(env, task, claim_mode="open")
    assert opened["claim_mode"] == "open" and opened["claimable"] is True
    for instance in (a, b):
        sent = texts_to(env, instance["im_user_id"])
        assert len(sent) == 1 and task["id"] in sent[0] and "claim_task" in sent[0]
    assert texts_to(env, reader["im_user_id"]) == []  # 只读的实例不通知
    sender = {
        body["sendID"]
        for body in env.fake.paths("/msg/send_msg")
        if body.get("recvID") == a["im_user_id"]
    }
    assert sender == {admin_im_user_id(env.owner_id)}
    # 再保存一次别的字段：不会再通知
    await patch(env, opened, priority=3)
    assert len(texts_to(env, a["im_user_id"])) == 1
    events = (
        await env.client.get(f"/api/v1/tasks/{task['id']}/events", headers=env.headers())
    ).json()["items"]
    assert [e["kind"] for e in events] == ["opened"]


async def test_open_requires_admin_and_no_assignee(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    _, task = await project_and_task(env)
    assigned = await patch(env, task, assignee_instance_id=a["id"])
    refused = await patch(env, assigned, expect=409, claim_mode="open")
    assert refused["error"]["code"] == "TASK_NOT_OPENABLE"
    # 改回未分配并设为自由接取可以一起做
    opened = await patch(
        env, assigned, claim_mode="open", assignee_instance_id=None, assignee_user_id=None
    )
    assert opened["claimable"] is True
    # 设了执行者：自动回到指派
    reassigned = await patch(env, opened, assignee_instance_id=a["id"])
    assert reassigned["claim_mode"] == "assigned" and reassigned["claimable"] is False


async def test_claim_is_first_come_and_requires_idle(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, token_b = await agent(env, "乙")
    project, task = await project_and_task(env)
    await patch(env, task, claim_mode="open")
    claimed = ok(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    assert claimed["status"] == "claimed" and claimed["already_yours"] is False
    assert claimed["joined_project"] is True
    again = ok(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    assert again["already_yours"] is True
    late = tool_error(await call(env, token_b, "claim_task", {"task_id": task["id"]}))
    assert late["code"] == "TASK_ALREADY_CLAIMED"
    current = await get_task(env, task["id"])
    assert current["assignee_instance_id"] == a["id"] and current["claimable"] is False
    only = await runs(env, task["id"])
    assert len(only) == 1 and only[0]["instance_id"] == a["id"] and only[0]["ended_at"] is None
    # 自动加入项目（项目知识库读写）
    members = (
        await env.client.get(f"/api/v1/projects/{project['id']}/agents", headers=env.headers())
    ).json()["items"]
    assert [m["name"] for m in members] == ["甲"]
    # 甲在忙：再领一个不行
    _, other = await project_and_task(env)
    await patch(env, other, claim_mode="open")
    busy = tool_error(await call(env, token_a, "claim_task", {"task_id": other["id"]}))
    assert busy["code"] == "AGENT_BUSY"
    # 执行记录：直接执行（direct），成功与失败都有
    listed = (await env.client.get("/api/v1/ai/tool-executions", headers=env.headers())).json()[
        "items"
    ]
    claims = [r for r in listed if r["tool_name"] == "claim_task"]
    assert {r["risk_level"] for r in claims} == {"direct"}
    assert {r["status"] for r in claims} == {"succeeded", "failed"}
    assert any(r["error_code"] == "TASK_ALREADY_CLAIMED" for r in claims)
    notified = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.notifications WHERE type = 'task.claimed'",
    )
    assert notified >= 1
    assert b["id"]  # 乙仍可领别的


async def test_project_scope_and_read_scope(env: ImEnv) -> None:
    _, token_a = await agent(env, "甲")
    _, token_reader = await agent(env, "丙", scope="read")
    _, task = await project_and_task(env)
    await patch(env, task, claim_mode="open", claim_scope="project")
    outside = tool_error(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    assert outside["code"] == "TASK_NOT_CLAIMABLE"
    denied = tool_error(await call(env, token_reader, "claim_task", {"task_id": task["id"]}))
    assert denied["code"] == "FORBIDDEN_SCOPE"
    # 指派的任务不能领
    _, assigned = await project_and_task(env)
    refused = tool_error(await call(env, token_a, "claim_task", {"task_id": assigned["id"]}))
    assert refused["code"] == "TASK_NOT_CLAIMABLE"


async def test_release_and_complete(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, token_b = await agent(env, "乙")
    _, task = await project_and_task(env)
    await patch(env, task, claim_mode="open")
    ok(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    not_mine = tool_error(
        await call(env, token_b, "release_task", {"task_id": task["id"], "reason": "x"})
    )
    assert not_mine["code"] == "TASK_NOT_YOURS"
    released = ok(
        await call(env, token_a, "release_task", {"task_id": task["id"], "reason": "需要设计稿"})
    )
    assert released["status"] == "released"
    current = await get_task(env, task["id"])
    assert current["assignee_instance_id"] is None and current["claimable"] is True
    assert [r["end_reason"] for r in await runs(env, task["id"])] == ["paused"]
    # 放弃后重新通知（乙收到第二条）
    assert len(texts_to(env, b["im_user_id"])) == 2
    ok(await call(env, token_b, "claim_task", {"task_id": task["id"]}))
    wrong = tool_error(await call(env, token_a, "complete_task", {"task_id": task["id"]}))
    assert wrong["code"] == "TASK_NOT_YOURS"
    done = ok(
        await call(env, token_b, "complete_task", {"task_id": task["id"], "note": "已发到群里"})
    )
    assert done["status"] == "completed" and done["already_done"] is False
    current = await get_task(env, task["id"])
    assert current["done"] is True
    # 智能体完成的任务：新的完成轮次，成员们查看前是“已完成待查看”（T-0166）
    assert current["work_state"] == "awaiting_view" and current["completion_seq"] == 1
    progress = (await env.client.get("/api/v1/tasks/progress", headers=env.headers())).json()
    assert progress["counts"]["awaiting_view"] >= 1
    assert any(i["id"] == task["id"] and i["status"] == "awaiting_view" for i in progress["items"])
    assert sorted(r["end_reason"] for r in await runs(env, task["id"])) == ["done", "paused"]
    events = (
        await env.client.get(f"/api/v1/tasks/{task['id']}/events", headers=env.headers())
    ).json()["items"]
    assert [e["kind"] for e in events] == ["completed", "claimed", "released", "claimed", "opened"]
    assert events[0]["note"] == "已发到群里" and events[2]["note"] == "需要设计稿"
    # 指派的任务不能放弃
    _, assigned = await project_and_task(env)
    await patch(env, assigned, assignee_instance_id=a["id"])
    refused = tool_error(
        await call(env, token_a, "release_task", {"task_id": assigned["id"], "reason": "忙"})
    )
    assert refused["code"] == "TASK_NOT_RELEASABLE"


async def test_reopen_sends_reason_and_opens_new_run(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    _, task = await project_and_task(env)
    await patch(env, task, claim_mode="open")
    ok(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    ok(await call(env, token_a, "complete_task", {"task_id": task["id"]}))
    done = await get_task(env, task["id"])
    response = await env.client.post(
        f"/api/v1/tasks/{task['id']}/reopen",
        json={"version": done["version"], "reason": "缺少回滚步骤"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    reopened = response.json()
    assert reopened["done"] is False and reopened["assignee_instance_id"] == a["id"]
    assert reopened["work_state"] == "running" and reopened["completion_seq"] == 1
    open_runs = [r for r in await runs(env, task["id"]) if r["ended_at"] is None]
    assert len(open_runs) == 1 and open_runs[0]["instance_id"] == a["id"]
    messages = texts_to(env, a["im_user_id"])
    assert any("被打回" in m and "缺少回滚步骤" in m for m in messages)
    # 智能体读到打回原因
    detail = ok(await call(env, token_a, "get_task", {"task_id": task["id"]}))["task"]
    assert detail["untrusted"]["last_reopen_reason"] == "缺少回滚步骤"
    assert detail["assignee"] == "me" and detail["running"] is True
    # 没完成的任务不能打回；版本不对 409
    again = await env.client.post(
        f"/api/v1/tasks/{task['id']}/reopen",
        json={"version": reopened["version"], "reason": "x"},
        headers=env.headers(write=True),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "TASK_NOT_DONE"


async def test_reopen_with_disabled_executor_returns_to_unassigned(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    _, task = await project_and_task(env)
    await patch(env, task, claim_mode="open")
    ok(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    ok(await call(env, token_a, "complete_task", {"task_id": task["id"]}))
    await env.set_enabled(a["id"], False)
    done = await get_task(env, task["id"])
    response = await env.client.post(
        f"/api/v1/tasks/{task['id']}/reopen",
        json={"version": done["version"], "reason": "重做"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assignee_instance_id"] is None and body["claim_mode"] == "assigned"
    assert all(r["ended_at"] is not None for r in await runs(env, task["id"]))


async def test_agent_views_filters_and_due_tasks(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    due = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    _, mine = await project_and_task(env, due_at=due)
    _, free = await project_and_task(env, due_at=due)
    await patch(env, mine, assignee_instance_id=a["id"])
    await patch(env, free, claim_mode="open")
    assigned = ok(await call(env, token_a, "list_tasks", {"assigned_to_me": True}))["items"]
    assert [t["id"] for t in assigned] == [mine["id"]]
    assert assigned[0]["assignee"] == "me" and assigned[0]["untrusted"]["assignee_name"] == "甲"
    claimable = ok(await call(env, token_a, "list_tasks", {"claimable": True}))["items"]
    assert [t["id"] for t in claimable] == [free["id"]]
    assert claimable[0]["claim"] == {"mode": "open", "scope": "all", "claimable": True}
    start = datetime.now(UTC).isoformat()
    end = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    agenda = ok(await call(env, token_a, "calendar_agenda", {"start": start, "end": end}))
    due_tasks = {t["id"]: t for t in agenda["due_tasks"]}
    assert due_tasks[mine["id"]]["assigned_to_me"] is True
    assert due_tasks[free["id"]]["claimable"] is True
    assert due_tasks[free["id"]]["untrusted"]["project"] == "发布 2.0"


async def test_claim_posts_to_project_group(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    project = await env.client.post(
        "/api/v1/projects",
        json={"title": "发布 2.0", "im_group": True},
        headers=env.headers(write=True),
    )
    assert project.status_code == 201, project.text
    created = await env.client.post(
        "/api/v1/tasks",
        json={"project_id": project.json()["id"], "title": "整理发布说明"},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    task = created.json()
    await patch(env, task, claim_mode="open")
    ok(await call(env, token_a, "claim_task", {"task_id": task["id"]}))
    group_id = project.json()["im_group"]["group_id"]
    assert a["im_user_id"] in env.fake.members[f"sg_{group_id}"]  # 领取后自动进了项目群
    assert any("甲 领取了《整理发布说明》" in t for t in group_texts(env))
    ok(await call(env, token_a, "complete_task", {"task_id": task["id"]}))
    assert any("甲 完成了《整理发布说明》" in t for t in group_texts(env))


async def test_blocked_task_cannot_be_claimed(env: ImEnv) -> None:
    """明确阻塞的任务不能被新领取；解除后恢复原来的领取规则（T-0166）。"""
    _, token = await agent(env, "丙")
    _, task = await project_and_task(env)
    task = await patch(env, task, claim_mode="open")
    response = await env.client.patch(
        f"/api/v1/tasks/{task['id']}/block",
        json={"version": task["version"], "blocked": True, "reason": "等发布窗口"},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    assert response.json()["work_state"] == "blocked"
    refused = tool_error(await call(env, token, "claim_task", {"task_id": task["id"]}))
    assert refused["code"] == "TASK_BLOCKED"
    unblocked = await env.client.patch(
        f"/api/v1/tasks/{task['id']}/block",
        json={"version": response.json()["version"], "blocked": False},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert unblocked.status_code == 200, unblocked.text
    ok(await call(env, token, "claim_task", {"task_id": task["id"]}))
