"""CI 验收用的模拟 n8n（T-0040）。

复用后端测试的内存版 n8n（services/api/tests/fake_n8n.py），行为与 pytest 里一致，依据见
docs/decisions/2026-10-31-n8n-trigger-and-execution-verification.md：Webhook + Header Auth、
traceparent 记在执行详情的 tracingContext 里、执行列表按 workflowId / startedAfter 筛选、
遮蔽后的错误摘要。与真实 n8n 的差异写在验收报告（docs/acceptance/sprint-5.md）里。

在 api 镜像里运行（它有 httpx），由 sprint5_acceptance.py 启动：
    /fake/tests  ← services/api/tests（只读挂载）
    /fake/server.py ← 本文件

除了 n8n 的接口，还有给验收脚本用的控制接口（只在这个模拟服务里存在）：
    POST /_fake/control  {"add_workflow": {"id": "...", "path": "...", "active": true},
                          "next_status": "success" | "error" | "running",
                          "trigger_failure": "timeout" | null,
                          "set_status": {"id": "1", "status": "error"},
                          "down": true | false}
    GET  /_fake/stats    {"triggers": n, "bodies": [...], "executions": {id: status}}
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

sys.path.insert(0, "/fake")
from tests.fake_n8n import FakeN8n

BASE = os.environ.get("FAKE_N8N_BASE", "http://fake-n8n:8080")
fake = FakeN8n(
    api_key=os.environ.get("FAKE_N8N_API_KEY", FakeN8n.api_key),
    secret=os.environ.get("FAKE_N8N_SECRET", FakeN8n.secret),
)
lock = threading.Lock()
# 触发请求的读超时：比工作台的 CONNECTOR_READ_TIMEOUT_SECONDS（默认 10 秒）长
HANG_SECONDS = float(os.environ.get("FAKE_N8N_HANG_SECONDS", "15"))


def _control(body: dict[str, Any]) -> None:
    if add := body.get("add_workflow"):
        fake.add_workflow(str(add["id"]), path=str(add["path"]), active=bool(add.get("active", True)))
    if "next_status" in body:
        fake.next_status = str(body["next_status"])
    if "trigger_failure" in body:
        fake.trigger_failure = body["trigger_failure"]
    if change := body.get("set_status"):
        execution = fake.executions[str(change["id"])]
        execution["status"] = str(change["status"])
        execution["stoppedAt"] = execution["startedAt"]
    if "down" in body:
        fake.down = bool(body["down"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # 不打印请求（API Key、共享密钥不进日志）
        return

    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method: str, body: bytes | None) -> None:
        request = httpx.Request(method, BASE + self.path, headers=dict(self.headers.items()), content=body)
        hang = False
        with lock:
            try:
                response = fake.handler(request)
            except httpx.ReadTimeout:
                hang = True  # 已受理、工作流已开始，但响应迟迟不回来（结果未知）
                response = httpx.Response(200, json={"message": "Workflow was started"})
            except httpx.ConnectError:
                response = httpx.Response(503, json={"message": "Service Unavailable"})
        if hang:
            time.sleep(HANG_SECONDS)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        self._send(response.status_code, response.content, headers)

    def do_GET(self) -> None:
        if self.path == "/_fake/stats":
            with lock:
                stats = {
                    "triggers": len(fake.received),
                    "bodies": [item["body"] for item in fake.received],
                    "executions": {k: v["status"] for k, v in fake.executions.items()},
                }
            self._send(200, json.dumps(stats).encode(), {"Content-Type": "application/json"})
            return
        self._proxy("GET", None)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path == "/_fake/control":
            with lock:
                _control(json.loads(raw or b"{}"))
            self._send(204, b"")
            return
        self._proxy("POST", raw)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104
