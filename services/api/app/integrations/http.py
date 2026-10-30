"""访问第三方服务的 HTTP 客户端（docs/DEVELOPMENT_SPEC.md 第 6.4 节、AGENTS.md 第 8 节第 7 条）。

- **SSRF 防护**（Sprint 3 契约第 4.3 节）：每次请求前解析目标主机，任何一个地址不是公网地址
  就拒绝，除非主机名在部署配置 INTEGRATION_ALLOWED_HOSTS 里；不跟随重定向。
- **超时与错误归类**：连接超时 / 连接失败说明请求没有发出（`not_sent`）；读超时等发生在
  请求发出之后，读操作归为 UNAVAILABLE，写操作归为 TIMEOUT_UNKNOWN（结果未知，不能盲目重试）。
- **重试**：只对读请求（GET）做有限次数的指数退避重试；429 不重试，交给调用方按 Retry-After 处理。
- **熔断**：同一个服务连续失败达到阈值后，在冷却期内直接失败，不再发请求。

请求带上当前的 X-Request-ID，便于对照两边的日志；Authorization 等请求头不写日志。
"""

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.logging import request_id_var
from app.integrations.base import ConnectorError

logger = logging.getLogger("app.integrations")

_READ_METHODS = frozenset({"GET", "HEAD"})
_RETRY_STATUSES = frozenset({502, 503, 504})


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_public(address: str) -> bool:
    return ipaddress.ip_address(address).is_global


@dataclass(frozen=True)
class OutboundPolicy:
    """决定一个目标地址能不能访问。

    allowed_hosts 里的主机名跳过地址检查（部署时显式允许的内网服务）。
    """

    allowed_hosts: frozenset[str] = frozenset()

    async def check(self, url: str) -> None:
        host = host_of(url)
        if not host:
            raise _not_allowed()
        if host in self.allowed_hosts:
            return
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            addresses = await _resolve(host, urlsplit(url).port or 443)
        if not addresses or not all(_is_public(a) for a in addresses):
            raise _not_allowed()


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ConnectorError(
            "UNAVAILABLE", "无法解析服务地址", not_sent=True, reason="dns_failed"
        ) from error
    return sorted({str(info[4][0]) for info in infos})


def _not_allowed() -> ConnectorError:
    return ConnectorError(
        "UNAVAILABLE", "不允许访问该地址", not_sent=True, reason="destination_not_allowed"
    )


@dataclass
class CircuitBreaker:
    """连续失败 threshold 次后打开 cooldown 秒；冷却期满后放行一次试探请求。"""

    threshold: int = 5
    cooldown_seconds: float = 30.0
    _failures: dict[str, int] = field(default_factory=dict)
    _opened_at: dict[str, float] = field(default_factory=dict)

    def before(self, key: str) -> None:
        opened = self._opened_at.get(key)
        if opened is None:
            return
        if time.monotonic() - opened < self.cooldown_seconds:
            raise ConnectorError(
                "UNAVAILABLE", "服务暂时不可用（熔断中）", not_sent=True, reason="circuit_open"
            )
        del self._opened_at[key]  # 半开：放行这一次

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)

    def failure(self, key: str) -> None:
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self.threshold:
            self._opened_at[key] = time.monotonic()
            self._failures[key] = 0


@dataclass
class ConnectorHttp:
    """所有 Connector 共用。transport 只在测试里替换（httpx.MockTransport）。"""

    policy: OutboundPolicy
    connect_timeout: float = 3.0
    read_timeout: float = 10.0
    read_retries: int = 2
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    transport: httpx.AsyncBaseTransport | None = None
    backoff_base_seconds: float = 0.2

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        """返回状态码 < 400 以外的响应也照常返回，由调用方按 Connector 的规则归类。"""
        method = method.upper()
        is_read = method in _READ_METHODS
        attempts = 1 + (self.read_retries if is_read else 0)
        key = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        all_headers = {"Accept": "application/json", **headers}
        if content_type:
            all_headers["Content-Type"] = content_type
        request_id = request_id_var.get()
        if request_id:
            all_headers["X-Request-ID"] = request_id
        last_error: ConnectorError | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(self.backoff_base_seconds * 2 ** (attempt - 1))
            self.breaker.before(key)
            await self.policy.check(url)
            try:
                response = await self._send(method, url, all_headers, params, json)
            except ConnectorError as error:
                self.breaker.failure(key)
                last_error = error
                if error.kind == "UNAVAILABLE" and is_read:
                    continue
                raise
            if response.status_code in _RETRY_STATUSES:
                self.breaker.failure(key)
                if is_read and attempt + 1 < attempts:
                    continue
            else:
                self.breaker.success(key)
            return response
        raise last_error or ConnectorError("UNAVAILABLE", "服务暂时不可用", reason="read_failed")

    async def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: Mapping[str, Any] | list[tuple[str, Any]] | None,
        json: Any,
    ) -> httpx.Response:
        timeout = httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.read_timeout,
            pool=self.connect_timeout,
        )
        is_read = method in _READ_METHODS
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=timeout, follow_redirects=False
            ) as client:
                return await client.request(method, url, headers=headers, params=params, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            # 连接没有建立：请求肯定没有到达对方
            raise ConnectorError(
                "UNAVAILABLE", "无法连接服务", not_sent=True, reason="connect_failed"
            ) from error
        except httpx.TransportError as error:
            # 读超时、连接中途断开：请求可能已经被处理
            if is_read:
                raise ConnectorError(
                    "UNAVAILABLE", "服务响应超时或连接中断", reason="read_failed"
                ) from error
            raise ConnectorError(
                "TIMEOUT_UNKNOWN", "写操作结果未知", reason="result_unknown"
            ) from error
