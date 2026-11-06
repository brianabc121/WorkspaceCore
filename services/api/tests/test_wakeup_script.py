"""接入包的 bin/wakeup.mjs（T-0121）：领取唤醒、在容器里跑一轮、报回结果。

用 Node 直接导入脚本，换掉“跑一轮”和“发请求”两个函数。没有 node 时跳过。
真实容器里的 `openclaw agent --session-key` 由 agents 端到端验收跑（核验记录 V4）。
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app/agent_bundle/files/bin/wakeup.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="没有 node")


def _node(code: str) -> Any:
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [
            NODE,
            "--input-type=module",
            "-e",
            f"import * as m from {json.dumps(SCRIPT.as_uri())};\n{code}",
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


def test_agent_args_use_the_session_key_and_a_message_file() -> None:
    args = _node(
        "console.log(JSON.stringify(m.agentArgs("
        '{ id: "w1", session_key: "openim:group:g1" }, "work/m.md")))'
    )
    assert args == [
        "openclaw.mjs",
        "agent",
        "--session-key",
        "openim:group:g1",
        "--message-file",
        "work/m.md",
        "--timeout",
        "600",
        "--json",
    ]
    bad = _node(
        'try { m.agentArgs({ id: "w1", session_key: "x; rm -rf /" }, "f"); console.log("null") }'
        " catch (e) { console.log(JSON.stringify(e.message)) }"
    )
    assert bad == "bad wakeup"


def test_main_runs_each_claimed_wakeup_and_reports() -> None:
    out = _node(
        """
const sent = [], ran = [];
const send = async (path, body) => {
  sent.push([path, body]);
  if (path === "/v1/wakeups/claim") return { items: [
    { id: "w1", session_key: "openim:group:g1", message: "做事" },
    { id: "w2", session_key: "myworkspace-wakeup-x", message: "会失败" },
  ] };
  return {};
};
const run = async (item) => {
  ran.push(item.id);
  if (item.id === "w2") throw new Error("openclaw agent exited 1:\\n  boom");
};
await m.main({ run, send });
console.log(JSON.stringify({ sent, ran }));
"""
    )
    assert out["ran"] == ["w1", "w2"]
    assert out["sent"] == [
        ["/v1/wakeups/claim", {}],
        ["/v1/wakeups/w1/result", {"status": "done"}],
        ["/v1/wakeups/w2/result", {"status": "failed", "error": "openclaw agent exited 1: boom"}],
    ]
    long = _node('console.log(JSON.stringify(m.failure(new Error("x".repeat(500)))))')
    assert len(long) == 300
