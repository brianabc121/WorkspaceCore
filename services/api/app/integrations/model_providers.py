"""中转站管理：服务商协议适配——列模型与连接检测（T-0202，MC-2）。

约定见 docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md
第 1 节（ConnectionCheckOut）、第 4 节（模型同步与检测约束）、第 5 节（网络、日志与错误）。

- 所有请求经 app/integrations/http.py 的 ConnectorHttp：每次请求前检查目标地址（私网、链路本地、
  云元数据、混合公私 DNS 一律拒绝，除非主机在部署配置 INTEGRATION_ALLOWED_HOSTS 里），
  连接检查过的 IP（Host 与 TLS 的 SNI / 证书校验仍用原主机名），不跟随跳转。
  这里不改 TLS 校验（不提供“忽略证书”）。
- Key 只放在 Authorization: Bearer 请求头里，不进 URL；不接受别的鉴权头。URL 由连接保存的协议基地址
  （已去掉尾斜杠）拼上 /models、/chat/completions，不会拼出 /v1/v1 或双斜杠。
- 超时：连接 5 秒、读 15 秒；每次尝试整体（含读完响应）最多 20 秒。
- 列模型（GET）最多一次额外重试，只限 ConnectorHttp 判定为可安全重试的读错误（连接失败、读失败、
  502/503/504）；429 不重试。推理检测（POST）从不自动重试。
- 响应体最多读 2 MiB（超出立即停止读取）；只接受未压缩的响应（请求带 Accept-Encoding: identity），
  防止压缩炸弹。
- 结果只归入契约的固定类别（status / reason），远端的响应正文与响应头只在内存里用来归类，
  不保存、不写日志、不进错误信息。

协议：
- openai_compatible：GET {base}/models 返回 {"data": [{"id": ...}]}；POST {base}/chat/completions
  （OpenAI API 参考 https://platform.openai.com/docs/api-reference/models/list 与
  https://platform.openai.com/docs/api-reference/chat/create ；MC-0 的核验记录见
  docs/decisions/2026-11-09-model-connections.md）。
- anthropic_native / gemini_native：还没有核验（MC-0），这里只是桩：不发任何请求，
  一律返回 unsupported / protocol_unsupported。核验后由对应任务补上。

状态与原因的归类（详见 _http_failure / _transport_failure）：
- 2xx 且格式正确 → passed；2xx 但不是 JSON / 结构不对 → failed / invalid_response；
- 401 → failed / unauthorized；403 → failed / forbidden；
- 429 → rate_limited（reason 为空），retry_at 取 Retry-After 的秒数或 HTTP 日期（只接受 24 小时内
  的将来时间，否则为 null，不捏造时间）；
- 列模型的 404 / 405 → unsupported / models_endpoint_unsupported；
- 推理时服务商说明模型不存在（error.code 为 model_not_found，或 400 / 404 的错误信息说模型不存在）
  → failed / model_not_available；
- 3xx（不跟随跳转）→ failed / invalid_response；
- 502 / 503 / 504（网关或上游暂时不可用）→ failed / unreachable；其他 5xx 与其余 4xx
  → failed / invalid_response；
- 连接失败、DNS 失败 → failed / unreachable；TLS 握手或证书错误 → failed / tls_error；
  超时 → failed / timeout；推理请求已发出后超时或断开 → unknown / timeout（或 unreachable），
  因为服务商可能已经处理了这次请求；
- 出站策略拒绝 → failed / destination_not_allowed（请求没有发出）。
"""

import asyncio
import email.utils
import json
import re
import ssl
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker, ConnectorHttp, OutboundPolicy
from app.schemas.model_connections import CheckReason, CheckStatus

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
# 每次尝试整体的上限（连接 + 读完响应）：慢慢滴数据的服务也不能拖过这个时间
ATTEMPT_DEADLINE_SECONDS = CONNECT_TIMEOUT_SECONDS + READ_TIMEOUT_SECONDS
# 列模型重试前的退避时间余量（ConnectorHttp 的退避不到 1 秒）
RETRY_ALLOWANCE_SECONDS = 1.0
# 列模型最多一次额外重试（只限 ConnectorHttp 判定安全的读错误）；POST 不会被 ConnectorHttp 重试
METADATA_RETRIES = 1
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_LISTED_MODELS = 1000
# 推理检测：固定的最短提示与很小的输出上限，不使用用户的聊天记录
PING_PROMPT = "ping"
PING_MAX_TOKENS = 16
# Retry-After 超过这个时间视为不合理，按“没有合法值”处理
MAX_RETRY_AFTER = timedelta(days=1)

# 与 schemas.model_connections.ModelId 相同的约束：服务商列出的不合规 ID 不能配置，跳过
_MODEL_ID = re.compile(r"[^\s\x00-\x1f\x7f]{1,128}")
_RETRY_SECONDS = re.compile(r"[0-9]{1,10}")
_GATEWAY_STATUSES = frozenset({502, 503, 504})
_MISSING_MODEL_CODES = frozenset({"model_not_found", "model_not_available", "invalid_model"})
_MISSING_MODEL_PHRASES = (
    "does not exist",
    "not exist",
    "not found",
    "not available",
    "no such model",
    "unknown model",
    "invalid model",
    "不存在",
    "无可用渠道",
)


@dataclass(frozen=True)
class ProbeResult:
    """一次请求的归类结果：只有固定类别，不含远端原文。"""

    status: CheckStatus
    reason: CheckReason | None = None
    latency_ms: int | None = None
    retry_at: datetime | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True)
class ModelListing:
    """列模型的结果。complete 为真时 model_ids 是完整、去重、排好序的列表；否则为空。"""

    result: ProbeResult
    model_ids: tuple[str, ...] = ()
    complete: bool = False


class ProviderAdapter(Protocol):
    """协议适配的统一接口。Key 以 SecretStr 传入，只在拼请求头时取出明文。"""

    protocol: str

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing: ...

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult: ...


# ---------------------------------------------------------------- 有界响应


class ResponseTooLarge(Exception):
    """响应体超过上限：已停止读取。只带状态码与内容类型，不带正文。"""

    def __init__(self, status_code: int, content_type: str) -> None:
        super().__init__("response too large")
        self.status_code = status_code
        self.content_type = content_type


class EncodedResponse(Exception):
    """服务商无视 Accept-Encoding: identity 返回了压缩内容：不解压（防压缩炸弹）。"""


class _CappedStream(httpx.AsyncByteStream):
    def __init__(
        self, inner: httpx.AsyncByteStream, limit: int, status_code: int, content_type: str
    ) -> None:
        self._inner = inner
        self._limit = limit
        self._status_code = status_code
        self._content_type = content_type

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self._inner:
            size += len(chunk)
            if size > self._limit:
                raise ResponseTooLarge(self._status_code, self._content_type)
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class CappedTransport(httpx.AsyncBaseTransport):
    """包在真正的传输层外面：响应体超过上限就停止读取；拒绝压缩过的响应。

    inner 为空时用 httpx 默认的 AsyncHTTPTransport（verify=True：证书与主机名照常校验）；
    测试里传入 httpx.MockTransport。"""

    def __init__(
        self, inner: httpx.AsyncBaseTransport | None, limit: int = MAX_RESPONSE_BYTES
    ) -> None:
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport()
        self.limit = limit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        stream = response.stream
        if not isinstance(stream, httpx.AsyncByteStream):  # pragma: no cover - httpx 保证
            raise TypeError("expected an async byte stream")
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if encoding not in ("", "identity"):
            await stream.aclose()
            raise EncodedResponse
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_CappedStream(
                stream,
                self.limit,
                response.status_code,
                response.headers.get("content-type", ""),
            ),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def provider_http(settings: Settings, transport: httpx.AsyncBaseTransport | None) -> ConnectorHttp:
    """模型服务商用的出站客户端：沿用部署的出站策略，超时与重试按契约第 4 节。

    熔断器每次新建：检测与同步是管理员手动触发、已有限流，每次都真正去连服务商。"""
    return ConnectorHttp(
        policy=OutboundPolicy(settings.allowed_integration_hosts),
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        read_retries=METADATA_RETRIES,
        breaker=CircuitBreaker(),
        transport=CappedTransport(transport),
    )


# ---------------------------------------------------------------- 归类


def _utcnow() -> datetime:
    return datetime.now(UTC)


def join_url(base_url: str, path: str) -> str:
    """协议基地址 + 相对路径：只加一个斜杠，不重复 /v1。"""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def parse_retry_after(value: str | None, now: datetime) -> datetime | None:
    """Retry-After 的秒数或 HTTP 日期（RFC 9110 第 10.2.3 节）→ 可重试时间。

    只接受不晚于 24 小时之后的合法值；日期已经过去、格式不对或太远都返回 None。"""
    if value is None:
        return None
    text = value.strip()
    if _RETRY_SECONDS.fullmatch(text):
        delay = timedelta(seconds=int(text))
        return now + delay if delay <= MAX_RETRY_AFTER else None
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:  # HTTP 日期总是 GMT
        when = when.replace(tzinfo=UTC)
    if when <= now or when - now > MAX_RETRY_AFTER:
        return None
    return when.astimezone(UTC)


def _json_body(response: httpx.Response) -> Any:
    try:
        return json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        return None


def _error_object(body: Any) -> Mapping[str, Any]:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return error
    return {}


def _says_model_missing(body: Any, status_code: int) -> bool:
    """服务商是否说明“这个模型不存在 / 不可用”。只在内存里看，不保存原文。"""
    error = _error_object(body)
    code = error.get("code")
    kind = error.get("type")
    if (isinstance(code, str) and code.lower() in _MISSING_MODEL_CODES) or (
        isinstance(kind, str) and kind.lower() in _MISSING_MODEL_CODES
    ):
        return True
    if status_code not in (400, 404):
        return False
    message = error.get("message")
    if not isinstance(message, str):
        return False
    lowered = message.lower()
    return ("model" in lowered or "模型" in lowered) and any(
        phrase in lowered for phrase in _MISSING_MODEL_PHRASES
    )


def _causes(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _transport_failure(error: ConnectorError | TimeoutError, *, write: bool) -> ProbeResult:
    """请求没有拿到响应时的归类。write：推理请求（发出后结果未知，状态为 unknown）。"""
    if isinstance(error, TimeoutError):
        # 整次尝试超过总时限：GET 算失败；POST 可能已被服务商处理
        return ProbeResult("unknown" if write else "failed", "timeout")
    if error.reason == "destination_not_allowed":
        return ProbeResult("failed", "destination_not_allowed")
    causes = list(_causes(error))
    if any(isinstance(c, ssl.SSLError) for c in causes):
        reason: CheckReason = "tls_error"
    elif any(isinstance(c, httpx.TimeoutException | TimeoutError) for c in causes):
        reason = "timeout"
    else:
        reason = "unreachable"
    unknown = write and error.kind == "TIMEOUT_UNKNOWN" and not error.not_sent
    return ProbeResult("unknown" if unknown else "failed", reason)


def _http_failure(
    status_code: int,
    *,
    headers: Mapping[str, str],
    body: Any,
    inference: bool,
    latency_ms: int,
    now: datetime,
) -> ProbeResult:
    """非 2xx 响应的归类。body 是解析过的 JSON（没有或不是 JSON 时为 None）。"""
    if status_code == 401:
        return ProbeResult("failed", "unauthorized", latency_ms)
    if status_code == 429:
        return ProbeResult(
            "rate_limited", None, latency_ms, parse_retry_after(headers.get("retry-after"), now)
        )
    if inference and status_code >= 400 and _says_model_missing(body, status_code):
        return ProbeResult("failed", "model_not_available", latency_ms)
    if status_code == 403:
        return ProbeResult("failed", "forbidden", latency_ms)
    if status_code in _GATEWAY_STATUSES:
        return ProbeResult("failed", "unreachable", latency_ms)
    # 3xx（不跟随跳转，Key 不会被带去别处）、其他 4xx、其他 5xx
    return ProbeResult("failed", "invalid_response", latency_ms)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


# ---------------------------------------------------------------- 协议适配


class OpenAICompatibleAdapter:
    """openai_compatible：GET {base}/models、POST {base}/chat/completions。

    鉴权只用 Authorization: Bearer <Key>。"""

    protocol = "openai_compatible"

    def __init__(self, http: ConnectorHttp, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._http = http
        self._clock = clock

    def _headers(self, api_key: SecretStr) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Accept-Encoding": "identity",
        }

    async def _request(
        self, method: str, url: str, api_key: SecretStr, payload: Any = None
    ) -> httpx.Response:
        attempts = 1 + (self._http.read_retries if method == "GET" else 0)
        # 重试之间有退避（每次不到 1 秒），整体上限按尝试次数放宽
        deadline = ATTEMPT_DEADLINE_SECONDS * attempts + RETRY_ALLOWANCE_SECONDS * (attempts - 1)
        async with asyncio.timeout(deadline):
            return await self._http.request(
                method, url, headers=self._headers(api_key), json=payload
            )

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing:
        started = time.monotonic()
        try:
            response = await self._request("GET", join_url(base_url, "models"), api_key)
        except ResponseTooLarge as error:
            latency = _elapsed_ms(started)
            if 200 <= error.status_code < 300:
                if "json" in error.content_type.lower():
                    # 认证与接口都可用，只是列表超过 2 MiB：不完整，保留旧快照
                    return ModelListing(ProbeResult("passed", None, latency))
                return ModelListing(ProbeResult("failed", "invalid_response", latency))
            return ModelListing(self._failure(error.status_code, {}, None, False, latency))
        except EncodedResponse:
            return ModelListing(ProbeResult("failed", "invalid_response", _elapsed_ms(started)))
        except (ConnectorError, TimeoutError) as error:
            return ModelListing(_transport_failure(error, write=False))
        latency = _elapsed_ms(started)
        status_code = response.status_code
        if status_code in (404, 405):
            return ModelListing(ProbeResult("unsupported", "models_endpoint_unsupported", latency))
        body = _json_body(response)
        if not 200 <= status_code < 300:
            return ModelListing(self._failure(status_code, response.headers, body, False, latency))
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return ModelListing(ProbeResult("failed", "invalid_response", latency))
        ids = {
            item["id"]
            for item in data
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and _MODEL_ID.fullmatch(item["id"])
        }
        passed = ProbeResult("passed", None, latency)
        if body.get("has_more") is True or len(ids) > MAX_LISTED_MODELS:
            # 还有下一页或超过 1000 个：只做一次请求，不完整，不覆盖旧快照
            return ModelListing(passed)
        return ModelListing(passed, tuple(sorted(ids)), complete=True)

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": PING_PROMPT}],
            "max_tokens": PING_MAX_TOKENS,
            "stream": False,
        }
        started = time.monotonic()
        try:
            response = await self._request(
                "POST", join_url(base_url, "chat/completions"), api_key, payload
            )
        except ResponseTooLarge as error:
            latency = _elapsed_ms(started)
            if 200 <= error.status_code < 300:
                return ProbeResult("failed", "invalid_response", latency)
            return self._failure(error.status_code, {}, None, True, latency)
        except EncodedResponse:
            return ProbeResult("failed", "invalid_response", _elapsed_ms(started))
        except (ConnectorError, TimeoutError) as error:
            return _transport_failure(error, write=True)
        latency = _elapsed_ms(started)
        body = _json_body(response)
        if not 200 <= response.status_code < 300:
            return self._failure(response.status_code, response.headers, body, True, latency)
        choices = body.get("choices") if isinstance(body, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return ProbeResult("passed", None, latency)
        # 有的中转站用 200 返回错误对象
        if _says_model_missing(body, 404):
            return ProbeResult("failed", "model_not_available", latency)
        return ProbeResult("failed", "invalid_response", latency)

    def _failure(
        self,
        status_code: int,
        headers: Mapping[str, str],
        body: Any,
        inference: bool,
        latency_ms: int,
    ) -> ProbeResult:
        return _http_failure(
            status_code,
            headers=headers,
            body=body,
            inference=inference,
            latency_ms=latency_ms,
            now=self._clock(),
        )


class UnverifiedProtocolAdapter:
    """anthropic_native / gemini_native 的桩：还没有核验（MC-0），不发任何请求。

    TODO(MC-0)：核验锁定版本的官方文档与 OpenClaw 实测后，按真实契约实现列模型与推理检测。"""

    def __init__(self, protocol: str) -> None:
        self.protocol = protocol

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing:
        return ModelListing(ProbeResult("unsupported", "protocol_unsupported"))

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult:
        return ProbeResult("unsupported", "protocol_unsupported")


def adapter_for(protocol: str, http: ConnectorHttp) -> ProviderAdapter:
    """按协议取适配器。只有 openai_compatible 会真正访问服务商。"""
    if protocol == OpenAICompatibleAdapter.protocol:
        return OpenAICompatibleAdapter(http)
    return UnverifiedProtocolAdapter(protocol)
