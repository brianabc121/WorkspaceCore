"""CI 验收用的模拟 Google OAuth + Gmail API（T-0131）。

复用后端测试的内存版 Google（services/api/tests/fake_google.py），请求与响应形状与 pytest 里完全一致，
依据见 docs/decisions/2026-11-06-gmail-verification.md。真实 Google 账号上的验收是人工的（核验记录第 6 节）。

在 api 镜像里运行（它有 httpx），由 agents_acceptance.py 的场景 23 启动：
    /fake/tests     ← services/api/tests（只读挂载）
    /fake/server.py ← 本文件
在 integrations 网络里的别名是 fake-gmail；api 的 GOOGLE_OAUTH_TOKEN_URL / GOOGLE_OAUTH_REVOKE_URL / GMAIL_API_URL
指向它（`/token`、`/revoke` 按 oauth2.googleapis.com 处理，`/gmail/v1/...` 按 gmail.googleapis.com 处理）。

给验收脚本用的控制接口（只在这个模拟服务里存在）：
    POST /_fake/consent  {"authorize_url": "...", "email": "...", "scopes": [...]?}  → 回调参数 {code, state}
    POST /_fake/message  {"email": "...", "sender": "...", "subject": "...", "text": "..."} → {id, threadId}
    GET  /_fake/sent?email=...  → 收到的发信 [{to, cc, subject, body, in_reply_to, thread_id}]
    GET  /_fake/stats    → {"requests": n, "revoked": n}
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

# 容器里挂载在 /fake；本机调试时用 FAKE_ROOT 指向 services/api
sys.path.insert(0, os.environ.get("FAKE_ROOT", "/fake"))
from tests.fake_google import FakeGoogle

fake = FakeGoogle()
lock = threading.Lock()


def _upstream(path: str, query: str) -> str:
    host = "gmail.googleapis.com" if path.startswith("/gmail/") else "oauth2.googleapis.com"
    return f"https://{host}{path}" + (f"?{query}" if query else "")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode())

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _control(self, method: str, path: str, query: str) -> bool:
        if not path.startswith("/_fake/"):
            return False
        params = {k: v[0] for k, v in parse_qs(query).items()}
        with lock:
            if method == "POST" and path == "/_fake/consent":
                data = json.loads(self._body() or b"{}")
                self._json(200, fake.consent(data["authorize_url"], email=data["email"], scopes=data.get("scopes")))
            elif method == "POST" and path == "/_fake/message":
                data = json.loads(self._body() or b"{}")
                email = data.pop("email")
                message = fake.add_message(email, **data)
                self._json(200, {"id": message["id"], "threadId": message["threadId"]})
            elif method == "GET" and path == "/_fake/sent":
                box = fake.mailboxes.get(params.get("email", ""))
                sent = [
                    {
                        "to": str(m["To"] or ""),
                        "cc": str(m["Cc"] or ""),
                        "subject": str(m["Subject"] or ""),
                        "body": m.get_content(),
                        "in_reply_to": str(m["In-Reply-To"] or ""),
                    }
                    for m in (box.sent if box else [])
                ]
                self._json(200, sent)
            elif method == "GET" and path == "/_fake/stats":
                self._json(200, {"requests": len(fake.requests), "revoked": len(fake.revoked)})
            else:
                self._json(404, {"error": "unknown control"})
        return True

    def _proxy(self, method: str) -> None:
        parts = urlsplit(self.path)
        if self._control(method, parts.path, parts.query):
            return
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length"}}
        request = httpx.Request(method, _upstream(parts.path, parts.query), headers=headers, content=self._body())
        try:
            with lock:
                response = asyncio.run(fake.transport.handle_async_request(request))
                content = asyncio.run(response.aread())
        except httpx.TimeoutException:
            # 模拟“请求已经发出、响应没回来”：不回任何内容就断开
            self.close_connection = True
            return
        self._send(response.status_code, content, response.headers.get("content-type", "application/json"))

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
