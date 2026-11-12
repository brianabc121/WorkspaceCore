"""知识库图片的对象存储（T-0103，docs/v2/TIMELINE_PLAN.md 4.2；核验见
docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md 第 3 节）。

两种后端，接口相同（put / get / exists / delete）：
- `LocalStore`：本机目录（Compose 里是卷 kb-files），先写临时文件再改名，写一半不会被读到；
- `S3Store`：S3 兼容接口（Cloudflare R2、阿里云 OSS，CI 里用 Garage 验证），
  自己实现 SigV4，不引入 SDK。
  - 只用 PUT / GET / HEAD / DELETE 四种对象操作，整块上传（OSS 不支持 aws-chunked），
    正文的 SHA-256 写进 x-amz-content-sha256；
  - 路径风格（endpoint/bucket/key，R2 默认）或虚拟托管风格（bucket.endpoint/key，OSS 只支持这种）；
  - 每次请求有超时；网络错误、429、5xx 最多重试 3 次；403 / 400 不重试；
  - 对象 key 是内容的哈希，PUT 天然幂等：重试是安全的；最后仍不确定时用 HEAD 核对对象在不在。

错误按 Connector 的统一分类抛 `ConnectorError`，只带 S3 的错误码（如 SignatureDoesNotMatch），
不带响应原文；日志里不出现密钥。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings
from app.integrations.base import ConnectorError, ConnectorErrorKind

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,511}$")
RETRIES = 3
_BACKOFF = (0.2, 0.5, 1.0)


def valid_key(key: str) -> bool:
    return bool(_KEY.match(key)) and ".." not in key.split("/") and "//" not in key


class ObjectStore(Protocol):
    """一个存储位置。`name` 是 local / r2 / oss；`where` 用于运维页显示（不含密钥）。"""

    name: str
    where: str

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...


# ---------------------------------------------------------------- 本地


class LocalStore:
    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.where = str(self.root)

    def _path(self, key: str) -> Path:
        if not valid_key(key):
            raise ConnectorError("VALIDATION_FAILED", "对象 key 不合法", reason="invalid_key")
        return self.root / key

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _get(self, key: str) -> bytes | None:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(self._put, key, data)
        except OSError as error:
            raise ConnectorError("UNAVAILABLE", "本地存储写入失败", reason="local_io") from error

    async def get(self, key: str) -> bytes | None:
        try:
            return await asyncio.to_thread(self._get, key)
        except OSError as error:
            raise ConnectorError("UNAVAILABLE", "本地存储读取失败", reason="local_io") from error

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._path(key).unlink, True)
        except OSError as error:
            raise ConnectorError("UNAVAILABLE", "本地存储删除失败", reason="local_io") from error


# ---------------------------------------------------------------- SigV4


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def uri_encode(value: str, *, keep_slash: bool) -> str:
    # SigV4：除 A-Z a-z 0-9 - _ . ~ 之外都按 %XX（大写）编码；S3 的路径只编码一次
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    key = _hmac(f"AWS4{secret}".encode(), date)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def canonical_request(
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    payload_hash: str,
) -> tuple[str, str]:
    """返回（规范请求, SignedHeaders）。path 是已经编码好的路径。"""
    names = sorted(name.lower() for name in headers)
    values = {name.lower(): " ".join(str(value).split()) for name, value in headers.items()}
    canonical_headers = "".join(f"{name}:{values[name]}\n" for name in names)
    signed = ";".join(names)
    canonical_query = "&".join(
        f"{uri_encode(k, keep_slash=False)}={uri_encode(v, keep_slash=False)}"
        for k, v in sorted(query.items())
    )
    request = "\n".join(
        [method, path or "/", canonical_query, canonical_headers, signed, payload_hash]
    )
    return request, signed


def authorization(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    amz_date: str,
    query: Mapping[str, str] | None = None,
) -> str:
    """Authorization 头（AWS Signature Version 4）。headers 必须包含 host 与 x-amz-date。"""
    date = amz_date[:8]
    request, signed = canonical_request(method, path, query or {}, headers, payload_hash)
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(request.encode()).hexdigest()]
    )
    signature = hmac.new(
        signing_key(secret_key, date, region, service), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )


# ---------------------------------------------------------------- S3 兼容


@dataclass(frozen=True)
class S3Config:
    name: str  # r2 / oss
    endpoint: str  # https://host[:port]，不带路径
    region: str
    bucket: str
    access_key: str
    secret_key: str
    virtual_hosted: bool


_ERROR_CODE = re.compile(r"<Code>([A-Za-z0-9.]{1,64})</Code>")


def _error_code(body: bytes) -> str | None:
    found = _ERROR_CODE.search(body[:4096].decode("utf-8", "replace"))
    return found.group(1) if found else None


class S3Store:
    def __init__(
        self,
        config: S3Config,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.name = config.name
        parts = urlsplit(config.endpoint)
        self._scheme = parts.scheme
        self._netloc = parts.netloc
        host = f"{config.bucket}.{parts.netloc}" if config.virtual_hosted else parts.netloc
        self._host = host
        self.where = f"{host}/{'' if config.virtual_hosted else config.bucket}".rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 5.0))
        self._transport = transport
        self._sleep = sleep

    def _path(self, key: str) -> str:
        if not valid_key(key):
            raise ConnectorError("VALIDATION_FAILED", "对象 key 不合法", reason="invalid_key")
        raw = f"/{key}" if self.config.virtual_hosted else f"/{self.config.bucket}/{key}"
        return uri_encode(raw, keep_slash=True)

    def _signed(
        self, method: str, path: str, payload_hash: str, extra: Mapping[str, str]
    ) -> dict[str, str]:
        amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        headers = {
            "host": self._host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            **{k.lower(): v for k, v in extra.items()},
        }
        headers["authorization"] = authorization(
            method=method,
            path=path,
            headers=headers,
            payload_hash=payload_hash,
            access_key=self.config.access_key,
            secret_key=self.config.secret_key,
            region=self.config.region,
            service="s3",
            amz_date=amz_date,
        )
        return headers

    async def _send(
        self,
        method: str,
        key: str,
        *,
        data: bytes | None = None,
        extra: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """发送一次请求（带重试）。返回 2xx / 404 的响应；其他情况抛 ConnectorError。"""
        path = self._path(key)
        payload = data or b""
        payload_hash = hashlib.sha256(payload).hexdigest() if data is not None else EMPTY_SHA256
        url = f"{self._scheme}://{self._host}{path}"
        last: ConnectorError | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        ) as client:
            for attempt in range(RETRIES + 1):
                if attempt:
                    await self._sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
                headers = self._signed(method, path, payload_hash, extra or {})
                try:
                    response = await client.request(
                        method, url, headers=headers, content=payload if data is not None else None
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
                    last = ConnectorError(
                        "UNAVAILABLE", "对象存储连不上", not_sent=True, reason="connect"
                    )
                    last.__cause__ = error
                    continue
                except httpx.TimeoutException as error:
                    last = ConnectorError("TIMEOUT_UNKNOWN", "对象存储超时", reason="timeout")
                    last.__cause__ = error
                    continue
                except httpx.HTTPError as error:
                    last = ConnectorError("TIMEOUT_UNKNOWN", "对象存储连接中断", reason="network")
                    last.__cause__ = error
                    continue
                if response.status_code < 300 or response.status_code == 404:
                    return response
                code = _error_code(response.content)
                if response.status_code == 429 or response.status_code >= 500:
                    kind: ConnectorErrorKind = (
                        "RATE_LIMITED" if response.status_code in (429, 503) else "UNAVAILABLE"
                    )
                    last = ConnectorError(
                        kind, "对象存储暂时不可用", status=response.status_code, reason=code
                    )
                    continue
                if response.status_code in (401, 403):
                    raise ConnectorError(
                        "AUTH_FAILED",
                        "对象存储拒绝了凭证或签名（检查密钥、region 与桶）",
                        status=response.status_code,
                        reason=code,
                    )
                raise ConnectorError(
                    "VALIDATION_FAILED",
                    "对象存储拒绝了请求（检查 endpoint、region、桶名与寻址方式）",
                    status=response.status_code,
                    reason=code,
                )
        if last is None:  # pragma: no cover - 循环至少执行一次
            raise ConnectorError("UNAVAILABLE", "对象存储不可用")
        raise last

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await self._send("PUT", key, data=data, extra={"content-type": content_type})
        except ConnectorError as error:
            # 结果不确定（超时、连接中断）：对象 key 是内容哈希，HEAD 看到对象就说明已经写成功
            if error.kind == "TIMEOUT_UNKNOWN" and await self.exists(key):
                return
            raise

    async def get(self, key: str) -> bytes | None:
        response = await self._send("GET", key)
        return None if response.status_code == 404 else response.content

    async def exists(self, key: str) -> bool:
        response = await self._send("HEAD", key)
        return response.status_code != 404

    async def delete(self, key: str) -> None:
        await self._send("DELETE", key)


# ---------------------------------------------------------------- 按配置建


class StorageNotConfigured(Exception):
    def __init__(self, name: str, missing: list[str]) -> None:
        super().__init__(f"{name} 没有配置完整，缺少 {', '.join(missing)}")
        self.name = name
        self.missing = missing


def r2_endpoint(account_id: str, jurisdiction: str = "") -> str:
    middle = f".{jurisdiction}" if jurisdiction else ""
    return f"https://{account_id}{middle}.r2.cloudflarestorage.com"


def oss_endpoint(region: str) -> str:
    return f"https://s3.oss-{region}.aliyuncs.com"


@dataclass(frozen=True)
class R2Account:
    """一个工作空间的 Cloudflare R2 连接（密钥已解密）。"""

    account_id: str = ""
    bucket: str = ""
    jurisdiction: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""


@dataclass(frozen=True)
class OssAccount:
    """一个工作空间的阿里云 OSS 连接（密钥已解密）。"""

    region: str = ""
    bucket: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""


@dataclass(frozen=True)
class StorageConfig:
    """一个工作空间的图片存储配置（T-0223，网页“设置 → 图片存储”）。没配置过就是本机、没有副本。"""

    primary: str = "local"
    replica: str = "none"
    r2: R2Account = R2Account()
    oss: OssAccount = OssAccount()


def build_store(
    kind: str,
    config: StorageConfig,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ObjectStore:
    """按名字（local / r2 / oss）建存储。

    连接信息不完整时抛 StorageNotConfigured（missing 是“设置 → 图片存储”页面上的字段名）。"""
    timeout = settings.file_storage_timeout_seconds
    if kind == "local":
        return LocalStore(settings.file_dir)
    if kind == "r2":
        r2 = config.r2
        values = {
            "账户 ID": r2.account_id or settings.r2_endpoint,
            "桶名": r2.bucket,
            "Access Key ID": r2.access_key_id,
            "Secret Access Key": r2.secret_access_key,
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise StorageNotConfigured("r2", missing)
        s3 = S3Config(
            name="r2",
            endpoint=settings.r2_endpoint or r2_endpoint(r2.account_id, r2.jurisdiction),
            region=settings.r2_region or "auto",
            bucket=r2.bucket,
            access_key=r2.access_key_id,
            secret_key=r2.secret_access_key,
            virtual_hosted=False,
        )
        return S3Store(s3, timeout=timeout, transport=transport)
    if kind == "oss":
        oss = config.oss
        values = {
            "地域": oss.region,
            "桶名": oss.bucket,
            "AccessKey ID": oss.access_key_id,
            "AccessKey Secret": oss.access_key_secret,
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise StorageNotConfigured("oss", missing)
        s3 = S3Config(
            name="oss",
            endpoint=settings.oss_endpoint or oss_endpoint(oss.region),
            region=oss.region,
            bucket=oss.bucket,
            access_key=oss.access_key_id,
            secret_key=oss.access_key_secret,
            virtual_hosted=True,
        )
        return S3Store(s3, timeout=timeout, transport=transport)
    raise ValueError(f"unknown storage {kind}")


async def self_check(store: ObjectStore) -> None:
    """写一个探测对象、读回、删除；任何一步失败抛 ConnectorError（部署自检与运维页用）。"""
    key = f"kb/_probe/{uuid.uuid4().hex}"
    data = b"myworkspace storage probe"
    await store.put(key, data, "application/octet-stream")
    try:
        back = await store.get(key)
        if back != data:
            raise ConnectorError("UNAVAILABLE", "探测对象读回的内容不一致", reason="probe_mismatch")
    finally:
        await store.delete(key)
