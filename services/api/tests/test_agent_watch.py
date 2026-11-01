"""OpenClaw 实例的周期检查（T-0063；方案第 4.1、4.3、4.4、6.1 节，契约第 11 节的通知）。

真实 PostgreSQL + Redis，模拟 OpenIM：
- 在线状态对账：按 OpenIM 的实时状态纠正 online / offline；离线超过 10 分钟才提醒，
  每次离线只提醒一次；停用后又连上来的实例再踢一次；
- 好友关系对账：缺了就补；
- 到期提醒：IM 令牌（签发时记下到期时间）与客户端证书。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.domain import agent_watch, im
from app.domain.agent_watch import OFFLINE_NOTIFIED_KEY, OFFLINE_SINCE_KEY
from app.jobs import worker
from tests.helpers import ClientFactory, run_sql
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import ImEnv, _worker_ctx, im_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def _status(env: ImEnv, instance_id: str) -> str:
    status: str = (await env.instance(instance_id))["status"]
    return status


async def _count(env: ImEnv, type_: str) -> int:
    count: int = await run_sql(
        env.database_url, "SELECT count(*) FROM workspace.notifications WHERE type = $1", type_
    )
    return count


async def test_presence_follows_openim_and_long_offline_is_notified_once(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    ctx = await _worker_ctx(env)
    assert await _status(env, a["id"]) == "pending"

    # 上线回调丢了：对账按 OpenIM 的实时状态改为在线
    env.fake.online = {a["im_user_id"]}
    assert await worker.watch_agents(ctx) == 1
    assert await _status(env, a["id"]) == "online"
    assert await _status(env, b["id"]) == "pending"  # 没连过的保持“待接入”

    # 下线回调也丢了：改为离线；刚离线不提醒（每周重启、冷启动都在几分钟内）
    env.fake.online = set()
    assert await worker.watch_agents(ctx) == 1
    assert await _status(env, a["id"]) == "offline"
    assert await _count(env, "agent_offline") == 0

    # 离线超过 10 分钟：提醒 owner 一次，之后的对账不再重复提醒
    redis = env.app.state.redis
    key = OFFLINE_SINCE_KEY.format(instance_id=a["id"])
    await redis.set(key, (datetime.now(UTC) - timedelta(minutes=11)).isoformat())
    assert await worker.watch_agents(ctx) == 0
    assert await _count(env, "agent_offline") == 1
    assert await worker.watch_agents(ctx) == 0
    assert await _count(env, "agent_offline") == 1

    # 重新上线：清掉离线记录，下次离线重新计时
    env.fake.online = {a["im_user_id"]}
    assert await worker.watch_agents(ctx) == 1
    assert await redis.get(key) is None

    # 停用后又连上来：再踢一次，状态保持“已停用”
    await env.set_enabled(b["id"], False)
    env.fake.logged_out.clear()
    env.fake.online = {a["im_user_id"], b["im_user_id"]}
    await worker.watch_agents(ctx)
    assert b["im_user_id"] in env.fake.logged_out
    assert await _status(env, b["id"]) == "disabled"

    # OpenIM 不可用：对账不报错，状态不变
    env.fake.down = True
    assert await worker.watch_agents(ctx) == 0
    assert await _status(env, a["id"]) == "online"


async def test_disabling_during_the_reconcile_is_not_overwritten(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """查询 OpenIM 期间管理员停用了实例：对账不能把 disabled 覆盖成 online / offline。"""
    a = await env.create_instance(name="甲")
    ctx = await _worker_ctx(env)
    env.fake.online = {a["im_user_id"]}
    assert await worker.watch_agents(ctx) == 1
    assert await _status(env, a["id"]) == "online"

    real = agent_watch._online_users

    async def disable_meanwhile(client: object, ids: list[str]) -> set[str]:
        online = await real(client, ids)  # type: ignore[arg-type]
        await env.set_enabled(a["id"], False)  # 在对账读出实例之后、提交之前停用
        return online

    monkeypatch.setattr(agent_watch, "_online_users", disable_meanwhile)
    env.fake.online = set()  # 停用时踢下线，对账看到的是“离线”
    assert await worker.watch_agents(ctx) == 0
    assert await _status(env, a["id"]) == "disabled"


async def test_offline_notice_is_retried_when_the_transaction_fails(env: ImEnv) -> None:
    """离线提醒在事务里写入；提交失败（这里回滚）时不记“已提醒”，下一轮重新提醒。"""
    a = await env.create_instance(name="甲")
    ctx = await _worker_ctx(env)
    env.fake.online = {a["im_user_id"]}
    await worker.watch_agents(ctx)
    env.fake.online = set()
    await worker.watch_agents(ctx)
    redis = env.app.state.redis
    await redis.set(
        OFFLINE_SINCE_KEY.format(instance_id=a["id"]),
        (datetime.now(UTC) - timedelta(minutes=11)).isoformat(),
    )
    app = env.app
    client = im.openim_client(app.state.settings, app.state.connectors, redis)
    assert client is not None
    async with app.state.sessionmaker() as db:
        result = await agent_watch.reconcile_presence(db, im.ImAccounts(db, client), redis)
        assert len(result.notified) == 1
        await db.rollback()
    assert await redis.get(OFFLINE_NOTIFIED_KEY.format(instance_id=a["id"])) is None
    assert await _count(env, "agent_offline") == 0
    # 下一轮正常提交：提醒一次，并记下“已提醒”
    await worker.watch_agents(ctx)
    assert await _count(env, "agent_offline") == 1
    assert await redis.get(OFFLINE_NOTIFIED_KEY.format(instance_id=a["id"])) is not None


async def test_missing_friendships_are_restored_hourly(env: ImEnv) -> None:
    a = await env.create_instance()
    owner_im = "ws_" + env.owner_id.hex
    assert env.fake.befriended(a["im_user_id"], owner_im)
    ctx = await _worker_ctx(env)
    assert await worker.reconcile_im_friends(ctx) == 0

    # 绕过工作台删掉了好友、删好友的回调也丢了：每小时的对账补回
    env.fake.unfriend(a["im_user_id"], owner_im)
    assert await worker.reconcile_im_friends(ctx) == 1
    assert env.fake.befriended(a["im_user_id"], owner_im)


async def test_expiring_im_tokens_and_client_certs_are_reported(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    token = await env.issue_token(a["id"])
    issued = await env.client.post("/agent-api/v1/im-token", headers=agent_headers(token))
    assert issued.status_code == 200, issued.text
    # 签发时记下到期时间（模拟的 OpenIM 返回 30 天）
    expires = datetime.fromisoformat((await env.instance(a["id"]))["im_token_expires_at"])
    assert timedelta(days=29) < expires - datetime.now(UTC) <= timedelta(days=30)
    ctx = await _worker_ctx(env)
    assert await worker.warn_agent_expiry(ctx) == 0

    # 每周重启没生效：令牌 3 天后到期；客户端证书 5 天后到期
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET im_token_expires_at = now() + interval '3 days'"
        " WHERE id = $1",
        uuid.UUID(a["id"]),
    )
    await env.add_cert(a["id"], "ab" * 32)
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_credentials SET expires_at = now() + interval '5 days'"
        " WHERE instance_id = $1 AND kind = 'client_cert'",
        uuid.UUID(a["id"]),
    )
    assert await worker.warn_agent_expiry(ctx) == 2
    assert await _count(env, "agent_im_token_expiring") == 1
    assert await _count(env, "agent_credential_expiring") == 1
    # 同一天再检查不重复提醒
    assert await worker.warn_agent_expiry(ctx) == 0

    # 停用的实例不提醒
    await run_sql(env.database_url, "DELETE FROM workspace.notifications")
    await env.set_enabled(a["id"], False)
    assert await worker.warn_agent_expiry(ctx) == 0
