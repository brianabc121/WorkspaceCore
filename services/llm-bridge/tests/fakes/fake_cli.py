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
if args == ["--help"]:
    print("  --model <model>  Model. Currently supported: (auto, fast-model, deep-model)")
    sys.exit(0)


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None


assert "-p" in args and value("--output-format") == "stream-json" and "--verbose" in args
text = sys.stdin.read()
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
if text == "crash":
    sys.stderr.write("boom " + "sk-" + "ant-abcdefghijklmnopqrstuvwxyz0123\n")
    sys.exit(3)
if text == "hang":
    import time

    time.sleep(60)
transcript.parent.mkdir(parents=True, exist_ok=True)
reply = f"model={value('--model')} tools={value('--tools')!r} perm={value('--permission-mode')} leak={os.environ.get('BRIDGE_ADMIN_TOKEN')} extra={os.environ.get('EXTRA_VAR')} :: {text}"
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
