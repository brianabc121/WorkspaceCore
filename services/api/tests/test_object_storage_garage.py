"""在真实的 S3 兼容服务（锁定的 Garage dxflrs/garage:v2.4.1）上验证知识库图片的存储。

T-0103、T-0106。

- 路径风格（R2 的寻址方式）与虚拟主机风格（OSS 的寻址方式，Garage 配了 root_domain）的读写删；
- 主存储 + 副本：上传写主存储，Worker 复制到副本；主存储丢了文件、或者主存储拒绝凭证时从副本读；
  每日清理把两边都删掉。

由 scripts/garage_s3_check.sh 启动 Garage、建密钥和两个桶后运行；没有 TEST_S3_ENDPOINT 时跳过。
R2 与 OSS 的真实账号验收要人类提供测试桶（见核验 ADR 第 3.3 节）。
"""

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.domain import knowledge_files
from app.domain.knowledge_files import FileService, FileStores, object_key
from app.integrations.base import ConnectorError
from app.integrations.object_storage import S3Config, S3Store, build_store, self_check
from app.models import KnowledgeFile
from tests.helpers import csrf
from tests.workspace_support import Env, app_of, open_env

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "")
SECRET = os.environ.get("TEST_S3_SECRET_KEY", "")
REGION = os.environ.get("TEST_S3_REGION", "garage")
REPLICA_BUCKET = os.environ.get("TEST_S3_REPLICA_BUCKET", "")
# Garage 的 root_domain（虚拟主机风格：<桶>.<根域名>）；没有 DNS，由 ToGarage 把连接发到本机
ROOT_DOMAIN = os.environ.get("TEST_S3_ROOT_DOMAIN", "")
pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="没有 TEST_S3_ENDPOINT（scripts/garage_s3_check.sh 会设置）"
)
needs_replica = pytest.mark.skipif(
    not (REPLICA_BUCKET and ROOT_DOMAIN),
    reason="没有 TEST_S3_REPLICA_BUCKET / TEST_S3_ROOT_DOMAIN（scripts/garage_s3_check.sh 会设置）",
)


class ToGarage(httpx.AsyncBaseTransport):
    """把连接发到本机的 Garage，Host 头（签名用的就是它）保持原样：虚拟主机风格的地址不用改 DNS。"""

    def __init__(self) -> None:
        parts = urlsplit(ENDPOINT)
        self.host, self.port = parts.hostname or "127.0.0.1", parts.port
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(host=self.host, port=self.port)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def _store(secret: str | None = None) -> S3Store:
    return S3Store(
        S3Config(
            name="r2",
            endpoint=ENDPOINT,
            region=os.environ.get("TEST_S3_REGION", "garage"),
            bucket=os.environ.get("TEST_S3_BUCKET", "kb-test"),
            access_key=os.environ.get("TEST_S3_ACCESS_KEY", ""),
            secret_key=secret or os.environ.get("TEST_S3_SECRET_KEY", ""),
            virtual_hosted=False,
        ),
        timeout=10,
    )


async def test_path_style_put_get_head_delete_on_garage() -> None:
    store = _store()
    key = f"kb/{uuid.uuid4()}/{'ab' * 32}"
    data = b"\x89PNG\r\n\x1a\n" + os.urandom(2048)
    await store.put(key, data, "image/png")
    await store.put(key, data, "image/png")  # 同一个 key 再写一次：幂等
    assert await store.exists(key) is True
    assert await store.get(key) == data
    await store.delete(key)
    assert await store.exists(key) is False
    assert await store.get(key) is None
    await store.delete(key)  # 删除不存在的对象不报错
    await self_check(store)


async def test_wrong_secret_is_an_auth_failure_with_the_s3_error_code() -> None:
    with pytest.raises(ConnectorError) as raised:
        await _store(secret="0" * 64).put(f"kb/x/{'cd' * 32}", b"x", "image/png")
    assert raised.value.kind == "AUTH_FAILED"
    assert raised.value.reason in {"SignatureDoesNotMatch", "AccessDenied", "InvalidAccessKeyId"}


async def test_r2_settings_with_an_endpoint_override_reach_garage() -> None:
    # 与生产代码同一条路径：按配置建存储（R2 的默认是路径风格；endpoint / region 覆盖成 Garage）
    settings = Settings(
        r2_endpoint=ENDPOINT,
        r2_region=os.environ.get("TEST_S3_REGION", "garage"),
        r2_access_key_id=os.environ.get("TEST_S3_ACCESS_KEY", ""),
        r2_secret_access_key=SecretStr(os.environ.get("TEST_S3_SECRET_KEY", "")),
        r2_bucket=os.environ.get("TEST_S3_BUCKET", "kb-test"),
    )
    await self_check(build_store("r2", settings))


def _garage_settings(base: Settings, *, r2_secret: str | None = None) -> Settings:
    """主存储 r2（路径风格，TEST_S3_BUCKET）；副本 oss（虚拟主机风格，TEST_S3_REPLICA_BUCKET）。"""
    port = urlsplit(ENDPOINT).port
    return base.model_copy(
        update={
            "file_storage": "r2",
            "file_storage_replica": "oss",
            "r2_endpoint": ENDPOINT,
            "r2_region": REGION,
            "r2_access_key_id": os.environ.get("TEST_S3_ACCESS_KEY", ""),
            "r2_secret_access_key": SecretStr(
                r2_secret or os.environ.get("TEST_S3_SECRET_KEY", "")
            ),
            "r2_bucket": os.environ.get("TEST_S3_BUCKET", "kb-test"),
            "oss_endpoint": f"http://{ROOT_DOMAIN}:{port}",
            "oss_region": REGION,
            "oss_access_key_id": os.environ.get("TEST_S3_ACCESS_KEY", ""),
            "oss_access_key_secret": SecretStr(os.environ.get("TEST_S3_SECRET_KEY", "")),
            "oss_bucket": REPLICA_BUCKET,
        }
    )


@needs_replica
async def test_oss_virtual_hosted_addressing_on_garage() -> None:
    settings = _garage_settings(Settings())
    store = build_store("oss", settings, transport=ToGarage())
    assert (
        isinstance(store, S3Store)
        and store.where == f"{REPLICA_BUCKET}.{ROOT_DOMAIN}:{urlsplit(ENDPOINT).port}"
    )
    await self_check(store)


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


@pytest.mark.integration
@needs_replica
async def test_primary_and_replica_on_garage(env: Env) -> None:
    app = app_of(env.client)
    original = app.state.settings
    settings = _garage_settings(original)
    app.state.settings = settings  # 之后的上传写主存储 r2，记下副本 oss
    try:
        data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x01" + os.urandom(
            64
        )
        uploaded = await env.client.post(
            "/api/v1/knowledge/files",
            content=data,
            headers={
                "X-Workspace-Id": env.workspace_id,
                "Content-Type": "application/octet-stream",
                **csrf(env.client),
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        created = uploaded.json()
        key = object_key(uuid.UUID(env.workspace_id), hashlib.sha256(data).hexdigest())
        stores = FileStores(settings, transport=ToGarage())
        primary, replica = stores.get("r2"), stores.get("oss")
        assert await primary.get(key) == data and await replica.exists(key) is False

        # Worker 复制到副本
        async with app.state.sessionmaker() as db:
            assert await knowledge_files.replicate_pending(db, stores) == 1
            await db.commit()
            row = await db.get(KnowledgeFile, uuid.UUID(created["id"]))
            assert row is not None and (row.storage, row.replica, row.replica_state) == (
                "r2",
                "oss",
                "done",
            )
        assert await replica.get(key) == data

        async with app.state.sessionmaker() as db:
            row = await db.get(KnowledgeFile, uuid.UUID(created["id"]))
            assert row is not None
            # 主存储拒绝凭证（例如密钥被轮换）：从副本读
            wrong = FileStores(_garage_settings(original, r2_secret="0" * 64), transport=ToGarage())
            assert (await FileService(db, settings, stores=wrong).read(row)).data == data
            # 主存储里的文件丢了：从副本读
            await primary.delete(key)
            assert (await FileService(db, settings, stores=stores).read(row)).data == data

        # 不再被引用、超过 24 小时：每日清理把主存储与副本都删掉
        await primary.put(key, data, "image/png")
        later = datetime.now(UTC) + timedelta(days=2)
        async with app.state.sessionmaker() as db:
            assert await knowledge_files.cleanup_unreferenced(db, stores, now=later) == 1
            await db.commit()
        assert await primary.exists(key) is False and await replica.exists(key) is False
    finally:
        app.state.settings = original
