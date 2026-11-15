#!/usr/bin/env python3
"""仿 `claude` / `codebuddy` 的无界面模式（参数与事件的形状取自 claude 2.1.323、codebuddy 2.184.0 的实测输出）。"""

import json
import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ["CODEBUDDY_CONFIG_DIR"])
KEY = (
    os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    or os.environ.get("CODEBUDDY_API_KEY")
)
args = sys.argv[1:]


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if args[:2] == ["auth", "status"]:
    print(
        json.dumps(
            {
                "loggedIn": bool(KEY) or (CONFIG / "logged-in").exists(),
                "authMethod": "api_key" if KEY else "claude.ai",
            }
        )
    )
    sys.exit(0)
if args[:2] == ["auth", "logout"]:
    (CONFIG / "logged-in").unlink(missing_ok=True)
    sys.exit(0)
if "--serve" in args:
    # 仿官方程序的本机服务模式（网页登录用）：三个账号接口，要带请求头和访问口令。
    # 记号文件：web-login-fails → 申请登录网址失败；web-login-done → 人已经在网页上登录完
    from http.server import BaseHTTPRequestHandler, HTTPServer

    PASSWORD = os.environ["CODEBUDDY_GATEWAY_PASSWORD"]
    assert len(PASSWORD) >= 32
    state = {"pending": False, "site": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            if self.headers.get("x-codebuddy-request") != "1":
                self.reply(400, {"error": "Missing required header: x-codebuddy-request"})
                return False
            if self.headers.get("Authorization") != f"Bearer {PASSWORD}":
                self.reply(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            if (CONFIG / "web-login-done").exists() and state["pending"]:
                (CONFIG / "logged-in").touch()
                state["pending"] = False
            if (CONFIG / "logged-in").exists():
                account = {"userId": "u-1", "userName": "测试账号"}
                self.reply(200, {"data": {"authenticated": True, "account": account}})
            else:
                extra = {"loginPending": True} if state["pending"] else {}
                self.reply(200, {"data": {"authenticated": False, "loginMethods": [], **extra}})

        def do_POST(self):
            if not self.allowed():
                return
            size = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(size) or b"{}")
            if self.path.endswith("/logout"):
                (CONFIG / "logged-in").unlink(missing_ok=True)
                (CONFIG / "web-login-done").unlink(missing_ok=True)
                self.reply(200, {"data": {"success": True}})
            elif (CONFIG / "web-login-fails").exists():
                self.reply(200, {"data": {"success": True, "loginError": "account.login.failed"}})
            else:
                state.update(pending=True, site=body.get("method"))
                url = f"https://example.test/login?site={body.get('method')}"
                self.reply(200, {"data": {"success": True, "authUrl": url}})

    CONFIG.mkdir(parents=True, exist_ok=True)
    HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), Handler).serve_forever()
if args == ["--help"]:
    print("  --model <model>  Model. Currently supported: (auto, fast-model, deep-model)")
    sys.exit(0)


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None


assert "-p" in args and value("--output-format") == "stream-json" and "--verbose" in args
text, images = sys.stdin.read(), 0
if value("--input-format") == "stream-json":
    # 带图片的提问：一条 stream-json 的用户消息，内容是 text 块和 base64 的 image 块
    blocks = json.loads(text)["message"]["content"]
    text = "".join(b["text"] for b in blocks if b["type"] == "text")
    images = sum(
        1
        for b in blocks
        if b["type"] == "image" and b["source"]["type"] == "base64" and b["source"]["data"]
    )
session = value("--session-id") or value("--resume")
transcript = CONFIG / "projects" / "-fake-cwd" / f"{session}.jsonl"
if value("--session-id") and transcript.exists():
    sys.stderr.write(f"Error: Session ID {session} is already in use.\n")
    sys.exit(1)
if value("--resume") and not transcript.exists():
    sys.stderr.write("No conversation found with session ID\n")
    sys.exit(1)
emit({"type": "system", "subtype": "init", "session_id": session, "cwd": os.getcwd()})
if not KEY and not (CONFIG / "logged-in").exists():
    emit(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "errors": [
                "Authentication required. Please use /login command to sign in to your account"
            ],
        }
    )
    sys.exit(0)
if text == "limit":
    # 额度用完：官方程序先报一条被拒绝的额度事件，再以错误结束
    emit(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "rateLimitType": "five_hour",
                "resetsAt": 1900000000,
                "utilization": 1.0,
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "You've hit your usage limit. Your limit resets at 1900000000.",
        }
    )
    sys.exit(0)
if text == "crash":
    sys.stderr.write("boom " + "sk-" + "ant-abcdefghijklmnopqrstuvwxyz0123\n")
    sys.exit(3)
if text == "hang":
    import time

    time.sleep(60)
transcript.parent.mkdir(parents=True, exist_ok=True)
reply = f"model={value('--model')} tools={value('--tools')!r} perm={value('--permission-mode')} leak={os.environ.get('BRIDGE_ADMIN_TOKEN')} extra={os.environ.get('EXTRA_VAR')} :: {text}"
if images:
    reply += f" images={images}"
if text == "read":
    # 读文件这类工具调用：先报调用，结果作为一条 user 消息报回来
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "README.md"},
                    }
                ]
            },
        }
    )
    emit(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "# 说明\n内容"}
                ],
            },
        }
    )
with transcript.open("a") as handle:
    handle.write(
        json.dumps(
            {
                "type": "user",
                "cwd": os.getcwd(),
                "timestamp": 1000,
                "message": {"role": "user", "content": text},
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    handle.write(json.dumps({"type": "file-history-snapshot"}) + "\n")
    handle.write(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": 2000,
                "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]},
            },
            ensure_ascii=False,
        )
        + "\n"
    )
emit(
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/a.py"}},
                {"type": "text", "text": reply},
            ]
        },
    }
)
emit(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": reply,
        "usage": {"input_tokens": 5, "output_tokens": 6},
    }
)
