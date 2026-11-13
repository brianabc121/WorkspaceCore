"""旧专用公钥的定向退役（T-0179）：密码登录、只删本记录那一行；旧的吊销入口 410。

目标服务器沿用 test_agent_ssh_install.py 的进程内假服务器；authorized_keys 是真实文件。
"""

import json
import uuid
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import pytest

from app.domain.agent_ssh import agent_ssh_context
from app.domain.credentials import CredentialBox
from tests import test_agent_ssh_install as base
from tests.helpers import ClientFactory, add_member, add_user, csrf, run_sql
from tests.test_agent_server_password_jobs import Spy, _password_only, spy
from tests.test_agent_ssh_install import PASSWORD, SETTINGS, FakeHost, Owner, ssh_host

pytestmark = pytest.mark.integration

owner = base.owner
fast_online = base.fast_online
__all__ = ["spy"]


def _public(comment: str) -> tuple[asyncssh.SSHKey, str]:
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    return key, key.export_public_key().decode().strip()


async def _call(owner: Owner, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await owner.client.request(method, path, headers=owner.headers(), **kwargs)


async def _legacy_server(owner: Owner, host: FakeHost, port: int) -> tuple[str, str]:
    """一台“旧服务器”：添加并确认，记录里有旧方案的专用私钥与公钥（pending），服务器上也有那一行。

    返回 (服务器 ID, 旧公钥那一行)。
    """
    instance_id, _ = await owner.prepare(port, initialize=False)
    server_id = owner.servers[instance_id]
    key, public = _public("myworkspace-server-legacy1")
    target_id = await run_sql(
        owner.database_url,
        "SELECT id FROM workspace.agent_ssh_targets WHERE server_id = $1",
        uuid.UUID(server_id),
    )
    box = CredentialBox.from_settings(owner.app.state.settings)
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_ssh_targets SET client_key_ref = $1, client_public_key = $2,"
        " legacy_key_cleanup_state = 'pending' WHERE id = $3",
        box.seal(key.export_private_key().decode(), context=agent_ssh_context(target_id)),
        public,
        target_id,
    )
    return server_id, public


async def _server(owner: Owner, server_id: str) -> dict[str, Any]:
    response = await _call(owner, "GET", f"/api/v1/agents/servers/{server_id}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["server"]
    return data


async def _cleanup(owner: Owner, server_id: str) -> httpx.Response:
    server = await _server(owner, server_id)
    return await _call(
        owner,
        "POST",
        f"/api/v1/agents/servers/{server_id}/legacy-key-cleanup",
        json={"version": server["version"]},
    )


async def _row(owner: Owner, server_id: str) -> dict[str, Any]:
    text = await run_sql(
        owner.database_url,
        "SELECT row_to_json(t)::text FROM workspace.agent_ssh_targets t WHERE server_id = $1",
        uuid.UUID(server_id),
    )
    row: dict[str, Any] = json.loads(text)
    return row


async def _audit(owner: Owner, action: str) -> int:
    count: int = await run_sql(
        owner.database_url, "SELECT count(*) FROM workspace.audit_logs WHERE action = $1", action
    )
    return count


# ---------------------------------------------------------------- 成功：只删那一行


async def test_cleanup_removes_only_this_records_key(
    owner: Owner, tmp_path: Path, spy: Spy
) -> None:
    host = FakeHost(tmp_path)
    _, someone = _public("alice@laptop")
    _, runner = _public("myworkspace-runner-0a1b2c3d")
    async with ssh_host(host) as (port, host_key):
        server_id, legacy = await _legacy_server(owner, host, port)
        kind, data, comment = legacy.split()
        (host.home / ".ssh").mkdir(mode=0o700)
        # 同一把旧公钥被重新排版（带选项、制表符）也算那一行；别的用户、运行服务器的公钥保留
        (host.home / ".ssh" / "authorized_keys").write_text(
            f'{someone}\nfrom="10.0.0.0/8" {kind}\t{data} {comment}\n{runner}\n# 用户自己的注释\n'
        )
        response = await _cleanup(owner, server_id)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["warning"] is None
        ssh_info = detail["server"]["ssh"]
        assert ssh_info["legacy_key_cleanup_state"] == "done"
        assert ssh_info["client_key_installed"] is False
        assert ssh_info["client_key_fingerprint"] is None
        assert ssh_info["connection_status"] == "connected"
        assert host.authorized() == [someone, runner, "# 用户自己的注释"]

        # 已经退役：没有可退役的了
        again = await _cleanup(owner, server_id)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "AGENT_SERVER_NO_LEGACY_KEY"
        # 没有部署记录、旧公钥已退役：可以移除
        current = await _server(owner, server_id)
        removed = await _call(
            owner,
            "DELETE",
            f"/api/v1/agents/servers/{server_id}",
            params={"version": current["version"]},
        )
        assert removed.status_code == 204, removed.text

    _password_only(spy, host_key)
    assert host.logins == ["password"]
    assert await _audit(owner, "agents.server.legacy_key_cleaned") == 1
    fingerprint = await run_sql(
        owner.database_url,
        "SELECT metadata_redacted_json->>'client_key_fingerprint' FROM workspace.audit_logs"
        " WHERE action = 'agents.server.legacy_key_cleaned'",
    )
    assert fingerprint == asyncssh.import_public_key(legacy).get_fingerprint("sha256")
    assert await _audit(owner, "agents.server.removed") == 1


async def test_cleanup_succeeds_when_the_line_is_already_gone(owner: Owner, tmp_path: Path) -> None:
    """服务器上已经没有那一行（或没有 authorized_keys）：清理成功，不报错。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id, _ = await _legacy_server(owner, host, port)
        response = await _cleanup(owner, server_id)
    assert response.status_code == 200, response.text
    assert response.json()["server"]["ssh"]["legacy_key_cleanup_state"] == "done"
    row = await _row(owner, server_id)
    assert row["client_key_ref"] is None and row["client_public_key"] is None


# ---------------------------------------------------------------- 失败：保留引用，不标 done


async def test_unreachable_server_keeps_the_reference(
    owner: Owner, tmp_path: Path, spy: Spy
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id, legacy = await _legacy_server(owner, host, port)
    # 服务器关了
    response = await _cleanup(owner, server_id)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["server"]["ssh"]["legacy_key_cleanup_state"] == "manual_required"
    assert detail["server"]["ssh"]["client_key_installed"] is True
    assert detail["server"]["ssh"]["connection_status"] == "unreachable"
    assert legacy in detail["warning"]
    row = await _row(owner, server_id)
    assert row["client_key_ref"] is not None and row["client_public_key"] == legacy
    assert await _audit(owner, "agents.server.legacy_key_cleanup_failed") == 1
    assert await _audit(owner, "agents.server.legacy_key_cleaned") == 0
    # 没有拿旧私钥去试
    assert not [c for c in spy.contexts if c.startswith("agent_ssh:")]
    assert all(login["client_keys"] is None for login in spy.logins)


async def test_write_failure_and_wrong_password_keep_the_reference(
    owner: Owner, tmp_path: Path, spy: Spy
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, host_key):
        server_id, legacy = await _legacy_server(owner, host, port)
        (host.home / ".ssh").mkdir(mode=0o700)
        (host.home / ".ssh" / "authorized_keys").write_text(legacy + "\n")
        # 改不了 authorized_keys（假的 awk 失败）
        (host.bin / "awk").write_text("#!/bin/sh\nexit 2\n")
        (host.bin / "awk").chmod(0o755)
        response = await _cleanup(owner, server_id)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["server"]["ssh"]["legacy_key_cleanup_state"] == "manual_required"
        assert detail["server"]["ssh"]["connection_status"] == "connected"
        assert "authorized_keys" in detail["warning"] and legacy in detail["warning"]
        assert host.authorized() == [legacy]
        (host.bin / "awk").unlink()

        # 保存的密码不对：登录被拒，也不回退到旧私钥（服务器仍然信任那把旧公钥）
        current = await _server(owner, server_id)
        replaced = await _call(
            owner,
            "PUT",
            f"/api/v1/agents/servers/{server_id}/credentials",
            json={"version": current["version"], "login_password": "not the password"},
        )
        assert replaced.status_code == 200, replaced.text
        response = await _cleanup(owner, server_id)
        assert response.status_code == 200, response.text
        assert response.json()["server"]["ssh"]["connection_status"] == "auth_failed"
        assert response.json()["server"]["ssh"]["legacy_key_cleanup_state"] == "manual_required"
        assert host.authorized() == [legacy]
        assert "publickey" not in host.logins

        # 改回正确的密码再试一次（manual_required 也可以重试）：成功
        current = await _server(owner, server_id)
        fixed = await _call(
            owner,
            "PUT",
            f"/api/v1/agents/servers/{server_id}/credentials",
            json={"version": current["version"], "login_password": PASSWORD},
        )
        assert fixed.status_code == 200
        response = await _cleanup(owner, server_id)
        assert response.json()["server"]["ssh"]["legacy_key_cleanup_state"] == "done"
        assert host.authorized() == []
    _password_only(spy, host_key)
    row = await _row(owner, server_id)
    assert row["client_key_ref"] is None
    assert await _audit(owner, "agents.server.legacy_key_cleanup_failed") == 2


# ---------------------------------------------------------------- 前提、删除与旧入口


async def test_cleanup_preconditions_and_delete_rules(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id, legacy = await _legacy_server(owner, host, port)
        current = await _server(owner, server_id)
        stale = await _call(
            owner,
            "POST",
            f"/api/v1/agents/servers/{server_id}/legacy-key-cleanup",
            json={"version": current["version"] + 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
        # 还没退役（pending）：不能删除服务器，提示先完成迁移
        blocked = await _call(
            owner,
            "DELETE",
            f"/api/v1/agents/servers/{server_id}",
            params={"version": current["version"]},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "AGENT_SERVER_LEGACY_KEY_PENDING"

        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET login_password_ref = NULL WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        missing = await _cleanup(owner, server_id)
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "AGENT_SERVER_CREDENTIAL_REQUIRED"
        current = await _server(owner, server_id)
        supplied = await _call(
            owner,
            "PUT",
            f"/api/v1/agents/servers/{server_id}/credentials",
            json={"version": current["version"], "login_password": PASSWORD},
        )
        assert supplied.status_code == 200
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET host_key_confirmed_at = NULL"
            " WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        unconfirmed = await _cleanup(owner, server_id)
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["error"]["code"] == "AGENT_SSH_HOST_KEY_UNCONFIRMED"
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET host_key_confirmed_at = now()"
            " WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        # 同机有进行中的任务：等它结束
        target_id = await run_sql(
            owner.database_url,
            "SELECT id FROM workspace.agent_ssh_targets WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        job_id = uuid.uuid4()
        await run_sql(
            owner.database_url,
            "INSERT INTO workspace.agent_ssh_jobs (id, workspace_id, target_id, kind, status)"
            " VALUES ($1, $2, $3, 'prepare', 'running')",
            job_id,
            uuid.UUID(owner.workspace),
            target_id,
        )
        owner.app.state.ssh_jobs.reserve(job_id)
        try:
            busy = await _cleanup(owner, server_id)
        finally:
            owner.app.state.ssh_jobs.release(job_id)
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_jobs SET status = 'interrupted', finished_at = now()"
            " WHERE id = $1",
            job_id,
        )
        assert host.logins == []
    # 服务器连不上：退役记为 manual_required 之后可以移除（那一行由 owner 手动删除）
    failed = await _cleanup(owner, server_id)
    assert failed.status_code == 200
    assert failed.json()["server"]["ssh"]["legacy_key_cleanup_state"] == "manual_required"
    current = await _server(owner, server_id)
    removed = await _call(
        owner,
        "DELETE",
        f"/api/v1/agents/servers/{server_id}",
        params={"version": current["version"]},
    )
    assert removed.status_code == 204, removed.text
    left = await run_sql(
        owner.database_url,
        "SELECT metadata_redacted_json->>'legacy_key_left' FROM workspace.audit_logs"
        " WHERE action = 'agents.server.removed'",
    )
    assert left == asyncssh.import_public_key(legacy).get_fingerprint("sha256")


async def test_revoke_is_retired(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id, legacy = await _legacy_server(owner, host, port)
        before = await _row(owner, server_id)
        current = await _server(owner, server_id)
        for body in ({"version": current["version"]}, {"version": 1, "force": True}):
            response = await _call(
                owner, "POST", f"/api/v1/agents/servers/{server_id}/revoke", json=body
            )
            assert response.status_code == 410, response.text
            error = response.json()["error"]
            assert error["code"] == "AGENT_SSH_KEY_AUTH_RETIRED"
            assert error["details"]["replacement"] == "legacy-key-cleanup"
    assert host.logins == []
    assert await _row(owner, server_id) == before
    assert legacy == before["client_public_key"]


async def test_address_change_leaves_the_old_key_for_manual_removal(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id, legacy = await _legacy_server(owner, host, port)
    other = FakeHost(tmp_path / "other")
    async with ssh_host(other) as (new_port, _):
        current = await _server(owner, server_id)
        response = await _call(
            owner,
            "PUT",
            f"/api/v1/agents/servers/{server_id}/address",
            json={
                "host": "127.0.0.1",
                "port": new_port,
                "username": "deploy",
                "login_password": PASSWORD,
                "version": current["version"],
            },
        )
    assert response.status_code == 200, response.text
    detail = response.json()
    assert legacy in detail["warning"]
    assert detail["server"]["ssh"]["legacy_key_cleanup_state"] == "manual_required"
    assert detail["server"]["ssh"]["client_key_installed"] is False
    row = await _row(owner, server_id)
    assert row["client_key_ref"] is None and row["client_public_key"] is None
    assert other.logins == [] and host.logins == []


async def test_only_the_owner_can_clean_up(
    owner: Owner, tmp_path: Path, make_client: ClientFactory
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id, _ = await _legacy_server(owner, host, port)
        creds = {"email": "admin@example.com", "password": "admin password 123456"}
        user = await add_user(owner.database_url, creds["email"], creds["password"])
        await add_member(owner.database_url, uuid.UUID(owner.workspace), user, "admin")
        async with make_client(**SETTINGS) as other:
            assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
            response = await other.post(
                f"/api/v1/agents/servers/{server_id}/legacy-key-cleanup",
                json={"version": 1},
                headers={"X-Workspace-Id": owner.workspace, **csrf(other)},
            )
        assert response.status_code == 403
    assert host.logins == []
