"""插入消息（T-0236 第三步，docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 5 节）。

会话在跑时 mode=insert：平台收得下就把消息送进正在跑的回合；收不下就中断它，
把新消息和“接着刚才的做”合成一个新回合排到最前面。消息不会丢。
"""

import asyncio
import time
from typing import Any

from bridge.engine import MERGED_SUFFIX
from tests.conftest import Bridge


async def _session(bridge: Bridge) -> str:
    created = await bridge.tool(
        "c1", "llm_session_create", {"platform": "fake", "mode": "chat", "model": "fake-1"}
    )
    return str(created["session_id"])


async def _send(bridge: Bridge, session: str, text: str, rid: str, **extra: Any) -> Any:
    arguments = {"session_id": session, "text": text, "request_id": rid, "wait_seconds": 10}
    return await bridge.tool("c1", "llm_send", {**arguments, **extra})


async def _running(bridge: Bridge, session: str, text: str, rid: str) -> str:
    """发一条会跑一阵的消息，等它真的开始处理。"""
    turn = await _send(bridge, session, text, rid, wait_seconds=0)
    deadline = time.monotonic() + 5
    while bridge.engine.store.running_turn(session) is None:
        assert time.monotonic() < deadline, "回合一直没有开始"
        await asyncio.sleep(0.02)
    return str(turn["turn_id"])


async def _messages(bridge: Bridge, session: str) -> list[dict[str, Any]]:
    response = await bridge.admin("GET", f"/admin/sessions/{session}")
    assert response.status_code == 200, response.text
    messages: list[dict[str, Any]] = response.json()["messages"]
    return messages


async def test_insert_into_an_idle_session_is_an_ordinary_send(bridge: Bridge) -> None:
    session = await _session(bridge)
    done = await _send(bridge, session, "你好", "t1", mode="insert")
    assert (done["status"], done["delivered"]) == ("completed", "started")
    plain = await _send(bridge, session, "再来", "t2")
    assert plain["delivered"] == "started"


async def test_insert_goes_into_the_running_turn_when_the_platform_takes_it(bridge: Bridge) -> None:
    """平台收得下：消息进正在跑的回合，不产生新回合，也不打断它。"""
    bridge.fake.steerable = True
    session = await _session(bridge)
    first = await _running(bridge, session, "sleep:0.6", "t1")
    queued = await _send(bridge, session, "排队的", "t2", wait_seconds=0)
    assert queued["delivered"] == "queued"
    inserted = await _send(bridge, session, "顺便看一下日志", "t3", mode="insert")
    assert inserted["delivered"] == "inserted" and inserted["turn_id"] == first
    assert inserted["status"] == "completed"  # 等的是它进去的那个回合
    assert bridge.fake.steered == ["顺便看一下日志"]
    # 同一个幂等号重试：不再插一次
    again = await _send(bridge, session, "顺便看一下日志", "t3", mode="insert")
    assert again["delivered"] == "inserted" and bridge.fake.steered == ["顺便看一下日志"]
    await bridge.tool("c1", "llm_turn_get", {"turn_id": queued["turn_id"], "wait_seconds": 10})

    turns = bridge.engine.store.list_turns(session_id=session)
    assert [(t["text"], t["status"]) for t in turns] == [
        ("sleep:0.6", "completed"),
        ("排队的", "completed"),
    ]
    view = await _messages(bridge, session)
    assert [(m["role"], m["text"], m.get("delivery")) for m in view[:3]] == [
        ("user", "sleep:0.6", "turn"),
        ("user", "顺便看一下日志", "inserted"),
        ("assistant", "[fake-1] sleep:0.6", None),
    ]
    assert view[1]["turn_id"] == first and view[1]["by"] == "c1"
    # 插进去的消息也计入每小时的发送次数
    bridge.engine.store.set_setting("send_per_hour", 3)
    limited = await _send(bridge, session, "再发", "t4")
    assert limited["error"]["code"] == "RATE_LIMITED"


async def test_insert_falls_back_to_interrupt_and_merge(bridge: Bridge) -> None:
    """平台收不下：中断正在跑的回合（不是失败），新消息带着“接着做”成为下一个回合，排在最前面。"""
    session = await _session(bridge)
    first = await _running(bridge, session, "sleep:30", "t1")
    waiting = await _send(bridge, session, "原来排队的", "t2", wait_seconds=0)
    merged = await _send(bridge, session, "先改这里", "t3", mode="insert")
    assert (merged["status"], merged["delivered"]) == ("completed", "merged")
    assert merged["turn_id"] not in (first, waiting["turn_id"])
    assert bridge.fake.steered == []

    interrupted = bridge.engine.store.get_turn(first)
    assert interrupted is not None
    assert (interrupted["status"], interrupted["error_kind"]) == ("interrupted", "superseded")
    assert interrupted["resume_state"] is None
    await bridge.tool("c1", "llm_turn_get", {"turn_id": waiting["turn_id"], "wait_seconds": 10})
    order = [t["text"] for t in bridge.engine.store.list_turns(session_id=session)]
    assert order == ["sleep:30", "先改这里", "原来排队的"], order

    # 存下来的是人发的原话；给模型的那份后面多一句“接着刚才的做”
    view = await _messages(bridge, session)
    asked = next(m for m in view if m["role"] == "user" and m["text"] == "先改这里")
    assert asked["delivery"] == "merged"
    native = bridge.engine.store.get_session(session)["native_id"]  # type: ignore[index]
    given = [m.text for m in bridge.fake.history[native] if m.role == "user"]
    assert "先改这里" + MERGED_SUFFIX in given
    reply = next(m for m in view if m["role"] == "assistant" and m["turn_id"] == first)
    assert reply["status"] == "interrupted" and reply["failure"]["kind"] == "superseded"
    # 这不是额度中断：不参与自动续跑，也不能“现在就试”
    assert (await bridge.admin("POST", f"/admin/turns/{first}/resume")).status_code == 409
    quota = (await bridge.admin("GET", "/admin/quota")).json()["platforms"]["fake"]
    assert quota["waiting_resume"] == 0 and quota["paused_until"] is None


async def test_insert_with_attachments_always_merges(bridge: Bridge) -> None:
    """带附件的插入不走回合中追加：附件要跟着一个回合走。"""
    bridge.fake.steerable = True
    session = await _session(bridge)
    await _running(bridge, session, "sleep:30", "t1")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    uploaded = await bridge.admin(
        "POST", f"/admin/sessions/{session}/attachments", params={"name": "a.png"}, content=png
    )
    merged = await _send(
        bridge, session, "看这张图", "t2", mode="insert", attachments=[uploaded.json()["id"]]
    )
    assert (merged["status"], merged["delivered"]) == ("completed", "merged")
    assert merged["result"].endswith("（收到 1 张图片）") and bridge.fake.steered == []
