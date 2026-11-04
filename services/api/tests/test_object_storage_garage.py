"""在真实的 S3 兼容服务（已锁定的 Garage dxflrs/garage:v2.4.1）上验证路径风格的读写删（T-0103）。

由 scripts/garage_s3_check.sh 启动 Garage、建密钥和桶后运行；没有 TEST_S3_ENDPOINT 时跳过。
R2 与 OSS 的真实账号验收要人类提供测试桶（见核验 ADR 第 3.3 节）。
"""

import os
import uuid

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.base import ConnectorError
from app.integrations.object_storage import S3Config, S3Store, build_store, self_check

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "")
pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="没有 TEST_S3_ENDPOINT（scripts/garage_s3_check.sh 会设置）"
)


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
