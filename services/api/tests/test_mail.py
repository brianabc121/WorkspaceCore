"""Gmail 邮件的页面接口（T-0128，docs/v2/MAIL_PLAN.md）。

内存版 Google 见 tests/fake_google.py（请求与响应形状按核验记录 2026-11-06-gmail-verification.md）。
覆盖：OAuth 客户端设置、授权（state、PKCE、拒绝、只授部分范围、state 绑定用户）、令牌加密、
刷新与 invalid_grant、查收 / 搜索 / 读信 / 会话 / HTML 转纯文字 / 附件、发信（RFC 2822、回复、
密钥拦截、收件人检查、结果未知不重发、限流）、断开、权限（member / viewer 403、跨工作空间 404）。
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.fake_google import CLIENT_ID, CLIENT_SECRET, READ, SEND, SETTINGS, FakeGoogle
from tests.helpers import (
    BASE_URL,
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

MAILBOX = "owner.mail@gmail.com"
ALL = {**CONNECTOR_SETTINGS, **SETTINGS}


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    fake: FakeGoogle
    make_client: ClientFactory

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def configure(self) -> None:
        response = await self.client.put(
            "/api/v1/mail/settings",
            json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            headers=self.headers(write=True),
        )
        assert response.status_code == 200, response.text

    async def connect(self, **consent: Any) -> httpx.Response:
        response = await self.client.post(
            "/api/v1/mail/connect", json={}, headers=self.headers(write=True)
        )
        assert response.status_code == 200, response.text
        params = self.fake.consent(response.json()["authorize_url"], **consent)
        return await self.client.get("/api/v1/mail/oauth/callback", params=params)

    async def account(self) -> dict[str, Any]:
        response = await self.client.get("/api/v1/mail/accounts", headers=self.headers())
        assert response.status_code == 200, response.text
        [account] = response.json()["items"]
        return dict(account)

    async def ready(self, **consent: Any) -> dict[str, Any]:
        await self.configure()
        response = await self.connect(**consent)
        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/settings/mail?mail=connected"
        return await self.account()

    def url(self, account: dict[str, Any], path: str = "") -> str:
        return f"/api/v1/mail/accounts/{account['id']}{path}"


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


def _location(response: httpx.Response) -> str:
    assert response.status_code == 303, response.text
    return str(response.headers["location"])


# ---------------------------------------------------------------- 设置与授权


async def test_settings_and_connect(env: Env) -> None:
    first = await env.client.put(
        "/api/v1/mail/settings", json={"client_id": CLIENT_ID}, headers=env.headers(write=True)
    )
    assert first.status_code == 422  # 第一次必须给 client_secret
    blank = await env.client.get("/api/v1/mail/settings", headers=env.headers())
    assert blank.json() == {
        "configured": False,
        "client_id": None,
        "client_secret_set": False,
        "redirect_uri": f"{BASE_URL}/api/v1/mail/oauth/callback",
        "version": None,
    }
    not_ready = await env.client.post(
        "/api/v1/mail/connect", json={}, headers=env.headers(write=True)
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "MAIL_NOT_CONFIGURED"

    await env.configure()
    saved = await env.client.get("/api/v1/mail/settings", headers=env.headers())
    assert saved.headers["cache-control"] == "no-store"
    body = saved.json()
    assert body["configured"] is True and body["client_secret_set"] is True
    assert CLIENT_SECRET not in saved.text
    stored = await run_sql(
        env.database_url, "SELECT client_secret_ref FROM workspace.mail_oauth_clients"
    )
    assert stored.startswith("enc:v1:") and CLIENT_SECRET not in stored
    # 只改 client_id、不填密钥：密钥保持；版本不对 409
    stale = await env.client.put(
        "/api/v1/mail/settings",
        json={"client_id": CLIENT_ID, "version": body["version"] + 5},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409

    started = await env.client.post(
        "/api/v1/mail/connect",
        json={"login_hint": MAILBOX},
        headers=env.headers(write=True),
    )
    url = started.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert params["scope"].split() == [READ, SEND]
    assert params["access_type"] == "offline" and params["prompt"] == "consent"
    assert params["include_granted_scopes"] == "true"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert params["redirect_uri"] == f"{BASE_URL}/api/v1/mail/oauth/callback"
    assert params["login_hint"] == MAILBOX and len(params["state"]) >= 32

    query = env.fake.consent(url)
    done = await env.client.get("/api/v1/mail/oauth/callback", params=query)
    assert _location(done) == "/settings/mail?mail=connected"
    again = await env.client.get("/api/v1/mail/oauth/callback", params=query)
    assert _location(again) == "/settings/mail?mail=MAIL_STATE_INVALID"  # state 只能用一次

    account = await env.account()
    assert account["email"] == MAILBOX and account["status"] == "active"
    assert account["can_read"] is True and account["can_send"] is True
    assert account["agents_allowed"] is True and account["agent_signature"] is True
    refresh = await run_sql(
        env.database_url, "SELECT refresh_token_ref FROM workspace.mail_accounts"
    )
    assert refresh.startswith("enc:v1:") and "1//0g" not in refresh
    actions = await run_sql(
        env.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'mail.%'",
    )
    assert actions == "mail.client_updated,mail.connected"
    audit = await run_sql(
        env.database_url, "SELECT metadata_redacted_json::text FROM workspace.audit_logs"
    )
    assert "ya29." not in str(audit) and "1//0g" not in str(audit)

    # 重新连接同一个邮箱：更新原记录，不新增
    assert _location(await env.connect()) == "/settings/mail?mail=connected"
    count = await run_sql(env.database_url, "SELECT count(*) FROM workspace.mail_accounts")
    assert count == 1


async def test_denied_partial_and_wrong_user(env: Env) -> None:
    await env.configure()
    denied = await env.connect(deny=True)
    assert _location(denied) == "/settings/mail?mail=MAIL_ACCESS_DENIED"
    no_read = await env.connect(scopes=[SEND])
    assert _location(no_read) == "/settings/mail?mail=MAIL_SCOPE_MISSING"
    bad = await env.client.get("/api/v1/mail/oauth/callback", params={"state": "nope", "code": "x"})
    assert _location(bad) == "/settings/mail?mail=MAIL_STATE_INVALID"

    read_only = await env.connect(scopes=[READ])
    assert _location(read_only) == "/settings/mail?mail=connected"
    account = await env.account()
    assert account["can_read"] is True and account["can_send"] is False
    send = await env.client.post(
        env.url(account, "/send"),
        json={"to": ["alice@example.com"], "subject": "hi", "body": "hello"},
        headers=env.headers(write=True),
    )
    assert send.status_code == 409 and send.json()["error"]["code"] == "MAIL_SCOPE_MISSING"

    # state 绑定发起连接的用户：另一个管理员拿到这个回调地址也用不了
    bob = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), bob, "admin")
    started = await env.client.post(
        "/api/v1/mail/connect", json={}, headers=env.headers(write=True)
    )
    query = env.fake.consent(started.json()["authorize_url"], email="other@gmail.com")
    async with env.make_client(**ALL) as other:
        app_of(other).state.connectors.transport = env.fake.transport
        login = await other.post("/api/v1/auth/login", json=BOB)
        assert login.status_code == 200
        stolen = await other.get("/api/v1/mail/oauth/callback", params=query)
        assert _location(stolen) == "/settings/mail?mail=MAIL_STATE_INVALID"
    async with httpx.AsyncClient(transport=env.client._transport, base_url=BASE_URL) as anonymous:
        unauthenticated = await anonymous.get("/api/v1/mail/oauth/callback", params=query)
    assert unauthenticated.status_code == 401


# ---------------------------------------------------------------- 读


async def test_search_read_thread_and_attachment(env: Env) -> None:
    account = await env.ready()
    first = env.fake.add_message(MAILBOX, subject="合同", when=1_793_000_000_000)
    env.fake.add_message(
        MAILBOX,
        sender="Bob <bob@example.com>",
        subject="Re: 合同",
        text=None,
        html=(
            "<html><head><style>p{color:red}</style><script>alert(1)</script></head>"
            '<body><p>请看<a href="https://example.com/doc">这里</a></p>'
            '<img src="https://tracker.example.com/p.gif"><div>谢谢</div></body></html>'
        ),
        thread_id=first["threadId"],
        labels=("INBOX",),
        attachments=(("报价.pdf", "application/pdf", b"%PDF-1.4 fake"),),
        when=1_793_000_100_000,
    )
    env.fake.add_message(MAILBOX, subject="周报", labels=("SENT",))

    labels = await env.client.get(env.url(account, "/labels"), headers=env.headers())
    assert labels.status_code == 200
    assert [x["id"] for x in labels.json()["items"]][:2] == ["INBOX", "SENT"]
    assert labels.json()["items"][0]["unread"] is None

    inbox = await env.client.get(
        env.url(account, "/messages"), params={"label": "INBOX"}, headers=env.headers()
    )
    assert inbox.status_code == 200 and inbox.headers["cache-control"] == "no-store"
    items = inbox.json()["items"]
    assert [i["subject"] for i in items] == ["Re: 合同", "合同"]
    assert items[1]["unread"] is True and items[0]["unread"] is False
    assert items[1]["sender"] == "Alice <alice@example.com>"
    # 列表只取 metadata，不取正文
    gets = [r for r in env.fake.requests if "/messages/m" in r.url.path]
    assert gets and all(r.url.params.get("format") == "metadata" for r in gets)

    paged = await env.client.get(
        env.url(account, "/messages"), params={"limit": 1}, headers=env.headers()
    )
    assert len(paged.json()["items"]) == 1
    assert paged.json()["next_page_token"] == "1"  # noqa: S105 - 分页游标
    searched = await env.client.get(
        env.url(account, "/messages"), params={"q": "from:bob@example.com"}, headers=env.headers()
    )
    assert [i["subject"] for i in searched.json()["items"]] == ["Re: 合同"]

    plain = await env.client.get(
        env.url(account, f"/messages/{first['id']}"), headers=env.headers()
    )
    assert plain.json()["text"] == "正文第一行\n第二行" and plain.json()["attachments"] == []

    reply_id = items[0]["id"]
    html = await env.client.get(env.url(account, f"/messages/{reply_id}"), headers=env.headers())
    text = html.json()["text"]
    assert "请看这里 <https://example.com/doc>" in text and "谢谢" in text
    assert (
        "alert" not in text and "color" not in text and "tracker" not in text and "<p>" not in text
    )
    [attachment] = html.json()["attachments"]
    assert attachment["filename"] == "报价.pdf" and attachment["size"] == 13

    thread = await env.client.get(
        env.url(account, f"/threads/{first['threadId']}"), headers=env.headers()
    )
    assert [m["subject"] for m in thread.json()["messages"]] == ["合同", "Re: 合同"]

    download = await env.client.get(
        env.url(account, f"/messages/{reply_id}/attachments/{attachment['attachment_id']}"),
        params={"filename": attachment["filename"]},
        headers=env.headers(),
    )
    assert download.status_code == 200 and download.content == b"%PDF-1.4 fake"
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")

    missing = await env.client.get(env.url(account, "/messages/zzz404"), headers=env.headers())
    assert missing.status_code == 404
    bad_id = await env.client.get(env.url(account, "/messages/a.b"), headers=env.headers())
    assert bad_id.status_code == 422

    env.fake.read_failure = "rate"
    limited = await env.client.get(env.url(account, "/labels"), headers=env.headers())
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"


async def test_token_refresh_and_reconnect(env: Env) -> None:
    account = await env.ready()
    env.fake.add_message(MAILBOX)
    redis = app_of(env.client).state.redis
    cached = await redis.get(f"mail_at:{account['id']}")
    assert cached and b"ya29." not in cached  # access token 加密后才放 Redis

    # access token 失效（Gmail 401）：刷新一次再试，成功
    env.fake.expire_access_tokens()
    ok = await env.client.get(env.url(account, "/messages"), headers=env.headers())
    assert ok.status_code == 200 and len(ok.json()["items"]) == 1
    grants = [r for r in env.fake.requests if r.url.path == "/token"]
    assert b"grant_type=refresh_token" in grants[-1].content

    # 用户在 Google 撤销了授权：刷新得到 invalid_grant → 需要重新连接 + 通知管理员
    env.fake.refresh_tokens.clear()
    env.fake.expire_access_tokens()
    failed = await env.client.get(env.url(account, "/messages"), headers=env.headers())
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "MAIL_RECONNECT"
    after = await env.account()
    assert after["status"] == "needs_reconnect" and after["last_error"] == "invalid_grant"
    notice = await run_sql(
        env.database_url,
        "SELECT title FROM workspace.notifications WHERE type = 'mail.reconnect'",
    )
    assert notice == f"Gmail 邮箱 {MAILBOX} 需要重新连接"
    again = await env.client.get(env.url(account, "/labels"), headers=env.headers())
    assert again.json()["error"]["code"] == "MAIL_RECONNECT"

    # 重新连接后恢复
    assert _location(await env.connect()) == "/settings/mail?mail=connected"
    assert (await env.account())["status"] == "active"
    assert (
        await env.client.get(env.url(account, "/labels"), headers=env.headers())
    ).status_code == 200


# ---------------------------------------------------------------- 发


async def test_send_and_reply(env: Env) -> None:
    account = await env.ready()
    sent = await env.client.post(
        env.url(account, "/send"),
        json={
            "to": ["Alice@Example.com"],
            "cc": ["carol@example.com"],
            "bcc": ["boss@example.com"],
            "subject": "  周报\n第 3 周 ",
            "body": "本周完成：\n- 邮件功能",
        },
        headers=env.headers(write=True),
    )
    assert sent.status_code == 200, sent.text
    record = sent.json()
    assert record["status"] == "sent" and record["actor_kind"] == "user"
    assert record["actor_name"] == "Owner" and record["sender"] == MAILBOX
    assert record["to"] == ["alice@example.com"] and record["bcc_count"] == 1
    assert record["subject"] == "周报 第 3 周" and record["gmail_message_id"]
    box = env.fake.mailboxes[MAILBOX]
    [message] = box.sent
    assert message["From"] == MAILBOX and message["To"] == "alice@example.com"
    assert message["Cc"] == "carol@example.com" and message["Bcc"] == "boss@example.com"
    assert str(message["Subject"]) == "周报 第 3 周"
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    payload = message.get_payload(decode=True)
    assert isinstance(payload, bytes) and payload.decode().strip() == "本周完成：\n- 邮件功能"
    assert message["Message-ID"].endswith("@gmail.com>")

    original = env.fake.add_message(
        MAILBOX, subject="询价", message_id_header="<abc@client.example.com>", references="<r0@x>"
    )
    reply = await env.client.post(
        env.url(account, "/send"),
        json={
            "to": ["alice@example.com"],
            "body": "收到",
            "reply_to_message_id": original["id"],
        },
        headers=env.headers(write=True),
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["subject"] == "Re: 询价"
    assert reply.json()["gmail_thread_id"] == original["threadId"]
    assert box.sent_bodies[-1]["threadId"] == original["threadId"]
    answer = box.sent[-1]
    assert answer["In-Reply-To"] == "<abc@client.example.com>"
    assert answer["References"] == "<r0@x> <abc@client.example.com>"

    listed = await env.client.get("/api/v1/mail/sends", headers=env.headers())
    assert [s["subject"] for s in listed.json()["items"]] == ["Re: 询价", "周报 第 3 周"]
    body_chars = await run_sql(env.database_url, "SELECT max(body_chars) FROM workspace.mail_sends")
    assert body_chars > 0
    audit = await run_sql(
        env.database_url, "SELECT count(*) FROM workspace.audit_logs WHERE action = 'mail.sent'"
    )
    assert audit == 2


async def test_send_checks_unknown_and_rate_limit(env: Env) -> None:
    account = await env.ready()
    box = env.fake.mailboxes[MAILBOX]

    async def send(**body: Any) -> httpx.Response:
        payload = {"to": ["alice@example.com"], "subject": "主题", "body": "正文", **body}
        return await env.client.post(
            env.url(account, "/send"), json=payload, headers=env.headers(write=True)
        )

    secret = await send(body="key: ghp_" + "a" * 36)
    assert secret.status_code == 422 and secret.json()["error"]["code"] == "SECRET_BLOCKED"
    bad = await send(to=["not an address"])
    assert bad.status_code == 422
    header_injection = await send(to=["a@example.com\nBcc: evil@example.com"])
    assert header_injection.status_code == 422
    many = await send(
        to=[f"u{i}@example.com" for i in range(15)], cc=[f"c{i}@x.com" for i in range(6)]
    )
    assert many.status_code == 422
    no_subject = await send(subject="")
    assert no_subject.status_code == 422
    assert box.sent == [] and box.sent_bodies == []
    count = await run_sql(env.database_url, "SELECT count(*) FROM workspace.mail_sends")
    assert count == 0  # 校验没过的不留记录

    env.fake.send_failure = "timeout"
    unknown = await send()
    assert unknown.status_code == 504 and unknown.json()["error"]["code"] == "TIMEOUT_UNKNOWN"
    assert len(box.sent_bodies) == 1  # 写请求不自动重试
    status = await run_sql(env.database_url, "SELECT status FROM workspace.mail_sends")
    assert status == "unknown"

    env.fake.send_failure = "rate"
    limited = await send()
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"
    env.fake.send_failure = "server"
    broken = await send()
    assert broken.status_code == 503
    statuses = await run_sql(
        env.database_url,
        "SELECT string_agg(status, ',' ORDER BY created_at) FROM workspace.mail_sends",
    )
    assert statuses == "unknown,failed,failed"
    listed = await env.client.get("/api/v1/mail/sends", headers=env.headers())
    assert [s["error_code"] for s in listed.json()["items"]] == [
        "EXTERNAL_UNAVAILABLE",
        "RATE_LIMITED",
        "TIMEOUT_UNKNOWN",
    ]


# ---------------------------------------------------------------- 邮箱设置、断开与权限


async def test_patch_and_disconnect(env: Env) -> None:
    account = await env.ready()
    stale = await env.client.patch(
        env.url(account),
        json={"version": account["version"] + 1, "agents_allowed": False},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    patched = await env.client.patch(
        env.url(account),
        json={"version": account["version"], "agents_allowed": False, "agent_signature": False},
        headers=env.headers(write=True),
    )
    assert patched.status_code == 200
    assert patched.json()["agents_allowed"] is False and patched.json()["agent_signature"] is False
    assert patched.json()["version"] == account["version"] + 1

    refresh_token = next(iter(env.fake.refresh_tokens))
    gone = await env.client.delete(env.url(account), headers=env.headers(write=True))
    assert gone.status_code == 204
    assert refresh_token in env.fake.revoked
    listed = await env.client.get("/api/v1/mail/accounts", headers=env.headers())
    assert listed.json()["items"] == []
    stored = await run_sql(
        env.database_url, "SELECT status || ':' || refresh_token_ref FROM workspace.mail_accounts"
    )
    assert stored == "disconnected:"
    assert await app_of(env.client).state.redis.get(f"mail_at:{account['id']}") is None
    hidden = await env.client.get(env.url(account, "/labels"), headers=env.headers())
    assert hidden.status_code == 404


async def test_roles_and_workspace_scope(env: Env) -> None:
    account = await env.ready()
    bob = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), bob, "member")
    other_workspace = await add_workspace(env.database_url, bob, "Bob 的空间")
    async with env.make_client(**ALL) as other:
        app_of(other).state.connectors.transport = env.fake.transport
        assert (await other.post("/api/v1/auth/login", json=BOB)).status_code == 200
        member = {"X-Workspace-Id": env.workspace_id}
        for path in (
            "/api/v1/mail/settings",
            "/api/v1/mail/accounts",
            "/api/v1/mail/sends",
            f"/api/v1/mail/accounts/{account['id']}/messages",
        ):
            response = await other.get(path, headers=member)
            assert response.status_code == 403, path
        send = await other.post(
            f"/api/v1/mail/accounts/{account['id']}/send",
            json={"to": ["a@example.com"], "subject": "x", "body": "y"},
            headers={**member, **csrf(other)},
        )
        assert send.status_code == 403
        # 自己是 owner 的另一个工作空间：看不到这个邮箱
        own = {"X-Workspace-Id": str(other_workspace)}
        assert (await other.get("/api/v1/mail/accounts", headers=own)).json()["items"] == []
        hidden = await other.get(f"/api/v1/mail/accounts/{account['id']}/labels", headers=own)
        assert hidden.status_code == 404
