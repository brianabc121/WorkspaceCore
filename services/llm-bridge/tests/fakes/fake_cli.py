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
    status = {
        "loggedIn": bool(KEY) or (CONFIG / "logged-in").exists(),
        "authMethod": "api_key" if KEY else "claude.ai",
    }
    # 用 /login（或网页授权登录，T-0287）存下的登录：2.1.323 报告邮箱
    if not KEY and (CONFIG / "email").exists():
        status["email"] = (CONFIG / "email").read_text().strip()
    print(json.dumps(status))
    sys.exit(0)
if args[:3] == ["auth", "login", "--claudeai"]:
    # 2.1.323 没有终端时的样子（T-0287 核实）：给出网址，从标准输入一行一行读授权码
    import time

    if (CONFIG / "exit-early").exists():
        sys.stderr.write("Login failed: Couldn't start the local listener\n")
        sys.exit(1)
    if (CONFIG / "no-url").exists():
        time.sleep(30)
        sys.exit(1)
    sys.stdout.write("Opening browser to sign in\u2026\n")
    sys.stdout.write(
        "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true"
        "&client_id=fake&state=s1\n"
    )
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    for raw in sys.stdin:
        code, _, state = raw.strip().partition("#")
        if not code or not state:
            sys.stderr.write("Invalid code. Please make sure the full code was copied.\n")
            sys.stderr.flush()
            continue
        if code != "good":
            sys.stderr.write(
                "Login failed: That code wasn't accepted. It may be incomplete, expired, already "
                "used, or from an earlier sign-in attempt. Sign in again to get a new code.\n"
            )
            sys.exit(1)
        CONFIG.mkdir(parents=True, exist_ok=True)
        (CONFIG / "logged-in").write_text("1")
        (CONFIG / "email").write_text("sub@example.com")
        sys.stdout.write("\nLogin successful.\n")
        sys.exit(0)
    sys.exit(130)
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
    port = int(args[args.index("--port") + 1])
    server = HTTPServer(("127.0.0.1", port), Handler)
    # 和官方程序一样，启动信息里带着访问口令（管理器记运行信息时要替换掉）
    print(f"Gateway listening on http://127.0.0.1:{port} password={PASSWORD}", flush=True)
    print(f"access token {PASSWORD}", flush=True)
    server.serve_forever()
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
reply = f"model={value('--model')} effort={value('--effort')} tools={value('--tools')!r} perm={value('--permission-mode')} leak={os.environ.get('BRIDGE_ADMIN_TOKEN')} extra={os.environ.get('EXTRA_VAR')} ac={os.environ.get('CLAUDE_AUTOCOMPACT_PCT_OVERRIDE') or os.environ.get('CODEBUDDY_AUTOCOMPACT_PCT_OVERRIDE')} :: {text}"
if images:
    reply += f" images={images}"
if text == "history":
    # 这个官方会话里之前有几条提问：看得出续接的是哪个会话、清空上下文后是不是从头开始（T-0255）
    lines = transcript.read_text().splitlines() if transcript.exists() else []
    reply += f" history={sum(1 for line in lines if json.loads(line).get('type') == 'user')}"
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
if text == "background":
    # 后台任务（2.1.323 / 2.184.0 的 system 事件）和一个带编号的 Bash 调用：
    # 一个任务做完了，另一个到回答结束时还在跑
    for event in (
        {
            "subtype": "task_started",
            "task_id": "t1",
            "description": "跑测试",
            "task_type": "local_bash",
        },
        {
            "subtype": "task_progress",
            "task_id": "t1",
            "description": "跑测试",
            "last_tool_name": "Bash",
        },
        {"subtype": "task_started", "task_id": "t2", "description": "等 CI"},
        {"subtype": "task_updated", "task_id": "t1", "patch": {"status": "running"}},
        {
            "subtype": "task_notification",
            "task_id": "t1",
            "status": "completed",
            "summary": "全部通过",
        },
    ):
        emit({"type": "system", **event})
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_b",
                        "name": "Bash",
                        "input": {"command": "make test"},
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
                "content": [{"type": "tool_result", "tool_use_id": "toolu_b", "content": "ok"}],
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
if "--include-partial-messages" in args:
    # 逐字的增量：回答分两段给；思考的增量和子代理的增量不算回答
    half = len(reply) // 2

    def partial(delta, parent=None):
        emit(
            {
                "type": "stream_event",
                "parent_tool_use_id": parent,
                "event": {"type": "content_block_delta", "index": 0, "delta": delta},
            }
        )

    partial({"type": "thinking_delta", "thinking": "想一想"})
    partial({"type": "text_delta", "text": "子代理说的"}, parent="toolu_1")
    partial({"type": "text_delta", "text": reply[:half]})
    partial({"type": "text_delta", "text": reply[half:]})
CODEBUDDY = "CODEBUDDY_CONFIG_DIR" in os.environ
turns = sum(1 for line in transcript.read_text().splitlines() if '"type": "user"' in line)
if text in ("compact-auto", "/compact") or text.startswith("/compact "):
    # 压缩上下文：Claude Code 报一条 compact_boundary（Agent SDK 0.3.323 的形状）；
    # CodeBuddy Code 只报“正在压缩”的状态事件，结束时状态变回 null（2.184.0 的 StreamJsonView）
    trigger = "auto" if text == "compact-auto" else "manual"
    if value("--settings"):
        # 带了钩子（--settings 的 JSON）：像官方程序那样，压缩前跑 PreCompact（输出并进压缩说明），
        # 压缩后跑 PostCompact（标准输入给 compact_summary）
        import shlex
        import subprocess

        hooks = json.loads(value("--settings"))["hooks"]

        def run_hook(event, payload):
            command = hooks[event][0]["hooks"][0]["command"]
            done = subprocess.run(
                shlex.split(command),
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return done.stdout

        base = {"session_id": session, "trigger": trigger}
        kept = run_hook("PreCompact", {**base, "hook_event_name": "PreCompact"})
        summary = f"结论和决定：先备份再升级\n没解决的问题：回滚脚本还没写\n（压缩说明：{kept}）"
        run_hook(
            "PostCompact", {**base, "hook_event_name": "PostCompact", "compact_summary": summary}
        )
    if CODEBUDDY:
        emit({"type": "system", "subtype": "status", "status": "compacting", "session_id": session})
        emit({"type": "system", "subtype": "status", "status": None, "session_id": session})
    else:
        emit(
            {
                "type": "system",
                "subtype": "compact_boundary",
                "session_id": session,
                "compact_metadata": {"trigger": trigger, "pre_tokens": 150000, "post_tokens": 9000},
            }
        )
# 上下文：每一回合多用 1000 个 token（主模型的这次调用）；子代理的调用不算
used = 1000 * turns
emit(
    {
        "type": "assistant",
        "parent_tool_use_id": "toolu_sub",
        "message": {
            "model": "claude-haiku-sub",
            "content": [{"type": "text", "text": ""}],
            "usage": {"input_tokens": 99999, "output_tokens": 1},
        },
    }
)
emit(
    {
        "type": "assistant",
        "message": {
            "model": "claude-main",
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/a.py"}},
                {"type": "text", "text": reply},
            ],
            "usage": {
                "input_tokens": used - 400,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 100,
                "output_tokens": 100,
            },
        },
    }
)
result = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": reply,
    "usage": {"input_tokens": 5, "output_tokens": 6},
}
if CODEBUDDY:  # 上下文在 _meta 里（官方文档 stream-json-turn-usage 第 9 节）
    result["_meta"] = {"codebuddy.ai/contextUsed": used + 7, "codebuddy.ai/contextWindow": 168000}
else:  # 窗口在 modelUsage 里，按模型分（Agent SDK 0.3.323 的 ModelUsage）
    result["modelUsage"] = {
        "claude-haiku-sub": {"contextWindow": 100000},
        "claude-main": {"contextWindow": 200000},
    }
emit(result)
