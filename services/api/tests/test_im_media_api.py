"""IM 图片（T-0055，OC-3，契约第 5.4 节）。

单元测试：按内容识别 PNG、JPEG、GIF、WebP 与尺寸，其他一律不认。
集成测试（真实 PostgreSQL + Redis，模拟 OpenIM 与对象存储）：上传（类型按内容、≤ 10 MiB）、
发图（PictureElem、实例侧地址、一张图只能发一次）、显示（上传者或能看到会话的管理员）、
没用过的上传到期清理。
"""

import struct
import uuid
import zlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.im import admin_im_user_id
from app.domain.images import sniff
from app.jobs import worker
from tests import fake_openim
from tests.helpers import add_member, add_user, run_sql
from tests.test_im_accounts_callbacks import ImEnv, _group, _worker_ctx, im_env

pytestmark = pytest.mark.integration

PASSWORD = "admin password 123456"  # noqa: S105 - 测试账号


def png(width: int = 40, height: int = 30) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xda\x00\x02" + b"\x00" * 10 + b"\xff\xd9"


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + b"\x3b"


def webp_vp8x(width: int, height: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (png(40, 30), ("image/png", 40, 30)),
        (jpeg(640, 480), ("image/jpeg", 640, 480)),
        (gif(12, 7), ("image/gif", 12, 7)),
        (webp_vp8x(300, 200), ("image/webp", 300, 200)),
    ],
)
def test_sniff_recognises_supported_images(data: bytes, expected: tuple[str, int, int]) -> None:
    info = sniff(data)
    assert info is not None and (info.mime, info.width, info.height) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        png(40, 30)[:20],  # 截断
        gif(0, 7),  # 宽为 0
        jpeg(30000, 10),  # 超出合理尺寸
        b"\xff\xd8\xff\xda\x00\x02",  # 没有帧头就开始图像数据
        b"RIFF\x00\x00\x00\x00WEBPXXXX" + b"\x00" * 20,
    ],
)
def test_sniff_rejects_everything_else(data: bytes) -> None:
    assert sniff(data) is None


# ---------------------------------------------------------------- 集成


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(
        make_client, migrated_database_url, openim_object_url=fake_openim.OBJECT_URL
    ) as env:
        yield env


async def _upload(env: ImEnv, data: bytes, content_type: str = "image/png") -> Any:
    return await env.client.post(
        "/api/v1/im/uploads",
        content=data,
        headers={**env.headers(write=True), "Content-Type": content_type},
    )


async def _open(env: ImEnv, instance_id: str) -> str:
    opened = await env.client.post(
        "/api/v1/im/conversations",
        json={"instance_id": instance_id},
        headers=env.headers(write=True),
    )
    conversation_id: str = opened.json()["conversation_id"]
    return conversation_id


async def _send_image(env: ImEnv, conv: str, upload_id: str) -> Any:
    return await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "image", "upload_id": upload_id},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )


async def test_upload_validates_content_and_size(env: ImEnv) -> None:
    # 声明的类型不算数：按内容识别
    uploaded = await _upload(env, jpeg(64, 48), content_type="image/png")
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert (body["mime"], body["width"], body["height"]) == ("image/jpeg", 64, 48)
    assert body["size"] == len(jpeg(64, 48)) and body["expires_at"]
    name = await run_sql(
        env.database_url,
        "SELECT object_name FROM workspace.im_media WHERE id = $1",
        uuid.UUID(body["upload_id"]),
    )
    assert name == f"myws/{env.workspace_id}/{body['upload_id']}.jpg"
    assert env.fake.objects[name] == jpeg(64, 48)

    svg = await _upload(env, b"<svg onload='alert(1)'/>", content_type="image/svg+xml")
    assert svg.status_code == 422
    big = await _upload(env, b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024))
    assert big.status_code == 413
    env.fake.refuse = True
    assert (await _upload(env, png())).status_code == 503


async def test_upload_needs_object_storage(make_client: Any, migrated_database_url: str) -> None:
    async with im_env(make_client, migrated_database_url) as env:
        response = await _upload(env, png())
        assert response.status_code == 503 and response.json()["error"]["code"] == "IM_UNAVAILABLE"


async def test_send_image_and_display(env: ImEnv) -> None:
    instance = await env.create_instance()
    conv = await _open(env, instance["id"])
    image = png(400, 300)
    upload = (await _upload(env, image)).json()
    # 发出之前上传者本人就能看
    before = await env.client.get(f"/api/v1/im/media/{upload['upload_id']}", headers=env.headers())
    assert before.status_code == 200 and before.content == image

    sent = await _send_image(env, conv, upload["upload_id"])
    assert sent.status_code == 202, sent.text
    message = sent.json()
    assert message["kind"] == "image" and message["text"] is None
    assert message["image"] == {
        "media_id": upload["upload_id"],
        "external_url": None,
        "width": 400,
        "height": 300,
    }
    call = env.fake.paths("/msg/send_msg")[-1]
    assert call["contentType"] == 102 and call["recvID"] == instance["im_user_id"]
    picture = call["content"]
    url = f"http://127.0.0.1:18443/im/obj/m/{upload['upload_id']}"
    assert picture["sourcePicture"]["url"] == url and picture["bigPicture"]["url"] == url
    assert picture["snapshotPicture"]["url"] == url + "?thumb=1"
    assert (picture["snapshotPicture"]["width"], picture["snapshotPicture"]["height"]) == (256, 192)
    assert picture["sourcePicture"]["type"] == "image/png"

    shown = await env.client.get(f"/api/v1/im/media/{upload['upload_id']}", headers=env.headers())
    assert shown.status_code == 200 and shown.content == image
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["cache-control"].startswith("private")

    # 一张上传只能发一次；不存在的上传 404
    assert (await _send_image(env, conv, upload["upload_id"])).status_code == 404
    assert (await _send_image(env, conv, str(uuid.uuid4()))).status_code == 404

    # 投影出来的图片消息带 media_id
    await worker.reconcile_im(await _worker_ctx(env))
    listed = await env.client.get(
        f"/api/v1/im/conversations/{conv}/messages", headers=env.headers()
    )
    images = [m for m in listed.json()["items"] if m["kind"] == "image"]
    assert len(images) == 1 and images[0]["image"]["media_id"] == upload["upload_id"]
    assert images[0]["seq"] is not None


async def test_media_is_visible_only_to_people_in_the_conversation(env: ImEnv) -> None:
    a = await env.create_instance()
    member_admin = await add_user(env.database_url, "admin@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member_admin, "admin")
    outsider = await add_user(env.database_url, "other@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), outsider, "admin")
    owner = admin_im_user_id(env.owner_id)
    group = await _group(
        env,
        [
            (a["im_user_id"], a["id"], None),
            (owner, None, env.owner_id),
            (admin_im_user_id(member_admin), None, member_admin),
        ],
    )
    for user_id in (env.owner_id, member_admin):
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.im_conversations (workspace_id, owner_user_id, conversation_id,"
            " kind, group_pk) SELECT workspace_id, $2, 'sg_' || group_id, 'group', id"
            " FROM workspace.im_groups WHERE group_id = $1",
            group,
            user_id,
        )
    unsent = (await _upload(env, png())).json()
    upload = (await _upload(env, png(10, 10))).json()
    assert (await _send_image(env, f"sg_{group}", upload["upload_id"])).status_code == 202

    async def view(email: str, media_id: str) -> int:
        await env.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        response = await env.client.get(f"/api/v1/im/media/{media_id}", headers=env.headers())
        return response.status_code

    assert await view("admin@example.com", upload["upload_id"]) == 200  # 在群里
    assert await view("admin@example.com", unsent["upload_id"]) == 404  # 别人没发出的上传
    assert await view("other@example.com", upload["upload_id"]) == 404  # 不在群里
    # 别人的上传也不能拿来发
    conv = await _open(env, a["id"])
    assert (await _send_image(env, conv, unsent["upload_id"])).status_code == 404


async def test_unused_uploads_expire(env: ImEnv) -> None:
    upload = (await _upload(env, png())).json()
    await run_sql(
        env.database_url,
        "UPDATE workspace.im_media SET expires_at = now() - interval '1 minute' WHERE id = $1",
        uuid.UUID(upload["upload_id"]),
    )
    instance = await env.create_instance()
    conv = await _open(env, instance["id"])
    assert (await _send_image(env, conv, upload["upload_id"])).status_code == 404
    await worker.purge(await _worker_ctx(env))
    left = await run_sql(env.database_url, "SELECT count(*) FROM workspace.im_media")
    assert left == 0
