"""智能体的 IM 会话目录与读消息（T-0117，docs/v2/AGENT_MESSAGING_PLAN.md 第 3 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖：
- `im_list_targets`：能私聊的管理员（白名单快照判断“他回你你收不收得到”、没开通 IM 的提示）、
  所在的群（成员、规则、接力暂停、这一分钟剩余条数）、按名字筛选、只列自己所在的群；
- `im_read_messages`：私聊与群（只读入群之后的）、分页、正文截断、附件引用；
  不能读别人的群、不能把其他实例当私聊对象。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.im import admin_im_user_id
from app.domain.im_callbacks import RATE_KEY, STREAK_KEY
from app.integrations.openim import single_conversation_id
from tests.helpers import add_member, add_user, run_sql, tool_error
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import agent, call, ok

pytestmark = pytest.mark.integration

PASSWORD = "admin password 123456"  # noqa: S105 - 测试账号


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def _admin(env: ImEnv, email: str) -> uuid.UUID:
    user_id = await add_user(env.database_url, email, PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user_id, "admin")
    return user_id


async def _group(env: ImEnv, name: str, instance_ids: list[str]) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/im/groups",
        json={"name": name, "instance_ids": instance_ids, "user_ids": [str(env.owner_id)]},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    group: dict[str, Any] = response.json()
    return group


async def _message(
    env: ImEnv,
    conversation: str,
    sender: str,
    text: str | None,
    minutes_ago: int,
    *,
    content_type: int = 101,
    content: str = "{}",
) -> None:
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.im_messages (workspace_id, conversation_id, server_msg_id,"
        " client_msg_id, sender_im_id, content_type, content_json, plain_text, sent_at)"
        " VALUES ($1, $2, $3, $3, $4, $5, $6::jsonb, $7, now() - make_interval(mins => $8))",
        uuid.UUID(env.workspace_id),
        conversation,
        uuid.uuid4().hex,
        sender,
        content_type,
        content,
        text,
        minutes_ago,
    )


async def test_list_targets_direct_and_whitelist(env: ImEnv) -> None:
    a, token = await agent(env, "甲")
    other = await _admin(env, "second@example.com")
    listed = ok(await call(env, token, "im_list_targets", {}))
    assert listed["me"]["im_user_id"] == a["im_user_id"]
    by_id = {d["im_user_id"]: d for d in listed["direct"]}
    owner_im = admin_im_user_id(env.owner_id)
    assert set(by_id) == {owner_im, admin_im_user_id(other)}
    assert by_id[owner_im]["role"] == "owner" and by_id[owner_im]["target"] == f"user:{owner_im}"
    assert by_id[owner_im]["conversation_id"] == single_conversation_id(a["im_user_id"], owner_im)
    # 还没签发过接入包：无法判断
    assert by_id[owner_im]["replies_reach_you"] is None
    assert any("无法判断" in n for n in listed["notes"])
    # 白名单快照里只有 owner：后加的管理员回的私聊收不到
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET bundle_whitelist = $1 WHERE id = $2",
        [owner_im, a["im_user_id"]],
        uuid.UUID(a["id"]),
    )
    listed = ok(await call(env, token, "im_list_targets", {"kind": "direct"}))
    by_id = {d["im_user_id"]: d for d in listed["direct"]}
    assert by_id[owner_im]["replies_reach_you"] is True
    second = by_id[admin_im_user_id(other)]
    assert second["replies_reach_you"] is False and "重新签发接入包" in second["hint"]
    assert listed["groups"] == []
    # 普通成员不能私聊，不列出
    member = await add_user(env.database_url, "member@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member, "member")
    listed = ok(await call(env, token, "im_list_targets", {"kind": "direct"}))
    assert admin_im_user_id(member) not in {d["im_user_id"] for d in listed["direct"]}
    # 按名字筛选（owner 的名字来自首次安装）
    none = ok(await call(env, token, "im_list_targets", {"query": "不存在的名字"}))
    assert none["direct"] == [] and none["groups"] == []


async def test_list_targets_groups_rules_and_state(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    c, _ = await agent(env, "丙")
    group = await _group(env, "发布群", [a["id"], b["id"]])
    await _group(env, "别人的群", [c["id"]])
    redis = env.app.state.redis
    await redis.set(STREAK_KEY.format(group_id=group["group_id"]), 8)
    await redis.set(RATE_KEY.format(group_id=group["group_id"], sender=a["im_user_id"]), 4)
    listed = ok(await call(env, token_a, "im_list_targets", {"kind": "group"}))
    assert [g["untrusted"]["name"] for g in listed["groups"]] == ["发布群"]
    item = listed["groups"][0]
    assert item["target"] == f"group:{group['group_id']}"
    assert item["rules"] == {"relay_enabled": True, "max_per_minute": 6, "max_agent_streak": 8}
    assert item["state"] == {"paused": True, "agent_streak": 8, "remaining_this_minute": 2}
    assert item["mention_required"] is True
    members = {m["im_user_id"]: m for m in item["members"]}
    assert members[a["im_user_id"]]["is_you"] is True
    assert members[b["im_user_id"]]["kind"] == "agent"
    assert members[admin_im_user_id(env.owner_id)]["kind"] == "admin"
    assert {m["untrusted"]["name"] for m in item["members"]} >= {"甲", "乙"}


async def test_read_messages_scope_paging_and_attachments(env: ImEnv) -> None:
    a, token = await agent(env, "甲")
    b, token_b = await agent(env, "乙")
    owner_im = admin_im_user_id(env.owner_id)
    direct = single_conversation_id(a["im_user_id"], owner_im)
    media = uuid.uuid4()
    await _message(env, direct, owner_im, "第一条", 30)
    await _message(env, direct, a["im_user_id"], "好的", 20)
    await _message(
        env,
        direct,
        owner_im,
        None,
        10,
        content_type=102,
        content=f'{{"sourcePicture": {{"uuid": "{media}", "type": "image/png", "size": 12}}}}',
    )
    await _message(env, direct, owner_im, "长" * 2500, 5)
    page = ok(
        await call(env, token, "im_read_messages", {"target": f"user:{owner_im}", "limit": 3})
    )
    assert page["conversation_id"] == direct and page["has_more"] is True
    texts = [m["untrusted"]["text"] for m in page["items"]]
    assert texts[0] == "好的" and texts[1] is None and len(texts[2]) == 2000
    assert page["items"][2]["untrusted"]["truncated"] is True
    assert page["items"][0]["is_you"] is True and page["items"][0]["sender_kind"] == "agent"
    picture = page["items"][1]
    assert picture["kind"] == "image" and picture["attachments"][0]["ref"] == f"im_media:{media}"
    older = ok(
        await call(
            env,
            token,
            "im_read_messages",
            {"target": f"user:{owner_im}", "before": page["next_before"]},
        )
    )
    assert [m["untrusted"]["text"] for m in older["items"]] == ["第一条"]
    assert older["has_more"] is False and older["next_before"] is None
    # 群：只读入群之后的消息；不在群里的读不到
    group = await _group(env, "发布群", [a["id"]])
    conv = group["conversation_id"]
    await run_sql(
        env.database_url,
        "UPDATE workspace.im_group_members SET joined_at = now() - interval '15 minutes'"
        " WHERE im_user_id = $1",
        a["im_user_id"],
    )
    await _message(env, conv, owner_im, "入群前", 60)
    await _message(env, conv, owner_im, "入群后", 1)
    read = ok(await call(env, token, "im_read_messages", {"target": f"group:{group['group_id']}"}))
    assert [m["untrusted"]["text"] for m in read["items"]] == ["入群后"]
    refused = tool_error(
        await call(env, token_b, "im_read_messages", {"target": f"group:{group['group_id']}"})
    )
    assert refused["code"] == "TARGET_NOT_ALLOWED"
    # 其他实例不是私聊对象
    refused = tool_error(
        await call(env, token, "im_read_messages", {"target": f"user:{b['im_user_id']}"})
    )
    assert refused["code"] == "TARGET_NOT_ALLOWED" and "群里 @" in refused["message"]
    invalid = tool_error(await call(env, token, "im_read_messages", {"target": "nobody"}))
    assert invalid["code"] == "VALIDATION_FAILED"
