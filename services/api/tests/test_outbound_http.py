"""出站 HTTP 的安全加固（T-0043）：不需要外部服务。

- 防 DNS 重绑定：检查通过后连接检查过的 IP，Host 头与 TLS 的 SNI 保留原主机名；
  第二次解析即使被换成内网地址，也不会被用上
- 熔断按调用方给的范围计数：一个集成持续失败，不影响指向同一主机的其他集成
"""

import asyncio
import socket
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker, ConnectorHttp, OutboundPolicy


def answer_dns(monkeypatch: pytest.MonkeyPatch, answers: list[list[str]]) -> list[str]:
    """按顺序返回每一次解析的结果（模拟重绑定：第一次公网、之后内网）；返回被解析过的主机名。"""
    asked: list[str] = []

    async def getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        asked.append(host)
        addresses = answers[min(len(asked), len(answers)) - 1]
        family = socket.AF_INET6
        return [
            (socket.AF_INET if ":" not in a else family, socket.SOCK_STREAM, 6, "", (a, port))
            for a in addresses
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    return asked


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": True})


def http_with(recorder: Recorder, **kwargs: Any) -> ConnectorHttp:
    return ConnectorHttp(
        policy=kwargs.pop("policy", OutboundPolicy()),
        transport=httpx.MockTransport(recorder),
        read_retries=kwargs.pop("read_retries", 0),
        backoff_base_seconds=0,
        **kwargs,
    )


# ---------------------------------------------------------------- 防 DNS 重绑定


async def test_connects_to_the_checked_address_with_original_host_and_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_dns(monkeypatch, [["93.184.215.14"]])
    recorder = Recorder()
    response = await http_with(recorder).request(
        "GET", "https://tasks.example.com/api/v1/info?x=1", headers={"Authorization": "Bearer t"}
    )
    assert response.status_code == 200
    sent = recorder.requests[0]
    assert sent.url.host == "93.184.215.14"  # 连接检查过的 IP，不再交给 httpx 解析
    assert sent.url.path == "/api/v1/info" and sent.url.params["x"] == "1"
    assert sent.headers["Host"] == "tasks.example.com"
    assert sent.extensions["sni_hostname"] == "tasks.example.com"  # 证书按主机名校验
    assert sent.headers["Authorization"] == "Bearer t"


async def test_rebinding_after_the_check_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    # 第一次解析（检查时）是公网地址，之后的解析被换成云元数据地址
    asked = answer_dns(monkeypatch, [["93.184.215.14"], ["169.254.169.254"]])
    recorder = Recorder()
    await http_with(recorder).request("GET", "https://rebind.example.com/", headers={})
    assert asked == ["rebind.example.com"]  # 整个请求只解析了一次
    assert recorder.requests[0].url.host == "93.184.215.14"
    # 下一次请求重新检查：解析结果已经是内网地址，请求不会发出
    with pytest.raises(ConnectorError) as caught:
        await http_with(recorder).request("GET", "https://rebind.example.com/", headers={})
    assert caught.value.reason == "destination_not_allowed"
    assert len(recorder.requests) == 1


async def test_ipv6_address_is_bracketed_and_port_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    answer_dns(monkeypatch, [["2606:4700:4700::1111"]])
    recorder = Recorder()
    await http_with(recorder).request("GET", "https://v6.example.com:8443/x", headers={})
    sent = recorder.requests[0]
    assert sent.url.host == "2606:4700:4700::1111" and sent.url.port == 8443
    assert str(sent.url).startswith("https://[2606:4700:4700::1111]:8443/x")
    assert sent.headers["Host"] == "v6.example.com:8443"


async def test_plain_http_keeps_host_header_without_sni(monkeypatch: pytest.MonkeyPatch) -> None:
    answer_dns(monkeypatch, [["93.184.215.14"]])
    recorder = Recorder()
    await http_with(recorder).request("GET", "http://plain.example.com/", headers={})
    sent = recorder.requests[0]
    assert sent.headers["Host"] == "plain.example.com"
    assert "sni_hostname" not in sent.extensions


async def test_allowed_internal_hosts_and_ip_literals_are_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = answer_dns(monkeypatch, [["172.18.0.4"]])
    recorder = Recorder()
    allowed = OutboundPolicy(frozenset({"vikunja"}))
    await http_with(recorder, policy=allowed).request("GET", "http://vikunja:3456/", headers={})
    await http_with(recorder).request("GET", "https://93.184.215.14/", headers={})
    assert [r.url.host for r in recorder.requests] == ["vikunja", "93.184.215.14"]
    assert asked == []  # 允许的内网主机由 Compose 的 DNS 解析，IP 字面量不需要解析


# ---------------------------------------------------------------- 熔断按集成计数


async def test_breaker_counts_each_scope_separately() -> None:
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=60)
    failing = Recorder(status=503)
    healthy = Recorder()
    policy = OutboundPolicy(frozenset({"shared.internal"}))
    first = http_with(failing, policy=policy, breaker=breaker, breaker_scope="integration:a")
    second = http_with(healthy, policy=policy, breaker=breaker, breaker_scope="integration:b")
    for _ in range(2):
        await first.request("GET", "http://shared.internal/", headers={})
    with pytest.raises(ConnectorError) as caught:
        await first.request("GET", "http://shared.internal/", headers={})
    assert caught.value.reason == "circuit_open"
    # 同一主机、另一个集成：照常发请求
    assert (await second.request("GET", "http://shared.internal/", headers={})).status_code == 200
    assert len(healthy.requests) == 1


def test_runtime_scopes_breakers_per_integration() -> None:
    runtime = ConnectorRuntime(Settings(app_env="test"), CircuitBreaker())
    assert runtime.http("integration:1").breaker_scope == "integration:1"
    assert runtime.http().breaker_scope == ""  # 部署级的监控检查按目标服务计数
    assert runtime.http("integration:1").breaker is runtime.http("integration:2").breaker
