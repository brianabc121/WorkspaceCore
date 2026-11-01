"""假模型服务（T-0059，方案第 8 节）：OpenAI 兼容的 /v1/chat/completions（流式与非流式）和 /v1/models。

按脚本回复，不需要真实模型密钥。规则看最后一条 assistant 之后的 user 消息：
- 含 `TOOL:<名字> <json>`：发出一次工具调用；工具名不在列表里而有 `tool_call` 时，经 Tool Search 的
  `tool_call` 间接调用（MCP 工具是这样暴露的）。收到工具结果后回复 `done: <结果前 300 字>`。
- 前面再加 `REPEAT:<n>`：同一个工具调用连续发 n 次（每次收到结果后再发），然后回复 `done`。
- 其他：回复 `echo: <原文末尾>`。
每个请求追加写入 FAKE_MODEL_LOG（JSON Lines，含 Authorization 请求头），验收脚本据此判断
是哪个实例（各自的模型密钥不同）、用的是哪个密钥。只用标准库。用法：python3 -I fake_model_server.py [端口]
"""

import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("FAKE_MODEL_LOG", "/tmp/fake_model.jsonl")
TOOL = re.compile(r"TOOL:([A-Za-z0-9_.\-]+)\s*(\{.*?\})?(?=\s*(?:TOOL:|$))", re.S)
REPEAT = re.compile(r"REPEAT:(\d+)")
_lock = threading.Lock()


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


CONTEXT = re.compile(r"<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>.*?<<<END_OPENCLAW_INTERNAL_CONTEXT>>>", re.S)


STAMP = re.compile(r"^\[[^\]]*UTC\]\s*")


def _clean(text: str) -> str:
    """去掉 OpenClaw 附加的内部上下文块、Runtime 行与时间戳前缀，只留下 IM 里真实的消息。"""
    lines = [STAMP.sub("", line) for line in CONTEXT.sub("", text).splitlines()]
    return "\n".join(line for line in lines if line.strip() and not line.startswith("Runtime:")).strip()


def _since_user(messages: list[dict]) -> tuple[str, int]:
    """最近一条有真实内容的用户消息与其后的工具结果数。

    OpenClaw 会在工具结果之后追加只含内部上下文的用户消息，清理后是空的，跳过它们。
    """
    tool_results = 0
    for m in reversed(messages):
        role = m.get("role")
        if role == "tool":
            tool_results += 1
        elif role == "user":
            text = _clean(_text(m.get("content")))
            if text:
                return text, tool_results
    return "", tool_results


def decide(body: dict) -> dict:
    messages = body.get("messages", [])
    text, done = _since_user(messages)
    match = TOOL.search(text)
    if not match:
        return {"content": "echo: " + text[:300]}
    repeat = int(m.group(1)) if (m := REPEAT.search(text)) else 1
    if done >= repeat:
        last = next((m for m in reversed(messages) if m.get("role") == "tool"), {})
        return {"content": "done: " + _text(last.get("content"))[:300]}
    name, args = match.group(1), match.group(2) or "{}"
    tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
    resolved = next((t for t in tools if t == name or (t or "").endswith("__" + name)), None)
    call_id = "call_" + uuid.uuid4().hex[:12]
    if resolved is None and "tool_call" in tools:
        return {"tool_call": {"id": call_id, "name": "tool_call",
                              "arguments": json.dumps({"id": name, "args": json.loads(args)})}}
    return {"tool_call": {"id": call_id, "name": resolved or name, "arguments": args}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _json(self, code: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        elif self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        result = decide(body)
        messages = body.get("messages", [])
        record = {"at": time.time(), "auth": self.headers.get("Authorization"),
                  "last_user": _since_user(messages)[0][-500:], "result": result,
                  "roles": [m.get("role") for m in messages[-6:]],
                  "last": _text(messages[-1].get("content"))[-800:] if messages else "",
                  "tools": [t.get("function", {}).get("name") for t in body.get("tools") or []]}
        with _lock, open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        created, cid, model = int(time.time()), "chatcmpl-" + uuid.uuid4().hex[:16], body.get("model", "fake-model")
        if "tool_call" in result:
            call = result["tool_call"]
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": call["id"], "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]}}]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": result["content"]}
            finish = "stop"
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if not body.get("stream"):
            self._json(200, {"id": cid, "object": "chat.completion", "created": created, "model": model,
                             "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(delta: dict, finish_reason: str | None = None, extra: dict | None = None) -> None:
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **(extra or {})}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()

        if "tool_calls" in message:
            call = message["tool_calls"][0]
            send({"role": "assistant", "tool_calls": [{"index": 0, "id": call["id"], "type": "function",
                                                       "function": {"name": call["function"]["name"], "arguments": ""}}]})
            send({"tool_calls": [{"index": 0, "function": {"arguments": call["function"]["arguments"]}}]})
        else:
            send({"role": "assistant", "content": message["content"]})
        send({}, finish, {"usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 18080), Handler).serve_forever()
