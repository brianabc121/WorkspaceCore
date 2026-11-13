"""会话桥的客户端（services/llm-bridge，接口见它的 bridge/app.py）。

- 工具：向 `<base_url>/mcp` POST 一条 JSON-RPC `tools/call`（会话桥是无状态、JSON 响应的 MCP 服务，
  不需要先 initialize）。用管理令牌认证，请求头说明代表哪个实例、它有哪些权限范围。
- 管理接口：`<base_url>/admin/*`，同一枚令牌。
- 只访问登记这座桥时填的那个主机（owner 明确登记的内网服务）；有超时，不自动重试写操作。
  `llm_send` 带幂等号，结果未知时调用方用同一个 request_id 重试是安全的。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.domain.connectors import ConnectorRuntime
from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy, host_of

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# llm_send / llm_turn_get 最多在会话桥上等 50 秒
READ_TIMEOUT = 65.0
_ACCEPT = "application/json, text/event-stream"
PROTOCOL_VERSION = "2025-06-18"


class BridgeCallError(Exception):
    """会话桥明确返回的错误（工具错误或管理接口的错误）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid() -> ConnectorError:
    return ConnectorError("UNAVAILABLE", "会话桥返回了无法识别的响应", reason="invalid_response")


def _json(response: httpx.Response) -> Any:
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise _invalid()
    try:
        if "text/event-stream" in response.headers.get("content-type", ""):
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise _invalid()
        return response.json()
    except ValueError as error:
        raise _invalid() from error


def _error_of(body: Any) -> BridgeCallError | None:
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return BridgeCallError(error["code"], str(error.get("message") or "")[:500])
    return None


@dataclass(frozen=True)
class BridgeCaller:
    """代表哪个实例调用，以及它的权限范围（write / account / takeover）。"""

    instance_id: str
    label: str
    scopes: tuple[str, ...]


@dataclass
class BridgeClient:
    base_url: str
    token: str
    http: ConnectorHttp

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _check_status(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise ConnectorError(
                "AUTH_FAILED", "会话桥不接受这枚令牌", status=401, reason="bad_token"
            )
        if response.status_code >= 500:
            raise ConnectorError(
                "UNAVAILABLE", "会话桥暂时不可用", status=response.status_code, reason="unavailable"
            )

    async def tool(self, caller: BridgeCaller, name: str, arguments: dict[str, Any]) -> Any:
        headers = {
            **self._headers(),
            "Accept": _ACCEPT,
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "X-Bridge-Caller": caller.instance_id,
            "X-Bridge-Caller-Label": quote(caller.label),
            "X-Bridge-Scopes": ",".join(caller.scopes),
        }
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = await self.http.request(
            "POST", f"{self.base_url}/mcp", headers=headers, json=body
        )
        self._check_status(response)
        message = _json(response)
        if response.status_code != 200 or not isinstance(message, dict):
            raise _invalid()
        if "error" in message:  # JSON-RPC 层的错误：不是会话桥的工具
            raise _invalid()
        result = message.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        text = content[0].get("text") if isinstance(content, list) and content else None
        if not isinstance(text, str):
            raise _invalid()
        try:
            payload = json.loads(text)
        except ValueError as error:
            raise _invalid() from error
        if isinstance(result, dict) and result.get("isError"):
            raise _error_of(payload) or _invalid()
        return payload

    async def admin(self, method: str, path: str, body: Any = None) -> Any:
        response = await self.http.request(
            method, f"{self.base_url}/admin{path}", headers=self._headers(), json=body
        )
        self._check_status(response)
        payload = _json(response)
        if response.status_code >= 400:
            error = _error_of(payload)
            if error is not None:
                raise error
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if response.status_code == 422 and detail is not None:
                raise BridgeCallError("VALIDATION_FAILED", "参数不正确")
            raise _invalid()
        return payload


def bridge_client(runtime: ConnectorRuntime, base_url: str, token: str) -> BridgeClient:
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({host_of(base_url)})),
        connect_timeout=5.0,
        read_timeout=READ_TIMEOUT,
        read_retries=0,
        breaker=runtime.breaker,
        transport=runtime.transport,
        breaker_scope=f"llm_bridge:{host_of(base_url)}",
    )
    return BridgeClient(base_url.rstrip("/"), token, http)
