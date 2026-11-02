"""集成配置接口（T-0020 起；T-0082 移除 Vikunja 后只有 GitHub）。

真实 PostgreSQL / Redis + 内存版 GitHub。

覆盖 Sprint 3 / Sprint 4 契约第 4 节：只写不读的 Token（加密保存、不进审计）、
每个工作空间 1 个同类集成、乐观并发、改 Token 后回到 pending、停用 / 启用、权限、
工作空间隔离、缺少加密密钥。
测试连接与同步见 tests/test_github.py。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.fake_github import TOKEN, FakeGitHub
from tests.helpers import OWNER, ClientFactory, run_sql
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


def github_body(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": "github",
        "name": "GitHub",
        "api_token": TOKEN,
        "repositories": ["octo/app"],
        **overrides,
    }


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    fake = FakeGitHub()
    fake.add_repo("octo/app")
    async with open_env(
        make_client, migrated_database_url, integration_allowed_hosts="api.github.com"
    ) as value:
        app_of(value.client).state.connectors.transport = fake.transport
        yield value


async def add(env: Env, **overrides: Any) -> httpx.Response:
    return await env.client.post(
        "/api/v1/integrations", json=github_body(**overrides), headers=env.headers(write=True)
    )


async def connect(env: Env) -> dict[str, Any]:
    created = await add(env)
    assert created.status_code == 201, created.text
    tested = await env.client.post(
        f"/api/v1/integrations/{created.json()['id']}/test", headers=env.headers(write=True)
    )
    assert tested.status_code == 200 and tested.json()["ok"] is True, tested.text
    items: list[dict[str, Any]] = (await env.get("/api/v1/integrations"))["items"]
    assert len(items) == 1
    return items[0]


async def test_token_is_write_only_and_encrypted(env: Env) -> None:
    created = await add(env)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["status"], body["token_set"], body["provider"]) == ("pending", True, "github")
    assert body["sync"]["status"] == "unavailable"
    assert TOKEN not in created.text
    listed = await env.client.get("/api/v1/integrations", headers=env.headers())
    assert TOKEN not in listed.text
    stored = await run_sql(
        env.database_url, "SELECT credential_ref FROM workspace.integration_accounts"
    )
    assert stored.startswith("enc:") and TOKEN not in stored
    audits = await run_sql(
        env.database_url,
        "SELECT string_agg(action || ' ' || metadata_redacted_json::text, '\n')"
        " FROM workspace.audit_logs WHERE action LIKE 'integration.%'",
    )
    assert "integration.created" in audits
    assert "github_pat_" not in audits


async def test_one_integration_per_provider(env: Env) -> None:
    assert (await add(env)).status_code == 201
    second = await add(env, name="第二个")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "vikunja"},  # T-0082 移除
        {"provider": "affine"},
        {"api_token": "tk_" + "a" * 40},
        {"base_url": "https://github.example.com"},
        {"repositories": ["not a repo"]},
        {"unexpected": True},
    ],
)
async def test_invalid_bodies_are_rejected(env: Env, overrides: dict[str, object]) -> None:
    response = await add(env, **overrides)
    assert response.status_code == 422, overrides
    assert "tk_aaaa" not in response.text


async def test_update_uses_versions_and_resets_status(env: Env) -> None:
    integration = await connect(env)
    path = f"/api/v1/integrations/{integration['id']}"

    async def patch(body: dict[str, Any]) -> httpx.Response:
        return await env.client.patch(path, json=body, headers=env.headers(write=True))

    stale = await patch({"version": integration["version"] - 1, "name": "改名"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    renamed = await patch({"version": integration["version"], "name": "改名"})
    assert renamed.status_code == 200
    assert renamed.json()["status"] == "active"  # 只改名不需要重新测试
    rotated = await patch(
        {"version": renamed.json()["version"], "api_token": "github_pat_" + "c" * 40}
    )
    assert rotated.status_code == 200
    assert rotated.json()["status"] == "pending"
    assert "github_pat_" not in rotated.text
    disabled = await patch({"version": rotated.json()["version"], "enabled": False})
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["sync"]["status"] == "not_configured"
    enabled = await patch({"version": disabled.json()["version"], "enabled": True})
    assert enabled.json()["status"] == "pending"
    for removed in [{"public_url": "https://example.com"}, {"base_url": "https://example.com"}]:
        response = await patch({"version": enabled.json()["version"], **removed})
        assert response.status_code == 422, removed
    audits = await run_sql(
        env.database_url,
        "SELECT string_agg(metadata_redacted_json::text, '\n') FROM workspace.audit_logs"
        " WHERE action = 'integration.updated'",
    )
    assert '"api_token"' in audits  # 只记字段名
    assert "github_pat_" not in audits


async def test_only_admins_manage_integrations(env: Env) -> None:
    integration = await connect(env)
    async with env.member("member@example.com", "member") as member:
        headers = {"X-Workspace-Id": env.workspace_id, "X-CSRF-Token": member.cookies["ws_csrf"]}
        listed = await member.get("/api/v1/integrations", headers=headers)
        assert listed.status_code == 200
        assert TOKEN not in listed.text
        attempts = [
            await member.post("/api/v1/integrations", json=github_body(), headers=headers),
            await member.patch(
                f"/api/v1/integrations/{integration['id']}",
                json={"version": integration["version"], "name": "x"},
                headers=headers,
            ),
            await member.post(f"/api/v1/integrations/{integration['id']}/test", headers=headers),
            await member.post(f"/api/v1/integrations/{integration['id']}/sync", headers=headers),
        ]
        assert [a.status_code for a in attempts] == [403, 403, 403, 403]


async def test_integrations_are_scoped_to_the_workspace(env: Env) -> None:
    integration = await connect(env)
    other_workspace = str(uuid.uuid4())
    response = await env.client.patch(
        f"/api/v1/integrations/{integration['id']}",
        json={"version": integration["version"], "name": "x"},
        headers={**env.headers(write=True), "X-Workspace-Id": other_workspace},
    )
    assert response.status_code in {403, 404}
    missing = await env.client.post(
        f"/api/v1/integrations/{uuid.uuid4()}/test", headers=env.headers(write=True)
    )
    assert missing.status_code == 404


async def test_missing_encryption_key_is_a_clear_503(make_client: ClientFactory) -> None:
    async with make_client(credential_encryption_key=None) as client:
        setup = (await client.post("/api/v1/setup", json=OWNER)).json()
        headers = {
            "X-Workspace-Id": setup["workspaces"][0]["id"],
            "X-CSRF-Token": client.cookies["ws_csrf"],
        }
        response = await client.post("/api/v1/integrations", json=github_body(), headers=headers)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EXTERNAL_UNAVAILABLE"
