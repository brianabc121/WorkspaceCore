"""TrendRadar MCP 4.1.1 的客户端（T-0153；
核验记录 docs/decisions/2026-11-07-trendradar-verification.md 第 2、4 节）。

- Streamable HTTP（FastMCP 2.12.5，协议 2025-06-18）：向 http://trendradar-mcp:3333/mcp
  POST JSON-RPC；`initialize` 的响应头带 `mcp-session-id`，之后的请求都带上它；
  响应是 JSON 或 SSE（text/event-stream，取 id 对得上的那条 data）。
  每次调用开一个会话，用完 DELETE 掉。
- MCP 没有鉴权，只在内部网络 news 里；这里只调白名单里的工具：只读查询 + 状态，`send_notification`
  只能经 `notify()`（方案 B 与测试推送，由工作台自己调用，不开放给智能体）。
- 工具返回 JSON 字符串（content[0].text）：成功带 `success: true`；失败带 `error.code`，
  例如没有数据时 DATA_NOT_FOUND → NOT_FOUND，参数不对 → VALIDATION_FAILED，其余 → UNAVAILABLE。
- 不重试：读工具失败就显示“暂时不可用”；推送请求发出后连接中断归为 TIMEOUT_UNKNOWN
  （结果未知，不自动重发）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp

logger = logging.getLogger("app.integrations")

PROTOCOL_VERSION = "2025-06-18"
# 工作台会调用的只读工具（智能体能用的是其中一部分，见 app/domain/news.py）
READ_TOOLS = frozenset(
    {
        "get_latest_news",
        "search_news",
        "get_news_by_date",
        "get_trending_topics",
        "get_latest_rss",
        "search_rss",
        "find_related_news",
        "get_system_status",
    }
)
NOTIFY_TOOL = "send_notification"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_ACCEPT = "application/json, text/event-stream"
_NOT_FOUND_CODES = frozenset({"DATA_NOT_FOUND", "FILE_NOT_FOUND"})
_INVALID_CODES = frozenset({"INVALID_PARAMETER", "INVALID_DATE_RANGE", "EMPTY_MESSAGE"})


def _invalid(reason: str = "invalid_response") -> ConnectorError:
    return ConnectorError("UNAVAILABLE", "TrendRadar 返回了无法识别的响应", reason=reason)


def _short(text: object, limit: int = 200) -> str:
    return " ".join(str(text or "").split())[:limit]


def parse_message(response: httpx.Response, request_id: int) -> dict[str, Any]:
    """取出与 request_id 对应的 JSON-RPC 响应（JSON 或 SSE）。"""
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise _invalid("response_too_large")
    ctype = response.headers.get("content-type", "")
    messages: list[Any] = []
    try:
        if "text/event-stream" in ctype:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    messages.append(json.loads(line[5:].strip()))
        else:
            body = response.json()
            messages = body if isinstance(body, list) else [body]
    except ValueError as error:
        raise _invalid() from error
    for message in messages:
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    raise _invalid()


def tool_payload(message: dict[str, Any]) -> dict[str, Any]:
    """tools/call 的结果 → 工具返回的 JSON；失败按 error.code 归类。"""
    error = message.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code in (-32602, -32600):
            raise ConnectorError("VALIDATION_FAILED", "参数不正确", reason="invalid_params")
        if code == -32601:
            raise ConnectorError("NOT_SUPPORTED", "TrendRadar 不支持这个工具", reason="no_tool")
        raise ConnectorError("UNAVAILABLE", "TrendRadar 暂时不可用", reason="rpc_error")
    result = message.get("result")
    if not isinstance(result, dict):
        raise _invalid()
    content = result.get("content")
    text = None
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
    if not isinstance(text, str):
        structured = result.get("structuredContent")
        text = structured.get("result") if isinstance(structured, dict) else None
    if not isinstance(text, str):
        raise _invalid()
    if result.get("isError"):
        # FastMCP 的参数校验失败等：text 是一句英文说明
        raise ConnectorError("VALIDATION_FAILED", _short(text) or "参数不正确", reason="tool_error")
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise _invalid() from error
    if not isinstance(payload, dict):
        raise _invalid()
    if payload.get("success") is False:
        raw_err = payload.get("error")
        err: dict[str, Any] = raw_err if isinstance(raw_err, dict) else {}
        code = str(err.get("code") or "")
        message_text = _short(err.get("message")) or "TrendRadar 返回失败"
        if code in _NOT_FOUND_CODES:
            raise ConnectorError("NOT_FOUND", message_text, reason="data_not_found")
        if code in _INVALID_CODES:
            raise ConnectorError("VALIDATION_FAILED", message_text, reason=code.lower())
        raise ConnectorError("UNAVAILABLE", message_text, reason=code.lower() or "tool_failed")
    return payload


@dataclass
class TrendRadarClient:
    url: str
    http: ConnectorHttp

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用一个只读工具。"""
        if tool not in READ_TOOLS:
            raise ConnectorError("NOT_SUPPORTED", "这个工具不开放", reason="tool_not_allowed")
        try:
            return await self._call(tool, arguments)
        except ConnectorError as error:
            if error.kind == "TIMEOUT_UNKNOWN":  # 读工具没有副作用：结果未知就是暂时不可用
                raise ConnectorError(
                    "UNAVAILABLE", "TrendRadar 响应超时", reason="read_failed"
                ) from error
            raise

    async def notify(self, *, title: str, message: str, channels: list[str]) -> dict[str, Any]:
        """经 TrendRadar 推送到指定渠道（工作台自己调用）。返回每个渠道的结果。"""
        if not channels:
            raise ConnectorError("VALIDATION_FAILED", "没有可用的推送渠道", reason="no_channels")
        return await self._call(
            NOTIFY_TOOL, {"title": title, "message": message, "channels": channels}
        )

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        session = await self._initialize()
        try:
            response = await self._post(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                session,
            )
            if response.status_code != 200:
                raise ConnectorError(
                    "UNAVAILABLE",
                    "TrendRadar 暂时不可用",
                    status=response.status_code,
                    reason="unavailable",
                )
            return tool_payload(parse_message(response, 2))
        finally:
            await self._close(session)

    async def _initialize(self) -> str:
        response = await self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "my-workspace", "version": "1"},
                },
            },
            None,
        )
        if response.status_code != 200:
            raise ConnectorError(
                "UNAVAILABLE",
                "TrendRadar 暂时不可用",
                status=response.status_code,
                reason="unavailable",
            )
        message = parse_message(response, 1)
        session = str(response.headers.get("mcp-session-id") or "")
        if "result" not in message or not session:
            raise _invalid()
        initialized = await self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, session
        )
        if initialized.status_code >= 400:
            raise ConnectorError(
                "UNAVAILABLE",
                "TrendRadar 暂时不可用",
                status=initialized.status_code,
                reason="unavailable",
            )
        return session

    async def _post(self, body: dict[str, Any], session: str | None) -> httpx.Response:
        headers = {"Accept": _ACCEPT, "MCP-Protocol-Version": PROTOCOL_VERSION}
        if session:
            headers["Mcp-Session-Id"] = session
        return await self.http.request("POST", self.url, headers=headers, json=body)

    async def _close(self, session: str) -> None:
        try:
            await self.http.request(
                "DELETE",
                self.url,
                headers={"Mcp-Session-Id": session, "MCP-Protocol-Version": PROTOCOL_VERSION},
            )
        except ConnectorError:
            # 会话在服务端也会过期；关不掉不影响这次的结果
            logger.info("trendradar session close failed")
