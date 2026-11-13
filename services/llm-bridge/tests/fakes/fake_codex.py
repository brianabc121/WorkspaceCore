#!/usr/bin/env python3
"""仿 `codex app-server` 的最小实现（方法与事件的形状取自 codex-cli 0.169.0 的 schema）。"""

import json
import os
import sys
import uuid
from pathlib import Path

HOME = Path(os.environ["CODEX_HOME"])
STATE = HOME / "fake-state.json"


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"account": None, "threads": {}}


def save(state):
    HOME.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def out(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def note(method, params):
    out({"method": method, "params": params})


def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def run_turn(state, thread_id, params):
    text = params["input"][0]["text"]
    turn_id = str(uuid.uuid4())
    out({"id": params["_id"], "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
    note("turn/started", {"threadId": thread_id, "turn": {"id": turn_id}})
    status, error, reply = (
        "completed",
        None,
        f"[{params.get('model')}|{params['sandboxPolicy']['type']}] {text}",
    )
    if text == "limit":
        note(
            "error",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "willRetry": False,
                "error": {"message": "usage limit reached"},
            },
        )
        status, error = (
            "failed",
            {"message": "usage limit reached", "codexErrorInfo": "usageLimitExceeded"},
        )
    elif text == "approve":
        out(
            {
                "id": 9001,
                "method": "item/commandExecution/requestApproval",
                "params": {"threadId": thread_id, "turnId": turn_id, "command": "rm -rf /"},
            }
        )
        answer = read()
        reply = f"approval={answer['result']['decision']}"
    elif text == "hang":
        while True:
            message = read()
            if message is None:
                return
            if message.get("method") == "turn/interrupt":
                out({"id": message["id"], "result": {}})
                status = "interrupted"
                break
    if status == "completed":
        note(
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {"type": "commandExecution", "command": "ls", "exitCode": 0},
            },
        )
        note(
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {"type": "fileChange", "changes": [{"path": "a.txt"}]},
            },
        )
        note(
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {"type": "agentMessage", "text": reply},
            },
        )
        note(
            "thread/tokenUsage/updated",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "tokenUsage": {"last": {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7}},
            },
        )
        state["threads"][thread_id]["turns"].append({"user": text, "agent": reply})
        save(state)
    note(
        "turn/completed",
        {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "error": error}},
    )


def main():
    assert sys.argv[1] == "app-server"
    state = load()
    loaded = set()
    while (message := read()) is not None:
        method, params, mid = message.get("method"), message.get("params") or {}, message.get("id")
        if mid is None:
            continue
        if method == "initialize":
            out({"id": mid, "result": {"codexHome": str(HOME)}})
        elif method == "account/read":
            out({"id": mid, "result": {"account": state["account"], "requiresOpenaiAuth": True}})
        elif method == "account/login/start":
            if params["type"] == "apiKey":
                state["account"] = {"type": "apiKey"}
                save(state)
                out({"id": mid, "result": {"type": "apiKey"}})
            else:
                out(
                    {
                        "id": mid,
                        "result": {
                            "type": "chatgptDeviceCode",
                            "loginId": "l1",
                            "userCode": "ABCD-1234",
                            "verificationUrl": "https://example.test/device",
                        },
                    }
                )
                state["account"] = {
                    "type": "chatgpt",
                    "email": "user@example.com",
                    "planType": "plus",
                }
                save(state)
                note("account/login/completed", {"loginId": "l1", "success": True, "error": None})
        elif method == "account/logout":
            state["account"] = None
            save(state)
            out({"id": mid, "result": {}})
        elif method == "model/list":
            out(
                {
                    "id": mid,
                    "result": {
                        "data": [
                            {
                                "id": "gpt-a",
                                "displayName": "GPT A",
                                "description": "d",
                                "isDefault": True,
                                "hidden": False,
                                "supportedReasoningEfforts": [
                                    {"reasoningEffort": "low"},
                                    {"reasoningEffort": "high"},
                                ],
                            },
                            {"id": "gpt-hidden", "hidden": True},
                        ]
                    },
                }
            )
        elif method == "thread/start":
            thread_id = str(uuid.uuid4())
            state["threads"][thread_id] = {
                "cwd": params["cwd"],
                "model": params.get("model"),
                "sandbox": params["sandbox"],
                "turns": [],
                "archived": False,
                "instructions": params.get("developerInstructions"),
            }
            save(state)
            loaded.add(thread_id)
            out({"id": mid, "result": {"thread": {"id": thread_id}}})
        elif method == "thread/resume":
            if params["threadId"] not in state["threads"]:
                out({"id": mid, "error": {"code": -32602, "message": "no such thread"}})
            else:
                loaded.add(params["threadId"])
                out({"id": mid, "result": {"thread": {"id": params["threadId"]}}})
        elif method == "turn/start":
            if params["threadId"] not in loaded:
                out({"id": mid, "error": {"code": -32600, "message": "thread not loaded"}})
            else:
                run_turn(state, params["threadId"], {**params, "_id": mid})
        elif method == "turn/interrupt":
            out({"id": mid, "result": {}})
        elif method == "thread/list":
            data = [
                {
                    "id": tid,
                    "preview": (t["turns"][0]["user"] if t["turns"] else ""),
                    "name": None,
                    "model": t["model"],
                    "cwd": t["cwd"],
                    "createdAt": 1,
                    "updatedAt": 2,
                }
                for tid, t in state["threads"].items()
                if not t["archived"]
            ]
            out(
                {
                    "id": mid,
                    "result": {"data": data[: params.get("limit") or 50], "nextCursor": None},
                }
            )
        elif method == "thread/turns/list":
            thread = state["threads"][params["threadId"]]
            if not thread["turns"]:
                out(
                    {
                        "id": mid,
                        "error": {"code": -32603, "message": "list_turns is not supported yet"},
                    }
                )
                continue
            assert params["itemsView"] == "full" and params["sortDirection"] == "asc"
            turns = [
                {
                    "startedAt": 5,
                    "items": [
                        {"type": "userMessage", "content": [{"type": "text", "text": t["user"]}]},
                        {"type": "agentMessage", "text": t["agent"]},
                    ],
                }
                for t in thread["turns"]
            ]
            # 分两页返回，检查驱动会翻页
            start = int(params.get("cursor") or 0)
            page, more = turns[start : start + 1], start + 1 < len(turns)
            out(
                {
                    "id": mid,
                    "result": {"data": page, "nextCursor": str(start + 1) if more else None},
                }
            )
        elif method == "thread/archive":
            state["threads"][params["threadId"]]["archived"] = True
            save(state)
            out({"id": mid, "result": {}})
        else:
            out({"id": mid, "error": {"code": -32601, "message": "unknown method"}})


main()
