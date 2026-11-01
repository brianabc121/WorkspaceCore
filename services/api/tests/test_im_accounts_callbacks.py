"""IM 账号、IM 令牌与 OpenIM 回调（T-0055，OC-3）。真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖 V2 契约（docs/decisions/2026-10-31-v2-oc1-contract.md）：
- 第 5.1 节：登记实例时注册 oc_ 账号，与 owner / admin（ws_ 账号按需注册）互加好友，im_ready；
  OpenIM 不可用时 im_ready 保持 false，由 Worker 重试；停用与总开关强制下线；
- 第 7 节：/agent-api/v1/im-token（no-store、每小时 10 次、没有 OpenIM 时 503 IM_UNAVAILABLE）；
- 第 10 节：回调的地址口令与来源网段、before 回调的拒收码 5001 ~ 5006 与 5009、
  after 回调写收件箱并去重、其他命令放行。
"""

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.domain import im as im_domain
from app.domain.im import admin_im_user_id
from app.domain.im_callbacks import IGNORED_KEY, STREAK_KEY
from app.jobs import worker
from tests import fake_openim
from tests.fake_openim import FakeOpenIM
from tests.helpers import OWNER, ClientFactory, add_member, add_user, add_workspace, run_sql
from tests.test_agents_api import SETTINGS, Env, agent_headers

pytestmark = pytest.mark.integration

PATH_TOKEN = secrets.token_urlsafe(32)
IM_SETTINGS: dict[str, Any] = {
    **SETTINGS,
    "openim_api_url": fake_openim.BASE_URL,
    "openim_secret": SecretStr(fake_openim.SECRET),
    "openim_callback_path_token": SecretStr(PATH_TOKEN),
    # 测试客户端的对端地址是 127.0.0.1，当作 OpenIM 所在的网络
    "openim_callback_allowed_cidrs": "127.0.0.1/32",
}


class ImEnv(Env):
    fake: FakeOpenIM
    owner_id: uuid.UUID

    async def callback(
        self, command: str, payload: Any, *, token: str = PATH_TOKEN
    ) -> httpx.Response:
        return await self.client.post(
            f"/internal/openim/{token}/{command}",
            content=payload if isinstance(payload, bytes) else json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    async def verdict(self, command: str, payload: dict[str, Any]) -> int:
        """before 回调的结果：0 放行，否则是拒收码。"""
        response = await self.callback(command, payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"actionCode", "errCode", "errMsg", "errDlt", "nextCode"}
        assert body["nextCode"] == (1 if body["errCode"] else 0)
        code: int = body["errCode"]
        return code

    async def set_enabled(self, instance_id: str, enabled: bool) -> None:
        current = await self.instance(instance_id)
        response = await self.client.post(
            f"/api/v1/agents/instances/{instance_id}/{'enable' if enabled else 'disable'}",
            json={"version": current["version"]},
            headers=self.headers(write=True),
        )
        assert response.status_code == 200, response.text

    async def kill_switch(self, enabled: bool) -> None:
        current = (await self.client.get("/api/v1/agents/settings", headers=self.headers())).json()
        response = await self.client.patch(
            "/api/v1/agents/settings",
            json={"version": current["version"], "agents_enabled": enabled},
            headers=self.headers(write=True),
        )
        assert response.status_code == 200, response.text


@asynccontextmanager
async def im_env(
    make_client: ClientFactory, database_url: str, *, setup: bool = True, **overrides: Any
) -> AsyncIterator[ImEnv]:
    """setup=False 用于同一个数据库上的第二个应用（只测回调，不再首次安装）。"""
    fake = FakeOpenIM()
    async with make_client(**{**IM_SETTINGS, **overrides}) as client:
        env = ImEnv(client, "", database_url)
        env.fake = fake
        env.app.state.connectors.transport = httpx.MockTransport(fake.handler)
        if setup:
            response = await client.post("/api/v1/setup", json=OWNER)
            assert response.status_code == 201, response.text
            env.workspace_id = response.json()["workspaces"][0]["id"]
            me = await client.get("/api/v1/me")
            env.owner_id = uuid.UUID(me.json()["user"]["id"])
        yield env


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


def msg(
    send_id: str, *, recv_id: str = "", group_id: str = "", at: list[str] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "callbackCommand": "x",
        "sendID": send_id,
        "recvID": recv_id,
        "groupID": group_id,
        "serverMsgID": uuid.uuid4().hex,
        "clientMsgID": uuid.uuid4().hex,
        "sessionType": 3 if group_id else 1,
        "contentType": 106 if at else 101,
        "senderPlatformID": 5,
        "seq": 0,
        "sendTime": 1700000000000,
    }
    if at is not None:
        payload["atUserList"] = at
    return payload


SINGLE = "callbackBeforeSendSingleMsgCommand"
GROUP = "callbackBeforeSendGroupMsgCommand"


# ---------------------------------------------------------------- 账号与好友


async def test_instance_gets_an_im_account_and_befriends_the_admins(env: ImEnv) -> None:
    admin_id = await add_user(env.database_url, "admin@example.com", "admin password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin_id, "admin")
    member_id = await add_user(env.database_url, "member@example.com", "member password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member_id, "member")

    instance = await env.create_instance(im_nickname="小助手")
    assert instance["im_ready"] is True
    oc = instance["im_user_id"]
    owner_im, admin_im = admin_im_user_id(env.owner_id), admin_im_user_id(admin_id)
    assert env.fake.users[oc] == "小助手"
    assert env.fake.befriended(oc, owner_im) and env.fake.befriended(oc, admin_im)
    # 普通成员不能用 IM：不注册、不加好友
    assert admin_im_user_id(member_id) not in env.fake.users
    accounts = await run_sql(env.database_url, "SELECT count(*) FROM workspace.im_accounts")
    assert accounts == 2

    # 第二个实例：管理员账号已登记，不再注册；只加新的好友
    registers = len(env.fake.paths("/user/user_register"))
    second = await env.create_instance(name="实例 B")
    assert second["im_ready"] is True
    assert len(env.fake.paths("/user/user_register")) == registers + 1
    assert env.fake.befriended(second["im_user_id"], owner_im)


async def test_provisioning_is_retried_when_openim_is_down(env: ImEnv) -> None:
    env.fake.down = True
    instance = await env.create_instance()
    assert instance["im_ready"] is False
    # Worker 定时任务：OpenIM 仍不可用时保持原样
    ctx = await _worker_ctx(env)
    assert await worker.provision_im(ctx) == 0
    env.fake.down = False
    assert await worker.provision_im(ctx) == 1
    assert (await env.instance(instance["id"]))["im_ready"] is True
    assert env.fake.befriended(instance["im_user_id"], admin_im_user_id(env.owner_id))
    # 已就绪与停用的实例不再处理
    disabled = await env.create_instance(name="停用的")
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET im_ready = false, status = 'disabled' WHERE id = $1",
        uuid.UUID(disabled["id"]),
    )
    assert await worker.provision_im(ctx) == 0


async def _worker_ctx(env: ImEnv) -> dict[str, Any]:
    from app.domain import sync

    app = env.app
    return {
        "redis": app.state.redis,
        "sync": sync.SyncContext(
            sessions=app.state.sessionmaker,
            runtime=app.state.connectors,
            redis=app.state.redis,
            settings=app.state.settings,
        ),
    }


async def test_disable_and_kill_switch_force_logout(env: ImEnv) -> None:
    first = await env.create_instance()
    second = await env.create_instance(name="实例 B")
    await env.set_enabled(first["id"], False)
    assert env.fake.logged_out == [first["im_user_id"]]
    await env.kill_switch(False)
    # 已停用的不再重复下线
    assert env.fake.logged_out == [first["im_user_id"], second["im_user_id"]]
    # OpenIM 不可用时停用照常生效
    await env.kill_switch(True)
    env.fake.down = True
    await env.set_enabled(second["id"], False)
    assert (await env.instance(second["id"]))["status"] == "disabled"


# ---------------------------------------------------------------- IM 令牌


async def test_im_token_is_issued_at_startup_and_rate_limited(env: ImEnv) -> None:
    instance = await env.create_instance()
    token = await env.issue_token(instance["id"])
    response = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["im_user_id"] == instance["im_user_id"] and body["platform_id"] == 5
    assert body["token"].startswith("user-token-")
    assert body["expires_at"] is not None and body["ws_addr"] is None and body["api_addr"] is None
    assert env.fake.tokens_issued == [instance["im_user_id"]]

    unauthenticated = await env.client.post("/agent-api/v1/im-token")
    assert unauthenticated.status_code == 401
    for _ in range(9):
        assert (
            await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
        ).status_code == 200
    limited = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0


async def test_im_token_provisions_first_and_reports_unavailable(env: ImEnv) -> None:
    env.fake.down = True
    instance = await env.create_instance()
    token = await env.issue_token(instance["id"])
    down = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
    assert down.status_code == 503 and down.json()["error"]["code"] == "IM_UNAVAILABLE"
    env.fake.down = False
    up = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
    assert up.status_code == 200
    assert (await env.instance(instance["id"]))["im_ready"] is True


async def test_without_openim_instances_work_and_im_token_is_unavailable(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with im_env(
        make_client, migrated_database_url, openim_api_url="", openim_secret=None
    ) as env:
        instance = await env.create_instance()
        assert instance["im_ready"] is False
        token = await env.issue_token(instance["id"])
        response = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
        assert response.status_code == 503 and response.json()["error"]["code"] == "IM_UNAVAILABLE"
        # 回调地址也不存在
        assert (await env.callback(SINGLE, msg("oc_x", recv_id="ws_y"))).status_code == 404
        assert env.fake.calls == []


# ---------------------------------------------------------------- 回调：地址与来源


async def test_callbacks_require_the_path_token_and_the_openim_network(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with im_env(make_client, migrated_database_url, setup=False) as env:
        wrong = await env.callback(SINGLE, msg("oc_x", recv_id="ws_y"), token="x" * 43)
        assert wrong.status_code == 404
        assert (await env.callback(SINGLE, msg("oc_x", recv_id="ws_y"))).status_code == 200
    async with im_env(
        make_client,
        migrated_database_url,
        setup=False,
        openim_callback_allowed_cidrs="10.9.0.0/16",
    ) as env:
        assert (await env.callback(SINGLE, msg("oc_x", recv_id="ws_y"))).status_code == 404
    async with im_env(
        make_client, migrated_database_url, setup=False, openim_callback_path_token=None
    ) as env:
        assert (await env.callback(SINGLE, msg("oc_x", recv_id="ws_y"))).status_code == 404


# ---------------------------------------------------------------- 回调：单聊


async def test_single_chat_is_only_between_admins_and_their_instances(env: ImEnv) -> None:
    a = await env.create_instance()
    b = await env.create_instance(name="实例 B")
    owner = admin_im_user_id(env.owner_id)
    oc_a, oc_b = a["im_user_id"], b["im_user_id"]
    assert await env.verdict(SINGLE, msg(oc_a, recv_id=owner)) == 0
    assert await env.verdict(SINGLE, msg(owner, recv_id=oc_a)) == 0
    assert await env.verdict(SINGLE, msg("imAdmin", recv_id=oc_a)) == 0
    assert await env.verdict(SINGLE, msg("ws_notice", recv_id=owner)) == 0
    assert await env.verdict(SINGLE, msg(oc_a, recv_id=oc_b)) == 5003

    # 跨工作空间：另一个工作空间的 owner 与实例
    stranger = await add_user(env.database_url, "other@example.com", "other password 123456")
    await add_workspace(env.database_url, stranger, "别人的")  # 同时成为那里的 owner
    assert await env.verdict(SINGLE, msg(oc_a, recv_id=admin_im_user_id(stranger))) == 5005
    assert await env.verdict(SINGLE, msg(admin_im_user_id(stranger), recv_id=oc_a)) == 5005
    # 普通成员、未知账号、格式不对的 ID
    member = await add_user(env.database_url, "member@example.com", "member password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member, "member")
    assert await env.verdict(SINGLE, msg(oc_a, recv_id=admin_im_user_id(member))) == 5005
    assert await env.verdict(SINGLE, msg(f"oc_{uuid.uuid4().hex}", recv_id=owner)) == 5005
    assert await env.verdict(SINGLE, msg(oc_a, recv_id="ws_notice")) == 5005
    assert await env.verdict(SINGLE, msg("someone", recv_id=oc_a)) == 5005

    await env.set_enabled(a["id"], False)
    assert await env.verdict(SINGLE, msg(oc_a, recv_id=owner)) == 5004
    await env.kill_switch(False)
    assert await env.verdict(SINGLE, msg(oc_b, recv_id=owner)) == 5004


async def test_invalid_payload_is_rejected_and_policy_errors_fail_closed(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await env.verdict(SINGLE, {"sendID": "oc_x"}) == 5005
    response = await env.callback(SINGLE, b"not json")
    assert response.json()["errCode"] == 5005
    big = await env.callback(SINGLE, b"{" + b" " * (256 * 1024) + b"}")
    assert big.json()["errCode"] == 5005

    async def broken(self: object, message: object) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.domain.im_callbacks.CallbackJudge.judge", broken)
    owner = admin_im_user_id(env.owner_id)
    response = await env.callback(SINGLE, msg("oc_x", recv_id=owner))
    assert response.json() == {
        "actionCode": 0,
        "errCode": 5009,
        "errMsg": "POLICY_UNAVAILABLE",
        "errDlt": "",
        "nextCode": 1,
    }


# ---------------------------------------------------------------- 回调：群聊


async def _group(
    env: ImEnv,
    members: list[tuple[str, str | None, uuid.UUID | None]],
    *,
    relay: bool = True,
    streak: int = 8,
    per_minute: int = 6,
) -> str:
    group_id = uuid.uuid4().hex[:10]
    pk = await run_sql(
        env.database_url,
        "INSERT INTO workspace.im_groups (workspace_id, group_id, name, owner_user_id,"
        " relay_enabled, max_agent_streak, max_per_minute)"
        " VALUES ($1, $2, '项目群', $3, $4, $5, $6) RETURNING id",
        uuid.UUID(env.workspace_id),
        group_id,
        env.owner_id,
        relay,
        streak,
        per_minute,
    )
    for im_user_id, instance_id, user_id in members:
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.im_group_members (group_pk, im_user_id, instance_id, user_id)"
            " VALUES ($1, $2, $3, $4)",
            pk,
            im_user_id,
            uuid.UUID(instance_id) if instance_id else None,
            user_id,
        )
    return group_id


async def test_group_relay_streak_rate_and_mentions(env: ImEnv) -> None:
    a = await env.create_instance()
    b = await env.create_instance(name="实例 B")
    outsider = await env.create_instance(name="不在群里")
    owner = admin_im_user_id(env.owner_id)
    oc_a, oc_b = a["im_user_id"], b["im_user_id"]
    members = [(oc_a, a["id"], None), (oc_b, b["id"], None), (owner, None, env.owner_id)]
    group = await _group(env, members, streak=3, per_minute=10)

    assert await env.verdict(GROUP, msg(outsider["im_user_id"], group_id=group)) == 5005
    assert await env.verdict(GROUP, msg(oc_a, group_id="unknown")) == 5005
    # 实例连续发言：第 4 条拒收 5001，只通知一次
    for sender in (oc_a, oc_b, oc_a):
        assert await env.verdict(GROUP, msg(sender, group_id=group)) == 0
    assert await env.verdict(GROUP, msg(oc_b, group_id=group)) == 5001
    assert await env.verdict(GROUP, msg(oc_a, group_id=group)) == 5001
    notices = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.notifications WHERE type = 'im_relay_paused'",
    )
    assert notices == 1
    # 运维指标：每次暂停计一次（T-0062）
    assert int(await env.app.state.redis.hget("ops:counter:im_relay_pauses", "_")) == 1
    # 系统消息不清零；管理员发言后恢复
    assert await env.verdict(GROUP, msg("ws_notice", group_id=group)) == 0
    assert await env.verdict(GROUP, msg(oc_a, group_id=group)) == 5001
    assert await env.verdict(GROUP, msg(owner, group_id=group)) == 0
    assert await env.app.state.redis.get(STREAK_KEY.format(group_id=group)) is None
    assert await env.verdict(GROUP, msg(oc_a, group_id=group)) == 0

    # 每分钟条数
    rated = await _group(env, members, streak=50, per_minute=2)
    assert await env.verdict(GROUP, msg(oc_a, group_id=rated)) == 0
    assert await env.verdict(GROUP, msg(oc_a, group_id=rated)) == 0
    assert await env.verdict(GROUP, msg(oc_a, group_id=rated)) == 5002
    assert await env.verdict(GROUP, msg(oc_b, group_id=rated)) == 0

    # 关闭接力的群：实例 @ 其他实例拒收；@ 管理员或只 @ 自己放行
    quiet = await _group(env, members, relay=False)
    assert await env.verdict(GROUP, msg(oc_a, group_id=quiet, at=[oc_b])) == 5006
    assert await env.verdict(GROUP, msg(oc_a, group_id=quiet, at=[owner])) == 0
    assert await env.verdict(GROUP, msg(owner, group_id=quiet, at=[oc_a, oc_b])) == 0

    # 停用的实例、解散的群
    await env.set_enabled(b["id"], False)
    assert await env.verdict(GROUP, msg(oc_b, group_id=quiet)) == 5004
    await run_sql(
        env.database_url,
        "UPDATE workspace.im_groups SET dismissed_at = now() WHERE group_id = $1",
        quiet,
    )
    assert await env.verdict(GROUP, msg(owner, group_id=quiet)) == 5005


async def test_downgraded_admin_cannot_speak_in_groups(env: ImEnv) -> None:
    a = await env.create_instance()
    admin_id = await add_user(env.database_url, "admin@example.com", "admin password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin_id, "admin")
    admin_im = admin_im_user_id(admin_id)
    group = await _group(env, [(a["im_user_id"], a["id"], None), (admin_im, None, admin_id)])
    assert await env.verdict(GROUP, msg(admin_im, group_id=group)) == 0
    await run_sql(
        env.database_url,
        "UPDATE workspace.workspace_members SET role = 'member' WHERE user_id = $1",
        admin_id,
    )
    assert await env.verdict(GROUP, msg(admin_im, group_id=group)) == 5005
    assert await env.verdict(SINGLE, msg(admin_im, recv_id=a["im_user_id"])) == 5005


# ---------------------------------------------------------------- 回调：after → 收件箱


async def test_after_callbacks_go_to_the_inbox_once(env: ImEnv) -> None:
    message = {
        **msg("oc_a", recv_id="ws_b"),
        "callbackCommand": "callbackAfterSendSingleMsgCommand",
    }
    for _ in range(2):
        response = await env.callback("callbackAfterSendSingleMsgCommand", message)
        assert response.json()["errCode"] == 0
    for command in ("callbackAfterUserOnlineCommand", "callbackAfterUserOfflineCommand"):
        online = {"callbackCommand": command, "userID": "oc_a", "platformID": 5}
        await env.callback(command, {**online, "seq": 1700000000123})
    await env.callback(
        "callbackAfterDeleteFriendCommand",
        {
            "callbackCommand": "callbackAfterDeleteFriendCommand",
            "ownerUserID": "oc_a",
            "friendUserID": "ws_b",
        },
    )
    await env.callback(
        "callbackAfterImportFriendsCommand",
        {
            "callbackCommand": "callbackAfterImportFriendsCommand",
            "ownerUserID": "oc_a",
            "friendUserIDs": ["ws_b"],
        },
    )
    ignored = await env.callback("callbackAfterSetGroupInfoCommand", {"groupID": "g"})
    assert ignored.json()["errCode"] == 0
    # 缺字段、不是 JSON：放行，不入库
    await env.callback("callbackAfterSendGroupMsgCommand", {"sendID": "oc_a"})
    await env.callback("callbackAfterUserOnlineCommand", b"[]")

    keys = await run_sql(
        env.database_url,
        "SELECT string_agg(split_part(event_key, ':', 1) || '|' || command || '|' || status,"
        " ',' ORDER BY event_key) FROM workspace.im_webhook_inbox",
    )
    assert keys.split(",") == [
        "delete_friend|callbackAfterDeleteFriendCommand|pending",
        "msg|callbackAfterSendSingleMsgCommand|pending",
        "offline|callbackAfterUserOfflineCommand|pending",
        "online|callbackAfterUserOnlineCommand|pending",
    ]
    exact = await run_sql(
        env.database_url,
        "SELECT event_key FROM workspace.im_webhook_inbox WHERE command = $1",
        "callbackAfterUserOnlineCommand",
    )
    assert exact == "online:oc_a:5:1700000000123"
    counted = await env.app.state.redis.hget(IGNORED_KEY, "callbackAfterSetGroupInfoCommand")
    assert int(counted) == 1
    assert im_domain.NOTICE_USER_ID == "ws_notice"
