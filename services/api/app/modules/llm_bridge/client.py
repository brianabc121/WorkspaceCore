"""会话桥的客户端（services/llm-bridge，接口见它的 bridge/app.py）。

- 工具：向 `<base_url>/mcp` POST 一条 JSON-RPC `tools/call`（会话桥是无状态、JSON 响应的 MCP 服务，
  不需要先 initialize）。用管理令牌认证，请求头说明代表哪个实例、它有哪些权限范围。
- 管理接口：`<base_url>/admin/*`，同一枚令牌。
- 只访问登记这座桥时填的那个主机（owner 明确登记的内网服务）；有超时，不自动重试写操作。
  `llm_send` 带幂等号，结果未知时调用方用同一个 request_id 重试是安全的。
- 工作台部署到别的服务器上的管理器只监听那台服务器的本机地址：地址在发请求时才定下来
  （`resolve`，经专用的 SSH 通道，见 tunnel.py），连不上按“会话桥暂时连不上”处理。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.domain.connectors import ConnectorRuntime
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker, ConnectorHttp, OutboundPolicy, host_of

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
    # 给了就用它得到实际访问的地址（SSH 通道在本进程里的那一头），base_url 只是登记的地址
    resolve: Callable[[], Awaitable[str]] | None = None

    async def _base(self) -> str:
        if self.resolve is None:
            return self.base_url
        return (await self.resolve()).rstrip("/")

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
            "POST", f"{await self._base()}/mcp", headers=headers, json=body
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
            method, f"{await self._base()}/admin{path}", headers=self._headers(), json=body
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


def bridge_client(
    runtime: ConnectorRuntime,
    base_url: str,
    token: str,
    *,
    breaker: CircuitBreaker | None = None,
    resolve: Callable[[], Awaitable[str]] | None = None,
    scope: str | None = None,
) -> BridgeClient:
    """breaker：不给时用全局的熔断计数；一键部署等管理器启动时给它自己的（T-0233）。

    resolve / scope：经 SSH 通道访问的管理器。通道在本进程里的那一头都是 127.0.0.1，
    熔断按部署记录分开计数（scope），不然一座连不上会把别的也挡掉。
    """
    host = "127.0.0.1" if resolve is not None else host_of(base_url)
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({host})),
        connect_timeout=5.0,
        read_timeout=READ_TIMEOUT,
        read_retries=0,
        breaker=breaker or runtime.breaker,
        transport=runtime.transport,
        breaker_scope=scope or f"llm_bridge:{host}",
    )
    return BridgeClient(base_url.rstrip("/"), token, http, resolve)
