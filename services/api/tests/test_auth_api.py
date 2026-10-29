"""认证接口集成测试（T-0010）：真实 PostgreSQL（已迁移）+ Redis。

覆盖验收场景 A01（空库首次安装）、A02（未登录 401）、A18（登出后旧会话失效），
以及 CSRF、来源校验、登录限流、会话过期与停用账户。
"""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from tests.helpers import BASE_URL, OWNER, ClientFactory, csrf, run_sql

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(make_client: ClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


async def test_first_setup_creates_owner_workspace_and_session(client: httpx.AsyncClient) -> None:
    """A01：空库首次安装。"""
    assert (await client.get("/api/v1/setup/status")).json() == {"setup_required": True}

    wrong = await client.post("/api/v1/setup", json={**OWNER, "setup_token": "wrong"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "SETUP_TOKEN_INVALID"

    response = await client.post("/api/v1/setup", json=OWNER)
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert [(w["name"], w["role"]) for w in body["workspaces"]] == [("我的工作空间", "owner")]

    set_cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookies if c.startswith("ws_session="))
    csrf_cookie = next(c for c in set_cookies if c.startswith("ws_csrf="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" not in session_cookie  # 测试环境是 http；生产环境会加 Secure

    assert (await client.get("/api/v1/setup/status")).json() == {"setup_required": False}
    again = await client.post("/api/v1/setup", json=OWNER)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "SETUP_COMPLETED"

    me = await client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]
    workspaces = (await client.get("/api/v1/workspaces")).json()
    assert workspaces["total_estimate"] == 1
    assert workspaces["items"][0]["role"] == "owner"


async def test_concurrent_setup_succeeds_only_once(make_client: ClientFactory) -> None:
    async with make_client() as first, make_client() as second:
        results = await asyncio.gather(
            first.post("/api/v1/setup", json=OWNER),
            second.post("/api/v1/setup", json={**OWNER, "email": "other@example.com"}),
        )
    assert sorted(r.status_code for r in results) == [201, 409]


async def test_setup_is_refused_when_token_not_configured(make_client: ClientFactory) -> None:
    async with make_client(setup_token=None) as client:
        response = await client.post("/api/v1/setup", json=OWNER)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SETUP_TOKEN_INVALID"


async def test_private_endpoints_require_a_session(client: httpx.AsyncClient) -> None:
    """A02：未登录访问私有接口返回 401，不泄露内容。"""
    for path in ("/api/v1/me", "/api/v1/workspaces"):
        response = await client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    forged = await client.get("/api/v1/me", headers={"Cookie": "ws_session=forged-token"})
    assert forged.status_code == 401


async def test_logout_requires_csrf_and_revokes_the_session(client: httpx.AsyncClient) -> None:
    """A18：登出后旧会话不能再访问私有接口。"""
    await client.post("/api/v1/setup", json=OWNER)
    old_token = client.cookies["ws_session"]

    missing = await client.post("/api/v1/auth/logout")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_FAILED"
    wrong = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert wrong.status_code == 403

    response = await client.post("/api/v1/auth/logout", headers=csrf(client))
    assert response.status_code == 204
    assert client.cookies.get("ws_session") is None
    assert client.cookies.get("ws_csrf") is None

    replay = await client.get("/api/v1/me", headers={"Cookie": f"ws_session={old_token}"})
    assert replay.status_code == 401


async def test_login_errors_do_not_reveal_whether_account_exists(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/api/v1/setup", json=OWNER)
    await client.post("/api/v1/auth/logout", headers=csrf(client))

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": OWNER["email"], "password": "not the password"}
    )
    unknown_user = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["error"]["code"] == unknown_user.json()["error"]["code"]
    assert wrong_password.json()["error"]["message"] == unknown_user.json()["error"]["message"]

    ok = await client.post(
        "/api/v1/auth/login",
        json={"email": "OWNER@example.COM", "password": OWNER["password"]},
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == "owner@example.com"
    assert (await client.get("/api/v1/me")).status_code == 200


async def test_login_is_rate_limited_per_account(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/setup", json=OWNER)
    await client.post("/api/v1/auth/logout", headers=csrf(client))
    bad = {"email": OWNER["email"], "password": "not the password"}
    for _ in range(3):
        assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 401

    limited = await client.post("/api/v1/auth/login", json=bad)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) > 0
    # 锁定期间即使密码正确也拒绝
    good = {"email": OWNER["email"], "password": OWNER["password"]}
    assert (await client.post("/api/v1/auth/login", json=good)).status_code == 429


async def test_untrusted_origin_is_rejected(client: httpx.AsyncClient) -> None:
    body = {"email": "nobody@example.com", "password": "whatever"}
    evil_origin = await client.post(
        "/api/v1/auth/login", json=body, headers={"Origin": "https://evil.example"}
    )
    assert evil_origin.status_code == 403
    assert evil_origin.json()["error"]["code"] == "CSRF_FAILED"
    evil_referer = await client.post(
        "/api/v1/auth/login", json=body, headers={"Referer": "https://evil.example/page"}
    )
    assert evil_referer.status_code == 403
    same_origin = await client.post("/api/v1/auth/login", json=body, headers={"Origin": BASE_URL})
    assert same_origin.status_code == 401  # 通过来源校验，进入正常的登录失败


async def test_expired_and_idle_sessions_are_rejected(
    client: httpx.AsyncClient, migrated_database_url: str
) -> None:
    await client.post("/api/v1/setup", json=OWNER)
    await run_sql(
        migrated_database_url,
        "UPDATE workspace.auth_sessions SET expires_at = now() - interval '1s'",
    )
    assert (await client.get("/api/v1/me")).status_code == 401

    await client.post(
        "/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]}
    )
    assert (await client.get("/api/v1/me")).status_code == 200
    await run_sql(
        migrated_database_url,
        "UPDATE workspace.auth_sessions SET last_seen_at = now() - interval '8 days'",
    )
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_disabled_user_cannot_use_or_create_sessions(
    client: httpx.AsyncClient, migrated_database_url: str
) -> None:
    await client.post("/api/v1/setup", json=OWNER)
    await run_sql(migrated_database_url, "UPDATE workspace.users SET status = 'disabled'")
    assert (await client.get("/api/v1/me")).status_code == 401
    login = await client.post(
        "/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]}
    )
    assert login.status_code == 401
    assert login.json()["error"]["code"] == "INVALID_CREDENTIALS"
