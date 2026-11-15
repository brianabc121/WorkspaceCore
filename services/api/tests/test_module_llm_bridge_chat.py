"""大模型聚合聊天页面的接口（T-0231，docs/v2/LLM_CHAT_PLAN.md）。

和 test_module_llm_bridge.py 用同一套环境：真实 PostgreSQL / Redis，会话桥是 services/llm-bridge 的
真实应用（进程内），Claude 这个账号换成不访问外部服务的测试驱动。覆盖的是整条链路：
浏览器的请求 → 工作台接口（角色、接手确认、审计）→ 会话桥（会话归属、事件）→ 驱动。
"""

import asyncio
import json
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


async def test_chat_quota_interruption_and_resume(rig: Rig) -> None:  # noqa: F811
    """额度用完：回合记成中断，页面看得到额度、重置时间和“什么时候自动继续”；能取消，也能现在就试。"""
    import time

    engine = rig.bridge_app.state.engine
    fake = engine.drivers["claude"]
    until = time.time() + 3600
    fake.quota_until = until
    created = await new_chat(rig, "跑一个长任务")
    session_id, turn_id = created["session"]["session_id"], created["turn"]["turn_id"]
    await wait_idle(rig, session_id)
    view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
    reply = view["messages"][-1]
    assert reply["status"] == "interrupted" and reply["failure"]["kind"] == "quota_exceeded"
    assert reply["resume"]["state"] == "pending" and reply["resume"]["at"] >= until

    # 状态和额度接口：窗口、已用百分比、重置时间、暂停到什么时候、有几个会话在等
    status = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    quota = (await rig.api("GET", f"/bridges/{rig.bridge_id}/quota")).json()["platforms"]
    assert status["quota"]["claude"] == quota["claude"]
    claude = quota["claude"]
    assert claude["paused_until"] >= until and claude["pause_estimated"] is False
    assert claude["waiting_resume"] == 1 and claude["can_refresh"] is False
    assert claude["reports_quota"] is True
    (window,) = claude["windows"]
    assert (window["kind"], window["status"], window["used_percent"]) == (
        "five_hour",
        "exceeded",
        100,
    )
    assert window["resets_at"] == pytest.approx(until) and window["reset_passed"] is False
    refreshed = await rig.api("GET", f"/bridges/{rig.bridge_id}/quota", params={"refresh": "true"})
    assert refreshed.status_code == 200 and "claude" in refreshed.json()["platforms"]

    # 设置里有三项，能改；发的那句话不能是空的
    assert status["settings"]["auto_resume"] is True and status["settings"]["auto_resume_max"] == 3
    assert status["settings"]["resume_text"] == "继续意外中断的任务"
    saved = await rig.api(
        "PUT",
        f"/bridges/{rig.bridge_id}/settings",
        json={"auto_resume_max": 5, "resume_text": "接着做刚才的任务"},
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["auto_resume_max"], saved.json()["resume_text"]) == (5, "接着做刚才的任务")
    empty = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"resume_text": ""})
    assert empty.status_code == 422

    # 不要自动继续 → 现在就试（额度已经恢复）：系统替人发那句话，回合完成
    cancelled = await rig.api("DELETE", chat(rig, f"/turns/{turn_id}/resume"))
    assert cancelled.status_code == 200 and cancelled.json()["resume"]["state"] == "cancelled"
    fake.quota_until = 0
    forced = await rig.api("POST", chat(rig, f"/turns/{turn_id}/resume"))
    assert forced.status_code == 200 and forced.json()["resume"]["state"] == "forced", forced.text
    for _ in range(100):
        view = (await rig.api("GET", chat(rig, f"/sessions/{session_id}"))).json()
        if view["messages"][-1]["status"] == "completed":
            break
        await asyncio.sleep(0.1)
    asked = [(m["text"], m["by"]) for m in view["messages"] if m["role"] == "user"]
    assert [text for text, _ in asked] == ["跑一个长任务", "接着做刚才的任务"], view["messages"]
    assert asked[1][1] == "系统（自动续跑）" and asked[0][1] != asked[1][1]
    interrupted = next(m for m in view["messages"] if m.get("status") == "interrupted")
    assert interrupted["resume"]["state"] == "done" and interrupted["resume"]["resumed_by"]
    again = await rig.api("POST", chat(rig, f"/turns/{turn_id}/resume"))
    assert again.status_code == 409, again.text
    assert (
        await rig.api("POST", chat(rig, "/turns/bt_" + "0" * 20 + "/resume"))
    ).status_code == 404

    # 只有管理员能操作；审计记了动作
    async with login_as(rig, "quota-member@example.com", "member") as member:
        denied = await member.post(
            f"/api/v1/llm_bridge{chat(rig, f'/turns/{turn_id}/resume')}",
            headers={"X-Workspace-Id": rig.env.workspace_id, **csrf(member)},
        )
        assert denied.status_code == 403
    from tests.helpers import run_sql

    actions = await run_sql(
        rig.env.database_url,
        "SELECT string_agg(action, ',') FROM workspace.audit_logs WHERE action LIKE $1",
        "llm_bridge.chat_resume_%",
    )
    assert "llm_bridge.chat_resume_now" in actions and "llm_bridge.chat_resume_cancelled" in actions


async def test_chat_attachments_and_stored_content(rig: Rig) -> None:  # noqa: F811
    """图片随消息发给模型、存在管理器里、能下载；过程里有工具调用；自己建的会话不需要重新导入。"""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    session_id = (await new_chat(rig, None))["session"]["session_id"]
    base = chat(rig, f"/sessions/{session_id}")

    async def upload(name: str, data: bytes) -> httpx.Response:
        return await rig.env.client.post(
            f"/api/v1/llm_bridge{base}/attachments",
            params={"name": name},
            content=data,
            headers={**rig.env.headers(write=True), "Content-Type": "application/octet-stream"},
        )

    made = await upload("机密截图.png", png)
    assert made.status_code == 201, made.text
    image = made.json()
    assert (image["name"], image["media_type"], image["size"], image["direction"]) == (
        "机密截图.png",
        "image/png",
        len(png),
        "input",
    )
    # 不收的：对话会话里的非图片、内容和扩展名对不上、太大（工作台先挡）
    assert (await upload("说明.txt", b"hello")).status_code == 422
    assert (await upload("假的.png", b"<html>")).status_code == 422
    assert (await upload("大.png", png + b"0" * (20 * 1024 * 1024))).status_code == 413
    spare = (await upload("多余.png", png)).json()
    dropped = await rig.api("DELETE", f"{base}/attachments/{spare['id']}")
    assert dropped.status_code == 204

    sent = await rig.api(
        "POST",
        f"{base}/messages",
        json={"text": "read:README.md", "request_id": "a1", "attachments": [image["id"]]},
    )
    assert sent.status_code == 200, sent.text
    await wait_idle(rig, session_id)
    assert rig.bridge_app.state.engine.drivers["claude"].last_images[0][1] == "image/png"
    view = (await rig.api("GET", base)).json()
    asked, reply = view["messages"]
    assert [a["id"] for a in asked["attachments"]] == [image["id"]] and asked["imported"] is False
    assert reply["text"].endswith("（收到 1 张图片）")
    assert [(s["kind"], s["name"]) for s in reply["steps"]] == [
        ("tool", "Read"),
        ("tool_result", "Read"),
    ]
    again = await rig.api(
        "POST",
        f"{base}/messages",
        json={"text": "再发", "request_id": "a2", "attachments": [image["id"]]},
    )
    assert again.status_code == 409, again.text
    bad = await rig.api(
        "POST", f"{base}/messages", json={"text": "x", "request_id": "a3", "attachments": ["x"]}
    )
    assert bad.status_code == 422

    got = await rig.api("GET", f"{base}/attachments/{image['id']}")
    assert got.status_code == 200 and got.content == png
    assert got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"].startswith("inline; filename*=UTF-8''")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]
    missing = await rig.api("GET", f"{base}/attachments/ba_{'0' * 20}")
    assert missing.status_code == 404
    assert (await rig.api("POST", f"{base}/reimport")).status_code == 409

    # member 不能上传也不能下载；审计只记类型和大小，不记文件名
    async with login_as(rig, "files-member@example.com", "member") as member:
        scope = {"X-Workspace-Id": rig.env.workspace_id}
        url = f"/api/v1/llm_bridge{base}/attachments"
        denied = await member.post(
            url, params={"name": "a.png"}, content=png, headers={**scope, **csrf(member)}
        )
        assert denied.status_code == 403
        assert (await member.get(f"{url}/{image['id']}", headers=scope)).status_code == 403
    from tests.helpers import run_sql

    logged = await run_sql(
        rig.env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = $1",
        "llm_bridge.chat_attachment_uploaded",
    )
    assert logged == 2
    leaked = await run_sql(
        rig.env.database_url,
        "SELECT count(*) FROM workspace.audit_logs a WHERE a::text LIKE $1",
        "%机密截图%",
    )
    assert leaked == 0


async def test_chat_insert_message(rig: Rig) -> None:  # noqa: F811
    """插入消息：平台收得下就进正在处理的回合；收不下就中断它、合并重发。页面拿到实际用了哪种。"""
    fake = rig.bridge_app.state.engine.drivers["claude"]
    created = await new_chat(rig, "sleep:30")
    session_id, running = created["session"]["session_id"], created["turn"]["turn_id"]
    base = chat(rig, f"/sessions/{session_id}")
    for _ in range(100):
        if fake.running:
            break
        await asyncio.sleep(0.05)

    fake.steerable = True
    inserted = await rig.api(
        "POST",
        f"{base}/messages",
        json={"text": "顺便看日志", "request_id": "i1", "mode": "insert"},
    )
    assert inserted.status_code == 200, inserted.text
    assert (inserted.json()["delivered"], inserted.json()["turn_id"]) == ("inserted", running)
    view = (await rig.api("GET", base)).json()
    assert [(m["role"], m["text"], m["delivery"]) for m in view["messages"][:2]] == [
        ("user", "sleep:30", "turn"),
        ("user", "顺便看日志", "inserted"),
    ]
    assert view["messages"][1]["mine"] is True

    fake.steerable = False
    merged = await rig.api(
        "POST", f"{base}/messages", json={"text": "先改这里", "request_id": "i2", "mode": "insert"}
    )
    assert merged.status_code == 200 and merged.json()["delivered"] == "merged", merged.text
    assert merged.json()["turn_id"] != running
    await wait_idle(rig, session_id)
    view = (await rig.api("GET", base)).json()
    first = next(
        m for m in view["messages"] if m["role"] == "assistant" and m["turn_id"] == running
    )
    assert first["status"] == "interrupted" and first["failure"]["kind"] == "superseded"
    last_ask, last_reply = view["messages"][-2:]
    assert (last_ask["text"], last_ask["delivery"]) == ("先改这里", "merged")
    assert last_reply["status"] == "completed"
    bad = await rig.api(
        "POST", f"{base}/messages", json={"text": "x", "request_id": "i3", "mode": "now"}
    )
    assert bad.status_code == 422
    # 空闲时插入就是普通的发送
    plain = await rig.api(
        "POST", f"{base}/messages", json={"text": "你好", "request_id": "i4", "mode": "insert"}
    )
    assert plain.json()["delivered"] == "started"
    await wait_idle(rig, session_id)


async def test_local_manager_is_added_without_address_or_token(rig: Rig) -> None:  # noqa: F811
    """本机部署（T-0238）：随工作台启动的管理器，选了平台就能添加；地址和令牌不经过页面。"""
    env = rig.env
    # 这个夹具的工作台没有随带本机管理器：页面上不能选，接口也拒绝
    assert (await rig.api("GET", "/local")).json() == {
        "available": False,
        "bridge_id": None,
        "taken": False,
    }
    none = await rig.api("POST", "/local", json={"platforms": ["claude"]})
    assert none.status_code == 409 and none.json()["error"]["code"] == "LOCAL_UNAVAILABLE"

    token = "l" * 40
    await rig.spawn("local.test", token)
    settings = {
        **CONNECTOR_SETTINGS,
        **SETTINGS,
        "llm_bridge_local_url": "http://local.test:8787",
        "llm_bridge_local_token": token,
    }
    password = "local owner password 1234"  # noqa: S105 - 测试密码
    scope = {"X-Workspace-Id": env.workspace_id}
    owner_id = await add_user(env.database_url, "second-owner@example.com", password)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), owner_id, "owner")
    admin_id = await add_user(env.database_url, "local-admin@example.com", password)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin_id, "admin")
    async with env.make_client(**settings) as owner, env.make_client(**settings) as admin:
        for client, email in (
            (owner, "second-owner@example.com"),
            (admin, "local-admin@example.com"),
        ):
            client._transport.app.state.connectors.transport = rig.transport  # type: ignore[attr-defined]
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text
        base = "/api/v1/llm_bridge"
        state = (await admin.get(f"{base}/local", headers=scope)).json()
        assert state == {"available": True, "bridge_id": None, "taken": False}
        # 只有 owner 能添加；要选平台
        write = {**scope, **csrf(admin)}
        denied = await admin.post(f"{base}/local", json={"platforms": ["claude"]}, headers=write)
        assert denied.status_code == 403
        write = {**scope, **csrf(owner)}
        empty = await owner.post(f"{base}/local", json={"platforms": []}, headers=write)
        assert empty.status_code == 422
        made = await owner.post(
            f"{base}/local", json={"platforms": ["claude", "workbuddy"]}, headers=write
        )
        assert made.status_code == 201, made.text
        bridge = made.json()
        assert (bridge["name"], bridge["base_url"], bridge["has_token"]) == (
            "本机",
            "http://local.test:8787",
            True,
        )
        assert token not in made.text
        status = (await owner.get(f"{base}/bridges/{bridge['id']}/status", headers=scope)).json()
        assert status["reachable"] is True and status["platforms"] == ["claude", "workbuddy"]
        state = (await owner.get(f"{base}/local", headers=scope)).json()
        assert state == {"available": True, "bridge_id": bridge["id"], "taken": False}
        again = await owner.post(f"{base}/local", json={"platforms": ["claude"]}, headers=write)
        assert again.status_code == 409 and again.json()["error"]["code"] == "LOCAL_EXISTS"
        # 地址和令牌由工作台管理，不能在页面上改
        moved = await owner.patch(
            f"{base}/bridges/{bridge['id']}", json={"token": "x" * 40}, headers=write
        )
        assert moved.status_code == 409 and moved.json()["error"]["code"] == "BRIDGE_MANAGED"
        renamed = await owner.patch(
            f"{base}/bridges/{bridge['id']}", json={"name": "本机一号"}, headers=write
        )
        assert renamed.status_code == 200


async def test_platform_failure_at_login_shows_the_reason(rig: Rig) -> None:  # noqa: F811
    """登录时平台那边没成功：页面拿到管理器写的原因，不是“MCP 管理器连不上”；之后管理器照常可用。"""
    fake = rig.bridge_app.state.engine.drivers["claude"]
    fake.login_error = "向平台申请验证码没有成功：这台服务器访问不了平台"
    for _ in range(6):  # 连着失败几次也不该把管理器当成坏了（熔断）
        failed = await rig.api(
            "POST",
            f"/bridges/{rig.bridge_id}/accounts/claude/login",
            json={"method": "api_key", "api_key": "k" * 20},
        )
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "UNAVAILABLE"
        assert failed.json()["error"]["message"] == fake.login_error
    status = await rig.api("GET", f"/bridges/{rig.bridge_id}/status")
    assert status.status_code == 200 and status.json()["reachable"] is True
    fake.login_error = None
    done = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/accounts/claude/login",
        json={"method": "api_key", "api_key": "k" * 20},
    )
    assert done.status_code == 200, done.text


async def test_connectivity_is_measured_by_the_manager(rig: Rig) -> None:  # noqa: F811
    """到各平台能不能用、延迟多少：由管理器在它那台服务器上测，工作台原样给页面。"""
    engine = rig.bridge_app.state.engine
    calls: list[str] = []

    async def probe(target: Any) -> dict[str, Any]:
        calls.append(target.host)
        if target.host == "claude.ai":
            note = "平台拒绝了这台服务器的访问（HTTP 403）"
            return {"state": "blocked", "latency_ms": 9, "http_status": 403, "error": note}
        return {"state": "ok", "latency_ms": 37, "http_status": 401, "error": None}

    engine.probe_host = probe
    path = f"/bridges/{rig.bridge_id}/connectivity"
    data = (await rig.api("GET", path)).json()
    assert data["supported"] is True and set(data["slots"]) == {"openai", "claude", "workbuddy"}
    claude = data["slots"]["claude"]
    assert claude["ok"] is False and claude["need"] == "all"
    blocked = claude["targets"][1]
    assert (blocked["host"], blocked["state"], blocked["ok"], blocked["latency_ms"]) == (
        "claude.ai",
        "blocked",
        False,
        9,
    )
    assert data["slots"]["openai"]["ok"] is True
    count = len(calls)
    await rig.api("GET", path)
    assert len(calls) == count  # 一分钟内复用
    await rig.api("GET", path, params={"refresh": "true"})
    assert len(calls) == count * 2
    async with login_as(rig, "net-member@example.com", "member") as member:
        scope = {"X-Workspace-Id": rig.env.workspace_id}
        denied = await member.get(f"/api/v1/llm_bridge{path}", headers=scope)
        assert denied.status_code == 403


async def test_chat_stream_pushes_deltas_and_resumes_from_a_cursor(rig: Rig) -> None:  # noqa: F811
    """推送通道（改进方案 3.2）：事件逐条推，逐字的增量在里面；带着编号重连能接上；跑完就结束。"""
    session_id = (await new_chat(rig, None))["session"]["session_id"]
    base = chat(rig, f"/sessions/{session_id}")
    sent = await rig.api(
        "POST", f"{base}/messages", json={"text": "stream:0.2", "request_id": "s1"}
    )
    assert sent.status_code == 200, sent.text

    async def read(after: int) -> list[tuple[str, dict[str, Any]]]:
        frames: list[tuple[str, dict[str, Any]]] = []
        async with rig.env.client.stream(
            "GET",
            f"/api/v1/llm_bridge{base}/stream",
            params={"after": after},
            headers=rig.env.headers(),
        ) as response:
            assert response.status_code == 200, await response.aread()
            assert response.headers["content-type"].startswith("text/event-stream")
            name = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    frames.append((name, json.loads(line[5:])))
        return frames

    frames = await read(0)
    kinds = [data["kind"] for name, data in frames if name == "chat"]
    assert "queued" in kinds and "delta" in kinds and kinds[-1] == "completed"
    assert kinds.index("delta") < kinds.index("message")
    # 增量在那一段完整的话之前到，拼起来是它的开头
    typed = "".join(d["text"] for n, d in frames if n == "chat" and d["kind"] == "delta")
    whole = next(d["text"] for n, d in frames if n == "chat" and d["kind"] == "message")
    assert typed and whole.startswith(typed[:2])
    end = frames[-1]
    assert end[0] == "end" and end[1]["busy"] is False
    # 带着最后的编号再连：没有新的，直接结束
    again = await read(end[1]["last_id"])
    assert again == [("end", {"busy": False, "last_id": end[1]["last_id"]})]
    # 刷新页面：回答上有用时；进行中的增量结束后不再留着
    view = (await rig.api("GET", base)).json()
    reply = view["messages"][-1]
    assert reply["status"] == "completed" and reply["timing"]["ran_seconds"] >= 0.3
    assert reply["partial"] is None and reply["source"]["kind"] == "user"
    # member 读不了
    assert (await rig.env.client.get(f"/api/v1/llm_bridge{base}/stream")).status_code in (
        400,
        404,
        422,
    )


async def test_chat_model_changes_between_turns(rig: Rig) -> None:  # noqa: F811
    """换模型从下一回合起生效；标题在发出第一条时就定下来（T-0253）。"""
    session_id = (await new_chat(rig, None))["session"]["session_id"]
    base = chat(rig, f"/sessions/{session_id}")
    first = await rig.api(
        "POST", f"{base}/messages", json={"text": "sleep:0.3", "request_id": "m1"}
    )
    assert first.status_code == 200, first.text
    # 第一回合还没做完，标题已经有了
    listed = (await rig.api("GET", chat(rig, "/sessions"))).json()["sessions"]
    assert listed[0]["title"] == "sleep:0.3" and listed[0]["busy"] is True
    changed = await rig.api("PUT", f"{base}/model", json={"model": "fake-2"})
    assert changed.status_code == 200 and changed.json()["model"] == "fake-2"
    second = await rig.api("POST", f"{base}/messages", json={"text": "换了吗", "request_id": "m2"})
    assert second.status_code == 200
    for _ in range(60):
        view = (await rig.api("GET", base)).json()
        if not view["session"]["busy"]:
            break
        await asyncio.sleep(0.1)
    replies = [m["text"] for m in view["messages"] if m["role"] == "assistant"]
    # 已经在跑的那一回合用原来的模型，后面的用新的
    assert replies == ["[fake-1] sleep:0.3", "[fake-2] 换了吗"]
    assert (await rig.api("PUT", f"{base}/model", json={"model": "有 空格"})).status_code == 422
    actions = [e["action"] for e in (await rig.env.get("/api/v1/audit-logs", limit=20))["items"]]
    assert "llm_bridge.chat_session_model_changed" in actions
    archived = await rig.api("POST", f"{base}/archive")
    assert archived.status_code == 200
    assert (await rig.api("PUT", f"{base}/model", json={"model": "fake-1"})).status_code == 409
