"""集成配置接口的集成测试（T-0020，S3-02）：真实 PostgreSQL / Redis + 内存版 Vikunja。

覆盖 Sprint 3 契约第 4 节：保存时只写不读的 Token（加密保存、不进审计）、SSRF 检查、
每个工作空间 1 个同类集成、乐观并发、测试连接的逐项检查与状态、测试通过后的全量同步、权限。
"""

import uuid
from collections.abc import AsyncIterator

import pytest

from tests.fake_vikunja import TOKEN
from tests.helpers import OWNER, ClientFactory, run_sql
from tests.vikunja_support import Env, integration_body, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def test_token_is_write_only_and_encrypted(env: Env) -> None:
    created = await env.add_integration()
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["token_set"] is True
    assert body["base_url"] == "http://vikunja.test"
    assert body["public_url"] == "https://tasks.example.com"
    assert (body["tested_version"], body["api_version"]) == ("2.7.1", "v2")
    assert body["sync"]["status"] == "unavailable"
    assert TOKEN not in created.text
    listed = await env.client.get("/api/v1/integrations", headers=env.headers())
    assert TOKEN not in listed.text
    stored = await run_sql(
        env.database_url, "SELECT credential_ref FROM workspace.integration_accounts"
    )
    assert stored.startswith("enc:")
    assert TOKEN not in stored
    secret_ref = await run_sql(
        env.database_url, "SELECT webhook_secret_ref FROM workspace.integrations"
    )
    assert secret_ref.startswith("enc:")
    audits = await run_sql(
        env.database_url,
        "SELECT string_agg(action || ' ' || metadata_redacted_json::text, '\n')"
        " FROM workspace.audit_logs WHERE action LIKE 'integration.%'",
    )
    assert "integration.created" in audits
    assert "tk_" not in audits


async def test_test_connection_activates_and_syncs(env: Env) -> None:
    work = env.fake.add_project("工作")
    env.fake.add_project("归档", archived=True)
    env.fake.add_task(work["id"], "写周报", priority=3)
    env.fake.add_task(work["id"], "已完成", done=True)
    created = (await env.add_integration()).json()
    result = await env.test_integration(created["id"])
    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["compatible"] is True
    assert (result["instance_version"], result["api_version"]) == ("2.7.1", "v2")
    assert [(c["name"], c["ok"]) for c in result["checks"]] == [
        ("reachable", True),
        ("version", True),
        ("token", True),
        ("tasks_read", True),
        ("webhooks", True),
    ]
    integration = await env.integration()
    assert integration["status"] == "active"
    assert integration["instance_version"] == "2.7.1"
    assert integration["last_tested_at"] is not None
    assert integration["webhooks"] == {"mode": "auto", "registered_projects": 0, "reason": None}
    assert integration["sync"]["status"] == "ok"
    assert integration["version"] == created["version"] + 1
    # 首次全量同步：项目（含归档）与任务（含已完成）都进了投影
    assert set(await env.projects()) == {"工作", "归档"}
    assert {t["title"] for t in await env.tasks(status="all")} == {"写周报", "已完成"}


async def test_failed_checks_set_the_status(env: Env) -> None:
    created = (await env.add_integration()).json()
    env.fake.fault("GET", "/info", "connect")
    unreachable = await env.test_integration(created["id"])
    assert unreachable["ok"] is False
    assert unreachable["status"] == "unavailable"
    assert unreachable["checks"] == [{"name": "reachable", "ok": False, "code": "connect_failed"}]
    assert unreachable["api_version"] is None

    env.fake.token = "tk_" + "z" * 40
    rejected = await env.test_integration(created["id"])
    assert rejected["status"] == "unauthorized"
    assert rejected["checks"][-1] == {"name": "token", "ok": False, "code": "AUTH_FAILED"}
    integration = await env.integration()
    assert integration["sync"]["status"] == "unauthorized"
    # 只有任务读权限、没有 Webhook 权限：仍然可用，只靠周期对账
    env.fake.token = TOKEN
    env.fake.add_project("工作")
    env.fake.webhooks_allowed = False
    limited = await env.test_integration(created["id"])
    assert limited["ok"] is True
    assert limited["checks"][-1] == {
        "name": "webhooks",
        "ok": False,
        "code": "missing_permission",
    }
    assert (await env.integration())["webhooks"]["mode"] == "unavailable"


async def test_untested_version_is_a_warning_not_an_error(env: Env) -> None:
    env.fake.version = "v2.8.0"
    created = (await env.add_integration()).json()
    result = await env.test_integration(created["id"])
    assert result["ok"] is True
    assert result["compatible"] is False
    assert result["instance_version"] == "2.8.0"
    assert {"name": "version", "ok": False, "code": "untested_version"} in result["checks"]
    assert result["warnings"] and "2.8.0" in result["warnings"][0]


async def test_one_integration_per_provider(env: Env) -> None:
    assert (await env.add_integration()).status_code == 201
    second = await env.add_integration(name="第二个")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:3456", "http://169.254.169.254", "http://10.1.2.3", "http://[::1]"],
)
async def test_private_addresses_are_rejected(env: Env, base_url: str) -> None:
    response = await env.add_integration(base_url=base_url)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["errors"][0]["loc"] == ["body", "base_url"]
    assert base_url.split("//")[1] not in response.text  # 不回显地址
    assert env.fake.requests == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "ftp://vikunja.test"},
        {"base_url": "http://user:pass@vikunja.test"},
        {"api_token": "not-a-vikunja-token"},
        {"provider": "affine"},
        {"unexpected": True},
    ],
)
async def test_invalid_bodies_are_rejected(env: Env, overrides: dict[str, object]) -> None:
    response = await env.add_integration(**overrides)
    assert response.status_code == 422
    assert "not-a-vikunja-token" not in response.text
    assert "pass@" not in response.text


async def test_update_uses_versions_and_resets_status(env: Env) -> None:
    integration = await env.connect()
    path = f"/api/v1/integrations/{integration['id']}"
    stale = await env.client.patch(
        path,
        json={"version": integration["version"] - 1, "name": "改名"},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    renamed = await env.client.patch(
        path,
        json={"version": integration["version"], "name": "改名"},
        headers=env.headers(write=True),
    )
    assert renamed.status_code == 200
    assert renamed.json()["status"] == "active"  # 只改名不需要重新测试
    rotated = await env.client.patch(
        path,
        json={"version": renamed.json()["version"], "api_token": "tk_" + "c" * 40},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200
    assert rotated.json()["status"] == "pending"
    assert "tk_" not in rotated.text
    disabled = await env.client.patch(
        path,
        json={"version": rotated.json()["version"], "enabled": False},
        headers=env.headers(write=True),
    )
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["sync"]["status"] == "not_configured"
    enabled = await env.client.patch(
        path,
        json={"version": disabled.json()["version"], "enabled": True},
        headers=env.headers(write=True),
    )
    assert enabled.json()["status"] == "pending"
    audits = await run_sql(
        env.database_url,
        "SELECT string_agg(metadata_redacted_json::text, '\n') FROM workspace.audit_logs"
        " WHERE action = 'integration.updated'",
    )
    assert '"api_token"' in audits  # 只记字段名
    assert "tk_" not in audits


async def test_only_admins_manage_integrations(env: Env) -> None:
    integration = await env.connect()
    async with env.member("member@example.com", "member") as member:
        headers = {"X-Workspace-Id": env.workspace_id, "X-CSRF-Token": member.cookies["ws_csrf"]}
        listed = await member.get("/api/v1/integrations", headers=headers)
        assert listed.status_code == 200
        assert TOKEN not in listed.text
        attempts = [
            await member.post("/api/v1/integrations", json=integration_body(), headers=headers),
            await member.patch(
                f"/api/v1/integrations/{integration['id']}",
                json={"version": integration["version"], "name": "x"},
                headers=headers,
            ),
            await member.post(f"/api/v1/integrations/{integration['id']}/test", headers=headers),
        ]
        assert [a.status_code for a in attempts] == [403, 403, 403]


async def test_integrations_are_scoped_to_the_workspace(
    env: Env, make_client: ClientFactory
) -> None:
    integration = await env.connect()
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


async def test_missing_encryption_key_is_a_clear_503(
    make_client: ClientFactory,
) -> None:
    async with make_client(credential_encryption_key=None) as client:
        setup = (await client.post("/api/v1/setup", json=OWNER)).json()
        headers = {
            "X-Workspace-Id": setup["workspaces"][0]["id"],
            "X-CSRF-Token": client.cookies["ws_csrf"],
        }
        response = await client.post(
            "/api/v1/integrations",
            json=integration_body(base_url="https://tasks.example.com"),
            headers=headers,
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EXTERNAL_UNAVAILABLE"
