"""接入包的 bin/usage-report.mjs（T-0104）：解析 sessions.usage、补报昨天、分批、上报。

用 Node 直接运行脚本：假的 openclaw.mjs 打印一份 sessions.usage 结果（字段按 OpenClaw 2026.9.36 的
SessionsUsageResult），本机 HTTP 服务收上报。没有 node 时跳过。
"""

import hashlib
import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app/agent_bundle/files/bin/usage-report.mjs"
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
    )
    return json.loads(result.stdout)


def _hash(key: str, session_id: str = "") -> str:
    return hashlib.sha256(f"{key}\n{session_id}".encode()).hexdigest()


def test_build_sessions_takes_only_the_four_counts() -> None:
    result = {
        "sessions": [
            {
                "key": "agent:main:explicit:a",
                "sessionId": "a",
                "usage": {
                    "input": 2,
                    "output": 3,
                    "cacheRead": 4,
                    "cacheWrite": 5,
                    "totalTokens": 14,
                },
            },
            {"key": "agent:main:cron:b", "usage": None, "computing": True},
            {
                "key": "agent:main:explicit:c",
                "usage": {
                    "input": 1,
                    "output": 1,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "refreshing": True,
                },
            },
            {
                "key": "agent:main:explicit:d",
                "usage": {"input": 1.5, "output": 1, "cacheRead": 0, "cacheWrite": 0},
            },
            {"key": "agent:main:explicit:e", "usage": {"input": 1, "output": 1, "cacheRead": 0}},
            {"key": "", "usage": {"input": 1, "output": 1, "cacheRead": 0, "cacheWrite": 0}},
            "junk",
        ],
        "aggregates": {"sessionCount": 6},
    }
    out = _node(
        f"const r = {json.dumps(result)};"
        "console.log(JSON.stringify({s: m.buildSessions(r), t: m.truncated(r)}));"
    )
    sessions = {s["session"]: s["usage"] for s in out["s"]}
    assert sessions[_hash("agent:main:explicit:a", "a")] == {
        "input": 2,
        "output": 3,
        "cache_read": 4,
        "cache_write": 5,
    }
    for key in (
        "agent:main:cron:b",
        "agent:main:explicit:c",
        "agent:main:explicit:d",
        "agent:main:explicit:e",
    ):
        assert sessions[_hash(key)] is None, key
    assert len(sessions) == 5 and out["t"] is False
    assert _node(
        "console.log(JSON.stringify(m.truncated({sessions: [], aggregates: {sessionCount: 3}})))"
    )
    with pytest.raises(subprocess.CalledProcessError):
        _node("m.buildSessions({});")


def test_days_params_and_chunks() -> None:
    out = _node(
        "console.log(JSON.stringify({"
        "late: m.daysToReport(new Date('2026-11-04T00:09:59Z')),"
        "day: m.daysToReport(new Date('2026-11-04T00:10:00Z')),"
        "params: m.usageParams('2026-11-04'),"
        "chunks: m.chunks([1,2,3,4,5], 2).length, empty: m.chunks([], 2)}))"
    )
    assert out["late"] == ["2026-11-03", "2026-11-04"] and out["day"] == ["2026-11-04"]
    assert out["params"] == {
        "mode": "utc",
        "startDate": "2026-11-04",
        "endDate": "2026-11-04",
        "agentScope": "all",
        "limit": 1000,
    }
    assert out["chunks"] == 3 and out["empty"] == [[]]


class _Recorder(BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, str, Any]]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.received.append((self.path, self.headers["authorization"], body))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def workspace() -> Iterator[tuple[str, list[tuple[str, str, Any]]]]:
    _Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/agent-api", _Recorder.received
    finally:
        server.shutdown()


def test_main_calls_the_gateway_and_posts_each_day(
    tmp_path: Path, workspace: tuple[str, list[tuple[str, str, Any]]]
) -> None:
    base, received = workspace
    # 假的 openclaw.mjs：记下参数，按日期打印结果
    (tmp_path / "openclaw.mjs").write_text(
        "import { appendFileSync } from 'node:fs';\n"
        "const args = process.argv.slice(2);\n"
        "appendFileSync('calls.jsonl', JSON.stringify(args) + '\\n');\n"
        "const params = JSON.parse(args[args.indexOf('--params') + 1]);\n"
        "const n = params.startDate.endsWith('03') ? 1 : 2;\n"
        "const usage = {input: n, output: n, cacheRead: 0, cacheWrite: 0};\n"
        "console.log(JSON.stringify({sessions: [{key: 'agent:main:explicit:x', usage}], "
        "aggregates: {sessionCount: 1}}));\n"
    )
    assert NODE is not None
    env = {
        "PATH": "/usr/bin:/bin",
        "MYWS_AGENT_API": base,
        "MYWS_INSTANCE_TOKEN": "mwa_test",
        "MYWS_OPENCLAW_DIR": str(tmp_path),
    }
    code = (
        f"import {{ main }} from {json.dumps(SCRIPT.as_uri())};"
        "await main(new Date('2026-11-04T00:05:00Z'));"
    )
    subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [NODE, "--input-type=module", "-e", code], env=env, cwd=tmp_path, check=True, timeout=30
    )
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [c[:4] for c in calls] == [["gateway", "call", "sessions.usage", "--params"]] * 2
    assert all("--json" in c for c in calls)
    assert [(path, auth) for path, auth, _ in received] == [
        ("/agent-api/v1/usage", "Bearer mwa_test")
    ] * 2
    assert [body["day"] for _, _, body in received] == ["2026-11-03", "2026-11-04"]
    assert received[1][2]["sessions"] == [
        {
            "session": _hash("agent:main:explicit:x"),
            "usage": {"input": 2, "output": 2, "cache_read": 0, "cache_write": 0},
        }
    ]

    # 网关调用失败：退出码非 0，不上报
    (tmp_path / "openclaw.mjs").write_text("process.exit(3);\n")
    received.clear()
    failed = subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [NODE, str(SCRIPT)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert failed.returncode == 1 and "sessions.usage failed" in failed.stderr
    assert received == []
