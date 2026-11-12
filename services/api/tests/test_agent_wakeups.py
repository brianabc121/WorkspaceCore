"""唤醒（T-0121，docs/v2/AGENT_MESSAGING_PLAN.md 第 7.2 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。覆盖：

- 下一次的时间：每天、工作日、每周，按时区的钟点（跨夏令时不变）；
  会话键与 openclaw-channel 的写法一致；
- schedule_wakeup：时间范围、只能用自己的目标、最多 20 个待执行；cancel_wakeup 只能取消自己安排的；
  待执行的出现在 im_outbox 里；
- 管理员：可选的会话、建（审计）、取消；
- 实例领取：只领到期的、领取即执行中、消息里提示词在最后；报回结果，重复的排下一次；
  领取后太久没有结果记为失败、不重跑；别的实例领不到、报不了。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import agent_wakeups
from app.domain.im import admin_im_user_id
from tests.helpers import run_sql, tool_error
from tests.test_agent_outbox import _group, _ready_agent
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import call, ok

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


def test_next_run_keeps_the_local_clock() -> None:
    # 上海没有夏令时：每天 09:00（UTC 01:00）
    first = datetime(2026, 11, 6, 1, 0, tzinfo=UTC)
    nxt = agent_wakeups.next_run(first, "daily", "Asia/Shanghai", first)
    assert nxt == datetime(2026, 11, 7, 1, 0, tzinfo=UTC)
    # 工作日：周五之后是周一（2026-11-06 是周五）
    assert agent_wakeups.next_run(first, "weekdays", "Asia/Shanghai", first) == datetime(
        2026, 11, 9, 1, 0, tzinfo=UTC
    )
    assert agent_wakeups.next_run(first, "weekly", "UTC", first) == datetime(
        2026, 11, 13, 1, 0, tzinfo=UTC
    )
    # 纽约 2026-11-01 结束夏令时：09:00 前后都是本地 09:00（UTC 13:00 → 14:00）
    before = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
    assert agent_wakeups.next_run(before, "daily", "America/New_York", before) == datetime(
        2026, 11, 1, 14, 0, tzinfo=UTC
    )
    # 错过了很多次（例如实例离线一周）：排到现在之后，不补跑
    later = first + timedelta(days=10, hours=3)
    assert agent_wakeups.next_run(first, "daily", "UTC", later) == datetime(
        2026, 11, 17, 1, 0, tzinfo=UTC
    )
    assert agent_wakeups.next_run(first, "none", "UTC", first) is None


def test_session_keys_follow_the_channel_plugin() -> None:
    wid = uuid.uuid4()
    assert agent_wakeups.session_key("group", "G123abc", wid) == "openim:group:g123abc"
    assert agent_wakeups.session_key("user", "ws_ABC", wid) == "openim:direct:ws_abc"
    assert agent_wakeups.session_key(None, "", wid) == f"myworkspace-wakeup-{wid.hex}"


async def _claim(env: ImEnv, token: str) -> list[dict[str, Any]]:
    response = await env.client.post(
        "/agent-api/v1/wakeups/claim", json={}, headers=agent_headers(token)
    )
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def _make_due(env: ImEnv, *ids: str) -> None:
    """让待执行的唤醒到期（给了 ID 就只让这几个到期）。"""
    if ids:
        await run_sql(
            env.database_url,
            "UPDATE workspace.agent_wakeups SET run_at = now() - interval '1 second'"
            " WHERE id = ANY($1::uuid[])",
            [uuid.UUID(i) for i in ids],
        )
        return
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_wakeups SET run_at = now() - interval '1 second'"
        " WHERE status = 'pending'",
    )


async def test_agent_schedules_and_cancels_its_own_wakeups(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    group = await _group(env, [a["id"]])
    soon = await call(env, token, "schedule_wakeup", {"prompt": "x", "in_minutes": 1})
    assert tool_error(soon)["code"] == "VALIDATION_FAILED"
    far = (datetime.now(UTC) + timedelta(days=31)).isoformat()
    assert (
        tool_error(await call(env, token, "schedule_wakeup", {"prompt": "x", "at": far}))["code"]
        == "VALIDATION_FAILED"
    )
    both = await call(env, token, "schedule_wakeup", {"prompt": "x", "in_minutes": 10, "at": far})
    assert tool_error(both)["code"] == "VALIDATION_FAILED"
    other = await call(
        env, token, "schedule_wakeup", {"prompt": "x", "in_minutes": 10, "target": "group:nope"}
    )
    assert tool_error(other)["code"] == "TARGET_NOT_ALLOWED"

    scheduled = ok(
        await call(
            env,
            token,
            "schedule_wakeup",
            {
                "prompt": "去群里说一下昨天的进展",
                "in_minutes": 30,
                "target": f"group:{group['group_id']}",
            },
        )
    )
    assert scheduled["status"] == "scheduled" and scheduled["pending"] == 1
    key = await run_sql(
        env.database_url,
        "SELECT session_key FROM workspace.agent_wakeups WHERE id = $1",
        uuid.UUID(scheduled["wakeup_id"]),
    )
    assert key == f"openim:group:{group['group_id']}".lower()
    outbox = ok(await call(env, token, "im_outbox", {}))
    assert [w["wakeup_id"] for w in outbox["wakeups"]] == [scheduled["wakeup_id"]]
    assert outbox["wakeups"][0]["untrusted"]["text"] == "去群里说一下昨天的进展"

    # 最多 20 个待执行
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.agent_wakeups (workspace_id, instance_id, source, session_key,"
        " prompt, run_at) SELECT workspace_id, id, 'agent', 'k', 'p', now() + interval '1 day'"
        " FROM workspace.agent_instances, generate_series(1, 19) WHERE id = $1",
        uuid.UUID(a["id"]),
    )
    full = await call(env, token, "schedule_wakeup", {"prompt": "x", "in_minutes": 10})
    assert tool_error(full)["code"] == "RATE_LIMITED"

    cancelled = ok(await call(env, token, "cancel_wakeup", {"wakeup_id": scheduled["wakeup_id"]}))
    assert cancelled["status"] == "cancelled"
    again = await call(env, token, "cancel_wakeup", {"wakeup_id": scheduled["wakeup_id"]})
    assert tool_error(again)["code"] == "CONFLICT"
    # 别人的（管理员建的）不能取消
    # 管理员可以排到 31 天后（最远一年）
    admin_made = await env.client.post(
        f"/api/v1/agents/instances/{a['id']}/wakeups",
        json={"prompt": "日报", "run_at": far},
        headers=env.headers(write=True),
    )
    assert admin_made.status_code == 201, admin_made.text
    denied = await call(env, token, "cancel_wakeup", {"wakeup_id": admin_made.json()["id"]})
    assert tool_error(denied)["code"] == "NOT_FOUND"


async def test_admin_wakeups_targets_and_audit(env: ImEnv) -> None:
    a, _ = await _ready_agent(env, "甲")
    group = await _group(env, [a["id"]])
    listed = await env.client.get(
        f"/api/v1/agents/instances/{a['id']}/wakeups", headers=env.headers()
    )
    assert listed.status_code == 200, listed.text
    targets = {t["target"]: t["label"] for t in listed.json()["targets"]}
    owner = admin_im_user_id(env.owner_id)
    assert f"user:{owner}" in targets and f"group:{group['group_id']}" in targets
    run_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    bad_zone = await env.client.post(
        f"/api/v1/agents/instances/{a['id']}/wakeups",
        json={"prompt": "日报", "run_at": run_at, "time_zone": "Mars/Base"},
        headers=env.headers(write=True),
    )
    assert bad_zone.status_code == 422
    created = await env.client.post(
        f"/api/v1/agents/instances/{a['id']}/wakeups",
        json={
            "prompt": "工作日 9 点在项目群发昨天的进展",
            "target": f"group:{group['group_id']}",
            "run_at": run_at,
            "repeat": "weekdays",
            "time_zone": "Asia/Shanghai",
        },
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["source"], body["repeat"], body["status"]) == ("admin", "weekdays", "pending")
    cancel = await env.client.post(
        f"/api/v1/agents/instances/{a['id']}/wakeups/{body['id']}/cancel",
        headers=env.headers(write=True),
    )
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
    actions = await run_sql(
        env.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.wakeup.%'",
    )
    assert actions == "agents.wakeup.created,agents.wakeup.cancelled"


async def test_instances_claim_due_wakeups_and_report(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    _, token_b = await _ready_agent(env, "乙")
    owner = admin_im_user_id(env.owner_id)
    once = ok(
        await call(
            env,
            token,
            "schedule_wakeup",
            {"prompt": "TOOL:im_send 提醒管理员交周报", "in_minutes": 5, "target": f"user:{owner}"},
        )
    )
    repeat = await env.client.post(
        f"/api/v1/agents/instances/{a['id']}/wakeups",
        json={
            "prompt": "每天看一眼任务",
            "run_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "repeat": "daily",
        },
        headers=env.headers(write=True),
    )
    assert repeat.status_code == 201
    assert await _claim(env, token) == []  # 还没到点
    await _make_due(env)
    assert await _claim(env, token_b) == []  # 别的实例领不到
    claimed = {item["id"]: item for item in await _claim(env, token)}
    assert set(claimed) == {once["wakeup_id"], repeat.json()["id"]}
    first = claimed[once["wakeup_id"]]
    assert first["session_key"] == f"openim:direct:{owner}".lower()
    assert first["message"].startswith("[工作台唤醒] 这是你自己安排")
    assert first["message"].endswith("TOOL:im_send 提醒管理员交周报")  # 提示词在最后
    assert claimed[repeat.json()["id"]]["session_key"].startswith("myworkspace-wakeup-")
    assert await _claim(env, token) == []  # 领取即执行中，不会再发

    # 别的实例报不了
    stranger = await env.client.post(
        f"/agent-api/v1/wakeups/{once['wakeup_id']}/result",
        json={"status": "done"},
        headers=agent_headers(token_b),
    )
    assert stranger.status_code == 404
    done = await env.client.post(
        f"/agent-api/v1/wakeups/{once['wakeup_id']}/result",
        json={"status": "done"},
        headers=agent_headers(token),
    )
    assert done.status_code == 200 and done.json()["status"] == "done"
    failed = await env.client.post(
        f"/agent-api/v1/wakeups/{repeat.json()['id']}/result",
        json={"status": "failed", "error": "openclaw agent exited 1"},
        headers=agent_headers(token),
    )
    # 重复的：失败也排下一次
    assert failed.status_code == 200 and failed.json()["status"] == "pending"
    assert datetime.fromisoformat(failed.json()["run_at"]) > datetime.now(UTC)
    row = await run_sql(
        env.database_url,
        "SELECT runs || ':' || last_error FROM workspace.agent_wakeups WHERE id = $1",
        uuid.UUID(repeat.json()["id"]),
    )
    assert row == "1:openclaw agent exited 1"

    # 领取后 15 分钟没有结果：记为失败，不重跑
    stale = ok(await call(env, token, "schedule_wakeup", {"prompt": "卡住的", "in_minutes": 5}))
    await _make_due(env, stale["wakeup_id"])
    assert [i["id"] for i in await _claim(env, token)] == [stale["wakeup_id"]]
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_wakeups SET claimed_at = now() - interval '16 minutes'"
        " WHERE id = $1",
        uuid.UUID(stale["wakeup_id"]),
    )
    assert await _claim(env, token) == []
    status = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.agent_wakeups WHERE id = $1",
        uuid.UUID(stale["wakeup_id"]),
    )
    assert status == "failed"
