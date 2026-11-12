"""运行服务器的 SSH 自动部署（T-0136，RUNNER_PLAN 第 9 节）。

目标服务器是 `test_agent_ssh_install` 里的进程内 asyncssh 服务器：
命令交给本机 /bin/sh 在临时目录里执行，PATH 里是假的 docker、sudo、bash。
这里的假 bash 代替接入包里的 install.sh（真的脚本要 root、会下载 Docker 与 gVisor），
只检查接入包在、记下调用，并打印成功的标记。
"""

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.domain import runner_ssh
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql
from tests.test_agent_ssh_install import PASSWORD, FakeHost, ssh_host

pytestmark = pytest.mark.integration

SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(secrets.token_hex(32)),
    "agent_public_url": "https://agent.example.com:9443",
    "agent_intranet_url": "https://agent.internal:8443",
    "agent_ssh_allow_loopback": True,
}

_BASH = r"""#!/bin/sh
case "$1" in
  */myworkspace-runner/install.sh)
    dir="$(dirname "$1")"
    echo "install.sh uid=$(id -u)" >> "$FAKE_LOG"
    if [ ! -f "$dir/runner.env" ] || [ ! -f "$dir/compose.yaml" ]; then
      echo "bundle missing in $dir: $(ls -A "$dir" 2>&1)" >&2; exit 4
    fi
    cp "$dir/runner.env" "$STATE/runner.env"
    if [ -f "$STATE/install_hold" ]; then
      touch "$STATE/install_entered"
      tries=0
      while [ -f "$STATE/install_hold" ]; do
        tries=$((tries + 1))
        if [ "$tries" -ge 400 ]; then echo "install_hold_timeout" >&2; exit 7; fi
        sleep 0.05
      done
    fi
    if [ -f "$STATE/install_fail" ]; then echo "gvisor_sha512_mismatch" >&2; exit 5; fi
    touch "$STATE/up"; echo "runner_agent_started"; exit 0 ;;
esac
exec /bin/bash "$@"
"""


class RunnerHost(FakeHost):
    def __init__(self, root: Path, **inspect: str) -> None:
        super().__init__(root, **inspect)
        (self.bin / "bash").write_text(_BASH)

    @property
    def runner_dir(self) -> Path:
        return self.home / runner_ssh.INSTALL_DIR_NAME


class Owner:
    def __init__(self, client: httpx.AsyncClient, workspace: str, database_url: str) -> None:
        self.client = client
        self.workspace = workspace
        self.database_url = database_url

    def headers(self) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace, **csrf(self.client)}

    async def state(self) -> dict[str, Any]:
        response = await self.client.get("/api/v1/runner/host/ssh", headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def prepare(self, port: int) -> int:
        created = await self.client.post(
            "/api/v1/runner/host",
            json={"name": "运行服务器", "ingress": "intranet"},
            headers=self.headers(),
        )
        assert created.status_code == 201, created.text
        response = await self.client.put(
            "/api/v1/runner/host/ssh",
            json={"host": "127.0.0.1", "port": port, "username": "deploy"},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text
        target = response.json()["target"]
        confirmed = await self.client.post(
            "/api/v1/runner/host/ssh/confirm",
            json={"version": target["version"], "fingerprint": target["host_key_fingerprint"]},
            headers=self.headers(),
        )
        assert confirmed.status_code == 200, confirmed.text
        return int(confirmed.json()["target"]["version"])

    async def run(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/runner/host/ssh/{path}", json=body, headers=self.headers()
        )
        assert response.status_code == 202, response.text
        for _ in range(600):
            state = await self.state()
            if state["job"] and state["job"]["status"] != "running":
                return state
            await asyncio.sleep(0.05)
        raise AssertionError("任务 30 秒内没有结束")


@pytest.fixture
async def owner(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Owner]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Owner(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


@pytest.fixture(autouse=True)
def fast_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_ssh, "ONLINE_POLL", 0.05)


async def _heartbeat(owner: Owner, host: RunnerHost) -> None:
    """模拟 runner-agent 用新凭证长轮询（install.sh 启动它之后）。"""
    while not (host.state / "up").exists():  # noqa: ASYNC110 - 等假 bash 写出的文件
        await asyncio.sleep(0.02)
    await run_sql(owner.database_url, "UPDATE workspace.runner_hosts SET last_seen_at = now()")


async def _install(owner: Owner, host: RunnerHost, body: dict[str, Any]) -> dict[str, Any]:
    beat = asyncio.create_task(_heartbeat(owner, host))
    try:
        return await owner.run("install", body)
    finally:
        beat.cancel()


def _steps(state: dict[str, Any]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in state["job"]["steps"]}


async def _stored_anywhere(owner: Owner, value: str) -> int:
    count: int = await run_sql(
        owner.database_url,
        """
        SELECT count(*) FROM (
            SELECT row_to_json(j)::text AS t FROM workspace.runner_ssh_jobs j
            UNION ALL SELECT row_to_json(s)::text FROM workspace.runner_ssh_targets s
            UNION ALL SELECT row_to_json(a)::text FROM workspace.audit_logs a
        ) rows WHERE strpos(t, $1) > 0
        """,
        value,
    )
    return count


async def test_deploy_with_password_uses_sudo_then_the_dedicated_key(
    owner: Owner, tmp_path: Path
) -> None:
    host = RunnerHost(tmp_path)
    async with ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        state = await _install(owner, host, {"version": version, "password": PASSWORD})
        assert state["job"]["status"] == "succeeded", state["job"]["log"][-1500:]
        assert _steps(state) == {key: "done" for key in runner_ssh.STEPS}
        target = state["target"]
        assert target["client_key_installed"] is True
        assert target["install_dir"] == str(host.runner_dir)
        assert host.logins[0] == "password"
        # 接入包上传了、install.sh 经 sudo（密码）运行
        assert (host.runner_dir / "compose.yaml").exists()
        assert any(c.startswith("sudo bash ") and "install.sh" in c for c in host.calls())
        token = next(
            line.split("=", 1)[1]
            for line in (host.state / "runner.env").read_text().splitlines()
            if line.startswith("MYWS_RUNNER_TOKEN=")
        )
        # 密码与运行服务器凭证都不落库（任务日志、目标、审计）
        assert await _stored_anywhere(owner, PASSWORD) == 0
        assert await _stored_anywhere(owner, token) == 0
        assert "myworkspace-runner-" in (host.home / ".ssh" / "authorized_keys").read_text()

        # 不是 root 账号：重新部署不填密码就做不了（install.sh 要 sudo）
        state = await _install(owner, host, {"version": target["version"]})
        assert state["job"]["status"] == "failed"
        assert state["job"]["error_code"] == "password_required"
        assert host.logins[-1] == "publickey"


async def test_root_account_redeploys_with_the_key_only(owner: Owner, tmp_path: Path) -> None:
    host = RunnerHost(tmp_path, uid="0", gid="0")
    async with ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        first = await _install(owner, host, {"version": version, "password": PASSWORD})
        assert first["job"]["status"] == "succeeded", first["job"]
        (host.state / "up").unlink()
        again = await _install(owner, host, {"version": first["target"]["version"]})
        assert again["job"]["status"] == "succeeded", again["job"]
        assert _steps(again)["authorize_key"] == "skipped"
        assert not any(c.startswith("sudo ") for c in host.calls())


async def test_conflicting_physical_host_is_rejected_before_key_or_runtime_changes(
    owner: Owner,
    tmp_path: Path,
) -> None:
    from app.domain.physical_hosts import claim_physical_host
    from app.models import AgentServer
    from tests.workspace_support import app_of

    host = RunnerHost(tmp_path)
    async with ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        app = app_of(owner.client)
        async with app.state.sessionmaker() as db:
            existing = AgentServer(
                workspace_id=uuid.UUID(owner.workspace), name="existing", kind="ssh"
            )
            db.add(existing)
            await db.flush()
            assert await claim_physical_host(
                db,
                host.resources["physical_host_id"],
                workspace_id=existing.workspace_id,
                role="agent",
                server_id=existing.id,
            )
            await db.commit()
        result = await _install(owner, host, {"version": version, "password": PASSWORD})
        assert result["job"]["status"] == "failed", result["job"]
        assert result["job"]["error_code"] == "physical_host_conflict"
        assert host.authorized() == []
        assert not host.runner_dir.exists()


async def test_host_removal_keeps_identity_during_remote_install(
    owner: Owner, tmp_path: Path
) -> None:
    from app.domain.physical_hosts import claim_physical_host
    from app.models import AgentServer, PhysicalHostRegistration
    from tests.workspace_support import app_of

    host = RunnerHost(tmp_path)
    hold = host.state / "install_hold"
    hold.touch()
    async with ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        host_id = await run_sql(owner.database_url, "SELECT id FROM workspace.runner_hosts")
        install = asyncio.create_task(
            _install(owner, host, {"version": version, "password": PASSWORD})
        )
        try:
            async with asyncio.timeout(10):
                while not (host.state / "install_entered").exists():
                    if install.done():
                        raise AssertionError(f"installer ended before hold: {install.result()}")
                    await asyncio.sleep(0.02)
            current = await owner.client.get(
                f"/api/v1/runner/hosts/{host_id}", headers=owner.headers()
            )
            assert current.status_code == 200, current.text
            removed = await owner.client.delete(
                f"/api/v1/runner/hosts/{host_id}",
                params={"version": current.json()["version"]},
                headers=owner.headers(),
            )
            assert removed.status_code == 409, removed.text
            assert removed.json()["error"]["code"] == "RUNNER_SSH_JOB_RUNNING"
            app = app_of(owner.client)
            async with app.state.sessionmaker() as db:
                registration = await db.get(
                    PhysicalHostRegistration, host.resources["physical_host_id"]
                )
                assert registration is not None
                assert registration.runner_host_id == host_id
                competing = AgentServer(
                    workspace_id=uuid.UUID(owner.workspace), name="other-pool", kind="ssh"
                )
                db.add(competing)
                await db.flush()
                assert not await claim_physical_host(
                    db,
                    host.resources["physical_host_id"],
                    workspace_id=competing.workspace_id,
                    role="agent",
                    server_id=competing.id,
                )
                await db.rollback()
        finally:
            hold.unlink(missing_ok=True)
            results = await asyncio.gather(install, return_exceptions=True)
        assert isinstance(results[0], dict), results[0]
        assert results[0]["job"]["status"] == "succeeded", results[0]
        current = await owner.client.get(f"/api/v1/runner/hosts/{host_id}", headers=owner.headers())
        removed = await owner.client.delete(
            f"/api/v1/runner/hosts/{host_id}",
            params={"version": current.json()["version"]},
            headers=owner.headers(),
        )
        assert removed.status_code == 204, removed.text
        assert (
            await run_sql(
                owner.database_url, "SELECT count(*) FROM workspace.physical_host_registrations"
            )
            == 0
        )


async def test_start_job_holds_host_lock_before_creating_job(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select
    from sqlalchemy.exc import DBAPIError

    from app.models import RunnerHost as RunnerHostModel
    from tests.workspace_support import app_of

    entered = asyncio.Event()
    release = asyncio.Event()
    original = runner_ssh.RunnerSshService._start

    async def paused_start(service: runner_ssh.RunnerSshService, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await original(service, *args, **kwargs)

    monkeypatch.setattr(runner_ssh.RunnerSshService, "_start", paused_start)
    host = RunnerHost(tmp_path)
    async with ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        install = asyncio.create_task(
            _install(owner, host, {"version": version, "password": PASSWORD})
        )
        try:
            async with asyncio.timeout(10):
                await entered.wait()
            app = app_of(owner.client)
            async with app.state.sessionmaker() as db:
                with pytest.raises(DBAPIError) as locked:
                    await db.scalar(select(RunnerHostModel).with_for_update(nowait=True))
                assert getattr(locked.value.orig, "sqlstate", None) == "55P03"
                await db.rollback()
        finally:
            release.set()
            results = await asyncio.gather(install, return_exceptions=True)
        assert isinstance(results[0], dict), results[0]
        assert results[0]["job"]["status"] == "succeeded", results[0]


async def test_failed_install_script_and_uninstall(owner: Owner, tmp_path: Path) -> None:
    host = RunnerHost(tmp_path)
    async with ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        (host.state / "install_fail").touch()
        failed = await _install(owner, host, {"version": version, "password": PASSWORD})
        assert failed["job"]["status"] == "failed"
        assert failed["job"]["error_code"] == "install_failed"
        assert "gvisor_sha512_mismatch" in failed["job"]["log"]
        (host.state / "install_fail").unlink()
        ok = await _install(
            owner, host, {"version": failed["target"]["version"], "password": PASSWORD}
        )
        assert ok["job"]["status"] == "succeeded", ok["job"]
        # 卸载：停止 runner-agent（sudo）并删除接入包目录
        removed = await owner.run(
            "uninstall",
            {"version": ok["target"]["version"], "remove_files": True, "password": PASSWORD},
        )
        assert removed["job"]["status"] == "succeeded", removed["job"]
        assert any("compose -f" in c and c.endswith("down") for c in host.calls())
        assert not host.runner_dir.exists()
        assert removed["target"]["install_dir"] is None
        # 吊销专用密钥，再删除目标
        revoked = await owner.client.post(
            "/api/v1/runner/host/ssh/revoke",
            json={"version": removed["target"]["version"]},
            headers=owner.headers(),
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["target"]["client_key_installed"] is False
        assert "myworkspace-runner-" not in (host.home / ".ssh" / "authorized_keys").read_text()
        deleted = await owner.client.delete(
            "/api/v1/runner/host/ssh",
            params={"version": revoked.json()["target"]["version"]},
            headers=owner.headers(),
        )
        assert deleted.status_code == 204
        assert (await owner.state())["target"] is None


async def test_only_the_owner_deploys(owner: Owner, make_client: ClientFactory) -> None:
    # 还没有添加运行服务器：读状态是“没有目标服务器”（200），填写地址要先添加（404）
    empty = await owner.client.get("/api/v1/runner/host/ssh", headers=owner.headers())
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"target": None, "job": None, "warning": None}
    early = await owner.client.put(
        "/api/v1/runner/host/ssh",
        json={"host": "203.0.113.20", "port": 22, "username": "root"},
        headers=owner.headers(),
    )
    assert early.status_code == 404, early.text
    created = await owner.client.post(
        "/api/v1/runner/host",
        json={"name": "运行服务器", "ingress": "intranet"},
        headers=owner.headers(),
    )
    assert created.status_code == 201
    user = await add_user(owner.database_url, "admin-ssh@example.com", OWNER["password"])
    await add_member(owner.database_url, uuid.UUID(owner.workspace), user, "admin")
    async with make_client(**SETTINGS) as admin:
        login = await admin.post(
            "/api/v1/auth/login",
            json={"email": "admin-ssh@example.com", "password": OWNER["password"]},
        )
        assert login.status_code == 200, login.text
        headers = {"X-Workspace-Id": owner.workspace, **csrf(admin)}
        assert (await admin.get("/api/v1/runner/host/ssh", headers=headers)).status_code == 403
        assert (await admin.get("/api/v1/runner/overview", headers=headers)).status_code == 200
