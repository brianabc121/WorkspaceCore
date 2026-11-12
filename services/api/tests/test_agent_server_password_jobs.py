"""OpenClaw 服务器的 SSH 动作全部用保存的密码登录（T-0179）。

初始化、部署、卸载、实例级与服务器级运维都经 `agent_server_ssh.connect_agent_server`：只用密码、
固定主机公钥；不生成、不安装、不读取 SSH 客户端密钥——即使旧方案留下的专用私钥还在（而且服务器
仍然信任它）也不用。目标服务器沿用 test_agent_ssh_install.py 的进程内假服务器。
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import pytest

from app import agent_provisioning as prov
from app.domain import agent_ssh_install
from app.domain.agent_ssh import agent_ssh_context
from app.domain.agent_ssh_install import AgentServerInstaller, InstallFailed, JobRecorder, Target
from app.domain.credentials import CredentialBox
from app.domain.runner_ssh import RunnerInstaller
from tests import test_agent_ssh_install as base
from tests.helpers import OWNER, run_sql
from tests.test_agent_server_credentials import _plaintext_anywhere
from tests.test_agent_ssh_install import (
    PASSWORD,
    FakeHost,
    Owner,
    _install_ok,
    _steps,
    ssh_host,
)

pytestmark = pytest.mark.integration

owner = base.owner
fast_online = base.fast_online


class Spy:
    """记录所有 SSH 登录的参数、所有解密的用途，以及有没有人去装公钥。"""

    def __init__(self) -> None:
        self.logins: list[dict[str, Any]] = []
        self.contexts: list[str] = []
        self.authorize: list[str] = []


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[Spy]:
    found = Spy()
    real_connect = asyncssh.connect
    real_resolve = CredentialBox.resolve
    real_authorize = prov.authorize_key_command

    async def connect(*args: Any, **kwargs: Any) -> asyncssh.SSHClientConnection:
        found.logins.append(
            {
                "client_keys": kwargs.get("client_keys", "<default>"),
                "public_key_auth": kwargs.get("public_key_auth", "<default>"),
                "agent_path": kwargs.get("agent_path", "<default>"),
                "password": kwargs.get("password") is not None,
                "preferred_auth": kwargs.get("preferred_auth"),
                "known_hosts": kwargs.get("known_hosts"),
                "config": kwargs.get("config"),
            }
        )
        return await real_connect(*args, **kwargs)

    def resolve(self: CredentialBox, ref: str, *, context: str) -> Any:
        found.contexts.append(context)
        return real_resolve(self, ref, context=context)

    def authorize(public_key: str) -> str:
        found.authorize.append(public_key)
        return real_authorize(public_key)

    monkeypatch.setattr(asyncssh, "connect", connect)
    monkeypatch.setattr(CredentialBox, "resolve", resolve)
    monkeypatch.setattr(prov, "authorize_key_command", authorize)
    yield found


def _password_only(spy: Spy, host_key: asyncssh.SSHKey) -> None:
    assert spy.logins, "应该登录过"
    for login in spy.logins:
        assert login["client_keys"] is None, login
        assert login["public_key_auth"] is False, login
        assert login["agent_path"] is None, login
        assert login["password"] is True, login
        assert login["preferred_auth"] == "keyboard-interactive,password", login
        assert login["config"] == [], login
        known_hosts = login["known_hosts"]
        assert [key.public_data for key in known_hosts[0]] == [host_key.public_data], login
        assert known_hosts[1:] == ([], []), login
    assert spy.authorize == []
    assert not [c for c in spy.contexts if c.startswith("agent_ssh:")], spy.contexts


async def _plant_legacy_key(owner: Owner, host: FakeHost, server_id: str) -> str:
    """旧方案留下的专用密钥：私钥加密存进记录，公钥写进服务器的 authorized_keys（仍然有效）。"""
    key = asyncssh.generate_private_key("ssh-ed25519", comment="myworkspace-server-legacy1")
    public = key.export_public_key().decode().strip()
    target_id = await run_sql(
        owner.database_url,
        "SELECT id FROM workspace.agent_ssh_targets WHERE server_id = $1",
        uuid.UUID(server_id),
    )
    box = CredentialBox.from_settings(owner.app.state.settings)
    sealed = box.seal(key.export_private_key().decode(), context=agent_ssh_context(target_id))
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_ssh_targets SET client_key_ref = $1, client_public_key = $2,"
        " legacy_key_cleanup_state = 'pending' WHERE id = $3",
        sealed,
        public,
        target_id,
    )
    (host.home / ".ssh").mkdir(mode=0o700, exist_ok=True)
    (host.home / ".ssh" / "authorized_keys").write_text(public + "\n")
    return public


async def _call(owner: Owner, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await owner.client.request(method, path, headers=owner.headers(), **kwargs)


async def _ops(
    owner: Owner, path: str, body: dict[str, Any], *, expect: str = "succeeded"
) -> dict[str, Any]:
    started = await _call(owner, "POST", path, json=body)
    assert started.status_code == 202, started.text
    action_id = started.json()["id"]
    for _ in range(400):
        current = await _call(owner, "GET", f"{path}/{action_id}")
        data: dict[str, Any] = current.json()
        if data["status"] != "running":
            assert data["status"] == expect, data
            return data
        await asyncio.sleep(0.05)
    raise AssertionError("运维动作 20 秒内没有结束")


async def _undeploy(owner: Owner, instance_id: str, *, remove_files: bool) -> dict[str, Any]:
    response = await _call(
        owner,
        "POST",
        f"/api/v1/agents/instances/{instance_id}/undeploy",
        json={"version": await owner.instance_version(instance_id), "remove_files": remove_files},
    )
    assert response.status_code == 202, response.text
    state = await owner.finished(instance_id)
    assert state["job"]["status"] == "succeeded", state["job"]
    return state


# ---------------------------------------------------------------- 所有 SSH 动作都只用密码


async def test_every_openclaw_ssh_action_logs_in_with_the_saved_password(
    owner: Owner, tmp_path: Path, spy: Spy
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, host_key):
        instance_id, version = await owner.prepare(port, initialize=False)
        server_id = owner.servers[instance_id]
        # 旧方案的专用私钥还在、服务器也还信任它：新的任务仍然只用密码
        legacy = await _plant_legacy_key(owner, host, server_id)

        prepare = await owner.initialize(server_id)
        assert prepare["status"] == "succeeded", prepare
        assert "authorize_key" not in [s["key"] for s in prepare["steps"]]
        state = await _install_ok(owner, host, instance_id, version)
        assert "authorize_key" not in _steps(state)

        instance_ops = f"/api/v1/agents/instances/{instance_id}/ops"
        for body in (
            {"action": "status"},
            {"action": "logs"},
            {"action": "logs", "service": "tunnel"},
            {"action": "report"},
            {"action": "restart", "password": OWNER["password"]},
            {"action": "up", "password": OWNER["password"]},
        ):
            await _ops(owner, instance_ops, body)
        server_ops = f"/api/v1/agents/servers/{server_id}/ops"
        await _ops(owner, server_ops, {"action": "containers"})
        await _ops(owner, server_ops, {"action": "prune", "password": OWNER["password"]})

        await _undeploy(owner, instance_id, remove_files=False)
        await _undeploy(owner, instance_id, remove_files=True)

    _password_only(spy, host_key)
    # 初始化 2 次 + 独立只读资源探测 1 + 部署 1 + 运维 8 + 卸载 2。
    assert len(spy.logins) == 14
    assert set(host.logins) == {"password"} and len(host.logins) == 14
    assert len(host.resource_probe_sessions()) == 1
    # 服务器上的 authorized_keys 原样：没有新装公钥，旧公钥也只等显式退役
    assert host.authorized() == [legacy]
    row = await run_sql(
        owner.database_url,
        "SELECT client_key_ref IS NOT NULL FROM workspace.agent_ssh_targets WHERE server_id = $1",
        uuid.UUID(server_id),
    )
    assert row is True
    # 密码与它的密文不在任务、日志、审计、运维记录里（密文只在服务器的 SSH 记录里）
    assert await _plaintext_anywhere(owner.database_url, PASSWORD) == []
    sealed = await run_sql(
        owner.database_url,
        "SELECT login_password_ref FROM workspace.agent_ssh_targets WHERE server_id = $1",
        uuid.UUID(server_id),
    )
    assert await _plaintext_anywhere(owner.database_url, sealed) == ["agent_ssh_targets"]
    audits = await run_sql(
        owner.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action LIKE 'agents.%'"
        " AND metadata_redacted_json->>'auth' = 'saved_password'",
    )
    # 初始化、部署、两次卸载、8 个运维动作开始时都记下了登录方式（不含密码）
    assert audits == 12


async def test_missing_password_blocks_ssh_actions_but_not_the_running_agent(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await _install_ok(owner, host, instance_id, version)
        server_id = owner.servers[instance_id]
        logins = len(host.logins)
        # 旧服务器的样子：没有保存登录密码
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET login_password_ref = NULL WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        server = (await _call(owner, "GET", f"/api/v1/agents/servers/{server_id}")).json()["server"]
        refused = [
            await _call(
                owner,
                "POST",
                f"/api/v1/agents/servers/{server_id}/prepare",
                json={"version": server["version"]},
            ),
            await owner.install(instance_id, version),
            await _call(
                owner,
                "POST",
                f"/api/v1/agents/instances/{instance_id}/undeploy",
                json={"version": await owner.instance_version(instance_id)},
            ),
        ]
        for response in refused:
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "AGENT_SERVER_CREDENTIAL_REQUIRED"
        state = (await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}/ops")).json()
        assert state["available"] is False and state["reason"] == "credential_missing"
        ops = await _call(
            owner, "POST", f"/api/v1/agents/instances/{instance_id}/ops", json={"action": "status"}
        )
        assert ops.status_code == 409 and ops.json()["error"]["details"]["reason"] == (
            "credential_missing"
        )
        server_state = (await _call(owner, "GET", f"/api/v1/agents/servers/{server_id}/ops")).json()
        assert server_state["reason"] == "credential_missing"

        # 已经运行的实例：其他接口照常（不连 SSH）
        shown = await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}")
        assert shown.status_code == 200 and shown.json()["status"] == "pending"
        renamed = await _call(
            owner,
            "PATCH",
            f"/api/v1/agents/instances/{instance_id}",
            json={"version": shown.json()["version"], "name": "改个名字"},
        )
        assert renamed.status_code == 200, renamed.text
        deployment = await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}/deployment")
        assert deployment.json()["deployment"]["runtime_installed"] is True
        assert host.directory.exists()
        assert len(host.logins) == logins

        # 密文所用的密钥不在当前配置里：credential_unreadable
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET login_password_ref = 'enc:v1:ffffffff:x'"
            " WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        state = (await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}/ops")).json()
        assert state["reason"] == "credential_unreadable"
        refused_again = await owner.install(instance_id, version)
        assert refused_again.json()["error"]["code"] == "AGENT_SERVER_CREDENTIAL_UNREADABLE"
        # 主机公钥没确认：host_key_unconfirmed（不再用旧的 key_missing）
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET host_key_confirmed_at = NULL"
            " WHERE server_id = $1",
            uuid.UUID(server_id),
        )
        state = (await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}/ops")).json()
        assert state["reason"] == "host_key_unconfirmed"
    assert len(host.logins) == logins


async def test_ops_step_up_uses_the_workspace_password_not_the_ssh_password(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await _install_ok(owner, host, instance_id, version)
        path = f"/api/v1/agents/instances/{instance_id}/ops"
        wrong = await _call(owner, "POST", path, json={"action": "restart", "password": PASSWORD})
        assert wrong.status_code == 403
        assert wrong.json()["error"]["code"] == "STEP_UP_FAILED"
        done = await _ops(owner, path, {"action": "restart", "password": OWNER["password"]})
        assert "docker compose restart openclaw" in done["output"]


# ---------------------------------------------------------------- 初始化：各步骤失败的位置


class _PasswordChangesAfterDocker(FakeHost):
    """装完 Docker（加入 docker 组）之后，服务器上的登录密码变了：重新登录失败。"""

    @property
    def password(self) -> str:
        return "changed later" if (self.state / "docker_ok").exists() else PASSWORD

    @password.setter
    def password(self, value: str) -> None:
        pass


async def _initialize(owner: Owner, host: FakeHost, port: int) -> tuple[dict[str, Any], str]:
    instance_id, _ = await owner.prepare(port, initialize=False)
    server_id = owner.servers[instance_id]
    return await owner.initialize(server_id), server_id


@pytest.mark.parametrize(
    ("make_host", "step", "code"),
    [
        (lambda root: FakeHost(root, os_id="centos", os_version="9"), "docker", "unsupported_os"),
        (lambda root: FakeHost(root, sudo="no"), "docker", "sudo_unavailable"),
        (lambda root: _PasswordChangesAfterDocker(root), "reconnect", "auth_failed"),
    ],
)
async def test_initialization_fails_at_the_right_step(
    owner: Owner, tmp_path: Path, make_host: Any, step: str, code: str
) -> None:
    host = make_host(tmp_path)
    async with ssh_host(host) as (port, _):
        job, server_id = await _initialize(owner, host, port)
    assert job["status"] == "failed" and job["error_code"] == code, job
    statuses = {s["key"]: s["status"] for s in job["steps"]}
    assert statuses[step] == "failed", statuses
    order = list(prov.PREPARE_STEPS)
    assert all(statuses[key] == "pending" for key in order[order.index(step) + 1 :]), statuses
    server = (await _call(owner, "GET", f"/api/v1/agents/servers/{server_id}")).json()["server"]
    assert server["ssh"]["prepared_at"] is None and server["initialization_status"] == "failed"
    assert server["deploy_block_reason"] == "initialization_failed"


async def test_initialization_refuses_a_changed_host_key(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, _ = await owner.prepare(port, initialize=False)
    async with ssh_host(host, port=port):
        job = await owner.initialize(owner.servers[instance_id])
    assert job["error_code"] == "host_key_changed"
    assert {s["key"]: s["status"] for s in job["steps"]}["connect"] == "failed"
    assert host.logins == []


async def test_repeated_initialization_creates_no_instance_bundle_or_key(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server = await owner.add_server(port)
        current = (await _call(owner, "GET", f"/api/v1/agents/servers/{server['id']}")).json()[
            "server"
        ]
        confirmed = await _call(
            owner,
            "POST",
            f"/api/v1/agents/servers/{server['id']}/confirm",
            json={
                "version": current["version"],
                "fingerprint": current["ssh"]["host_key_fingerprint"],
            },
        )
        assert confirmed.status_code == 200
        first = await owner.initialize(server["id"])
        second = await owner.initialize(server["id"])
    assert first["status"] == second["status"] == "succeeded"
    # 第二次：Docker 已经能用，沿用、不升级，不重新登录
    steps = {s["key"]: s["status"] for s in second["steps"]}
    assert steps["docker"] == "skipped" and steps["reconnect"] == "skipped"
    calls = host.calls()
    assert calls.count("install-docker user=deploy") == 1
    # 不建容器、不拉镜像、不全局清理（只读的 docker info / compose version 检查除外）
    started = ("docker compose up", "docker compose run", "docker compose pull", "docker run")
    assert not any("prune" in c or c.startswith(started) for c in calls), calls
    assert host.authorized() == [] and not (host.home / ".ssh").exists()
    assert list((host.home / prov.INSTALL_ROOT_NAME).iterdir()) == []
    counts = await run_sql(
        owner.database_url,
        "SELECT (SELECT count(*) FROM workspace.agent_instances)"
        " + (SELECT count(*) FROM workspace.agent_credentials)"
        " + (SELECT count(*) FROM workspace.agent_deployments)"
        " + (SELECT count(*) FROM workspace.audit_logs WHERE action = 'agents.bundle.issued')",
    )
    assert counts == 0
    ref = await run_sql(
        owner.database_url,
        "SELECT count(*) FROM workspace.agent_ssh_targets WHERE client_key_ref IS NOT NULL"
        " OR client_public_key IS NOT NULL",
    )
    assert ref == 0


# ---------------------------------------------------------------- 单元：两种策略分开


async def test_openclaw_installer_refuses_client_keys_but_runner_keeps_them() -> None:
    target = Target(
        job_id=uuid.uuid4(),
        kind="prepare",
        target_id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        started_by=None,
        address="127.0.0.1",
        port=22,
        username="deploy",
        host_key="ssh-ed25519 AAAA",
        client_key_ref="enc:v1:00000000:legacy",
        host_key_confirmed=True,
        server_id=uuid.uuid4(),
        credential_revision=1,
    )
    recorder = JobRecorder(None, None)  # type: ignore[arg-type]
    installer = AgentServerInstaller(None, None, recorder, target, client_ip=None)  # type: ignore[arg-type]
    with pytest.raises(InstallFailed):
        installer._existing_key()
    with pytest.raises(InstallFailed):
        await installer._authorize(None, None)  # type: ignore[arg-type]
    with pytest.raises(InstallFailed):
        await installer._save_key(None, "")  # type: ignore[arg-type]
    with pytest.raises(InstallFailed):
        await installer._connect(password=None, key=asyncssh.generate_private_key("ssh-ed25519"))
    # 运行服务器仍有自己的专用密钥流程（覆盖了取私钥与保存私钥，装公钥沿用公共实现）
    assert RunnerInstaller._existing_key is not agent_ssh_install.Installer._existing_key
    assert RunnerInstaller._save_key is not agent_ssh_install.Installer._save_key
    assert RunnerInstaller._authorize is agent_ssh_install.Installer._authorize
    assert RunnerInstaller._connect is agent_ssh_install.Installer._connect


def test_retained_deployment_offers_no_instance_ops() -> None:
    """卸载时保留了目录（容器与定时任务已删除）：实例级运维按未部署处理，
    以免“重新拉起”绕过部署记录把容器拉起来（T-0183 核验发现）。"""
    from app.domain.agent_ops import unavailable_reason
    from app.models import AgentDeployment, AgentSshTarget

    target = AgentSshTarget(host_key_confirmed_at=datetime.now(UTC), login_password_ref=None)
    retained = AgentDeployment(
        install_dir="/home/deploy/myworkspace-openclaw/abc", runtime_installed=False
    )
    assert unavailable_reason(retained, target, None) == "not_deployed"
    running = AgentDeployment(
        install_dir="/home/deploy/myworkspace-openclaw/abc", runtime_installed=True
    )
    assert unavailable_reason(running, target, None) == "credential_missing"
    legacy = AgentDeployment(
        install_dir="/home/deploy/myworkspace-openclaw/abc", runtime_installed=None
    )
    assert unavailable_reason(legacy, target, None) == "credential_missing"
