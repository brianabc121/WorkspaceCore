"""大模型聚合聊天页面的接口（T-0231，docs/v2/LLM_CHAT_PLAN.md）。

和 test_module_llm_bridge.py 用同一套环境：真实 PostgreSQL / Redis，会话桥是 services/llm-bridge 的
真实应用（进程内），Claude 这个账号换成不访问外部服务的测试驱动。覆盖的是整条链路：
浏览器的请求 → 工作台接口（角色、接手确认、审计）→ 会话桥（会话归属、事件）→ 驱动。
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from tests.helpers import add_member, add_user, csrf, tool_error
from tests.test_mcp_api import SETTINGS, call, issue_token, new_instance
from tests.test_module_llm_bridge import Rig, ok, rig  # noqa: F401 - rig 是夹具
from tests.workspace_support import CONNECTOR_SETTINGS

pytestmark = pytest.mark.integration
MISSING = "bs_" + "0" * 20


@asynccontextmanager
async def login_as(rig: Rig, email: str, role: str) -> AsyncIterator[httpx.AsyncClient]:  # noqa: F811
    """以另一个成员身份登录；用和夹具一样的设置（同一把凭证密钥）和通往会话桥的传输。"""
    env = rig.env
    password = "member password 123456"  # noqa: S105 - 测试密码
    user_id = await add_user(env.database_url, email, password)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user_id, role)
    async with env.make_client(**{**CONNECTOR_SETTINGS, **SETTINGS}) as other:
        other._transport.app.state.connectors.transport = rig.transport  # type: ignore[attr-defined]
        login = await other.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        yield other


def chat(rig: Rig, suffix: str = "") -> str:  # noqa: F811
    return f"/bridges/{rig.bridge_id}/chat{suffix}"


async def wait_idle(rig: Rig, session_id: str, after: int = 0) -> list[dict[str, Any]]:  # noqa: F811
    """用长轮询读事件，直到这个会话没有回合在进行。"""
    events: list[dict[str, Any]] = []
    for _ in range(40):
        batch = await rig.api(
            "GET",
            chat(rig, f"/sessions/{session_id}/events"),
            params={"after": after, "wait_seconds": 5},
        )
        assert batch.status_code == 200, batch.text
        data = batch.json()
        events += data["events"]
        after = data["last_id"]
        if not data["busy"] and not data["events"]:
            return events
    raise AssertionError(f"会话一直没有停下来：{[e['kind'] for e in events]}")


async def new_chat(rig: Rig, text: str | None = "你好", **extra: Any) -> dict[str, Any]:  # noqa: F811
    body: dict[str, Any] = {"platform": "claude", "mode": "chat", **extra}
    if text is not None:
        body |= {"text": text, "request_id": f"r-{uuid.uuid4().hex[:8]}"}
    created = await rig.api("POST", chat(rig, "/sessions"), json=body)
    assert created.status_code == 201, created.text
    data: dict[str, Any] = created.json()
    return data


async def test_chat_create_ask_and_read(rig: Rig) -> None:  # noqa: F811
    """新建会话并提问：回答陆续出现，会话内容里有谁发的、回答和过程。"""
    # 可选的模型来自平台；没登录的平台读不到模型，但不算页面出错
    options = (
        await rig.api("GET", chat(rig, "/options"), params={"platform": "claude", "mode": "chat"})
    ).json()
    assert options["models"][0]["default"] and options["models_error"] == ""
    assert options["environments"] == []
    codex = (
        await rig.api("GET", chat(rig, "/options"), params={"platform": "codex", "mode": "chat"})
    ).json()
    assert codex["models"] == [] and codex["models_error"]

    created = await new_chat(rig, "work:0.3", title="看一下登录")
    session = created["session"]
    assert (session["owner_kind"], session["owner"], session["title"]) == (
        "me",
        "Owner",
        "看一下登录",
    )
    assert created["send_error"] == "" and created["turn"]["status"] in ("queued", "running")
    session_id = session["session_id"]

    kinds = [e["kind"] for e in await wait_idle(rig, session_id)]
    assert kinds == [
        "created",
        "queued",
        "started",
        "message",
        "command",
        "files",
        "message",
        "completed",
    ]

    view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
    user, reply = view["messages"]
    assert (user["role"], user["text"], user["by"], user["mine"]) == (
        "user",
        "work:0.3",
        "Owner",
        True,
    )
    assert (reply["role"], reply["status"]) == ("assistant", "completed")
    assert reply["text"].endswith("work:0.3")
    assert [(s["kind"], s["text"]) for s in reply["steps"]] == [
        ("message", "先看一下代码"),
        ("command", "ls src"),
        ("files", "src/a.py"),
    ]
    assert view["session"]["busy"] is False and view["session"]["turns"] == 1

    # 接着问：立刻返回回合，不等回答
    sent = await rig.api(
        "POST",
        chat(rig, f"/sessions/{session_id}/messages"),
        json={"text": "大家好", "request_id": "r2"},
    )
    assert sent.status_code == 200 and sent.json()["status"] in ("queued", "running")
    # 同一个 request_id 重发不会多一条
    again = await rig.api(
        "POST",
        chat(rig, f"/sessions/{session_id}/messages"),
        json={"text": "大家好", "request_id": "r2"},
    )
    assert again.json()["turn_id"] == sent.json()["turn_id"]
    await wait_idle(rig, session_id, view["last_event_id"])
    view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
    assert [m["text"] for m in view["messages"] if m["role"] == "user"] == ["work:0.3", "大家好"]

    # 列表：搜索、筛选
    listed = (await rig.api("GET", chat(rig, "/sessions"))).json()
    assert [s["session_id"] for s in listed["sessions"]] == [session_id]
    assert (await rig.api("GET", chat(rig, "/sessions"), params={"q": "登录"})).json()["sessions"]
    assert (await rig.api("GET", chat(rig, "/sessions"), params={"q": "没有"})).json()[
        "sessions"
    ] == []
    assert (await rig.api("GET", chat(rig, "/sessions"), params={"platform": "workbuddy"})).json()[
        "sessions"
    ] == []

    # 校验：带第一条消息要有 request_id；不认识的平台、格式不对的会话编号
    bad = await rig.api(
        "POST", chat(rig, "/sessions"), json={"platform": "claude", "mode": "chat", "text": "x"}
    )
    assert bad.status_code == 422
    assert (
        await rig.api("POST", chat(rig, "/sessions"), json={"platform": "fake", "mode": "chat"})
    ).status_code == 422
    assert (await rig.api("GET", chat(rig, "/sessions/not-an-id"))).status_code == 422
    assert (await rig.api("GET", chat(rig, f"/sessions/{MISSING}"))).status_code == 404


async def test_chat_stop_rename_archive(rig: Rig) -> None:  # noqa: F811
    created = await new_chat(rig, "sleep:30")
    session_id, turn_id = created["session"]["session_id"], created["turn"]["turn_id"]
    stopped = await rig.api("POST", chat(rig, f"/turns/{turn_id}/cancel"))
    assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
    await wait_idle(rig, session_id)
    view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
    assert view["messages"][-1]["status"] == "cancelled"

    # 失败的回合：原因如实给出
    failed = await rig.api(
        "POST",
        chat(rig, f"/sessions/{session_id}/messages"),
        json={"text": "fail:platform_error", "request_id": "f1"},
    )
    assert failed.status_code == 200
    await wait_idle(rig, session_id, view["last_event_id"])
    view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
    last = view["messages"][-1]
    assert last["status"] == "failed" and last["failure"]["kind"] == "platform_error"

    renamed = await rig.api("PATCH", chat(rig, f"/sessions/{session_id}"), json={"title": "新标题"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "新标题"
    assert (
        await rig.api("PATCH", chat(rig, f"/sessions/{session_id}"), json={"title": ""})
    ).status_code == 422

    archived = await rig.api("POST", chat(rig, f"/sessions/{session_id}/archive"))
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    # 归档后不能再发；默认的列表里不再出现，按“已归档”能筛出来
    closed = await rig.api(
        "POST",
        chat(rig, f"/sessions/{session_id}/messages"),
        json={"text": "还在吗", "request_id": "c1"},
    )
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "CONFLICT"
    assert (await rig.api("GET", chat(rig, "/sessions"))).json()["sessions"] == []
    only = (await rig.api("GET", chat(rig, "/sessions"), params={"status": "archived"})).json()
    assert [s["session_id"] for s in only["sessions"]] == [session_id]

    # 停用的管理器：可以看，不能新建和发送
    off = await rig.api("PATCH", f"/bridges/{rig.bridge_id}", json={"enabled": False})
    assert off.status_code == 200
    assert (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).status_code == 200
    refused = await rig.api(
        "POST", chat(rig, "/sessions"), json={"platform": "claude", "mode": "chat"}
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "BRIDGE_DISABLED"


async def test_chat_queue_view_edit_and_reorder(rig: Rig) -> None:  # noqa: F811
    """会话在处理时再发的消息进队列：能看、能改内容、能置顶；已经开始的不能改。"""
    created = await new_chat(rig, "sleep:1.5")
    session_id, running = created["session"]["session_id"], created["turn"]["turn_id"]
    turns = []
    for index, text in enumerate(("第二条", "第三条")):
        sent = await rig.api(
            "POST",
            chat(rig, f"/sessions/{session_id}/messages"),
            json={"text": text, "request_id": f"q{index}"},
        )
        assert sent.status_code == 200 and sent.json()["status"] == "queued", sent.text
        turns.append(sent.json()["turn_id"])
    second, third = turns
    queue = (await rig.api("GET", chat(rig, f"/sessions/{session_id}/queue"))).json()
    assert queue["running"]["turn_id"] == running and queue["running"]["mine"] is True
    assert [(q["text"], q["mine"]) for q in queue["queued"]] == [("第二条", True), ("第三条", True)]

    moved = await rig.api("POST", chat(rig, f"/turns/{third}/move"), json={"to": "first"})
    assert moved.status_code == 200, moved.text
    assert [q["turn_id"] for q in moved.json()["queued"]] == [third, second]
    edited = await rig.api("PATCH", chat(rig, f"/turns/{third}"), json={"text": "改过的第三条"})
    assert edited.status_code == 200 and edited.json()["queued"][0]["text"] == "改过的第三条"
    for refused in (
        await rig.api("PATCH", chat(rig, f"/turns/{running}"), json={"text": "x"}),
        await rig.api("POST", chat(rig, f"/turns/{running}/move"), json={"to": "last"}),
    ):
        assert refused.status_code == 409, refused.text
    assert (
        await rig.api("POST", chat(rig, f"/turns/{second}/move"), json={"to": "middle"})
    ).status_code == 422
    assert (await rig.api("GET", chat(rig, f"/sessions/{MISSING}/queue"))).status_code == 404

    await wait_idle(rig, session_id)
    view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
    asked = [m["text"] for m in view["messages"] if m["role"] == "user"]
    assert asked == ["sleep:1.5", "改过的第三条", "第二条"], asked
    empty = (await rig.api("GET", chat(rig, f"/sessions/{session_id}/queue"))).json()
    assert empty == {"session_id": session_id, "running": None, "queued": []}

    # 状态里有每个平台的进行中 / 等待 / 上限，设置里有两项限流
    status = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    assert status["load"]["claude"] == {"running": 0, "waiting": 0, "limit": 3}
    assert status["settings"]["concurrency"] == 3
    saved = await rig.api(
        "PUT",
        f"/bridges/{rig.bridge_id}/settings",
        json={"concurrency": 2, "min_start_interval_seconds": 5},
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["concurrency"], saved.json()["min_start_interval_seconds"]) == (2, 5)
    # 审计记动作，不记消息内容
    from tests.helpers import run_sql

    actions = await run_sql(
        rig.env.database_url,
        "SELECT string_agg(action, ',') FROM workspace.audit_logs WHERE action LIKE $1",
        "llm_bridge.chat_queue_%",
    )
    assert "llm_bridge.chat_queue_edited" in actions and "llm_bridge.chat_queue_moved" in actions
    leaked = await run_sql(
        rig.env.database_url,
        "SELECT count(*) FROM workspace.audit_logs a WHERE a::text LIKE $1",
        "%改过的第三条%",
    )
    assert leaked == 0


async def test_chat_takeover_needs_confirmation(rig: Rig) -> None:  # noqa: F811
    """智能体的会话可以看；接着发要带确认，后端不靠页面把关。"""
    env = rig.env
    instance = await new_instance(env, name="openclaw-1")
    token = await issue_token(env, instance)
    await rig.grant(instance, can_write=True)
    theirs = ok(
        await call(env, token, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )["session_id"]
    said = ok(
        await call(
            env,
            token,
            "llm_bridge_send",
            {"session_id": theirs, "text": "你好", "request_id": "a1", "wait_seconds": 10},
        )
    )
    assert said["status"] == "completed"

    listed = (await rig.api("GET", chat(rig, "/sessions"))).json()["sessions"]
    assert [(s["session_id"], s["owner_kind"], s["owner"]) for s in listed] == [
        (theirs, "agent", "openclaw-1")
    ]
    view = (await rig.api("GET", chat(rig, f"/sessions/{theirs}"))).json()
    assert [m["text"] for m in view["messages"]] == ["你好", "[fake-1] 你好"]
    assert view["messages"][0]["mine"] is False and view["messages"][0]["by"] == "openclaw-1"

    send = {"text": "我来接着问", "request_id": "h1"}
    blocked = await rig.api("POST", chat(rig, f"/sessions/{theirs}/messages"), json=send)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "TAKEOVER_REQUIRED"
    # 不存在的会话是 404，不是“要确认”
    missing = await rig.api("POST", chat(rig, f"/sessions/{MISSING}/messages"), json=send)
    assert missing.status_code == 404
    confirmed = await rig.api(
        "POST", chat(rig, f"/sessions/{theirs}/messages"), json={**send, "takeover": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    await wait_idle(rig, theirs, view["last_event_id"])
    view = (await rig.api("GET", chat(rig, f"/sessions/{theirs}"))).json()
    mine = [m for m in view["messages"] if m["role"] == "user" and m["mine"]]
    assert [(m["text"], m["by"]) for m in mine] == [("我来接着问", "Owner")]
    # 会话还是智能体的；智能体能看到人接着发的内容
    assert view["session"]["owner_kind"] == "agent"
    seen = ok(await call(env, token, "llm_bridge_session_get", {"session_id": theirs}))
    assert "我来接着问" in [m["text"] for m in seen["messages"]]

    # 智能体看不到人建的会话（它没有“查看账号下全部会话”的权限）
    own = (await new_chat(rig, "只给我看"))["session"]["session_id"]
    hidden = tool_error(await call(env, token, "llm_bridge_session_get", {"session_id": own}))
    assert hidden["code"] == "NOT_FOUND"

    # 审计：新建、发送、接手都有记录，不带消息正文
    logs = (
        await env.client.get("/api/v1/audit-logs", params={"limit": 100}, headers=env.headers())
    ).json()
    actions = [item["action"] for item in logs["items"]]
    for action in ("chat_session_created", "chat_message_sent", "chat_takeover_sent"):
        assert f"llm_bridge.{action}" in actions, actions
    assert "我来接着问" not in str(logs) and "只给我看" not in str(logs)


async def test_chat_roles(rig: Rig) -> None:  # noqa: F811
    """admin 能用聊天；member 不能看也不能发（不只是页面上没有入口）。"""
    env = rig.env
    session_id = (await new_chat(rig))["session"]["session_id"]
    await wait_idle(rig, session_id)

    def headers(client: httpx.AsyncClient, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": env.workspace_id, **(csrf(client) if write else {})}

    base = f"/api/v1/llm_bridge/bridges/{rig.bridge_id}/chat"
    async with login_as(rig, "admin@example.com", "admin") as admin:
        seen = await admin.get(f"{base}/sessions", headers=headers(admin))
        assert [s["owner_kind"] for s in seen.json()["sessions"]] == [
            "user"
        ]  # owner 建的，不是自己的
        made = await admin.post(
            f"{base}/sessions",
            json={"platform": "claude", "mode": "chat", "text": "我是管理员", "request_id": "ad1"},
            headers=headers(admin, write=True),
        )
        assert made.status_code == 201, made.text
        assert (made.json()["session"]["owner_kind"], made.json()["session"]["owner"]) == (
            "me",
            "Bob",
        )
        # 别的管理员的会话也要确认才能接着发
        other = await admin.post(
            f"{base}/sessions/{session_id}/messages",
            json={"text": "x", "request_id": "ad2"},
            headers=headers(admin, write=True),
        )
        assert other.json()["error"]["code"] == "TAKEOVER_REQUIRED"
        # 没带 CSRF 的写请求被拒绝
        no_csrf = await admin.post(
            f"{base}/sessions", json={"platform": "claude", "mode": "chat"}, headers=headers(admin)
        )
        assert no_csrf.status_code == 403

    for role in ("member", "viewer"):
        async with login_as(rig, f"{role}@example.com", role) as other_client:
            for method, path, body in (
                ("GET", "/sessions", None),
                ("GET", f"/sessions/{session_id}", None),
                ("GET", f"/sessions/{session_id}/events", None),
                ("GET", "/options?platform=claude", None),
                ("POST", "/sessions", {"platform": "claude", "mode": "chat"}),
                ("POST", f"/sessions/{session_id}/messages", {"text": "x", "request_id": "m1"}),
                ("POST", f"/sessions/{session_id}/archive", None),
                ("PATCH", f"/sessions/{session_id}", {"title": "x"}),
            ):
                response = await other_client.request(
                    method,
                    f"{base}{path}",
                    json=body,
                    headers=headers(other_client, write=method != "GET"),
                )
                assert response.status_code == 403, (role, method, path, response.text)

    # 别的工作空间的管理器编号：404
    stranger = await rig.api("GET", f"/bridges/{uuid.uuid4()}/chat/sessions")
    assert stranger.status_code == 404
