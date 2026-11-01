"""IM 群、通讯录与分组（T-0055，OC-3）。真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖契约（docs/decisions/2026-10-31-v2-oc1-contract.md）：
- 第 5.6 节：建群必须带 Idempotency-Key（重放、结果未知后重发不会多建群）；成员只能是本工作空间
  已就绪的实例与 owner / admin；ws_notice 一起进群；改名与公告、邀请、移出（不能移出群主）、
  解散（只有群主）先调用 OpenIM，失败 503 本地不改；防刷屏设置乐观并发、paused 取实时计数；
- 第 10 节：自动接力暂停时 ws_notice 在群里提示；
- 第 5.7 节：通讯录包含停用的实例；分组名称唯一、成员整体替换、删除不影响实例；会话按分组筛选。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.domain.im import admin_im_user_id
from app.domain.im_callbacks import STREAK_KEY
from tests.helpers import add_member, add_user, add_workspace, run_sql
from tests.test_im_accounts_callbacks import GROUP, ImEnv, im_env, msg

pytestmark = pytest.mark.integration

PASSWORD = "admin password 123456"  # noqa: S105 - 测试账号


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


def _h(env: ImEnv, *, key: uuid.UUID | None = None) -> dict[str, str]:
    headers = env.headers(write=True)
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return headers


async def _create(env: ImEnv, body: dict[str, Any], key: uuid.UUID | None = None) -> Any:
    return await env.client.post(
        "/api/v1/im/groups", json=body, headers=_h(env, key=key or uuid.uuid4())
    )


async def _admin(env: ImEnv, email: str = "admin@example.com") -> uuid.UUID:
    user_id = await add_user(env.database_url, email, PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user_id, "admin")
    return user_id


async def test_create_group_is_idempotent_and_validates_members(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    admin = await _admin(env)
    member = await add_user(env.database_url, "member@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member, "member")
    key = uuid.uuid4()
    body = {
        "name": "项目群",
        "announcement": "每天同步一次",
        "instance_ids": [a["id"], b["id"]],
        "user_ids": [str(admin), str(env.owner_id)],
    }
    created = await _create(env, body, key)
    assert created.status_code == 201, created.text
    group = created.json()
    assert group["group_id"] == f"ws{key.hex}" and group["conversation_id"] == f"sg_ws{key.hex}"
    owner = admin_im_user_id(env.owner_id)
    assert group["owner_im_id"] == owner and group["announcement"] == "每天同步一次"
    roles = {m["im_user_id"]: (m["role"], m["nickname"]) for m in group["members"]}
    assert roles[owner][0] == "owner" and roles[a["im_user_id"]] == ("member", "甲")
    assert set(roles) == {owner, a["im_user_id"], b["im_user_id"], admin_im_user_id(admin)}
    assert group["policy"] == {
        "relay_enabled": True,
        "max_agent_streak": 8,
        "max_per_minute": 6,
        "paused": False,
        "version": 1,
    }
    # ws_notice 也在 OpenIM 的群里（imAdmin 不入群）；群公告随建群写入
    assert "ws_notice" in env.fake.members[group["conversation_id"]]
    assert "imAdmin" not in env.fake.members[group["conversation_id"]]
    assert env.fake.groups[group["group_id"]]["notification"] == "每天同步一次"
    conversations = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_conversations WHERE conversation_id = $1",
        group["conversation_id"],
    )
    assert conversations == 2  # 群主与另一个管理员

    replay = await _create(env, body, key)
    assert replay.status_code == 201 and replay.headers["idempotent-replayed"] == "true"
    assert len(env.fake.paths("/group/create_group")) == 1
    reused = await _create(env, {**body, "name": "别的"}, key)
    assert reused.status_code == 409

    foreign_ws = await add_workspace(env.database_url, env.owner_id, "别的空间")
    foreign = await env.client.post(
        "/api/v1/agents/instances",
        json={"name": "外", "network_mode": "intranet"},
        headers={**_h(env), "X-Workspace-Id": str(foreign_ws)},
    )
    cases = {
        "别的工作空间的实例": ({"name": "g", "instance_ids": [foreign.json()["id"]]}, 404),
        "普通成员": ({"name": "g", "instance_ids": [a["id"]], "user_ids": [str(member)]}, 404),
        "没有实例": ({"name": "g", "user_ids": [str(admin)]}, 422),
    }
    for name, (case, expected) in cases.items():
        assert (await _create(env, case)).status_code == expected, name
    env.fake.down = True
    not_ready = await env.create_instance(name="未就绪")
    env.fake.down = False
    refused = await _create(env, {"name": "g", "instance_ids": [not_ready["id"]]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "IM_NOT_READY"


async def test_create_group_retries_after_unknown_or_refused(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = await env.create_instance()
    body = {"name": "g", "instance_ids": [a["id"]]}
    # 连接被拒：503，同一个键可以直接重试
    env.fake.refuse = True
    key = uuid.uuid4()
    assert (await _create(env, body, key)).status_code == 503
    env.fake.refuse = False
    assert (await _create(env, body, key)).status_code == 201

    # OpenIM 建好了群但应答超时：504；重发同一个键时 OpenIM 返回 1202，当作已经建好
    original = env.fake._group_create_group

    def slow(request_body: dict[str, Any]) -> httpx.Response:
        original(request_body)
        raise httpx.ReadTimeout("simulated timeout")

    monkeypatch.setattr(env.fake, "_group_create_group", slow)
    key = uuid.uuid4()
    unknown = await _create(env, body, key)
    assert unknown.status_code == 504 and unknown.json()["error"]["code"] == "RESULT_UNKNOWN"
    monkeypatch.setattr(env.fake, "_group_create_group", original)
    done = await _create(env, body, key)
    assert done.status_code == 201 and done.json()["group_id"] == f"ws{key.hex}"
    groups = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_groups WHERE group_id = $1",
        f"ws{key.hex}",
    )
    assert groups == 1


async def test_update_members_policy_and_dismiss(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    c = await env.create_instance(name="丙")
    admin = await _admin(env)
    group = (await _create(env, {"name": "g", "instance_ids": [a["id"]]})).json()
    gid, path = group["group_id"], f"/api/v1/im/groups/{group['group_id']}"

    renamed = await env.client.patch(
        path, json={"name": "新名字", "announcement": "公告"}, headers=_h(env)
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "新名字"
    assert env.fake.groups[gid]["name"] == "新名字"
    env.fake.refuse = True
    failed = await env.client.patch(path, json={"name": "不会改"}, headers=_h(env))
    assert failed.status_code == 503
    env.fake.refuse = False
    assert (await env.client.get(path, headers=env.headers())).json()["name"] == "新名字"

    added = await env.client.post(
        f"{path}/members",
        json={"instance_ids": [a["id"], c["id"]], "user_ids": [str(admin)]},
        headers=_h(env),
    )
    assert added.status_code == 200
    assert {m["nickname"] for m in added.json()["members"]} >= {"甲", "丙"}
    assert {c["im_user_id"], admin_im_user_id(admin)} <= env.fake.members[f"sg_{gid}"]
    owner = admin_im_user_id(env.owner_id)
    owner_kick = await env.client.delete(f"{path}/members/{owner}", headers=_h(env))
    assert owner_kick.status_code == 422
    assert (await env.client.delete(f"{path}/members/nobody", headers=_h(env))).status_code == 404
    kicked = await env.client.delete(f"{path}/members/{admin_im_user_id(admin)}", headers=_h(env))
    assert kicked.status_code == 204
    left = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_conversations WHERE owner_user_id = $1",
        admin,
    )
    assert left == 0

    # 群的版本号是共用的：上面改过名，当前是 2
    current = (await env.client.get(path, headers=env.headers())).json()["policy"]["version"]
    policy = await env.client.put(
        f"{path}/policy",
        json={
            "version": current,
            "relay_enabled": False,
            "max_agent_streak": 2,
            "max_per_minute": 3,
        },
        headers=_h(env),
    )
    assert policy.status_code == 200 and policy.json()["version"] == current + 1
    assert policy.json()["relay_enabled"] is False
    stale = await env.client.put(
        f"{path}/policy",
        json={
            "version": current,
            "relay_enabled": True,
            "max_agent_streak": 2,
            "max_per_minute": 3,
        },
        headers=_h(env),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    await env.app.state.redis.set(STREAK_KEY.format(group_id=gid), "2")
    assert (await env.client.get(path, headers=env.headers())).json()["policy"]["paused"] is True

    # 只有群主能解散
    await env.client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )
    forbidden = await env.client.delete(path, headers=_h(env))
    assert forbidden.status_code == 403
    from tests.helpers import OWNER

    await env.client.post(
        "/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]}
    )
    assert (await env.client.delete(path, headers=_h(env))).status_code == 204
    assert env.fake.groups[gid]["dismissed"] is True
    assert (await env.client.delete(path, headers=_h(env))).status_code == 204
    after = await env.client.patch(path, json={"name": "x"}, headers=_h(env))
    assert after.status_code == 409
    assert (await env.client.get(path, headers=env.headers())).json()["dismissed"] is True


async def test_relay_pause_posts_a_notice_in_the_group(env: ImEnv) -> None:
    a = await env.create_instance()
    group = (await _create(env, {"name": "g", "instance_ids": [a["id"]]})).json()
    gid = group["group_id"]
    await env.client.put(
        f"/api/v1/im/groups/{gid}/policy",
        json={"version": 1, "relay_enabled": True, "max_agent_streak": 1, "max_per_minute": 10},
        headers=_h(env),
    )
    assert await env.verdict(GROUP, msg(a["im_user_id"], group_id=gid)) == 0
    assert await env.verdict(GROUP, msg(a["im_user_id"], group_id=gid)) == 5001
    notices = [b for b in env.fake.paths("/msg/send_msg") if b["sendID"] == "ws_notice"]
    assert len(notices) == 1 and notices[0]["groupID"] == gid
    assert "自动接力已暂停" in notices[0]["content"]["content"]
    # 第二次拒收不再重复提示
    assert await env.verdict(GROUP, msg(a["im_user_id"], group_id=gid)) == 5001
    assert len([b for b in env.fake.paths("/msg/send_msg") if b["sendID"] == "ws_notice"]) == 1


async def test_contacts_and_contact_groups(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙", im_nickname="小乙")
    await env.set_enabled(b["id"], False)
    contacts = (await env.client.get("/api/v1/im/contacts", headers=env.headers())).json()
    by_name = {c["nickname"]: c for c in contacts["items"]}
    assert set(by_name) == {"甲", "小乙"} and by_name["小乙"]["status"] == "disabled"
    owner = admin_im_user_id(env.owner_id)
    assert (
        by_name["甲"]["conversation_id"].startswith("si_")
        and owner in by_name["甲"]["conversation_id"]
    )

    created = await env.client.post(
        "/api/v1/im/contact-groups", json={"name": "运维", "sort_order": 2}, headers=_h(env)
    )
    assert created.status_code == 201
    group = created.json()
    second = await env.client.post(
        "/api/v1/im/contact-groups", json={"name": "开发", "sort_order": 1}, headers=_h(env)
    )
    duplicate = await env.client.post(
        "/api/v1/im/contact-groups", json={"name": "运维"}, headers=_h(env)
    )
    assert (
        duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "CONTACT_GROUP_EXISTS"
    )
    renamed = await env.client.patch(
        f"/api/v1/im/contact-groups/{second.json()['id']}",
        json={"version": 1, "name": "运维"},
        headers=_h(env),
    )
    assert renamed.status_code == 409 and renamed.json()["error"]["code"] == "CONTACT_GROUP_EXISTS"

    members_path = f"/api/v1/im/contact-groups/{group['id']}/members"
    replaced = await env.client.put(
        members_path, json={"version": 1, "instance_ids": [a["id"], a["id"]]}, headers=_h(env)
    )
    assert replaced.status_code == 200 and replaced.json()["instance_ids"] == [a["id"]]
    stale = await env.client.put(
        members_path, json={"version": 1, "instance_ids": []}, headers=_h(env)
    )
    assert stale.status_code == 409
    unknown = await env.client.put(
        members_path, json={"version": 2, "instance_ids": [str(uuid.uuid4())]}, headers=_h(env)
    )
    assert unknown.status_code == 404

    listed = (await env.client.get("/api/v1/im/contact-groups", headers=env.headers())).json()
    assert [g["name"] for g in listed["items"]] == ["开发", "运维"]
    filtered = await env.client.get(
        "/api/v1/im/contacts", params={"contact_group_id": group["id"]}, headers=env.headers()
    )
    assert [c["nickname"] for c in filtered.json()["items"]] == ["甲"]
    assert filtered.json()["items"][0]["contact_group_ids"] == [group["id"]]
    searched = await env.client.get(
        "/api/v1/im/contacts", params={"q": "小乙"}, headers=env.headers()
    )
    assert [c["nickname"] for c in searched.json()["items"]] == ["小乙"]

    # 会话列表按分组筛选
    for instance in (a,):
        await env.client.post(
            "/api/v1/im/conversations", json={"instance_id": instance["id"]}, headers=_h(env)
        )
    conversations = await env.client.get(
        "/api/v1/im/conversations", params={"contact_group_id": group["id"]}, headers=env.headers()
    )
    assert [c["title"] for c in conversations.json()["items"]] == ["甲"]

    stale_delete = await env.client.delete(
        f"/api/v1/im/contact-groups/{group['id']}", params={"version": 1}, headers=_h(env)
    )
    assert stale_delete.status_code == 409
    deleted = await env.client.delete(
        f"/api/v1/im/contact-groups/{group['id']}", params={"version": 2}, headers=_h(env)
    )
    assert deleted.status_code == 204
    after = (await env.client.get("/api/v1/im/contacts", headers=env.headers())).json()
    assert len(after["items"]) == 2  # 删除分组不影响实例
