"""中转站管理：服务商协议适配——列模型与连接检测（T-0202，MC-2）。

约定见 docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md
第 1 节（ConnectionCheckOut）、第 4 节（模型同步与检测约束）、第 5 节（网络、日志与错误）。

- 所有请求经 app/integrations/http.py 的 ConnectorHttp：每次请求前检查目标地址（私网、链路本地、
  云元数据、混合公私 DNS 一律拒绝，除非主机在部署配置 INTEGRATION_ALLOWED_HOSTS 里），
  连接检查过的 IP（Host 与 TLS 的 SNI / 证书校验仍用原主机名），不跟随跳转。
  这里不改 TLS 校验（不提供“忽略证书”）。
- Key 只放在各协议规定的那一个请求头里，不进 URL（不用查询参数 key=）；不接受别的鉴权头。
  URL 由连接保存的协议基地址（已去掉尾斜杠）按协议拼接，不会拼出 /v1/v1 或双斜杠。
- 超时：连接 5 秒、读 15 秒；每次尝试整体（含读完响应）最多 20 秒。
- 列模型（GET）最多一次额外重试，只限 ConnectorHttp 判定为可安全重试的读错误（连接失败、读失败、
  502/503/504）；429 不重试。推理检测（POST）从不自动重试。
- 响应体最多读 2 MiB（超出立即停止读取）；只接受未压缩的响应（请求带 Accept-Encoding: identity），
  防止压缩炸弹。
- 列模型只发一次请求、每页取官方允许的最大条数（1000，与 MAX_LISTED_MODELS 相同）；
  服务商说还有下一页或超过 1000 个时，结果不完整（partial），不覆盖旧快照。
- 结果只归入契约的固定类别（status / reason），远端的响应正文与响应头只在内存里用来归类，
  不保存、不写日志、不进错误信息。
- 推理检测：固定的最短提示 ping、很小的输出上限，不用流式、不重试生成。

协议（请求格式都已在官方文档上核实；与 OpenClaw 2026.9.36 实际发出的请求一致，见
docs/decisions/2026-11-09-model-connections.md 第 2.1 节与
docs/verification/T-0202-native-protocols.md）：

- openai_compatible：GET {base}/models 返回 {"data": [{"id": ...}]}；POST {base}/chat/completions；
  鉴权 Authorization: Bearer <Key>。
  官方文档：https://platform.openai.com/docs/api-reference/models/list 、
  https://platform.openai.com/docs/api-reference/chat/create 。

- anthropic_native（Anthropic Messages API）：
  - 地址：基地址以 /v1 结尾时拼 {base}/models、{base}/messages，否则拼 {base}/v1/models、
    {base}/v1/messages（与 OpenClaw anthropic-transport-stream.ts 的
    resolveAnthropicMessagesUrl 相同）。
  - 鉴权：x-api-key: <Key>，另带 anthropic-version: 2023-06-01（OpenClaw 发的就是这个版本；
    官方版本说明里 2023-06-01 仍是有效版本，2026-10-21 只改了 Messages 的采样参数等，
    Models 接口两个版本行为相同）。官方 API 概览说明 x-api-key 仍受支持（“Legacy fallback for
    Authorization, still supported”）：https://platform.claude.com/docs/en/api/overview 、
    https://platform.claude.com/docs/en/api/versioning 。
  - 列模型：GET /v1/models?limit=1000（limit 默认 20、范围 1 ~ 1000；分页用 after_id / before_id），
    响应 {"data": [{"id", "type": "model", "display_name", ...}], "has_more",
    "first_id", "last_id"}：
    https://platform.claude.com/docs/en/api/models/list 。
  - 推理：POST /v1/messages，必填 model、max_tokens、messages；响应 {"type": "message",
    "role": "assistant", "content": [...], "stop_reason", ...}：
    https://platform.claude.com/docs/en/api/messages/create 。
  - 错误：{"type": "error", "error": {"type": "not_found_error", "message": ...}, "request_id"}；
    400 invalid_request_error、401 authentication_error、402 billing_error、
    403 permission_error、404 not_found_error、413 request_too_large、
    429 rate_limit_error（带 retry-after）、500 api_error、504 timeout_error、
    529 overloaded_error：https://platform.claude.com/docs/en/api/errors 。
    模型不存在与组织无权使用都返回 404 not_found_error，message 以 “model: <id>” 开头
    （claude-api 技能 shared/error-codes.md 的 404 一节）。

- gemini_native（Gemini API，generativelanguage.googleapis.com）：
  - 地址：基地址已含版本路径（例如 https://generativelanguage.googleapis.com/v1beta）。
    与 OpenClaw normalizeGoogleApiBaseUrl 相同：官方主机（HTTPS）路径为空时补 /v1beta，
    其他主机原样使用。
    列模型 GET {base}/models；推理 POST {base}/models/<id>:generateContent。
    模型路径与 OpenClaw resolveGoogleModelPath 相同：以 models/ 或 tunedModels/ 开头的原样使用，
    否则去掉 google/ 前缀后加 models/。列出的 name（models/xxx）去掉 models/ 前缀作为 model_id。
  - 鉴权：x-goog-api-key: <Key>（OpenClaw gemini-auth 与官方 @google/genai 2.23.0 SDK
    都用这个请求头）。
  - 列模型：GET v1beta/models，查询参数 pageSize（不填为 50，最多 1000）与 pageToken；
    响应 {"models": [{"name": "models/...", "supportedGenerationMethods": [...], ...}],
    "nextPageToken"}，
    nextPageToken 缺省表示没有下一页。只保留 supportedGenerationMethods 含 generateContent 的模型
    （没有这个字段的中转站条目照常保留）。
  - 推理：POST v1beta/{model=models/*}:generateContent，请求体 {"contents": [{"role":
    "user", "parts": [{"text": ...}]}], "generationConfig": {"maxOutputTokens": n}}；
    响应 {"candidates": [...], "promptFeedback", "usageMetadata", ...}。
  - 错误：google.rpc.Status 的 JSON 形式 {"error": {"code", "message", "status", "details"}}；
    HTTP 映射见 google/rpc/code.proto（400 INVALID_ARGUMENT、401 UNAUTHENTICATED、
    403 PERMISSION_DENIED、404 NOT_FOUND、429 RESOURCE_EXHAUSTED、503 UNAVAILABLE、
    504 DEADLINE_EXCEEDED）。
    Key 无效时返回 400 + ErrorInfo.reason = API_KEY_INVALID（google/api/error_reason.proto），
    归为 unauthorized；429 没有 Retry-After 时取 google.rpc.RetryInfo.retryDelay（Duration 的 JSON
    形式 "17s"，google/rpc/error_details.proto 与 google/protobuf/duration.proto）。
  - 官方文档：https://ai.google.dev/api/models 、https://ai.google.dev/api/generate-content 、
    https://ai.google.dev/gemini-api/docs/api-key 。本次核验时会话网络不允许访问 ai.google.dev，
    改用同一 API 的官方机器可读描述与源码核实：
    https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta （revision 20261109）、
    https://github.com/googleapis/googleapis 里的
    google/ai/generativelanguage/v1beta/model_service.proto、generative_service.proto，
    google/rpc/code.proto、status.proto、error_details.proto，google/api/error_reason.proto，
    以及镜像里的 @google/genai 2.23.0。
    TODO(T-0202)：会话网络放行 ai.google.dev 后对照上面三个页面复核一遍
    （字段名与 discovery 文档一致）。

状态与原因的归类（详见 _http_failure / _transport_failure，各协议的补充见各适配器的 _failure）：
- 2xx 且格式正确 → passed；2xx 但不是 JSON / 结构不对 → failed / invalid_response；
- 401 → failed / unauthorized；403 → failed / forbidden；
- 429 → rate_limited（reason 为空），retry_at 取 Retry-After 的秒数或 HTTP 日期（只接受 24 小时内
  的将来时间，否则为 null，不捏造时间）；
- 列模型的 404 / 405 → unsupported / models_endpoint_unsupported；
- 推理时服务商说明模型不存在（error.code 为 model_not_found，或 400 / 404 的错误信息说模型不存在；
  Anthropic 的 404 not_found_error “model: …”；Gemini 的 404 NOT_FOUND）
  → failed / model_not_available；
- 3xx（不跟随跳转）→ failed / invalid_response；
- 502 / 503 / 504（网关或上游暂时不可用）与 Anthropic 的 529（过载）→ failed / unreachable；
  其他 5xx 与其余 4xx → failed / invalid_response；
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
from urllib.parse import quote, urlsplit

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


def _rejects_max_tokens(body: Any, status_code: int) -> bool:
    """400 且错误说明是 max_tokens 不被这个模型支持（要用 max_completion_tokens）。"""
    if status_code != 400:
        return False
    error = _error_object(body)
    message = str(error.get("message") or "")
    return error.get("param") == "max_tokens" or "max_completion_tokens" in message


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
    model_missing: Callable[[Any, int], bool] = _says_model_missing,
) -> ProbeResult:
    """非 2xx 响应的归类。body 是解析过的 JSON（没有或不是 JSON 时为 None）。

    model_missing：协议自己的“模型不存在”判断（Anthropic、Gemini 的错误格式不同）。"""
    if status_code == 401:
        return ProbeResult("failed", "unauthorized", latency_ms)
    if status_code == 429:
        return ProbeResult(
            "rate_limited", None, latency_ms, parse_retry_after(headers.get("retry-after"), now)
        )
    if inference and status_code >= 400 and model_missing(body, status_code):
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


@dataclass(frozen=True)
class _Reply:
    """拿到了完整响应（状态码任意）：只留归类需要的东西，不外传。"""

    status_code: int
    headers: Mapping[str, str]
    body: Any
    latency_ms: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class _HttpAdapter:
    """各协议共用的请求、有界读取与归类；子类只管 URL、鉴权头、请求体与响应结构。"""

    protocol = ""

    def __init__(self, http: ConnectorHttp, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._http = http
        self._clock = clock

    # ---- 子类实现

    def _auth_headers(self, api_key: SecretStr) -> dict[str, str]:
        raise NotImplementedError

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing:
        raise NotImplementedError

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult:
        raise NotImplementedError

    # ---- 请求

    def _headers(self, api_key: SecretStr) -> dict[str, str]:
        return {**self._auth_headers(api_key), "Accept-Encoding": "identity"}

    async def _request(
        self,
        method: str,
        url: str,
        api_key: SecretStr,
        payload: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        attempts = 1 + (self._http.read_retries if method == "GET" else 0)
        # 重试之间有退避（每次不到 1 秒），整体上限按尝试次数放宽
        deadline = ATTEMPT_DEADLINE_SECONDS * attempts + RETRY_ALLOWANCE_SECONDS * (attempts - 1)
        async with asyncio.timeout(deadline):
            return await self._http.request(
                method, url, headers=self._headers(api_key), params=params, json=payload
            )

    async def _fetch_listing(
        self, url: str, api_key: SecretStr, params: Mapping[str, Any] | None
    ) -> ModelListing | _Reply:
        """列模型的 GET：拿不到完整的 2xx 响应时直接给出归类结果。"""
        started = time.monotonic()
        try:
            response = await self._request("GET", url, api_key, params=params)
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
        reply = _Reply(status_code, response.headers, _json_body(response), latency)
        if not reply.ok:
            return ModelListing(
                self._failure(status_code, reply.headers, reply.body, False, latency)
            )
        return reply

    def _listing(self, latency_ms: int, names: list[str] | None, *, more: bool) -> ModelListing:
        """names 为 None 表示响应结构不对。去重、排序、原样保留大小写；不合规的 ID 跳过。"""
        if names is None:
            return ModelListing(ProbeResult("failed", "invalid_response", latency_ms))
        ids = {name for name in names if _MODEL_ID.fullmatch(name)}
        passed = ProbeResult("passed", None, latency_ms)
        if more or len(ids) > MAX_LISTED_MODELS:
            # 还有下一页或超过 1000 个：只做一次请求，不完整，不覆盖旧快照
            return ModelListing(passed)
        return ModelListing(passed, tuple(sorted(ids)), complete=True)

    async def _post(self, url: str, api_key: SecretStr, payload: Any) -> ProbeResult | _Reply:
        """推理检测的 POST（不重试）：拿不到完整响应时直接给出归类结果。"""
        started = time.monotonic()
        try:
            response = await self._request("POST", url, api_key, payload)
        except ResponseTooLarge as error:
            latency = _elapsed_ms(started)
            if 200 <= error.status_code < 300:
                return ProbeResult("failed", "invalid_response", latency)
            return self._failure(error.status_code, {}, None, True, latency)
        except EncodedResponse:
            return ProbeResult("failed", "invalid_response", _elapsed_ms(started))
        except (ConnectorError, TimeoutError) as error:
            return _transport_failure(error, write=True)
        return _Reply(
            response.status_code, response.headers, _json_body(response), _elapsed_ms(started)
        )

    # ---- 归类

    def _model_missing(self, body: Any, status_code: int) -> bool:
        return _says_model_missing(body, status_code)

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
            model_missing=self._model_missing,
        )

    def _inference_result(self, reply: _Reply, passed: bool) -> ProbeResult:
        if not reply.ok:
            return self._failure(
                reply.status_code, reply.headers, reply.body, True, reply.latency_ms
            )
        if passed:
            return ProbeResult("passed", None, reply.latency_ms)
        # 有的中转站用 200 返回错误对象
        if self._model_missing(reply.body, 404):
            return ProbeResult("failed", "model_not_available", reply.latency_ms)
        return ProbeResult("failed", "invalid_response", reply.latency_ms)


class OpenAICompatibleAdapter(_HttpAdapter):
    """openai_compatible：GET {base}/models、POST {base}/chat/completions。

    鉴权只用 Authorization: Bearer <Key>。"""

    protocol = "openai_compatible"

    def _auth_headers(self, api_key: SecretStr) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key.get_secret_value()}"}

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing:
        reply = await self._fetch_listing(join_url(base_url, "models"), api_key, None)
        if isinstance(reply, ModelListing):
            return reply
        body = reply.body
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return self._listing(reply.latency_ms, None, more=False)
        names = [
            item["id"]
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        return self._listing(reply.latency_ms, names, more=body.get("has_more") is True)

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult:
        """推理检测。先用兼容面最广的 max_tokens；服务商以 400 明确拒绝这个参数（OpenAI 的推理模型
        要求 max_completion_tokens，https://platform.openai.com/docs/api-reference/chat/create）时，
        换成 max_completion_tokens 再发一次——第一次被拒绝时没有生成，这不是重试生成。"""
        result, rejected = await self._infer_once(
            base_url, api_key, model_id, {"max_tokens": PING_MAX_TOKENS}
        )
        if rejected:
            result, _ = await self._infer_once(
                base_url, api_key, model_id, {"max_completion_tokens": PING_MAX_TOKENS}
            )
        return result

    async def _infer_once(
        self, base_url: str, api_key: SecretStr, model_id: str, limit: dict[str, int]
    ) -> tuple[ProbeResult, bool]:
        """返回（结果, 是否因为输出上限参数被拒绝）。"""
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": PING_PROMPT}],
            **limit,
            "stream": False,
        }
        reply = await self._post(join_url(base_url, "chat/completions"), api_key, payload)
        if isinstance(reply, ProbeResult):
            return reply, False
        if not reply.ok:
            rejected = "max_tokens" in limit and _rejects_max_tokens(reply.body, reply.status_code)
            return self._inference_result(reply, False), rejected
        choices = reply.body.get("choices") if isinstance(reply.body, dict) else None
        passed = isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict)
        return self._inference_result(reply, passed), False


# Anthropic：OpenClaw 2026.9.36 发的 API 版本（anthropic-transport-stream.ts 第 299 行）
ANTHROPIC_VERSION = "2023-06-01"


def anthropic_url(base_url: str, path: str) -> str:
    """与 OpenClaw resolveAnthropicMessagesUrl 相同：基地址以 /v1 结尾时直接拼，否则补 /v1。"""
    normalized = base_url.rstrip("/")
    prefix = normalized if normalized.endswith("/v1") else f"{normalized}/v1"
    return f"{prefix}/{path.lstrip('/')}"


class AnthropicAdapter(_HttpAdapter):
    """anthropic_native：GET …/v1/models?limit=1000、POST …/v1/messages。

    鉴权只用 x-api-key: <Key> 与 anthropic-version（不发 Authorization）。"""

    protocol = "anthropic_native"

    def _auth_headers(self, api_key: SecretStr) -> dict[str, str]:
        return {"x-api-key": api_key.get_secret_value(), "anthropic-version": ANTHROPIC_VERSION}

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing:
        # limit 默认 20、最多 1000（https://platform.claude.com/docs/en/api/models/list）
        reply = await self._fetch_listing(
            anthropic_url(base_url, "models"), api_key, {"limit": MAX_LISTED_MODELS}
        )
        if isinstance(reply, ModelListing):
            return reply
        body = reply.body
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return self._listing(reply.latency_ms, None, more=False)
        names = [
            item["id"]
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        return self._listing(reply.latency_ms, names, more=body.get("has_more") is True)

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult:
        payload = {
            "model": model_id,
            "max_tokens": PING_MAX_TOKENS,
            "messages": [{"role": "user", "content": PING_PROMPT}],
            "stream": False,
        }
        reply = await self._post(anthropic_url(base_url, "messages"), api_key, payload)
        if isinstance(reply, ProbeResult):
            return reply
        body = reply.body
        passed = (
            isinstance(body, dict)
            and body.get("type") == "message"
            and isinstance(body.get("content"), list)
        )
        return self._inference_result(reply, passed)

    def _model_missing(self, body: Any, status_code: int) -> bool:
        # 模型不存在与无权使用都是 404 not_found_error，message 以 “model: <id>” 开头
        error = _error_object(body)
        message = error.get("message")
        if (
            status_code == 404
            and error.get("type") == "not_found_error"
            and isinstance(message, str)
            and message.lower().startswith("model:")
        ):
            return True
        return super()._model_missing(body, status_code)

    def _failure(
        self,
        status_code: int,
        headers: Mapping[str, str],
        body: Any,
        inference: bool,
        latency_ms: int,
    ) -> ProbeResult:
        if status_code == 529:  # overloaded_error：暂时过载
            return ProbeResult("failed", "unreachable", latency_ms)
        return super()._failure(status_code, headers, body, inference, latency_ms)


GEMINI_OFFICIAL_HOST = "generativelanguage.googleapis.com"
GEMINI_DEFAULT_VERSION_PATH = "/v1beta"
_GEMINI_MODEL_PREFIX = "models/"
_GEMINI_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
_GEMINI_ERROR_INFO = "type.googleapis.com/google.rpc.ErrorInfo"
# google.protobuf.Duration 的 JSON 形式：秒数加 s，可带最多 9 位小数
_DURATION = re.compile(r"([0-9]{1,10})(?:\.[0-9]{1,9})?s")


def gemini_base(base_url: str) -> str:
    """与 OpenClaw normalizeGoogleApiBaseUrl 相同：官方主机（HTTPS）路径为空时补 /v1beta。"""
    normalized = base_url.rstrip("/")
    parts = urlsplit(normalized)
    if (
        parts.scheme == "https"
        and (parts.hostname or "").lower() == GEMINI_OFFICIAL_HOST
        and not parts.path.rstrip("/")
    ):
        return f"{normalized}{GEMINI_DEFAULT_VERSION_PATH}"
    return normalized


def gemini_model_path(model_id: str) -> str:
    """与 OpenClaw resolveGoogleModelPath 相同；路径里的特殊字符转义，不能改变请求的路径或查询。"""
    if model_id.startswith(("models/", "tunedModels/")):
        path = model_id
    else:
        path = _GEMINI_MODEL_PREFIX + model_id.removeprefix("google/")
    return quote(path, safe="/")


def _gemini_details(body: Any) -> list[Mapping[str, Any]]:
    details = _error_object(body).get("details")
    if not isinstance(details, list):
        return []
    return [d for d in details if isinstance(d, dict)]


def _gemini_retry_delay(body: Any, now: datetime) -> datetime | None:
    """google.rpc.RetryInfo.retryDelay → 可重试时间（与 Retry-After 一样只接受 24 小时内）。"""
    for detail in _gemini_details(body):
        if detail.get("@type") != _GEMINI_RETRY_INFO:
            continue
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and (match := _DURATION.fullmatch(delay.strip())):
            seconds = int(match.group(1)) + (1 if "." in delay else 0)  # 小数部分向上取整
            wait = timedelta(seconds=seconds)
            return now + wait if wait <= MAX_RETRY_AFTER else None
    return None


class GeminiAdapter(_HttpAdapter):
    """gemini_native：GET {base}/models?pageSize=1000、POST {base}/models/<id>:generateContent。

    鉴权只用 x-goog-api-key: <Key>（不用查询参数 key=，Key 不进 URL）。"""

    protocol = "gemini_native"

    def _auth_headers(self, api_key: SecretStr) -> dict[str, str]:
        return {"x-goog-api-key": api_key.get_secret_value()}

    async def list_models(self, *, base_url: str, api_key: SecretStr) -> ModelListing:
        # pageSize 不填为 50、最多 1000；nextPageToken 缺省表示没有下一页
        reply = await self._fetch_listing(
            f"{gemini_base(base_url)}/models", api_key, {"pageSize": MAX_LISTED_MODELS}
        )
        if isinstance(reply, ModelListing):
            return reply
        body = reply.body
        if not isinstance(body, dict):
            return self._listing(reply.latency_ms, None, more=False)
        # proto3 的 JSON 省略空的重复字段：一个模型都没有时是 {}
        models = body.get("models", [] if not body else None)
        if not isinstance(models, list):
            return self._listing(reply.latency_ms, None, more=False)
        names: list[str] = []
        for item in models:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            methods = item.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue  # 例如只支持 embedContent 的向量模型，不能用来对话
            names.append(item["name"].removeprefix(_GEMINI_MODEL_PREFIX))
        token = body.get("nextPageToken")
        return self._listing(reply.latency_ms, names, more=isinstance(token, str) and bool(token))

    async def infer(self, *, base_url: str, api_key: SecretStr, model_id: str) -> ProbeResult:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": PING_PROMPT}]}],
            "generationConfig": {"maxOutputTokens": PING_MAX_TOKENS},
        }
        url = f"{gemini_base(base_url)}/{gemini_model_path(model_id)}:generateContent"
        reply = await self._post(url, api_key, payload)
        if isinstance(reply, ProbeResult):
            return reply
        body = reply.body
        # 有候选回复，或提示被内容过滤拦下（promptFeedback）：Key 与模型都可用
        passed = isinstance(body, dict) and (
            (isinstance(body.get("candidates"), list) and bool(body["candidates"]))
            or isinstance(body.get("promptFeedback"), dict)
        )
        return self._inference_result(reply, passed)

    def _model_missing(self, body: Any, status_code: int) -> bool:
        # 地址里的模型资源不存在：404 NOT_FOUND
        if status_code == 404 and _error_object(body).get("status") == "NOT_FOUND":
            return True
        return super()._model_missing(body, status_code)

    def _failure(
        self,
        status_code: int,
        headers: Mapping[str, str],
        body: Any,
        inference: bool,
        latency_ms: int,
    ) -> ProbeResult:
        if status_code == 400 and any(
            d.get("@type") == _GEMINI_ERROR_INFO and d.get("reason") == "API_KEY_INVALID"
            for d in _gemini_details(body)
        ):
            return ProbeResult("failed", "unauthorized", latency_ms)
        if status_code == 429:
            now = self._clock()
            retry_at = parse_retry_after(headers.get("retry-after"), now)
            if retry_at is None:
                retry_at = _gemini_retry_delay(body, now)
            return ProbeResult("rate_limited", None, latency_ms, retry_at)
        return super()._failure(status_code, headers, body, inference, latency_ms)


_ADAPTERS: dict[str, type[_HttpAdapter]] = {
    adapter.protocol: adapter
    for adapter in (OpenAICompatibleAdapter, AnthropicAdapter, GeminiAdapter)
}


def adapter_for(protocol: str, http: ConnectorHttp) -> ProviderAdapter:
    """按协议取适配器。协议是否开放由调用方（ModelCatalogService）先检查。"""
    adapter = _ADAPTERS.get(protocol)
    if adapter is None:
        raise ValueError(f"unknown model protocol: {protocol}")
    result: ProviderAdapter = adapter(http)
    return result
