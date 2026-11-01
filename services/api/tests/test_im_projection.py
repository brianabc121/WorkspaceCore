"""IM 消息投影、回调收件箱的处理与对账（T-0055，OC-3）。

单元测试：消息解析、单聊会话 ID 的拆分。
集成测试（真实 PostgreSQL + Redis，模拟 OpenIM）：
- 契约第 5.3 节：回调只当作提示，按序号补齐；内容、序号以拉取为准；重复处理不重复写入；
- 第 10 节：上线更新实例状态并补好友、下线向 OpenIM 核对、过期事件丢弃、停用的实例再踢一次；
  删好友后自动补回并通知；处理失败重试、超限进 dead_letter；
- 对账：回调丢失时增量对账补齐，全量对账补上中间缺的；群消息按成员拉取，降级的管理员没有会话。
"""

import base64
import json
import uuid
from typing import Any

import pytest

from app.domain.im import admin_im_user_id
from app.domain.im_projection import parse_message, split_single
from app.integrations.openim import single_conversation_id
from app.jobs import worker
from tests.helpers import add_member, add_user, run_sql
from tests.test_im_accounts_callbacks import ImEnv, _group, _worker_ctx, im_env

pytestmark = pytest.mark.integration


def _raw(content_type: int, content: Any, **extra: Any) -> dict[str, Any]:
    return {
        "seq": 3,
        "serverMsgID": "s3",
        "clientMsgID": "c3",
        "sendID": "oc_x",
        "contentType": content_type,
        "content": base64.b64encode(json.dumps(content).encode()).decode(),
        "sendTime": 1700000000000,
        **extra,
    }


@pytest.mark.parametrize(
    ("content_type", "content", "text", "at", "kept"),
    [
        (101, {"content": "你好"}, "你好", [], True),
        (106, {"text": "@A 看下", "atUserList": ["oc_a", 7]}, "@A 看下", ["oc_a"], True),
        (102, {"sourcePicture": {"url": "http://x/1.png", "width": 4}}, None, [], True),
        (1201, {"detail": "{}"}, "已成为好友", [], False),
        (1509, {}, "有新成员被邀请入群", [], False),
        (1999, {}, "系统通知", [], False),
        (110, {"data": "custom"}, None, [], False),
    ],
)
def test_parse_message_kinds(
    content_type: int, content: Any, text: str | None, at: list[str], kept: bool
) -> None:
    parsed = parse_message(_raw(content_type, content))
    assert parsed is not None
    assert (parsed.plain_text, parsed.at_im_ids) == (text, at)
    # 只保留文字、@、图片的内容；通知与不支持的类型不存原始内容
    assert parsed.content == (content if kept else {})
    assert parsed.seq == 3 and parsed.sent_at.year == 2023


def test_parse_message_rejects_placeholders_and_garbage() -> None:
    assert parse_message({**_raw(101, {}), "seq": 0}) is None
    assert parse_message({**_raw(101, {}), "serverMsgID": ""}) is None
    assert parse_message({**_raw(101, {}), "seq": "x"}) is None
    garbage = parse_message({**_raw(101, {}), "content": "%%%"})
    assert garbage is not None and garbage.content == {} and garbage.plain_text == ""
    huge = base64.b64encode(json.dumps({"content": "x" * 70000}).encode()).decode()
    assert parse_message({**_raw(101, {}), "content": huge}).content == {}  # type: ignore[union-attr]


def test_split_single() -> None:
    a, b = f"oc_{'a' * 32}", f"ws_{'b' * 32}"
    assert split_single(single_conversation_id(b, a)) == (a, b)
    assert split_single("si_short") is None
    assert split_single("sg_" + "x" * 71) is None


# ---------------------------------------------------------------- 集成


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> Any:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


class RecordingJobs:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def enqueue(self, function: str, *args: Any) -> None:
        self.calls.append(function)


async def _single(env: ImEnv) -> tuple[dict[str, Any], str, str]:
    instance = await env.create_instance()
    owner = admin_im_user_id(env.owner_id)
    conv = single_conversation_id(owner, instance["im_user_id"])
    return instance, owner, conv


async def _after(env: ImEnv, sender: str, recv: str = "", group: str = "") -> None:
    command = "callbackAfterSendGroupMsgCommand" if group else "callbackAfterSendSingleMsgCommand"
    payload = {
        "callbackCommand": command,
        "sendID": sender,
        "recvID": recv,
        "groupID": group,
        "serverMsgID": uuid.uuid4().hex,
        "clientMsgID": uuid.uuid4().hex,
        "sessionType": 3 if group else 1,
        "contentType": 101,
    }
    response = await env.callback(command, payload)
    assert response.json()["errCode"] == 0


async def _messages(env: ImEnv, conv: str) -> list[list[Any]]:
    rows = await run_sql(
        env.database_url,
        "SELECT coalesce(json_agg(json_build_array(seq, sender_im_id, plain_text) ORDER BY seq),"
        " '[]') FROM workspace.im_messages WHERE conversation_id = $1",
        conv,
    )
    return [list(r) for r in json.loads(rows)]


async def test_callbacks_trigger_projection_by_seq(env: ImEnv) -> None:
    jobs = RecordingJobs()
    env.app.state.jobs = jobs
    instance, owner, conv = await _single(env)
    oc = instance["im_user_id"]
    env.fake.add_message(conv, oc, content_type=1201, members={owner, oc})
    env.fake.add_message(conv, oc, {"content": "第一条"})
    env.fake.add_message(conv, owner, {"content": "收到"})
    await _after(env, oc, recv=owner)
    assert jobs.calls == ["process_im_inbox"]

    ctx = await _worker_ctx(env)
    assert await worker.process_im_inbox(ctx) == 1
    assert await _messages(env, conv) == [
        [1, oc, "已成为好友"],
        [2, oc, "第一条"],
        [3, owner, "收到"],
    ]
    sender_instance = await run_sql(
        env.database_url,
        "SELECT sender_instance_id FROM workspace.im_messages WHERE conversation_id = $1"
        " AND seq = 2",
        conv,
    )
    assert str(sender_instance) == instance["id"]
    conversation = await run_sql(
        env.database_url,
        "SELECT json_build_object('kind', kind, 'max_seq', max_seq, 'peer', peer_instance_id,"
        " 'owner', owner_user_id) FROM workspace.im_conversations WHERE conversation_id = $1",
        conv,
    )
    assert json.loads(conversation) == {
        "kind": "single",
        "max_seq": 3,
        "peer": instance["id"],
        "owner": str(env.owner_id),
    }
    status = await run_sql(
        env.database_url, "SELECT string_agg(status, ',') FROM workspace.im_webhook_inbox"
    )
    assert status == "completed"

    # 再来一条：只拉新的；重复的回调（同一个 serverMsgID）不再入库
    env.fake.add_message(conv, oc, {"content": "第四条"})
    await _after(env, oc, recv=owner)
    await worker.process_im_inbox(ctx)
    pulls = env.fake.paths("/msg/pull_msg_by_seq")
    assert pulls[-1]["seqRanges"][0]["begin"] == 4
    assert len(await _messages(env, conv)) == 4


async def test_untracked_conversations_are_ignored(env: ImEnv) -> None:
    await _after(env, "oc_" + "1" * 32, recv="ws_" + "2" * 32)
    ctx = await _worker_ctx(env)
    assert await worker.process_im_inbox(ctx) == 1
    outcome = await run_sql(
        env.database_url, "SELECT error_text FROM workspace.im_webhook_inbox LIMIT 1"
    )
    assert outcome == "not_tracked"
    assert env.fake.paths("/msg/pull_msg_by_seq") == []


async def test_reconcile_recovers_lost_callbacks_and_holes(env: ImEnv) -> None:
    instance, owner, conv = await _single(env)
    oc = instance["im_user_id"]
    for i in range(5):
        env.fake.add_message(conv, oc, {"content": f"m{i}"}, members={owner, oc})
    ctx = await _worker_ctx(env)
    # 没有回调：增量对账按 newest_seq 补齐
    assert await worker.reconcile_im(ctx) == 5
    assert await worker.reconcile_im(ctx) == 0
    # 中间缺一条：增量对账看不出来，全量对账补上
    await run_sql(
        env.database_url,
        "DELETE FROM workspace.im_messages WHERE conversation_id = $1 AND seq = 3",
        conv,
    )
    assert await worker.reconcile_im(ctx) == 0
    assert await worker.reconcile_im_full(ctx) == 5
    assert [m[0] for m in await _messages(env, conv)] == [1, 2, 3, 4, 5]
    # OpenIM 不可用时对账不报错，下一轮再来
    env.fake.down = True
    assert await worker.reconcile_im(ctx) == 0


async def test_group_messages_are_projected_for_admin_members(env: ImEnv) -> None:
    a = await env.create_instance()
    admin_id = await add_user(env.database_url, "admin@example.com", "admin password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin_id, "admin")
    member_id = await add_user(env.database_url, "member@example.com", "member password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member_id, "member")
    owner, admin_im = admin_im_user_id(env.owner_id), admin_im_user_id(admin_id)
    member_im = admin_im_user_id(member_id)
    oc = a["im_user_id"]
    group = await _group(
        env,
        [
            (oc, a["id"], None),
            (owner, None, env.owner_id),
            (admin_im, None, admin_id),
            (member_im, None, member_id),
        ],
    )
    conv = f"sg_{group}"
    env.fake.add_message(conv, owner, content_type=1501, members={owner, admin_im, oc})
    env.fake.add_message(
        conv, oc, {"text": "@管理员 好了", "atUserList": [owner]}, content_type=106
    )
    await _after(env, oc, group=group)
    await worker.process_im_inbox(await _worker_ctx(env))
    assert await _messages(env, conv) == [[1, owner, "群已创建"], [2, oc, "@管理员 好了"]]
    # 以群主的身份拉取；只有 owner / admin 有会话
    assert env.fake.paths("/msg/pull_msg_by_seq")[0]["userID"] == owner
    owners = await run_sql(
        env.database_url,
        "SELECT array_agg(owner_user_id ORDER BY owner_user_id) FROM workspace.im_conversations"
        " WHERE conversation_id = $1",
        conv,
    )
    assert sorted(owners) == sorted([env.owner_id, admin_id])
    at = await run_sql(
        env.database_url,
        "SELECT at_im_ids FROM workspace.im_messages WHERE conversation_id = $1 AND seq = 2",
        conv,
    )
    assert at == [owner]


async def _presence(env: ImEnv, command: str, user: str, seq: int) -> None:
    payload = {"callbackCommand": command, "userID": user, "platformID": 5, "seq": seq}
    await env.callback(command, payload)


async def test_presence_updates_status_and_befriends_new_admins(env: ImEnv) -> None:
    instance = await env.create_instance()
    oc = instance["im_user_id"]
    # 实例登记之后才加入的管理员：上线时补好友
    admin_id = await add_user(env.database_url, "admin@example.com", "admin password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin_id, "admin")
    ctx = await _worker_ctx(env)
    on, off = "callbackAfterUserOnlineCommand", "callbackAfterUserOfflineCommand"

    await _presence(env, on, oc, 1000)
    await worker.process_im_inbox(ctx)
    current = await env.instance(instance["id"])
    assert current["status"] == "online" and current["last_seen_at"] is not None
    assert env.fake.befriended(oc, admin_im_user_id(admin_id))

    # 过期的下线事件（比上次上线早）丢弃
    await _presence(env, off, oc, 999)
    await worker.process_im_inbox(ctx)
    assert (await env.instance(instance["id"]))["status"] == "online"
    # 旧连接下线、新连接还在：OpenIM 说在线就保持
    env.fake.online.add(oc)
    await _presence(env, off, oc, 2000)
    await worker.process_im_inbox(ctx)
    assert (await env.instance(instance["id"]))["status"] == "online"
    env.fake.online.discard(oc)
    await _presence(env, off, oc, 3000)
    await worker.process_im_inbox(ctx)
    assert (await env.instance(instance["id"]))["status"] == "offline"

    # 停用的实例又连上来：再踢一次，状态不变
    await env.set_enabled(instance["id"], False)
    env.fake.logged_out.clear()
    await _presence(env, on, oc, 4000)
    await worker.process_im_inbox(ctx)
    assert env.fake.logged_out == [oc]
    assert (await env.instance(instance["id"]))["status"] == "disabled"


async def test_deleted_friend_is_restored_and_admin_notified(env: ImEnv) -> None:
    instance, owner, conv = await _single(env)
    oc = instance["im_user_id"]
    env.fake.unfriend(oc, owner)
    command = "callbackAfterDeleteFriendCommand"
    await env.callback(
        command, {"callbackCommand": command, "ownerUserID": owner, "friendUserID": oc}
    )
    await worker.process_im_inbox(await _worker_ctx(env))
    assert env.fake.befriended(oc, owner)
    notice = await run_sql(
        env.database_url,
        "SELECT link FROM workspace.notifications WHERE type = 'im_friend_restored'",
    )
    assert notice == f"/im?c={conv}"


async def test_failures_are_retried_then_dead_lettered(env: ImEnv) -> None:
    instance, owner, conv = await _single(env)
    oc = instance["im_user_id"]
    env.fake.add_message(conv, oc, {"content": "x"}, members={owner, oc})
    await _after(env, oc, recv=owner)
    env.fake.down = True
    ctx = await _worker_ctx(env)
    await worker.process_im_inbox(ctx)
    row = await run_sql(
        env.database_url,
        "SELECT json_build_object('status', status, 'attempts', attempts, 'error', error_text,"
        " 'later', next_retry_at > now()) FROM workspace.im_webhook_inbox",
    )
    assert json.loads(row) == {
        "status": "retry",
        "attempts": 1,
        "error": "UNAVAILABLE",
        "later": True,
    }
    # 到期后再试；达到上限进 dead_letter
    await run_sql(
        env.database_url,
        "UPDATE workspace.im_webhook_inbox SET attempts = 7, next_retry_at = now()",
    )
    await worker.process_im_inbox(ctx)
    status = await run_sql(env.database_url, "SELECT status FROM workspace.im_webhook_inbox")
    assert status == "dead_letter"
    # 恢复后对账照样能补齐
    env.fake.down = False
    assert await worker.reconcile_im(ctx) == 1


async def test_purge_removes_old_messages_and_processed_events(env: ImEnv) -> None:
    instance, owner, conv = await _single(env)
    oc = instance["im_user_id"]
    for i in range(2):
        env.fake.add_message(conv, oc, {"content": f"m{i}"}, members={owner, oc})
    ctx = await _worker_ctx(env)
    await worker.reconcile_im(ctx)
    await run_sql(
        env.database_url,
        "UPDATE workspace.im_messages SET sent_at = CASE WHEN seq = 1"
        " THEN now() - interval '400 days' ELSE now() END",
    )
    await _after(env, oc, recv=owner)
    await worker.process_im_inbox(ctx)
    await run_sql(
        env.database_url,
        "UPDATE workspace.im_webhook_inbox SET processed_at = now() - interval '8 days'",
    )
    await worker.purge(ctx)
    assert [m[0] for m in await _messages(env, conv)] == [2]
    assert await run_sql(env.database_url, "SELECT count(*) FROM workspace.im_webhook_inbox") == 0
