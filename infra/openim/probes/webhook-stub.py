"""OpenIM 回调的放行桩：只用于越权回归测试（compose.probe.yaml）。

OpenIM 的“发送前”回调请求失败时，消息直接发不出去。测试环境里没有工作台 API，
所以用这个桩一律放行，好让“正常发送”的对照组能成功、“冒充发送”的失败只来自补丁。
只用标准库；不记录载荷（里面有消息内容）。
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOW = json.dumps(
    {"actionCode": 0, "errCode": 0, "errMsg": "", "errDlt": "", "nextCode": 0}
).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # 不打印请求
        pass

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(ALLOW)))
        self.end_headers()
        self.wfile.write(ALLOW)


ThreadingHTTPServer(("0.0.0.0", 10006), Handler).serve_forever()
