"""清空上下文（T-0255）：同一个会话换一个新的官方会话，之后的回合从零开始，之前的记录保留。"""

from __future__ import annotations

import time
from typing import Any

from bridge.drivers.base import NativeSession
from tests.conftest import Bridge

HUMAN, AGENT = "user:u1", "openclaw-1"


async def _session(bridge: Bridge, caller: str = HUMAN, **extra: object) -> str:
    created = await bridge.tool(
        caller, "llm_session_create", {"platform": "fake", "mode": "chat", **extra}
    )
    assert "error" not in created, created
    return str(created["session_id"])


async def _send(
    bridge: Bridge, session: str, text: str, rid: str, wait: float = 10, caller: str = HUMAN
) -> Any:
    return await bridge.tool(
        caller,
        "llm_send",
        {"session_id": session, "text": text, "request_id": rid, "wait_seconds": wait},
    )


def _native(bridge: Bridge, session: str) -> str:
    row = bridge.engine.store.get_session(session)
    assert row is not None
    return str(row["native_id"])


async def test_clear_starts_a_new_native_session_and_keeps_the_record(bridge: Bridge) -> None:
    session = await _session(bridge)
    assert (await _send(bridge, session, "第一问", "r1"))["status"] == "completed"
    assert (await _send(bridge, session, "第二问", "r2"))["status"] == "completed"
    old = _native(bridge, session)
    assert [m.text for m in bridge.fake.history[old]] == [
        "第一问",
        "[fake-1] 第一问",
        "第二问",
        "[fake-1] 第二问",
    ]

    cleared = await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={"by": "张三"})
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["cleared"] is True and body["cleared_at"] > 0 and body["session_id"] == session
    assert body["turns"] == 2  # 会话一共的回合数不变
    new = _native(bridge, session)
    assert new != old and bridge.fake.history[new] == []

    # 之后的回合交给新的官方会话：模型看不到清空之前的内容
    assert (await _send(bridge, session, "第三问", "r3"))["status"] == "completed"
    assert [m.text for m in bridge.fake.history[new]] == ["第三问", "[fake-1] 第三问"]
    assert len(bridge.fake.history[old]) == 4

    # 会话内容：之前的记录都在，清空的位置有一条分隔
    view = (await bridge.admin("GET", f"/admin/sessions/{session}")).json()
    roles = [(m["role"], m["text"]) for m in view["messages"]]
    assert roles == [
        ("user", "第一问"),
        ("assistant", "[fake-1] 第一问"),
        ("user", "第二问"),
        ("assistant", "[fake-1] 第二问"),
        ("context_cleared", ""),
        ("user", "第三问"),
        ("assistant", "[fake-1] 第三问"),
    ]
    divider = view["messages"][4]
    assert divider["by"] == "张三" and divider["turns"] == 2
    assert view["session"]["context_cleared_at"] == body["cleared_at"]
    # 页面跟着事件刷新：清空本身也是一条事件
    events = (
        await bridge.admin("GET", f"/admin/sessions/{session}/events", params={"after": 0})
    ).json()["events"]
    assert any(e["kind"] == "cleared" and e["by"] == "张三" for e in events)

    # 智能体读会话也看得到清空的位置
    read = await bridge.tool(HUMAN, "llm_session_get", {"session_id": session})
    assert [(m["role"], m["text"]) for m in read["messages"]][3:6] == [
        ("assistant", "[fake-1] 第二问"),
        ("status", "context_cleared"),
        ("user", "第三问"),
    ]


async def test_clear_with_nothing_new_does_nothing(bridge: Bridge) -> None:
    session = await _session(bridge)
    native = _native(bridge, session)
    empty = (await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})).json()
    assert empty["cleared"] is False and _native(bridge, session) == native
    await _send(bridge, session, "你好", "r1")
    assert (await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})).json()[
        "cleared"
    ]
    # 清空之后还没发过消息：再清一次什么也不做，也不多一条分隔
    again = await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})
    assert again.json()["cleared"] is False
    messages = (await bridge.admin("GET", f"/admin/sessions/{session}")).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "context_cleared"]
    assert messages[-1]["by"] is None


async def test_clear_is_refused_while_busy_archived_or_external(bridge: Bridge) -> None:
    session = await _session(bridge)
    await _send(bridge, session, "你好", "r1")
    running = await _send(bridge, session, "sleep:5", "r2", wait=0)
    queued = await _send(bridge, session, "排队的", "r3", wait=0)
    busy = await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "CONFLICT"
    for turn in (queued, running):
        await bridge.tool(HUMAN, "llm_turn_cancel", {"turn_id": turn["turn_id"]})
    for turn in (queued, running):  # 等两个回合都停下来
        done = await bridge.tool(
            HUMAN, "llm_turn_get", {"turn_id": turn["turn_id"], "wait_seconds": 10}
        )
        assert done["status"] == "cancelled", done
    assert (await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})).json()[
        "cleared"
    ]

    await bridge.tool(HUMAN, "llm_session_archive", {"session_id": session})
    archived = await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})
    assert archived.status_code == 409

    bridge.fake.external = [NativeSession("native-ext", "别处的会话", "fake-1", "/w", 1.0, 2.0)]
    listed = await bridge.tool(
        "boss", "llm_session_list", {"scope": "account"}, scopes="write,account,takeover"
    )
    (external,) = [s for s in listed["sessions"] if s["origin"] == "external"]
    refused = await bridge.admin("POST", f"/admin/sessions/{external['session_id']}/clear", json={})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "NOT_SUPPORTED"
    assert (await bridge.admin("POST", "/admin/sessions/bs_nope/clear", json={})).status_code == 404


async def test_owner_guard_lets_the_workbench_ask_before_clearing_others(bridge: Bridge) -> None:
    """带 owner 时只清这个调用方自己的会话：别人的回答 409 NOT_OWNER，不清。"""
    session = await _session(bridge, AGENT)
    await _send(bridge, session, "你好", "r1", caller=AGENT)
    native = _native(bridge, session)
    refused = await bridge.admin(
        "POST", f"/admin/sessions/{session}/clear", json={"owner": HUMAN, "by": "张三"}
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "NOT_OWNER"
    assert _native(bridge, session) == native
    mine = await bridge.admin(
        "POST", f"/admin/sessions/{session}/clear", json={"owner": AGENT, "by": "x"}
    )
    assert mine.json()["cleared"] is True and _native(bridge, session) != native


async def test_old_native_session_is_not_registered_again(bridge: Bridge) -> None:
    """清空前的官方会话还留在官方程序里：同步会话时认得出它属于哪个会话，不再登记一次。"""
    session = await _session(bridge, AGENT)
    await _send(bridge, session, "你好", "r1", caller=AGENT)
    old = _native(bridge, session)
    assert (await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})).json()[
        "cleared"
    ]
    new = _native(bridge, session)
    bridge.fake.external = [
        NativeSession(old, "你好", "fake-1", "/w", 1.0, 2.0),
        NativeSession(new, "", "fake-1", "/w", 3.0, 4.0),
    ]
    listed = (await bridge.admin("GET", "/admin/sessions", params={"sync": "true"})).json()
    assert [s["session_id"] for s in listed["sessions"]] == [session]


async def test_pending_auto_resume_is_dropped_when_the_context_is_cleared(bridge: Bridge) -> None:
    session = await _session(bridge)
    bridge.engine.store.set_setting("auto_resume", True)
    bridge.fake.quota_until = time.time() + 30
    hit = await _send(bridge, session, "长任务", "r1")
    assert hit["status"] == "interrupted" and hit["resume"]["state"] == "pending"
    cleared = await bridge.admin("POST", f"/admin/sessions/{session}/clear", json={})
    assert cleared.json()["cleared"] is True
    turn = bridge.engine.store.get_turn(hit["turn_id"])
    assert turn is not None and turn["resume_state"] == "skipped"
    assert bridge.engine.store.pending_resumes("fake") == []
