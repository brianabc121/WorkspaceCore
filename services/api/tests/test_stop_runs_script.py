"""接入包的 bin/stop-runs.mjs（T-0126）：领取终止令、中止正在运行的会话、报回结果。

用 Node 直接导入脚本，换掉“调网关”和“发请求”两个函数。没有 node 时跳过。
真实容器里的 `sessions.list` / `sessions.abort` 由 agents 端到端验收跑（核验记录 6.2）。
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app/agent_bundle/files/bin/stop-runs.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="没有 node")

# 记下调用；send 按路径返回预设的结果
HARNESS = """
const calls = [], sent = [];
const makeCall = (list, fail) => async (method, params) => {
  calls.push([method, params]);
  if (fail && method === fail) throw new Error(`${method} failed: 1`);
  return method === "sessions.list" ? list : { ok: true };
};
const makeSend = (claimed) => async (path, body) => {
  sent.push([path, body]);
  return path === "/v1/stops/claim" ? claimed : { status: body.status };
};
"""


def _node(code: str) -> Any:
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [
            NODE,
            "--input-type=module",
            "-e",
            f"import * as m from {json.dumps(SCRIPT.as_uri())};\n{HARNESS}\n{code}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "MYWS_AGENT_API": "http://127.0.0.1:9",
            "MYWS_INSTANCE_TOKEN": "t",
        },
    )
    return json.loads(result.stdout)


def test_active_sessions_take_explicit_keys_only() -> None:
    rows = _node(
        "console.log(JSON.stringify(m.activeSessions({ sessions: ["
        '{ key: "openim:group:g1", hasActiveRun: true },'
        ' { key: "openim:ws_a", hasActiveRun: false, agentId: "main" },'
        ' { key: "openim:group:g1" }, { sessionKey: "x" }, { key: "" }, null,'
        ' { key: "global", agentId: "main" }, { key: "global", agentId: "ops" },'
        ' { key: "unknown" }] })))'
    )
    assert rows == [
        {"key": "openim:group:g1"},
        {"key": "openim:ws_a"},
        {"key": "global", "agentId": "main"},
        {"key": "global", "agentId": "ops"},
        {"key": "unknown"},
    ]
    assert _node('console.log(JSON.stringify(m.activeSessions([{ key: "a" }])))') == [{"key": "a"}]
    assert _node("console.log(JSON.stringify(m.activeSessions({})))") == []
    bad = _node(
        "try { m.activeSessions({ sessions: 1 }); console.log('null') }"
        " catch (e) { console.log(JSON.stringify(e.message)) }"
    )
    assert bad == "unexpected sessions.list result"


def test_abort_params_skip_clear_queued_for_sentinel_keys() -> None:
    params = _node(
        "console.log(JSON.stringify(["
        'm.abortParams({ key: "openim:ws_a" }), m.abortParams({ key: "global", agentId: "main" }),'
        ' m.abortParams({ key: "unknown" })]))'
    )
    assert params == [
        {"key": "openim:ws_a", "clearQueued": True},
        {"key": "global", "agentId": "main"},
        {"key": "unknown"},
    ]


def test_nothing_claimed_does_not_touch_the_gateway() -> None:
    out = _node(
        "await m.main({ call: makeCall([]), send: makeSend({ items: [] }) });"
        "console.log(JSON.stringify({ calls, sent }))"
    )
    assert out == {"calls": [], "sent": [["/v1/stops/claim", {}]]}


def test_claimed_stops_abort_every_active_session_and_report() -> None:
    out = _node(
        "const list = { sessions: [{ key: 'openim:group:g1' },"
        " { key: 'global', agentId: 'main' }] };"
        "await m.main({ call: makeCall(list),"
        " send: makeSend({ items: [{ id: 's1' }, { id: 's2' }] }) });"
        "console.log(JSON.stringify({ calls, sent }))"
    )
    assert out["calls"] == [
        ["sessions.list", {"activeOnly": True}],
        ["sessions.abort", {"key": "openim:group:g1", "clearQueued": True}],
        ["sessions.abort", {"key": "global", "agentId": "main"}],
    ]
    assert out["sent"][1:] == [
        ["/v1/stops/s1/result", {"status": "done", "aborted": 2}],
        ["/v1/stops/s2/result", {"status": "done", "aborted": 2}],
    ]


def test_gateway_failure_is_reported() -> None:
    out = _node(
        "const list = { sessions: [{ key: 'a' }] };"
        "await m.main({ call: makeCall(list, 'sessions.abort'),"
        " send: makeSend({ items: [{ id: 's1' }] }) });"
        "console.log(JSON.stringify(sent))"
    )
    assert out[1] == [
        "/v1/stops/s1/result",
        {"status": "failed", "aborted": 0, "error": "sessions.abort failed: 1"},
    ]
    long = _node('console.log(JSON.stringify(m.failure(new Error("x\\n".repeat(400)))))')
    assert len(long) == 300 and "\n" not in long
