"""中转站管理：模型同步与连接检测（T-0202，MC-2）。

单元部分（不需要外部服务）：OpenAI 兼容协议的 URL 拼接与鉴权头、状态与原因归类、Retry-After、
响应大小与压缩、超时、推理请求不重试、TLS 校验不变；Anthropic Messages 与 Gemini 原生协议的
URL（与 OpenClaw 2026.9.36 一致）、鉴权头、分页参数、请求体与错误归类，以及三个协议共用的网络约束。

集成部分（真实 PostgreSQL / Redis；内存版 OpenAI 兼容中转站接在 ConnectorRuntime.transport 上）：
指定模型 / 全部可用模型；不支持列模型时只能手工填写；同步不完整保留旧快照、不移除在用模型；
metadata 通过不等于推理通过；检测在途时更换 Key，旧结果不覆盖新配置；出站策略拒绝私网 /
元数据地址 / 混合 DNS / 重绑定 / 跳转；Key 不出现在响应、审计、数据库明文和日志里；
owner 专属、跨空间 404；限流与在途锁。
"""

import asyncio
import email.utils
import gzip
import inspect
import json
import logging
import secrets
import socket
import ssl
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.domain import model_connections
from app.integrations import model_providers
from app.integrations.http import ConnectorHttp
from app.integrations.model_providers import (
    AnthropicAdapter,
    CappedTransport,
    GeminiAdapter,
    OpenAICompatibleAdapter,
    adapter_for,
    anthropic_url,
    gemini_base,
    gemini_model_path,
    join_url,
    parse_retry_after,
    provider_http,
)
from tests.helpers import (
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    asyncpg_dsn,
    csrf,
    run_sql,
)
from tests.test_agents_api import SETTINGS as AGENT_SETTINGS
from tests.workspace_support import app_of

integration = pytest.mark.integration

KEY = "sk-relay-" + secrets.token_hex(12)
ROTATED = "sk-relay-rotated-" + secrets.token_hex(12)
RELAY_URL = "https://relay.test/v1"
BASE = "/api/v1/model-connections"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
MIB = 1024 * 1024

Reply = Callable[[httpx.Request], Any]


def listing(*ids: str, **extra: Any) -> httpx.Response:
    return httpx.Response(
        200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids], **extra}
    )


def chat_ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "pong"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def status(code: int, **kwargs: Any) -> Reply:
    return lambda _: httpx.Response(code, **kwargs)


def raises(error: type[httpx.TransportError], *, tls: bool = False) -> Reply:
    def reply(request: httpx.Request) -> httpx.Response:
        if tls:
            raise error("[SSL: CERTIFICATE_VERIFY_FAILED]", request=request) from (
                ssl.SSLCertVerificationError(1, "certificate verify failed")
            )
        raise error("simulated", request=request)

    return reply


def reset_mid_body(request: httpx.Request) -> httpx.Response:
    async def body() -> AsyncIterator[bytes]:
        yield b'{"object": "list", "data": [{"id": "gpt-4o-mini"}'
        raise httpx.ReadError("connection reset", request=request)

    return httpx.Response(200, headers={"content-type": "application/json"}, content=body())


def too_large_json(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}], "pad": "x" * (2 * MIB)})


async def _resolve(reply: Reply, request: httpx.Request) -> httpx.Response:
    result = reply(request)
    if inspect.isawaitable(result):
        result = await result
    assert isinstance(result, httpx.Response)
    return result


class Recorder:
    def __init__(self, reply: Reply) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await _resolve(self.reply, request)


# 测试里的主机都在部署允许列表里：出站策略不做 DNS 解析，请求只到 MockTransport
TEST_HOSTS = "relay.test,api.anthropic.com,generativelanguage.googleapis.com"


def _http(recorder: Recorder) -> ConnectorHttp:
    settings = Settings(app_env="test", integration_allowed_hosts=TEST_HOSTS)
    http = provider_http(settings, httpx.MockTransport(recorder))
    http.backoff_base_seconds = 0
    return http


def make_adapter(reply: Reply) -> tuple[OpenAICompatibleAdapter, Recorder]:
    recorder = Recorder(reply)
    return OpenAICompatibleAdapter(_http(recorder), clock=lambda: NOW), recorder


def make_anthropic(reply: Reply) -> tuple[AnthropicAdapter, Recorder]:
    recorder = Recorder(reply)
    return AnthropicAdapter(_http(recorder), clock=lambda: NOW), recorder


def make_gemini(reply: Reply) -> tuple[GeminiAdapter, Recorder]:
    recorder = Recorder(reply)
    return GeminiAdapter(_http(recorder), clock=lambda: NOW), recorder


async def list_with(reply: Reply) -> tuple[model_providers.ModelListing, Recorder]:
    adapter, recorder = make_adapter(reply)
    return await adapter.list_models(base_url=RELAY_URL, api_key=SecretStr(KEY)), recorder


async def infer_with(
    reply: Reply, model_id: str = "gpt-4o-mini"
) -> tuple[model_providers.ProbeResult, Recorder]:
    adapter, recorder = make_adapter(reply)
    result = await adapter.infer(base_url=RELAY_URL, api_key=SecretStr(KEY), model_id=model_id)
    return result, recorder


# ================================================================ 单元：协议适配


def test_join_url_never_doubles_version_or_slashes() -> None:
    assert join_url("https://relay.test/v1", "models") == "https://relay.test/v1/models"
    assert join_url("https://relay.test/v1/", "/models") == "https://relay.test/v1/models"
    assert join_url("https://relay.test", "chat/completions") == (
        "https://relay.test/chat/completions"
    )
    assert join_url("https://relay.test/api/openai/v1", "models").count("/v1") == 1


async def test_list_models_request_shape_and_complete_listing() -> None:
    reply = listing("qwen-max", "GPT-4o", "deepseek-chat", "GPT-4o", "has space", "", "x" * 129)
    result, recorder = await list_with(lambda _: reply)
    assert result.result.status == "passed" and result.result.reason is None
    assert result.complete is True
    # 去重、排序、原样保留大小写；不合规的 ID（空白、空串、超长）跳过
    assert result.model_ids == ("GPT-4o", "deepseek-chat", "qwen-max")
    assert result.result.latency_ms is not None and result.result.latency_ms >= 0
    (sent,) = recorder.requests
    assert sent.method == "GET" and str(sent.url) == "https://relay.test/v1/models"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.headers["accept-encoding"] == "identity"
    assert KEY not in str(sent.url)
    assert not [h for h in sent.headers if h.lower() in ("x-api-key", "api-key")]


async def test_infer_request_shape_is_fixed_tiny_and_not_retried() -> None:
    result, recorder = await infer_with(chat_ok, "deepseek-chat")
    assert result.status == "passed" and result.reason is None
    (sent,) = recorder.requests
    assert sent.method == "POST" and str(sent.url) == "https://relay.test/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(sent.content) == {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
        "stream": False,
    }
    assert KEY not in str(sent.url) and KEY.encode() not in sent.content


async def test_infer_switches_to_max_completion_tokens_only_when_rejected() -> None:
    """OpenAI 的推理模型拒绝 max_tokens（要求 max_completion_tokens）：被 400 拒绝时（没有生成）
    换参数再发一次；其他 400 不重发。"""

    def reply(request: httpx.Request) -> httpx.Response:
        if "max_tokens" in json.loads(request.content):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Unsupported parameter: 'max_tokens' is not supported with this"
                        " model. Use 'max_completion_tokens' instead.",
                        "type": "invalid_request_error",
                        "param": "max_tokens",
                        "code": "unsupported_parameter",
                    }
                },
            )
        return chat_ok(request)

    result, recorder = await infer_with(reply, "o4-mini")
    assert result.status == "passed"
    first, second = recorder.requests
    assert json.loads(first.content)["max_tokens"] == 16
    body = json.loads(second.content)
    assert body["max_completion_tokens"] == 16 and "max_tokens" not in body

    other = status(400, json={"error": {"message": "bad request", "param": "messages"}})
    result, recorder = await infer_with(other)
    assert (result.status, result.reason) == ("failed", "invalid_response")
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    ("reply", "expected", "attempts"),
    [
        (
            status(401, json={"error": {"message": f"Incorrect API key {KEY}"}}),
            ("failed", "unauthorized"),
            1,
        ),
        (status(403), ("failed", "forbidden"), 1),
        (status(404), ("unsupported", "models_endpoint_unsupported"), 1),
        (status(405), ("unsupported", "models_endpoint_unsupported"), 1),
        (status(500), ("failed", "invalid_response"), 1),
        # 网关错误：读请求按 ConnectorHttp 的规则最多再试一次
        (status(502), ("failed", "unreachable"), 2),
        (status(503), ("failed", "unreachable"), 2),
        (status(200, text="<html>login</html>"), ("failed", "invalid_response"), 1),
        (status(200, json={"models": ["gpt-4o-mini"]}), ("failed", "invalid_response"), 1),
        (status(400), ("failed", "invalid_response"), 1),
        # 跳转不跟随：Key 不会被带去别的地址
        (
            status(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}),
            ("failed", "invalid_response"),
            1,
        ),
        (
            status(
                200,
                headers={"content-encoding": "gzip", "content-type": "application/json"},
                content=gzip.compress(b'{"data": [{"id": "a"}]}'),
            ),
            ("failed", "invalid_response"),
            1,
        ),
        (raises(httpx.ReadTimeout), ("failed", "timeout"), 2),
        (raises(httpx.ConnectTimeout), ("failed", "timeout"), 2),
        (raises(httpx.ConnectError), ("failed", "unreachable"), 2),
        (raises(httpx.ConnectError, tls=True), ("failed", "tls_error"), 2),
        (reset_mid_body, ("failed", "unreachable"), 2),
    ],
    ids=[
        "401",
        "403",
        "404",
        "405",
        "500",
        "502",
        "503",
        "non_json",
        "wrong_shape",
        "400",
        "redirect",
        "gzip",
        "read_timeout",
        "connect_timeout",
        "connect_error",
        "tls",
        "reset_mid_body",
    ],
)
async def test_metadata_status_classification(
    reply: Reply, expected: tuple[str, str], attempts: int
) -> None:
    result, recorder = await list_with(reply)
    assert (result.result.status, result.result.reason) == expected
    assert result.complete is False and result.model_ids == ()
    assert len(recorder.requests) == attempts


@pytest.mark.parametrize(
    "reply",
    [
        lambda _: listing("a", "b", has_more=True),
        lambda _: listing(*[f"m-{i:04d}" for i in range(1001)]),
        too_large_json,
    ],
    ids=["has_more", "over_1000", "over_2mib"],
)
async def test_truncated_listings_pass_metadata_but_are_partial(reply: Reply) -> None:
    result, recorder = await list_with(reply)
    assert result.result.status == "passed"
    assert result.complete is False and result.model_ids == ()
    assert len(recorder.requests) == 1


async def test_oversized_non_json_listing_is_invalid() -> None:
    result, _ = await list_with(
        status(200, headers={"content-type": "text/html"}, content=b"x" * (2 * MIB + 1))
    )
    assert (result.result.status, result.result.reason) == ("failed", "invalid_response")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (status(401), ("failed", "unauthorized")),
        (status(403), ("failed", "forbidden")),
        (
            status(
                404,
                json={
                    "error": {
                        "message": "The model `gpt-x` does not exist or you do not have access.",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
            ),
            ("failed", "model_not_available"),
        ),
        (
            status(400, json={"error": {"message": "Model gpt-x does not exist"}}),
            ("failed", "model_not_available"),
        ),
        (
            status(503, json={"error": {"message": "无可用渠道", "code": "model_not_found"}}),
            ("failed", "model_not_available"),
        ),
        (
            status(200, json={"error": {"code": "model_not_found"}}),
            ("failed", "model_not_available"),
        ),
        (
            status(400, json={"error": {"message": "max_tokens is too large"}}),
            ("failed", "invalid_response"),
        ),
        (status(404), ("failed", "invalid_response")),
        (status(500), ("failed", "invalid_response")),
        (status(503), ("failed", "unreachable")),
        (status(200, json={"choices": []}), ("failed", "invalid_response")),
        (status(200, text="pong"), ("failed", "invalid_response")),
        (
            status(307, headers={"location": "https://evil.example/collect"}),
            ("failed", "invalid_response"),
        ),
        # 请求可能已被服务商处理：结果未知，不自动重试
        (raises(httpx.ReadTimeout), ("unknown", "timeout")),
        (raises(httpx.RemoteProtocolError), ("unknown", "unreachable")),
        # 连接没有建立：请求肯定没有发出
        (raises(httpx.ConnectTimeout), ("failed", "timeout")),
        (raises(httpx.ConnectError), ("failed", "unreachable")),
        (raises(httpx.ConnectError, tls=True), ("failed", "tls_error")),
    ],
    ids=[
        "401",
        "403",
        "404_model_not_found",
        "400_model_missing_message",
        "503_model_not_found_code",
        "200_error_model_not_found",
        "400_other",
        "404_bare",
        "500",
        "503",
        "200_no_choices",
        "200_non_json",
        "redirect",
        "read_timeout",
        "protocol_error",
        "connect_timeout",
        "connect_error",
        "tls",
    ],
)
async def test_inference_status_classification_never_retries(
    reply: Reply, expected: tuple[str, str]
) -> None:
    result, recorder = await infer_with(reply)
    assert (result.status, result.reason) == expected
    assert len(recorder.requests) == 1  # POST 从不自动重试
    assert result.retry_at is None


@pytest.mark.parametrize("infer", [False, True])
async def test_rate_limit_parses_retry_after_and_is_not_retried(infer: bool) -> None:
    later = NOW + timedelta(minutes=5)
    cases: list[tuple[dict[str, str], datetime | None]] = [
        ({"retry-after": "120"}, NOW + timedelta(seconds=120)),
        ({"retry-after": email.utils.format_datetime(later, usegmt=True)}, later),
        ({"retry-after": "soon"}, None),
        (
            {"retry-after": email.utils.format_datetime(NOW - timedelta(minutes=1), usegmt=True)},
            None,
        ),
        ({"retry-after": str(10 * 24 * 3600)}, None),
        ({}, None),
    ]
    for headers, retry_at in cases:
        reply = status(429, headers=headers, json={"error": {"message": "slow down"}})
        if infer:
            result, recorder = await infer_with(reply)
        else:
            listed, recorder = await list_with(reply)
            result = listed.result
        assert result.status == "rate_limited" and result.reason is None, headers
        assert result.retry_at == retry_at, headers
        assert len(recorder.requests) == 1, headers  # 429 不立即重试


def test_parse_retry_after_only_accepts_legit_future_values() -> None:
    assert parse_retry_after("0", NOW) == NOW
    assert parse_retry_after(" 30 ", NOW) == NOW + timedelta(seconds=30)
    assert parse_retry_after("-5", NOW) is None
    assert parse_retry_after("1.5", NOW) is None
    assert parse_retry_after("٣٠", NOW) is None  # 只认 ASCII 数字
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", NOW) is None  # 已经过去
    assert parse_retry_after(None, NOW) is None
    assert parse_retry_after("", NOW) is None


async def test_total_deadline_bounds_slow_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_providers, "ATTEMPT_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(model_providers, "RETRY_ALLOWANCE_SECONDS", 0.05)

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return listing("a")

    listed, _ = await list_with(slow)
    assert (listed.result.status, listed.result.reason) == ("failed", "timeout")
    inferred, recorder = await infer_with(slow)
    assert (inferred.status, inferred.reason) == ("unknown", "timeout")
    assert len(recorder.requests) == 1


def test_adapter_for_maps_every_protocol() -> None:
    http = provider_http(Settings(app_env="test"), httpx.MockTransport(Recorder(status(500))))
    assert isinstance(adapter_for("openai_compatible", http), OpenAICompatibleAdapter)
    assert isinstance(adapter_for("anthropic_native", http), AnthropicAdapter)
    assert isinstance(adapter_for("gemini_native", http), GeminiAdapter)
    with pytest.raises(ValueError):
        adapter_for("bedrock", http)


# ---------------------------------------------------------------- Anthropic Messages

ANTHROPIC_URL = "https://api.anthropic.com"


def anthropic_models(*ids: str, has_more: bool = False) -> httpx.Response:
    """官方 GET /v1/models 的响应结构（https://platform.claude.com/docs/en/api/models/list）。"""
    data = [
        {
            "type": "model",
            "id": i,
            "display_name": i,
            "created_at": "2026-09-21T00:00:00Z",
        }
        for i in ids
    ]
    return httpx.Response(
        200,
        json={
            "data": data,
            "has_more": has_more,
            "first_id": ids[0] if ids else None,
            "last_id": ids[-1] if ids else None,
        },
    )


def anthropic_message(_: httpx.Request) -> httpx.Response:
    """官方 POST /v1/messages 的响应结构（https://platform.claude.com/docs/en/api/messages/create）。"""
    return httpx.Response(
        200,
        json={
            "id": "msg_013Zva2CMHLNnXjNJJKqJ2EF",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "pong"}],
            "model": "claude-haiku-5-5",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 8, "output_tokens": 2},
        },
    )


def anthropic_error(code: int, kind: str, message: str, **kwargs: Any) -> Reply:
    """官方错误结构（https://platform.claude.com/docs/en/api/errors）。"""
    body = {
        "type": "error",
        "error": {"type": kind, "message": message},
        "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy",
    }
    return status(code, json=body, **kwargs)


def test_anthropic_url_matches_openclaw() -> None:
    """与 OpenClaw resolveAnthropicMessagesUrl 相同：以 /v1 结尾直接拼，否则补 /v1。"""
    assert anthropic_url("https://api.anthropic.com", "messages") == (
        "https://api.anthropic.com/v1/messages"
    )
    assert anthropic_url("https://api.anthropic.com/", "models") == (
        "https://api.anthropic.com/v1/models"
    )
    assert anthropic_url("https://relay.test/v1", "messages") == "https://relay.test/v1/messages"
    assert anthropic_url("https://relay.test/v1/", "models") == "https://relay.test/v1/models"
    assert anthropic_url("https://relay.test/api/anthropic", "messages") == (
        "https://relay.test/api/anthropic/v1/messages"
    )
    assert anthropic_url("https://relay.test/v1beta", "models") == (
        "https://relay.test/v1beta/v1/models"
    )


def assert_anthropic_auth(sent: httpx.Request) -> None:
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert sent.headers["accept-encoding"] == "identity"
    assert "authorization" not in sent.headers and "x-goog-api-key" not in sent.headers
    assert KEY not in str(sent.url)


async def test_anthropic_list_models_request_shape_and_pagination() -> None:
    adapter, recorder = make_anthropic(
        lambda _: anthropic_models("claude-opus-5-5", "claude-haiku-5-5", "has space")
    )
    listed = await adapter.list_models(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY))
    assert (listed.result.status, listed.result.reason) == ("passed", None)
    assert listed.complete is True
    assert listed.model_ids == ("claude-haiku-5-5", "claude-opus-5-5")
    (sent,) = recorder.requests
    # limit 默认 20、最多 1000：只发一次，取最大页
    assert sent.method == "GET"
    assert str(sent.url) == "https://api.anthropic.com/v1/models?limit=1000"
    assert_anthropic_auth(sent)

    # has_more：不完整，不覆盖旧快照
    adapter, recorder = make_anthropic(lambda _: anthropic_models("a", "b", has_more=True))
    listed = await adapter.list_models(base_url="https://relay.test/v1", api_key=SecretStr(KEY))
    assert listed.result.status == "passed" and listed.complete is False
    assert listed.model_ids == ()
    assert str(recorder.requests[0].url) == "https://relay.test/v1/models?limit=1000"

    # 结构不对（OpenAI 的列表也是 data[].id，能读；完全不对的结构是 invalid_response）
    adapter, _ = make_anthropic(status(200, json={"models": ["a"]}))
    listed = await adapter.list_models(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY))
    assert (listed.result.status, listed.result.reason) == ("failed", "invalid_response")


async def test_anthropic_infer_request_shape_is_fixed_tiny_and_not_retried() -> None:
    adapter, recorder = make_anthropic(anthropic_message)
    result = await adapter.infer(
        base_url=ANTHROPIC_URL, api_key=SecretStr(KEY), model_id="claude-haiku-5-5"
    )
    assert (result.status, result.reason) == ("passed", None)
    (sent,) = recorder.requests
    assert sent.method == "POST"
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert_anthropic_auth(sent)
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == {
        "model": "claude-haiku-5-5",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
    assert KEY.encode() not in sent.content


@pytest.mark.parametrize(
    ("reply", "expected", "attempts"),
    [
        (anthropic_error(401, "authentication_error", "invalid x-api-key"), "unauthorized", 1),
        (anthropic_error(403, "permission_error", "no access"), "forbidden", 1),
        (status(404), "models_endpoint_unsupported", 1),
        (anthropic_error(529, "overloaded_error", "Overloaded"), "unreachable", 1),
        (anthropic_error(500, "api_error", "Internal server error"), "invalid_response", 1),
        (anthropic_error(503, "api_error", "unavailable"), "unreachable", 2),
        (status(200, text="<html>login</html>"), "invalid_response", 1),
    ],
    ids=["401", "403", "404", "529", "500", "503", "non_json"],
)
async def test_anthropic_metadata_classification(
    reply: Reply, expected: str, attempts: int
) -> None:
    adapter, recorder = make_anthropic(reply)
    listed = await adapter.list_models(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY))
    assert listed.result.reason == expected
    assert listed.complete is False
    assert len(recorder.requests) == attempts


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (anthropic_error(401, "authentication_error", "invalid x-api-key"), "unauthorized"),
        (anthropic_error(403, "permission_error", "no access"), "forbidden"),
        # 模型不存在与组织无权使用：404 not_found_error，message 以 “model: <id>” 开头
        (anthropic_error(404, "not_found_error", "model: claude-x"), "model_not_available"),
        # 地址不对的 404 不是“模型不可用”
        (anthropic_error(404, "not_found_error", "Not Found"), "invalid_response"),
        (status(404), "invalid_response"),
        (
            anthropic_error(400, "invalid_request_error", "max_tokens: field required"),
            "invalid_response",
        ),
        (anthropic_error(402, "billing_error", "billing"), "invalid_response"),
        (anthropic_error(413, "request_too_large", "too large"), "invalid_response"),
        (anthropic_error(500, "api_error", "Internal server error"), "invalid_response"),
        (anthropic_error(504, "timeout_error", "timeout"), "unreachable"),
        (anthropic_error(529, "overloaded_error", "Overloaded"), "unreachable"),
        # 200 但不是 message：错误对象或别的结构
        (status(200, json={"type": "error", "error": {"type": "api_error"}}), "invalid_response"),
        (
            status(200, json={"type": "error", "error": {"type": "x", "code": "model_not_found"}}),
            "model_not_available",
        ),
        (status(200, json={"choices": [{"message": {}}]}), "invalid_response"),
    ],
    ids=[
        "401",
        "403",
        "404_model",
        "404_path",
        "404_bare",
        "400",
        "402",
        "413",
        "500",
        "504",
        "529",
        "200_error",
        "200_model_missing",
        "200_openai_shape",
    ],
)
async def test_anthropic_inference_classification_never_retries(
    reply: Reply, expected: str
) -> None:
    adapter, recorder = make_anthropic(reply)
    result = await adapter.infer(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY), model_id="m")
    assert result.status == "failed" and result.reason == expected
    assert len(recorder.requests) == 1


async def test_anthropic_rate_limit_uses_retry_after() -> None:
    reply = anthropic_error(429, "rate_limit_error", "rate limited", headers={"retry-after": "30"})
    adapter, recorder = make_anthropic(reply)
    result = await adapter.infer(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY), model_id="m")
    assert (result.status, result.reason) == ("rate_limited", None)
    assert result.retry_at == NOW + timedelta(seconds=30)
    listed = await adapter.list_models(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY))
    assert listed.result.status == "rate_limited"
    assert len(recorder.requests) == 2  # 都不重试

    # 401 / 429 优先于“模型不存在”：错误信息里提到模型也不改变归类
    for code, expected in ((401, ("failed", "unauthorized")), (429, ("rate_limited", None))):
        adapter, _ = make_anthropic(anthropic_error(code, "not_found_error", "model: claude-x"))
        result = await adapter.infer(base_url=ANTHROPIC_URL, api_key=SecretStr(KEY), model_id="m")
        assert (result.status, result.reason) == expected


# ---------------------------------------------------------------- Gemini

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"


def gemini_models(*models: dict[str, Any], next_page: str | None = None) -> httpx.Response:
    """GET v1beta/models 的响应结构（ListModelsResponse：models[]、nextPageToken）。"""
    body: dict[str, Any] = {"models": list(models)}
    if next_page:
        body["nextPageToken"] = next_page
    return httpx.Response(200, json=body)


def gemini_model(name: str, *methods: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": f"models/{name}", "displayName": name}
    if methods:
        entry["supportedGenerationMethods"] = list(methods)
    return entry


def gemini_ok(_: httpx.Request) -> httpx.Response:
    """GenerateContentResponse：candidates[].content.parts[].text、finishReason、usageMetadata。"""
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "pong"}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            "modelVersion": "gemini-2.5-flash",
        },
    )


def gemini_error(
    code: int, state: str, message: str, *details: dict[str, Any], **kwargs: Any
) -> Reply:
    """google.rpc.Status 的 JSON 形式：{"error": {"code", "message", "status", "details"}}。"""
    error: dict[str, Any] = {"code": code, "message": message, "status": state}
    if details:
        error["details"] = list(details)
    return status(code, json={"error": error}, **kwargs)


KEY_INVALID = {
    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
    "reason": "API_KEY_INVALID",
    "domain": "googleapis.com",
    "metadata": {"service": "generativelanguage.googleapis.com"},
}


def retry_info(delay: str) -> dict[str, Any]:
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}


def test_gemini_urls_match_openclaw() -> None:
    """与 OpenClaw normalizeGoogleApiBaseUrl / resolveGoogleModelPath 相同。"""
    assert gemini_base(GEMINI_URL) == GEMINI_URL
    assert gemini_base(GEMINI_URL + "/") == GEMINI_URL
    # 官方主机没写版本路径：补 /v1beta；其他主机原样使用
    assert gemini_base("https://generativelanguage.googleapis.com") == GEMINI_URL
    assert gemini_base("https://generativelanguage.googleapis.com/v1") == (
        "https://generativelanguage.googleapis.com/v1"
    )
    assert gemini_base("https://relay.test") == "https://relay.test"
    assert gemini_base("http://generativelanguage.googleapis.com") == (
        "http://generativelanguage.googleapis.com"
    )
    assert gemini_model_path("gemini-2.5-flash") == "models/gemini-2.5-flash"
    assert gemini_model_path("models/gemini-2.5-flash") == "models/gemini-2.5-flash"
    assert gemini_model_path("google/gemini-2.5-flash") == "models/gemini-2.5-flash"
    assert gemini_model_path("tunedModels/my-tune") == "tunedModels/my-tune"
    # 模型 ID 里的特殊字符不能改变请求路径或带出查询参数
    assert gemini_model_path("a?key=x#y") == "models/a%3Fkey%3Dx%23y"


def assert_gemini_auth(sent: httpx.Request) -> None:
    assert sent.headers["x-goog-api-key"] == KEY
    assert sent.headers["accept-encoding"] == "identity"
    assert "authorization" not in sent.headers and "x-api-key" not in sent.headers
    assert KEY not in str(sent.url) and "key=" not in str(sent.url)


async def test_gemini_list_models_request_shape_and_pagination() -> None:
    reply = gemini_models(
        gemini_model("gemini-2.5-flash", "generateContent", "countTokens"),
        gemini_model("gemini-2.5-pro", "generateContent"),
        gemini_model("text-embedding-004", "embedContent"),
        gemini_model("relay-model"),  # 中转站没给 supportedGenerationMethods：保留
        {"displayName": "no name"},
    )
    adapter, recorder = make_gemini(lambda _: reply)
    listed = await adapter.list_models(base_url=GEMINI_URL, api_key=SecretStr(KEY))
    assert (listed.result.status, listed.result.reason) == ("passed", None)
    assert listed.complete is True
    # models/ 前缀去掉（OpenClaw 发请求时会补回 models/）；只支持 embedContent 的模型不列
    assert listed.model_ids == ("gemini-2.5-flash", "gemini-2.5-pro", "relay-model")
    (sent,) = recorder.requests
    assert sent.method == "GET"
    assert str(sent.url) == f"{GEMINI_URL}/models?pageSize=1000"
    assert_gemini_auth(sent)

    # nextPageToken：还有下一页，不完整
    adapter, _ = make_gemini(lambda _: gemini_models(gemini_model("a"), next_page="next"))
    listed = await adapter.list_models(base_url=GEMINI_URL, api_key=SecretStr(KEY))
    assert listed.result.status == "passed" and listed.complete is False

    # proto3 JSON 省略空的重复字段：一个模型都没有时是 {}
    adapter, _ = make_gemini(status(200, json={}))
    listed = await adapter.list_models(base_url=GEMINI_URL, api_key=SecretStr(KEY))
    assert listed.result.status == "passed" and listed.complete is True
    assert listed.model_ids == ()

    for wrong in ({"models": "x"}, {"data": [{"id": "a"}]}, ["models"]):
        adapter, _ = make_gemini(status(200, json=wrong))
        listed = await adapter.list_models(base_url=GEMINI_URL, api_key=SecretStr(KEY))
        assert (listed.result.status, listed.result.reason) == ("failed", "invalid_response")


async def test_gemini_infer_request_shape_is_fixed_tiny_and_not_retried() -> None:
    adapter, recorder = make_gemini(gemini_ok)
    result = await adapter.infer(
        base_url=GEMINI_URL, api_key=SecretStr(KEY), model_id="gemini-2.5-flash"
    )
    assert (result.status, result.reason) == ("passed", None)
    (sent,) = recorder.requests
    assert sent.method == "POST"
    assert str(sent.url) == f"{GEMINI_URL}/models/gemini-2.5-flash:generateContent"
    assert_gemini_auth(sent)
    assert json.loads(sent.content) == {
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 16},
    }

    # 中转站地址：原样拼接，不补版本路径
    adapter, recorder = make_gemini(gemini_ok)
    await adapter.infer(
        base_url="https://relay.test/gemini/v1beta", api_key=SecretStr(KEY), model_id="m-1"
    )
    assert str(recorder.requests[0].url) == (
        "https://relay.test/gemini/v1beta/models/m-1:generateContent"
    )


@pytest.mark.parametrize(
    ("reply", "expected", "attempts"),
    [
        # Key 无效：400 INVALID_ARGUMENT + ErrorInfo.reason = API_KEY_INVALID
        (
            gemini_error(400, "INVALID_ARGUMENT", "API key not valid.", KEY_INVALID),
            "unauthorized",
            1,
        ),
        (gemini_error(400, "INVALID_ARGUMENT", "bad request"), "invalid_response", 1),
        (gemini_error(401, "UNAUTHENTICATED", "no credentials"), "unauthorized", 1),
        (gemini_error(403, "PERMISSION_DENIED", "denied"), "forbidden", 1),
        (status(404), "models_endpoint_unsupported", 1),
        (gemini_error(503, "UNAVAILABLE", "overloaded"), "unreachable", 2),
        (gemini_error(500, "INTERNAL", "internal"), "invalid_response", 1),
    ],
    ids=["key_invalid", "400", "401", "403", "404", "503", "500"],
)
async def test_gemini_metadata_classification(reply: Reply, expected: str, attempts: int) -> None:
    adapter, recorder = make_gemini(reply)
    listed = await adapter.list_models(base_url=GEMINI_URL, api_key=SecretStr(KEY))
    assert listed.result.reason == expected
    assert listed.complete is False
    assert len(recorder.requests) == attempts


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            gemini_error(400, "INVALID_ARGUMENT", "API key not valid.", KEY_INVALID),
            ("failed", "unauthorized"),
        ),
        (gemini_error(403, "PERMISSION_DENIED", "denied"), ("failed", "forbidden")),
        (
            gemini_error(
                404,
                "NOT_FOUND",
                "models/gemini-x is not found for API version v1beta, or is not supported"
                " for generateContent.",
            ),
            ("failed", "model_not_available"),
        ),
        (status(404, text="<html>404</html>"), ("failed", "invalid_response")),
        (gemini_error(400, "FAILED_PRECONDITION", "location"), ("failed", "invalid_response")),
        (gemini_error(503, "UNAVAILABLE", "overloaded"), ("failed", "unreachable")),
        (gemini_error(504, "DEADLINE_EXCEEDED", "deadline"), ("failed", "unreachable")),
        (status(200, json={"candidates": []}), ("failed", "invalid_response")),
        (status(200, json={"choices": [{}]}), ("failed", "invalid_response")),
        # 提示被内容过滤拦下：Key 与模型都可用
        (
            status(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
            ("passed", None),
        ),
    ],
    ids=[
        "key_invalid",
        "403",
        "404_model",
        "404_html",
        "400_precondition",
        "503",
        "504",
        "200_no_candidates",
        "200_openai_shape",
        "200_prompt_blocked",
    ],
)
async def test_gemini_inference_classification_never_retries(
    reply: Reply, expected: tuple[str, str | None]
) -> None:
    adapter, recorder = make_gemini(reply)
    result = await adapter.infer(base_url=GEMINI_URL, api_key=SecretStr(KEY), model_id="m")
    assert (result.status, result.reason) == expected
    assert len(recorder.requests) == 1


async def test_gemini_rate_limit_uses_retry_after_or_retry_info() -> None:
    cases: list[tuple[Reply, datetime | None]] = [
        (
            gemini_error(429, "RESOURCE_EXHAUSTED", "quota", retry_info("17s")),
            NOW + timedelta(seconds=17),
        ),
        # 小数秒向上取整
        (
            gemini_error(429, "RESOURCE_EXHAUSTED", "quota", retry_info("0.250s")),
            NOW + timedelta(seconds=1),
        ),
        # Retry-After 优先
        (
            gemini_error(
                429,
                "RESOURCE_EXHAUSTED",
                "quota",
                retry_info("17s"),
                headers={"retry-after": "5"},
            ),
            NOW + timedelta(seconds=5),
        ),
        (gemini_error(429, "RESOURCE_EXHAUSTED", "quota", retry_info("soon")), None),
        (gemini_error(429, "RESOURCE_EXHAUSTED", "quota", retry_info("172800s")), None),
        (gemini_error(429, "RESOURCE_EXHAUSTED", "quota"), None),
    ]
    for reply, retry_at in cases:
        adapter, recorder = make_gemini(reply)
        result = await adapter.infer(base_url=GEMINI_URL, api_key=SecretStr(KEY), model_id="m")
        assert (result.status, result.reason, result.retry_at) == ("rate_limited", None, retry_at)
        listed = await adapter.list_models(base_url=GEMINI_URL, api_key=SecretStr(KEY))
        assert listed.result.status == "rate_limited" and listed.result.retry_at == retry_at
        assert len(recorder.requests) == 2  # 429 都不重试


# ---------------------------------------------------------------- 三个协议共用的网络约束

NATIVE = [
    (make_adapter, RELAY_URL),
    (make_anthropic, ANTHROPIC_URL),
    (make_gemini, GEMINI_URL),
]
NATIVE_IDS = ["openai_compatible", "anthropic_native", "gemini_native"]


@pytest.mark.parametrize(("factory", "base_url"), NATIVE, ids=NATIVE_IDS)
@pytest.mark.parametrize(
    ("reply", "listed", "list_attempts", "inferred"),
    [
        (raises(httpx.ReadTimeout), ("failed", "timeout"), 2, ("unknown", "timeout")),
        (raises(httpx.ConnectError), ("failed", "unreachable"), 2, ("failed", "unreachable")),
        (raises(httpx.ConnectError, tls=True), ("failed", "tls_error"), 2, ("failed", "tls_error")),
        (
            status(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}),
            ("failed", "invalid_response"),
            1,
            ("failed", "invalid_response"),
        ),
        (
            status(
                200,
                headers={"content-encoding": "gzip", "content-type": "application/json"},
                content=gzip.compress(b"{}"),
            ),
            ("failed", "invalid_response"),
            1,
            ("failed", "invalid_response"),
        ),
        (too_large_json, ("passed", None), 1, ("failed", "invalid_response")),
        (status(502), ("failed", "unreachable"), 2, ("failed", "unreachable")),
    ],
    ids=["read_timeout", "connect_error", "tls", "redirect", "gzip", "over_2mib", "502"],
)
async def test_every_protocol_shares_network_constraints(
    factory: Callable[[Reply], tuple[Any, Recorder]],
    base_url: str,
    reply: Reply,
    listed: tuple[str, str | None],
    list_attempts: int,
    inferred: tuple[str, str],
) -> None:
    """超时、TLS、不跟随跳转、拒绝压缩、2 MiB 上限、GET 最多重试一次、POST 不重试：三个协议一致。"""
    adapter, recorder = factory(reply)
    listing_result = await adapter.list_models(base_url=base_url, api_key=SecretStr(KEY))
    assert (listing_result.result.status, listing_result.result.reason) == listed
    assert listing_result.complete is False
    assert len(recorder.requests) == list_attempts
    recorder.requests.clear()
    result = await adapter.infer(base_url=base_url, api_key=SecretStr(KEY), model_id="m")
    assert (result.status, result.reason) == inferred
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(("factory", "base_url"), NATIVE, ids=NATIVE_IDS)
async def test_every_protocol_total_deadline(
    factory: Callable[[Reply], tuple[Any, Recorder]],
    base_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_providers, "ATTEMPT_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(model_providers, "RETRY_ALLOWANCE_SECONDS", 0.05)

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return listing("a")

    adapter, recorder = factory(slow)
    listed = await adapter.list_models(base_url=base_url, api_key=SecretStr(KEY))
    assert (listed.result.status, listed.result.reason) == ("failed", "timeout")
    recorder.requests.clear()
    inferred = await adapter.infer(base_url=base_url, api_key=SecretStr(KEY), model_id="m")
    assert (inferred.status, inferred.reason) == ("unknown", "timeout")
    assert len(recorder.requests) == 1


def test_production_transport_keeps_tls_verification_and_no_redirects() -> None:
    http = provider_http(Settings(app_env="test"), None)
    assert isinstance(http.transport, CappedTransport)
    inner = http.transport.inner
    assert isinstance(inner, httpx.AsyncHTTPTransport)
    context = inner._pool._ssl_context
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert http.connect_timeout == 5.0 and http.read_timeout == 15.0
    assert http.read_retries == 1


# ================================================================ 集成：接口


SETTINGS: dict[str, Any] = {**AGENT_SETTINGS, "integration_allowed_hosts": "relay.test"}


@dataclass
class FakeRelay:
    """内存版中转站：OpenAI 兼容（GET {base}/models、POST {base}/chat/completions），
    另按地址响应 Anthropic（POST …/v1/messages；带 x-api-key 的 GET …/models）与
    Gemini（GET …/v1beta/models、POST …/models/<id>:generateContent）。"""

    models: list[str] = field(default_factory=lambda: ["gpt-4o-mini", "deepseek-chat"])
    requests: list[httpx.Request] = field(default_factory=list)
    list_reply: Reply | None = None
    chat_reply: Reply | None = None
    # 下一次请求到达时先执行（一次性），用来模拟“请求在途时发生的事”
    before: Callable[[httpx.Request], Awaitable[None]] | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.before is not None:
            hook, self.before = self.before, None
            await hook(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/v1beta/models"):
            reply = self.list_reply or (
                lambda _: gemini_models(*[gemini_model(m, "generateContent") for m in self.models])
            )
        elif (
            request.method == "GET" and path.endswith("/models") and "x-api-key" in request.headers
        ):
            reply = self.list_reply or (lambda _: anthropic_models(*self.models))
        elif request.method == "GET" and path.endswith("/models"):
            reply = self.list_reply or (lambda _: listing(*self.models))
        elif request.method == "POST" and path.endswith("/chat/completions"):
            reply = self.chat_reply or self._chat
        elif request.method == "POST" and path.endswith("/v1/messages"):
            reply = self.chat_reply or self._anthropic
        elif request.method == "POST" and path.endswith(":generateContent"):
            reply = self.chat_reply or self._gemini
        else:
            return httpx.Response(404)
        return await _resolve(reply, request)

    def _anthropic(self, request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model not in self.models:
            missing: httpx.Response = anthropic_error(404, "not_found_error", f"model: {model}")(
                request
            )
            return missing
        return anthropic_message(request)

    def _gemini(self, request: httpx.Request) -> httpx.Response:
        model = request.url.path.rsplit("/models/", 1)[1].removesuffix(":generateContent")
        if model not in self.models:
            missing: httpx.Response = gemini_error(
                404, "NOT_FOUND", f"models/{model} is not found"
            )(request)
            return missing
        return gemini_ok(request)

    def _chat(self, request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model not in self.models:
            return httpx.Response(
                404,
                json={
                    "error": {
                        "message": f"The model `{model}` does not exist",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
            )
        return chat_ok(request)


@dataclass
class Ctx:
    client: httpx.AsyncClient
    workspace_id: str
    owner_id: uuid.UUID
    database_url: str
    relay: FakeRelay
    make_client: ClientFactory
    responses: list[httpx.Response] = field(default_factory=list)

    @property
    def app(self) -> Any:
        return app_of(self.client)

    def headers(self, *, write: bool = False, workspace: str | None = None) -> dict[str, str]:
        return {
            "X-Workspace-Id": workspace or self.workspace_id,
            **(csrf(self.client) if write else {}),
        }

    def _keep(self, response: httpx.Response) -> httpx.Response:
        self.responses.append(response)
        return response

    async def create(self, workspace: str | None = None, **overrides: Any) -> dict[str, Any]:
        body = {
            "name": "中转站 A",
            "connection_type": "relay",
            "protocol": "openai_compatible",
            "base_url": RELAY_URL,
            "api_key": KEY,
            "model_scope": "selected",
            "models": [{"model_id": "gpt-4o-mini"}, {"model_id": "deepseek-chat"}],
            **overrides,
        }
        response = self._keep(
            await self.client.post(
                BASE, json=body, headers=self.headers(write=True, workspace=workspace)
            )
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def get(self, connection_id: str, workspace: str | None = None) -> dict[str, Any]:
        response = self._keep(
            await self.client.get(
                f"{BASE}/{connection_id}", headers=self.headers(workspace=workspace)
            )
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def models(self, connection_id: str) -> dict[str, dict[str, Any]]:
        response = self._keep(
            await self.client.get(
                f"{BASE}/{connection_id}/models", params={"limit": 100}, headers=self.headers()
            )
        )
        assert response.status_code == 200, response.text
        return {m["model_id"]: m for m in response.json()["items"]}

    async def reset_limits(self) -> None:
        redis = self.app.state.redis
        async for key in redis.scan_iter("model_connection_probe:*"):
            await redis.delete(key)

    async def sync(
        self,
        connection_id: str,
        *,
        version: int | None = None,
        workspace: str | None = None,
        fresh: bool = True,
    ) -> httpx.Response:
        if version is None:
            version = (await self.get(connection_id, workspace))["version"]
        if fresh:
            await self.reset_limits()
        return self._keep(
            await self.client.post(
                f"{BASE}/{connection_id}/models/sync",
                json={"version": version},
                headers=self.headers(write=True, workspace=workspace),
            )
        )

    async def synced(self, connection_id: str) -> dict[str, Any]:
        response = await self.sync(connection_id)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def check(
        self,
        connection_id: str,
        mode: str,
        model_id: str | None = None,
        *,
        version: int | None = None,
        workspace: str | None = None,
        fresh: bool = True,
    ) -> httpx.Response:
        if version is None:
            version = (await self.get(connection_id, workspace))["version"]
        if fresh:
            await self.reset_limits()
        body: dict[str, Any] = {"version": version, "mode": mode}
        if model_id is not None:
            body["model_id"] = model_id
        return self._keep(
            await self.client.post(
                f"{BASE}/{connection_id}/checks",
                json=body,
                headers=self.headers(write=True, workspace=workspace),
            )
        )

    async def checked(
        self, connection_id: str, mode: str, model_id: str | None = None
    ) -> dict[str, Any]:
        response = await self.check(connection_id, mode, model_id)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def put_models(
        self, connection_id: str, scope: str, models: list[str] | None = None
    ) -> httpx.Response:
        version = (await self.get(connection_id))["version"]
        return self._keep(
            await self.client.put(
                f"{BASE}/{connection_id}/models",
                json={
                    "version": version,
                    "model_scope": scope,
                    "models": [{"model_id": m} for m in models or []],
                },
                headers=self.headers(write=True),
            )
        )

    async def rotate(self, connection_id: str, key: str) -> dict[str, Any]:
        version = (await self.get(connection_id))["version"]
        response = self._keep(
            await self.client.post(
                f"{BASE}/{connection_id}/rotate-key",
                json={"version": version, "api_key": key},
                headers=self.headers(write=True),
            )
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def bind(self, connection_id: str, model_id: str, name: str) -> str:
        """建一个实例，并让它以这个连接的模型为目标（绑定接口由 MC-4 提供，这里直接写表）。"""
        response = await self.client.post(
            "/api/v1/agents/instances",
            json={"name": name, "network_mode": "intranet"},
            headers=self.headers(write=True),
        )
        assert response.status_code == 201, response.text
        instance_id = response.json()["id"]
        await run_sql(
            self.database_url,
            "INSERT INTO workspace.agent_model_bindings"
            " (instance_id, workspace_id, connection_id, model_id, apply_state)"
            " VALUES ($1, $2, $3, $4, 'pending')",
            uuid.UUID(instance_id),
            uuid.UUID(self.workspace_id),
            uuid.UUID(connection_id),
            model_id,
        )
        return str(instance_id)


@pytest.fixture
async def ctx(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Ctx]:
    relay = FakeRelay()
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201, setup.text
        data = setup.json()
        app_of(client).state.connectors.transport = httpx.MockTransport(relay.handle)
        yield Ctx(
            client=client,
            workspace_id=data["workspaces"][0]["id"],
            owner_id=uuid.UUID(data["user"]["id"]),
            database_url=migrated_database_url,
            relay=relay,
            make_client=make_client,
        )


def _error(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def _plaintext_anywhere(database_url: str, value: str) -> list[str]:
    """工作空间 schema 里所有表的所有行（转成 JSON 文本）中出现这个值的表。"""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        tables = [
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'workspace' AND table_type = 'BASE TABLE'"
            )
        ]
        found = []
        for table in tables:
            count = await conn.fetchval(
                f'SELECT count(*) FROM workspace."{table}" x'  # noqa: S608 - 表名来自系统目录
                " WHERE strpos(row_to_json(x)::text, $1) > 0",
                value,
            )
            if count:
                found.append(table)
        return found
    finally:
        await conn.close()


async def _audit(database_url: str, action: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        rows = await conn.fetch(
            "SELECT metadata_redacted_json::text AS m FROM workspace.audit_logs"
            " WHERE action = $1 ORDER BY created_at",
            action,
        )
    finally:
        await conn.close()
    return [json.loads(row["m"]) for row in rows]


# ---------------------------------------------------------------- 模型目录


@integration
async def test_manual_and_all_available_catalog(ctx: Ctx) -> None:
    created = await ctx.create()
    cid = created["id"]
    assert created["configuration_state"] == "ready" and created["sync_state"] is None
    ctx.relay.models = ["gpt-4o-mini", "deepseek-chat", "qwen-max", "o3-mini"]

    # 指定模型：同步只更新快照与已选模型的 last_seen_at，不改变选中的集合
    result = await ctx.synced(cid)
    assert result["sync_state"] == "complete" and result["kept_previous"] is False
    assert result["discovered"] == sorted(ctx.relay.models)
    assert (result["added"], result["removed"], result["total"]) == (0, 0, 2)
    assert result["reason"] is None and result["retry_at"] is None
    assert result["kept_in_use"] == []
    out = result["connection"]
    assert out["sync_state"] == "complete" and out["models_synced_at"] is not None
    assert out["config_revision"] == created["config_revision"]  # 选中的集合没变
    assert out["version"] == created["version"] + 1
    models = await ctx.models(cid)
    assert set(models) == {"gpt-4o-mini", "deepseek-chat"}
    assert all(m["source"] == "manual" and m["availability"] == "listed" for m in models.values())
    assert all(m["last_seen_at"] is not None for m in models.values())
    sent = ctx.relay.requests[-1]
    assert sent.method == "GET" and sent.url.path == "/v1/models"
    assert sent.headers["authorization"] == f"Bearer {KEY}" and KEY not in str(sent.url)

    # 全部可用模型：模型集合就是完整同步到的快照
    switched = await ctx.put_models(cid, "all_available")
    assert switched.status_code == 200, switched.text
    assert set(await ctx.models(cid)) == set(ctx.relay.models)
    before = await ctx.get(cid)

    # 服务商的列表变了：新增 / 移除，修订号只因可用集合变化加 1
    ctx.relay.models = ["gpt-4o-mini", "deepseek-chat", "qwen-max", "glm-4"]
    changed = await ctx.synced(cid)
    assert (changed["added"], changed["removed"], changed["total"]) == (1, 1, 4)
    assert changed["connection"]["config_revision"] == before["config_revision"] + 1
    models = await ctx.models(cid)
    assert set(models) == set(ctx.relay.models)
    assert models["glm-4"]["source"] == "discovered"
    assert models["glm-4"]["availability"] == "listed"

    same = await ctx.synced(cid)
    assert (same["added"], same["removed"]) == (0, 0)
    assert same["connection"]["config_revision"] == changed["connection"]["config_revision"]
    assert same["connection"]["configuration_state"] == "ready"

    # 一个 Key 多个模型：分别推理检测，请求都发到同一个地址，只有通过的模型标 verified
    for model_id in ("gpt-4o-mini", "glm-4"):
        check = await ctx.checked(cid, "inference", model_id)
        assert check["status"] == "passed" and check["model_id"] == model_id
    posts = [r for r in ctx.relay.requests if r.method == "POST"]
    assert [json.loads(r.content)["model"] for r in posts] == ["gpt-4o-mini", "glm-4"]
    assert {str(r.url) for r in posts} == {"https://relay.test/v1/chat/completions"}
    models = await ctx.models(cid)
    assert {m for m, row in models.items() if row["availability"] == "verified"} == {
        "gpt-4o-mini",
        "glm-4",
    }


@integration
@pytest.mark.parametrize(
    ("protocol", "base_url", "list_url", "post_url", "auth_header"),
    [
        (
            "anthropic_native",
            "https://relay.test/anthropic",
            "https://relay.test/anthropic/v1/models?limit=1000",
            "https://relay.test/anthropic/v1/messages",
            "x-api-key",
        ),
        (
            "gemini_native",
            "https://relay.test/v1beta",
            "https://relay.test/v1beta/models?pageSize=1000",
            "https://relay.test/v1beta/models/{model}:generateContent",
            "x-goog-api-key",
        ),
    ],
    ids=["anthropic_native", "gemini_native"],
)
async def test_native_protocols_sync_and_check(
    ctx: Ctx, protocol: str, base_url: str, list_url: str, post_url: str, auth_header: str
) -> None:
    """原生协议走同一套接口：同步模型、metadata 检测、逐个模型推理检测；Key 只在各自的请求头里。"""
    created = await ctx.create(name=f"{protocol} 中转", protocol=protocol, base_url=base_url)
    cid = created["id"]
    ctx.relay.models = ["deepseek-chat", "gpt-4o-mini", "qwen-max"]

    result = await ctx.synced(cid)
    assert result["sync_state"] == "complete", result
    assert result["discovered"] == sorted(ctx.relay.models)
    metadata = await ctx.checked(cid, "metadata")
    assert (metadata["status"], metadata["reason"]) == ("passed", None)
    for model_id in ("gpt-4o-mini", "deepseek-chat"):
        check = await ctx.checked(cid, "inference", model_id)
        assert (check["status"], check["model_id"]) == ("passed", model_id)
    missing = await ctx.put_models(cid, "selected", ["gpt-4o-mini", "not-listed"])
    assert missing.status_code == 200, missing.text
    gone = await ctx.checked(cid, "inference", "not-listed")
    assert (gone["status"], gone["reason"]) == ("failed", "model_not_available")
    models = await ctx.models(cid)
    assert models["gpt-4o-mini"]["availability"] == "verified"

    gets = [r for r in ctx.relay.requests if r.method == "GET"]
    posts = [r for r in ctx.relay.requests if r.method == "POST"]
    assert {str(r.url) for r in gets} == {list_url}
    sent_models = ["gpt-4o-mini", "deepseek-chat", "not-listed"]
    if protocol == "anthropic_native":
        assert {str(r.url) for r in posts} == {post_url}
        assert [json.loads(r.content)["model"] for r in posts] == sent_models
    else:
        assert [str(r.url) for r in posts] == [post_url.format(model=m) for m in sent_models]
    for sent in ctx.relay.requests:
        assert sent.headers[auth_header] == KEY
        assert "authorization" not in sent.headers
        assert KEY not in str(sent.url)
    for response in ctx.responses:
        assert KEY not in response.text


@integration
async def test_unsupported_models_endpoint_keeps_manual_mode(ctx: Ctx) -> None:
    ctx.relay.list_reply = status(404, json={"error": {"message": "Not Found"}})
    created = await ctx.create(models=[{"model_id": "gpt-4o-mini"}])
    cid = created["id"]

    result = await ctx.synced(cid)
    assert result["sync_state"] == "unsupported"
    assert result["reason"] == "models_endpoint_unsupported"
    assert result["kept_previous"] is True and result["discovered"] == []
    assert (result["added"], result["removed"], result["total"]) == (0, 0, 1)
    assert result["connection"]["sync_state"] == "unsupported"
    assert result["connection"]["configuration_state"] == "ready"  # 手工模型照常可用

    # 不能把“没有列表”伪装成空的“全部可用模型”
    rejected = await ctx.put_models(cid, "all_available")
    assert rejected.status_code == 409 and _error(rejected) == "MODEL_SYNC_REQUIRED"
    assert set(await ctx.models(cid)) == {"gpt-4o-mini"}

    metadata = await ctx.checked(cid, "metadata")
    assert (metadata["status"], metadata["reason"]) == (
        "unsupported",
        "models_endpoint_unsupported",
    )
    assert metadata["mode"] == "metadata" and metadata["model_id"] is None

    # 手工模型仍可单独做推理验证
    inference = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert inference["status"] == "passed"
    assert (await ctx.models(cid))["gpt-4o-mini"]["availability"] == "verified"

    ctx.relay.list_reply = status(405)
    again = await ctx.synced(cid)
    assert again["sync_state"] == "unsupported" and again["kept_previous"] is True


@integration
async def test_partial_sync_preserves_snapshot_and_bound_models(ctx: Ctx) -> None:
    ctx.relay.models = ["gpt-4o-mini", "deepseek-chat", "qwen-max"]
    created = await ctx.create(models=[{"model_id": "gpt-4o-mini"}])
    cid = created["id"]
    assert (await ctx.synced(cid))["sync_state"] == "complete"
    assert (await ctx.put_models(cid, "all_available")).status_code == 200
    instance_id = await ctx.bind(cid, "deepseek-chat", "实例 A")
    baseline = await ctx.get(cid)
    snapshot = sorted(ctx.relay.models)

    future = email.utils.format_datetime(datetime.now(UTC) + timedelta(minutes=5), usegmt=True)
    variants: list[tuple[str, Reply, str, str | None]] = [
        ("has_more", lambda _: listing("gpt-4o-mini", has_more=True), "partial", None),
        (
            "over_1000",
            lambda _: listing(*[f"m-{i:04d}" for i in range(1001)]),
            "partial",
            None,
        ),
        ("over_2mib", too_large_json, "partial", None),
        ("reset_mid_body", reset_mid_body, "failed", "unreachable"),
        ("server_error", status(503), "failed", "unreachable"),
        ("unauthorized", status(401), "failed", "unauthorized"),
        ("rate_limited", status(429, headers={"retry-after": future}), "failed", None),
        ("unsupported", status(404), "unsupported", "models_endpoint_unsupported"),
        ("timeout", raises(httpx.ReadTimeout), "failed", "timeout"),
    ]
    for name, reply, sync_state, reason in variants:
        ctx.relay.list_reply = reply
        result = await ctx.synced(cid)
        assert result["sync_state"] == sync_state, name
        assert result["reason"] == reason, name
        assert result["kept_previous"] is True, name
        assert result["discovered"] == snapshot, name
        assert (result["added"], result["removed"], result["total"]) == (0, 0, 3), name
        out = result["connection"]
        # 生效的仍是上一次完整快照：连接保持 ready，修订号不变
        assert out["sync_state"] == "complete", name
        assert out["configuration_state"] == "ready", name
        assert out["config_revision"] == baseline["config_revision"], name
        assert set(await ctx.models(cid)) == set(snapshot), name
        if name == "rate_limited":
            assert result["retry_at"] is not None
            assert abs(_at(result["retry_at"]) - email.utils.parsedate_to_datetime(future)) < (
                timedelta(seconds=1)
            )

    # 完整同步不再列出在用的模型：不默默移除，单独列出；没人用的照常移除
    ctx.relay.list_reply = None
    ctx.relay.models = ["gpt-4o-mini"]
    result = await ctx.synced(cid)
    assert result["sync_state"] == "complete" and result["kept_previous"] is False
    assert result["discovered"] == ["gpt-4o-mini"]
    assert result["kept_in_use"] == ["deepseek-chat"]
    assert (result["added"], result["removed"], result["total"]) == (0, 1, 2)
    assert set(await ctx.models(cid)) == {"gpt-4o-mini", "deepseek-chat"}
    assert result["connection"]["config_revision"] == baseline["config_revision"] + 1
    assert result["connection"]["binding_count"] == 1
    binding = await run_sql(
        ctx.database_url,
        "SELECT model_id FROM workspace.agent_model_bindings WHERE instance_id = $1",
        uuid.UUID(instance_id),
    )
    assert binding == "deepseek-chat"
    audits = await _audit(ctx.database_url, "model_connection.models_synced")
    assert audits[-1]["kept_in_use"] == 1 and audits[-1]["removed"] == 1


# ---------------------------------------------------------------- 检测结果


@integration
async def test_check_results_are_categories_and_metadata_is_not_inference(ctx: Ctx) -> None:
    created = await ctx.create()
    cid = created["id"]

    metadata = await ctx.checked(cid, "metadata")
    assert metadata["status"] == "passed" and metadata["reason"] is None
    assert metadata["mode"] == "metadata" and metadata["model_id"] is None
    assert metadata["config_revision"] == created["config_revision"]
    assert isinstance(metadata["latency_ms"], int) and metadata["retry_at"] is None
    current = await ctx.get(cid)
    assert current["last_check"]["id"] == metadata["id"]
    assert current["version"] == created["version"]  # 检测结果不是配置，不改 version
    # metadata 通过不代表任何模型能推理
    assert {m["availability"] for m in (await ctx.models(cid)).values()} == {"listed"}

    ctx.relay.chat_reply = status(401, json={"error": {"message": f"bad key {KEY}"}})
    failed = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert (failed["status"], failed["reason"]) == ("failed", "unauthorized")
    assert failed["mode"] == "inference" and failed["model_id"] == "gpt-4o-mini"
    assert (await ctx.get(cid))["last_check"]["id"] == failed["id"]
    assert (await ctx.models(cid))["gpt-4o-mini"]["availability"] == "listed"

    ctx.relay.chat_reply = status(403)
    assert (await ctx.checked(cid, "inference", "gpt-4o-mini"))["reason"] == "forbidden"

    started = datetime.now(UTC)
    ctx.relay.chat_reply = status(429, headers={"retry-after": "30"})
    limited = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert limited["status"] == "rate_limited" and limited["reason"] is None
    retry_at = _at(limited["retry_at"])
    assert started + timedelta(seconds=25) < retry_at < datetime.now(UTC) + timedelta(seconds=35)

    later = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    ctx.relay.chat_reply = status(
        429, headers={"retry-after": email.utils.format_datetime(later, usegmt=True)}
    )
    dated = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert _at(dated["retry_at"]) == later

    ctx.relay.chat_reply = status(429, headers={"retry-after": "later please"})
    assert (await ctx.checked(cid, "inference", "gpt-4o-mini"))["retry_at"] is None

    posts_before = len([r for r in ctx.relay.requests if r.method == "POST"])
    ctx.relay.chat_reply = status(503)
    unavailable = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert (unavailable["status"], unavailable["reason"]) == ("failed", "unreachable")
    ctx.relay.chat_reply = raises(httpx.ReadTimeout)
    timed_out = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert (timed_out["status"], timed_out["reason"]) == ("unknown", "timeout")
    ctx.relay.chat_reply = status(200, text="not json")
    assert (await ctx.checked(cid, "inference", "gpt-4o-mini"))["reason"] == "invalid_response"
    # 推理请求一次检测只发一次
    assert len([r for r in ctx.relay.requests if r.method == "POST"]) == posts_before + 3

    # 模型在连接的集合里，但服务商说不存在
    ctx.relay.chat_reply = None
    ctx.relay.models = ["gpt-4o-mini"]
    missing = await ctx.checked(cid, "inference", "deepseek-chat")
    assert (missing["status"], missing["reason"]) == ("failed", "model_not_available")

    passed = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert passed["status"] == "passed"
    models = await ctx.models(cid)
    assert models["gpt-4o-mini"]["availability"] == "verified"
    assert models["deepseek-chat"]["availability"] == "listed"
    after = await ctx.get(cid)
    assert after["last_check"]["id"] == passed["id"] and after["last_check"]["mode"] == "inference"
    # 失败的检测不清空配置
    assert after["configuration_state"] == "ready" and after["model_count"] == 2
    rows = await run_sql(
        ctx.database_url,
        "SELECT count(*) FROM workspace.model_connection_checks WHERE connection_id = $1",
        uuid.UUID(cid),
    )
    assert rows == 11


@integration
async def test_check_preconditions_and_disabled_connection(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await ctx.create()
    cid = created["id"]

    no_model = await ctx.check(cid, "inference")
    assert no_model.status_code == 422 and _error(no_model) == "VALIDATION_FAILED"
    stranger = await ctx.check(cid, "inference", "qwen-max")
    assert stranger.status_code == 422 and _error(stranger) == "MODEL_NOT_ALLOWED"
    with_model = await ctx.check(cid, "metadata", "gpt-4o-mini")
    assert with_model.status_code == 422
    stale = await ctx.check(cid, "metadata", version=created["version"] + 5)
    assert stale.status_code == 409 and _error(stale) == "VERSION_CONFLICT"
    stale_sync = await ctx.sync(cid, version=created["version"] + 5)
    assert stale_sync.status_code == 409 and _error(stale_sync) == "VERSION_CONFLICT"
    assert ctx.relay.requests == []

    # 停用的连接仍可检测（排查问题），停用不影响检测
    patched = await ctx.client.patch(
        f"{BASE}/{cid}",
        json={"version": created["version"], "enabled": False},
        headers=ctx.headers(write=True),
    )
    assert patched.status_code == 200, patched.text
    assert (await ctx.checked(cid, "metadata"))["status"] == "passed"
    assert (await ctx.synced(cid))["sync_state"] == "complete"

    # 草稿：没有协议和地址
    draft = await ctx.create(
        name="草稿",
        connection_type=None,
        protocol=None,
        base_url=None,
        models=[],
        save_as_draft=True,
    )
    incomplete = await ctx.check(draft["id"], "metadata")
    assert incomplete.status_code == 409 and _error(incomplete) == "MODEL_CONFIGURATION_INCOMPLETE"
    assert _error(await ctx.sync(draft["id"])) == "MODEL_CONFIGURATION_INCOMPLETE"

    # 协议没有核验开放（例如旧数据里存着、或部署只开放了 openai_compatible）：不能访问服务商
    monkeypatch.setattr(model_connections, "SUPPORTED_PROTOCOLS", ("openai_compatible",))
    other = await ctx.create(name="原生协议")
    await run_sql(
        ctx.database_url,
        "UPDATE workspace.model_connections SET protocol = 'anthropic_native' WHERE id = $1",
        uuid.UUID(other["id"]),
    )
    unsupported = await ctx.check(other["id"], "metadata")
    assert unsupported.status_code == 409 and _error(unsupported) == "MODEL_PROTOCOL_UNSUPPORTED"

    # 非模型凭据
    await run_sql(
        ctx.database_url,
        "UPDATE workspace.model_connections SET migration_state = 'non_model' WHERE id = $1",
        uuid.UUID(other["id"]),
    )
    non_model = await ctx.sync(other["id"])
    assert non_model.status_code == 409 and _error(non_model) == "MODEL_CONNECTION_NON_MODEL"

    # Key 解不开：记为 credential_unreadable，不发请求
    sent = len(ctx.relay.requests)
    await run_sql(
        ctx.database_url,
        "UPDATE workspace.agent_secret_versions SET ciphertext_ref = 'enc:v1:bogus:AAAA'"
        " WHERE secret_id = (SELECT credential_secret_id FROM workspace.model_connections"
        " WHERE id = $1)",
        uuid.UUID(cid),
    )
    unreadable = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert (unreadable["status"], unreadable["reason"]) == ("failed", "credential_unreadable")
    unreadable_sync = await ctx.synced(cid)
    assert unreadable_sync["reason"] == "credential_unreadable"
    assert unreadable_sync["kept_previous"] is True
    assert len(ctx.relay.requests) == sent


# ---------------------------------------------------------------- 修订号竞态


@integration
async def test_stale_check_does_not_replace_new_revision(ctx: Ctx) -> None:
    created = await ctx.create()
    cid = created["id"]
    first = await ctx.checked(cid, "metadata")
    assert (await ctx.get(cid))["last_check"]["id"] == first["id"]

    rotated: dict[str, Any] = {}

    async def rotate_meanwhile(_: httpx.Request) -> None:
        rotated.update(await ctx.rotate(cid, ROTATED))

    # 推理请求在途时更换 Key：旧回包不能写成新 Key 的检测结果
    ctx.relay.before = rotate_meanwhile
    response = await ctx.check(cid, "inference", "gpt-4o-mini", version=created["version"])
    assert response.status_code == 200, response.text
    stale = response.json()
    assert rotated["config_revision"] == created["config_revision"] + 1
    assert (stale["status"], stale["reason"]) == ("unknown", "stale_revision")
    assert stale["config_revision"] == created["config_revision"]
    assert stale["retry_at"] is None
    current = await ctx.get(cid)
    assert current["config_revision"] == rotated["config_revision"]
    assert current["last_check"] is None
    assert (await ctx.models(cid))["gpt-4o-mini"]["availability"] == "listed"
    linked = await run_sql(
        ctx.database_url,
        "SELECT last_check_id FROM workspace.model_connections WHERE id = $1",
        uuid.UUID(cid),
    )
    assert linked is None
    stored = await run_sql(
        ctx.database_url,
        "SELECT status || '/' || reason FROM workspace.model_connection_checks WHERE id = $1",
        uuid.UUID(stale["id"]),
    )
    assert stored == "unknown/stale_revision"
    assert ctx.relay.requests[-1].headers["authorization"] == f"Bearer {KEY}"

    # 新 Key 的检测正常写回
    fresh = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert fresh["status"] == "passed"
    assert fresh["config_revision"] == rotated["config_revision"]
    assert ctx.relay.requests[-1].headers["authorization"] == f"Bearer {ROTATED}"
    assert (await ctx.get(cid))["last_check"]["id"] == fresh["id"]
    assert (await ctx.models(cid))["gpt-4o-mini"]["availability"] == "verified"

    # 同步在途时更换 Key：结果作废，不保存快照
    async def rotate_back(_: httpx.Request) -> None:
        await ctx.rotate(cid, KEY)

    ctx.relay.models = ["gpt-4o-mini", "deepseek-chat", "qwen-max"]
    ctx.relay.before = rotate_back
    version = (await ctx.get(cid))["version"]
    synced = await ctx.sync(cid, version=version)
    assert synced.status_code == 200, synced.text
    result = synced.json()
    assert (result["sync_state"], result["reason"]) == ("failed", "stale_revision")
    assert result["kept_previous"] is True and result["discovered"] == []
    assert result["connection"]["sync_state"] is None
    assert result["connection"]["models_synced_at"] is None


# ---------------------------------------------------------------- 出站策略


@integration
async def test_outbound_policy_prevents_secret_exfiltration(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    original = loop.getaddrinfo
    answers: list[list[str]] = []
    asked: list[str] = []

    async def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if host != "relay.example.net":
            return await original(host, port, *args, **kwargs)
        asked.append(host)
        addresses = answers.pop(0) if len(answers) > 1 else answers[0]
        return [
            (
                socket.AF_INET6 if ":" in a else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (a, port),
            )
            for a in addresses
        ]

    monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)
    created = await ctx.create(base_url="https://relay.example.net/v1")
    cid = created["id"]

    # 私网、回环、链路本地 / 云元数据、IPv6 唯一本地、混合公私 DNS：请求不发出
    for addresses in (
        ["10.0.0.8"],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["fd00::1"],
        ["93.184.215.14", "10.0.0.8"],
    ):
        answers[:] = [addresses]
        check = await ctx.checked(cid, "inference", "gpt-4o-mini")
        assert (check["status"], check["reason"]) == ("failed", "destination_not_allowed"), (
            addresses
        )
        assert check["latency_ms"] is None
        synced = await ctx.synced(cid)
        assert synced["reason"] == "destination_not_allowed", addresses
    assert ctx.relay.requests == []

    # 公网地址：连接检查过的 IP，Host 与 SNI（证书校验）仍是原主机名
    answers[:] = [["93.184.215.14"]]
    assert (await ctx.checked(cid, "metadata"))["status"] == "passed"
    (sent,) = ctx.relay.requests
    assert sent.url.host == "93.184.215.14" and sent.url.path == "/v1/models"
    assert sent.headers["host"] == "relay.example.net"
    assert sent.extensions["sni_hostname"] == "relay.example.net"
    assert sent.headers["authorization"] == f"Bearer {KEY}"

    # DNS 重绑定：每次检测都重新检查，换成元数据地址后不再发请求
    answers[:] = [["93.184.215.14"], ["169.254.169.254"]]
    assert (await ctx.checked(cid, "metadata"))["status"] == "passed"
    rebound = await ctx.checked(cid, "metadata")
    assert rebound["reason"] == "destination_not_allowed"
    assert len(ctx.relay.requests) == 2

    # 跳转不跟随：Key 不会被带到跳转目标
    answers[:] = [["93.184.215.14"]]
    ctx.relay.list_reply = status(
        302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
    )
    ctx.relay.chat_reply = status(307, headers={"location": "https://collector.example/steal"})
    redirected = await ctx.checked(cid, "metadata")
    assert (redirected["status"], redirected["reason"]) == ("failed", "invalid_response")
    redirected_post = await ctx.checked(cid, "inference", "gpt-4o-mini")
    assert (redirected_post["status"], redirected_post["reason"]) == ("failed", "invalid_response")
    assert len(ctx.relay.requests) == 4
    assert {r.url.host for r in ctx.relay.requests} == {"93.184.215.14"}

    # IP 字面量地址：不经过 DNS 也同样检查
    literal = await ctx.create(name="元数据地址", base_url="https://169.254.169.254/v1")
    blocked = await ctx.checked(literal["id"], "metadata")
    assert blocked["reason"] == "destination_not_allowed"
    assert len(ctx.relay.requests) == 4
    assert set(asked) == {"relay.example.net"}


# ---------------------------------------------------------------- Key 不外泄


@integration
async def test_key_never_appears_in_responses_audit_or_logs(
    ctx: Ctx, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    created = await ctx.create()
    cid = created["id"]
    ctx.relay.models = ["gpt-4o-mini", "deepseek-chat", "qwen-max"]
    await ctx.synced(cid)
    await ctx.checked(cid, "metadata")
    await ctx.checked(cid, "inference", "gpt-4o-mini")
    # 服务商的错误正文里回显了 Key：只记类别，正文不保存、不回给浏览器
    ctx.relay.chat_reply = status(401, json={"error": {"message": f"Incorrect API key: {KEY}"}})
    ctx.relay.list_reply = status(400, json={"error": {"message": f"bad key {KEY}"}})
    await ctx.checked(cid, "inference", "gpt-4o-mini")
    await ctx.synced(cid)
    await ctx.checked(cid, "metadata")
    await ctx.rotate(cid, ROTATED)
    ctx.relay.chat_reply = None
    await ctx.checked(cid, "inference", "gpt-4o-mini")
    activity = await ctx.client.get(f"{BASE}/{cid}/activity", headers=ctx.headers())
    assert activity.status_code == 200
    ctx.responses.append(activity)

    for response in ctx.responses:
        assert KEY not in response.text and ROTATED not in response.text, response.url
        assert "enc:v1" not in response.text
    for value in (KEY, ROTATED):
        assert await _plaintext_anywhere(ctx.database_url, value) == []
        assert value not in caplog.text
    for action in ("model_connection.models_synced", "model_connection.checked"):
        audits = await _audit(ctx.database_url, action)
        assert audits, action
        for metadata in audits:
            assert KEY not in json.dumps(metadata) and ROTATED not in json.dumps(metadata)
    checked = await _audit(ctx.database_url, "model_connection.checked")
    assert set(checked[0]) == {"mode", "model_id", "status", "reason", "config_revision", "host"}
    # Key 只出现在发给连接地址的 Authorization 请求头里
    for request in ctx.relay.requests:
        assert request.url.host == "relay.test"
        assert KEY not in str(request.url) and ROTATED not in str(request.url)
        assert KEY.encode() not in request.content and ROTATED.encode() not in request.content
        assert request.headers["authorization"] in (f"Bearer {KEY}", f"Bearer {ROTATED}")


# ---------------------------------------------------------------- 权限


@integration
async def test_owner_only_and_cross_workspace_is_not_found(ctx: Ctx) -> None:
    created = await ctx.create()
    cid = created["id"]
    password = "member password 123456"  # noqa: S105 - 测试密码
    for role in ("admin", "member"):
        user_id = await add_user(ctx.database_url, f"{role}@example.com", password)
        await add_member(ctx.database_url, uuid.UUID(ctx.workspace_id), user_id, role)
    async with (
        ctx.make_client(**SETTINGS) as admin,
        ctx.make_client(**SETTINGS) as member,
    ):
        for role, client in (("admin", admin), ("member", member)):
            stray = Recorder(lambda _: listing("x"))
            app_of(client).state.connectors.transport = httpx.MockTransport(stray)
            login = await client.post(
                "/api/v1/auth/login", json={"email": f"{role}@example.com", "password": password}
            )
            assert login.status_code == 200, login.text
            headers = {"X-Workspace-Id": ctx.workspace_id, **csrf(client)}
            for path, body in (
                (f"{BASE}/{cid}/models/sync", {"version": created["version"]}),
                (f"{BASE}/{cid}/checks", {"version": created["version"], "mode": "metadata"}),
            ):
                response = await client.post(path, json=body, headers=headers)
                assert response.status_code == 403, (role, path, response.text)
            assert stray.requests == []

    # 另一个工作空间的连接：用本空间的身份访问一律 404，不写任何东西
    second = await add_workspace(ctx.database_url, ctx.owner_id, "第二空间")
    foreign = await ctx.create(workspace=str(second), name="别的空间的连接")
    sync = await ctx.sync(foreign["id"], version=foreign["version"])
    assert sync.status_code == 404 and _error(sync) == "NOT_FOUND"
    check = await ctx.check(foreign["id"], "metadata", version=foreign["version"])
    assert check.status_code == 404 and _error(check) == "NOT_FOUND"
    assert ctx.relay.requests == []
    unchanged = await ctx.get(foreign["id"], workspace=str(second))
    assert unchanged["version"] == foreign["version"] and unchanged["last_check"] is None
    rows = await run_sql(ctx.database_url, "SELECT count(*) FROM workspace.model_connection_checks")
    assert rows == 0


# ---------------------------------------------------------------- 限流与在途锁


@integration
async def test_rate_limit_and_in_flight_lock(ctx: Ctx) -> None:
    created = await ctx.create()
    cid = created["id"]
    version = created["version"]
    redis = ctx.app.state.redis
    busy_key = f"ws:model-connection-busy:{cid}"

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(_: httpx.Request) -> None:
        entered.set()
        await release.wait()

    ctx.relay.before = hold
    first = asyncio.create_task(ctx.check(cid, "metadata", version=version, fresh=False))
    await asyncio.wait_for(entered.wait(), 10)
    assert 0 < await redis.ttl(busy_key) <= 90  # 锁带过期时间：进程异常退出也会释放
    busy_sync = await ctx.sync(cid, version=version, fresh=False)
    assert busy_sync.status_code == 409 and _error(busy_sync) == "CHECK_IN_PROGRESS"
    busy_check = await ctx.check(cid, "inference", "gpt-4o-mini", version=version, fresh=False)
    assert busy_check.status_code == 409 and _error(busy_check) == "CHECK_IN_PROGRESS"
    release.set()
    done = await asyncio.wait_for(first, 10)
    assert done.status_code == 200 and done.json()["status"] == "passed"
    assert await redis.exists(busy_key) == 0

    # 每个 owner 每个连接每分钟最多 5 次（同步与检测合计；被锁拒绝的不计）
    for _ in range(3):
        ok = await ctx.check(cid, "metadata", version=version, fresh=False)
        assert ok.status_code == 200, ok.text
    assert (await ctx.sync(cid, fresh=False)).status_code == 200
    version = (await ctx.get(cid))["version"]
    limited = await ctx.check(cid, "metadata", version=version, fresh=False)
    assert limited.status_code == 429 and _error(limited) == "RATE_LIMITED"
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    limited_sync = await ctx.sync(cid, version=version, fresh=False)
    assert limited_sync.status_code == 429
    assert len(ctx.relay.requests) == 5
    assert await redis.exists(busy_key) == 0

    # 另一个连接不受影响
    other = await ctx.create(name="中转站 B")
    assert (await ctx.check(other["id"], "metadata", fresh=False)).status_code == 200
