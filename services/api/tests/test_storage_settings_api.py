"""工作空间的图片存储设置（T-0223）：网页“设置 → 图片存储”。

真实 PostgreSQL + Redis，R2 / OSS 用假的 S3 兼容服务（记下请求的主机与路径）。

覆盖：
- 没保存过是本机、没有副本；owner 修改，admin 只读，member 看不到；各工作空间互不影响；
- 地址只由账户 ID / 辖区 / 地域拼出 R2、OSS 的官方域名（含辖区、OSS 的虚拟托管风格），
  填别的主机名、大写桶名等都被拒绝；
- 密钥加密保存、不返回明文、审计里没有；不填保持、空字符串清除；
- 保存前自检：不通过就不保存，返回各自的原因；版本冲突 409；
- 选 R2 做主存储后，上传写到 R2，运维页显示 R2 与这次的自检结果。
"""

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.helpers import add_workspace, csrf, run_sql
from tests.test_knowledge_files import png, upload
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration

PATH = "/api/v1/settings/storage"
R2 = {
    "account_id": "0123456789abcdef0123456789abcdef",
    "bucket": "kb-test",
    "access_key_id": "AK123",
    "secret_access_key": "r2-secret-value",
}
OSS = {
    "region": "oss-cn-hangzhou",
    "bucket": "kb-oss",
    "access_key_id": "LTAI5tTEST",
    "access_key_secret": "oss-secret-value",
}


class FakeS3:
    """假的 S3 兼容服务：按主机 + 路径存对象，记下每个请求的主机。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.hosts: list[str] = []
        self.fail: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if self.fail:
            return httpx.Response(403, content=f"<Error><Code>{self.fail}</Code></Error>".encode())
        key = request.url.host + request.url.path
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if key not in self.objects:
            return httpx.Response(404)
        if request.method == "DELETE":
            self.objects.pop(key)
            return httpx.Response(204)
        return httpx.Response(200, content=self.objects[key])


@pytest.fixture
async def files_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FILE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str, files_dir: Path) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


@pytest.fixture
def fake(env: Env) -> Iterator[FakeS3]:
    app = app_of(env.client)
    server = FakeS3()
    app.state.storage_transport = httpx.MockTransport(server)
    yield server
    app.state.storage_transport = None


async def put(env: Env, body: dict[str, Any]) -> httpx.Response:
    return await env.client.put(PATH, json=body, headers=env.headers(write=True))


def error(response: httpx.Response) -> dict[str, Any]:
    data: dict[str, Any] = response.json()["error"]
    return data


async def test_defaults_roles_and_workspace_isolation(env: Env, fake: FakeS3) -> None:
    current = await env.get(PATH)
    assert current["primary"] == "local" and current["replica"] == "none"
    assert current["version"] == 0 and current["in_use"] == [] and current["updated_at"] is None
    assert current["r2"]["secret_set"] is False and current["oss"]["secret_set"] is False

    async with env.member("admin@example.com", "admin") as admin:
        seen = await admin.get(PATH, headers={"X-Workspace-Id": env.workspace_id})
        assert seen.status_code == 200
        denied = await admin.put(
            PATH,
            json={"version": 0, "primary": "local"},
            headers={"X-Workspace-Id": env.workspace_id, **csrf(admin)},
        )
        assert denied.status_code == 403
    async with env.member("member@example.com", "member") as member:
        hidden = await member.get(PATH, headers={"X-Workspace-Id": env.workspace_id})
        assert hidden.status_code == 403

    saved = await put(env, {"version": 0, "primary": "r2", "r2": R2})
    assert saved.status_code == 200, saved.text
    # 另一个工作空间（同一个 owner）不受影响
    other = await add_workspace(env.database_url, env.owner_id, "另一个")
    theirs = await env.client.get(PATH, headers={"X-Workspace-Id": str(other)})
    assert theirs.status_code == 200 and theirs.json()["primary"] == "local"


async def test_r2_and_oss_hosts_secrets_and_audit(env: Env, fake: FakeS3) -> None:
    saved = await put(env, {"version": 0, "primary": "r2", "replica": "oss", "r2": R2, "oss": OSS})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["version"] == 1 and out["primary"] == "r2" and out["replica"] == "oss"
    assert out["checks"]["r2"]["ok"] is True and out["checks"]["oss"]["ok"] is True
    # 地址由账户 ID / 地域拼出官方域名；OSS 是虚拟托管风格（桶在主机名里），地域去掉了 oss- 前缀
    assert set(fake.hosts) == {
        "0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com",
        "kb-oss.s3.oss-cn-hangzhou.aliyuncs.com",
    }
    assert out["oss"]["region"] == "cn-hangzhou"
    # 探测对象写过又删了
    assert fake.objects == {}
    # 不返回密钥；数据库里是密文；审计里没有密钥
    assert out["r2"]["secret_set"] is True and out["oss"]["secret_set"] is True
    text = saved.text
    assert "r2-secret-value" not in text and "oss-secret-value" not in text
    refs = await run_sql(
        env.database_url,
        "SELECT r2_secret_ref || ' ' || oss_secret_ref FROM workspace.storage_settings",
    )
    assert refs.count("enc:v1:") == 2 and "secret-value" not in refs
    audit = await run_sql(
        env.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs"
        " WHERE action = 'storage.settings_updated'",
    )
    assert "secret-value" not in audit
    assert '"keys_changed": ["oss", "r2"]' in audit

    # 密钥不填保持不变；换辖区后地址跟着变
    fake.hosts.clear()
    kept = await put(
        env,
        {
            "version": 1,
            "primary": "r2",
            "r2": {**R2, "jurisdiction": "EU", "secret_access_key": None},
            "oss": {**OSS, "access_key_secret": None},
        },
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["r2"]["jurisdiction"] == "eu" and kept.json()["r2"]["secret_set"] is True
    assert fake.hosts and set(fake.hosts) == {
        "0123456789abcdef0123456789abcdef.eu.r2.cloudflarestorage.com"
    }
    # 空字符串清除（OSS 没被选、也没有文件）
    cleared = await put(
        env,
        {
            "version": 2,
            "primary": "r2",
            "r2": {**R2, "jurisdiction": "eu"},
            "oss": {**OSS, "access_key_secret": ""},
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["oss"]["secret_set"] is False


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        ({"primary": "r2", "r2": {**R2, "account_id": "evil.example.com/x"}}, "account_id"),
        ({"primary": "r2", "r2": {**R2, "jurisdiction": "eu.evil"}}, "jurisdiction"),
        ({"primary": "r2", "r2": {**R2, "bucket": "Upper"}}, "bucket"),
        ({"primary": "oss", "oss": {**OSS, "region": "cn-hangzhou.evil.com"}}, "region"),
        ({"primary": "oss", "oss": {**OSS, "bucket": "a.b"}}, "bucket"),
        ({"primary": "r2", "r2": {**R2, "secret_access_key": "has space"}}, "secret_access_key"),
        ({"primary": "r2", "r2": {**R2, "access_key_id": ""}}, "r2"),
        ({"primary": "local", "replica": "local"}, "replica"),
    ],
)
async def test_validation(env: Env, fake: FakeS3, body: dict[str, Any], loc: str) -> None:
    response = await put(env, {"version": 0, **body})
    assert response.status_code == 422, response.text
    assert error(response)["details"]["errors"][0]["loc"][-1] == loc
    assert fake.hosts == []  # 校验不过就不发请求
    assert (await env.get(PATH))["version"] == 0


async def test_check_failure_keeps_settings_and_version_conflict(env: Env, fake: FakeS3) -> None:
    fake.fail = "SignatureDoesNotMatch"
    failed = await put(env, {"version": 0, "primary": "r2", "r2": R2})
    assert failed.status_code == 422
    body = error(failed)
    assert body["code"] == "STORAGE_CHECK_FAILED"
    assert body["details"]["checks"]["r2"]["ok"] is False
    assert body["details"]["checks"]["r2"]["reason"] == "SignatureDoesNotMatch"
    assert (await env.get(PATH))["version"] == 0
    fake.fail = None
    assert (await put(env, {"version": 0, "primary": "r2", "r2": R2})).status_code == 200
    stale = await put(env, {"version": 0, "primary": "local"})
    assert stale.status_code == 409 and error(stale)["code"] == "VERSION_CONFLICT"


async def test_uploads_go_to_the_workspace_primary(env: Env, fake: FakeS3) -> None:
    saved = await put(env, {"version": 0, "primary": "r2", "r2": R2})
    assert saved.status_code == 200, saved.text
    data = png(extra=b"to r2")
    created = await upload(env, data)
    assert created.status_code == 201, created.text
    key = (
        "0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"
        f"/kb-test/kb/{env.workspace_id}/{hashlib.sha256(data).hexdigest()}"
    )
    assert fake.objects[key] == data
    read = await env.client.get(created.json()["url"], headers=env.headers())
    assert read.status_code == 200 and read.content == data
    status = await env.get("/api/v1/operations/storage")
    assert status["primary"]["name"] == "r2" and status["primary"]["configured"] is True
    assert status["primary"]["check"]["ok"] is True  # 保存时的自检结果
    assert "kb-test" in status["primary"]["where"]
    assert (await env.get(PATH))["in_use"] == ["r2"]
    # 换回本机：旧图片仍从 R2 读，R2 的连接信息不能清空
    back = await put(
        env, {"version": 1, "primary": "local", "r2": {**R2, "secret_access_key": None}}
    )
    assert back.status_code == 200, back.text
    again = await env.client.get(created.json()["url"], headers=env.headers())
    assert again.status_code == 200 and again.content == data
    cleared = await put(
        env, {"version": 2, "primary": "local", "r2": {**R2, "secret_access_key": ""}}
    )
    assert cleared.status_code == 409 and error(cleared)["code"] == "STORAGE_IN_USE"
