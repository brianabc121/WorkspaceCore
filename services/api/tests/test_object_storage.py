"""知识库图片的对象存储（T-0103）：SigV4（AWS 官方测试向量）、S3 兼容客户端、本地存储。"""

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.base import ConnectorError
from app.integrations.object_storage import (
    LocalStore,
    S3Config,
    S3Store,
    StorageNotConfigured,
    authorization,
    build_store,
    self_check,
    uri_encode,
)

# ---------------------------------------------------------------- SigV4：AWS 官方测试向量

EMPTY = hashlib.sha256(b"").hexdigest()


def test_sigv4_test_suite_get_vanilla() -> None:
    # AWS Signature Version 4 测试套件 get-vanilla
    header = authorization(
        method="GET",
        path="/",
        headers={"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"},
        payload_hash=EMPTY,
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",  # noqa: S106 - AWS 文档的示例值
        region="us-east-1",
        service="service",
        amz_date="20150830T123600Z",
    )
    assert header == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )


def test_sigv4_s3_get_object_virtual_hosted() -> None:
    # S3 文档“Signature Calculations for the Authorization Header”的 GET Object 示例（虚拟托管风格）
    header = authorization(
        method="GET",
        path="/test.txt",
        headers={
            "Host": "examplebucket.s3.amazonaws.com",
            "Range": "bytes=0-9",
            "x-amz-content-sha256": EMPTY,
            "x-amz-date": "20130524T000000Z",
        },
        payload_hash=EMPTY,
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # noqa: S106 - AWS 文档的示例值
        region="us-east-1",
        service="s3",
        amz_date="20130524T000000Z",
    )
    assert header.endswith(
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_sigv4_s3_put_object_with_encoded_path() -> None:
    # 同一文档的 PUT Object 示例：路径里的 $ 编码为 %24，正文哈希写进 x-amz-content-sha256
    body = b"Welcome to Amazon S3."
    digest = hashlib.sha256(body).hexdigest()
    assert digest == "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"
    header = authorization(
        method="PUT",
        path=uri_encode("/test$file.text", keep_slash=True),
        headers={
            "Host": "examplebucket.s3.amazonaws.com",
            "Date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-date": "20130524T000000Z",
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
            "x-amz-content-sha256": digest,
        },
        payload_hash=digest,
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # noqa: S106 - AWS 文档的示例值
        region="us-east-1",
        service="s3",
        amz_date="20130524T000000Z",
    )
    assert header.endswith(
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"
    )


# ---------------------------------------------------------------- S3 兼容客户端

SECRET = "test-secret-key"  # noqa: S105 - 测试值


def _verify(request: httpx.Request, region: str) -> None:
    """模拟服务端：用请求里的头重新算签名，必须与 Authorization 一致。"""
    auth = request.headers["authorization"]
    signed = auth.split("SignedHeaders=")[1].split(",")[0].split(";")
    headers = {name: request.headers[name] for name in signed}
    expected = authorization(
        method=request.method,
        path=request.url.raw_path.decode(),
        headers=headers,
        payload_hash=request.headers["x-amz-content-sha256"],
        access_key="AK",
        secret_key=SECRET,
        region=region,
        service="s3",
        amz_date=request.headers["x-amz-date"],
    )
    assert auth == expected
    assert request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()


def _store(handler: Any, *, virtual: bool = False, region: str = "auto") -> S3Store:
    config = S3Config(
        name="oss" if virtual else "r2",
        endpoint="https://storage.example.com",
        region=region,
        bucket="kb-bucket",
        access_key="AK",
        secret_key=SECRET,
        virtual_hosted=virtual,
    )

    async def no_sleep(_: float) -> None:
        return None

    return S3Store(config, transport=httpx.MockTransport(handler), sleep=no_sleep)


async def test_path_style_put_get_head_delete() -> None:
    objects: dict[str, bytes] = {}
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _verify(request, "auto")
        seen.append((request.method, request.url.host, request.url.path))
        key = request.url.path
        if request.method == "PUT":
            assert request.headers["content-type"] == "image/png"
            assert "transfer-encoding" not in request.headers  # 整块上传
            objects[key] = request.content
            return httpx.Response(200)
        if key not in objects:
            return httpx.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>")
        if request.method == "DELETE":
            objects.pop(key)
            return httpx.Response(204)
        return httpx.Response(200, content=objects[key] if request.method == "GET" else b"")

    store = _store(handler)
    await store.put("kb/ws-1/abc", b"png-bytes", "image/png")
    assert await store.get("kb/ws-1/abc") == b"png-bytes"
    assert await store.exists("kb/ws-1/abc") is True
    await store.delete("kb/ws-1/abc")
    assert await store.get("kb/ws-1/abc") is None
    assert await store.exists("kb/ws-1/abc") is False
    await store.delete("kb/ws-1/abc")  # 删除不存在的对象不报错
    assert seen[0] == ("PUT", "storage.example.com", "/kb-bucket/kb/ws-1/abc")
    assert store.where == "storage.example.com/kb-bucket"


async def test_virtual_hosted_puts_the_bucket_in_the_host() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _verify(request, "cn-hangzhou")
        seen.append((request.url.host, request.url.path))
        return httpx.Response(200)

    store = _store(handler, virtual=True, region="cn-hangzhou")
    await store.put("kb/ws-1/abc", b"x", "image/jpeg")
    assert seen == [("kb-bucket.storage.example.com", "/kb/ws-1/abc")]
    assert store.where == "kb-bucket.storage.example.com"


async def test_retries_5xx_and_429_then_succeeds() -> None:
    statuses = iter([500, 429, 200])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(next(statuses))

    await _store(handler).put("kb/ws/a", b"x", "image/png")
    assert calls == 3


async def test_gives_up_after_three_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, content=b"<Error><Code>SlowDown</Code></Error>")

    with pytest.raises(ConnectorError) as raised:
        await _store(handler).get("kb/ws/a")
    assert calls == 4  # 第一次 + 重试 3 次
    assert raised.value.kind == "RATE_LIMITED"
    assert raised.value.reason == "SlowDown"


async def test_auth_failure_is_not_retried_and_keeps_only_the_error_code() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = b"<Error><Code>SignatureDoesNotMatch</Code><Message>secret stuff</Message></Error>"
        return httpx.Response(403, content=body)

    with pytest.raises(ConnectorError) as raised:
        await _store(handler).put("kb/ws/a", b"x", "image/png")
    assert calls == 1
    assert raised.value.kind == "AUTH_FAILED"
    assert raised.value.reason == "SignatureDoesNotMatch"
    assert "secret stuff" not in str(raised.value)


async def test_uncertain_put_is_confirmed_with_head() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "PUT":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200)  # HEAD：对象其实已经写进去了

    await _store(handler).put("kb/ws/a", b"x", "image/png")
    assert methods == ["PUT", "PUT", "PUT", "PUT", "HEAD"]


async def test_uncertain_put_without_object_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(404)

    with pytest.raises(ConnectorError) as raised:
        await _store(handler).put("kb/ws/a", b"x", "image/png")
    assert raised.value.kind == "TIMEOUT_UNKNOWN"


async def test_connection_refused_is_marked_not_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConnectorError) as raised:
        await _store(handler).get("kb/ws/a")
    assert raised.value.kind == "UNAVAILABLE"
    assert raised.value.not_sent is True


async def test_rejects_bad_keys() -> None:
    store = _store(lambda request: httpx.Response(200))
    for key in ["../etc/passwd", "kb/../x", "/abs", "kb//x", ""]:
        with pytest.raises(ConnectorError):
            await store.get(key)


# ---------------------------------------------------------------- 本地存储与配置


async def test_local_store_round_trip(tmp_path: Path) -> None:
    store = LocalStore(str(tmp_path))
    await store.put("kb/ws/abc", b"data", "image/png")
    assert (tmp_path / "kb/ws/abc").read_bytes() == b"data"
    assert await store.exists("kb/ws/abc")
    assert await store.get("kb/ws/abc") == b"data"
    await store.put("kb/ws/abc", b"data", "image/png")  # 覆盖写同样内容
    await store.delete("kb/ws/abc")
    assert await store.get("kb/ws/abc") is None
    await store.delete("kb/ws/abc")
    assert not list(tmp_path.rglob(".upload-*"))  # noqa: ASYNC240 - 没有残留的临时文件
    with pytest.raises(ConnectorError):
        await store.get("../outside")
    await self_check(store)
    assert not list((tmp_path / "kb/_probe").iterdir())


def _settings(tmp: Path, **values: Any) -> Settings:
    return Settings(file_dir=str(tmp), file_storage_timeout_seconds=5.0, **values)


def test_build_store_defaults_and_missing_settings(tmp_path: Path) -> None:
    with pytest.raises(StorageNotConfigured) as raised:
        build_store("r2", _settings(tmp_path, r2_bucket="b"))
    assert raised.value.missing == ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]

    r2 = build_store(
        "r2",
        _settings(
            tmp_path,
            r2_account_id="acc123",
            r2_access_key_id="AK",
            r2_secret_access_key=SecretStr("SK"),
            r2_bucket="kb",
            r2_jurisdiction="eu",
        ),
    )
    assert isinstance(r2, S3Store)
    assert r2.config.endpoint == "https://acc123.eu.r2.cloudflarestorage.com"
    assert (r2.config.region, r2.config.virtual_hosted) == ("auto", False)

    oss = build_store(
        "oss",
        _settings(
            tmp_path,
            oss_region="cn-hangzhou",
            oss_access_key_id="AK",
            oss_access_key_secret=SecretStr("SK"),
            oss_bucket="kb",
        ),
    )
    assert isinstance(oss, S3Store)
    assert oss.config.endpoint == "https://s3.oss-cn-hangzhou.aliyuncs.com"
    assert oss.config.virtual_hosted is True
    assert oss.where == "kb.s3.oss-cn-hangzhou.aliyuncs.com"
    assert "SK" not in repr(oss.where)

    assert isinstance(build_store("local", _settings(tmp_path)), LocalStore)
