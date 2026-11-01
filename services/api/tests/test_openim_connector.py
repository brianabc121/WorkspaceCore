"""OpenIM 连接器的单元测试（V2 OC-3）：管理员令牌缓存与重取、errCode 归类、请求体。

对真实 OpenIM 的测试见 test_openim_live.py。
"""

import json
from typing import Any

import httpx
import pytest

from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker, ConnectorHttp, OutboundPolicy
from app.integrations.openim import (
    CONTENT_AT_TEXT,
    CONTENT_TEXT,
    SESSION_GROUP,
    SESSION_SINGLE,
    OpenIMClient,
    OpenIMError,
    group_conversation_id,
    single_conversation_id,
)

BASE = "http://openim.test:10002"


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def get(self, name: str) -> Any:
        return self.data.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        self.data[name] = value
        self.ttl[name] = ex

    async def delete(self, *names: str) -> Any:
        for name in names:
            self.data.pop(name, None)


class FakeOpenIM:
    """按路径返回预设应答，记录收到的请求。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.tokens_issued = 0
        self.replies: dict[str, list[dict[str, Any]]] = {}

    def reply(self, path: str, *payloads: dict[str, Any]) -> None:
        self.replies.setdefault(path, []).extend(payloads)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.requests.append((path, body, dict(request.headers)))
        if path == "/auth/get_admin_token":
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "errCode": 0,
                    "data": {"token": f"admin-{self.tokens_issued}", "expireTimeSeconds": 7776000},
                },
            )
        queued = self.replies.get(path)
        payload = queued.pop(0) if queued else {"errCode": 0, "data": {}}
        return httpx.Response(200, json=payload)

    def client(self, cache: MemoryCache | None = None) -> OpenIMClient:
        http = ConnectorHttp(
            policy=OutboundPolicy(frozenset({"openim.test"})),
            read_retries=0,
            breaker=CircuitBreaker(),
            transport=httpx.MockTransport(self.handler),
            backoff_base_seconds=0,
        )
        return OpenIMClient(http, BASE, "s3cret", cache=cache)

    def calls(self, path: str) -> list[dict[str, Any]]:
        return [body for p, body, _ in self.requests if p == path]


async def test_admin_token_is_cached_with_refresh_margin() -> None:
    fake, cache = FakeOpenIM(), MemoryCache()
    client = fake.client(cache)
    await client.call("/user/account_check", {"checkUserIDs": []})
    await client.call("/user/account_check", {"checkUserIDs": []})
    assert fake.tokens_issued == 1
    assert fake.calls("/auth/get_admin_token") == [{"secret": "s3cret", "userID": "imAdmin"}]
    # 管理员令牌放在 token 请求头，换令牌的请求本身不带
    headers = [h for p, _, h in fake.requests]
    assert "token" not in headers[0] and headers[1]["token"] == "admin-1"  # noqa: S105
    assert cache.ttl["openim:admin_token"] == 7776000 - 3600


async def test_token_error_clears_cache_and_retries_once() -> None:
    fake, cache = FakeOpenIM(), MemoryCache()
    cache.data["openim:admin_token"] = "stale"  # noqa: S105
    fake.reply(
        "/user/account_check",
        {"errCode": 1501, "errMsg": "TokenExpiredError"},
        {"errCode": 0, "data": {}},
    )
    await fake.client(cache).call("/user/account_check", {"checkUserIDs": []})
    tokens = [h.get("token") for p, _, h in fake.requests if p == "/user/account_check"]
    assert tokens == ["stale", "admin-1"]


async def test_token_error_twice_is_auth_failed() -> None:
    fake = FakeOpenIM()
    fake.reply("/user/account_check", {"errCode": 1502}, {"errCode": 1502})
    with pytest.raises(OpenIMError) as info:
        await fake.client(MemoryCache()).call("/user/account_check", {"checkUserIDs": []})
    assert info.value.kind == "AUTH_FAILED" and info.value.err_code == 1502


@pytest.mark.parametrize(
    ("err_code", "kind"),
    [
        (1001, "VALIDATION_FAILED"),
        (1002, "AUTH_FAILED"),
        (1003, "CONFLICT"),
        (1004, "NOT_FOUND"),
        (1101, "NOT_FOUND"),
        (1102, "CONFLICT"),
        (1204, "NOT_FOUND"),
        (1304, "CONFLICT"),
        (500, "UNAVAILABLE"),
    ],
)
async def test_err_codes_are_classified(err_code: int, kind: str) -> None:
    fake = FakeOpenIM()
    fake.reply(
        "/friend/import_friend", {"errCode": err_code, "errMsg": "x", "errDlt": "secret detail"}
    )
    with pytest.raises(OpenIMError) as info:
        await fake.client().import_friends("ws_a", ["oc_b"])
    assert info.value.kind == kind
    # 不把对方的错误详情带进错误信息
    assert "secret detail" not in str(info.value)


async def test_non_200_and_garbage_are_unavailable() -> None:
    for response in (httpx.Response(502, text="bad gateway"), httpx.Response(200, text="<html>")):
        http = ConnectorHttp(
            policy=OutboundPolicy(frozenset({"openim.test"})),
            read_retries=0,
            transport=httpx.MockTransport(lambda _r, resp=response: resp),
        )
        with pytest.raises(ConnectorError) as info:
            await OpenIMClient(http, BASE, "s").call("/x", {}, admin=False)
        assert info.value.kind == "UNAVAILABLE"


async def test_existing_users_uses_numeric_status() -> None:
    fake = FakeOpenIM()
    fake.reply(
        "/user/account_check",
        {
            "errCode": 0,
            "data": {
                "results": [
                    {"userID": "oc_a", "accountStatus": 1},
                    {"userID": "oc_b", "accountStatus": 0},
                ]
            },
        },
    )
    assert await fake.client().existing_users(["oc_a", "oc_b"]) == {"oc_a"}


async def test_is_friend_requires_both_directions() -> None:
    fake = FakeOpenIM()
    fake.reply(
        "/friend/is_friend",
        {"errCode": 0, "data": {"inUser1Friends": True, "inUser2Friends": False}},
    )
    fake.reply(
        "/friend/is_friend",
        {"errCode": 0, "data": {"inUser1Friends": True, "inUser2Friends": True}},
    )
    client = fake.client()
    assert await client.is_friend("ws_a", "oc_b") is False
    assert await client.is_friend("ws_a", "oc_b") is True
    assert fake.calls("/friend/is_friend")[0] == {"userID1": "ws_a", "userID2": "oc_b"}


async def test_import_friends_skips_empty() -> None:
    fake = FakeOpenIM()
    await fake.client().import_friends("ws_a", [])
    assert fake.calls("/friend/import_friend") == []


async def test_send_text_single_and_group_with_at() -> None:
    fake = FakeOpenIM()
    fake.reply(
        "/msg/send_msg",
        {"errCode": 0, "data": {"serverMsgID": "s1", "clientMsgID": "c1", "sendTime": 1700}},
    )
    client = fake.client()
    sent = await client.send_text(send_id="ws_a", recv_id="oc_b", text="hi")
    assert (sent.server_msg_id, sent.client_msg_id, sent.send_time_ms) == ("s1", "c1", 1700)
    await client.send_text(send_id="ws_a", group_id="g1", text="@b go", at_user_ids=["oc_b"])
    single, group = fake.calls("/msg/send_msg")
    assert single["sessionType"] == SESSION_SINGLE and single["contentType"] == CONTENT_TEXT
    assert (
        single["content"] == {"content": "hi"}
        and single["recvID"] == "oc_b"
        and single["groupID"] == ""
    )
    assert group["sessionType"] == SESSION_GROUP and group["contentType"] == CONTENT_AT_TEXT
    assert group["content"] == {"text": "@b go", "atUserList": ["oc_b"]}
    with pytest.raises(ValueError):
        await client.send_text(send_id="ws_a", text="x")


async def test_pull_messages_skips_placeholders() -> None:
    fake = FakeOpenIM()
    conv = single_conversation_id("ws_a", "oc_b")
    fake.reply(
        "/msg/pull_msg_by_seq",
        {
            "errCode": 0,
            "data": {
                "msgs": {conv: {"Msgs": [{"seq": 0}, {"seq": 1, "content": "e30="}], "isEnd": True}}
            },
        },
    )
    msgs = await fake.client().pull_messages("ws_a", conv, 1, 10)
    assert [m["seq"] for m in msgs] == [1]
    assert fake.calls("/msg/pull_msg_by_seq")[0]["seqRanges"] == [
        {"conversationID": conv, "begin": 1, "end": 10, "num": 100}
    ]


async def test_group_and_conversation_bodies_use_plain_values() -> None:
    fake = FakeOpenIM()
    fake.reply("/group/create_group", {"errCode": 0, "data": {"groupInfo": {"groupID": "g9"}}})
    client = fake.client()
    assert await client.create_group(owner="ws_a", name="项目组", members=["oc_b"]) == "g9"
    await client.set_group_info("g9", name="新名字")
    await client.set_conversation(
        "ws_a", "sg_g9", conversation_type=SESSION_GROUP, group_id="g9", pinned=True, muted=True
    )
    assert fake.calls("/group/create_group")[0]["groupInfo"] == {
        "groupName": "项目组",
        "groupType": 2,
    }
    assert fake.calls("/group/set_group_info_ex")[0] == {"groupID": "g9", "groupName": "新名字"}
    conversation = fake.calls("/conversation/set_conversations")[0]
    assert conversation["userIDs"] == ["ws_a"]
    assert conversation["conversation"]["isPinned"] is True
    assert conversation["conversation"]["recvMsgOpt"] == 2


async def test_create_group_without_id_is_unavailable() -> None:
    fake = FakeOpenIM()
    fake.reply("/group/create_group", {"errCode": 0, "data": {}})
    with pytest.raises(ConnectorError) as info:
        await fake.client().create_group(owner="ws_a", name="x", members=[])
    assert info.value.kind == "UNAVAILABLE"


def test_conversation_ids() -> None:
    assert (
        single_conversation_id("oc_b", "ws_a")
        == "si_oc_b_ws_a"
        == single_conversation_id("ws_a", "oc_b")
    )
    assert group_conversation_id("123") == "sg_123"
