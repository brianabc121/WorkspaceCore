"""任务时间轴后端（T-0104，docs/v2/TIMELINE_PLAN.md 第 2 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。覆盖：

- 任务：执行者（成员 / 实例二选一）、计划开始时间；
- 执行记录：人的任务开始 / 暂停 / 完成；Idempotency-Key 重放；同一任务只有一段在执行；
- 交给实例执行：以管理员的 IM 账号发一条消息、同一个键不重复发、IM 结果未知时不开段；
- 中断：改执行者、停用实例、离线超过 10 分钟；
- 用量上报：实例凭证、基准、增量、值变小、null 跳过、日期与限流；
- 时间轴：token 按时间归属与平均分、等待审批、按项目、按执行者 / 项目分组、概况、范围与权限。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.domain import agent_watch
from app.domain.im import admin_im_user_id
from app.jobs import worker
from tests.helpers import OWNER, add_member, add_user, add_workspace, run_sql
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import ImEnv, _worker_ctx, im_env

pytestmark = pytest.mark.integration

PASSWORD = "member password 123456"  # noqa: S105 - 测试密码
SESSION_A = "a" * 64
SESSION_B = "b" * 64
SESSION_C = "c" * 64


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


def _h(env: ImEnv, *, write: bool = False, key: uuid.UUID | None = None) -> dict[str, str]:
    headers = env.headers(write=write)
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return headers


def code(response: httpx.Response) -> str:
    value: str = response.json()["error"]["code"]
    return value


async def add_project(env: ImEnv, title: str = "工作") -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/projects", json={"title": title}, headers=_h(env, write=True)
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def add_task(env: ImEnv, project_id: str, title: str, **fields: Any) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": title, **fields},
        headers=_h(env, write=True, key=uuid.uuid4()),
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def get_task(env: ImEnv, task_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/tasks/{task_id}", headers=_h(env))
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def patch(env: ImEnv, task_id: str, body: dict[str, Any]) -> httpx.Response:
    version = (await get_task(env, task_id))["version"]
    return await env.client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"version": version, **body},
        headers=_h(env, write=True, key=uuid.uuid4()),
    )


async def start(
    env: ImEnv, task_id: str, *, dispatch: bool = False, key: uuid.UUID | None = None
) -> httpx.Response:
    return await env.client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={"dispatch": dispatch},
        headers=_h(env, write=True, key=key or uuid.uuid4()),
    )


async def stop(env: ImEnv, task_id: str, run_id: str, reason: str) -> httpx.Response:
    return await env.client.post(
        f"/api/v1/tasks/{task_id}/runs/{run_id}/stop",
        json={"reason": reason},
        headers=_h(env, write=True),
    )


async def runs(env: ImEnv, task_id: str) -> list[dict[str, Any]]:
    response = await env.client.get(f"/api/v1/tasks/{task_id}/runs", headers=_h(env))
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def timeline(env: ImEnv, **params: Any) -> dict[str, Any]:
    response = await env.client.get("/api/v1/tasks/timeline", params=params, headers=_h(env))
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def set_online(env: ImEnv, instance_id: str) -> None:
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET status = 'online', last_seen_at = now() "
        "WHERE id = $1",
        uuid.UUID(instance_id),
    )


async def login(env: ImEnv, email: str, password: str) -> None:
    response = await env.client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text


async def audit(env: ImEnv, action: str) -> int:
    count: int = await run_sql(
        env.database_url, "SELECT count(*) FROM workspace.audit_logs WHERE action = $1", action
    )
    return count


def sends(env: ImEnv) -> list[dict[str, Any]]:
    return env.fake.paths("/msg/send_msg")


# ---------------------------------------------------------------- 任务的执行者与计划开始


async def test_assignee_and_planned_start(env: ImEnv) -> None:
    instance = await env.create_instance(name="甲")
    project = await add_project(env)
    task = await add_task(env, project["id"], "写周报")
    assert task["assignee_user_id"] is None and task["assignee_instance_id"] is None
    assert task["planned_start_at"] is None and task["started_at"] is None

    planned = "2026-11-05T01:00:00Z"
    updated = await patch(
        env, task["id"], {"planned_start_at": planned, "assignee_instance_id": instance["id"]}
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["planned_start_at"].startswith("2026-11-05T01:00:00")
    assert body["assignee_instance_id"] == instance["id"] and body["version"] != task["version"]

    # 执行者二选一：设成成员时实例自动清除
    to_user = await patch(env, task["id"], {"assignee_user_id": str(env.owner_id)})
    assert to_user.json()["assignee_user_id"] == str(env.owner_id)
    assert to_user.json()["assignee_instance_id"] is None

    both = await patch(
        env,
        task["id"],
        {"assignee_user_id": str(env.owner_id), "assignee_instance_id": instance["id"]},
    )
    assert both.status_code == 422
    stranger = await patch(env, task["id"], {"assignee_user_id": str(uuid.uuid4())})
    assert stranger.status_code == 422
    assert stranger.json()["error"]["details"]["errors"][0]["loc"] == ["body", "assignee_user_id"]

    # 其他工作空间的实例、停用的实例都不行
    other = await add_workspace(env.database_url, env.owner_id, "别处")
    elsewhere = await env.client.post(
        "/api/v1/agents/instances",
        json={"name": "别处的", "network_mode": "intranet"},
        headers={**_h(env, write=True), "X-Workspace-Id": str(other)},
    )
    assert elsewhere.status_code == 201, elsewhere.text
    foreign = await patch(env, task["id"], {"assignee_instance_id": elsewhere.json()["id"]})
    assert foreign.status_code == 422
    await env.set_enabled(instance["id"], False)
    disabled = await patch(env, task["id"], {"assignee_instance_id": instance["id"]})
    assert disabled.status_code == 422

    cleared = await patch(env, task["id"], {"assignee_user_id": None, "planned_start_at": None})
    assert cleared.status_code == 200
    assert cleared.json()["assignee_user_id"] is None and cleared.json()["planned_start_at"] is None
    assert (await patch(env, task["id"], {"title": None})).status_code == 422


# ---------------------------------------------------------------- 人的任务


async def test_human_runs_start_pause_replay_and_done(env: ImEnv) -> None:
    project = await add_project(env)
    task = await add_task(env, project["id"], "写周报")
    other = await add_task(env, project["id"], "评审")
    missing_key = await env.client.post(
        f"/api/v1/tasks/{task['id']}/runs", json={}, headers=_h(env, write=True)
    )
    assert missing_key.status_code == 422

    key = uuid.uuid4()
    started = await start(env, task["id"], key=key)
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["executor_kind"] == "user" and run["user_id"] == str(env.owner_id)
    assert run["ended_at"] is None and run["tokens"] is None and run["usage_reported"] is False
    after = await get_task(env, task["id"])
    # 没有执行者时，点“开始”的人成为执行者；started_at 记下，version 不变
    assert after["assignee_user_id"] == str(env.owner_id) and after["started_at"] is not None
    assert after["version"] == task["version"]

    replay = await start(env, task["id"], key=key)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == run["id"]
    reused = await start(env, other["id"], key=key)
    assert reused.status_code == 409 and code(reused) == "IDEMPOTENCY_KEY_REUSED"
    second = await start(env, task["id"])
    assert second.status_code == 409 and code(second) == "RUN_ALREADY_OPEN"

    paused = await stop(env, task["id"], run["id"], "paused")
    assert paused.status_code == 200, paused.text
    assert paused.json()["end_reason"] == "paused" and paused.json()["ended_by"] == str(
        env.owner_id
    )
    again = await stop(env, task["id"], run["id"], "cancelled")
    assert again.status_code == 200 and again.json()["end_reason"] == "paused"  # 已结束：原样返回
    assert (await stop(env, other["id"], run["id"], "paused")).status_code == 404

    resumed = await start(env, task["id"])
    assert resumed.status_code == 201
    done = await patch(env, task["id"], {"done": True})
    assert done.status_code == 200
    history = await runs(env, task["id"])
    assert [r["end_reason"] for r in history] == ["done", "paused"]  # 新的在前
    assert history[0]["ended_by"] == str(env.owner_id)
    closed = await start(env, task["id"])
    assert closed.status_code == 409 and code(closed) == "TASK_DONE"
    assert await audit(env, "task.run.started") == 2
    assert await audit(env, "task.run.stopped") == 1


async def test_roles_for_runs_and_timeline(env: ImEnv) -> None:
    instance = await env.create_instance(name="甲")
    await set_online(env, instance["id"])
    project = await add_project(env)
    human = await add_task(env, project["id"], "人的任务")
    agent = await add_task(env, project["id"], "智能体的任务")
    assert (
        await patch(env, agent["id"], {"assignee_instance_id": instance["id"]})
    ).status_code == 200
    dispatched = await start(env, agent["id"], dispatch=True)
    assert dispatched.status_code == 201, dispatched.text
    workspace = uuid.UUID(env.workspace_id)
    for email, role in (("viewer@example.com", "viewer"), ("member@example.com", "member")):
        user = await add_user(env.database_url, email, PASSWORD)
        await add_member(env.database_url, workspace, user, role)

    await login(env, "viewer@example.com", PASSWORD)
    assert (
        await env.client.get(f"/api/v1/tasks/{human['id']}/runs", headers=_h(env))
    ).status_code == 200
    assert (await env.client.get("/api/v1/tasks/timeline", headers=_h(env))).status_code == 200
    assert (await start(env, human["id"])).status_code == 403

    await login(env, "member@example.com", PASSWORD)
    member_run = await start(env, human["id"])
    assert member_run.status_code == 201, member_run.text
    assert (await stop(env, human["id"], member_run.json()["id"], "paused")).status_code == 200
    # 交给实例执行、结束实例执行的段：要 owner / admin
    forbidden = await start(env, agent["id"], dispatch=True)
    assert forbidden.status_code == 403
    assert (await stop(env, agent["id"], dispatched.json()["id"], "cancelled")).status_code == 403

    await login(env, OWNER["email"], OWNER["password"])
    ok = await stop(env, agent["id"], dispatched.json()["id"], "cancelled")
    assert ok.status_code == 200 and ok.json()["end_reason"] == "cancelled"
    # 其他工作空间看不到
    other = await add_workspace(env.database_url, env.owner_id, "别处")
    foreign = await env.client.get(
        f"/api/v1/tasks/{human['id']}/runs",
        headers={**_h(env), "X-Workspace-Id": str(other)},
    )
    assert foreign.status_code == 404


# ---------------------------------------------------------------- 交给实例执行


async def test_dispatch_sends_one_message_with_the_admins_account(env: ImEnv) -> None:
    instance = await env.create_instance(name="甲")
    project = await add_project(env)
    task = await add_task(env, project["id"], "整理发布说明", due_at="2026-11-06T10:00:00Z")
    unassigned = await start(env, task["id"], dispatch=True)
    assert unassigned.status_code == 409 and code(unassigned) == "NO_AGENT_ASSIGNEE"
    assert (
        await patch(env, task["id"], {"assignee_instance_id": instance["id"]})
    ).status_code == 200
    human = await start(env, task["id"])
    assert human.status_code == 409 and code(human) == "AGENT_TASK"
    offline = await start(env, task["id"], dispatch=True)
    assert offline.status_code == 409 and code(offline) == "AGENT_OFFLINE"
    assert sends(env) == []

    await set_online(env, instance["id"])
    key = uuid.uuid4()
    dispatched = await start(env, task["id"], dispatch=True, key=key)
    assert dispatched.status_code == 201, dispatched.text
    run = dispatched.json()
    assert run["executor_kind"] == "instance" and run["instance_id"] == instance["id"]
    assert run["executor_name"] == "甲" and run["started_by"] == str(env.owner_id)
    [message] = sends(env)
    assert message["sendID"] == admin_im_user_id(env.owner_id)
    assert message["recvID"] == instance["im_user_id"]
    text = message["content"]["content"]
    assert task["id"] in text and "整理发布说明" in text and "2026-11-06 10:00" in text
    assert "get_task" in text and "complete_task" in text

    replay = await start(env, task["id"], dispatch=True, key=key)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    open_again = await start(env, task["id"], dispatch=True)
    assert open_again.status_code == 409 and code(open_again) == "RUN_ALREADY_OPEN"
    assert len(sends(env)) == 1
    assert await audit(env, "task.run.dispatched") == 1

    # IM 结果未知：不开段；用同一个键重试，由 IM 接口查清后再开（不会发第二条）
    second = await add_task(env, project["id"], "写测试")
    assert (
        await patch(env, second["id"], {"assignee_instance_id": instance["id"]})
    ).status_code == 200
    env.fake.send_timeout = True
    unknown_key = uuid.uuid4()
    unknown = await start(env, second["id"], dispatch=True, key=unknown_key)
    assert unknown.status_code == 504 and code(unknown) == "RESULT_UNKNOWN"
    assert await runs(env, second["id"]) == []
    env.fake.send_timeout = False
    resolved = await start(env, second["id"], dispatch=True, key=unknown_key)
    assert resolved.status_code == 201, resolved.text
    assert len(sends(env)) == 2  # 第二个任务只发过一次（超时的那次）

    # OpenIM 不可用：503，不开段
    third = await add_task(env, project["id"], "第三个")
    assert (
        await patch(env, third["id"], {"assignee_instance_id": instance["id"]})
    ).status_code == 200
    env.fake.down = True
    down = await start(env, third["id"], dispatch=True)
    assert down.status_code == 503
    assert await runs(env, third["id"]) == []


# ---------------------------------------------------------------- 中断


async def test_runs_are_interrupted(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    project = await add_project(env)
    tasks = [await add_task(env, project["id"], f"任务 {i}") for i in range(4)]
    for task, instance in zip(tasks, (a, b, b, a), strict=True):
        await set_online(env, instance["id"])
        assert (
            await patch(env, task["id"], {"assignee_instance_id": instance["id"]})
        ).status_code == 200
    first = (await start(env, tasks[0]["id"], dispatch=True)).json()
    second = (await start(env, tasks[1]["id"], dispatch=True)).json()
    fourth = (await start(env, tasks[3]["id"], dispatch=True)).json()

    # 改执行者：原执行者正在执行的段中断
    reassigned = await patch(env, tasks[3]["id"], {"assignee_user_id": str(env.owner_id)})
    assert reassigned.status_code == 200
    [row] = await runs(env, tasks[3]["id"])
    assert row["id"] == fourth["id"] and row["end_reason"] == "interrupted"

    # 停用实例：它正在执行的段中断，系统结束（ended_by 为空）
    await env.set_enabled(a["id"], False)
    [row] = await runs(env, tasks[0]["id"])
    assert row["id"] == first["id"] and row["end_reason"] == "interrupted"
    assert row["ended_by"] is None

    # 离线 9 分钟：还在执行；离线 11 分钟：中断，结束在离线开始的时间
    started = datetime.now(UTC) - timedelta(minutes=30)
    await run_sql(
        env.database_url,
        "UPDATE workspace.task_runs SET started_at = $1 WHERE id = $2",
        started,
        uuid.UUID(second["id"]),
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET status = 'offline' WHERE id = $1",
        uuid.UUID(b["id"]),
    )
    redis = env.app.state.redis
    key = agent_watch.OFFLINE_SINCE_KEY.format(instance_id=b["id"])
    await redis.set(key, (datetime.now(UTC) - timedelta(minutes=9)).isoformat())
    ctx = await _worker_ctx(env)
    assert await worker.close_stale_runs(ctx) == 0
    since = datetime.now(UTC) - timedelta(minutes=11)
    await redis.set(key, since.isoformat())
    assert await worker.close_stale_runs(ctx) == 1
    [row] = await runs(env, tasks[1]["id"])
    assert row["end_reason"] == "interrupted"
    ended = datetime.fromisoformat(row["ended_at"])
    assert abs((ended - since).total_seconds()) < 1


# ---------------------------------------------------------------- 用量上报


async def _report(env: ImEnv, token: str, sessions: list[dict[str, Any]], **body: Any) -> Any:
    return await env.client.post(
        "/agent-api/v1/usage",
        json={"day": datetime.now(UTC).date().isoformat(), "sessions": sessions, **body},
        headers=agent_headers(token),
    )


def _s(session: str, i: int, o: int, cr: int = 0, cw: int = 0) -> dict[str, Any]:
    return {
        "session": session,
        "usage": {"input": i, "output": o, "cache_read": cr, "cache_write": cw},
    }


async def test_usage_report_counts_increments(env: ImEnv) -> None:
    instance = await env.create_instance(name="甲")
    token = await env.issue_token(instance["id"])
    first = await _report(env, token, [_s(SESSION_A, 10, 5)])
    assert first.status_code == 200, first.text
    # 第一次上报只记基准
    assert first.json() == {"accepted": 1, "skipped": 0, "tokens": 0, "baseline": True}

    second = await _report(
        env,
        token,
        [_s(SESSION_A, 15, 7, 1), _s(SESSION_B, 3, 3), {"session": SESSION_C, "usage": None}],
    )
    assert second.json() == {"accepted": 2, "skipped": 1, "tokens": 14, "baseline": False}
    smaller = await _report(env, token, [_s(SESSION_A, 4, 4)])  # 会话被压缩：以新值为准
    assert smaller.json()["tokens"] == 0
    grown = await _report(env, token, [_s(SESSION_A, 6, 4)])
    assert grown.json()["tokens"] == 2
    total = await run_sql(
        env.database_url,
        "SELECT sum(input + output + cache_read + cache_write) FROM workspace.agent_usage_minutes "
        "WHERE instance_id = $1",
        uuid.UUID(instance["id"]),
    )
    assert total == 16
    reported = await run_sql(
        env.database_url,
        "SELECT usage_reported_at IS NOT NULL FROM workspace.agent_instances WHERE id = $1",
        uuid.UUID(instance["id"]),
    )
    assert reported is True

    old = await _report(
        env, token, [], day=(datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    )
    assert old.status_code == 422
    for bad in (
        [{"session": "not-a-hash", "usage": None}],
        [
            {
                "session": SESSION_A,
                "usage": {"input": -1, "output": 0, "cache_read": 0, "cache_write": 0},
            }
        ],
        [{"session": SESSION_A, "usage": {"input": 1, "output": 0, "cache_read": 0}}],
        [{"session": SESSION_A, "usage": None, "key": "agent:main:x"}],
    ):
        assert (await _report(env, token, bad)).status_code == 422, bad
    unauthenticated = await env.client.post(
        "/agent-api/v1/usage", json={"day": "2026-11-04", "sessions": []}
    )
    assert unauthenticated.status_code == 401

    # 每个实例每分钟最多 6 次
    other = await env.create_instance(name="乙")
    other_token = await env.issue_token(other["id"])
    statuses = [(await _report(env, other_token, [])).status_code for _ in range(7)]
    assert statuses == [200] * 6 + [429]


# ---------------------------------------------------------------- 时间轴与 token 归属


async def test_timeline_attributes_tokens_by_time(env: ImEnv) -> None:
    instance = await env.create_instance(name="甲")
    instance_id = uuid.UUID(instance["id"])
    workspace = uuid.UUID(env.workspace_id)
    p1, p2 = await add_project(env, "发布"), await add_project(env, "文档")
    t1 = await add_task(env, p1["id"], "打包")
    t2 = await add_task(env, p2["id"], "写说明")
    t3 = await add_task(env, p1["id"], "计划中")
    t4 = await add_task(env, p2["id"], "只有截止")
    now = datetime.now(UTC)
    base = (now - timedelta(hours=2)).replace(second=0, microsecond=0)
    base = base - timedelta(minutes=base.minute % 10)

    def at(minutes: int) -> datetime:
        return base + timedelta(minutes=minutes)

    async def insert_run(task: dict[str, Any], start_min: int, end_min: int | None) -> uuid.UUID:
        run_id: uuid.UUID = await run_sql(
            env.database_url,
            "INSERT INTO workspace.task_runs (workspace_id, task_id, executor_kind, instance_id,"
            " executor_name, started_at, ended_at, end_reason) VALUES ($1, $2, 'instance', $3,"
            " '甲', $4, $5, $6) RETURNING id",
            workspace,
            uuid.UUID(task["id"]),
            instance_id,
            at(start_min),
            at(end_min) if end_min is not None else None,
            "paused" if end_min is not None else None,
        )
        return run_id

    r1 = await insert_run(t1, 0, 10)
    r2 = await insert_run(t2, 5, 20)
    for minute, tokens in ((2, (100, 0, 0, 0)), (7, (0, 60, 0, 0)), (15, (0, 0, 30, 0))):
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.agent_usage_minutes (instance_id, minute, workspace_id, input,"
            " output, cache_read, cache_write) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            instance_id,
            at(minute),
            workspace,
            *tokens,
        )
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET usage_reported_at = now() WHERE id = $1",
        instance_id,
    )
    # 等待审批：12 ~ 14 分钟，落在第二段里
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.ai_tool_executions (workspace_id, instance_id, instance_name,"
        " tool_name, risk_level, status, summary, arguments_hash, created_at, decided_at)"
        " VALUES ($1, $2, '甲', 'complete_task', 'write', 'succeeded', '完成', 'h', $3, $4)",
        workspace,
        instance_id,
        at(12),
        at(14),
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET done = true, done_at = $1, started_at = $2 WHERE id = $3",
        at(10),
        at(0),
        uuid.UUID(t1["id"]),
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET started_at = $1 WHERE id = $2",
        at(5),
        uuid.UUID(t2["id"]),
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET planned_start_at = $1, due_at = $2 WHERE id = $3",
        at(30),
        at(50),
        uuid.UUID(t3["id"]),
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET due_at = $1 WHERE id = $2",
        at(40),
        uuid.UUID(t4["id"]),
    )
    window = {"from": at(-60).isoformat(), "to": at(60).isoformat()}

    data = await timeline(env, **window)
    assert data["group"] == "executor" and data["usage"]["bucket_minutes"] == 5
    by_run = {s["run"]["id"]: s for s in data["segments"] if s["kind"] == "run"}
    one, two = by_run[str(r1)]["run"], by_run[str(r2)]["run"]
    # 第 2 分钟只有第一段；第 7 分钟两段平均分；第 15 分钟只有第二段（第一段 10 分钟结束）
    assert one["tokens"] == {
        "input": 100,
        "output": 30,
        "cache_read": 0,
        "cache_write": 0,
        "total": 130,
    }
    assert two["tokens"] == {
        "input": 0,
        "output": 30,
        "cache_read": 30,
        "cache_write": 0,
        "total": 60,
    }
    assert one["tokens_shared"] is True and two["tokens_shared"] is True
    assert one["waits"] == [] and [w["tool"] for w in two["waits"]] == ["complete_task"]
    assert two["waits"][0]["end"] is not None
    assert by_run[str(r1)]["track"] == f"instance:{instance['id']}"
    planned = {s["task_id"]: s for s in data["segments"] if s["kind"] == "planned"}
    assert set(planned) == {t3["id"], t4["id"]}
    assert planned[t4["id"]]["start"] is None and planned[t4["id"]]["end"] is not None
    assert planned[t3["id"]]["track"] == "unassigned"
    tracks = {t["key"]: t for t in data["tracks"]}
    assert tracks[f"instance:{instance['id']}"]["name"] == "甲" and "unassigned" in tracks
    usage = data["usage"]
    assert usage["scope"] == "workspace" and usage["reported"] is True
    assert usage["total"]["total"] == 190 and len(usage["buckets"]) == 3
    assert data["summary"] == {"running": 0, "done": 1, "tokens": 190, "overdue": 2}
    titles = {t["id"]: t for t in data["tasks"]}
    assert titles[t1["id"]]["project_title"] == "发布" and titles[t1["id"]]["done"] is True

    # 只看一个项目：片段、轨道、token 和概况都只算这个项目
    only = await timeline(env, project_id=p1["id"], **window)
    assert {s["task_id"] for s in only["segments"]} == {t1["id"], t3["id"]}
    assert only["usage"]["scope"] == "project" and only["usage"]["total"]["total"] == 130
    assert only["summary"] == {"running": 0, "done": 1, "tokens": 130, "overdue": 1}
    assert {t["kind"] for t in only["tracks"]} == {"instance", "unassigned"}

    grouped = await timeline(env, group="project", **window)
    assert {t["key"] for t in grouped["tracks"]} == {f"project:{p1['id']}", f"project:{p2['id']}"}
    assert by_run[str(r2)]["track"] != f"project:{p2['id']}"
    assert {s["track"] for s in grouped["segments"] if s["task_id"] == t2["id"]} == {
        f"project:{p2['id']}"
    }

    # 执行记录：每 10 分钟的用量
    [history] = await runs(env, t2["id"])
    assert [p["tokens"]["total"] for p in history["series"]] == [30, 30]

    # 范围与权限
    bad = await env.client.get(
        "/api/v1/tasks/timeline",
        params={"from": at(0).isoformat(), "to": at(0).isoformat()},
        headers=_h(env),
    )
    assert bad.status_code == 422
    too_long = await env.client.get(
        "/api/v1/tasks/timeline",
        params={"from": at(0).isoformat(), "to": (at(0) + timedelta(days=32)).isoformat()},
        headers=_h(env),
    )
    assert too_long.status_code == 422
    missing = await env.client.get(
        "/api/v1/tasks/timeline", params={"project_id": str(uuid.uuid4())}, headers=_h(env)
    )
    assert missing.status_code == 404
    other = await add_workspace(env.database_url, env.owner_id, "别处")
    elsewhere = await env.client.get(
        "/api/v1/tasks/timeline",
        params={**window, "project_id": p1["id"]},
        headers={**_h(env), "X-Workspace-Id": str(other)},
    )
    assert elsewhere.status_code == 404
    empty = await env.client.get(
        "/api/v1/tasks/timeline", params=window, headers={**_h(env), "X-Workspace-Id": str(other)}
    )
    assert empty.status_code == 200 and empty.json()["segments"] == []
    assert empty.json()["usage"]["total"]["total"] == 0


async def test_timeline_reports_no_usage_and_running_runs(env: ImEnv) -> None:
    instance = await env.create_instance(name="甲")
    await set_online(env, instance["id"])
    project = await add_project(env)
    task = await add_task(env, project["id"], "进行中")
    assert (
        await patch(env, task["id"], {"assignee_instance_id": instance["id"]})
    ).status_code == 200
    run = (await start(env, task["id"], dispatch=True)).json()
    assert run["usage_reported"] is False and run["tokens"] is None
    data = await timeline(env)
    assert data["usage"]["reported"] is False and data["summary"]["running"] == 1
    [segment] = [s for s in data["segments"] if s["kind"] == "run"]
    assert segment["end"] is None and segment["run"]["usage_reported"] is False
    assert [t["running"] for t in data["tasks"]] == [True]


async def test_assignee_options_list_members_and_live_instances(env: ImEnv) -> None:
    live = await env.create_instance(name="乙")
    stopped = await env.create_instance(name="甲")
    await env.set_enabled(stopped["id"], False)
    user = await add_user(env.database_url, "viewer@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user, "viewer")
    await login(env, "viewer@example.com", PASSWORD)
    response = await env.client.get("/api/v1/tasks/assignees", headers=_h(env))
    assert response.status_code == 200, response.text
    data = response.json()
    assert {u["id"] for u in data["users"]} == {str(env.owner_id), str(user)}
    assert [(i["id"], i["name"]) for i in data["instances"]] == [(live["id"], "乙")]
    other = await add_workspace(env.database_url, env.owner_id, "别处")
    foreign = await env.client.get(
        "/api/v1/tasks/assignees", headers={**_h(env), "X-Workspace-Id": str(other)}
    )
    assert foreign.status_code == 404
