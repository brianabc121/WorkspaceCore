"""假模型服务（T-0059，方案第 8 节）：OpenAI 兼容的 /v1/chat/completions（流式与非流式）和 /v1/models；
T-0202 起另有 Anthropic Messages 与 Gemini 原生协议（见下）。

按脚本回复，不需要真实模型密钥。规则看最后一条 assistant 之后的 user 消息：
- 含 `TOOL:<名字> <json>`：发出一次工具调用；工具名不在列表里而有 `tool_call` 时，经 Tool Search 的
  `tool_call` 间接调用（MCP 工具是这样暴露的）。收到工具结果后回复 `done: <结果前 300 字>`。
- 前面再加 `REPEAT:<n>`：同一个工具调用连续发 n 次（每次收到结果后再发），然后回复 `done`；
  参数里的 `{n}` 换成第几次（从 1 开始），让每次的内容不同（im_send 按内容去重，T-0120）。
- 其他：回复 `echo: <原文末尾>`。
每个请求追加写入 FAKE_MODEL_LOG（JSON Lines，含 Authorization 请求头），验收脚本据此判断
是哪个实例（各自的模型密钥不同）、用的是哪个密钥。只用标准库。用法：python3 -I fake_model_server.py [端口]

原生协议（T-0202，只回复 `echo: <原文末尾>`，不支持工具调用）：
- Anthropic Messages（https://platform.claude.com/docs/en/api/messages/create 、
  https://platform.claude.com/docs/en/build-with-claude/streaming ）：
  POST …/v1/messages（stream 为真时按官方事件顺序 message_start / content_block_start /
  content_block_delta / content_block_stop / message_delta / message_stop 发 SSE）；
  GET …/v1/models（带 x-api-key 或 anthropic-version 请求头时用 Anthropic 的列表格式）。
- Gemini（generativelanguage v1beta：ListModels、GenerateContent、StreamGenerateContent）：
  GET …/v1beta/models；POST …/v1beta/models/<id>:generateContent；
  POST …/v1beta/models/<id>:streamGenerateContent?alt=sse（每个 GenerateContentResponse 一行 data:）。
原生协议的请求也写进 FAKE_MODEL_LOG（列模型的 GET 也记），在原来的字段之外多记 protocol、kind、path、
model、stream、查询参数名（query，只记名字）与 alt 的值，以及 x-api-key / x-goog-api-key /
anthropic-version 请求头；OpenAI 兼容请求的记录格式不变。
"""

import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

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
    name, args = match.group(1), (match.group(2) or "{}").replace("{n}", str(done + 1))
    tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
    resolved = next((t for t in tools if t == name or (t or "").endswith("__" + name)), None)
    call_id = "call_" + uuid.uuid4().hex[:12]
    if resolved is None and "tool_call" in tools:
        return {"tool_call": {"id": call_id, "name": "tool_call",
                              "arguments": json.dumps({"id": name, "args": json.loads(args)})}}
    return {"tool_call": {"id": call_id, "name": resolved or name, "arguments": args}}


GEMINI_CALL = re.compile(r"/models/(.+):(generateContent|streamGenerateContent)$")


def _record(base: dict, **extra: object) -> None:
    with _lock, open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**base, **extra}, ensure_ascii=False) + "\n")


def _anthropic_messages(body: dict) -> list[dict]:
    """Anthropic 的 messages（content 是字符串或内容块数组）→ 通用的 role / content 列表。"""
    return [{"role": m.get("role"), "content": m.get("content")} for m in body.get("messages") or []
            if isinstance(m, dict)]


def _gemini_messages(body: dict) -> list[dict]:
    """Gemini 的 contents（role 为 user / model，parts[].text）→ 通用的 role / content 列表。"""
    out = []
    for c in body.get("contents") or []:
        if isinstance(c, dict):
            role = "assistant" if c.get("role") == "model" else "user"
            out.append({"role": role, "content": _text(c.get("parts"))})
    return out


def _native_record(handler: "Handler", protocol: str, kind: str, path: str, model: str,
                   messages: list[dict], result: dict | None, stream: bool = False) -> dict:
    return {"at": time.time(), "auth": handler.headers.get("Authorization"),
            "protocol": protocol, "kind": kind, "path": path, "model": model, "stream": stream,
            # 只记查询参数名与 alt 的值（不记参数值，免得有人把 Key 放进 ?key=）
            "query": sorted(parse_qs(urlsplit(handler.path).query)),
            "alt": parse_qs(urlsplit(handler.path).query).get("alt", [None])[0],
            "x_api_key": handler.headers.get("x-api-key"),
            "x_goog_api_key": handler.headers.get("x-goog-api-key"),
            "anthropic_version": handler.headers.get("anthropic-version"),
            "last_user": _since_user(messages)[0][-500:], "result": result,
            "roles": [m.get("role") for m in messages[-6:]], "count": len(messages),
            "last": _text(messages[-1].get("content"))[-800:] if messages else "",
            "tool": "", "tools": [], "notes": []}


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

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _event(self, data: dict, name: str | None = None) -> None:
        head = f"event: {name}\n" if name else ""
        self.wfile.write(f"{head}data: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        if path.endswith("/v1beta/models"):
            _record(_native_record(self, "gemini_native", "models", path, "", [], None))
            self._json(200, {"models": [{"name": "models/fake-model", "displayName": "Fake model",
                                         "supportedGenerationMethods": ["generateContent", "countTokens"]}]})
        elif path.endswith("/models") and (self.headers.get("x-api-key") or self.headers.get("anthropic-version")):
            _record(_native_record(self, "anthropic_native", "models", path, "", [], None))
            self._json(200, {"data": [{"type": "model", "id": "fake-model", "display_name": "Fake model",
                                       "created_at": "2026-01-01T00:00:00Z"}],
                             "has_more": False, "first_id": "fake-model", "last_id": "fake-model"})
        elif self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        elif self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def _anthropic(self, body: dict, path: str) -> None:
        messages = _anthropic_messages(body)
        result = {"content": "echo: " + _since_user(messages)[0][:300]}
        model = str(body.get("model", "fake-model"))
        _record(_native_record(self, "anthropic_native", "generate", path, model, messages, result,
                               bool(body.get("stream"))))
        mid = "msg_" + uuid.uuid4().hex[:24]
        usage = {"input_tokens": 1, "output_tokens": 1}
        if not body.get("stream"):
            self._json(200, {"id": mid, "type": "message", "role": "assistant", "model": model,
                             "content": [{"type": "text", "text": result["content"]}],
                             "stop_reason": "end_turn", "stop_sequence": None, "usage": usage})
            return
        self._sse()
        self._event({"type": "message_start", "message": {
            "id": mid, "type": "message", "role": "assistant", "content": [], "model": model,
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
            "message_start")
        self._event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    "content_block_start")
        self._event({"type": "ping"}, "ping")
        self._event({"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": result["content"]}}, "content_block_delta")
        self._event({"type": "content_block_stop", "index": 0}, "content_block_stop")
        self._event({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                     "usage": {"output_tokens": 1}}, "message_delta")
        self._event({"type": "message_stop"}, "message_stop")

    def _gemini(self, body: dict, path: str, model: str, stream: bool) -> None:
        messages = _gemini_messages(body)
        result = {"content": "echo: " + _since_user(messages)[0][:300]}
        _record(_native_record(self, "gemini_native", "generate", path, model, messages, result, stream))
        reply = {"candidates": [{"content": {"role": "model", "parts": [{"text": result["content"]}]},
                                 "finishReason": "STOP", "index": 0}],
                 "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
                 "modelVersion": model.removeprefix("models/")}
        if not stream:
            self._json(200, reply)
            return
        self._sse()
        self._event(reply)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = urlsplit(self.path).path.rstrip("/")
        if path.endswith("/messages"):
            self._anthropic(body, path)
            return
        if call := GEMINI_CALL.search(path):
            self._gemini(body, path, unquote(call.group(1)), call.group(2) == "streamGenerateContent")
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        result = decide(body)
        messages = body.get("messages", [])
        record = {"at": time.time(), "auth": self.headers.get("Authorization"), "model": body.get("model"),
                  "last_user": _since_user(messages)[0][-500:], "result": result,
                  "roles": [m.get("role") for m in messages[-6:]],
                  "count": len(messages),
                  "last": _text(messages[-1].get("content"))[-800:] if messages else "",
                  # 最近一条工具结果的末尾（OpenClaw 会在工具结果后面再补一条只含内部上下文的用户消息，
                  # last 看不到它；工作台工具的错误码在结果的末尾，T-0108）
                  "tool": next((_text(m.get("content"))[-1500:] for m in reversed(messages)
                                if m.get("role") == "tool"), ""),
                  "tools": [t.get("function", {}).get("name") for t in body.get("tools") or []],
                  # 工作台放回会话的系统事件（openclaw-channel 补丁，T-0120）：任何一条消息里带“[工作台]”的行
                  "notes": [line.strip()[-300:] for m in messages for line in _text(m.get("content")).splitlines()
                            if "[工作台]" in line][-5:]}
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
