"""管理员端 IM 接口（T-0055，OC-3）：状态、角标、会话、消息、发送、置顶、导出。

真实 PostgreSQL + Redis，模拟 OpenIM。覆盖契约（docs/decisions/2026-10-31-v2-oc1-contract.md）：
- 第 5.1 节：只有 owner / admin；会话属于当前用户 + 工作空间，别人看不到；第一次打开 IM 时注册账号
  并与实例补好友；
- 第 5.2 节：打开单聊（IM 未就绪 409）、列表排序（置顶在前）与筛选、未读数不含免打扰、
  置顶与免打扰同步到 OpenIM（不可用时 503、本地不改）、已读只增不减；
- 第 5.3 节：消息分页；发送必须带 Idempotency-Key、重放、换内容 409、拒收 409、结果未知 504 后
  用同一个键查询（按 ex 找回 / 超时确认未生效）、没发出 503 可重试、单聊不能 @、按用户限流；
- 第 5.5 节：消息置顶最多 10 条、重复置顶返回原来那条；第 5.8 节：导出 NDJSON 并写审计。
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.domain.im import admin_im_user_id
from app.integrations.openim import single_conversation_id
from app.jobs import worker
from tests.helpers import add_member, add_user, csrf, run_sql
from tests.test_im_accounts_callbacks import ImEnv, _group, _worker_ctx, im_env

pytestmark = pytest.mark.integration

ADMIN_PASSWORD = "admin password 123456"  # noqa: S105 - 测试账号


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


def _h(env: ImEnv, *, write: bool = False, key: uuid.UUID | None = None) -> dict[str, str]:
    headers = env.headers(write=write)
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return headers


async def _open(env: ImEnv, instance_id: str) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/im/conversations", json={"instance_id": instance_id}, headers=_h(env, write=True)
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def _send(
    env: ImEnv, conv: str, text: str = "你好", *, key: uuid.UUID | None = None, **extra: Any
) -> Any:
    return await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "text", "text": text, **extra},
        headers=_h(env, write=True, key=key or uuid.uuid4()),
    )


# ---------------------------------------------------------------- 状态与权限


async def test_status_registers_account_and_befriends_ready_instances(env: ImEnv) -> None:
    instance = await env.create_instance()
    # 实例登记时 owner 已经是好友；先拆掉，看第一次打开 IM 时补上
    owner = admin_im_user_id(env.owner_id)
    env.fake.unfriend(instance["im_user_id"], owner)
    status = await env.client.get("/api/v1/im/status", headers=_h(env))
    assert status.status_code == 200
    assert status.json() == {"available": True, "account_ready": True, "im_user_id": owner}
    assert env.fake.befriended(instance["im_user_id"], owner)

    env.fake.refuse = True
    down = await env.client.get("/api/v1/im/status", headers=_h(env))
    assert down.json()["available"] is False and down.json()["account_ready"] is True


async def test_members_cannot_use_im(env: ImEnv) -> None:
    member = await add_user(env.database_url, "member@example.com", ADMIN_PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member, "member")
    login = await env.client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200
    for path in ("/api/v1/im/status", "/api/v1/im/badges", "/api/v1/im/conversations"):
        assert (await env.client.get(path, headers=_h(env))).status_code == 403


# ---------------------------------------------------------------- 会话


async def test_open_list_update_and_read_conversations(env: ImEnv) -> None:
    a = await env.create_instance(name="甲", im_nickname="小甲")
    b = await env.create_instance(name="乙")
    env.fake.down = True
    not_ready = await env.create_instance(name="丙")
    env.fake.down = False
    refused = await env.client.post(
        "/api/v1/im/conversations",
        json={"instance_id": not_ready["id"]},
        headers=_h(env, write=True),
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "IM_NOT_READY"
    missing = await env.client.post(
        "/api/v1/im/conversations",
        json={"instance_id": str(uuid.uuid4())},
        headers=_h(env, write=True),
    )
    assert missing.status_code == 404

    conv_a = await _open(env, a["id"])
    assert conv_a["title"] == "小甲" and conv_a["kind"] == "single"
    assert conv_a["peer_instance_id"] == a["id"] and conv_a["unread_count"] == 0
    assert (await _open(env, a["id"]))["conversation_id"] == conv_a["conversation_id"]
    conv_b = await _open(env, b["id"])

    owner = admin_im_user_id(env.owner_id)
    # 实例发来 3 条（a）与 1 条（b）消息，经对账投影
    for i in range(3):
        env.fake.add_message(conv_a["conversation_id"], a["im_user_id"], {"content": f"a{i}"})
    env.fake.add_message(conv_b["conversation_id"], b["im_user_id"], {"content": "b0"})
    await worker.reconcile_im(await _worker_ctx(env))

    listed = await env.client.get("/api/v1/im/conversations", headers=_h(env))
    body = listed.json()
    assert [c["title"] for c in body["items"]] == ["小甲", "乙"]  # 最近的消息在前
    assert body["unread_total"] == 4
    first = body["items"][0]
    assert first["unread_count"] == 3 and first["max_seq"] == 3
    assert first["last_message"]["text_preview"] == "a2"

    # 置顶：同步到 OpenIM，排到最前
    pinned = await env.client.patch(
        f"/api/v1/im/conversations/{conv_b['conversation_id']}",
        json={"pinned": True, "muted": True},
        headers=_h(env, write=True),
    )
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True and pinned.json()["muted"] is True
    synced = env.fake.paths("/conversation/set_conversations")[-1]
    assert synced["userIDs"] == [owner]
    assert synced["conversation"]["isPinned"] is True
    assert synced["conversation"]["recvMsgOpt"] == 2
    assert synced["conversation"]["userID"] == b["im_user_id"]
    listed = (await env.client.get("/api/v1/im/conversations", headers=_h(env))).json()
    assert [c["title"] for c in listed["items"]] == ["乙", "小甲"]
    assert listed["unread_total"] == 3  # 免打扰的会话不计入
    badges = (await env.client.get("/api/v1/im/badges", headers=_h(env))).json()
    assert badges == {"im_unread": 3, "pending_approvals": 0}

    # OpenIM 不可用：503，本地不改
    env.fake.refuse = True
    failed = await env.client.patch(
        f"/api/v1/im/conversations/{conv_b['conversation_id']}",
        json={"pinned": False},
        headers=_h(env, write=True),
    )
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "IM_UNAVAILABLE"
    env.fake.refuse = False
    again = (await env.client.get("/api/v1/im/conversations", headers=_h(env))).json()
    assert again["items"][0]["pinned"] is True

    # 筛选
    only = await env.client.get("/api/v1/im/conversations", params={"q": "乙"}, headers=_h(env))
    assert [c["title"] for c in only.json()["items"]] == ["乙"]
    groups = await env.client.get(
        "/api/v1/im/conversations", params={"kind": "group"}, headers=_h(env)
    )
    assert groups.json()["items"] == []
    no_group = await env.client.get(
        "/api/v1/im/conversations",
        params={"contact_group_id": str(uuid.uuid4())},
        headers=_h(env),
    )
    assert no_group.status_code == 404
    paged = await env.client.get("/api/v1/im/conversations", params={"limit": 1}, headers=_h(env))
    assert paged.json()["next_cursor"]
    second = await env.client.get(
        "/api/v1/im/conversations",
        params={"limit": 1, "cursor": paged.json()["next_cursor"]},
        headers=_h(env),
    )
    assert [c["title"] for c in second.json()["items"]] == ["小甲"]

    # 已读只增不减，不超过最大序号
    path = f"/api/v1/im/conversations/{conv_b['conversation_id']}/read"
    read = await env.client.post(path, json={"seq": 99}, headers=_h(env, write=True))
    assert read.json()["read_seq"] == 1 and read.json()["unread_count"] == 0
    back = await env.client.post(path, json={"seq": 0}, headers=_h(env, write=True))
    assert back.json()["read_seq"] == 1


async def test_conversations_belong_to_the_current_user(env: ImEnv) -> None:
    instance = await env.create_instance()
    conv = await _open(env, instance["id"])
    admin = await add_user(env.database_url, "admin@example.com", ADMIN_PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin, "admin")
    await env.client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": ADMIN_PASSWORD}
    )
    listed = await env.client.get("/api/v1/im/conversations", headers=_h(env))
    assert listed.json()["items"] == []
    path = f"/api/v1/im/conversations/{conv['conversation_id']}"
    assert (await env.client.get(f"{path}/messages", headers=_h(env))).status_code == 404
    assert (await _send(env, conv["conversation_id"])).status_code == 404
    assert (await env.client.get(f"{path}/export", headers=_h(env))).status_code == 404


# ---------------------------------------------------------------- 消息与发送


async def test_send_is_idempotent_and_projection_does_not_duplicate(env: ImEnv) -> None:
    instance = await env.create_instance()
    conv = (await _open(env, instance["id"]))["conversation_id"]
    key = uuid.uuid4()
    sent = await _send(env, conv, "第一条", key=key)
    assert sent.status_code == 202, sent.text
    body = sent.json()
    assert body["status"] == "sent" and body["server_msg_id"] and body["text"] == "第一条"
    assert body["sender_im_id"] == admin_im_user_id(env.owner_id)
    call = env.fake.paths("/msg/send_msg")[-1]
    assert call["sendID"] == admin_im_user_id(env.owner_id)
    assert call["recvID"] == instance["im_user_id"]
    assert json.loads(call["ex"]) == {"myws_key": str(key)}

    replay = await _send(env, conv, "第一条", key=key)
    assert replay.status_code == 202 and replay.headers["idempotent-replayed"] == "true"
    assert replay.json() == body
    assert len(env.fake.paths("/msg/send_msg")) == 1
    reused = await _send(env, conv, "别的内容", key=key)
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # 投影补上序号，仍是同一行
    await worker.reconcile_im(await _worker_ctx(env))
    rows = await run_sql(
        env.database_url,
        "SELECT json_agg(json_build_array(seq, status)) FROM workspace.im_messages"
        " WHERE conversation_id = $1",
        conv,
    )
    assert json.loads(rows) == [[1, "sent"]]
    listed = await env.client.get(f"/api/v1/im/conversations/{conv}/messages", headers=_h(env))
    assert [m["seq"] for m in listed.json()["items"]] == [1]


async def test_send_rejections_unknown_results_and_retries(env: ImEnv) -> None:
    instance = await env.create_instance()
    conv = (await _open(env, instance["id"]))["conversation_id"]

    # OpenIM 拒收（before 回调 5005）：409，重放同样的错误
    env.fake.send_error = 5005
    key = uuid.uuid4()
    rejected = await _send(env, conv, key=key)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "IM_SEND_REJECTED"
    assert rejected.json()["error"]["details"] == {"err_code": 5005}
    replayed = await _send(env, conv, key=key)
    assert replayed.status_code == 409 and replayed.headers["idempotent-replayed"] == "true"
    env.fake.send_error = 0

    # 连接被拒：确定没有发出，503，同一个键可以重试
    env.fake.refuse = True
    key = uuid.uuid4()
    assert (await _send(env, conv, key=key)).status_code == 503
    env.fake.refuse = False
    assert (await _send(env, conv, key=key)).status_code == 202

    # 应答超时（OpenIM 其实已受理）：504；用同一个键查询时补齐投影、按 ex 找回
    env.fake.send_timeout = True
    key = uuid.uuid4()
    unknown = await _send(env, conv, "超时的那条", key=key)
    assert unknown.status_code == 504 and unknown.json()["error"]["code"] == "RESULT_UNKNOWN"
    env.fake.send_timeout = False
    found = await _send(env, conv, "超时的那条", key=key)
    assert found.status_code == 202 and found.json()["text"] == "超时的那条"
    assert found.json()["server_msg_id"]
    count = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_messages WHERE plain_text = '超时的那条'",
    )
    assert count == 1

    # 结果未知、OpenIM 也没有这条：5 分钟内仍是 504，之后确认没有生效
    env.fake.send_timeout = True
    env.fake.send_error = 0
    key = uuid.uuid4()
    original = env.fake._msg_send_msg

    def lost(body: dict[str, Any]) -> Any:
        raise httpx.ReadTimeout("lost")

    env.fake._msg_send_msg = lost  # type: ignore[method-assign]
    assert (await _send(env, conv, "丢了", key=key)).status_code == 504
    env.fake._msg_send_msg = original  # type: ignore[method-assign]
    env.fake.send_timeout = False
    assert (await _send(env, conv, "丢了", key=key)).status_code == 504
    await run_sql(
        env.database_url,
        "UPDATE workspace.idempotency_requests SET context_json = jsonb_set(context_json,"
        " '{started_at}', to_jsonb((now() - interval '6 minutes')::text))"
        " WHERE idempotency_key = $1",
        key,
    )
    gone = await _send(env, conv, "丢了", key=key)
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "OPERATION_NOT_APPLIED"


async def test_send_validation_and_rate_limit(env: ImEnv) -> None:
    a = await env.create_instance()
    b = await env.create_instance(name="乙")
    conv = (await _open(env, a["id"]))["conversation_id"]
    at_single = await _send(env, conv, at_im_ids=[a["im_user_id"]])
    assert at_single.status_code == 422
    image = await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "image", "upload_id": str(uuid.uuid4())},
        headers=_h(env, write=True, key=uuid.uuid4()),
    )
    assert image.status_code == 404
    no_key = await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "text", "text": "x"},
        headers=_h(env, write=True),
    )
    assert no_key.status_code == 422

    # 群里只能 @ 群成员；@ 时用 106
    owner = admin_im_user_id(env.owner_id)
    group = await _group(env, [(a["im_user_id"], a["id"], None), (owner, None, env.owner_id)])
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.im_conversations (workspace_id, owner_user_id, conversation_id,"
        " kind, group_pk) SELECT workspace_id, owner_user_id, 'sg_' || group_id, 'group', id"
        " FROM workspace.im_groups WHERE group_id = $1",
        group,
    )
    outsider = await _send(env, f"sg_{group}", at_im_ids=[b["im_user_id"]])
    assert outsider.status_code == 422
    ok = await _send(env, f"sg_{group}", "@甲 看下", at_im_ids=[a["im_user_id"]])
    assert ok.status_code == 202 and ok.json()["at_im_ids"] == [a["im_user_id"]]
    call = env.fake.paths("/msg/send_msg")[-1]
    assert call["groupID"] == group and call["contentType"] == 106

    # 每人每分钟 30 条：上面的群消息算 1 条
    for _ in range(29):
        assert (await _send(env, conv)).status_code == 202
    limited = await _send(env, conv)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0


async def test_messages_pagination_kinds_and_pins(env: ImEnv) -> None:
    instance = await env.create_instance()
    conv = (await _open(env, instance["id"]))["conversation_id"]
    oc = instance["im_user_id"]
    env.fake.add_message(conv, oc, content_type=1201)
    for i in range(11):
        env.fake.add_message(conv, oc, {"content": f"m{i}"})
    env.fake.add_message(conv, oc, {"data": "x"}, content_type=110)
    await worker.reconcile_im(await _worker_ctx(env))

    path = f"/api/v1/im/conversations/{conv}"
    first = (await env.client.get(f"{path}/messages", params={"limit": 5}, headers=_h(env))).json()
    assert [m["seq"] for m in first["items"]] == [13, 12, 11, 10, 9]
    assert first["items"][0]["kind"] == "unsupported" and first["items"][0]["text"] is None
    rest: list[dict[str, Any]] = []
    cursor = first["next_cursor"]
    while cursor:
        page = (
            await env.client.get(
                f"{path}/messages", params={"limit": 5, "cursor": cursor}, headers=_h(env)
            )
        ).json()
        rest += page["items"]
        cursor = page["next_cursor"]
    assert [m["seq"] for m in rest] == list(range(8, 0, -1))
    assert rest[-1]["kind"] == "system" and rest[-1]["text"] == "已成为好友"
    assert rest[-1]["sender_instance_id"] == instance["id"]
    bad = await env.client.get(f"{path}/messages", params={"cursor": "%%"}, headers=_h(env))
    assert bad.status_code == 422

    everything = first["items"] + rest
    ids = [m["server_msg_id"] for m in everything[:10]]
    for server_msg_id in ids:
        pinned = await env.client.post(
            f"{path}/pins", json={"server_msg_id": server_msg_id}, headers=_h(env, write=True)
        )
        assert pinned.status_code == 201, pinned.text
    again = await env.client.post(
        f"{path}/pins", json={"server_msg_id": ids[0]}, headers=_h(env, write=True)
    )
    assert again.status_code == 201 and again.json()["server_msg_id"] == ids[0]
    full = await env.client.post(
        f"{path}/pins",
        json={"server_msg_id": everything[10]["server_msg_id"]},
        headers=_h(env, write=True),
    )
    assert full.status_code == 409 and full.json()["error"]["code"] == "PIN_LIMIT_REACHED"
    unknown = await env.client.post(
        f"{path}/pins", json={"server_msg_id": "nope"}, headers=_h(env, write=True)
    )
    assert unknown.status_code == 404
    listed = (await env.client.get(f"{path}/pins", headers=_h(env))).json()
    assert len(listed["items"]) == 10 and listed["items"][0]["message"]["pinned"] is True
    messages = (await env.client.get(f"{path}/messages", headers=_h(env))).json()
    assert sum(m["pinned"] for m in messages["items"]) == 10
    removed = await env.client.delete(f"{path}/pins/{ids[0]}", headers=_h(env, write=True))
    assert removed.status_code == 204
    gone = await env.client.delete(f"{path}/pins/{ids[0]}", headers=_h(env, write=True))
    assert gone.status_code == 404


async def test_export_streams_ndjson_and_is_audited(env: ImEnv) -> None:
    instance = await env.create_instance()
    conv = (await _open(env, instance["id"]))["conversation_id"]
    for i in range(3):
        env.fake.add_message(conv, instance["im_user_id"], {"content": f"m{i}"})
    await worker.reconcile_im(await _worker_ctx(env))
    exported = await env.client.get(f"/api/v1/im/conversations/{conv}/export", headers=_h(env))
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in exported.text.splitlines()]
    assert [m["text"] for m in lines] == ["m0", "m1", "m2"]
    audited = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'im.conversation.exported'",
    )
    assert audited == 1
    assert single_conversation_id(admin_im_user_id(env.owner_id), instance["im_user_id"]) == conv
    assert csrf(env.client)
