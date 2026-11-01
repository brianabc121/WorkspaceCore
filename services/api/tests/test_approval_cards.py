"""IM 审批卡片（T-0057，OC-5 第 3 批后半；ADR 第 11 节）：真实 PostgreSQL / Redis + 内存版 OpenIM。

覆盖：新的待审批投递发卡片的作业；工作空间的通知账号按通知账号注册（AppMangerLevel 3）；
卡片是 OA 通知（1400），会话 sn_，同一个人只发一次；投影把它识别为 approval_card、带记录 ID；
通知会话只出现在所属工作空间；不能往通知会话发消息；已处理的记录不再发卡片。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain import approval_cards, im
from app.domain.ai_tools import AiToolService
from app.domain.im import admin_im_user_id, workspace_notice_im_id
from app.integrations.openim import notice_conversation_id
from app.jobs import worker
from app.jobs.queue import SEND_APPROVAL_CARDS
from app.models import AgentInstance
from app.schemas.ai_tools import TOOLS
from tests.helpers import ClientFactory, add_workspace, run_sql
from tests.test_im_accounts_callbacks import ImEnv, _worker_ctx, im_env, msg

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as value:
        yield value


class Recording:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue(self, function: str, *args: Any) -> None:
        self.jobs.append((function, args))


async def request_bookmark(env: ImEnv, instance_id: str, jobs: Recording, url: str) -> uuid.UUID:
    app = env.app
    async with app.state.sessionmaker() as db:
        service = AiToolService(
            db, app.state.settings, app.state.connectors, app.state.redis, jobs=jobs
        )
        instance = await db.get(AgentInstance, uuid.UUID(instance_id))
        assert instance is not None
        pending = await service.request(
            await service.requester(instance), TOOLS["create_bookmark"], {"url": url, "title": "x"}
        )
    return pending.action_id


async def send(env: ImEnv, execution_id: uuid.UUID) -> int:
    app = env.app
    client = im.openim_client(app.state.settings, app.state.connectors, app.state.redis)
    assert client is not None
    async with app.state.sessionmaker() as db:
        return await approval_cards.send_cards(db, client, app.state.redis, execution_id)


async def test_new_approvals_send_one_card_per_admin(env: ImEnv) -> None:
    instance = await env.create_instance(scope="read_write")
    jobs = Recording()
    action_id = await request_bookmark(env, instance["id"], jobs, "https://a.test/1")
    assert jobs.jobs == [(SEND_APPROVAL_CARDS, (str(action_id),))]
    # 去重返回的同一条不再投递
    assert await request_bookmark(env, instance["id"], jobs, "https://a.test/1") == action_id
    assert len(jobs.jobs) == 1

    assert await send(env, action_id) == 1
    notice = workspace_notice_im_id(uuid.UUID(env.workspace_id))
    assert env.fake.levels[notice] == 3  # 注册为通知账号
    owner_im = admin_im_user_id(env.owner_id)
    conv = notice_conversation_id(notice, owner_im)
    [card] = env.fake.messages[conv]
    assert (card["contentType"], card["sessionType"]) == (1400, 4)
    assert str(action_id) in card["ex"]
    # 重试不会重复发
    assert await send(env, action_id) == 0
    assert len(env.fake.messages[conv]) == 1

    # 投影：通知会话出现在会话列表里，消息是审批卡片
    await worker.reconcile_im(await _worker_ctx(env))
    listed = await env.client.get(
        "/api/v1/im/conversations", params={"kind": "notice"}, headers=env.headers()
    )
    assert listed.status_code == 200, listed.text
    [conversation] = listed.json()["items"]
    assert (conversation["conversation_id"], conversation["title"]) == (conv, "审批通知")
    assert conversation["last_message"]["kind"] == "approval_card"
    messages = await env.client.get(
        f"/api/v1/im/conversations/{conv}/messages", headers=env.headers()
    )
    [message] = messages.json()["items"]
    assert message["kind"] == "approval_card"
    assert message["approval_execution_id"] == str(action_id)
    assert "新建书签" in message["text"] and "15 分钟内" in message["text"]
    # 通知会话不能发消息
    sent = await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "text", "text": "同意"},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert sent.status_code == 409 and sent.json()["error"]["code"] == "IM_SEND_REJECTED"


async def test_cards_stay_in_their_workspace(env: ImEnv) -> None:
    other = await add_workspace(env.database_url, env.owner_id, "另一个工作空间")
    instance = await env.create_instance(scope="read_write")
    action_id = await request_bookmark(env, instance["id"], Recording(), "https://a.test/2")
    assert await send(env, action_id) == 1
    await worker.reconcile_im(await _worker_ctx(env))
    # 同一个人在另一个工作空间里看不到这个工作空间的通知会话
    elsewhere = await env.client.get(
        "/api/v1/im/conversations",
        params={"kind": "notice"},
        headers={"X-Workspace-Id": str(other)},
    )
    assert elsewhere.status_code == 200, elsewhere.text
    assert elsewhere.json()["items"] == []
    here = await env.client.get(
        "/api/v1/im/conversations", params={"kind": "notice"}, headers=env.headers()
    )
    assert len(here.json()["items"]) == 1


async def test_processed_records_get_no_card(env: ImEnv) -> None:
    instance = await env.create_instance(scope="read_write")
    action_id = await request_bookmark(env, instance["id"], Recording(), "https://a.test/3")
    await run_sql(
        env.database_url,
        "UPDATE workspace.ai_tool_executions SET status = 'rejected' WHERE id = $1",
        action_id,
    )
    assert await send(env, action_id) == 0
    assert not [c for c in env.fake.messages if c.startswith("sn_")]


def test_card_ex_round_trip() -> None:
    execution_id = uuid.uuid4()
    assert approval_cards.execution_id_from_ex(approval_cards.card_ex(execution_id)) == execution_id
    for bad in ("", "x", '{"kind": "other"}', '{"kind": "approval_card", "execution_id": "no"}'):
        assert approval_cards.execution_id_from_ex(bad) is None


async def test_after_send_callback_projects_the_notice_conversation(env: ImEnv) -> None:
    instance = await env.create_instance(scope="read_write")
    action_id = await request_bookmark(env, instance["id"], Recording(), "https://a.test/4")
    assert await send(env, action_id) == 1
    notice = workspace_notice_im_id(uuid.UUID(env.workspace_id))
    owner_im = admin_im_user_id(env.owner_id)
    # OpenIM 对 OA 通知同样调用“单聊发送后”回调，sessionType 是 4
    payload = msg(notice, recv_id=owner_im)
    payload.update({"sessionType": 4, "contentType": 1400})
    response = await env.callback("callbackAfterSendSingleMsgCommand", payload)
    assert response.status_code == 200, response.text
    assert await worker.process_im_inbox(await _worker_ctx(env)) == 1
    conv = notice_conversation_id(notice, owner_im)
    messages = await env.client.get(
        f"/api/v1/im/conversations/{conv}/messages", headers=env.headers()
    )
    assert [m["approval_execution_id"] for m in messages.json()["items"]] == [str(action_id)]


def test_only_workspace_notice_accounts_produce_cards() -> None:
    import base64
    import json

    from app.domain.im_projection import parse_message

    execution_id = uuid.uuid4()
    detail = json.dumps({"text": "「alpha」请求：删除任务", "notificationType": 1})
    raw = {
        "seq": 1,
        "serverMsgID": "s1",
        "clientMsgID": "c1",
        "contentType": 1400,
        "sendTime": 1700000000000,
        "content": base64.b64encode(json.dumps({"detail": detail}).encode()).decode(),
        "ex": approval_cards.card_ex(execution_id),
    }
    card = parse_message({**raw, "sendID": workspace_notice_im_id(uuid.uuid4())})
    assert card is not None and card.content == {"approval_execution_id": str(execution_id)}
    assert card.plain_text == "「alpha」请求：删除任务"
    # 别的账号（例如 imAdmin 或实例）发的同样内容不算审批卡片
    for sender in ("imAdmin", f"oc_{uuid.uuid4().hex}", "ws_notice"):
        other = parse_message({**raw, "sendID": sender})
        assert other is not None and other.content == {}


def test_card_text_names_who_triggered_it() -> None:
    from app.models import AiToolExecution

    row = AiToolExecution(instance_name="甲", summary="删除任务“写周报”", risk_level="high")
    row.source = {"conversation_id": None, "triggered_by_name": None, "inferred": True}
    assert approval_cards.card_text(row).splitlines()[0] == "「甲」请求：删除任务“写周报”"
    assert "触发" not in approval_cards.card_text(row)
    row.source = {"conversation_id": "si_a_b", "triggered_by_name": "张三", "inferred": True}
    assert (
        approval_cards.card_text(row).splitlines()[1]
        == "由 张三 私聊它触发（根据实例最近收到的消息推断）。"
    )
    row.source = {"conversation_id": "sg_1", "triggered_by_name": "张三", "inferred": True}
    assert "由 张三 在群聊里 @ 它触发" in approval_cards.card_text(row)
    row.risk_level = "critical"
    assert "重新输入密码" in approval_cards.card_text(row)
