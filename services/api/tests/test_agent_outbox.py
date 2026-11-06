"""智能体主动发消息：发件箱（T-0118，docs/v2/AGENT_MESSAGING_PLAN.md 第 4.1、5、6 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖：
- `im_send` 以实例身份经 /msg/send_msg 发出：ex 里带发件箱 ID，投影里先有“发送中”
  再变成已发出，写执行记录；
- 幂等：同一个 client_key 同样内容返回同一次结果、不重复发；内容不同报 IDEMPOTENCY_MISMATCH；
- 群里 @：只能 @ 群成员，私聊不能 @；
- 预检：接力暂停（5001）当场拒收、不打扰 OpenIM；每分钟上限（5002）排队或按要求直接失败；
- OpenIM 拒收（errCode）→ rejected 与中文原因；连不上 → 退避重试，恢复后由 Worker 发出；
  应答超时 → 结果未知，对账找到那条消息后记为已发出、不重发；
- 定时发送与取消、`im_outbox` 查询、每小时限额；
- before 回调拒收实例的自动回复：记录拒收；5002 的文字回复排队补发；im_send 发的不重复记录。
"""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import agent_outbox
from app.domain.im import admin_im_user_id, openim_client
from app.domain.im_callbacks import RATE_KEY, STREAK_KEY
from app.domain.im_projection import verified_ex_key
from tests import fake_openim
from tests.helpers import run_sql, tool_error
from tests.test_im_accounts_callbacks import GROUP, ImEnv, im_env, msg
from tests.test_task_claims_api import agent, call, ok

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def _ready_agent(env: ImEnv, name: str) -> tuple[dict[str, Any], str]:
    created, token = await agent(env, name)
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET im_ready = true WHERE id = $1",
        uuid.UUID(created["id"]),
    )
    return created, token


async def _group(env: ImEnv, instance_ids: list[str]) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/im/groups",
        json={"name": "发布群", "instance_ids": instance_ids, "user_ids": [str(env.owner_id)]},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    group: dict[str, Any] = response.json()
    return group


async def _deliver_due(env: ImEnv) -> int:
    app = env.app
    client = openim_client(app.state.settings, app.state.connectors, app.state.redis)
    async with app.state.sessionmaker() as db:
        ctx = agent_outbox.SendContext(db, app.state.settings, app.state.redis, client)
        return await agent_outbox.AgentOutbox(ctx).deliver_due()


async def _make_due(env: ImEnv) -> None:
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_im_outbox SET next_attempt_at = now() - interval '1 second',"
        " updated_at = now() - interval '10 minutes'"
        " WHERE status IN ('queued', 'scheduled', 'unknown')",
    )


def _sent_by(env: ImEnv, conversation: str, sender: str) -> list[dict[str, Any]]:
    return [m for m in env.fake.messages.get(conversation, []) if m["sendID"] == sender]


def _texts(env: ImEnv, conversation: str, sender: str) -> list[str]:
    """模拟 OpenIM 存的 content 是 base64 编码的 JSON。"""
    out = []
    for m in _sent_by(env, conversation, sender):
        content = json.loads(base64.b64decode(m["content"]))
        out.append(content.get("content") or content.get("text") or "")
    return out


async def test_send_to_admin_idempotent_and_recorded(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    body = {"target": f"user:{owner}", "text": "周报写好了", "client_key": "weekly-report-1"}
    sent = ok(await call(env, token, "im_send", body))
    assert sent["status"] == "sent" and sent["replayed"] is False
    message = sent["messages"][0]
    assert message["status"] == "sent" and message["kind"] == "text"
    conv = "si_" + "_".join(sorted([a["im_user_id"], owner]))
    delivered = _sent_by(env, conv, a["im_user_id"])
    assert len(delivered) == 1
    ex = json.loads(delivered[0]["ex"])
    assert ex["myws_key"] == message["message_id"]
    # 工作台签名：回调据此认出是发件箱发的（T-0119）
    key = verified_ex_key(fake_openim.SECRET, delivered[0]["ex"])
    assert key == uuid.UUID(message["message_id"])
    local = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.im_messages WHERE idempotency_key = $1",
        uuid.UUID(message["message_id"]),
    )
    assert local == "sent"
    # 同一个键、同样内容：返回同一次结果，不重复发
    again = ok(await call(env, token, "im_send", body))
    assert again["replayed"] is True
    assert again["messages"][0]["message_id"] == message["message_id"]
    assert len(_sent_by(env, conv, a["im_user_id"])) == 1
    mismatch = tool_error(await call(env, token, "im_send", {**body, "text": "改了"}))
    assert mismatch["code"] == "IDEMPOTENCY_MISMATCH"
    recorded = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions"
        " WHERE tool_name = 'im_send' AND status = 'succeeded'",
    )
    assert recorded == 2
    listed = ok(await call(env, token, "im_outbox", {}))
    assert [i["message_id"] for i in listed["items"]] == [message["message_id"]]
    assert listed["items"][0]["untrusted"]["text"] == "周报写好了"


async def test_group_mentions_precheck_and_rate_limit(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    b, _ = await _ready_agent(env, "乙")
    group = await _group(env, [a["id"], b["id"]])
    gid = group["group_id"]
    target = f"group:{gid}"
    sent = ok(
        await call(
            env,
            token,
            "im_send",
            {"target": target, "text": "@乙 看一下", "mention": [b["im_user_id"]]},
        )
    )
    assert sent["status"] == "sent"
    stored = _sent_by(env, f"sg_{gid}", a["im_user_id"])[-1]
    assert stored["contentType"] == 106
    outsider = tool_error(
        await call(env, token, "im_send", {"target": target, "text": "x", "mention": ["oc_nobody"]})
    )
    assert outsider["code"] == "VALIDATION_FAILED"
    owner = admin_im_user_id(env.owner_id)
    dm_mention = tool_error(
        await call(
            env, token, "im_send", {"target": f"user:{owner}", "text": "x", "mention": [owner]}
        )
    )
    assert dm_mention["code"] == "VALIDATION_FAILED"
    # 接力暂停：当场拒收，不发给 OpenIM
    redis = env.app.state.redis
    before = len(_sent_by(env, f"sg_{gid}", a["im_user_id"]))
    await redis.set(STREAK_KEY.format(group_id=gid), 8)
    paused = ok(await call(env, token, "im_send", {"target": target, "text": "还在吗"}))
    assert paused["status"] == "rejected"
    assert paused["messages"][0]["code"] == 5001 and "暂停" in paused["messages"][0]["reason"]
    assert len(_sent_by(env, f"sg_{gid}", a["im_user_id"])) == before
    await redis.delete(STREAK_KEY.format(group_id=gid))
    # 每分钟上限：默认排队，fail 时直接返回
    await redis.set(RATE_KEY.format(group_id=gid, sender=a["im_user_id"]), 6, ex=30)
    queued = ok(await call(env, token, "im_send", {"target": target, "text": "排队"}))
    assert queued["status"] == "queued" and queued["messages"][0]["code"] == 5002
    assert 0 < queued["messages"][0]["retry_after_seconds"] <= 30
    failed = ok(
        await call(
            env, token, "im_send", {"target": target, "text": "不排队", "if_rate_limited": "fail"}
        )
    )
    assert failed["status"] == "rejected" and failed["messages"][0]["code"] == 5002
    await redis.delete(RATE_KEY.format(group_id=gid, sender=a["im_user_id"]))
    await _make_due(env)
    assert await _deliver_due(env) == 1
    texts = _texts(env, f"sg_{gid}", a["im_user_id"])
    assert "排队" in texts and "不排队" not in texts


async def test_rejections_retries_and_unknown(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    target = f"user:{owner}"
    conv = "si_" + "_".join(sorted([a["im_user_id"], owner]))
    env.fake.send_error = 5005
    rejected = ok(await call(env, token, "im_send", {"target": target, "text": "被拒"}))
    assert rejected["status"] == "rejected" and rejected["messages"][0]["code"] == 5005
    failed_local = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.im_messages WHERE idempotency_key = $1",
        uuid.UUID(rejected["messages"][0]["message_id"]),
    )
    assert failed_local == "failed"
    env.fake.send_error = 0
    # 连不上：退避重试，恢复后由 Worker 发出
    env.fake.refuse = True
    queued = ok(await call(env, token, "im_send", {"target": target, "text": "重试"}))
    assert queued["status"] == "queued" and queued["hint"]
    env.fake.refuse = False
    await _make_due(env)
    assert await _deliver_due(env) == 1
    assert _texts(env, conv, a["im_user_id"]) == ["重试"]
    # 应答超时：结果未知，对账找到那条消息就记为已发出，不重发
    env.fake.send_timeout = True
    unknown = ok(await call(env, token, "im_send", {"target": target, "text": "超时"}))
    assert unknown["messages"][0]["status"] == "unknown"
    env.fake.send_timeout = False
    await _make_due(env)
    await _deliver_due(env)
    status = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.agent_im_outbox WHERE id = $1",
        uuid.UUID(unknown["messages"][0]["message_id"]),
    )
    assert status == "sent"
    assert _texts(env, conv, a["im_user_id"]) == [
        "重试",
        "超时",
    ]


async def test_schedule_cancel_and_hourly_limit(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    scheduled = ok(
        await call(
            env, token, "im_send", {"target": f"user:{owner}", "text": "提醒", "send_at": later}
        )
    )
    assert scheduled["status"] == "scheduled"
    message_id = scheduled["messages"][0]["message_id"]
    cancelled = ok(
        await call(env, token, "im_cancel", {"message_ids": [message_id, str(uuid.uuid4())]})
    )
    assert cancelled["cancelled"] == [message_id] and len(cancelled["not_cancellable"]) == 1
    too_far = tool_error(
        await call(
            env,
            token,
            "im_send",
            {
                "target": f"user:{owner}",
                "text": "x",
                "send_at": (datetime.now(UTC) + timedelta(days=31)).isoformat(),
            },
        )
    )
    assert too_far["code"] == "VALIDATION_FAILED"
    await env.app.state.redis.set(f"im_send_hour:{a['id']}", 60, ex=3600)
    limited = tool_error(
        await call(env, token, "im_send", {"target": f"user:{owner}", "text": "多"})
    )
    assert limited["code"] == "RATE_LIMITED"
    listed = ok(await call(env, token, "im_list_targets", {"kind": "direct"}))
    assert listed["sends_left_this_hour"] == 0


async def test_callback_rejections_are_recorded_and_retried(env: ImEnv) -> None:
    a, token = await _ready_agent(env, "甲")
    group = await _group(env, [a["id"]])
    gid = group["group_id"]
    redis = env.app.state.redis
    await redis.set(RATE_KEY.format(group_id=gid, sender=a["im_user_id"]), 6, ex=5)
    payload = msg(a["im_user_id"], group_id=gid)
    payload["content"] = json.dumps({"content": "自动回复"})
    assert await env.verdict(GROUP, payload) == 5002
    rows = ok(await call(env, token, "im_outbox", {}))
    assert rows["rejected_replies"][0]["code"] == 5002
    assert rows["rejected_replies"][0]["retried"] is True
    assert rows["rejected_replies"][0]["untrusted"]["text"] == "自动回复"
    retry = [i for i in rows["items"] if i["source"] == "reply_retry"]
    assert len(retry) == 1 and retry[0]["status"] == "queued"
    listed = ok(await call(env, token, "im_list_targets", {"kind": "group"}))
    assert listed["recent_rejections"] == 1
    # 限额恢复后补发
    await redis.delete(RATE_KEY.format(group_id=gid, sender=a["im_user_id"]))
    await _make_due(env)
    await _deliver_due(env)
    assert _texts(env, f"sg_{gid}", a["im_user_id"]) == ["自动回复"]
    # im_send 发的消息被回调拒收时，ex 里有发件箱 ID：不重复记录
    await redis.set(STREAK_KEY.format(group_id=gid), 8)
    own = msg(a["im_user_id"], group_id=gid)
    own["ex"] = json.dumps({"myws_key": str(uuid.uuid4())})
    assert await env.verdict(GROUP, own) == 5001
    count = await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_im_rejections")
    assert count == 1
