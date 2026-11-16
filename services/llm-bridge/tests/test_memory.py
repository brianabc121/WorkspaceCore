"""上下文与压缩、会话纪要、参考资料（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md）。

经 /mcp 与 /admin，用 fake 平台。
"""

from __future__ import annotations

from typing import Any

from tests.conftest import Bridge


async def _session(bridge: Bridge, caller: str, **extra: object) -> str:
    created = await bridge.tool(
        caller, "llm_session_create", {"platform": "fake", "mode": "chat", **extra}
    )
    assert "error" not in created, created
    return str(created["session_id"])


async def _send(
    bridge: Bridge, caller: str, session: str, text: str, rid: str, **extra: Any
) -> dict[str, Any]:
    return await bridge.tool(
        caller,
        "llm_send",
        {"session_id": session, "text": text, "request_id": rid, "wait_seconds": 10, **extra},
    )


# ---- 第 1 期：看得见（4.1）----
async def test_context_usage_and_compactions_are_visible(bridge: Bridge) -> None:
    """会话记下上下文用了多少、窗口多大、压缩过几次；智能体的工具结果里有 context；
    会话内容里压缩的位置有一条分隔，过程里有一步。"""
    agent = "openclaw-1"
    session = await _session(bridge, agent)
    created = (await bridge.tool(agent, "llm_session_get", {"session_id": session}))["session"]
    assert created["context"]["percent"] is None and created["context"]["compactions"] == 0

    first = await _send(bridge, agent, session, "你好", "r1")
    assert first["context"]["used"] == 1000 and first["context"]["window"] == 10000
    assert first["context"]["percent"] == 10 and not first["context"]["near_limit"]
    for i in range(2, 9):
        await _send(bridge, agent, session, f"第 {i} 条", f"r{i}")
    near = (await bridge.tool(agent, "llm_session_get", {"session_id": session}))["session"]
    assert near["context"]["percent"] == 80 and near["context"]["near_limit"]

    compacted = await _send(bridge, agent, session, "compact", "r9")
    context = compacted["context"]
    assert context["compactions"] == 1 and context["last_trigger"] == "auto"
    assert context["last_compacted_at"] and context["percent"] == 15  # 压缩后从 500 起算
    assert not context["near_limit"]

    # 智能体读会话：压缩的位置是一条 status=context_compacted，排在那一问前面
    detail = await bridge.tool(
        agent, "llm_session_get", {"session_id": session, "order": "newest", "limit": 3}
    )
    marker, asked, answer = detail["messages"]
    assert (marker["role"], marker["text"], marker["trigger"]) == (
        "status",
        "context_compacted",
        "auto",
    )
    assert asked["text"] == "compact" and answer["role"] == "assistant"

    # 聊天页：一条 role=context_compacted 的分隔在那一问前面；过程里有一步“上下文已压缩”
    view = (await bridge.admin("GET", f"/admin/sessions/{session}")).json()
    assert view["session"]["context"]["compactions"] == 1
    roles = [m["role"] for m in view["messages"]]
    divider = roles.index("context_compacted")
    assert view["messages"][divider + 1]["text"] == "compact"
    assert view["messages"][divider]["pre_tokens"] == 8000
    steps = view["messages"][divider + 2]["steps"]
    assert [s["kind"] for s in steps] == ["compaction"] and "自动" in steps[0]["text"]

    # 事件里也有，带触发方式；工作台的会话索引里有压缩次数
    events = (await bridge.admin("GET", f"/admin/sessions/{session}/events?after=0")).json()
    assert [e["trigger"] for e in events["events"] if e["kind"] == "compacted"] == ["auto"]
    index = (await bridge.admin("GET", "/admin/index?after=0")).json()
    (row,) = [s for s in index["sessions"] if s["session_id"] == session]
    assert row["compactions"] == 1 and row["context"]["percent"] == 15
