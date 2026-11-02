"""n8n 的登录账号（T-0080；T-0082 移除 Vikunja 后只有 n8n）：真实 PostgreSQL + Redis。

覆盖：只有 admin 以上能读写（member、viewer 403，非成员 404）；密码加密保存、列表不含密码；
查看密码单独请求、带 CSRF、写审计、响应不缓存；乐观并发；删除；登录页地址取自 n8n 连接；
审计与日志里没有密码。
"""

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest

from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql
from tests.workspace_support import CONNECTOR_SETTINGS

pytestmark = pytest.mark.integration

PASSWORD = "fake app password"  # noqa: S105  测试用的假值
N8N_PASSWORD = "fake n8n owner password"  # noqa: S105
MEMBER = {"email": "member@example.com", "password": "member password 123"}


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def logins(self) -> dict[str, dict[str, object]]:
        response = await self.client.get("/api/v1/app-logins", headers=self.headers())
        assert response.status_code == 200, response.text
        return {item["app"]: item for item in response.json()["items"]}

    async def put(self, app: str, body: dict[str, object]) -> httpx.Response:
        return await self.client.put(
            f"/api/v1/app-logins/{app}", json=body, headers=self.headers(write=True)
        )

    async def reveal(self, app: str) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/app-logins/{app}/reveal", headers=self.headers(write=True)
        )

    async def audit(self) -> list[dict[str, object]]:
        response = await self.client.get("/api/v1/audit-logs", headers=self.headers())
        assert response.status_code == 200
        return [e for e in response.json()["items"] if str(e["action"]).startswith("app_login.")]


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(**CONNECTOR_SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


async def test_nothing_saved(env: Env) -> None:
    response = await env.client.get("/api/v1/app-logins", headers=env.headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [item["app"] for item in items] == ["n8n"]
    assert all(item["configured"] is False and item["password_set"] is False for item in items)
    assert all(item["login_url"] is None and item["version"] is None for item in items)
    missing = await env.reveal("n8n")
    assert missing.status_code == 404


async def test_save_reveal_update_delete(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    # 新建时必须给用户名
    no_username = await env.put("n8n", {"password": PASSWORD})
    assert no_username.status_code == 422
    created = await env.put("n8n", {"username": " workspace ", "password": PASSWORD})
    assert created.status_code == 200, created.text
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["configured"] and body["password_set"] and body["username"] == "workspace"
    assert body["version"] == 1 and "password" not in body
    assert PASSWORD not in created.text

    # 数据库里只有密文引用
    ref = await run_sql(
        env.database_url, "SELECT password_ref FROM workspace.app_logins WHERE app = 'n8n'"
    )
    assert ref.startswith("enc:v1:") and PASSWORD not in ref

    listed = await env.client.get("/api/v1/app-logins", headers=env.headers())
    assert PASSWORD not in listed.text

    revealed = await env.reveal("n8n")
    assert revealed.status_code == 200
    assert revealed.headers["cache-control"] == "no-store"
    assert revealed.json() == {"app": "n8n", "username": "workspace", "password": PASSWORD}

    # 查看要 CSRF
    no_csrf = await env.client.post(
        "/api/v1/app-logins/n8n/reveal", headers={"X-Workspace-Id": env.workspace_id}
    )
    assert no_csrf.status_code == 403

    # 只改用户名不动密码；版本不对 409；修改要带版本
    stale = await env.put("n8n", {"version": 9, "username": "other"})
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == 1
    no_version = await env.put("n8n", {"username": "other"})
    assert no_version.status_code == 422
    renamed = await env.put("n8n", {"version": 1, "username": "other"})
    assert renamed.status_code == 200 and renamed.json()["version"] == 2
    again = await env.reveal("n8n")
    assert again.json()["password"] == PASSWORD and again.json()["username"] == "other"
    empty = await env.put("n8n", {"version": 2})
    assert empty.status_code == 422

    for unknown in ("grafana", "vikunja"):  # T-0082 移除了 Vikunja
        assert (await env.put(unknown, {"username": "admin"})).status_code == 422, unknown

    # 删除：版本不对 409；删除后回到未保存
    wrong = await env.client.delete(
        "/api/v1/app-logins/n8n?version=1", headers=env.headers(write=True)
    )
    assert wrong.status_code == 409
    deleted = await env.client.delete(
        "/api/v1/app-logins/n8n?version=2", headers=env.headers(write=True)
    )
    assert deleted.status_code == 204
    assert (await env.logins())["n8n"]["configured"] is False
    gone = await env.client.delete(
        "/api/v1/app-logins/n8n?version=2", headers=env.headers(write=True)
    )
    assert gone.status_code == 404

    # 可以先只保存用户名，之后再补密码
    only_user = await env.put("n8n", {"username": "owner@example.com"})
    assert only_user.status_code == 200 and only_user.json()["password_set"] is False
    assert (await env.reveal("n8n")).status_code == 404
    with_password = await env.put("n8n", {"version": 1, "password": N8N_PASSWORD})
    assert with_password.json()["password_set"] is True
    assert (await env.reveal("n8n")).json()["password"] == N8N_PASSWORD

    # 审计：每次查看都记；元数据只有应用名与字段名
    events = await env.audit()
    actions = [e["action"] for e in events]
    assert actions.count("app_login.revealed") == 3
    assert {"app_login.created", "app_login.updated", "app_login.deleted"} <= set(actions)
    for event in events:
        assert PASSWORD not in str(event) and N8N_PASSWORD not in str(event)
    stored = await run_sql(
        env.database_url,
        "SELECT string_agg(metadata_redacted_json::text, ' ') FROM workspace.audit_logs"
        " WHERE action LIKE 'app_login.%'",
    )
    assert PASSWORD not in stored and N8N_PASSWORD not in stored
    assert PASSWORD not in caplog.text and N8N_PASSWORD not in caplog.text


async def test_login_url_comes_from_the_n8n_connection(env: Env) -> None:
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.automation_connections"
        " (workspace_id, base_url, public_url, api_key_ref, trigger_secret_ref)"
        " VALUES ($1, 'http://n8n:5678', 'https://n8n.example.com/', 'x', 'y')",
        uuid.UUID(env.workspace_id),
    )
    logins = await env.logins()
    assert logins["n8n"]["login_url"] == "https://n8n.example.com/"


async def test_only_admins(env: Env, make_client: ClientFactory) -> None:
    saved = await env.put("n8n", {"username": "workspace", "password": PASSWORD})
    assert saved.status_code == 200
    user_id = await add_user(env.database_url, MEMBER["email"], MEMBER["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user_id, "viewer")
    async with make_client(**CONNECTOR_SETTINGS) as other:
        await other.post("/api/v1/auth/login", json=MEMBER)
        headers = {"X-Workspace-Id": env.workspace_id, **csrf(other)}
        for role in ("viewer", "member"):
            await run_sql(
                env.database_url,
                "UPDATE workspace.workspace_members SET role = $1 WHERE user_id = $2",
                role,
                user_id,
            )
            listed = await other.get("/api/v1/app-logins", headers=headers)
            revealed = await other.post("/api/v1/app-logins/n8n/reveal", headers=headers)
            changed = await other.put(
                "/api/v1/app-logins/n8n", json={"version": 1, "password": "x"}, headers=headers
            )
            assert (listed.status_code, revealed.status_code, changed.status_code) == (
                403,
                403,
                403,
            ), role
            assert PASSWORD not in revealed.text
        await run_sql(
            env.database_url,
            "UPDATE workspace.workspace_members SET role = 'admin' WHERE user_id = $1",
            user_id,
        )
        allowed = await other.post("/api/v1/app-logins/n8n/reveal", headers=headers)
        assert allowed.status_code == 200 and allowed.json()["password"] == PASSWORD

        # 别的工作空间看不到
        foreign = await other.get(
            "/api/v1/app-logins", headers={"X-Workspace-Id": str(uuid.uuid4())}
        )
        assert foreign.status_code == 404
