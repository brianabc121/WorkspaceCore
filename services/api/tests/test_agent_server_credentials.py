"""OpenClaw 服务器的登录密码（T-0178）：加密保存、只写不读、连接检测、替换密码、并发与权限。

目标服务器沿用 test_agent_ssh_install.py 的进程内假服务器（asyncssh + 假的 sudo / docker），
它记录每一次登录尝试（password / password-denied / publickey / publickey-denied）。
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import asyncssh
import httpx
import pytest

from app.domain import agent_servers
from app.domain.agent_server_credentials import agent_server_password_context
from app.domain.credentials import CredentialBox
from app.integrations import ssh
from tests import test_agent_ssh_install as base
from tests.helpers import (
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    asyncpg_dsn,
    csrf,
    run_sql,
)
from tests.test_agent_ssh_install import PASSWORD, SETTINGS, FakeHost, Owner, ssh_host

pytestmark = pytest.mark.integration

owner = base.owner
SPACED = "  pass word with spaces  "


# ---------------------------------------------------------------- 工具


async def _call(owner: Owner, method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = {**owner.headers(), **kwargs.pop("headers", {})}
    return await owner.client.request(method, path, headers=headers, **kwargs)


async def _create(
    owner: Owner,
    port: int,
    *,
    password: str = PASSWORD,
    headers: dict[str, str] | None = None,
    **body: Any,
) -> httpx.Response:
    return await _call(
        owner,
        "POST",
        "/api/v1/agents/servers",
        json={
            "host": "127.0.0.1",
            "port": port,
            "username": "deploy",
            "login_password": password,
            **body,
        },
        headers=headers or {},
    )


async def _confirm(owner: Owner, server: dict[str, Any]) -> dict[str, Any]:
    response = await _call(
        owner,
        "POST",
        f"/api/v1/agents/servers/{server['id']}/confirm",
        json={"version": server["version"], "fingerprint": server["ssh"]["host_key_fingerprint"]},
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["server"]
    return data


async def _confirmed(owner: Owner, port: int, *, password: str = PASSWORD) -> dict[str, Any]:
    created = await _create(owner, port, password=password)
    assert created.status_code == 201, created.text
    return await _confirm(owner, created.json()["server"])


async def _server(owner: Owner, server_id: str) -> dict[str, Any]:
    response = await _call(owner, "GET", f"/api/v1/agents/servers/{server_id}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def _test(owner: Owner, server: dict[str, Any], **kwargs: Any) -> httpx.Response:
    return await _call(
        owner,
        "POST",
        f"/api/v1/agents/servers/{server['id']}/test-connection",
        json={"version": server["version"]},
        **kwargs,
    )


async def _credentials(
    owner: Owner, server: dict[str, Any], password: str, **kwargs: Any
) -> httpx.Response:
    return await _call(
        owner,
        "PUT",
        f"/api/v1/agents/servers/{server['id']}/credentials",
        json={"version": server["version"], "login_password": password},
        **kwargs,
    )


def _box(owner: Owner) -> CredentialBox:
    return CredentialBox.from_settings(owner.app.state.settings)


async def _target_row(owner: Owner, server_id: str) -> dict[str, Any]:
    conn = await asyncpg.connect(asyncpg_dsn(owner.database_url))
    try:
        row = await conn.fetchrow(
            "SELECT * FROM workspace.agent_ssh_targets WHERE server_id = $1",
            uuid.UUID(server_id),
        )
    finally:
        await conn.close()
    assert row is not None
    return dict(row)


async def _plaintext_anywhere(database_url: str, value: str) -> list[str]:
    """工作空间 schema 里所有表的所有行（转成 JSON 文本）中出现这个值的表。"""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        tables = [
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'workspace' AND table_type = 'BASE TABLE'"
            )
        ]
        found = []
        for table in tables:
            count = await conn.fetchval(
                f'SELECT count(*) FROM workspace."{table}" x'  # noqa: S608 - 表名来自系统目录
                " WHERE strpos(row_to_json(x)::text, $1) > 0",
                value,
            )
            if count:
                found.append(table)
        return found
    finally:
        await conn.close()


async def _audit(owner: Owner, action: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(asyncpg_dsn(owner.database_url))
    try:
        rows = await conn.fetch(
            "SELECT metadata_redacted_json::text AS m FROM workspace.audit_logs"
            " WHERE action = $1 ORDER BY created_at",
            action,
        )
    finally:
        await conn.close()
    return [{"text": row["m"]} for row in rows]


# ---------------------------------------------------------------- 加密保存、只写不读


async def test_create_stores_sealed_password_and_never_returns_it(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    host.password = SPACED
    async with ssh_host(host) as (port, _):
        response = await _create(owner, port, password=SPACED)
    assert response.status_code == 201, response.text
    assert SPACED.strip() not in response.text and "enc:v1" not in response.text
    server = response.json()["server"]
    # 名称省略：用 host:port
    assert server["name"] == f"127.0.0.1:{port}"
    info = server["ssh"]
    assert info["auth_method"] == "password" and info["password_set"] is True
    assert info["credential_revision"] == 1 and info["connection_status"] == "unknown"
    assert info["last_connection_at"] is None
    assert info["legacy_key_cleanup_state"] == "not_needed"
    assert info["client_key_installed"] is False
    assert server["initialization_status"] == "not_initialized"
    assert server["deployable"] is False
    assert server["deploy_block_reason"] == "host_key_unconfirmed"
    # 添加时只读主机公钥，不登录
    assert host.logins == []

    row = await _target_row(owner, server["id"])
    assert row["login_password_ref"].startswith("enc:v1:")
    assert row["credential_revision"] == 1
    secret = _box(owner).resolve(
        row["login_password_ref"], context=agent_server_password_context(row["id"])
    )
    assert secret.get_secret_value() == SPACED, "首尾空格原样保存"

    # 读接口也不返回明文或密文引用
    listed = await _call(owner, "GET", "/api/v1/agents/servers")
    detail = await _call(owner, "GET", f"/api/v1/agents/servers/{server['id']}")
    for text in (listed.text, detail.text):
        assert SPACED.strip() not in text and "enc:v1" not in text
        assert "login_password" not in text
    assert listed.json()["summary"]["needs_credentials_count"] == 0
    assert await _plaintext_anywhere(owner.database_url, SPACED.strip()) == []
    created = await _audit(owner, "agents.server.created")
    assert len(created) == 1 and '"login_saved": true' in created[0]["text"]


async def test_unconfirmed_host_never_receives_password(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        created = await _create(owner, port)
        server = created.json()["server"]
        # 指纹没确认：测试连接与初始化都拒绝，密码不会发给服务器
        refused = await _test(owner, server)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "AGENT_SSH_HOST_KEY_UNCONFIRMED"
        prepare = await _call(
            owner,
            "POST",
            f"/api/v1/agents/servers/{server['id']}/prepare",
            json={"version": server["version"]},
        )
        assert prepare.status_code == 409
        assert prepare.json()["error"]["code"] == "AGENT_SSH_HOST_KEY_UNCONFIRMED"
        assert host.logins == [] and host.commands == []

        confirmed = await _confirm(owner, server)
        tested = await _test(owner, confirmed)
        assert tested.status_code == 200, tested.text
    detail = tested.json()
    info = detail["server"]["ssh"]
    assert info["connection_status"] == "connected" and info["last_connection_at"]
    assert detail["warning"] is None
    # 一次密码登录，没有尝试公钥
    assert host.logins == ["password"]
    # 只读检查 + 用同一个密码验证 sudo（经标准输入）：不装 Docker、不建容器、不写公钥
    assert host.commands == ["sh -s", "sudo -S -p '' -k true"]
    assert host.calls() == ["sudo true"]
    assert host.authorized() == []
    assert not (host.state / "crontab").exists()
    assert all(PASSWORD not in command for command in host.commands)
    # 测试连接不改服务器的版本号（不是配置变化），也不算初始化
    assert detail["server"]["version"] == confirmed["version"]
    assert detail["server"]["initialization_status"] == "not_initialized"
    assert detail["server"]["deploy_block_reason"] == "not_initialized"
    tested_audit = await _audit(owner, "agents.server.connection_tested")
    assert len(tested_audit) == 1 and '"result": "connected"' in tested_audit[0]["text"]


async def test_connection_failures_are_reported_in_the_status(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server = await _confirmed(owner, port)
        # 密码错误：200，结果在连接状态里
        changed = await _credentials(owner, server, "wrong password 9")
        assert changed.status_code == 200, changed.text
        server = changed.json()["server"]
        tested = await _test(owner, server)
        assert tested.status_code == 200, tested.text
        out = tested.json()["server"]
        assert out["ssh"]["connection_status"] == "auth_failed"
        assert out["deployable"] is False and out["deploy_block_reason"] == "auth_failed"
        assert "password-denied" in host.logins
        assert not [login for login in host.logins if login.startswith("publickey")]

        # 换回正确的密码：先回到 unknown，测试后 connected
        fixed = await _credentials(owner, out, PASSWORD)
        assert fixed.json()["server"]["ssh"]["connection_status"] == "unknown"
        server = fixed.json()["server"]
        tested = await _test(owner, server)
        assert tested.json()["server"]["ssh"]["connection_status"] == "connected"
    logins = len(host.logins)

    # 同一个端口换了一把主机公钥（可能被冒充）：不登录、不发送密码
    async with ssh_host(host, port=port):
        tested = await _test(owner, server)
    assert tested.status_code == 200, tested.text
    out = tested.json()["server"]
    assert out["ssh"]["connection_status"] == "host_key_changed"
    assert out["deploy_block_reason"] == "host_key_changed"
    assert len(host.logins) == logins

    # 服务器关了
    tested = await _test(owner, server)
    assert tested.json()["server"]["ssh"]["connection_status"] == "unreachable"
    results = await _audit(owner, "agents.server.connection_tested")
    assert [r["text"].split('"result": "')[1].split('"')[0] for r in results] == [
        "auth_failed",
        "connected",
        "host_key_changed",
        "unreachable",
    ]


async def test_connection_check_reports_sudo_and_environment(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path, os_id="centos", os_version="9", crontab="no")
    (host.state / "sudo_password").write_text("a different sudo password")
    async with ssh_host(host) as (port, _):
        server = await _confirmed(owner, port)
        tested = await _test(owner, server)
    assert tested.status_code == 200, tested.text
    detail = tested.json()
    assert detail["server"]["ssh"]["connection_status"] == "connected"
    warning = detail["warning"] or ""
    assert "支持范围" in warning and "sudo" in warning and "crontab" in warning
    assert host.calls() == []  # sudo 校验失败：没有以 root 执行任何命令


async def test_password_only_never_falls_back_to_keys(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """运行环境里有默认私钥（而且服务器信任它）、有 SSH_AUTH_SOCK：密码错误时也不能改用公钥登录。"""
    home = tmp_path / "api-home"
    (home / ".ssh").mkdir(parents=True)
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.write_private_key(str(home / ".ssh" / "id_ed25519"))
    host = FakeHost(tmp_path / "host")
    (host.home / ".ssh").mkdir(mode=0o700)
    (host.home / ".ssh" / "authorized_keys").write_text(
        key.export_public_key().decode().strip() + "\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "no-agent.sock"))
    async with ssh_host(host) as (port, host_key):
        server = await _confirmed(owner, port, password="wrong password 7")  # noqa: S106
        tested = await _test(owner, server)
        pinned = " ".join(host_key.export_public_key().decode().split()[:2])
        with pytest.raises(ssh.SshError) as caught:
            await ssh.connect_password_only("127.0.0.1", port, "deploy", pinned, "wrong pw")
    assert caught.value.reason == "auth_failed"
    assert tested.json()["server"]["ssh"]["connection_status"] == "auth_failed"
    assert host.logins and set(host.logins) == {"password-denied"}


# ---------------------------------------------------------------- 替换密码、更换地址


async def test_set_credentials_keeps_initialization_and_checks_version_and_busy(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server = await _confirmed(owner, port)
        tested = await _test(owner, server)
        assert tested.json()["server"]["ssh"]["connection_status"] == "connected"
    # 模拟已经初始化、部署了一个实例
    instance_id = await owner.instance()
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_ssh_targets SET prepared_at = now() WHERE server_id = $1",
        uuid.UUID(server["id"]),
    )
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_deployments"
        " (workspace_id, instance_id, server_id, install_dir, installed_at)"
        " VALUES ($1, $2, $3, '/home/deploy/myworkspace-openclaw/x', now())",
        uuid.UUID(owner.workspace),
        uuid.UUID(instance_id),
        uuid.UUID(server["id"]),
    )
    before = await _target_row(owner, server["id"])
    current = (await _server(owner, server["id"]))["server"]

    # 版本过期
    stale = await _credentials(owner, {**current, "version": current["version"] - 1}, "new pw 1")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    # 同一台服务器上有进行中的任务：拒绝，原密码不变
    job_id = uuid.uuid4()
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_ssh_jobs"
        " (id, workspace_id, instance_id, target_id, kind, status)"
        " VALUES ($1, $2, $3, $4, 'install', 'running')",
        job_id,
        uuid.UUID(owner.workspace),
        uuid.UUID(instance_id),
        before["id"],
    )
    runner = owner.app.state.ssh_jobs
    runner.reserve(job_id)
    busy = await _credentials(owner, current, "new pw 2")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
    assert (await _target_row(owner, server["id"]))["login_password_ref"] == before[
        "login_password_ref"
    ]
    listed = (await _call(owner, "GET", "/api/v1/agents/servers")).json()["items"][0]
    assert listed["deploy_block_reason"] == "busy"
    runner.release(job_id)
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_ssh_jobs SET status = 'succeeded', finished_at = now()"
        " WHERE id = $1",
        job_id,
    )

    replaced = await _credentials(owner, current, SPACED)
    assert replaced.status_code == 200, replaced.text
    out = replaced.json()["server"]
    assert out["version"] == current["version"] + 1
    assert out["ssh"]["credential_revision"] == 2
    assert out["ssh"]["connection_status"] == "unknown"
    assert out["ssh"]["last_connection_at"] is None
    assert out["ssh"]["prepared_at"] == current["ssh"]["prepared_at"]
    assert out["initialization_status"] == "ready" and out["managed_deployments"] == 1
    after = await _target_row(owner, server["id"])
    assert after["login_password_ref"] != before["login_password_ref"]
    assert (
        _box(owner)
        .resolve(after["login_password_ref"], context=agent_server_password_context(after["id"]))
        .get_secret_value()
        == SPACED
    )
    deployments = await run_sql(
        owner.database_url, "SELECT count(*) FROM workspace.agent_deployments"
    )
    assert deployments == 1
    shown = (await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}")).json()
    assert shown["status"] == "pending"
    audit = await _audit(owner, "agents.server.credentials_set")
    assert len(audit) == 1 and '"auth_revision": 2' in audit[0]["text"]
    assert SPACED.strip() not in audit[0]["text"]


async def test_address_requires_password_and_refuses_with_deployments(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server = await _confirmed(owner, port)
    path = f"/api/v1/agents/servers/{server['id']}/address"
    missing = await _call(
        owner,
        "PUT",
        path,
        json={"host": "127.0.0.1", "port": port, "username": "deploy", "version": 2},
    )
    assert missing.status_code == 422
    instance_id = await owner.instance()
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_deployments (workspace_id, instance_id, server_id)"
        " VALUES ($1, $2, $3)",
        uuid.UUID(owner.workspace),
        uuid.UUID(instance_id),
        uuid.UUID(server["id"]),
    )
    body = {
        "host": "127.0.0.1",
        "port": port + 1,
        "username": "deploy",
        "login_password": "new address pw",
        "version": server["version"],
    }
    blocked = await _call(owner, "PUT", path, json=body)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "AGENT_SERVER_HAS_DEPLOYMENTS"
    await run_sql(owner.database_url, "DELETE FROM workspace.agent_deployments")

    other = FakeHost(tmp_path / "other")
    async with ssh_host(other) as (port2, key2):
        moved = await _call(owner, "PUT", path, json={**body, "port": port2})
    assert moved.status_code == 200, moved.text
    info = moved.json()["server"]["ssh"]
    assert info["port"] == port2 and info["host_key_confirmed"] is False
    assert info["host_key_fingerprint"] == key2.get_fingerprint("sha256")
    assert info["credential_revision"] == 2 and info["connection_status"] == "unknown"
    assert info["prepared_at"] is None
    row = await _target_row(owner, server["id"])
    assert (
        _box(owner)
        .resolve(row["login_password_ref"], context=agent_server_password_context(row["id"]))
        .get_secret_value()
        == "new address pw"
    )
    assert other.logins == []


# ---------------------------------------------------------------- 校验、幂等、日志与事件不含密码


async def test_validation_and_idempotency_records_redact_secret(
    owner: Owner, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = "Idem-secret-" + secrets.token_hex(8)
    # 422：不回显输入（密码过长、为空、多余的旧字段）
    for body in (
        {"login_password": secret + "x" * 1024},
        {"login_password": ""},
        {"login_password": secret, "password": secret},
    ):
        payload = {"host": "127.0.0.1", "port": 22, "username": "deploy", **body}
        response = await _call(owner, "POST", "/api/v1/agents/servers", json=payload)
        assert response.status_code == 422, response.text
        assert secret not in response.text
    missing = await _call(
        owner,
        "POST",
        "/api/v1/agents/servers",
        json={"host": "127.0.0.1", "port": 22, "username": "deploy"},
    )
    assert missing.status_code == 422

    host = FakeHost(tmp_path)
    key = str(uuid.uuid4())
    async with ssh_host(host) as (port, _):
        first = await _create(owner, port, password=secret, headers={"Idempotency-Key": key})
        assert first.status_code == 201, first.text
        again = await _create(owner, port, password=secret, headers={"Idempotency-Key": key})
        assert again.status_code == 201, again.text
        assert again.headers.get("Idempotent-Replayed") == "true"
        assert again.json()["server"]["id"] == first.json()["server"]["id"]
        # 同一个键、不同的密码：冲突（指纹里是密码的 HMAC，不是明文）
        reused = await _create(owner, port, password=secret + "!", headers={"Idempotency-Key": key})
        assert reused.status_code == 409
        assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        servers = await run_sql(owner.database_url, "SELECT count(*) FROM workspace.agent_servers")
        assert servers == 1

        server = first.json()["server"]
        update_key = str(uuid.uuid4())
        changed = await _credentials(
            owner, server, secret + "2", headers={"Idempotency-Key": update_key}
        )
        assert changed.status_code == 200, changed.text
        replay = await _credentials(
            owner, server, secret + "2", headers={"Idempotency-Key": update_key}
        )
        assert replay.status_code == 200 and replay.headers.get("Idempotent-Replayed") == "true"
        assert replay.json()["server"]["ssh"]["credential_revision"] == 2
    assert host.logins == []

    conn = await asyncpg.connect(asyncpg_dsn(owner.database_url))
    try:
        records = await conn.fetch(
            "SELECT row_to_json(r)::text AS t, context_json::text AS c"
            " FROM workspace.idempotency_requests r ORDER BY created_at"
        )
    finally:
        await conn.close()
    assert len(records) == 2
    for record in records:
        assert secret not in record["t"] and "enc:v1" not in record["t"]
        assert server["id"] in record["c"]
    for value in (secret, secret + "2"):
        assert await _plaintext_anywhere(owner.database_url, value) == []
        assert value not in caplog.text
    # SSE 的事件流（Redis Stream）里也没有
    stream = await owner.app.state.redis.xrange(f"events:ws:{owner.workspace}")
    assert secret not in repr(stream)


# ---------------------------------------------------------------- 权限


async def test_wrong_workspace_and_non_owner_cannot_write(
    owner: Owner, make_client: ClientFactory, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server = await _confirmed(owner, port)
    admin = {"email": "admin@example.com", "password": "admin password 123456"}
    stranger = {"email": "stranger@example.com", "password": "stranger password 123"}
    admin_id = await add_user(owner.database_url, admin["email"], admin["password"])
    await add_member(owner.database_url, uuid.UUID(owner.workspace), admin_id, "admin")
    stranger_id = await add_user(owner.database_url, stranger["email"], stranger["password"])
    other_workspace = await add_workspace(owner.database_url, stranger_id, "别人的空间")
    writes = [
        ("PUT", f"/api/v1/agents/servers/{server['id']}/credentials", {"login_password": "x1"}),
        ("POST", f"/api/v1/agents/servers/{server['id']}/test-connection", {}),
    ]
    async with make_client(**SETTINGS) as as_admin, make_client(**SETTINGS) as as_stranger:
        assert (await as_admin.post("/api/v1/auth/login", json=admin)).status_code == 200
        assert (await as_stranger.post("/api/v1/auth/login", json=stranger)).status_code == 200
        for method, path, body in writes:
            response = await as_admin.request(
                method,
                path,
                json={"version": server["version"], **body},
                headers={"X-Workspace-Id": owner.workspace, **csrf(as_admin)},
            )
            assert response.status_code == 403, (path, response.text)
            # 别的工作空间的 owner：带自己的空间 404（服务器不在那里），带这个空间 404（不是成员）
            for workspace in (str(other_workspace), owner.workspace):
                response = await as_stranger.request(
                    method,
                    path,
                    json={"version": server["version"], **body},
                    headers={"X-Workspace-Id": workspace, **csrf(as_stranger)},
                )
                assert response.status_code == 404, (path, workspace, response.text)
    row = await _target_row(owner, server["id"])
    assert row["credential_revision"] == 1 and row["last_connection_at"] is None
    assert host.logins == []


# ---------------------------------------------------------------- 服务器的统计与部署状态


async def test_server_counts_states_and_summary(
    owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys: dict[int, asyncssh.SSHKey] = {}

    async def probe(address: str, port: int) -> ssh.HostKey:
        key = keys.setdefault(port, asyncssh.generate_private_key("ssh-ed25519"))
        return ssh.HostKey(
            algorithm="ssh-ed25519",
            public_key=" ".join(key.export_public_key().decode().split()[:2]),
            fingerprint=key.get_fingerprint("sha256"),
        )

    monkeypatch.setattr(ssh, "probe_host_key", probe)
    url = owner.database_url
    workspace = uuid.UUID(owner.workspace)
    ready = await _confirmed(owner, 2201)
    legacy = await _confirmed(owner, 2202)
    fresh = await _confirmed(owner, 2203)
    auto = uuid.uuid4()
    await run_sql(
        url,
        "INSERT INTO workspace.agent_servers (id, workspace_id, name, kind)"
        " VALUES ($1, $2, '自动识别', 'auto')",
        auto,
        workspace,
    )
    await run_sql(
        url,
        "UPDATE workspace.agent_ssh_targets SET prepared_at = now() WHERE server_id = ANY($1)",
        [uuid.UUID(ready["id"]), uuid.UUID(legacy["id"])],
    )
    # 旧服务器：没有密码，留着旧专用公钥
    await run_sql(
        url,
        "UPDATE workspace.agent_ssh_targets SET login_password_ref = NULL,"
        " client_key_ref = 'enc:v1:00000000:legacy', legacy_key_cleanup_state = 'pending'"
        " WHERE server_id = $1",
        uuid.UUID(legacy["id"]),
    )
    ready_target = (await _target_row(owner, ready["id"]))["id"]

    async def deploy(
        server_id: str,
        *,
        installed: bool,
        runtime: bool | None,
        mem: int | None,
        cpus: float | None,
        job: str | None = None,
    ) -> str:
        instance_id = await owner.instance()
        await run_sql(
            url,
            "UPDATE workspace.agent_instances SET server_id = $1, server_manual = true,"
            " mem_limit_mb = $2, cpus = $3 WHERE id = $4",
            uuid.UUID(server_id),
            mem,
            cpus,
            uuid.UUID(instance_id),
        )
        await run_sql(
            url,
            "INSERT INTO workspace.agent_deployments"
            " (workspace_id, instance_id, server_id, install_dir, installed_at, runtime_installed)"
            " VALUES ($1, $2, $3, '/srv/x', CASE WHEN $4 THEN now() END, $5)",
            workspace,
            uuid.UUID(instance_id),
            uuid.UUID(server_id),
            installed,
            runtime,
        )
        if job is not None:
            await run_sql(
                url,
                "INSERT INTO workspace.agent_ssh_jobs"
                " (workspace_id, instance_id, target_id, kind, status, finished_at)"
                " VALUES ($1, $2, $3, 'install', $4, now())",
                workspace,
                uuid.UUID(instance_id),
                ready_target,
                job,
            )
        return instance_id

    deployed = await deploy(ready["id"], installed=True, runtime=True, mem=1024, cpus=1.5)
    retained = await deploy(ready["id"], installed=True, runtime=False, mem=512, cpus=0.5)
    failed = await deploy(
        ready["id"], installed=False, runtime=None, mem=2048, cpus=2.0, job="failed"
    )
    await deploy(legacy["id"], installed=True, runtime=None, mem=None, cpus=None)
    associated = await owner.instance()
    await run_sql(
        url,
        "UPDATE workspace.agent_instances SET server_id = $1, server_manual = true WHERE id = $2",
        uuid.UUID(ready["id"]),
        uuid.UUID(associated),
    )

    listed = (await _call(owner, "GET", "/api/v1/agents/servers")).json()
    items = {item["id"]: item for item in listed["items"]}
    one = items[ready["id"]]
    assert one["initialization_status"] == "ready" and one["deployable"] is True
    assert one["deploy_block_reason"] is None
    assert one["managed_deployments"] == 3 and one["successful_deployments"] == 1
    assert one["deployment_counts_partial"] is False
    assert one["allocated_mem_limit_mb"] == 3584 and one["allocated_cpus"] == 4.0
    assert one["has_unlimited_limits"] is False and one["instances_total"] == 4
    old = items[legacy["id"]]
    assert old["ssh"]["password_set"] is False
    assert old["ssh"]["connection_status"] == "credentials_required"
    assert old["ssh"]["legacy_key_cleanup_state"] == "pending"
    assert old["initialization_status"] == "ready"
    assert old["deploy_block_reason"] == "credentials_required"
    assert old["successful_deployments"] is None and old["deployment_counts_partial"] is True
    assert old["allocated_mem_limit_mb"] is None and old["allocated_cpus"] is None
    assert old["has_unlimited_limits"] is True
    new = items[fresh["id"]]
    assert new["initialization_status"] == "not_initialized"
    assert new["deploy_block_reason"] == "not_initialized" and new["managed_deployments"] == 0
    automatic = items[str(auto)]
    assert automatic["initialization_status"] is None
    assert automatic["deploy_block_reason"] == "not_ssh"
    summary = listed["summary"]
    assert summary["server_count"] == 4 and summary["ready_count"] == 2
    assert summary["not_initialized_count"] == 1 and summary["needs_credentials_count"] == 1
    assert summary["successful_deployments"] is None
    assert summary["deployment_counts_partial"] is True and summary["generated_at"]

    rows = {r["instance_id"]: r for r in (await _server(owner, ready["id"]))["agents"]}
    assert rows[deployed]["deployment_state"] == "deployed"
    assert rows[deployed]["deployment_project"] == (
        "myworkspace-openclaw-" + deployed.replace("-", "")[:12]
    )
    assert rows[retained]["deployment_state"] == "retained"
    assert rows[failed]["deployment_state"] == "failed"
    assert rows[failed]["latest_job"]["status"] == "failed"
    assert rows[associated]["deployment_state"] == "associated"
    assert rows[associated]["deployment_project"] is None
    assert rows[associated]["latest_job"] is None
    # 只有旧部署（运行状态未知）的服务器以外，成功数不会把未知当成 0
    deployment = (
        await _call(owner, "GET", f"/api/v1/agents/instances/{retained}/deployment")
    ).json()["deployment"]
    assert deployment["runtime_installed"] is False


T0 = datetime(2026, 11, 9, tzinfo=UTC)


def _job(status: str, started: int, kind: str = "prepare") -> SimpleNamespace:
    return SimpleNamespace(status=status, kind=kind, started_at=T0 + timedelta(minutes=started))


def test_initialization_and_agent_state_rules() -> None:
    t0 = T0
    target = SimpleNamespace(prepared_at=None)
    rule = agent_servers._initialization
    assert rule(target, None) == "not_initialized"  # type: ignore[arg-type]
    assert rule(target, _job("running", 1)) == "initializing"  # type: ignore[arg-type]
    assert rule(target, _job("failed", 1)) == "failed"  # type: ignore[arg-type]
    assert rule(target, _job("interrupted", 1)) == "interrupted"  # type: ignore[arg-type]
    target.prepared_at = t0 + timedelta(minutes=5)
    # 成功之后又失败：以最近的失败为准；失败在成功之前：仍是就绪
    assert rule(target, _job("failed", 10)) == "failed"  # type: ignore[arg-type]
    assert rule(target, _job("failed", 1)) == "ready"  # type: ignore[arg-type]
    assert rule(target, _job("succeeded", 1)) == "ready"  # type: ignore[arg-type]

    state = agent_servers._agent_state
    assert state(None, None) == "associated"
    ok = SimpleNamespace(installed_at=t0, runtime_installed=True)
    unknown = SimpleNamespace(installed_at=t0, runtime_installed=None)
    kept = SimpleNamespace(installed_at=None, runtime_installed=False)
    never = SimpleNamespace(installed_at=None, runtime_installed=None)
    assert state(ok, _job("succeeded", 1, "install")) == "deployed"  # type: ignore[arg-type]
    assert state(unknown, None) == "deployed"  # type: ignore[arg-type]
    assert state(kept, _job("succeeded", 1, "uninstall")) == "retained"  # type: ignore[arg-type]
    # 旧的成功部署重新部署失败：标明最近的任务失败
    assert state(ok, _job("failed", 2, "install")) == "failed"  # type: ignore[arg-type]
    assert state(ok, _job("running", 2, "install")) == "deploying"  # type: ignore[arg-type]
    assert state(never, _job("interrupted", 2, "install")) == "interrupted"  # type: ignore[arg-type]


# ---------------------------------------------------------------- 契约已定、实现在后续任务的接口


async def test_contract_stubs_are_declared_but_not_implemented(owner: Owner) -> None:
    request_id = str(uuid.uuid4())
    body = {
        "request_id": request_id,
        "server_id": str(uuid.uuid4()),
        "server_version": 1,
        "name": "甲",
    }
    missing_key = await _call(owner, "POST", "/api/v1/agents/deployments", json=body)
    assert missing_key.status_code == 422
    mismatch = await _call(
        owner,
        "POST",
        "/api/v1/agents/deployments",
        json=body,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert mismatch.status_code == 422, mismatch.text
    for method, path, kwargs in (
        (
            "POST",
            "/api/v1/agents/deployments",
            {"json": body, "headers": {"Idempotency-Key": request_id}},
        ),
        ("GET", f"/api/v1/agents/deployments/requests/{request_id}", {}),
        ("GET", "/api/v1/agents/overview", {"params": {"q": "甲", "status": "online"}}),
    ):
        response = await _call(owner, method, path, **kwargs)
        assert response.status_code == 501, (path, response.text)
        assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
    # 不接受地址、密码、镜像这类字段
    extra = await _call(
        owner,
        "POST",
        "/api/v1/agents/deployments",
        json={**body, "login_password": "x"},
        headers={"Idempotency-Key": request_id},
    )
    assert extra.status_code == 422
