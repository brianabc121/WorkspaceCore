"""接入网关的 IM 部分（T-0055，OC-3，契约第 8 节）。

单元测试：IM 令牌（HS256）的校验、protobuf 顶层字段的读取。
集成测试（真实 PostgreSQL + Redis，模拟 OpenIM）：forward_auth 放行 /im/ws 的条件、
/im/api 的白名单与用户校验、请求体上限、只认接入入口来的请求、外网入口要客户端证书、
实例取图的能力地址。
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.im import admin_im_user_id
from app.domain.im_gateway import proto_string_fields, verify_im_token
from tests import fake_openim
from tests.test_health import _Collect
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_im_media_api import png

pytestmark = pytest.mark.integration


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(
    user_id: str,
    *,
    secret: str = fake_openim.SECRET,
    platform: int = 5,
    expires_in: int = 3600,
    alg: str = "HS256",
) -> str:
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps(
            {"UserID": user_id, "PlatformID": platform, "exp": int(time.time()) + expires_in}
        ).encode()
    )
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def test_verify_im_token() -> None:
    claims = verify_im_token(make_token("oc_x"), fake_openim.SECRET)
    assert claims is not None and (claims.user_id, claims.platform_id) == ("oc_x", 5)
    assert verify_im_token(make_token("oc_x", secret="other"), fake_openim.SECRET) is None  # noqa: S106
    assert verify_im_token(make_token("oc_x", expires_in=-5), fake_openim.SECRET) is None
    assert verify_im_token(make_token("oc_x", alg="none"), fake_openim.SECRET) is None
    assert verify_im_token("not.a.token", fake_openim.SECRET) is None
    assert verify_im_token("x", fake_openim.SECRET) is None


def _pb(field: int, value: str) -> bytes:
    data = value.encode()
    return bytes([(field << 3) | 2, len(data)]) + data


def test_proto_string_fields() -> None:
    message = _pb(1, "oc_a") + b"\x10\x0a" + _pb(2, "si_x") + _pb(2, "si_y")
    assert proto_string_fields(message, 1) == ["oc_a"]
    assert proto_string_fields(message, 2) == ["si_x", "si_y"]
    assert proto_string_fields(_pb(1, "a") + _pb(1, "b"), 1) == ["a", "b"]
    assert proto_string_fields(b"\x0a\x10abc", 1) is None  # 长度越界
    assert proto_string_fields(b"\x0f", 1) is None  # 未知的线格式
    assert proto_string_fields(b"", 1) == []


# ---------------------------------------------------------------- 集成


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(
        make_client, migrated_database_url, openim_object_url=fake_openim.OBJECT_URL
    ) as env:
        yield env


INTRANET = {"X-MyWS-Ingress": "intranet"}


async def _auth(env: ImEnv, uri: str, headers: dict[str, str] | None = None) -> int:
    response = await env.client.get(
        "/internal/gateway/im-auth",
        headers={
            **(INTRANET if headers is None else headers),
            "X-Forwarded-Method": "GET",
            "X-Forwarded-Uri": uri,
        },
    )
    return response.status_code


async def test_forward_auth_for_websocket(env: ImEnv) -> None:
    instance = await env.create_instance()
    oc = instance["im_user_id"]
    token = make_token(oc)
    ws = f"/im/ws?sendID={oc}&token={token}&platformID=5&operationID=x"
    assert await _auth(env, ws) == 200
    assert await _auth(env, ws, headers={}) == 401  # 没有入口：不是经接入入口来的
    assert await _auth(env, ws, headers={"X-MyWS-Ingress": "public"}) == 401  # 入口不对
    owner = admin_im_user_id(env.owner_id)
    assert await _auth(env, f"/im/ws?sendID={owner}&token={token}") == 401  # sendID 不符
    assert await _auth(env, f"/im/ws?sendID={owner}&token={make_token(owner)}") == 401  # 不是实例
    assert await _auth(env, f"/im/ws?sendID={oc}&token={token}&platformID=1") == 401
    expired = make_token(oc, expires_in=-1)
    assert await _auth(env, f"/im/ws?sendID={oc}&token={expired}") == 401
    assert await _auth(env, f"/im/api/x?sendID={oc}&token={token}") == 403  # 只管 /im/ws
    await env.set_enabled(instance["id"], False)
    assert await _auth(env, ws) == 401
    await env.set_enabled(instance["id"], True)
    await env.kill_switch(False)
    assert await _auth(env, ws) == 401


async def test_public_ingress_requires_the_instances_certificate(env: ImEnv) -> None:
    instance = await env.create_instance(network_mode="public")
    oc = instance["im_user_id"]
    ws = f"/im/ws?sendID={oc}&token={make_token(oc)}&platformID=5"
    fingerprint = "ab" * 32
    public = {"X-MyWS-Ingress": "public"}
    assert await _auth(env, ws, public) == 401
    await env.add_cert(instance["id"], fingerprint)
    assert await _auth(env, ws, {**public, "X-MyWS-Client-Cert-SHA256": fingerprint}) == 200
    other = {**public, "X-MyWS-Client-Cert-SHA256": "cd" * 32}
    assert await _auth(env, ws, other) == 401
    # 外网入口同一来源失败 10 次后封禁
    for _ in range(9):
        await _auth(env, ws, other)
    assert await _auth(env, ws, {**public, "X-MyWS-Client-Cert-SHA256": fingerprint}) == 429


async def _api(env: ImEnv, route: str, body: Any, token: str, *, raw: bytes | None = None) -> Any:
    return await env.client.post(
        f"/im/api{route}",
        content=raw if raw is not None else json.dumps(body).encode(),
        headers={
            **INTRANET,
            "token": token,
            "operationID": "op-1",
            "Content-Type": "application/json",
        },
    )


async def test_rest_whitelist_and_user_checks(env: ImEnv) -> None:
    a = await env.create_instance()
    b = await env.create_instance(name="乙")
    oc, token = a["im_user_id"], make_token(a["im_user_id"])
    owner = admin_im_user_id(env.owner_id)
    await env.client.get("/api/v1/im/status", headers=env.headers())  # 登记 owner 的 IM 账号

    ok = await _api(env, "/friend/get_full_friend_user_ids", {"userID": oc, "idHash": 0}, token)
    assert ok.status_code == 200 and ok.json()["data"] == {"forwarded": True}
    path, forwarded_token, body = env.fake.forwarded[-1]
    assert path == "/friend/get_full_friend_user_ids" and forwarded_token == token
    assert json.loads(body) == {"userID": oc, "idHash": 0}

    blocked = {
        "errCode": 1002,
        "errMsg": "NoPermissionError",
        "errDlt": "blocked by My Workspace gateway",
    }
    spoofed = await _api(
        env, "/conversation/get_incremental_conversations", {"userID": b["im_user_id"]}, token
    )
    assert spoofed.status_code == 403 and spoofed.json() == blocked
    forwarded = len(env.fake.forwarded)
    # 白名单以外一律拦截（T-0058 / OC-6）：删好友、搜消息、改会话、按序号拉别人的消息、
    # 建群踢人解散、撤回删除、管理员与注册接口；都不转发给 OpenIM
    for route in (
        "/friend/delete_friend",
        "/friend/add_friend",
        "/msg/search_msg",
        "/msg/pull_msg_by_seq",
        "/msg/revoke_msg",
        "/msg/delete_msgs",
        "/conversation/set_conversations",
        "/conversation/get_all_conversations",
        "/user/user_register",
        "/user/get_users_online_status",
        "/auth/get_admin_token",
        "/auth/force_logout",
        "/group/create_group",
        "/group/kick_group",
        "/group/dismiss_group",
        "/group/transfer_group",
    ):
        refused = await _api(env, route, {"userID": oc}, token)
        assert refused.status_code == 404 and refused.json() == blocked, route
    assert len(env.fake.forwarded) == forwarded

    # 好友申请通知后 SDK 查这条申请：只能查自己与本工作空间成员之间的（被拦截会让 OpenClaw 退出）
    apply = "/friend/get_designated_friend_apply"
    allowed = await _api(env, apply, {"fromUserID": owner, "toUserID": oc}, token)
    assert allowed.status_code == 200, allowed.text
    assert json.loads(env.fake.forwarded[-1][2]) == {"fromUserID": owner, "toUserID": oc}
    for pair in (
        {"fromUserID": owner, "toUserID": b["im_user_id"]},  # 别人之间的申请
        {"fromUserID": f"ws_{uuid.uuid4().hex}", "toUserID": oc},  # 工作空间以外的人
        {"fromUserID": oc},  # 缺字段
    ):
        refused = await _api(env, apply, pair, token)
        assert refused.status_code == 403, pair

    # 单聊前查对方的好友信息：只能以自己为 owner、查本工作空间的人
    friends = "/friend/get_designated_friends"
    mine = {"ownerUserID": oc, "friendUserIDs": [owner, b["im_user_id"]]}
    assert (await _api(env, friends, mine, token)).status_code == 200
    for lookup in (
        {"ownerUserID": owner, "friendUserIDs": [oc]},  # 冒充别人
        {"ownerUserID": oc, "friendUserIDs": [f"oc_{uuid.uuid4().hex}"]},  # 工作空间以外
        {"ownerUserID": oc, "friendUserIDs": []},
    ):
        assert (await _api(env, friends, lookup, token)).status_code == 403, lookup

    same_ws = await _api(
        env, "/user/get_users_info", {"userIDs": [oc, b["im_user_id"], owner, "ws_notice"]}, token
    )
    assert same_ws.status_code == 200
    stranger = await _api(
        env, "/user/get_users_info", {"userIDs": [f"oc_{uuid.uuid4().hex}"]}, token
    )
    assert stranger.status_code == 403

    pb_ok = await _api(
        env, "/jssdk/get_conversations", None, token, raw=_pb(1, oc) + _pb(2, "si_x")
    )
    assert pb_ok.status_code == 200
    pb_bad = await _api(env, "/jssdk/get_active_conversations", None, token, raw=_pb(1, owner))
    assert pb_bad.status_code == 403
    garbage = await _api(env, "/jssdk/get_active_conversations", None, token, raw=b"\x0a\x40x")
    assert garbage.status_code == 403

    assert (
        await _api(env, "/friend/get_full_friend_user_ids", {"userID": oc}, "bad")
    ).status_code == 401
    too_big = await _api(
        env, "/friend/get_full_friend_user_ids", None, token, raw=b"{" + b" " * 70000 + b"}"
    )
    assert too_big.status_code == 413
    no_ingress = await env.client.post(
        "/im/api/friend/get_full_friend_user_ids",
        json={"userID": oc},
        headers={"token": token},
    )
    assert no_ingress.status_code == 401


async def test_group_member_lookups_stay_inside_the_group(env: ImEnv) -> None:
    """SDK 发群消息前查群成员信息（被拦截时消息发不出去，T-0059 验收发现）：只能查自己所在的群。"""
    a = await env.create_instance()
    b = await env.create_instance(name="乙")
    c = await env.create_instance(name="丙")
    await env.client.get("/api/v1/im/status", headers=env.headers())
    created = await env.client.post(
        "/api/v1/im/groups",
        json={"name": "值班群", "instance_ids": [a["id"], b["id"]], "user_ids": []},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    gid = created.json()["group_id"]
    route = "/group/get_group_members_info"
    token_a = make_token(a["im_user_id"])
    body = {"groupID": gid, "userIDs": [a["im_user_id"], b["im_user_id"]]}
    allowed = await _api(env, route, body, token_a)
    assert allowed.status_code == 200, allowed.text
    assert json.loads(env.fake.forwarded[-1][2]) == body
    # 不在群里的实例查不了；群成员也不能借它查群外的人或别的群
    outsider = await _api(env, route, body, make_token(c["im_user_id"]))
    assert outsider.status_code == 403
    for refused in (
        {"groupID": gid, "userIDs": [c["im_user_id"]]},
        {"groupID": f"ws{uuid.uuid4().hex}", "userIDs": [a["im_user_id"]]},
        {"groupID": gid, "userIDs": []},
    ):
        assert (await _api(env, route, refused, token_a)).status_code == 403, refused


async def test_instances_fetch_sent_images_by_capability(env: ImEnv) -> None:
    instance = await env.create_instance()
    opened = await env.client.post(
        "/api/v1/im/conversations",
        json={"instance_id": instance["id"]},
        headers=env.headers(write=True),
    )
    conv = opened.json()["conversation_id"]
    image = png(64, 64)
    upload = (
        await env.client.post(
            "/api/v1/im/uploads",
            content=image,
            headers={**env.headers(write=True), "Content-Type": "image/png"},
        )
    ).json()
    path = f"/im/obj/m/{upload['upload_id']}"
    assert (await env.client.get(path, headers=INTRANET)).status_code == 404  # 还没发出
    await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "image", "upload_id": upload["upload_id"]},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    request_logger = logging.getLogger("app.request")
    collect = _Collect()
    request_logger.addHandler(collect)
    previous_level = request_logger.level
    request_logger.setLevel(logging.INFO)
    try:
        fetched = await env.client.get(path, headers=INTRANET)
    finally:
        request_logger.removeHandler(collect)
        request_logger.setLevel(previous_level)
    assert fetched.status_code == 200 and fetched.content == image
    # 能力地址不进访问日志
    logged = [r.__dict__.get("path") for r in collect.records]
    assert "/im/obj/m/***" in logged and not any(upload["upload_id"] in str(p) for p in logged)
    assert fetched.headers["content-type"] == "image/png"
    assert (await env.client.get(path)).status_code == 404  # 不是经接入入口来的
    assert (await env.client.get(f"/im/obj/m/{uuid.uuid4()}", headers=INTRANET)).status_code == 404
