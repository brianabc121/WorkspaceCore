"""对真实 OpenIM（补丁镜像）的连接器测试（可选，V2 OC-3）。

设置以下环境变量时运行，否则跳过（CI 不运行）：
- TEST_OPENIM_URL：OpenIM REST 地址，例如 http://127.0.0.1:10002
  （infra/openim/compose.yaml + compose.probe.yaml 启动；回调桩放行一切）
- TEST_OPENIM_SECRET：该实例的 share.secret（OPENIM_SECRET）

核对连接器依赖的真实行为：契约里的用户 ID 格式（ws_ / oc_ 加 32 位十六进制）可用；
重复注册是 CONFLICT；is_friend / import_friend 的双向语义；
以 ws_ 用户身份代发的消息能用 pull_msg_by_seq 拉回原文；
群的建立、邀请、移出、改名、解散；会话置顶与免打扰；用户令牌与强制下线。
"""

import asyncio
import base64
import json
import os
import uuid
from typing import Any

import pytest

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy, host_of
from app.integrations.openim import (
    SESSION_GROUP,
    OpenIMClient,
    group_conversation_id,
    single_conversation_id,
)

URL = os.environ.get("TEST_OPENIM_URL", "")
SECRET = os.environ.get("TEST_OPENIM_SECRET", "")
pytestmark = pytest.mark.skipif(
    not (URL and SECRET), reason="需要 TEST_OPENIM_URL 与 TEST_OPENIM_SECRET"
)


def _client() -> OpenIMClient:
    http = ConnectorHttp(policy=OutboundPolicy(frozenset({host_of(URL)})), read_retries=0)
    return OpenIMClient(http, URL.rstrip("/"), SECRET)


def _text(msg: dict[str, Any]) -> str:
    content = json.loads(base64.b64decode(msg.get("content") or "").decode() or "{}")
    return str(content.get("content") or content.get("text") or "")


async def test_users_friends_messages_groups_conversations() -> None:
    im = _client()
    admin = f"ws_{uuid.uuid4().hex}"
    agent_a, agent_b = f"oc_{uuid.uuid4().hex}", f"oc_{uuid.uuid4().hex}"
    assert len(admin) == 35

    await im.register_users([(admin, "管理员"), (agent_a, "实例 A"), (agent_b, "实例 B")])
    assert await im.existing_users([admin, agent_a, "ws_" + "0" * 32]) == {admin, agent_a}
    with pytest.raises(ConnectorError) as dup:
        await im.register_users([(admin, "again")])
    assert dup.value.kind == "CONFLICT"

    assert await im.is_friend(admin, agent_a) is False
    await im.import_friends(admin, [agent_a, agent_b])
    assert await im.is_friend(admin, agent_a) is True
    assert await im.is_friend(agent_b, admin) is True

    marker = f"live-{uuid.uuid4().hex[:8]}"
    sent = await im.send_text(send_id=admin, recv_id=agent_a, text=marker)
    assert sent.server_msg_id and sent.client_msg_id
    conv = single_conversation_id(admin, agent_a)
    # 消息经 Kafka 异步写入存储；会话里还有导入好友产生的通知（contentType 1201），
    # 所以一直拉到暗号出现为止
    found = False
    for _ in range(30):
        top = (await im.max_seqs(admin)).get(conv, 0)
        if top and any(
            _text(m) == marker and m.get("sendID") == admin
            for m in await im.pull_messages(admin, conv, 1, top)
        ):
            found = True
            break
        await asyncio.sleep(0.5)
    assert found

    token = await im.user_token(agent_a)
    assert token.token and token.expires_in > 0
    await im.force_logout(agent_a)

    group_id = await im.create_group(owner=admin, name="测试群", members=[agent_a])
    await im.invite_to_group(group_id, [agent_b])
    await im.set_group_info(group_id, name="改名后的群", notification="公告")
    at_marker = f"at-{uuid.uuid4().hex[:8]}"
    await im.send_text(
        send_id=admin, group_id=group_id, text=f"@实例A {at_marker}", at_user_ids=[agent_a]
    )
    await im.kick_from_group(group_id, [agent_b])
    gconv = group_conversation_id(group_id)
    await im.set_conversation(
        admin, gconv, conversation_type=SESSION_GROUP, group_id=group_id, pinned=True, muted=True
    )
    await im.set_conversation(
        admin, conv, conversation_type=1, peer_user_id=agent_a, pinned=False, muted=False
    )
    await im.dismiss_group(group_id)
    with pytest.raises(ConnectorError) as gone:
        await im.invite_to_group(group_id, [agent_b])
    assert gone.value.kind in ("NOT_FOUND", "VALIDATION_FAILED")


async def test_notification_account_sends_approval_cards() -> None:
    """工作空间通知账号（OC-5 第 3 批）：注册为通知账号、发 OA 通知、按序号拉回并识别为审批卡片。"""
    from app.domain.approval_cards import card_ex
    from app.domain.im_projection import parse_message
    from app.integrations.openim import notice_conversation_id

    im = _client()
    admin, notice = f"ws_{uuid.uuid4().hex}", f"wn_{uuid.uuid4().hex}"
    await im.register_users([(admin, "管理员")])
    await im.add_notification_account(notice, "审批通知")
    assert await im.existing_users([notice]) == {notice}
    # 已存在的用户 ID 不能再注册成通知账号（1001）
    with pytest.raises(ConnectorError) as used:
        await im.add_notification_account(admin, "x")
    assert used.value.kind == "VALIDATION_FAILED"
    # 普通账号发 OA 通知被拒（1002）
    with pytest.raises(ConnectorError) as denied:
        await im.send_oa_notification(send_id=admin, recv_id=notice, name="x", text="x", ex="")
    assert denied.value.kind == "AUTH_FAILED"

    execution_id = uuid.uuid4()
    await im.send_oa_notification(
        send_id=notice,
        recv_id=admin,
        name="审批通知",
        text="「alpha」请求：删除任务",
        ex=card_ex(execution_id),
    )
    conv = notice_conversation_id(notice, admin)
    parsed = None
    for _ in range(30):
        top = (await im.max_seqs(admin)).get(conv, 0)
        if top:
            [raw] = await im.pull_messages(admin, conv, 1, top)
            parsed = parse_message(raw)
            break
        await asyncio.sleep(1)
    assert parsed is not None
    assert parsed.content_type == 1400 and parsed.sender_im_id == notice
    assert parsed.plain_text == "「alpha」请求：删除任务"
    assert parsed.content == {"approval_execution_id": str(execution_id)}
