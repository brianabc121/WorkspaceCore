"""工作台 IM 后端对真实 OpenIM（补丁镜像）的端到端测试（可选，V2 OC-3）。

需要 TEST_OPENIM_URL、TEST_OPENIM_SECRET（见 test_openim_live.py）以及集成测试的
TEST_DATABASE_URL、TEST_REDIS_URL；缺任何一个就跳过（CI 不运行）。

核对：登记实例时真实注册账号并互加好友（im_ready）；实例启动时取到真实的 IM 令牌；
以实例身份发出的消息经对账按序号投影，内容、发送人与真实实例一致；在线状态接口的返回格式；
停用时强制下线。
"""

import asyncio
import json
import os
import uuid
from typing import Any

import pytest
from pydantic import SecretStr

from app.domain.im import admin_im_user_id
from app.domain.im import openim_client as make_openim_client
from app.integrations.openim import single_conversation_id
from app.jobs import worker
from tests.helpers import ClientFactory, run_sql
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import IM_SETTINGS, _worker_ctx, im_env

URL = os.environ.get("TEST_OPENIM_URL", "")
SECRET = os.environ.get("TEST_OPENIM_SECRET", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (URL and SECRET), reason="需要 TEST_OPENIM_URL 与 TEST_OPENIM_SECRET"),
]


async def test_instance_account_token_and_projection_on_real_openim(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    overrides: dict[str, Any] = {
        **IM_SETTINGS,
        "openim_api_url": URL.rstrip("/"),
        "openim_secret": SecretStr(SECRET),
    }
    async with im_env(make_client, migrated_database_url, **overrides) as env:
        env.app.state.connectors.transport = None  # 走真实网络
        instance = await env.create_instance(name=f"live-{uuid.uuid4().hex[:6]}")
        assert instance["im_ready"] is True
        oc, owner = instance["im_user_id"], admin_im_user_id(env.owner_id)

        token = await env.issue_token(instance["id"])
        issued = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
        assert issued.status_code == 200, issued.text
        assert issued.json()["token"] and issued.json()["expires_at"]

        client = make_openim_client(
            env.app.state.settings, env.app.state.connectors, env.app.state.redis
        )
        assert client is not None
        assert await client.is_friend(oc, owner) is True
        assert await client.online_users([oc, owner]) == set()

        marker = f"live-{uuid.uuid4().hex[:8]}"
        sent = await client.send_text(send_id=oc, recv_id=owner, text=marker)
        conv = single_conversation_id(oc, owner)
        ctx = await _worker_ctx(env)
        # 消息经 Kafka 异步落库：对账直到暗号出现
        found: Any = None
        for _ in range(30):
            await worker.reconcile_im(ctx)
            found = await run_sql(
                env.database_url,
                "SELECT json_build_object('seq', seq, 'sender', sender_instance_id,"
                " 'server', server_msg_id) FROM workspace.im_messages"
                " WHERE conversation_id = $1 AND plain_text = $2",
                conv,
                marker,
            )
            if found:
                break
            await asyncio.sleep(0.5)
        assert found, "对账没有投影出代发的消息"
        row = json.loads(found)
        assert row["sender"] == instance["id"] and row["server"] == sent.server_msg_id
        assert row["seq"] >= 1
        # 导入好友的通知也在这个会话里，按系统消息投影
        system = await run_sql(
            env.database_url,
            "SELECT count(*) FROM workspace.im_messages WHERE conversation_id = $1"
            " AND content_type >= 1000",
            conv,
        )
        assert system >= 1

        await env.set_enabled(instance["id"], False)


async def test_admin_send_round_trips_ex_on_real_openim(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    """管理员经工作台发消息：ex 里的 Idempotency-Key 原样回到 pull_msg_by_seq，投影不重复。"""
    overrides: dict[str, Any] = {
        **IM_SETTINGS,
        "openim_api_url": URL.rstrip("/"),
        "openim_secret": SecretStr(SECRET),
    }
    async with im_env(make_client, migrated_database_url, **overrides) as env:
        env.app.state.connectors.transport = None
        instance = await env.create_instance(name=f"live-{uuid.uuid4().hex[:6]}")
        status = await env.client.get("/api/v1/im/status", headers=env.headers())
        assert status.json()["available"] is True and status.json()["account_ready"] is True
        opened = await env.client.post(
            "/api/v1/im/conversations",
            json={"instance_id": instance["id"]},
            headers=env.headers(write=True),
        )
        assert opened.status_code == 200, opened.text
        conv = opened.json()["conversation_id"]
        key = uuid.uuid4()
        marker = f"admin-{uuid.uuid4().hex[:8]}"
        sent = await env.client.post(
            f"/api/v1/im/conversations/{conv}/messages",
            json={"kind": "text", "text": marker},
            headers={**env.headers(write=True), "Idempotency-Key": str(key)},
        )
        assert sent.status_code == 202, sent.text
        ctx = await _worker_ctx(env)
        row: Any = None
        for _ in range(30):
            await worker.reconcile_im(ctx)
            row = await run_sql(
                env.database_url,
                "SELECT json_build_object('seq', seq, 'key', idempotency_key,"
                " 'n', count(*) OVER ()) FROM workspace.im_messages"
                " WHERE conversation_id = $1 AND plain_text = $2",
                conv,
                marker,
            )
            if row and json.loads(row)["seq"]:
                break
            await asyncio.sleep(0.5)
        assert row, "对账没有投影出管理员发的消息"
        data = json.loads(row)
        assert data["seq"] >= 1 and data["key"] == str(key) and data["n"] == 1
        listed = await env.client.get(
            f"/api/v1/im/conversations/{conv}/messages", headers=env.headers()
        )
        texts = [m["text"] for m in listed.json()["items"]]
        assert texts.count(marker) == 1


async def test_group_create_with_derived_id_on_real_openim(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    """建群：群 ID 由 Idempotency-Key 推出、公告随建群写入、ws_notice 在群里（imAdmin 不入群）；
    幂等记录丢失后用同一个键重发，OpenIM 返回 1202，仍然只有这一个群；ws_notice 能在群里发提示。"""
    from app.domain.im_groups import send_relay_paused_notice

    overrides: dict[str, Any] = {
        **IM_SETTINGS,
        "openim_api_url": URL.rstrip("/"),
        "openim_secret": SecretStr(SECRET),
    }
    async with im_env(make_client, migrated_database_url, **overrides) as env:
        env.app.state.connectors.transport = None
        instance = await env.create_instance(name=f"live-{uuid.uuid4().hex[:6]}")
        key = uuid.uuid4()
        body = {"name": "真实群", "announcement": "实测公告", "instance_ids": [instance["id"]]}
        headers = {**env.headers(write=True), "Idempotency-Key": str(key)}
        created = await env.client.post("/api/v1/im/groups", json=body, headers=headers)
        assert created.status_code == 201, created.text
        group_id = created.json()["group_id"]
        assert group_id == f"ws{key.hex}"

        client = make_openim_client(
            env.app.state.settings, env.app.state.connectors, env.app.state.redis
        )
        assert client is not None
        infos = await client.call("/group/get_groups_info", {"groupIDs": [group_id]})
        info = (infos.get("groupInfos") or [{}])[0]
        assert info.get("groupID") == group_id and info.get("notification") == "实测公告"
        members = await client.call(
            "/group/get_group_member_list",
            {"groupID": group_id, "pagination": {"pageNumber": 1, "showNumber": 100}},
        )
        ids = {m.get("userID") for m in members.get("members") or []}
        assert {
            admin_im_user_id(env.owner_id),
            instance["im_user_id"],
            "ws_notice",
        } <= ids
        assert "imAdmin" not in ids

        # 幂等记录丢失（例如过期）后用同一个键重发：OpenIM 返回 1202，当作已经建好
        await run_sql(
            env.database_url,
            "DELETE FROM workspace.idempotency_requests WHERE idempotency_key = $1",
            key,
        )
        again = await env.client.post("/api/v1/im/groups", json=body, headers=headers)
        assert again.status_code == 201 and again.json()["group_id"] == group_id
        count = await run_sql(
            env.database_url,
            "SELECT count(*) FROM workspace.im_groups WHERE group_id = $1",
            group_id,
        )
        assert count == 1

        await send_relay_paused_notice(client, group_id)
        found = False
        for _ in range(30):
            top = (await client.max_seqs(admin_im_user_id(env.owner_id))).get(f"sg_{group_id}", 0)
            pulled = await client.pull_messages(
                admin_im_user_id(env.owner_id), f"sg_{group_id}", 1, max(top, 1)
            )
            if any(m.get("sendID") == "ws_notice" for m in pulled):
                found = True
                break
            await asyncio.sleep(0.5)
        assert found, "ws_notice 的提示没有出现在群里"


def _png(width: int, height: int) -> bytes:
    import struct
    import zlib

    # 每行：过滤字节 0 + 每个像素 3 字节（RGB）
    rows = b"".join(b"\x00" + bytes([y % 256, 0, 99]) * width for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    text = chunk(b"tEXt", b"Comment\x00" + uuid.uuid4().hex.encode())
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + text
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


async def test_image_upload_send_and_display_on_real_openim(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    """图片：上传到真实的对象存储（Garage）、作为 102 消息发出（PictureElem 通过 OpenIM 校验）、
    经 /im/media 原样取回；投影出来的图片消息带 media_id。"""
    object_url = os.environ.get("TEST_OPENIM_OBJECT_URL", "")
    if not object_url:
        pytest.skip("需要 TEST_OPENIM_OBJECT_URL（对象存储地址，例如 http://garage:3900）")
    overrides: dict[str, Any] = {
        **IM_SETTINGS,
        "openim_api_url": URL.rstrip("/"),
        "openim_secret": SecretStr(SECRET),
        "openim_object_url": object_url,
    }
    async with im_env(make_client, migrated_database_url, **overrides) as env:
        env.app.state.connectors.transport = None
        instance = await env.create_instance(name=f"live-{uuid.uuid4().hex[:6]}")
        opened = await env.client.post(
            "/api/v1/im/conversations",
            json={"instance_id": instance["id"]},
            headers=env.headers(write=True),
        )
        conv = opened.json()["conversation_id"]
        image = _png(320, 200)
        uploaded = await env.client.post(
            "/api/v1/im/uploads",
            content=image,
            headers={**env.headers(write=True), "Content-Type": "image/png"},
        )
        assert uploaded.status_code == 201, uploaded.text
        upload = uploaded.json()
        assert (upload["mime"], upload["width"], upload["height"]) == ("image/png", 320, 200)

        sent = await env.client.post(
            f"/api/v1/im/conversations/{conv}/messages",
            json={"kind": "image", "upload_id": upload["upload_id"]},
            headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert sent.status_code == 202, sent.text
        assert sent.json()["kind"] == "image"
        assert sent.json()["image"]["media_id"] == upload["upload_id"]

        shown = await env.client.get(
            f"/api/v1/im/media/{upload['upload_id']}", headers=env.headers()
        )
        assert shown.status_code == 200 and shown.content == image
        assert shown.headers["content-type"] == "image/png"

        ctx = await _worker_ctx(env)
        projected: Any = None
        for _ in range(30):
            await worker.reconcile_im(ctx)
            listed = await env.client.get(
                f"/api/v1/im/conversations/{conv}/messages", headers=env.headers()
            )
            projected = next(
                (m for m in listed.json()["items"] if m["kind"] == "image" and m["seq"]), None
            )
            if projected:
                break
            await asyncio.sleep(0.5)
        assert projected, "对账没有投影出图片消息"
        assert projected["image"]["media_id"] == upload["upload_id"]
        assert projected["image"]["width"] == 320


def _pb_string(field: int, value: str) -> bytes:
    data = value.encode()
    return bytes([(field << 3) | 2, len(data)]) + data


async def test_ingress_gateway_on_real_openim(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    """接入网关：真实 IM 令牌经 /im/api 转发到 OpenIM（白名单、用户校验、protobuf 接口）、
    forward_auth 放行 /im/ws、实例经 /im/obj/m 取回工作台发出的图片（含缩略图）。"""
    object_url = os.environ.get("TEST_OPENIM_OBJECT_URL", "")
    if not object_url:
        pytest.skip("需要 TEST_OPENIM_OBJECT_URL")
    from tests.test_agents_api import agent_headers as _agent_headers

    overrides: dict[str, Any] = {
        **IM_SETTINGS,
        "openim_api_url": URL.rstrip("/"),
        "openim_secret": SecretStr(SECRET),
        "openim_object_url": object_url,
    }
    async with im_env(make_client, migrated_database_url, **overrides) as env:
        env.app.state.connectors.transport = None
        instance = await env.create_instance(name=f"live-{uuid.uuid4().hex[:6]}")
        oc, owner = instance["im_user_id"], admin_im_user_id(env.owner_id)
        token = await env.issue_token(instance["id"])
        im_token = (
            await env.client.post("/agent-api/v1/im-token", headers=_agent_headers(token))
        ).json()["token"]
        ingress = {"X-MyWS-Ingress": "intranet"}

        async def api(route: str, body: Any, *, raw: bytes | None = None) -> Any:
            return await env.client.post(
                f"/im/api{route}",
                content=raw if raw is not None else json.dumps(body).encode(),
                headers={
                    **ingress,
                    "token": im_token,
                    "operationID": uuid.uuid4().hex,
                    "Content-Type": "application/json",
                },
            )

        friends = await api("/friend/get_full_friend_user_ids", {"userID": oc, "idHash": 0})
        assert friends.status_code == 200, friends.text
        assert friends.json()["errCode"] == 0 and owner in friends.json()["data"]["userIDs"]
        other = await api("/friend/get_full_friend_user_ids", {"userID": owner, "idHash": 0})
        assert other.status_code == 403 and other.json()["errCode"] == 1002
        blocked = await api("/friend/delete_friend", {"ownerUserID": oc, "friendUserID": owner})
        assert blocked.status_code == 404
        info = await api("/user/get_users_info", {"userIDs": [oc, owner]})
        assert info.status_code == 200 and info.json()["errCode"] == 0
        stranger = await api("/user/get_users_info", {"userIDs": [f"ws_{uuid.uuid4().hex}"]})
        assert stranger.status_code == 403
        active = await api(
            "/jssdk/get_active_conversations", None, raw=_pb_string(1, oc) + b"\x10\x0a"
        )
        assert active.status_code == 200, active.text
        spoofed = await api("/jssdk/get_active_conversations", None, raw=_pb_string(1, owner))
        assert spoofed.status_code == 403
        bad_token = await env.client.post(
            "/im/api/friend/get_full_friend_user_ids",
            json={"userID": oc, "idHash": 0},
            headers={**ingress, "token": im_token[:-2] + "xx"},
        )
        assert bad_token.status_code == 401

        async def auth(uri: str) -> int:
            response = await env.client.get(
                "/internal/gateway/im-auth",
                headers={**ingress, "X-Forwarded-Method": "GET", "X-Forwarded-Uri": uri},
            )
            return response.status_code

        assert await auth(f"/im/ws?sendID={oc}&token={im_token}&platformID=5") == 200
        assert await auth(f"/im/ws?sendID={owner}&token={im_token}&platformID=5") == 401
        assert await auth(f"/im/obj/x?sendID={oc}&token={im_token}") == 403

        # 实例取图：管理员发出的图片，经 /im/obj/m 原样取回；缩略图不超过 256
        await env.client.post(
            "/api/v1/im/conversations",
            json={"instance_id": instance["id"]},
            headers=env.headers(write=True),
        )
        conv = single_conversation_id(owner, oc)
        image = _png(600, 400)
        upload = (
            await env.client.post(
                "/api/v1/im/uploads",
                content=image,
                headers={**env.headers(write=True), "Content-Type": "image/png"},
            )
        ).json()
        unsent = await env.client.get(f"/im/obj/m/{upload['upload_id']}", headers=ingress)
        assert unsent.status_code == 404  # 还没发出
        await env.client.post(
            f"/api/v1/im/conversations/{conv}/messages",
            json={"kind": "image", "upload_id": upload["upload_id"]},
            headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
        )
        fetched = await env.client.get(f"/im/obj/m/{upload['upload_id']}", headers=ingress)
        assert fetched.status_code == 200 and fetched.content == image
        thumb = await env.client.get(f"/im/obj/m/{upload['upload_id']}?thumb=1", headers=ingress)
        assert thumb.status_code == 200 and thumb.content[:8] == b"\x89PNG\r\n\x1a\n"
        import struct

        width, height = struct.unpack(">II", thumb.content[16:24])
        assert max(width, height) <= 256
        assert (await env.client.get(f"/im/obj/m/{upload['upload_id']}")).status_code == 404

        # 停用后令牌不再放行
        await env.set_enabled(instance["id"], False)
        assert await auth(f"/im/ws?sendID={oc}&token={im_token}&platformID=5") == 401
