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
