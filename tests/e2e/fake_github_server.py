"""CI 验收用的模拟 GitHub REST API（T-0032）。

复用后端测试的内存版 GitHub（services/api/tests/fake_github.py），行为与 pytest 里完全一致：
版本头、Token、Link 分页、ETag / 304、主 / 次级限流、Issue 列表里的 PR 等，依据见
docs/decisions/2026-10-30-github-rest-and-affine-verification.md。
与真实 GitHub 的差异写在验收报告（docs/acceptance/sprint-4.md）里。

在 api 镜像里运行（它有 httpx），由 sprint4_acceptance.py 启动：
    /fake/tests  ← services/api/tests（只读挂载）
    /fake/server.py ← 本文件

除了 GitHub 的 GET 接口，还有给验收脚本用的控制接口（只在这个模拟服务里存在）：
    POST /_fake/control  {"rate_limit": "primary" | "secondary" | null, "reset_in": 秒,
                          "token": "...", "add_issue": {"repo": "...", "title": "..."},
                          "delete_issue": {"repo": "...", "title": "..."}}
    GET  /_fake/stats    {"requests": n, "github": n, "other": [路径...]}
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
from tests.fake_github import FakeGitHub

BASE = os.environ.get("FAKE_GITHUB_BASE", "http://fake-github:8080")
REPO = os.environ.get("FAKE_GITHUB_REPO", "e2e/workspace")

fake = FakeGitHub(BASE)
fake.token = os.environ.get("FAKE_GITHUB_TOKEN", fake.token)
fake.add_repo(REPO, description="验收用仓库")
fake.add_issue(REPO, "验收：登录失败")
fake.add_pull(REPO, "验收：修复登录")
fake.add_run(REPO)
fake.add_run(REPO, failed=True)
lock = threading.Lock()
other_paths: list[str] = []


def _control(body: dict[str, Any]) -> None:
    if "rate_limit" in body:
        fake.rate_limit = body["rate_limit"]
    if "reset_in" in body:
        # 主限流的 x-ratelimit-reset：验收里设得很短，免得后面的步骤等一小时
        fake.reset = int(time.time()) + int(body["reset_in"])
    if "token" in body:
        fake.token = str(body["token"])
    if add := body.get("add_issue"):
        fake.add_issue(str(add["repo"]), str(add["title"]))
    if gone := body.get("delete_issue"):
        key = str(gone["repo"]).lower()
        fake.issues[key] = [i for i in fake.issues[key] if i["title"] != gone["title"]]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # 不打印请求（Authorization 头不进日志）
        return

    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        with lock:
            if self.path == "/_fake/stats":
                stats = {
                    "requests": len(fake.requests) + len(other_paths),
                    "github": len(fake.requests),
                    "other": list(other_paths),
                }
                self._send(200, json.dumps(stats).encode(), {"Content-Type": "application/json"})
                return
            if not self.path.startswith("/repos/"):
                # 例如对 AFFiNE 地址的请求：工作台不应该发出任何这类请求（Sprint 4 DoD）
                other_paths.append(self.path.split("?")[0])
                self._send(404, b'{"message": "Not Found"}', {"Content-Type": "application/json"})
                return
            request = httpx.Request("GET", BASE + self.path, headers=dict(self.headers.items()))
            response = fake.handle(request)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        self._send(response.status_code, response.content, headers)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        with lock:
            if self.path != "/_fake/control":
                other_paths.append(self.path)
                self._send(405, b"{}")
                return
            _control(body)
        self._send(204, b"")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104
