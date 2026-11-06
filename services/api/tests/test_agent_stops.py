"""终止智能体正在执行的任务（T-0126，docs/v2/AGENT_MESSAGING_PLAN.md 第 7.3 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。覆盖：

- 权限开关：默认关闭，工具不列出、调用返回 FORBIDDEN_CAPABILITY；
  管理员打开 / 关闭（乐观并发、审计）；只读实例打开了也看不到、调用不了；
- 智能体叫停别的智能体：list_agent_runs 看得到正在执行的任务；不能叫停自己、不能叫停别的工作空间的；
  叫停后对方的工具调用返回 RUN_STOPPED，指定的任务这一段记为中断，通知管理员、写审计与执行记录；
  每小时限额；
- 管理员叫停：建终止令（审计）、任务不是它在执行时拒绝、最近记录；
- 实例领取与报回：领取即执行中、别的实例领不到也报不了、报回成功解除软停止、失败不解除；
  10 分钟没领取记为过期，领取后 5 分钟没结果记为失败；结束 30 天的清理。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import agent_stops
from tests.helpers import run_sql, tool_error
from tests.test_agent_outbox import _ready_agent
from tests.test_agent_projects_api import tool_names
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import agent, call, ok, project_and_task, runs

pytestmark = pytest.mark.integration

STOP_TOOLS = {"list_agent_runs", "stop_agent_run"}


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def set_stop_others(env: ImEnv, instance_id: str, enabled: bool, expect: int = 200) -> Any:
    current = await env.instance(instance_id)
    response = await env.client.put(
        f"/api/v1/agents/instances/{instance_id}/stop-others",
        json={"enabled": enabled, "version": current["version"]},
        headers=env.headers(write=True),
    )
    assert response.status_code == expect, response.text
    return response.json()


async def running_task(env: ImEnv, instance: dict[str, Any]) -> dict[str, Any]:
    """建一个交给 instance 执行、正在执行中的任务（时间轴上有一段没结束的）。"""
    _, task = await project_and_task(env)
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET assignee_instance_id = $1 WHERE id = $2",
        uuid.UUID(instance["id"]),
        uuid.UUID(task["id"]),
    )
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.task_runs (workspace_id, task_id, executor_kind, instance_id,"
        " executor_name, started_at) VALUES ($1, $2, 'instance', $3, $4, now())",
        uuid.UUID(env.workspace_id),
        uuid.UUID(task["id"]),
        uuid.UUID(instance["id"]),
        instance["name"],
    )
    return task


async def claim(env: ImEnv, token: str) -> list[str]:
    response = await env.client.post(
        "/agent-api/v1/stops/claim", json={}, headers=agent_headers(token)
    )
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def report(env: ImEnv, token: str, stop_id: str, **body: Any) -> Any:
    return await env.client.post(
        f"/agent-api/v1/stops/{stop_id}/result", json=body, headers=agent_headers(token)
    )


async def stop_status(env: ImEnv, stop_id: str) -> str:
    status: str = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.agent_run_stops WHERE id = $1",
        uuid.UUID(stop_id),
    )
    return status


async def test_permission_is_off_by_default_and_audited(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    b, _ = await _ready_agent(env, "乙")
    assert (await env.instance(a["id"]))["stop_others"] == {
        "enabled": False,
        "granted_by": None,
        "granted_at": None,
    }
    assert not STOP_TOOLS & await tool_names(env, token)
    refused = await call(env, token, "stop_agent_run", {"instance_id": b["id"], "reason": "停一下"})
    assert tool_error(refused)["code"] == "FORBIDDEN_CAPABILITY"
    assert tool_error(await call(env, token, "list_agent_runs", {}))["code"] == (
        "FORBIDDEN_CAPABILITY"
    )

    stale = await env.instance(a["id"])
    granted = await set_stop_others(env, a["id"], True)
    assert granted["enabled"] is True and granted["granted_by"] == str(env.owner_id)
    assert await tool_names(env, token) >= STOP_TOOLS
    # 旧版本号：冲突
    conflict = await env.client.put(
        f"/api/v1/agents/instances/{a['id']}/stop-others",
        json={"enabled": False, "version": stale["version"]},
        headers=env.headers(write=True),
    )
    assert conflict.status_code == 409
    revoked = await set_stop_others(env, a["id"], False)
    assert revoked == {"enabled": False, "granted_by": None, "granted_at": None}
    assert not STOP_TOOLS & await tool_names(env, token)
    actions = await run_sql(
        env.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.instance.stop_others_%'",
    )
    assert actions == "agents.instance.stop_others_enabled,agents.instance.stop_others_disabled"
    # 记了拒绝的执行记录
    denied = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions"
        " WHERE tool_name = 'stop_agent_run' AND error_code = 'FORBIDDEN_CAPABILITY'",
    )
    assert denied == 1


async def test_read_only_instance_cannot_stop_others(env: ImEnv) -> None:
    reader, token = await agent(env, "丙", scope="read")
    b, _ = await _ready_agent(env, "乙")
    await set_stop_others(env, reader["id"], True)
    names = await tool_names(env, token)
    assert not STOP_TOOLS & names
    refused = await call(env, token, "stop_agent_run", {"instance_id": b["id"], "reason": "x"})
    assert tool_error(refused)["code"] == "FORBIDDEN_SCOPE"


async def test_agent_stops_another_agent_and_instance_aborts(env: ImEnv) -> None:
    a, token_a = await _ready_agent(env, "甲")
    b, token_b = await _ready_agent(env, "乙")
    await set_stop_others(env, a["id"], True)
    task = await running_task(env, b)

    listed = ok(await call(env, token_a, "list_agent_runs", {}))
    rows = {row["instance_id"]: row for row in listed["items"]}
    assert rows[a["id"]]["is_you"] is True and rows[a["id"]]["running_tasks"] == []
    mine = rows[b["id"]]
    assert mine["is_you"] is False and mine["stopping"] is False
    assert [t["task_id"] for t in mine["running_tasks"]] == [task["id"]]
    assert mine["running_tasks"][0]["untrusted"]["title"] == task["title"]
    assert mine["untrusted"]["name"] == "乙"

    myself = await call(env, token_a, "stop_agent_run", {"instance_id": a["id"], "reason": "x"})
    assert tool_error(myself)["code"] == "VALIDATION_FAILED"
    nobody = await call(
        env, token_a, "stop_agent_run", {"instance_id": str(uuid.uuid4()), "reason": "x"}
    )
    assert tool_error(nobody)["code"] == "NOT_FOUND"
    empty = await call(env, token_a, "stop_agent_run", {"instance_id": b["id"], "reason": " "})
    assert tool_error(empty)["code"] == "VALIDATION_FAILED"
    wrong_task = await call(
        env,
        token_a,
        "stop_agent_run",
        {"instance_id": b["id"], "reason": "x", "task_id": str(uuid.uuid4())},
    )
    assert tool_error(wrong_task)["code"] == "NOT_FOUND"

    before = ok(await call(env, token_b, "list_tasks", {}))
    assert before is not None
    stopped = ok(
        await call(
            env,
            token_a,
            "stop_agent_run",
            {"instance_id": b["id"], "reason": "同一个查询调了 40 次", "task_id": task["id"]},
        )
    )
    assert stopped["status"] == "stopping"
    soft_until = datetime.fromisoformat(stopped["soft_until"])
    assert timedelta(minutes=4) < soft_until - datetime.now(UTC) <= timedelta(minutes=5)

    # 乙接下来的工具调用都被拒绝，说明是谁、为什么
    blocked = tool_error(await call(env, token_b, "list_tasks", {}))
    assert blocked["code"] == "RUN_STOPPED"
    assert "智能体「甲」" in blocked["message"] and "同一个查询调了 40 次" in blocked["message"]
    # 甲不受影响
    assert ok(await call(env, token_a, "list_agent_runs", {}))["items"]
    listed = ok(await call(env, token_a, "list_agent_runs", {}))
    rows = {row["instance_id"]: row for row in listed["items"]}
    assert rows[b["id"]]["stopping"] is True and rows[b["id"]]["running_tasks"] == []
    # 指定的任务：这一段记为中断，任务仍归乙
    segments = await runs(env, task["id"])
    assert [s["end_reason"] for s in segments] == ["interrupted"]
    assert (await env.instance(b["id"]))["id"] == b["id"]
    assignee = await run_sql(
        env.database_url,
        "SELECT assignee_instance_id FROM workspace.tasks WHERE id = $1",
        uuid.UUID(task["id"]),
    )
    assert str(assignee) == b["id"]
    # 通知管理员、审计、执行记录
    title = await run_sql(
        env.database_url,
        "SELECT title FROM workspace.notifications WHERE type = 'agent.run_stopped_by_agent'",
    )
    assert title == "「甲」叫停了「乙」正在执行的任务"
    audit = await run_sql(
        env.database_url,
        "SELECT metadata_redacted_json->>'by_instance' FROM workspace.audit_logs"
        " WHERE action = 'agents.run.stop_requested'",
    )
    assert audit == a["id"]
    executed = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions"
        " WHERE tool_name = 'stop_agent_run' AND status = 'succeeded'",
    )
    assert executed == 1

    # 乙的接入包领取终止令（别的实例领不到），中止后报回，软停止解除
    assert await claim(env, token_a) == []
    assert await claim(env, token_b) == [stopped["stop_id"]]
    assert await claim(env, token_b) == []  # 领取即执行中
    assert await stop_status(env, stopped["stop_id"]) == "running"
    stranger = await report(env, token_a, stopped["stop_id"], status="done", aborted=1)
    assert stranger.status_code == 404
    done = await report(env, token_b, stopped["stop_id"], status="done", aborted=2)
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert ok(await call(env, token_b, "list_tasks", {})) is not None
    history = await env.client.get(
        f"/api/v1/agents/instances/{b['id']}/stops", headers=env.headers()
    )
    assert history.status_code == 200
    (item,) = history.json()["items"]
    assert (item["status"], item["aborted"], item["requested_by_instance"]) == ("done", 2, a["id"])
    assert item["task_id"] == task["id"] and item["requested_by"] is None
    # 再报一次不改结果
    again = await report(env, token_b, stopped["stop_id"], status="failed", error="x")
    assert again.status_code == 200 and again.json()["status"] == "done"


async def test_agent_stops_are_rate_limited(env: ImEnv) -> None:
    a, token_a = await _ready_agent(env, "甲")
    b, _ = await _ready_agent(env, "乙")
    await set_stop_others(env, a["id"], True)
    for _ in range(agent_stops.STOPS_PER_HOUR):
        ok(await call(env, token_a, "stop_agent_run", {"instance_id": b["id"], "reason": "x"}))
    limited = await call(env, token_a, "stop_agent_run", {"instance_id": b["id"], "reason": "x"})
    assert tool_error(limited)["code"] == "RATE_LIMITED"


async def test_stopped_agent_is_told_so_even_when_rate_limited(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import mcp_tools

    monkeypatch.setattr(mcp_tools, "CALLS_PER_MINUTE", 2)
    _, token_a = await _ready_agent(env, "甲")
    b, token_b = await _ready_agent(env, "乙")
    for _ in range(2):
        assert ok(await call(env, token_a, "list_tasks", {})) is not None
    assert tool_error(await call(env, token_a, "list_tasks", {}))["code"] == "RATE_LIMITED"
    stopped = await env.client.post(
        f"/api/v1/agents/instances/{b['id']}/stop",
        json={"reason": "刷调用"},
        headers=env.headers(write=True),
    )
    assert stopped.status_code == 201
    # 停不下来的那一轮往往正在刷调用：它看到的是“被叫停”，不是“太频繁”
    for _ in range(4):
        assert tool_error(await call(env, token_b, "list_tasks", {}))["code"] == "RUN_STOPPED"


async def test_admin_stop_expiry_and_failure(env: ImEnv) -> None:
    _, token_a = await _ready_agent(env, "甲")
    b, token_b = await _ready_agent(env, "乙")
    _, other_task = await project_and_task(env)
    not_its = await env.client.post(
        f"/api/v1/agents/instances/{b['id']}/stop",
        json={"reason": "停下", "task_id": other_task["id"]},
        headers=env.headers(write=True),
    )
    assert not_its.status_code == 422, not_its.text
    blank = await env.client.post(
        f"/api/v1/agents/instances/{b['id']}/stop",
        json={"reason": ""},
        headers=env.headers(write=True),
    )
    assert blank.status_code == 422
    created = await env.client.post(
        f"/api/v1/agents/instances/{b['id']}/stop",
        json={"reason": "跑偏了"},
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert (first["status"], first["requested_by"]) == ("pending", str(env.owner_id))
    blocked = tool_error(await call(env, token_b, "list_tasks", {}))
    assert blocked["code"] == "RUN_STOPPED" and blocked["message"].startswith("管理员叫停")
    assert ok(await call(env, token_a, "list_tasks", {})) is not None
    audit = await run_sql(
        env.database_url,
        "SELECT actor_id FROM workspace.audit_logs WHERE action = 'agents.run.stop_requested'",
    )
    assert audit == env.owner_id

    # 10 分钟没被领取：过期
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_run_stops SET created_at = now() - interval '11 minutes'"
        " WHERE id = $1",
        uuid.UUID(first["id"]),
    )
    assert await claim(env, token_b) == []
    assert await stop_status(env, first["id"]) == "expired"

    # 领取后 5 分钟没有结果：失败
    second = (
        await env.client.post(
            f"/api/v1/agents/instances/{b['id']}/stop",
            json={"reason": "再停一次"},
            headers=env.headers(write=True),
        )
    ).json()
    assert await claim(env, token_b) == [second["id"]]
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_run_stops SET claimed_at = now() - interval '6 minutes'"
        " WHERE id = $1",
        uuid.UUID(second["id"]),
    )
    assert await claim(env, token_b) == []
    assert await stop_status(env, second["id"]) == "failed"

    # 报回失败：记下原因，软停止不解除
    third = (
        await env.client.post(
            f"/api/v1/agents/instances/{b['id']}/stop",
            json={"reason": "第三次"},
            headers=env.headers(write=True),
        )
    ).json()
    assert await claim(env, token_b) == [third["id"]]
    failed = await report(
        env, token_b, third["id"], status="failed", aborted=0, error="sessions.abort failed: 1"
    )
    assert failed.status_code == 200 and failed.json()["status"] == "failed"
    assert tool_error(await call(env, token_b, "list_tasks", {}))["code"] == "RUN_STOPPED"
    history = (
        await env.client.get(f"/api/v1/agents/instances/{b['id']}/stops", headers=env.headers())
    ).json()["items"]
    assert [i["status"] for i in history] == ["failed", "failed", "expired"]
    assert history[0]["error"] == "sessions.abort failed: 1"

    # 别的工作空间的实例看不到（这里用不存在的实例代替）
    missing = await env.client.post(
        f"/api/v1/agents/instances/{uuid.uuid4()}/stop",
        json={"reason": "x"},
        headers=env.headers(write=True),
    )
    assert missing.status_code == 404

    # 结束 30 天的清理
    async with env.app.state.sessionmaker() as db:
        assert await agent_stops.purge(db, datetime.now(UTC) + timedelta(days=31)) == 3
        await db.commit()
    left = await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_run_stops")
    assert left == 0
