"""多模型同问的接口（T-0232，docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）。

和 test_module_llm_bridge_chat.py 用同一套环境：真实 PostgreSQL / Redis，会话桥是
services/llm-bridge 的真实应用（进程内，两座），Claude 这个账号换成不访问外部服务的测试驱动。
"""

import uuid
from typing import Any

import httpx
import pytest

from tests.helpers import csrf
from tests.test_module_llm_bridge import SECOND_TOKEN, SECOND_URL, Rig, rig  # noqa: F401 - rig 是夹具
from tests.test_module_llm_bridge_chat import login_as

pytestmark = pytest.mark.integration
GROUPS = "/chat/groups"


async def second_bridge(rig: Rig) -> str:  # noqa: F811
    await rig.spawn("bridge2.test", SECOND_TOKEN)
    made = await rig.api(
        "POST", "/bridges", json={"name": "2 号", "base_url": SECOND_URL, "token": SECOND_TOKEN}
    )
    assert made.status_code == 201, made.text
    bridge_id: str = made.json()["id"]
    return bridge_id


def target(bridge_id: str, platform: str = "claude", **extra: Any) -> dict[str, Any]:
    return {"bridge_id": bridge_id, "platform": platform, "mode": "chat", **extra}


async def wait_done(rig: Rig, bridge_id: str, session_id: str) -> dict[str, Any]:  # noqa: F811
    """等这个会话没有回合在进行，返回它的内容。"""
    base = f"/bridges/{bridge_id}/chat/sessions/{session_id}"
    after = 0
    for _ in range(40):
        batch = (
            await rig.api("GET", f"{base}/events", params={"after": after, "wait_seconds": 5})
        ).json()
        after = batch["last_id"]
        if not batch["busy"] and not batch["events"]:
            view: dict[str, Any] = (await rig.api("GET", base)).json()
            return view
    raise AssertionError("会话一直没有停下来")


async def test_group_asks_several_models_across_managers(rig: Rig) -> None:  # noqa: F811
    """同一个问题同时发给两个管理器上的模型；某个目标建不成只影响它自己。"""
    env = rig.env
    second = await second_bridge(rig)
    question = "用两句话解释幂等"
    body = {
        "text": question,
        "request_id": "g1",
        # 第三个目标：1 号管理器的 ChatGPT 没有登录，会话建不成
        "targets": [target(rig.bridge_id), target(second), target(rig.bridge_id, "chatgpt")],
    }
    made = await rig.api("POST", GROUPS, json=body)
    assert made.status_code == 201, made.text
    group, results = made.json()["group"], made.json()["results"]
    assert (group["title"], group["status"], group["owner_kind"], group["owner"]) == (
        question,
        "active",
        "me",
        "Owner",
    )
    members = group["members"]
    assert [(m["position"], m["bridge_name"], m["platform"]) for m in members] == [
        (0, "主桥", "claude"),
        (1, "2 号", "claude"),
        (2, "主桥", "chatgpt"),
    ]
    assert members[0]["session_id"] and members[1]["session_id"]
    assert members[0]["bridge_id"] == rig.bridge_id and members[1]["bridge_id"] == second
    assert all(m["bridge_enabled"] for m in members)
    assert members[2]["session_id"] is None and members[2]["error"]
    assert [bool(r["turn"]) for r in results] == [True, True, False]
    assert results[2]["error"] == members[2]["error"]

    # 每个成员就是一个普通会话：内容用单会话的接口读，各自在自己的管理器上
    for member in members[:2]:
        view = await wait_done(rig, member["bridge_id"], member["session_id"])
        assert view["session"]["owner_kind"] == "me" and view["session"]["title"] == question
        assert [m["role"] for m in view["messages"]] == ["user", "assistant"]
        assert view["messages"][1]["status"] == "completed"
    first = (await rig.api("GET", f"/bridges/{rig.bridge_id}/chat/sessions")).json()["sessions"]
    other = (await rig.api("GET", f"/bridges/{second}/chat/sessions")).json()["sessions"]
    assert [s["session_id"] for s in first] == [members[0]["session_id"]]
    assert [s["session_id"] for s in other] == [members[1]["session_id"]]

    # 用同一个 request_id 再建一次：得到的是同一组，不会多出会话
    again = await rig.api("POST", GROUPS, json=body)
    assert again.status_code == 201 and again.json()["group"]["id"] == group["id"]
    assert again.json()["results"] == []
    assert len((await rig.api("GET", f"/bridges/{second}/chat/sessions")).json()["sessions"]) == 1

    listed = (await rig.api("GET", GROUPS)).json()["groups"]
    assert [g["id"] for g in listed] == [group["id"]]
    assert len(listed[0]["members"]) == 3
    assert (await rig.api("GET", f"{GROUPS}/{group['id']}")).json() == listed[0]
    assert (await rig.api("GET", f"{GROUPS}/{uuid.uuid4()}")).status_code == 404

    # 审计记了动作，不带问题的内容
    logs = (
        await env.client.get("/api/v1/audit-logs", params={"limit": 100}, headers=env.headers())
    ).json()
    assert "llm_bridge.chat_group_created" in [item["action"] for item in logs["items"]]
    assert "用两句话解释幂等" not in str(logs)


async def test_group_input_is_checked_before_anything_is_created(rig: Rig) -> None:  # noqa: F811
    one = target(rig.bridge_id)
    for targets, status in (
        ([one], 422),  # 至少两个
        ([one] * 7, 422),  # 最多六个
        ([one, target(str(uuid.uuid4()))], 404),  # 没有这个管理器
        ([one, {**one, "mode": "cloud"}], 422),
    ):
        refused = await rig.api(
            "POST", GROUPS, json={"text": "你好", "request_id": "bad", "targets": targets}
        )
        assert refused.status_code == status, refused.text
    # 一个会话都建不成：不留下空的一组
    nothing = await rig.api(
        "POST",
        GROUPS,
        json={
            "text": "你好",
            "request_id": "none",
            "targets": [target(rig.bridge_id, "chatgpt"), target(rig.bridge_id, "workbuddy")],
        },
    )
    assert nothing.status_code == 409 and "一个会话都没有建成" in nothing.json()["error"]["message"]
    # 停用的管理器不能参加
    off = await rig.api("PATCH", f"/bridges/{rig.bridge_id}", json={"enabled": False})
    assert off.status_code == 200
    disabled = await rig.api(
        "POST", GROUPS, json={"text": "你好", "request_id": "off", "targets": [one, one]}
    )
    assert disabled.status_code == 409 and disabled.json()["error"]["code"] == "BRIDGE_DISABLED"
    assert (await rig.api("GET", GROUPS, params={"status": "all"})).json()["groups"] == []
    assert (await rig.api("GET", f"/bridges/{rig.bridge_id}/chat/sessions")).json()[
        "sessions"
    ] == []


async def test_group_continue_takeover_archive(rig: Rig) -> None:  # noqa: F811
    """向一组继续发；别的管理员要确认；管理器停用或删除后那一栏给出原因；归档不影响会话。"""
    env = rig.env
    second = await second_bridge(rig)
    made = await rig.api(
        "POST",
        GROUPS,
        json={
            "title": "解释幂等",
            "text": "第一问",
            "request_id": "g1",
            "targets": [target(rig.bridge_id), target(second)],
        },
    )
    assert made.status_code == 201, made.text
    group = made.json()["group"]
    assert group["title"] == "解释幂等"
    sessions = [m["session_id"] for m in group["members"]]
    for bridge_id, session_id in zip((rig.bridge_id, second), sessions, strict=True):
        await wait_done(rig, bridge_id, session_id)
    path = f"{GROUPS}/{group['id']}"

    more = {"text": "第二问", "request_id": "g2"}
    sent = await rig.api("POST", f"{path}/messages", json=more)
    assert sent.status_code == 200, sent.text
    turns = [r["turn"]["turn_id"] for r in sent.json()["results"]]
    assert [r["turn"]["session_id"] for r in sent.json()["results"]] == sessions
    # 重试用同一个 request_id：每个会话都不会多一条
    again = await rig.api("POST", f"{path}/messages", json=more)
    assert [r["turn"]["turn_id"] for r in again.json()["results"]] == turns
    for bridge_id, session_id in zip((rig.bridge_id, second), sessions, strict=True):
        view = await wait_done(rig, bridge_id, session_id)
        assert [m["text"] for m in view["messages"] if m["role"] == "user"] == ["第一问", "第二问"]

    def headers(client: httpx.AsyncClient, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": env.workspace_id, **(csrf(client) if write else {})}

    api = "/api/v1/llm_bridge"
    async with login_as(rig, "admin@example.com", "admin") as admin:
        seen = (await admin.get(f"{api}{GROUPS}", headers=headers(admin))).json()["groups"]
        assert [(g["id"], g["owner_kind"], g["owner"]) for g in seen] == [
            (group["id"], "user", "Owner")
        ]
        third = {"text": "第三问", "request_id": "g3"}
        blocked = await admin.post(
            f"{api}{path}/messages", json=third, headers=headers(admin, write=True)
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "TAKEOVER_REQUIRED"
        confirmed = await admin.post(
            f"{api}{path}/messages",
            json={**third, "takeover": True},
            headers=headers(admin, write=True),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert all(r["turn"] for r in confirmed.json()["results"])
    async with login_as(rig, "member@example.com", "member") as member:
        assert (await member.get(f"{api}{GROUPS}", headers=headers(member))).status_code == 403
        refused = await member.post(
            f"{api}{path}/messages",
            json={"text": "x", "request_id": "m1", "takeover": True},
            headers=headers(member, write=True),
        )
        assert refused.status_code == 403
    for bridge_id, session_id in zip((rig.bridge_id, second), sessions, strict=True):
        view = await wait_done(rig, bridge_id, session_id)
        assert [m["by"] for m in view["messages"] if m["role"] == "user"][-1] == "Bob"

    # 2 号停用：它那一栏给出原因，另一栏照常
    assert (
        await rig.api("PATCH", f"/bridges/{second}", json={"enabled": False})
    ).status_code == 200
    partial = await rig.api("POST", f"{path}/messages", json={"text": "第四问", "request_id": "g4"})
    assert partial.status_code == 200, partial.text
    one, two = partial.json()["results"]
    assert one["turn"] and one["error"] == ""
    assert two["turn"] is None and "已停用" in two["error"]
    assert [m["bridge_enabled"] for m in partial.json()["group"]["members"]] == [True, False]
    await wait_done(rig, rig.bridge_id, sessions[0])
    # 2 号的登记删除：这一栏只剩当时的名字
    assert (await rig.api("DELETE", f"/bridges/{second}")).status_code == 204
    left = (await rig.api("GET", path)).json()["members"][1]
    assert (left["bridge_id"], left["bridge_name"], left["bridge_enabled"]) == (None, "2 号", False)
    assert left["session_id"] == sessions[1]
    gone = await rig.api("POST", f"{path}/messages", json={"text": "第五问", "request_id": "g5"})
    assert "已删除" in gone.json()["results"][1]["error"]
    await wait_done(rig, rig.bridge_id, sessions[0])

    # 归档：这一组不再列出、不能再发；每个模型的会话不受影响，还能单独继续
    archived = await rig.api("POST", f"{path}/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert (await rig.api("GET", GROUPS)).json()["groups"] == []
    kept = (await rig.api("GET", GROUPS, params={"status": "archived"})).json()["groups"]
    assert [g["id"] for g in kept] == [group["id"]]
    closed = await rig.api("POST", f"{path}/messages", json={"text": "x", "request_id": "g6"})
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "CONFLICT"
    alone = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/chat/sessions/{sessions[0]}/messages",
        json={"text": "单独继续", "request_id": "solo"},
    )
    assert alone.status_code == 200, alone.text

    logs = (
        await env.client.get("/api/v1/audit-logs", params={"limit": 100}, headers=env.headers())
    ).json()
    actions = [item["action"] for item in logs["items"]]
    for action in ("chat_group_sent", "chat_group_takeover_sent", "chat_group_archived"):
        assert f"llm_bridge.{action}" in actions, actions
    assert "第二问" not in str(logs) and "第三问" not in str(logs)
