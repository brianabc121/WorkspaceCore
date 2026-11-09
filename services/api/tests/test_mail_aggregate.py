"""多邮箱聚合读取 GET /api/v1/mail/messages（T-0170，四页改造规范第 7 节、实施计划 Task 5）。

内存版 Google 见 tests/fake_google.py。覆盖：
- 两个邮箱各两页、消息 / 会话 ID 故意相同、时间相同 / 缺失 / 乱序；“全部邮件”不含垃圾邮件；
- A 限流 B 正常、全部失败、服务不可用、待重连、全部需要重连、没有邮箱、正常空、未知总数；
- 游标：跨工作空间、换用户、变查询 / 分类 / 数量 / 范围、邮箱集合变化、过期、
  重试同一个游标、角色撤销；
- 6 个邮箱里前三个慢时下一批推进后面的邮箱；并发上限；
- 两个邮箱同时刷新令牌（各用自己的数据库会话）；
- Redis 与日志里没有主题、摘要、正文、搜索词与游标原文。
"""

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.logging import JsonFormatter
from app.domain import mail_aggregate
from tests.fake_google import CLIENT_ID, CLIENT_SECRET, SETTINGS, FakeGoogle
from tests.helpers import (
    BOB,
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    csrf,
    run_sql,
)
from tests.workspace_support import CONNECTOR_SETTINGS, app_of

pytestmark = pytest.mark.integration

ALL = {**CONNECTOR_SETTINGS, **SETTINGS}
A, B = "a.box@gmail.com", "b.box@gmail.com"
T = 1_793_000_000_000  # 毫秒


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    fake: FakeGoogle
    make_client: ClientFactory

    def headers(self, *, write: bool = False, workspace: str | None = None) -> dict[str, str]:
        return {
            "X-Workspace-Id": workspace or self.workspace_id,
            **(csrf(self.client) if write else {}),
        }

    async def connect(self, *emails: str) -> dict[str, dict[str, Any]]:
        configured = await self.client.get("/api/v1/mail/settings", headers=self.headers())
        if not configured.json()["configured"]:
            saved = await self.client.put(
                "/api/v1/mail/settings",
                json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
                headers=self.headers(write=True),
            )
            assert saved.status_code == 200, saved.text
        for email in emails:
            started = await self.client.post(
                "/api/v1/mail/connect", json={}, headers=self.headers(write=True)
            )
            params = self.fake.consent(started.json()["authorize_url"], email=email)
            done = await self.client.get("/api/v1/mail/oauth/callback", params=params)
            assert done.headers["location"] == "/settings/mail?mail=connected", done.text
        listed = await self.client.get("/api/v1/mail/accounts", headers=self.headers())
        return {a["email"]: a for a in listed.json()["items"]}

    async def page(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        workspace: str | None = None,
        **params: Any,
    ) -> httpx.Response:
        query = {k: v for k, v in params.items() if v is not None}
        return await (client or self.client).get(
            "/api/v1/mail/messages", params=query, headers=self.headers(workspace=workspace)
        )

    async def ok(self, **params: Any) -> dict[str, Any]:
        response = await self.page(**params)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body: dict[str, Any] = response.json()
        return body


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    fake = FakeGoogle()
    async with make_client(**ALL) as client:
        app_of(client).state.connectors.transport = fake.transport
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(
            client, setup.json()["workspaces"][0]["id"], migrated_database_url, fake, make_client
        )


def keys(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(i["account_email"], i["id"]) for i in body["items"]]


def status(body: dict[str, Any]) -> dict[str, str]:
    return {a["email"]: a["status"] for a in body["accounts"]}


def gmail_requests(
    fake: FakeGoogle, path: str = "/gmail/v1/users/me/messages"
) -> list[httpx.Request]:
    return [r for r in fake.requests if r.url.path == path]


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []
        self.formatter_ = JsonFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.formatter_.format(record))


# ---------------------------------------------------------------- 合并、分页、ID 冲突


async def test_merges_accounts_pages_and_keeps_ids_apart(env: Env) -> None:
    accounts = await env.connect(A, B)
    a_id, b_id = accounts[A]["id"], accounts[B]["id"]
    fake = env.fake
    # A：乱序加入；与 B 有同一个消息 ID / 会话 ID、同一个时间；一封没有 internalDate；一封垃圾邮件
    fake.add_message(A, subject="A 早", text="A3 正文", when=T + 1000)
    fake.add_message(
        A,
        subject="A 合同",
        text="A 的正文",
        message_id="mdup",
        thread_id="tdup",
        attachments=(("a.pdf", "application/pdf", b"AAA"),),
        when=T + 2000,
    )
    fake.add_message(A, subject="A 新", text="A2 正文", when=T + 4000)
    fake.add_message(A, subject="A 无时间", text="A4 正文", no_date=True)
    fake.add_message(A, subject="A 垃圾", labels=("SPAM",), when=T + 9000)
    fake.add_message(
        B,
        subject="B 合同",
        text="B 的正文",
        message_id="mdup",
        thread_id="tdup",
        attachments=(("b.pdf", "application/pdf", b"BBB"),),
        when=T + 2000,
    )
    fake.add_message(B, subject="B 旧", text="B3 正文", when=T)
    fake.add_message(B, subject="B 中", text="B2 正文", when=T + 3000)

    collect = _Collect()
    app_logger = logging.getLogger("app")
    app_logger.addHandler(collect)
    previous = app_logger.level
    app_logger.setLevel(logging.DEBUG)
    try:
        first = await env.ok(per_account_limit=2)
        second = await env.ok(per_account_limit=2, cursor=first["next_cursor"])
        searched = await env.ok(q="合同")
    finally:
        app_logger.removeHandler(collect)
        app_logger.setLevel(previous)

    # 第一批：每个邮箱各 2 封；同一时间按 account_id 排；不跨邮箱去重
    tie = sorted([(a_id, A), (b_id, B)])
    assert keys(first) == [
        (A, first["items"][0]["id"]),
        (B, first["items"][1]["id"]),
        (tie[0][1], "mdup"),
        (tie[1][1], "mdup"),
    ]
    assert [i["subject"] for i in first["items"][:2]] == ["A 新", "B 中"]
    dup = [i for i in first["items"] if i["id"] == "mdup"]
    assert {i["account_id"] for i in dup} == {a_id, b_id}
    assert {i["thread_id"] for i in dup} == {"tdup"}
    assert all(i["account_email"] in (A, B) and i["account_display_name"] for i in dup)
    assert first["outcome"] == "ok" and first["partial"] is False
    assert first["total_estimate"] == 7  # A 4 封（垃圾邮件不算）+ B 3 封
    assert status(first) == {A: "ok", B: "ok"}
    unread = {a["email"]: a["unread"] for a in first["accounts"]}
    assert unread == {A: 4, B: 3}  # labels.get INBOX 的 messagesUnread
    assert {a["email"]: a["loaded"] for a in first["accounts"]} == {A: 2, B: 2}
    assert first["next_cursor"]

    # 第二批：接着各自的剩余页；没有时间的放最后；读完后没有下一页
    assert [i["subject"] for i in second["items"]] == ["A 早", "B 旧", "A 无时间"]
    assert second["items"][-1]["date"] is None
    assert second["next_cursor"] is None
    assert all(a["exhausted"] for a in second["accounts"])
    loaded = {(i["account_id"], i["id"]) for i in first["items"] + second["items"]}
    assert len(loaded) == 7  # 两页合起来不漏不重
    assert "A 垃圾" not in str(first) + str(second)  # “全部邮件”不含垃圾邮件
    lists = gmail_requests(env.fake)
    assert lists and all(
        "labelIds" not in r.url.params and "includeSpamTrash" not in r.url.params for r in lists
    )
    gets = [r for r in fake.requests if "/messages/" in r.url.path and "labels" not in r.url.path]
    assert gets and all(r.url.params.get("format") == "metadata" for r in gets)
    assert [r.url.path for r in fake.requests if "/labels/" in r.url.path] == [
        "/gmail/v1/users/me/labels/INBOX"
    ] * 4  # 每个邮箱在每个查询的第一批读一次

    # 搜索：两个邮箱里的同 ID 邮件都在，各自标明邮箱
    assert sorted(keys(searched)) == sorted([(A, "mdup"), (B, "mdup")])

    # 同一个消息 ID：按所属邮箱读信、下载附件，不串号
    for email, text, name, content in (
        (A, "A 的正文", "a.pdf", b"AAA"),
        (B, "B 的正文", "b.pdf", b"BBB"),
    ):
        account_id = accounts[email]["id"]
        detail = await env.client.get(
            f"/api/v1/mail/accounts/{account_id}/messages/mdup", headers=env.headers()
        )
        assert detail.json()["text"] == text
        [attachment] = detail.json()["attachments"]
        assert attachment["filename"] == name
        download = await env.client.get(
            f"/api/v1/mail/accounts/{account_id}/messages/mdup/attachments/{attachment['attachment_id']}",
            headers=env.headers(),
        )
        assert download.content == content
        other = b_id if account_id == a_id else a_id
        crossed = await env.client.get(
            f"/api/v1/mail/accounts/{other}/messages/mdup/attachments/{attachment['attachment_id']}",
            headers=env.headers(),
        )
        assert crossed.status_code == 404

    # Redis 的游标状态与日志里都没有邮件内容、搜索词与游标原文
    redis = app_of(env.client).state.redis
    stored = [await redis.get(k) for k in await redis.keys("mail_cursor:*")]
    assert stored
    blob = b"\n".join(v for v in stored if v).decode()
    for secret in ("合同", "正文", "A 新", "B 中", "Alice", A, B, "tdup"):
        assert secret not in blob, secret
    logs = "\n".join(collect.lines)
    assert "mail aggregate batch" in logs
    for secret in ("合同", "正文", "A 新", "Alice", first["next_cursor"]):
        assert secret not in logs, secret


async def test_single_account_scope_and_labels(env: Env) -> None:
    accounts = await env.connect(A, B)
    env.fake.add_message(A, subject="A 收件", when=T + 1)
    env.fake.add_message(A, subject="A 已发", labels=("SENT",), when=T + 2)
    env.fake.add_message(A, subject="A 客户", labels=("INBOX", "Label_1"), when=T + 3)
    env.fake.add_message(B, subject="B 收件", when=T + 4)

    only_a = await env.ok(account_id=accounts[A]["id"])
    assert {i["account_email"] for i in only_a["items"]} == {A}
    assert [a["email"] for a in only_a["accounts"]] == [A]
    sent = await env.ok(label="SENT")
    assert [i["subject"] for i in sent["items"]] == ["A 已发"]
    inbox_a = await env.ok(account_id=accounts[A]["id"], label="INBOX", q="收件")
    assert [i["subject"] for i in inbox_a["items"]] == ["A 收件"]

    # 自定义标签只在所属邮箱里有效
    custom = await env.page(label="Label_1")
    assert custom.status_code == 422
    assert custom.json()["error"]["code"] == "MAIL_LABEL_NEEDS_ACCOUNT"
    scoped = await env.ok(account_id=accounts[A]["id"], label="Label_1")
    assert [i["subject"] for i in scoped["items"]] == ["A 客户"]

    # 正常空：所有邮箱都正常、确实没有邮件
    empty = await env.ok(label="STARRED")
    assert empty["items"] == [] and empty["outcome"] == "ok" and empty["partial"] is False
    assert empty["total_estimate"] == 0 and empty["next_cursor"] is None

    bad = await env.page(per_account_limit=21)
    assert bad.status_code == 422
    missing = await env.page(account_id=str(uuid.uuid4()))
    assert missing.status_code == 404


# ---------------------------------------------------------------- 故障与计数


async def test_one_account_failing_keeps_the_others(env: Env) -> None:
    await env.connect(A, B)
    env.fake.add_message(A, subject="A1", when=T + 1)
    env.fake.add_message(B, subject="B1", when=T + 2)
    env.fake.add_message(B, subject="B2", when=T + 3)

    env.fake.read_failures = {A: "rate"}
    limited = await env.ok()
    assert [i["subject"] for i in limited["items"]] == ["B2", "B1"]
    assert status(limited) == {A: "rate_limited", B: "ok"}
    assert limited["outcome"] == "partial" and limited["partial"] is True
    assert limited["total_estimate"] is None  # 有邮箱失败时不给总数
    [a] = [x for x in limited["accounts"] if x["email"] == A]
    assert a["error_code"] == "RATE_LIMITED" and a["unread"] is None
    retry_at = datetime.fromisoformat(a["retry_at"])
    assert (retry_at - datetime.now(UTC)).total_seconds() > 30
    assert limited["next_cursor"]

    # 下一批：A 的重试时间没到就不去请求它；B 已经读完
    before = len(env.fake.requests)
    later = await env.ok(cursor=limited["next_cursor"])
    assert later["items"] == [] and status(later) == {A: "rate_limited", B: "ok"}
    assert len(env.fake.requests) == before

    env.fake.read_failures = {A: "rate", B: "rate"}
    failed = await env.ok()
    assert failed["items"] == [] and failed["outcome"] == "all_failed"
    assert failed["partial"] is True and failed["total_estimate"] is None

    env.fake.read_failures = {A: "server"}
    broken = await env.ok()
    assert status(broken) == {A: "unavailable", B: "ok"}
    assert {a["email"]: a["error_code"] for a in broken["accounts"]}[A] == "EXTERNAL_UNAVAILABLE"
    assert [i["subject"] for i in broken["items"]] == ["B2", "B1"]


async def test_reconnect_states_and_parallel_refresh(env: Env) -> None:
    accounts = await env.connect(A, B)
    env.fake.add_message(A, subject="A1", when=T + 1)
    env.fake.add_message(B, subject="B1", when=T + 2)
    redis = app_of(env.client).state.redis

    # 两个邮箱的 access token 都不在缓存里：两个 worker 同时刷新，各用自己的数据库会话
    for account in accounts.values():
        await redis.delete(f"mail_at:{account['id']}")
    refreshed = await env.ok()
    assert status(refreshed) == {A: "ok", B: "ok"}
    tokens = [r for r in env.fake.requests if r.url.path == "/token"]
    assert sum(b"grant_type=refresh_token" in r.content for r in tokens) == 2

    # B 需要重新连接：不去请求 B，A 照常
    await run_sql(
        env.database_url,
        "UPDATE workspace.mail_accounts SET status = 'needs_reconnect' WHERE email = $1",
        B,
    )
    before = len(env.fake.requests)
    partial = await env.ok()
    assert status(partial) == {A: "ok", B: "needs_reconnect"}
    assert {a["email"]: a["error_code"] for a in partial["accounts"]}[B] == "MAIL_RECONNECT"
    assert [i["subject"] for i in partial["items"]] == ["A1"]
    after = env.fake.requests[before:]
    grants = env.fake.access_tokens
    assert after and all(
        grants[r.headers["authorization"].removeprefix("Bearer ")].email == A for r in after
    )
    # 单看 B：明确说明需要重新连接，不悄悄换成别的邮箱
    only_b = await env.ok(account_id=accounts[B]["id"])
    assert only_b["outcome"] == "no_access" and only_b["items"] == []
    assert [a["email"] for a in only_b["accounts"]] == [B]

    # A 的授权也失效（invalid_grant）：worker 在自己的会话里标记并提交
    env.fake.refresh_tokens.clear()
    env.fake.expire_access_tokens()
    await redis.delete(f"mail_at:{accounts[A]['id']}")
    gone = await env.ok()
    assert status(gone) == {A: "needs_reconnect", B: "needs_reconnect"}
    assert gone["outcome"] == "no_access" and gone["next_cursor"] is None
    stored = await run_sql(
        env.database_url, "SELECT status FROM workspace.mail_accounts WHERE email = $1", A
    )
    assert stored == "needs_reconnect"
    notices = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.notifications WHERE type = 'mail.reconnect'",
    )
    assert notices >= 1

    # 没有已连接的邮箱
    for account in accounts.values():
        removed = await env.client.delete(
            f"/api/v1/mail/accounts/{account['id']}", headers=env.headers(write=True)
        )
        assert removed.status_code == 204
    nothing = await env.ok()
    assert nothing["outcome"] == "no_accounts" and nothing["accounts"] == []


# ---------------------------------------------------------------- 游标


async def test_cursor_binding_expiry_and_retry(env: Env) -> None:
    # 先建好第二个客户端（新客户端会清空测试用 Redis 库）
    bob = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), bob, "admin")
    async with env.make_client(**ALL) as other:
        app_of(other).state.connectors.transport = env.fake.transport
        assert (await other.post("/api/v1/auth/login", json=BOB)).status_code == 200
        accounts = await env.connect(A, B)
        for i in range(5):
            env.fake.add_message(A, subject=f"A{i}", when=T + 10 * i)
            env.fake.add_message(B, subject=f"B{i}", when=T + 10 * i + 5)
        me = await env.client.get("/api/v1/me")
        owner_id = uuid.UUID(me.json()["user"]["id"])
        second_workspace = await add_workspace(env.database_url, owner_id, "第二个空间")

        base: dict[str, Any] = {"per_account_limit": 2}
        first = await env.ok(**base)
        assert len(first["items"]) == 4
        cursor = first["next_cursor"]

        async def error(response: httpx.Response) -> str:
            assert response.status_code == 422, response.text
            return str(response.json()["error"]["code"])

        # 输入的游标不消耗：重试同一个游标得到同样的结果
        again_1 = await env.ok(**base, cursor=cursor)
        again_2 = await env.ok(**base, cursor=cursor)
        assert keys(again_1) == keys(again_2) and again_1["items"]
        assert again_1["next_cursor"] != again_2["next_cursor"]  # 每一批生成新游标

        # 变了查询、分类、每个邮箱的数量、范围：旧游标不能用
        assert (
            await error(await env.page(**{**base, "q": "其他"}, cursor=cursor))
            == "MAIL_CURSOR_MISMATCH"
        )
        assert (
            await error(await env.page(**base, label="INBOX", cursor=cursor))
            == "MAIL_CURSOR_MISMATCH"
        )
        assert (
            await error(await env.page(**{**base, "per_account_limit": 3}, cursor=cursor))
            == "MAIL_CURSOR_MISMATCH"
        )
        assert (
            await error(await env.page(**base, account_id=accounts[A]["id"], cursor=cursor))
            == "MAIL_CURSOR_MISMATCH"
        )
        # 另一个工作空间（同一个用户）、另一个用户（同一个工作空间）
        assert (
            await error(await env.page(**base, cursor=cursor, workspace=str(second_workspace)))
            == "MAIL_CURSOR_MISMATCH"
        )
        assert await error(await env.page(other, **base, cursor=cursor)) == "MAIL_CURSOR_MISMATCH"

        # 过期 / 不认识的游标
        assert await error(await env.page(**base, cursor="x" * 32)) == "MAIL_CURSOR_EXPIRED"
        redis = app_of(env.client).state.redis
        ttl = await redis.ttl(f"mail_cursor:{cursor}")
        assert 0 < ttl <= mail_aggregate.CURSOR_TTL
        await redis.delete(f"mail_cursor:{cursor}")
        assert await error(await env.page(**base, cursor=cursor)) == "MAIL_CURSOR_EXPIRED"
        malformed = await env.page(**base, cursor="bad cursor!")
        assert malformed.status_code == 422

        # 邮箱集合变了（又连了一个）：旧游标 422，要从第一页重来
        fresh = await env.ok(**base)
        await env.connect("c.box@gmail.com")
        changed = await env.page(**base, cursor=fresh["next_cursor"])
        assert await error(changed) == "MAIL_CURSOR_MISMATCH"

        # 角色撤销：翻页时重新校验，403
        mine = await env.page(other, **base)
        assert mine.status_code == 200
        await run_sql(
            env.database_url,
            "UPDATE workspace.workspace_members SET role = 'member' WHERE user_id = $1",
            bob,
        )
        revoked = await env.page(other, **base, cursor=mine.json()["next_cursor"])
        assert revoked.status_code == 403


# ---------------------------------------------------------------- 并发与预算


async def test_slow_accounts_do_not_starve_the_rest(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    emails = [f"box{i}@gmail.com" for i in range(6)]
    accounts = await env.connect(*emails)
    for i, email in enumerate(emails):
        for j in range(4):
            env.fake.add_message(email, subject=f"{i}-{j}", when=T + 100 * i + j)

    # 并发上限：3 个邮箱 × 每个 2 个 = 总 HTTP 不超过 6
    env.fake.delays = dict.fromkeys(emails, 0.05)
    full = await env.ok()
    assert len(full["items"]) == 24 and full["outcome"] == "ok"
    assert env.fake.max_in_flight <= mail_aggregate.TOTAL_HTTP
    assert max(env.fake.max_box_in_flight.values()) <= mail_aggregate.ACCOUNT_FETCHES
    assert env.fake.max_in_flight > mail_aggregate.ACCOUNT_FETCHES  # 确实在并发

    # 前三个邮箱很慢：第一批到预算还没读完；下一批先读没轮到的后三个
    monkeypatch.setattr(mail_aggregate, "BATCH_BUDGET", 0.8)
    env.fake.delays = dict.fromkeys(emails[:3], 5.0)
    first = await env.ok()
    assert first["items"] == [] and first["outcome"] == "pending"
    assert set(status(first).values()) == {"pending"}
    assert first["total_estimate"] is None and first["next_cursor"]

    second = await env.ok(cursor=first["next_cursor"])
    assert {i["account_email"] for i in second["items"]} == set(emails[3:])
    assert status(second) == {
        **dict.fromkeys(emails[:3], "pending"),
        **dict.fromkeys(emails[3:], "ok"),
    }
    assert second["outcome"] == "partial" and second["next_cursor"]

    # 慢邮箱恢复后，接着读它们（后三个已经读完）
    env.fake.delays = {}
    third = await env.ok(cursor=second["next_cursor"])
    assert {i["account_email"] for i in third["items"]} == set(emails[:3])
    assert third["outcome"] == "ok" and third["next_cursor"] is None
    assert len(accounts) == 6
