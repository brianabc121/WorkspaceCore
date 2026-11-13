"""会话桥端到端（经 /mcp 与 /admin，用不访问外部服务的 fake 平台）。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from bridge.channels import CHANNELS, FAKE_CHANNELS
from bridge.config import Settings
from bridge.engine import Engine
from bridge.store import Store
from tests.conftest import ADMIN, Bridge


async def _session(bridge: Bridge, caller: str, **extra: object) -> str:
    created = await bridge.tool(
        caller, "llm_session_create", {"platform": "fake", "mode": "chat", **extra}
    )
    assert "error" not in created, created
    return str(created["session_id"])


async def _send(bridge: Bridge, caller: str, session: str, text: str, rid: str, wait: float = 10):
    return await bridge.tool(
        caller,
        "llm_send",
        {"session_id": session, "text": text, "request_id": rid, "wait_seconds": wait},
    )


async def test_requirement_example(bridge: Bridge) -> None:
    """需求里的例子：1 号向同一个聊天发两条，2 号向另一个平台会话发一条，互不可见。"""
    a = await _session(bridge, "openclaw-1", model="fake-2")
    first = await _send(bridge, "openclaw-1", a, "你好", "r1")
    second = await _send(bridge, "openclaw-1", a, "大家好", "r2")
    assert first["status"] == second["status"] == "completed"
    assert first["result"] == "[fake-2] 你好" and second["model"] == "fake-2"
    c = await _session(bridge, "openclaw-2")
    assert (await _send(bridge, "openclaw-2", c, "你好", "r1"))["result"] == "[fake-1] 你好"

    mine = await bridge.tool("openclaw-1", "llm_session_list")
    assert [s["session_id"] for s in mine["sessions"]] == [a]
    assert (await bridge.tool("openclaw-2", "llm_session_get", {"session_id": a}))["error"][
        "code"
    ] == "NOT_FOUND"
    assert (await _send(bridge, "openclaw-2", a, "插话", "r9"))["error"]["code"] == "NOT_FOUND"
    detail = await bridge.tool("openclaw-1", "llm_session_get", {"session_id": a})
    assert [m["text"] for m in detail["messages"]] == [
        "你好",
        "[fake-2] 你好",
        "大家好",
        "[fake-2] 大家好",
    ]
    assert detail["session"]["title"] == "你好"


async def test_idempotent_send(bridge: Bridge) -> None:
    a = await _session(bridge, "c1")
    b = await _session(bridge, "c1")
    first = await _send(bridge, "c1", a, "一次", "same")
    again = await _send(bridge, "c1", a, "一次", "same")
    assert again["turn_id"] == first["turn_id"]
    assert len(bridge.fake.history[bridge.engine.store.get_session(a)["native_id"]]) == 2
    assert (await _send(bridge, "c1", b, "一次", "same"))["error"]["code"] == "CONFLICT"
    # 另一个调用方用同一个 request_id 不冲突
    other = await _session(bridge, "c2")
    assert (await _send(bridge, "c2", other, "x", "same"))["status"] == "completed"


async def test_async_turn_and_queue_order(bridge: Bridge) -> None:
    a = await _session(bridge, "c1")
    slow = await _send(bridge, "c1", a, "sleep:0.5", "r1", wait=0)
    queued = await _send(bridge, "c1", a, "第二条", "r2", wait=0)
    assert slow["status"] in ("queued", "running")
    assert queued["status"] == "queued" and queued["ahead_in_session"] == 1
    done = await bridge.tool(
        "c1", "llm_turn_get", {"turn_id": queued["turn_id"], "wait_seconds": 10}
    )
    assert done["status"] == "completed"
    turns = bridge.engine.store.list_turns(session_id=a)
    assert turns[0]["finished_at"] <= turns[1]["started_at"]


async def test_concurrency_cap(bridge: Bridge) -> None:
    await bridge.admin("PUT", "/admin/settings", json={"concurrency": 1})
    sessions = [await _session(bridge, "c1") for _ in range(3)]
    await asyncio.gather(
        *(_send(bridge, "c1", s, "sleep:0.2", f"r{i}") for i, s in enumerate(sessions))
    )
    assert bridge.fake.max_running == 1
    await bridge.admin("PUT", "/admin/settings", json={"concurrency": 3})
    await asyncio.gather(
        *(_send(bridge, "c1", s, "sleep:0.2", f"q{i}") for i, s in enumerate(sessions))
    )
    assert bridge.fake.max_running == 3


async def test_cancel(bridge: Bridge) -> None:
    a = await _session(bridge, "c1")
    running = await _send(bridge, "c1", a, "sleep:30", "r1", wait=0)
    queued = await _send(bridge, "c1", a, "后面的", "r2", wait=0)
    assert (await bridge.tool("c1", "llm_turn_cancel", {"turn_id": queued["turn_id"]}))[
        "status"
    ] == "cancelled"
    await asyncio.sleep(0.05)
    cancelled = await bridge.tool("c1", "llm_turn_cancel", {"turn_id": running["turn_id"]})
    assert cancelled["status"] == "cancelled"
    assert (await _send(bridge, "c1", a, "还能用", "r3"))["status"] == "completed"


async def test_failures_and_redaction(bridge: Bridge) -> None:
    a = await _session(bridge, "c1")
    failed = await _send(bridge, "c1", a, "fail:rate_limited", "r1")
    assert failed["status"] == "failed" and failed["failure"]["kind"] == "rate_limited"
    secret = await _send(bridge, "c1", a, "key " + "sk-" + "abcdefghijklmnopqrstuvwxyz123456", "r2")
    assert "sk-abcdef" not in secret["result"] and "***" in secret["result"]
    bridge.engine.store.set_setting("turn_timeout_seconds", 1)
    late = await _send(bridge, "c1", a, "sleep:20", "r3")
    assert late["status"] == "failed" and late["failure"]["kind"] == "timeout"


async def test_scopes(bridge: Bridge) -> None:
    a = await _session(bridge, "owner")
    await _send(bridge, "owner", a, "内容", "r1")
    # 没有 write：只列读工具，写工具被拒绝
    listed = await bridge.rpc("tools/list", {}, bridge.headers("reader", ""))
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert "llm_session_list" in names and "llm_send" not in names
    denied = await bridge.tool(
        "reader", "llm_session_create", {"platform": "fake", "mode": "chat"}, scopes=""
    )
    assert denied["error"]["code"] == "FORBIDDEN"
    assert (await bridge.tool("reader", "llm_session_list", {"scope": "account"}, scopes=""))[
        "error"
    ]["code"] == "FORBIDDEN"
    # account：能看到别人的会话，但读不到内容，也不能续发
    seen = await bridge.tool(
        "viewer", "llm_session_list", {"scope": "account"}, scopes="write,account"
    )
    assert [(s["session_id"], s["mine"], s["owner"]) for s in seen["sessions"]] == [
        (a, False, "owner")
    ]
    peek = await bridge.tool("viewer", "llm_session_get", {"session_id": a}, scopes="write,account")
    assert peek["messages"] == [] and "note" in peek
    blocked = await bridge.tool(
        "viewer",
        "llm_send",
        {"session_id": a, "text": "x", "request_id": "v1"},
        scopes="write,account",
    )
    assert blocked["error"]["code"] == "NOT_FOUND"
    # takeover：可以读、可以接着发
    scopes = "write,account,takeover"
    full = await bridge.tool("boss", "llm_session_get", {"session_id": a}, scopes=scopes)
    assert len(full["messages"]) == 2
    taken = await bridge.tool(
        "boss", "llm_send", {"session_id": a, "text": "接手", "request_id": "b1"}, scopes=scopes
    )
    assert taken["status"] == "completed"


async def test_external_sessions(bridge: Bridge) -> None:
    from bridge.drivers.base import Message, NativeSession

    bridge.fake.external = [
        NativeSession("native-1", "在别处开的会话", "fake-1", "/work/x", 1.0, 2.0)
    ]
    bridge.fake.history["native-1"] = [Message("user", "旧消息"), Message("assistant", "旧回答")]
    scopes = "write,account,takeover"
    listed = await bridge.tool("boss", "llm_session_list", {"scope": "account"}, scopes=scopes)
    (item,) = listed["sessions"]
    assert item["origin"] == "external" and item["title"] == "在别处开的会话" and not item["mine"]
    read = await bridge.tool(
        "boss", "llm_session_get", {"session_id": item["session_id"]}, scopes=scopes
    )
    assert [m["text"] for m in read["messages"]] == ["旧消息", "旧回答"]
    # 再列一次不会重复登记
    again = await bridge.tool("boss", "llm_session_list", {"scope": "account"}, scopes=scopes)
    assert len(again["sessions"]) == 1
    assert (await bridge.tool("someone", "llm_session_list"))["sessions"] == []


async def test_single_account_guard(bridge: Bridge) -> None:
    a = await _session(bridge, "c1")
    status = (await bridge.admin("GET", "/admin/status")).json()
    assert status["accounts"]["fake"]["state"] == "logged_in"
    assert status["accounts"]["fake"]["identity"] == "f***@example.com"
    # 登录目录里换成了另一个账号：拒绝调用
    bridge.fake.identity = "other@example.com"
    refreshed = (await bridge.admin("POST", "/admin/accounts/fake/refresh")).json()
    assert refreshed["state"] == "mismatch"
    assert (await bridge.tool("c1", "llm_session_create", {"platform": "fake", "mode": "chat"}))[
        "error"
    ]["code"] == "ACCOUNT_MISMATCH"
    assert (await _send(bridge, "c1", a, "x", "r1"))["error"]["code"] == "ACCOUNT_MISMATCH"
    # 管理员明确更换账号：旧会话归档，新账号可用
    reset = (await bridge.admin("POST", "/admin/accounts/fake/reset")).json()
    assert reset["archived_sessions"] == 1
    assert (await _send(bridge, "c1", a, "x", "r2"))["error"]["code"] == "CONFLICT"
    b = await _session(bridge, "c1")
    assert (await _send(bridge, "c1", b, "新账号", "r3"))["status"] == "completed"
    # 退出登录
    assert (await bridge.admin("POST", "/admin/accounts/fake/logout")).status_code == 200
    assert (await _send(bridge, "c1", b, "x", "r4"))["error"]["code"] == "NOT_LOGGED_IN"
    # 用别的账号登录回来：仍然只认登记的那个
    await bridge.admin(
        "POST",
        "/admin/accounts/fake/login",
        json={"method": "api_key", "api_key": "third@example.com"},
    )
    assert (await _send(bridge, "c1", b, "x", "r5"))["error"]["code"] == "ACCOUNT_MISMATCH"


async def test_rate_limit(bridge: Bridge) -> None:
    await bridge.admin("PUT", "/admin/settings", json={"send_per_hour": 2})
    a = await _session(bridge, "c1")
    assert (await _send(bridge, "c1", a, "1", "r1"))["status"] == "completed"
    assert (await _send(bridge, "c1", a, "2", "r2"))["status"] == "completed"
    assert (await _send(bridge, "c1", a, "3", "r3"))["error"]["code"] == "RATE_LIMITED"
    other = await _session(bridge, "c2")
    assert (await _send(bridge, "c2", other, "1", "r1"))["status"] == "completed"
    bad = await bridge.admin("PUT", "/admin/settings", json={"send_per_hour": 0})
    assert bad.status_code == 422


async def test_dev_mode_environment(bridge: Bridge, settings: Settings) -> None:
    missing = await bridge.tool("c1", "llm_session_create", {"platform": "fake", "mode": "dev"})
    assert missing["error"]["code"] == "VALIDATION_FAILED"
    bad = await bridge.admin(
        "POST",
        "/admin/environments",
        json={"platform": "fake", "mode": "dev", "name": "x", "repo_url": "file:///etc"},
    )
    assert bad.status_code == 422
    env = (
        await bridge.admin(
            "POST",
            "/admin/environments",
            json={
                "platform": "fake",
                "mode": "dev",
                "name": "空目录",
                "sandbox": "workspace_write",
            },
        )
    ).json()
    dup = await bridge.admin(
        "POST", "/admin/environments", json={"platform": "fake", "mode": "dev", "name": "空目录"}
    )
    assert dup.status_code == 409
    listed = await bridge.tool("c1", "llm_environments", {"platform": "fake", "mode": "dev"})
    assert [e["id"] for e in listed["environments"]] == [env["id"]]
    wrong = await bridge.tool(
        "c1", "llm_session_create", {"platform": "fake", "mode": "chat", "environment": env["id"]}
    )
    assert wrong["error"]["code"] == "VALIDATION_FAILED"
    created = await bridge.tool(
        "c1", "llm_session_create", {"platform": "fake", "mode": "dev", "environment": env["id"]}
    )
    assert created["options"] == {"sandbox": "workspace_write", "network": False}
    workdir = settings.work_dir / created["session_id"]
    assert workdir.is_dir()
    archived = await bridge.tool("c1", "llm_session_archive", {"session_id": created["session_id"]})
    assert archived["status"] == "archived" and not workdir.exists()
    # 停用的环境不再列出，也不能用来新建
    await bridge.admin(
        "PUT",
        f"/admin/environments/{env['id']}",
        json={"platform": "fake", "mode": "dev", "name": "空目录", "enabled": False},
    )
    assert (await bridge.tool("c1", "llm_environments", {"platform": "fake", "mode": "dev"}))[
        "environments"
    ] == []


async def test_channels_and_validation(bridge: Bridge) -> None:
    cloud = await bridge.tool("c1", "llm_session_create", {"platform": "codex", "mode": "cloud"})
    assert cloud["error"]["code"] == "NOT_SUPPORTED" and "接口桩" in cloud["error"]["message"]
    assert (await bridge.tool("c1", "llm_models", {"platform": "fake", "mode": "chat"}))["models"][
        0
    ]["default"]
    unknown = await bridge.tool(
        "c1", "llm_session_create", {"platform": "fake", "mode": "chat", "x": 1}
    )
    assert unknown["error"]["code"] == "VALIDATION_FAILED"
    a = await _session(bridge, "c1")
    assert (await _send(bridge, "c1", a, "x", "bad id!"))["error"]["code"] == "VALIDATION_FAILED"
    assert (await bridge.tool("c1", "no_such_tool"))["error"]["code"] == "NOT_FOUND"
    overview = await bridge.tool("c1", "llm_status")
    assert {"platform": "chatgpt", "mode": "chat"}.items() <= overview["channels"][0].items()


async def test_auth(bridge: Bridge) -> None:
    assert (await bridge.client.get("/healthz")).json()["status"] == "ok"
    assert (await bridge.client.get("/admin/status")).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert (await bridge.client.get("/admin/status", headers=wrong)).status_code == 401
    assert (await bridge.rpc("tools/list", {}, wrong)).status_code == 401
    # 管理令牌调用 /mcp 必须说明代表谁
    assert (
        await bridge.rpc("tools/list", {}, {"Authorization": f"Bearer {ADMIN}"})
    ).status_code == 400
    # 直连的调用方令牌：只返回一次，桥里只存哈希
    made = (
        await bridge.admin(
            "POST", "/admin/callers", json={"label": "直连 1 号", "scopes": ["write"]}
        )
    ).json()
    token = made["token"]
    assert token not in str(bridge.engine.store.list_callers())
    direct = {"Authorization": f"Bearer {token}"}
    created = await bridge.rpc(
        "tools/call",
        {"name": "llm_session_create", "arguments": {"platform": "fake", "mode": "chat"}},
        direct,
    )
    assert created.json()["result"]["structuredContent"]["owner"] == "直连 1 号"
    # 调用方令牌不能进管理接口，也不能自己改身份
    assert (await bridge.client.get("/admin/status", headers=direct)).status_code == 401
    assert (await bridge.admin("DELETE", f"/admin/callers/{made['id']}")).status_code == 200
    assert (await bridge.rpc("tools/list", {}, direct)).status_code == 401


async def test_restart_recovery(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, admin_token=ADMIN, enable_fake=True)
    store = Store(settings.db_path)
    from bridge.drivers.fake import FakeDriver

    session = store.create_session(
        platform="fake",
        mode="chat",
        slot="fake",
        native_id="n1",
        model="fake-1",
        caller_id="c1",
        caller_label="c1",
        workdir=str(tmp_path / "w"),
    )
    interrupted = store.create_turn(session["id"], "c1", "r1", "做到一半")
    store.update_turn(interrupted["id"], status="running", started_at=time.time())
    waiting = store.create_turn(session["id"], "c1", "r2", "还没开始")
    engine = Engine(store, {"fake": FakeDriver()}, CHANNELS + FAKE_CHANNELS, settings)
    await engine.startup()
    assert store.get_turn(interrupted["id"])["status"] == "unknown"
    done = await engine.wait_turn(waiting["id"], 10)
    assert done["status"] == "completed" and done["result"] == "[fake-1] 还没开始"
    await engine.shutdown()
    store.close()
