"""会话桥端到端（经 /mcp 与 /admin，用不访问外部服务的 fake 平台）。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from bridge.channels import CHANNELS, FAKE_CHANNELS
from bridge.config import Settings
from bridge.engine import Engine
from bridge.store import Store
from tests.conftest import ADMIN, Bridge

CHAT: dict[str, Any] = {"platform": "fake", "mode": "chat"}


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


async def test_limit_is_per_platform_and_starts_are_spaced(bridge: Bridge) -> None:
    """每个平台同时进行的会话数默认 3，按平台计数；同一个平台两次开始之间隔开一段时间。"""
    defaults = (await bridge.admin("GET", "/admin/status")).json()
    assert defaults["settings"]["concurrency"] == 3
    load = defaults["load"]["fake"]
    assert load == {"running": 0, "waiting": 0, "limit": 3}
    sessions = [await _session(bridge, "c1") for _ in range(5)]
    started = [
        await _send(bridge, "c1", s, "sleep:0.4", f"p{i}", wait=0) for i, s in enumerate(sessions)
    ]
    await asyncio.sleep(0.15)
    load = (await bridge.admin("GET", "/admin/status")).json()["load"]["fake"]
    assert load == {"running": 3, "waiting": 2, "limit": 3}
    for turn in started:
        done = await bridge.tool(
            "c1", "llm_turn_get", {"turn_id": turn["turn_id"], "wait_seconds": 10}
        )
        assert done["status"] == "completed"
    assert bridge.fake.max_running == 3

    # 起跑间隔：三个会话同时发，开始时刻依次错开
    saved = await bridge.admin("PUT", "/admin/settings", json={"min_start_interval_seconds": 1})
    assert saved.json()["min_start_interval_seconds"] == 1
    await asyncio.gather(
        *(_send(bridge, "c1", s, "快", f"i{i}") for i, s in enumerate(sessions[:3]))
    )
    starts = sorted(
        t["started_at"]
        for s in sessions[:3]
        for t in bridge.engine.store.list_turns(session_id=s)
        if t["request_id"].startswith("i")
    )
    assert starts[1] - starts[0] >= 0.9 and starts[2] - starts[1] >= 0.9
    refused = await bridge.admin("PUT", "/admin/settings", json={"min_start_interval_seconds": 61})
    assert refused.status_code == 422


async def test_queue_can_be_viewed_edited_and_reordered(bridge: Bridge) -> None:
    a = await _session(bridge, "c1")
    running = await _send(bridge, "c1", a, "sleep:0.6", "q1", wait=0)
    second = await _send(bridge, "c1", a, "第二条", "q2", wait=0)
    third = await _send(bridge, "c1", a, "第三条", "q3", wait=0)
    await asyncio.sleep(0.1)
    queue = (await bridge.admin("GET", f"/admin/sessions/{a}/queue")).json()
    assert queue["running"]["turn_id"] == running["turn_id"]
    assert [q["text"] for q in queue["queued"]] == ["第二条", "第三条"]
    assert queue["queued"][0]["by"] and queue["queued"][0]["status"] == "queued"
    # 调用方自己也能看（只读工具）；别的调用方看不到这个会话
    mine = await bridge.tool("c1", "llm_queue", {"session_id": a})
    assert [q["turn_id"] for q in mine["queued"]] == [second["turn_id"], third["turn_id"]]
    assert (await bridge.tool("c2", "llm_queue", {"session_id": a}))["error"]["code"] == "NOT_FOUND"

    # 第三条调到队首并改内容：先处理它
    moved = await bridge.admin(
        "POST", f"/admin/turns/{third['turn_id']}/move", json={"to": "first"}
    )
    assert [q["turn_id"] for q in moved.json()["queued"]] == [third["turn_id"], second["turn_id"]]
    edited = await bridge.admin(
        "PATCH", f"/admin/turns/{third['turn_id']}", json={"text": "改过的第三条"}
    )
    assert edited.json()["queued"][0]["text"] == "改过的第三条"
    ahead = await bridge.tool(
        "c1", "llm_turn_get", {"turn_id": second["turn_id"], "wait_seconds": 0}
    )
    assert ahead["ahead_in_session"] == 2
    # 已经开始的不能改、不能调；内容不能为空；不存在的 404
    for response in (
        await bridge.admin("PATCH", f"/admin/turns/{running['turn_id']}", json={"text": "x"}),
        await bridge.admin("POST", f"/admin/turns/{running['turn_id']}/move", json={"to": "last"}),
    ):
        assert response.status_code == 409, response.text
    assert (
        await bridge.admin("PATCH", f"/admin/turns/{second['turn_id']}", json={"text": "  "})
    ).status_code == 422
    assert (
        await bridge.admin("POST", "/admin/turns/turn_none/move", json={"to": "first"})
    ).status_code == 404

    for turn in (third, second):
        done = await bridge.tool(
            "c1", "llm_turn_get", {"turn_id": turn["turn_id"], "wait_seconds": 10}
        )
        assert done["status"] == "completed"
    turns = {t["id"]: t for t in bridge.engine.store.list_turns(session_id=a)}
    assert turns[third["turn_id"]]["finished_at"] <= turns[second["turn_id"]]["started_at"]
    assert turns[third["turn_id"]]["text"] == "改过的第三条"
    native = bridge.engine.store.get_session(a)["native_id"]
    assert "改过的第三条" in str(bridge.fake.history[native])
    empty = (await bridge.admin("GET", f"/admin/sessions/{a}/queue")).json()
    assert empty["running"] is None and empty["queued"] == []


async def test_old_database_gets_queue_positions(tmp_path: Path) -> None:
    """旧库升级：回合表加上队列顺序一列，已有的回合按入队时间回填。"""
    import sqlite3

    from bridge.store import SCHEMA_VERSION, Store

    path = tmp_path / "old.db"
    store = Store(path)
    store.create_session(
        id="s1", platform="fake", mode="chat", slot="fake", caller_id="c", caller_label="c"
    )
    store.create_turn("s1", "c", "r1", "旧的")
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE turns DROP COLUMN position")
    raw.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    raw.commit()
    raw.close()
    reopened = Store(path)
    (turn,) = reopened.list_turns(session_id="s1")
    assert turn["position"] == turn["queued_at"]
    assert reopened._one("SELECT value FROM meta WHERE key = 'schema_version'")["value"] == str(
        SCHEMA_VERSION
    )
    reopened.close()


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


async def _limited(
    bridge: Bridge, caller: str, platforms: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """像工作台那样带着“只能用这些平台”调用（X-Bridge-Platforms）。"""
    headers = {**bridge.headers(caller, "write,account,takeover"), "X-Bridge-Platforms": platforms}
    response = await bridge.rpc("tools/call", {"name": name, "arguments": arguments}, headers)
    payload: dict[str, Any] = json.loads(response.json()["result"]["content"][0]["text"])
    return payload


async def test_platform_limit_follows_the_session(bridge: Bridge) -> None:
    """管理员没给的平台：新建不了，别人建好的会话也读不了、发不了、取不了回合（T-0247）。

    发送、取回合这些调用不带 platform 参数，所以要按会话所属的平台检查。"""
    a = await _session(bridge, "owner")
    turn = await _send(bridge, "owner", a, "内容", "r1")
    # 只给了别的平台：有接手权限也一样
    assert (await _limited(bridge, "agent", "claude", "llm_session_create", CHAT))["error"][
        "code"
    ] == "FORBIDDEN"
    for name, arguments in (
        ("llm_session_get", {"session_id": a}),
        ("llm_send", {"session_id": a, "text": "x", "request_id": "p1"}),
        ("llm_turn_get", {"turn_id": turn["turn_id"]}),
        ("llm_turn_cancel", {"turn_id": turn["turn_id"]}),
        ("llm_queue", {"session_id": a}),
        ("llm_session_archive", {"session_id": a}),
    ):
        denied = await _limited(bridge, "agent", "claude", name, arguments)
        assert denied["error"]["code"] == "NOT_FOUND", (name, denied)
    listed = await _limited(bridge, "agent", "claude", "llm_session_list", {"scope": "account"})
    assert listed["sessions"] == []
    assert (await _limited(bridge, "agent", "claude", "llm_models", CHAT))["error"][
        "code"
    ] == "FORBIDDEN"
    status = await _limited(bridge, "agent", "claude", "llm_status", {})
    assert {c["platform"] for c in status["channels"]} <= {"claude"}
    assert "fake" not in status["accounts"]
    # 给了这个平台：照常能用；没带这个头（人在聊天页）不限
    seen = await _limited(bridge, "agent", "fake,claude", "llm_session_get", {"session_id": a})
    assert len(seen["messages"]) == 2
    only = await _limited(bridge, "agent", "fake", "llm_status", {})
    assert {c["platform"] for c in only["channels"]} == {"fake"}


async def test_create_with_request_id_is_idempotent(bridge: Bridge) -> None:
    """新建会话带幂等号：重试、同时到的两次调用，得到的都是同一个会话（T-0247）。"""
    args = {**CHAT, "request_id": "c-1"}
    first, second = await asyncio.gather(
        bridge.tool("c1", "llm_session_create", args),
        bridge.tool("c1", "llm_session_create", args),
    )
    again = await bridge.tool("c1", "llm_session_create", args)
    assert first["session_id"] == second["session_id"] == again["session_id"]
    listed = await bridge.tool("c1", "llm_session_list", {})
    assert len(listed["sessions"]) == 1
    # 幂等号按调用方算；不带的每次都新建
    other = await bridge.tool("c2", "llm_session_create", args)
    assert other["session_id"] != first["session_id"]
    plain = await bridge.tool("c1", "llm_session_create", CHAT)
    assert plain["session_id"] != first["session_id"]
    bad = await bridge.tool("c1", "llm_session_create", {**CHAT, "request_id": "有 空格"})
    assert bad["error"]["code"] == "VALIDATION_FAILED"


async def test_send_says_when_it_is_a_takeover_and_progress_is_the_latest(bridge: Bridge) -> None:
    a = await _session(bridge, "owner")
    mine = await _send(bridge, "owner", a, "内容", "r1")
    assert "takeover" not in mine
    taken = await bridge.tool(
        "boss",
        "llm_send",
        {"session_id": a, "text": "接手", "request_id": "b1"},
        scopes="write,account,takeover",
    )
    assert taken["takeover"] is True and taken["status"] == "completed"
    # 进度按回合取最新的一条：会话里之前有多少事件都不影响
    running = await _send(bridge, "owner", a, "sleep:0.6", "r2", wait=0)
    assert running["status"] in ("queued", "running")
    bridge.engine.store.add_event(a, running["turn_id"], "message", {"text": "第一段"})
    bridge.engine.store.add_event(a, running["turn_id"], "command", {"command": "ls -la"})
    latest = bridge.engine.store.last_progress(running["turn_id"])
    assert latest is not None and latest["payload"]["command"] == "ls -la"
    assert bridge.engine.store.last_progress(mine["turn_id"]) is not None


async def test_turn_view_says_what_it_waits_for_and_what_came_of_it(bridge: Bridge) -> None:
    """统一的回合状态（改进方案 3.1）：在等什么、用时、用量、接替的回合、失败能不能重试。"""
    a = await _session(bridge, "c1")
    done = await _send(bridge, "c1", a, "内容", "r1")
    assert done["timing"]["ran_seconds"] >= 0 and done["timing"]["finished_at"]
    assert "waiting" not in done and "next_turn_id" not in done
    # 会话里前面还有消息：排队的回合说明是在等它
    await _send(bridge, "c1", a, "sleep:0.5", "r2", wait=0)
    queued = await _send(bridge, "c1", a, "后面的", "r3", wait=0)
    assert queued["status"] == "queued"
    assert queued["waiting"] == {"reason": "session", "position": 1}
    # 平台因额度暂停：说明暂停到什么时候
    until = time.time() + 600
    bridge.engine.store.set_pause("fake", until, True, "quota")
    b = await _session(bridge, "c1")
    paused = await _send(bridge, "c1", b, "等额度", "r4", wait=0)
    assert paused["waiting"] == {"reason": "quota", "until": until, "estimated": True}
    bridge.engine.store.clear_pause("fake")
    # 失败的回合：说明能不能重发
    c = await _session(bridge, "c1")
    slow = await _send(bridge, "c1", c, "fail:timeout", "r5")
    # 超时：不知道做没做，先看会话再决定，不直接说“能重试”
    assert slow["status"] == "unknown" and slow["failure"]["retryable"] is None
    down = await _send(bridge, "c1", c, "fail:platform_error", "r7")
    assert down["status"] == "failed" and down["failure"]["retryable"] is True
    wrong = await _send(bridge, "c1", c, "fail:invalid", "r6")
    assert wrong["failure"]["retryable"] is False


async def test_source_is_kept_and_index_feed_follows_changes(bridge: Bridge) -> None:
    """出处随会话和回合存下来；会话的变更按序号给出，不含对话内容（改进方案 3.3、3.4）。"""
    from urllib.parse import quote

    source = {
        "kind": "agent",
        "instance_id": "i-1",
        "tasks": [{"task_id": "t-1", "title": "写周报"}],
    }
    headers = {**bridge.headers("i-1"), "X-Bridge-Source": quote(json.dumps(source))}

    async def tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await bridge.rpc("tools/call", {"name": name, "arguments": arguments}, headers)
        payload: dict[str, Any] = json.loads(response.json()["result"]["content"][0]["text"])
        return payload

    start = (await bridge.admin("GET", "/admin/index")).json()["last_rev"]
    created = await tool("llm_session_create", {**CHAT, "title": "周报"})
    assert created["source"] == source
    sent = await tool(
        "llm_send",
        {"session_id": created["session_id"], "text": "机密的内容", "request_id": "s1"},
    )
    assert sent["source"] == source and sent["status"] == "completed"

    page = (await bridge.admin("GET", "/admin/index", params={"after": start})).json()
    (item,) = page["sessions"]
    assert item["session_id"] == created["session_id"] and item["rev"] == page["last_rev"]
    assert (item["owner_id"], item["source"], item["turns_total"]) == ("i-1", source, 1)
    assert item["turns_failed"] == 0 and item["last_turn_at"] and item["tokens"] >= 0
    assert "机密的内容" not in json.dumps(page, ensure_ascii=False)
    # 没有新的变化：下一页是空的，序号不动
    again = (await bridge.admin("GET", "/admin/index", params={"after": page["last_rev"]})).json()
    assert again["sessions"] == [] and again["last_rev"] == page["last_rev"]
    assert again["pauses"] == []
    # 回合换了状态（失败）也算变化
    await tool(
        "llm_send",
        {"session_id": created["session_id"], "text": "fail:invalid", "request_id": "s2"},
    )
    later = (await bridge.admin("GET", "/admin/index", params={"after": page["last_rev"]})).json()
    assert [(s["turns_total"], s["turns_failed"]) for s in later["sessions"]] == [(2, 1)]
    # 读不懂的出处当作没有
    bad = await bridge.rpc(
        "tools/call",
        {"name": "llm_session_create", "arguments": CHAT},
        {**bridge.headers("i-2"), "X-Bridge-Source": "%7Bnot-json"},
    )
    assert json.loads(bad.json()["result"]["content"][0]["text"])["source"] is None


async def test_deltas_stream_while_running_and_are_dropped_afterwards(bridge: Bridge) -> None:
    """逐字的增量（改进方案 3.2）：回合进行中按编号读得到，刷新页面能接着显示；结束后不留。"""
    a = await _session(bridge, "c1")
    turn = await _send(bridge, "c1", a, "stream:0.3", "r1", wait=0)
    seen: list[str] = []
    partial = ""
    after = 0
    for _ in range(40):
        batch = (
            await bridge.admin(
                "GET", f"/admin/sessions/{a}/events", params={"after": after, "wait_seconds": 1}
            )
        ).json()
        after = batch["last_id"]
        seen += [e["text"] for e in batch["events"] if e["kind"] == "delta"]
        if seen and not partial:
            view = (await bridge.admin("GET", f"/admin/sessions/{a}")).json()
            partial = next(m for m in view["messages"] if m["role"] == "assistant").get(
                "partial", ""
            )
        if not batch["busy"]:
            break
    # 增量按顺序到，拼起来是那一段话的开头；进行中刷新页面拿得到已经写出来的部分
    assert "".join(seen) and "第一二三段".startswith("".join(seen)[:2])
    assert partial and "第一二三段".startswith(partial)
    done = await bridge.tool("c1", "llm_turn_get", {"turn_id": turn["turn_id"], "wait_seconds": 5})
    assert done["status"] == "completed" and done["result"].endswith("stream:0.3")
    # 结束后增量不留在事件里，完整的那一段在
    events = (await bridge.admin("GET", f"/admin/sessions/{a}/events")).json()["events"]
    kinds = [e["kind"] for e in events]
    assert "delta" not in kinds and "message" in kinds
    view = (await bridge.admin("GET", f"/admin/sessions/{a}")).json()
    reply = next(m for m in view["messages"] if m["role"] == "assistant")
    assert "partial" not in reply and reply["timing"]["ran_seconds"] >= 0.5


async def test_agent_reads_output_files_and_pages_from_the_newest(bridge: Bridge) -> None:
    """按编号读回合产出的文字文件；读会话可以从最新的开始；没做完的回合说明原因（T-0250）。"""
    made = await bridge.admin(
        "POST",
        "/admin/environments",
        json={"platform": "fake", "mode": "dev", "name": "空", "sandbox": "exec"},
    )
    assert made.status_code in (200, 201), made.text
    session = await bridge.tool(
        "c1",
        "llm_session_create",
        {"platform": "fake", "mode": "dev", "environment": made.json()["id"]},
    )
    a = session["session_id"]
    turn = await _send(bridge, "c1", a, "write:out/report.md", "r1")
    (output,) = turn["outputs"]
    assert output["name"].endswith("report.md")
    got = await bridge.tool(
        "c1", "llm_file_get", {"session_id": a, "file_id": output["id"], "max_chars": 4}
    )
    assert got["text"] == "gene" and got["next_offset"] == 4 and got["chars"] == len("generated\n")
    rest = await bridge.tool(
        "c1", "llm_file_get", {"session_id": a, "file_id": output["id"], "offset": 4}
    )
    assert rest["text"] == "rated\n" and rest["next_offset"] is None
    # 别人读不了；不是这个会话的文件读不了
    other = await bridge.tool("c2", "llm_file_get", {"session_id": a, "file_id": output["id"]})
    assert other["error"]["code"] == "NOT_FOUND"
    # 读会话：默认从最早的开始；newest 从最新的开始，页内仍按时间先后
    await _send(bridge, "c1", a, "fail:invalid", "r2")
    newest = await bridge.tool(
        "c1", "llm_session_get", {"session_id": a, "limit": 2, "order": "newest"}
    )
    assert newest["total"] == 4 and newest["next_offset"] == 2
    assert [m["role"] for m in newest["messages"]] == ["user", "status"]
    assert newest["messages"][1]["failure"]["retryable"] is False
    oldest = await bridge.tool("c1", "llm_session_get", {"session_id": a, "limit": 2})
    assert [m["role"] for m in oldest["messages"]] == ["user", "assistant"]
    # 给工作台的回合状态：不含内容
    status = (
        await bridge.admin("POST", "/admin/turns/status", json={"ids": [turn["turn_id"], "bt_x"]})
    ).json()["turns"]
    assert (
        status[turn["turn_id"]]["status"] == "completed" and status["bt_x"]["status"] == "missing"
    )
    assert "generated" not in json.dumps(status)


async def test_search_finds_text_in_questions_and_answers_and_usage_adds_up(bridge: Bridge) -> None:
    """全文搜索和用量汇总（改进方案 4.2 的 B1、B5）。"""
    a = await _session(bridge, "agent-1")
    await _send(bridge, "agent-1", a, "帮我写一份季度复盘", "r1")
    b = await _session(bridge, "user:u1")
    await _send(bridge, "user:u1", b, "今天天气怎么样", "r2")
    await _send(bridge, "user:u1", b, "fail:invalid", "r3")

    found = (await bridge.admin("GET", "/admin/search", params={"q": "季度复盘"})).json()["hits"]
    assert [h["session_id"] for h in found] == [a]
    # 回答里也有这几个字（仿真程序把提问原样带回来）：给的是最新的一处
    assert found[0]["role"] == "assistant" and "季度复盘" in found[0]["snippet"]
    assert (await bridge.admin("GET", "/admin/search", params={"q": "没有这句话"})).json() == {
        "hits": []
    }
    # 通配符当普通字符；太短的不搜
    assert (await bridge.admin("GET", "/admin/search", params={"q": "%_"})).json()["hits"] == []
    assert (await bridge.admin("GET", "/admin/search", params={"q": "x"})).status_code == 422

    rows = (await bridge.admin("GET", "/admin/usage")).json()["rows"]
    by_owner = {r["owner_id"]: r for r in rows}
    assert by_owner["agent-1"]["turns"] == 1 and by_owner["agent-1"]["failed"] == 0
    assert by_owner["user:u1"]["turns"] == 2 and by_owner["user:u1"]["failed"] == 1
    assert by_owner["agent-1"]["tokens"] > 0 and by_owner["agent-1"]["platform"] == "fake"
    assert by_owner["agent-1"]["day"] == time.strftime("%Y-%m-%d", time.gmtime())
    # 没有报积分的平台：积分是空的，不是 0
    assert by_owner["agent-1"]["credits"] is None
    quota = (await bridge.admin("GET", "/admin/quota")).json()["platforms"]["fake"]
    assert quota["credits_5h"] is None


async def test_reported_credits_show_on_the_turn_and_add_up(bridge: Bridge) -> None:
    """官方程序报了这一回合消耗的积分（CodeBuddy）：回合上带着，用量和最近 5 小时里相加。"""
    a = await _session(bridge, "agent-1")
    first = await _send(bridge, "agent-1", a, "credits:2.5 第一问", "r1")
    assert first["usage"]["credits"] == 2.5 and first["usage"]["credit_status"] == "known"
    await _send(bridge, "agent-1", a, "credits:0.75 第二问", "r2")
    await _send(bridge, "agent-1", a, "没有报积分的一问", "r3")

    (row,) = (await bridge.admin("GET", "/admin/usage")).json()["rows"]
    assert row["turns"] == 3 and row["credits"] == 3.25
    quota = (await bridge.admin("GET", "/admin/quota")).json()["platforms"]["fake"]
    assert quota["credits_5h"] == 3.25


def _init_repo(workdir: Path) -> None:
    """把工作目录变成一个有一次提交的 git 仓库（a.txt 的内容是 old）。"""
    import subprocess

    git = ["git", "-C", str(workdir), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    (workdir / "a.txt").write_text("old\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)


async def test_dev_turn_leaves_a_patch_and_retention_clears_content(bridge: Bridge) -> None:
    """开发回合留下改动补丁（归档后还在）；保留期到了只清内容（改进方案 4.2 的 B3、B8）。"""
    made = await bridge.admin(
        "POST",
        "/admin/environments",
        json={"platform": "fake", "mode": "dev", "name": "仓库", "sandbox": "exec"},
    )
    session = await bridge.tool(
        "c1",
        "llm_session_create",
        {"platform": "fake", "mode": "dev", "environment": made.json()["id"]},
    )
    a = session["session_id"]
    workdir = Path(bridge.engine.store.get_session(a)["workdir"])  # type: ignore[index]
    _init_repo(workdir)
    (workdir / "a.txt").write_text("new\n")
    turn = await _send(bridge, "c1", a, "write:out/b.txt", "r1")
    names = [o["name"] for o in turn["outputs"]]
    assert "改动（到这一回合为止）.patch" in names
    patch = next(o for o in turn["outputs"] if o["name"].endswith(".patch"))
    text = (await bridge.tool("c1", "llm_file_get", {"session_id": a, "file_id": patch["id"]}))[
        "text"
    ]
    assert "-old" in text and "+new" in text and "out/b.txt" in text
    # 没有新的改动：下一回合不再存一份一样的
    again = await _send(bridge, "c1", a, "没改东西", "r2")
    assert not [o for o in again.get("outputs", []) if o["name"].endswith(".patch")]
    # 归档后工作目录删了，补丁还在
    await bridge.tool("c1", "llm_session_archive", {"session_id": a})
    assert not await asyncio.to_thread(workdir.exists)
    kept = await bridge.admin("GET", f"/admin/sessions/{a}/attachments/{patch['id']}")
    assert kept.status_code == 200 and b"+new" in kept.content

    # 保留期：默认一直保留；设了之后，最后活动早于它的会话清掉内容，回合的状态和用量留着
    assert bridge.engine.purge_expired() == 0
    assert (
        await bridge.admin("PUT", "/admin/settings", json={"retention_days": 1})
    ).status_code == 200
    assert bridge.engine.purge_expired() == 0  # 刚活动过的不清
    bridge.engine.store._exec("UPDATE sessions SET updated_at = updated_at - 3 * 86400")
    assert bridge.engine.purge_expired() == 1
    view = (await bridge.admin("GET", f"/admin/sessions/{a}")).json()
    assert view["session"]["purged_at"] and view["session"]["turns"] == 2
    assert all(not m["text"] for m in view["messages"])
    statuses = [m["status"] for m in view["messages"] if m["role"] == "assistant"]
    assert statuses == ["completed", "completed"]
    assert (
        await bridge.admin("GET", f"/admin/sessions/{a}/attachments/{patch['id']}")
    ).status_code == 404
    assert (await bridge.admin("GET", "/admin/search", params={"q": "没改东西"})).json()[
        "hits"
    ] == []
    usage = (await bridge.admin("GET", "/admin/usage")).json()["rows"]
    assert sum(r["turns"] for r in usage) == 2
    assert bridge.engine.purge_expired() == 0  # 清过的不再清


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


async def test_key_logins_are_one_unknown_account(bridge: Bridge) -> None:
    """用密钥登录时看不到是哪个账号：换密钥不算换账号；换成具体账号才算。"""
    bridge.fake.anonymous = True
    await bridge.admin("POST", "/admin/accounts/fake/reset")
    a = await _session(bridge, "c1")
    account = (await bridge.admin("GET", "/admin/status")).json()["accounts"]["fake"]
    assert (account["state"], account["identity"], account["registered"]) == (
        "logged_in",
        None,
        True,
    )
    assert account["method"] == "api_key" and "换一把密钥不算换账号" in account["detail"]
    # 换一把密钥（平台仍然不报告账号）：照常可用
    await bridge.admin(
        "POST", "/admin/accounts/fake/login", json={"method": "api_key", "api_key": "k2"}
    )
    assert (await _send(bridge, "c1", a, "换了密钥", "r1"))["status"] == "completed"
    # 换成一个报告了邮箱的登录：这是另一个账号
    bridge.fake.anonymous = False
    bridge.fake.identity = "someone@example.com"
    refreshed = (await bridge.admin("POST", "/admin/accounts/fake/refresh")).json()
    assert refreshed["state"] == "mismatch" and "看不到是哪个" in refreshed["detail"]
    assert (await _send(bridge, "c1", a, "x", "r2"))["error"]["code"] == "ACCOUNT_MISMATCH"
    # 退出登录后：登记还在（registered），但不再显示登录方式
    await bridge.admin("POST", "/admin/accounts/fake/reset")
    await bridge.admin("POST", "/admin/accounts/fake/refresh")
    await bridge.admin("POST", "/admin/accounts/fake/logout")
    account = (await bridge.admin("GET", "/admin/status")).json()["accounts"]["fake"]
    assert (account["state"], account["registered"], account["method"]) == (
        "logged_out",
        True,
        None,
    )


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


async def test_controlled_platforms(bridge: Bridge) -> None:
    """一座桥可以只控制一部分平台：没勾选的不能登录、不能新建和发送，已有的记录还能看。"""
    status = (await bridge.admin("GET", "/admin/status")).json()
    everything = ["chatgpt", "codex", "claude", "workbuddy", "fake"]
    assert status["platforms"] == status["settings"]["platforms"] == everything
    assert all(a["enabled"] for a in status["accounts"].values())
    a = await _session(bridge, "c1")
    assert (await _send(bridge, "c1", a, "之前", "r1"))["status"] == "completed"

    # 1 号管理器只控制 claude / workbuddy（顺序按通道表，不按提交的顺序）
    saved = await bridge.admin(
        "PUT", "/admin/settings", json={"platforms": ["workbuddy", "claude"]}
    )
    assert saved.json()["platforms"] == ["claude", "workbuddy"]
    status = (await bridge.admin("GET", "/admin/status")).json()
    assert status["platforms"] == ["claude", "workbuddy"]
    enabled = {slot: account["enabled"] for slot, account in status["accounts"].items()}
    assert enabled == {"openai": False, "claude": True, "workbuddy": True, "fake": False}
    channels = {(c["platform"], c["mode"]): c for c in status["channels"]}
    assert (
        not channels[("codex", "chat")]["enabled"] and not channels[("codex", "chat")]["available"]
    )
    assert channels[("claude", "chat")]["enabled"] and channels[("claude", "chat")]["available"]
    # 智能体看到的概况里只有这座桥控制的平台
    seen = await bridge.tool("c1", "llm_status")
    assert set(seen["accounts"]) == {"claude", "workbuddy"}
    assert {c["platform"] for c in seen["channels"]} == {"claude", "workbuddy"}

    # 没勾选的平台：不能新建、不能查模型和环境、不能向已有会话发送、不能登录
    for tool, arguments in (
        ("llm_session_create", {"platform": "codex", "mode": "chat"}),
        ("llm_models", {"platform": "chatgpt", "mode": "chat"}),
        ("llm_environments", {"platform": "codex", "mode": "dev"}),
        ("llm_session_create", {"platform": "fake", "mode": "chat"}),
    ):
        refused = await bridge.tool("c1", tool, arguments)
        assert refused["error"]["code"] == "NOT_SUPPORTED", (tool, refused)
        assert "没有启用" in refused["error"]["message"]
        assert "claude、workbuddy" in refused["error"]["message"]
    assert (await _send(bridge, "c1", a, "之后", "r2"))["error"]["code"] == "NOT_SUPPORTED"
    for slot in ("openai", "fake"):
        login = await bridge.admin(
            "POST", f"/admin/accounts/{slot}/login", json={"method": "api_key", "api_key": "k"}
        )
        assert login.status_code == 409 and login.json()["error"]["code"] == "CONFLICT", login.text
    # 已有的会话记录还能看；账号下全部会话不去读没勾选的平台
    detail = await bridge.tool("c1", "llm_session_get", {"session_id": a})
    assert [m["text"] for m in detail["messages"]] == ["之前", "[fake-1] 之前"]
    listed = await bridge.tool("c1", "llm_session_list", {"scope": "account"}, scopes="account")
    assert "Fake" not in listed["note"]
    # 已有的登录可以退出
    assert (await bridge.admin("POST", "/admin/accounts/fake/logout")).status_code == 200

    # 2 号管理器控制 claude / workbuddy / codex：openai 槽位可用，但 ChatGPT 聊天通道没开
    saved = await bridge.admin(
        "PUT", "/admin/settings", json={"platforms": ["claude", "workbuddy", "codex", "fake"]}
    )
    assert saved.json()["platforms"] == ["codex", "claude", "workbuddy", "fake"]
    status = (await bridge.admin("GET", "/admin/status")).json()
    assert status["accounts"]["openai"]["enabled"]
    channels = {(c["platform"], c["mode"]): c for c in status["channels"]}
    assert channels[("codex", "chat")]["available"] and not channels[("chatgpt", "chat")]["enabled"]
    refused = await bridge.tool("c1", "llm_session_create", {"platform": "chatgpt", "mode": "chat"})
    assert refused["error"]["code"] == "NOT_SUPPORTED"
    # 重新勾选后，原来的会话可以接着用
    await bridge.admin(
        "POST",
        "/admin/accounts/fake/login",
        json={"method": "api_key", "api_key": "fake@example.com"},
    )
    assert (await _send(bridge, "c1", a, "回来了", "r3"))["status"] == "completed"

    # 校验：至少一个、不能重复、不能是这座桥没有的平台；其他设置不受影响
    for bad in ([], ["claude", "claude"], ["nope"], "claude", [1]):
        refused = await bridge.admin("PUT", "/admin/settings", json={"platforms": bad})
        assert refused.json()["error"]["code"] == "VALIDATION_FAILED", bad
    kept = await bridge.admin("PUT", "/admin/settings", json={"concurrency": 3})
    assert kept.json()["concurrency"] == 3
    assert kept.json()["platforms"] == ["codex", "claude", "workbuddy", "fake"]


async def test_chat_page_admin_views(bridge: Bridge) -> None:
    """聊天页用的管理接口：列表的搜索与筛选、会话内容、陆续出现的事件、改标题。"""
    human, agent = "user:u1", "openclaw-1"
    mine = await _session(bridge, human, title="检查登录的竞态")
    theirs = await _session(bridge, agent)
    assert (await _send(bridge, agent, theirs, "你好", "a1"))["status"] == "completed"
    first = await _send(bridge, human, mine, "work:0", "h1")
    assert first["status"] == "completed"

    # 列表：谁建的、是否有回合在进行、搜索、筛选、分页
    listed = (await bridge.admin("GET", "/admin/sessions")).json()
    by_id = {s["session_id"]: s for s in listed["sessions"]}
    assert by_id[mine]["owner_id"] == human and by_id[theirs]["owner_id"] == agent
    assert not by_id[mine]["busy"] and listed["next_offset"] is None
    found = (await bridge.admin("GET", "/admin/sessions", params={"q": "登录"})).json()
    assert [s["session_id"] for s in found["sessions"]] == [mine]
    # 搜索里的 % 和 _ 按字面处理，不当通配符
    assert (await bridge.admin("GET", "/admin/sessions", params={"q": "%"})).json()[
        "sessions"
    ] == []
    none = (await bridge.admin("GET", "/admin/sessions", params={"platform": "codex"})).json()
    assert none["sessions"] == []
    paged = (await bridge.admin("GET", "/admin/sessions", params={"limit": 1})).json()
    assert len(paged["sessions"]) == 1 and paged["next_offset"] == 1

    # 会话内容：谁发的、回答、过程（最后一段就是结果，不重复出现）
    view = (await bridge.admin("GET", f"/admin/sessions/{mine}")).json()
    assert view["session"]["title"] == "检查登录的竞态" and view["session"]["environment"] is None
    user, reply = view["messages"]
    assert (user["role"], user["text"], user["by"], user["by_id"]) == (
        "user",
        "work:0",
        human,
        human,
    )
    assert (reply["role"], reply["status"], reply["text"]) == (
        "assistant",
        "completed",
        "[fake-1] work:0",
    )
    assert [(s["kind"], s["text"]) for s in reply["steps"]] == [
        ("message", "先看一下代码"),
        ("command", "ls src"),
        ("files", "src/a.py"),
    ]
    assert view["last_event_id"] > 0
    assert (await bridge.admin("GET", "/admin/sessions/bs_nope")).status_code == 404

    # 官方程序在同一条消息里既回答又调工具时，回答的事件排在工具前面：过程里同样不重复出现回答
    assert (await _send(bridge, agent, theirs, "tools-after:1", "a2"))["status"] == "completed"
    after = (await bridge.admin("GET", f"/admin/sessions/{theirs}")).json()["messages"][-1]
    assert after["text"] == "[fake-1] tools-after:1"
    assert [(s["kind"], s["text"]) for s in after["steps"]] == [
        ("command", "ls -la"),
        ("files", "src/b.py"),
    ]

    # 进行中的回合：先拿到已经给出的部分，再长轮询等后面的事件
    running = await _send(bridge, human, mine, "work:0.6", "h2", wait=0)
    assert running["status"] in ("queued", "running")
    cursor = view["last_event_id"]
    kinds: list[str] = []
    for _ in range(20):
        batch = (
            await bridge.admin(
                "GET",
                f"/admin/sessions/{mine}/events",
                params={"after": cursor, "wait_seconds": 5},
            )
        ).json()
        kinds += [e["kind"] for e in batch["events"]]
        cursor = batch["last_id"]
        if not batch["busy"] and not batch["events"]:
            break
    assert kinds == ["queued", "started", "message", "command", "files", "message", "completed"]
    partial_text = [e for e in kinds if e == "message"]
    assert len(partial_text) == 2
    # 没有回合在进行时不等待，立刻返回
    started = time.monotonic()
    idle = (
        await bridge.admin(
            "GET", f"/admin/sessions/{mine}/events", params={"after": cursor, "wait_seconds": 20}
        )
    ).json()
    assert idle == {"events": [], "busy": False, "last_id": cursor}
    assert time.monotonic() - started < 2

    # 没做完的回合：会话内容里带已经给出的部分和失败原因
    failed = await _send(bridge, human, mine, "fail:platform_error", "h3")
    assert failed["status"] == "failed"
    view = (await bridge.admin("GET", f"/admin/sessions/{mine}")).json()
    last = view["messages"][-1]
    assert last["status"] == "failed" and last["failure"]["kind"] == "platform_error"

    # 改标题：不改变列表里的先后；密钥照常拦截；空标题不行
    before = [
        s["session_id"] for s in (await bridge.admin("GET", "/admin/sessions")).json()["sessions"]
    ]
    renamed = await bridge.admin(
        "PATCH", f"/admin/sessions/{theirs}", json={"title": "  打个  招呼 "}
    )
    assert renamed.status_code == 200 and renamed.json()["title"] == "打个 招呼"
    after = [
        s["session_id"] for s in (await bridge.admin("GET", "/admin/sessions")).json()["sessions"]
    ]
    assert before == after
    secret = await bridge.admin(
        "PATCH", f"/admin/sessions/{theirs}", json={"title": "key sk-ant-api03-" + "x" * 40}
    )
    assert "sk-ant-api03" not in secret.json()["title"]
    assert (
        await bridge.admin("PATCH", f"/admin/sessions/{theirs}", json={"title": "   "})
    ).status_code == 422
    # 归档的会话可以单独筛出来
    await bridge.tool(agent, "llm_session_archive", {"session_id": theirs})
    archived = (await bridge.admin("GET", "/admin/sessions", params={"status": "archived"})).json()
    assert [s["session_id"] for s in archived["sessions"]] == [theirs]
    active = (await bridge.admin("GET", "/admin/sessions", params={"status": "active"})).json()
    assert [s["session_id"] for s in active["sessions"]] == [mine]


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


async def test_platform_failure_during_login_is_reported_not_hidden(bridge: Bridge) -> None:
    """登录时平台那边出错：管理接口带着原因回 424，不回 5xx——工作台会把 5xx 当成“管理器连不上”。"""
    bridge.fake.login_error = "向平台申请验证码没有成功：连不上平台"
    failed = await bridge.admin(
        "POST", "/admin/accounts/fake/login", json={"method": "api_key", "api_key": "k"}
    )
    assert failed.status_code == 424, failed.text
    assert failed.json()["error"] == {
        "code": "UNAVAILABLE",
        "message": "向平台申请验证码没有成功：连不上平台",
    }
    bridge.fake.login_error = None
    ok = await bridge.admin(
        "POST", "/admin/accounts/fake/login", json={"method": "api_key", "api_key": "k"}
    )
    assert ok.status_code == 200, ok.text


async def test_connectivity_reports_each_platform_with_latency(bridge: Bridge) -> None:
    """到各平台能不能用、多慢：逐个地址报，分清“连不上”和“被平台拒绝”；一分钟内复用结果。"""

    from bridge import connectivity
    from bridge.drivers.fake import FakeDriver

    calls: list[str] = []

    async def probe(target: connectivity.Target) -> dict[str, Any]:
        calls.append(target.host)
        if target.host == "auth.openai.com":  # 连得上，但平台回的是拦截页
            note = "平台拒绝了这台服务器的访问（HTTP 403，拦截页）"
            return {"state": "blocked", "latency_ms": 12, "http_status": 403, "error": note}
        if target.host == "copilot.tencent.com":
            note = "超时（8 秒内没有应答）"
            return {"state": "unreachable", "latency_ms": None, "http_status": None, "error": note}
        return {"state": "ok", "latency_ms": 42, "http_status": 401, "error": None}

    bridge.engine.probe_host = probe
    for slot in ("openai", "claude", "workbuddy"):
        bridge.engine.drivers.setdefault(slot, FakeDriver())
    slots = (await bridge.admin("GET", "/admin/connectivity")).json()["slots"]
    assert set(slots) == {"openai", "claude", "workbuddy"}  # 仿真槽位没有平台地址，不列
    # OpenAI 要三个地址都可用；有一个被拒绝就算用不了，延迟照样给
    assert slots["openai"]["ok"] is False and slots["openai"]["need"] == "all"
    blocked = slots["openai"]["targets"][1]
    assert (blocked["host"], blocked["ok"], blocked["state"], blocked["latency_ms"]) == (
        "auth.openai.com",
        False,
        "blocked",
        12,
    )
    assert "拒绝" in blocked["error"]
    assert slots["claude"]["ok"] is True
    assert [t["latency_ms"] for t in slots["claude"]["targets"]] == [42, 42]
    # CodeBuddy 国内站、国际站有一个可用就行
    assert slots["workbuddy"]["ok"] is True and slots["workbuddy"]["need"] == "any"
    assert slots["workbuddy"]["targets"][0]["state"] == "unreachable"
    count = len(calls)
    await bridge.admin("GET", "/admin/connectivity")
    assert len(calls) == count
    await bridge.admin("GET", "/admin/connectivity", params={"refresh": "true"})
    assert len(calls) == count * 2
    denied = await bridge.client.get("/admin/connectivity")
    assert denied.status_code in (401, 403)
    # 真的去连一个本机没人听的端口：报连不上，不抛异常
    real = await connectivity.probe(connectivity.Target("localhost", "本机"))
    assert real["state"] == "unreachable" and real["latency_ms"] is None and real["error"]


def test_connectivity_tells_a_block_page_from_a_normal_answer() -> None:
    """平台回了什么算可用、什么算被拒绝。拦截页的样子取自一台被 OpenAI 拦截的服务器上的实测。"""
    from bridge.connectivity import classify

    openai_block = (
        '<div class="message"><p>Unable to load site</p></div><span class="explanation">'
        "Please try again later. If you are using a VPN, try turning it off.</span>"
        '<span class="explanation"> [IP:203.0.113.7 | Ray ID:a5ad0c6498b9e8d4] </span>'
        "<script>/cdn-cgi/challenge-platform/scripts/jsd/main.js</script>"
    )
    assert classify(403, {}, openai_block)[0] == "blocked"
    assert classify(200, {}, openai_block)[0] == "blocked"  # 不管状态码，拦截页就是被拒绝
    region = '{"error": {"code": "unsupported_country_region_territory"}}'
    assert classify(403, {}, region)[0] == "blocked"
    assert classify(403, {}, "forbidden")[0] == "blocked"
    assert classify(451, {}, "")[0] == "blocked"
    # 没带密钥被要求登录、找不到页面：平台正常应答了，算可用
    for status in (200, 301, 401, 404, 421):
        assert classify(status, {}, '{"error": "missing api key"}') == ("ok", None)
    # 普通的人机验证页不算被拒绝
    assert (
        classify(403, {"cf-mitigated": "challenge"}, "<title>Just a moment...</title>")[0] == "ok"
    )
    assert classify(403, {}, "<title>Just a moment...</title>")[0] == "ok"
