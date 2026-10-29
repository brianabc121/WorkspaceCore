"""会话管理与修改密码的接口集成测试（T-0011，S1-05）：真实 PostgreSQL（已迁移）+ Redis。"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest

from tests.helpers import BOB, OWNER, ClientFactory, add_user, csrf

pytestmark = pytest.mark.integration

LOGIN = {"email": OWNER["email"], "password": OWNER["password"]}
NEW_PASSWORD = "a brand new passphrase"  # noqa: S105


@dataclass
class Devices:
    laptop: httpx.AsyncClient
    phone: httpx.AsyncClient
    database_url: str


@pytest.fixture
async def devices(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Devices]:
    """同一个账户在两台设备上登录：laptop 完成首次安装，phone 登录。"""
    async with make_client() as laptop, make_client() as phone:
        assert (await laptop.post("/api/v1/setup", json=OWNER)).status_code == 201
        assert (await phone.post("/api/v1/auth/login", json=LOGIN)).status_code == 200
        yield Devices(laptop=laptop, phone=phone, database_url=migrated_database_url)


async def sessions(client: httpx.AsyncClient) -> list[dict[str, object]]:
    response = await client.get("/api/v1/auth/sessions")
    assert response.status_code == 200
    items: list[dict[str, object]] = response.json()["items"]
    return items


async def test_list_and_revoke_other_session(devices: Devices) -> None:
    laptop, phone = devices.laptop, devices.phone
    listed = await sessions(laptop)
    assert len(listed) == 2
    assert [s["current"] for s in listed].count(True) == 1
    phone_session = next(s["id"] for s in listed if not s["current"])

    no_csrf = await laptop.delete(f"/api/v1/auth/sessions/{phone_session}")
    assert no_csrf.status_code == 403

    revoked = await laptop.delete(f"/api/v1/auth/sessions/{phone_session}", headers=csrf(laptop))
    assert revoked.status_code == 204
    assert laptop.cookies.get("ws_session") is not None  # 撤销别的会话不影响当前会话
    assert (await phone.get("/api/v1/me")).status_code == 401
    assert [s["current"] for s in await sessions(laptop)] == [True]

    again = await laptop.delete(f"/api/v1/auth/sessions/{phone_session}", headers=csrf(laptop))
    assert again.status_code == 404
    unknown = await laptop.delete(f"/api/v1/auth/sessions/{uuid.uuid4()}", headers=csrf(laptop))
    assert unknown.status_code == 404


async def test_cannot_revoke_someone_elses_session(
    devices: Devices, make_client: ClientFactory
) -> None:
    await add_user(devices.database_url, BOB["email"], BOB["password"])
    async with make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        bob_session = (await sessions(bob))[0]["id"]
        attempt = await devices.laptop.delete(
            f"/api/v1/auth/sessions/{bob_session}", headers=csrf(devices.laptop)
        )
        assert attempt.status_code == 404
        assert (await bob.get("/api/v1/me")).status_code == 200


async def test_revoking_the_current_session_signs_out(devices: Devices) -> None:
    laptop = devices.laptop
    old_token = laptop.cookies["ws_session"]
    current = next(s["id"] for s in await sessions(laptop) if s["current"])
    response = await laptop.delete(f"/api/v1/auth/sessions/{current}", headers=csrf(laptop))
    assert response.status_code == 204
    assert laptop.cookies.get("ws_session") is None
    replay = await laptop.get("/api/v1/me", headers={"Cookie": f"ws_session={old_token}"})
    assert replay.status_code == 401
    assert (await devices.phone.get("/api/v1/me")).status_code == 200


async def test_change_password_revokes_every_other_session(devices: Devices) -> None:
    laptop, phone = devices.laptop, devices.phone
    old_token = laptop.cookies["ws_session"]
    body = {"current_password": OWNER["password"], "new_password": NEW_PASSWORD}

    assert (await laptop.post("/api/v1/auth/password", json=body)).status_code == 403  # 缺 CSRF
    too_short = await laptop.post(
        "/api/v1/auth/password", json={**body, "new_password": "short"}, headers=csrf(laptop)
    )
    assert too_short.status_code == 422
    wrong = await laptop.post(
        "/api/v1/auth/password",
        json={**body, "current_password": "not the password"},
        headers=csrf(laptop),
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "INVALID_CURRENT_PASSWORD"

    changed = await laptop.post("/api/v1/auth/password", json=body, headers=csrf(laptop))
    assert changed.status_code == 204
    assert laptop.cookies["ws_session"] != old_token
    assert (await laptop.get("/api/v1/me")).status_code == 200
    assert [s["current"] for s in await sessions(laptop)] == [True]
    assert (await phone.get("/api/v1/me")).status_code == 401
    replay = await phone.get("/api/v1/me", headers={"Cookie": f"ws_session={old_token}"})
    assert replay.status_code == 401

    assert (await phone.post("/api/v1/auth/login", json=LOGIN)).status_code == 401
    new_login = {"email": OWNER["email"], "password": NEW_PASSWORD}
    assert (await phone.post("/api/v1/auth/login", json=new_login)).status_code == 200

    workspace = (await laptop.get("/api/v1/workspaces")).json()["items"][0]["id"]
    logs = await laptop.get("/api/v1/audit-logs", headers={"X-Workspace-Id": workspace})
    entries = {e["action"]: e for e in logs.json()["items"]}
    assert entries["auth.password_changed"]["metadata"] == {"revoked_sessions": 2}
    assert "auth.password_change_failed" in entries
    assert NEW_PASSWORD not in logs.text


async def test_password_change_is_rate_limited(devices: Devices) -> None:
    laptop = devices.laptop
    wrong = {"current_password": "not the password", "new_password": NEW_PASSWORD}
    for _ in range(3):
        response = await laptop.post("/api/v1/auth/password", json=wrong, headers=csrf(laptop))
        assert response.status_code == 400
    limited = await laptop.post("/api/v1/auth/password", json=wrong, headers=csrf(laptop))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    right = {"current_password": OWNER["password"], "new_password": NEW_PASSWORD}
    assert (
        await laptop.post("/api/v1/auth/password", json=right, headers=csrf(laptop))
    ).status_code == 429
