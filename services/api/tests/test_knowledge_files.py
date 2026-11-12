"""知识库图片（T-0103，docs/v2/TIMELINE_PLAN.md 第 4 节）：真实 PostgreSQL / Redis。

覆盖：上传（类型按文件头、大小、配额、JPEG 去元数据、去重）、读取（ETag、跨工作空间 404、角色）、
笔记引用与每日清理、副本复制（假 S3）与读主存储失败读副本、运维页的存储状态与自检。
"""

import hashlib
import struct
import uuid
import zlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.domain import knowledge_files
from app.domain.storage_config import StoreRegistry
from app.models import KnowledgeFile, KnowledgeFileRef
from tests.helpers import add_user, add_workspace, csrf, run_sql
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


def png(width: int = 2, height: int = 1, extra: bytes = b"") -> bytes:
    """最小的 PNG 文件头（识别类型和尺寸只看 IHDR）；extra 用来造不同内容、不同大小。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
        + extra
    )


def jpeg_with_gps() -> bytes:
    def seg(marker: int, payload: bytes) -> bytes:
        return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload

    tiff = (
        b"MM\x00*"
        + struct.pack(">I", 8)
        + struct.pack(">H", 2)
        + struct.pack(">HHIHH", 0x0112, 3, 1, 6, 0)
        + struct.pack(">HHII", 0x8825, 4, 1, 99)
        + struct.pack(">I", 0)
    )
    return (
        b"\xff\xd8"
        + seg(0xE1, b"Exif\x00\x00" + tiff + b"GPS 31.2304N 121.4737E")
        + seg(0xC0, b"\x08" + struct.pack(">HH", 10, 20) + b"\x01\x01\x11\x00")
        + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00pixels\xff\xd9"
    )


@pytest.fixture
async def files_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # 所有客户端（包括 env.member 新建的）都读这个环境变量
    monkeypatch.setenv("FILE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str, files_dir: Path) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def upload(env: Env, data: bytes, client: httpx.AsyncClient | None = None) -> httpx.Response:
    c = client or env.client
    return await c.post(
        "/api/v1/knowledge/files",
        content=data,
        headers={
            "X-Workspace-Id": env.workspace_id,
            "Content-Type": "application/octet-stream",
            **csrf(c),
        },
    )


async def public_space_id(env: Env) -> str:
    spaces = (await env.get("/api/v1/knowledge/spaces"))["items"]
    return str(next(s["id"] for s in spaces if s["kind"] == "public"))


async def test_upload_read_and_dedupe(env: Env, files_dir: Path) -> None:
    data = png(640, 480, b"one")
    created = await upload(env, data)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["mime"] == "image/png" and (body["width"], body["height"]) == (640, 480)
    assert body["url"] == f"/api/v1/knowledge/files/{body['id']}"
    digest = hashlib.sha256(data).hexdigest()
    assert (files_dir / "kb" / env.workspace_id / digest).read_bytes() == data

    read = await env.client.get(body["url"], headers=env.headers())
    assert read.status_code == 200 and read.content == data
    assert read.headers["content-type"] == "image/png"
    assert read.headers["cache-control"].startswith("private")
    assert read.headers["x-content-type-options"] == "nosniff"
    assert read.headers["etag"] == f'"{digest}"'
    cached = await env.client.get(
        body["url"], headers={**env.headers(), "If-None-Match": read.headers["etag"]}
    )
    assert cached.status_code == 304 and cached.content == b""

    again = await upload(env, data)
    assert again.status_code == 201 and again.json()["id"] == body["id"]  # 同内容只存一份


async def test_rejects_non_images_svg_and_oversize(
    make_client: Any, migrated_database_url: str, files_dir: Path
) -> None:
    async with open_env(make_client, migrated_database_url, file_max_mb=1) as env:
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        for data in (svg, b"plain text", b"GIF89a"):
            rejected = await upload(env, data)
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["error"]["code"] == "UNSUPPORTED_IMAGE"
        big = await upload(env, png(extra=b"x" * (1024 * 1024 + 1)))
        assert big.status_code == 413
        assert big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_quota(make_client: Any, migrated_database_url: str, files_dir: Path) -> None:
    async with open_env(make_client, migrated_database_url, file_quota_mb=1) as env:
        first = await upload(env, png(extra=b"a" * 600_000))
        assert first.status_code == 201, first.text
        second = await upload(env, png(extra=b"b" * 600_000))
        assert second.status_code == 413
        assert second.json()["error"]["code"] == "QUOTA_EXCEEDED"


async def test_jpeg_metadata_is_stripped(env: Env) -> None:
    created = await upload(env, jpeg_with_gps())
    assert created.status_code == 201, created.text
    stored = (await env.client.get(created.json()["url"], headers=env.headers())).content
    assert b"GPS" not in stored and stored.endswith(b"pixels\xff\xd9")
    assert b"Exif\x00\x00II*" in stored  # 只留下方向


async def test_roles_and_other_workspaces(env: Env) -> None:
    created = await upload(env, png(extra=b"roles"))
    url = created.json()["url"]
    async with env.member("viewer@example.com", "viewer") as viewer:
        assert (
            await viewer.get(url, headers={"X-Workspace-Id": env.workspace_id})
        ).status_code == 200
        denied = await upload(env, png(extra=b"viewer"), client=viewer)
        assert denied.status_code == 403
    # 另一个工作空间的成员：看不到这张图（404，不泄露是否存在）
    other_user = await add_user(env.database_url, "stranger@example.com", "stranger password 1")
    other_ws = await add_workspace(env.database_url, other_user, "别人的空间")
    async with env.make_client() as stranger:
        login = await stranger.post(
            "/api/v1/auth/login",
            json={"email": "stranger@example.com", "password": "stranger password 1"},
        )
        assert login.status_code == 200, login.text
        hidden = await stranger.get(url, headers={"X-Workspace-Id": str(other_ws)})
        assert hidden.status_code == 404
        mine = await stranger.get(url, headers={"X-Workspace-Id": env.workspace_id})
        assert mine.status_code == 404
        assert (await stranger.get(url)).status_code == 404
    # 浏览器的 <img> 不带 X-Workspace-Id：按图片所属的工作空间校验
    assert (await env.client.get(url)).status_code == 200
    async with env.make_client() as anonymous:
        assert (await anonymous.get(url)).status_code == 401


async def _refs(env: Env, file_id: str) -> list[tuple[str, bool]]:
    async with app_of(env.client).state.sessionmaker() as db:
        rows = (
            await db.execute(
                select(KnowledgeFileRef.note_id, KnowledgeFileRef.current).where(
                    KnowledgeFileRef.file_id == uuid.UUID(file_id)
                )
            )
        ).all()
    return [(str(note_id), current) for note_id, current in rows]


async def test_refs_follow_note_bodies_and_cleanup(env: Env, files_dir: Path) -> None:
    kept = (await upload(env, png(extra=b"kept"))).json()
    orphan = (await upload(env, png(extra=b"orphan"))).json()
    space = await public_space_id(env)
    created = await env.client.post(
        "/api/v1/knowledge/notes",
        json={
            "space_id": space,
            "title": "带图的笔记",
            "body": f"截图：\n\n![架构图]({kept['url']})",
        },
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    note = created.json()
    assert await _refs(env, kept["id"]) == [(note["id"], True)]

    # 改掉正文里的图：引用保留为历史引用（版本历史里还看得到）
    updated = await env.client.patch(
        f"/api/v1/knowledge/notes/{note['id']}",
        json={"version": note["version"], "body": "没有图了"},
        headers=env.headers(write=True),
    )
    assert updated.status_code == 200, updated.text
    assert await _refs(env, kept["id"]) == [(note["id"], False)]

    later = datetime.now(UTC) + timedelta(days=2)
    async with app_of(env.client).state.sessionmaker() as db:
        stores = StoreRegistry(db, app_of(env.client).state.settings)
        removed = await knowledge_files.cleanup_unreferenced(db, stores, now=later)
        await db.commit()
    assert removed == 1
    assert (await env.client.get(orphan["url"], headers=env.headers())).status_code == 404
    assert (await env.client.get(kept["url"], headers=env.headers())).status_code == 200
    orphan_digest = hashlib.sha256(png(extra=b"orphan")).hexdigest()
    assert not (files_dir / "kb" / env.workspace_id / orphan_digest).exists()

    # 刚上传的图（不到 24 小时）不清理
    fresh = (await upload(env, png(extra=b"fresh"))).json()
    async with app_of(env.client).state.sessionmaker() as db:
        stores = StoreRegistry(db, app_of(env.client).state.settings)
        assert await knowledge_files.cleanup_unreferenced(db, stores) == 0
    assert (await env.client.get(fresh["url"], headers=env.headers())).status_code == 200


async def test_refs_ignore_other_workspaces_files(env: Env) -> None:
    foreign = uuid.uuid4()
    space = await public_space_id(env)
    created = await env.client.post(
        "/api/v1/knowledge/notes",
        json={
            "space_id": space,
            "title": "乱引用",
            "body": f"![x](/api/v1/knowledge/files/{foreign})",
        },
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    assert await _refs(env, str(foreign)) == []


class FakeS3:
    """假的 S3 兼容服务：只看方法与路径。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(500, content=b"<Error><Code>InternalError</Code></Error>")
        key = request.url.path
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if key not in self.objects:
            return httpx.Response(404)
        if request.method == "DELETE":
            self.objects.pop(key)
            return httpx.Response(204)
        return httpx.Response(200, content=self.objects[key])


R2 = {
    "account_id": "acct0001",
    "bucket": "kb-test",
    "access_key_id": "AK",
    "secret_access_key": "SK",
}


async def put_storage(env: Env, body: dict[str, Any]) -> httpx.Response:
    return await env.client.put(
        "/api/v1/settings/storage", json=body, headers=env.headers(write=True)
    )


async def test_replication_and_fallback_read(env: Env, files_dir: Path) -> None:
    app = app_of(env.client)
    fake = FakeS3()
    original = app.state.settings
    # 测试里把 R2 的地址指到假服务（部署级的覆盖，网页上改不了）
    settings = original.model_copy(update={"r2_endpoint": "https://r2.example.com"})
    app.state.settings = settings
    app.state.storage_transport = httpx.MockTransport(fake)
    try:
        saved = await put_storage(
            env, {"version": 0, "primary": "local", "replica": "r2", "r2": R2}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["checks"]["r2"]["ok"] is True  # 保存前的自检写过、删过探测对象
        data = png(extra=b"replicate me")
        created = (await upload(env, data)).json()
        async with app.state.sessionmaker() as db:
            stores = StoreRegistry(db, settings, transport=httpx.MockTransport(fake))
            row = await db.get(KnowledgeFile, uuid.UUID(created["id"]))
            assert row is not None and (row.replica, row.replica_state) == ("r2", "pending")
            # 副本暂时不可用：记下原因，下次重试
            fake.fail = True
            fake_sleepless = await knowledge_files.replicate_pending(db, stores)
            assert fake_sleepless == 0
            await db.commit()
            await db.refresh(row)
            assert (row.replica_state, row.replica_attempts) == ("pending", 1)
            assert row.replica_error == "InternalError"
            fake.fail = False
            assert await knowledge_files.replicate_pending(db, stores) == 1
            await db.commit()
            await db.refresh(row)
            assert row.replica_state == "done"
        key = f"/kb-test/kb/{env.workspace_id}/{hashlib.sha256(data).hexdigest()}"
        assert fake.objects[key] == data

        # 本地文件丢了：从副本读（经接口，按这个工作空间的配置）
        (files_dir / "kb" / env.workspace_id / hashlib.sha256(data).hexdigest()).unlink()
        read = await env.client.get(created["url"], headers=env.headers())
        assert read.status_code == 200 and read.content == data

        # 副本里还有文件：R2 的连接信息不能清空
        current = await env.get("/api/v1/settings/storage")
        assert current["in_use"] == ["local", "r2"]
        cleared = await put_storage(
            env,
            {
                "version": current["version"],
                "primary": "local",
                "replica": "none",
                "r2": {**R2, "secret_access_key": ""},
            },
        )
        assert cleared.status_code == 409 and cleared.json()["error"]["code"] == "STORAGE_IN_USE"
    finally:
        app.state.settings = original
        app.state.storage_transport = None


async def test_storage_status_and_self_check(env: Env) -> None:
    await upload(env, png(extra=b"status"))
    status = await env.get("/api/v1/operations/storage")
    assert status["primary"] == {
        "name": "local",
        "configured": True,
        "where": None,
        "missing": [],
        "check": None,
    }
    assert status["replica"] is None and status["files"] == 1 and status["used_bytes"] > 0
    checked = await env.client.post(
        "/api/v1/operations/storage/check", headers=env.headers(write=True)
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["primary"]["check"]["ok"] is True
    assert (await env.get("/api/v1/operations/storage"))["primary"]["check"]["ok"] is True
    async with env.member("viewer2@example.com", "viewer") as viewer:
        denied = await viewer.get(
            "/api/v1/operations/storage", headers={"X-Workspace-Id": env.workspace_id}
        )
        assert denied.status_code == 403


async def test_storage_not_configured(env: Env) -> None:
    """密钥解不开（换了加密密钥又没留旧的）时当作没填：上传 503，运维页说缺什么。"""
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.storage_settings"
        " (workspace_id, primary_store, oss_region, oss_bucket, oss_access_key_id, version)"
        " VALUES ($1, 'oss', 'cn-hangzhou', 'kb', 'AK', 1)",
        uuid.UUID(env.workspace_id),
    )
    failed = await upload(env, png(extra=b"nowhere"))
    assert failed.status_code == 503
    error = failed.json()["error"]
    assert error["code"] == "STORAGE_NOT_CONFIGURED" and "AccessKey Secret" in error["message"]
    status = await env.get("/api/v1/operations/storage")
    assert status["primary"]["configured"] is False
    assert status["primary"]["missing"] == ["AccessKey Secret"]
    count = await run_sql(env.database_url, "SELECT count(*) FROM workspace.knowledge_files")
    assert count == 0


async def test_graph_shows_image_nodes_on_request(env: Env) -> None:
    image = (await upload(env, png(320, 200, b"graph"))).json()
    space = await public_space_id(env)
    created = await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": space, "title": "图谱里的图", "body": f"![示意]({image['url']})"},
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    plain = await env.get("/api/v1/knowledge/graph")
    assert all(n["kind"] != "file" for n in plain["nodes"])
    graph = await env.get("/api/v1/knowledge/graph", files="true")
    files = [n for n in graph["nodes"] if n["kind"] == "file"]
    assert [(n["id"], n["title"], n["degree"]) for n in files] == [(image["id"], "图片 320×200", 1)]
    assert {"source": created.json()["id"], "target": image["id"]} in graph["edges"]
    note_node = next(n for n in graph["nodes"] if n["id"] == created.json()["id"])
    assert note_node["degree"] == 1
