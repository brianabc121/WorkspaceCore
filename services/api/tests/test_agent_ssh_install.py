"""SSH 自动部署 B2（T-0089）：初始化、部署、卸载任务（T-0179 起全程用保存的登录密码）。

目标服务器是进程内的 asyncssh 服务器（127.0.0.1 随机端口，密码与公钥登录、SFTP）。
它把收到的命令交给本机的 /bin/sh 在临时目录里真正执行，PATH 里是假的 docker、sudo、bash、
chown、crontab、timeout（记录参数、模拟结果），所以 authorized_keys、crontab、上传的文件都是
真实写出来的；只有只读检查（`sh -s` 读入 inspect.sh）由测试直接给出结果，方便模拟不同的服务器。
初始化最后一步的检查（verify.sh）真的在 /bin/sh 里运行；连工作台接入地址用的是假的 timeout。
"""

import asyncio
import hashlib
import json
import re
import secrets
import stat
import subprocess
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app import agent_bundle
from app import agent_provisioning as prov
from app.domain import agent_ssh_install
from app.domain.agent_ssh_install import JobRecorder, SshJobRunner
from app.integrations import ssh
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(secrets.token_hex(32)),
    "agent_public_url": "https://agent.example.com:9443",
    "agent_ssh_allow_loopback": True,
}
PASSWORD = "server login password 42"  # noqa: S105 - 测试用的假密码

_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  info)
    if [ -f "$STATE/docker_ok" ]; then echo 29.9.1; exit 0; fi
    echo "permission denied while trying to connect to the docker API" >&2; exit 1 ;;
  run) exit 0 ;;
  compose)
    shift
    case "$1" in
      version) echo 5.6.0 ;;
      pull) if [ -f "$STATE/pull_fail" ]; then echo "pull access denied" >&2; exit 1; fi ;;
      run)
        cat > "$STATE/plugin-stdin"
        if [ -f "$STATE/plugin_fail" ]; then echo "plugin_sha256_mismatch 0000" >&2; exit 6; fi
        # 与 install-plugin.sh 一致：已经装了锁定版本时只打印这一行、不再装；
        # 补丁（T-0120）两种情况都打
        if [ -f "$STATE/plugin_installed" ]; then echo "plugin_already_installed 0.3.3"
        else touch "$STATE/plugin_installed"; echo "Installed plugin: openclaw-channel"; fi
        if [ -f "$STATE/plugin_patched" ]; then echo "plugin_patch_already_applied"
        else touch "$STATE/plugin_patched"; echo "plugin_patch_applied"; fi ;;
      up) touch "$STATE/up" ;;
      ps) echo "openclaw   running" ;;
      logs) echo "openclaw | token $(grep MYWS_INSTANCE_TOKEN myworkspace.env | cut -d= -f2)" ;;
      # 每晚复盘（T-0100）：与 OpenClaw 2026.9.36 一样，同一个 declaration key 第二次是更新
      exec)
        if [ -f "$STATE/reflection_fail" ]; then echo "gateway not reachable" >&2; exit 1; fi
        # 刚启动时网关还没就绪：第一次失败，之后成功
        if [ -f "$STATE/reflection_fail_once" ]; then
          rm "$STATE/reflection_fail_once"; echo "gateway not reachable" >&2; exit 1
        fi
        if [ -f "$STATE/reflection" ]; then echo '{"created": false, "updated": true}'
        else touch "$STATE/reflection"; echo '{"created": true}'; fi ;;
    esac ;;
esac
exit 0
"""
_SUDO = r"""#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in -S|-k) shift ;; -p) shift 2 ;; *) break ;; esac
done
IFS= read -r pw || pw=""
if [ "$pw" != "$(cat "$STATE/sudo_password")" ]; then
  echo "Sorry, try again." >&2; exit 1
fi
echo "sudo $*" >> "$FAKE_LOG"
exec "$@"
"""
_BASH = r"""#!/bin/sh
case "$1" in
  *install-docker.sh)
    echo "install-docker user=$2" >> "$FAKE_LOG"
    cp "$1" "$STATE/install-docker.sh"
    if [ -f "$STATE/docker_install_fail" ]; then echo "unsupported_os centos 9" >&2; exit 3; fi
    touch "$STATE/docker_ok"; echo 29.9.1; exit 0 ;;
esac
exec /bin/bash "$@"
"""
_CHOWN = r"""#!/bin/sh
echo "chown $*" >> "$FAKE_LOG"
"""
# 初始化最后一步检查能否连到工作台的接入地址（verify.sh 用 timeout + bash 建一次 TCP 连接）
_TIMEOUT = r"""#!/bin/sh
echo "timeout $*" >> "$FAKE_LOG"
if [ -f "$STATE/ingress_fail" ]; then exit 1; fi
exit 0
"""
_CRONTAB = r"""#!/bin/sh
case "$1" in
  -l)
    if [ -f "$STATE/crontab" ]; then cat "$STATE/crontab"; else echo "no crontab" >&2; exit 1; fi ;;
  # 与真实的 crontab 一样：读完标准输入再整体替换（管道左边的 crontab -l 还在读旧文件）
  -) t="$(mktemp)"; cat > "$t"; mv "$t" "$STATE/crontab" ;;
esac
"""


class FakeHost:
    def __init__(self, root: Path, **inspect: str) -> None:
        self.home = root / "home"
        self.bin = root / "bin"
        self.state = root / "state"
        self.log = root / "fake.log"
        for path in (self.home, self.bin, self.state):
            path.mkdir(parents=True)
        self.log.write_text("")
        (self.state / "sudo_password").write_text(PASSWORD)
        for name, body in {
            "docker": _DOCKER,
            "sudo": _SUDO,
            "bash": _BASH,
            "chown": _CHOWN,
            "crontab": _CRONTAB,
            "timeout": _TIMEOUT,
        }.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.password = PASSWORD
        self.inspect = inspect
        self.logins: list[str] = []
        self.commands: list[str] = []
        self.stdin: list[bytes] = []
        # Explicit fake host data for the independent SSH probe; this is never
        # claimed as a real machine capacity verification.
        self.resources = {
            "generation": str(uuid.uuid4()),
            "physical_host_id": uuid.uuid4().hex * 2,
            "sequence": 1000,
            "cores": 16,
            "cpu_percent": 5,
            "load1": 0.2,
            "mem_total": 32 << 30,
            "mem_available": 24 << 30,
            "root_total": 200 << 30,
            "root_free": 150 << 30,
            "data_total": 200 << 30,
            "data_free": 150 << 30,
            "runtime_ready": True,
            "network_ready": True,
            "managed_identities": [],
            "managed_usage": [],
        }

    @property
    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STATE": str(self.state),
            "FAKE_LOG": str(self.log),
        }

    def inspect_output(self) -> str:
        ready = (self.state / "docker_ok").exists()
        values = {
            "os_id": "ubuntu",
            "os_version": "24.04",
            "os_codename": "noble",
            "arch": "x86_64",
            "uid": "1001",
            "gid": "1001",
            "user": "deploy",
            "home": str(self.home),
            "docker": "yes" if ready else "no",
            "docker_access": "yes" if ready else "no",
            "compose": "yes" if ready else "no",
            "sudo": "yes",
            "crontab": "yes",
            **self.inspect,
        }
        return "".join(f"{key}={value}\n" for key, value in values.items())

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    @property
    def directory(self) -> Path:
        """这个实例的接入包目录：~/myworkspace-openclaw/<实例短号>（测试里一台服务器只部署一个）。"""
        root = self.home / prov.INSTALL_ROOT_NAME
        found = sorted(root.iterdir()) if root.is_dir() else []
        return found[0] if found else root / "not-installed"

    def authorized(self) -> list[str]:
        path = self.home / ".ssh" / "authorized_keys"
        return path.read_text().splitlines() if path.exists() else []

    async def handle(self, process: asyncssh.SSHServerProcess[bytes]) -> None:
        command = process.command or ""
        self.commands.append(command)
        data = await process.stdin.read()
        self.stdin.append(data)
        if command == "sh -s":
            if b"/proc/sys/kernel/random/boot_id" in data:
                self.resources["sequence"] += 1
                process.stdout.write(json.dumps(self.resources).encode())
                process.exit(0)
                return
            if b"# myworkspace-physical-id-v1" in data:
                process.stdout.write((self.resources["physical_host_id"] + "\n").encode())
                process.exit(0)
                return
            process.stdout.write(self.inspect_output().encode())
            process.exit(0)
            return
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=self.home,
        )
        out, err = await proc.communicate(data)
        process.stdout.write(out)
        process.stderr.write(err)
        process.exit(proc.returncode or 0)


class _Server(asyncssh.SSHServer):
    def __init__(self, host: FakeHost) -> None:
        self.host = host

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        ok = username == "deploy" and password == self.host.password
        self.host.logins.append("password" if ok else "password-denied")
        return ok

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        for line in self.host.authorized():
            if asyncssh.import_public_key(line).public_data == key.public_data:
                self.host.logins.append("publickey")
                return username == "deploy"
        self.host.logins.append("publickey-denied")
        return False


@asynccontextmanager
async def ssh_host(
    host: FakeHost, *, port: int = 0, key: asyncssh.SSHKey | None = None
) -> AsyncIterator[tuple[int, asyncssh.SSHKey]]:
    host_key = key or asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        lambda: _Server(host),
        "127.0.0.1",
        port,
        server_host_keys=[host_key],
        process_factory=host.handle,
        sftp_factory=True,
        encoding=None,
        config=[],
        reuse_address=True,
    )
    try:
        yield server.sockets[0].getsockname()[1], host_key
    finally:
        server.close()
        await server.wait_closed()


class Owner:
    def __init__(self, client: httpx.AsyncClient, workspace: str, database_url: str) -> None:
        self.client = client
        self.workspace = workspace
        self.database_url = database_url
        self.servers = {}
        self.by_port = {}

    @property
    def app(self) -> FastAPI:
        transport = self.client._transport
        assert isinstance(transport, httpx.ASGITransport)
        app = transport.app
        assert isinstance(app, FastAPI)
        return app

    def headers(self) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace, **csrf(self.client)}

    async def instance(self, network_mode: str = "public") -> str:
        response = await self.client.post(
            "/api/v1/agents/instances",
            json={"name": "自动部署", "network_mode": network_mode},
            headers=self.headers(),
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    # 一台服务器多个实例后（T-0145）：SSH 信息在服务器上，实例的目录在部署记录里。
    # 下面把它们拼回“目标服务器”的样子（target：主机公钥、专用密钥、目录、服务器的版本号），
    # 这个文件里的部署流程断言不用改。
    servers: dict[str, str]
    by_port: dict[int, dict[str, Any]]

    async def _get(self, path: str) -> dict[str, Any]:
        response = await self.client.get(path, headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def instance_version(self, instance_id: str) -> int:
        return int((await self._get(f"/api/v1/agents/instances/{instance_id}"))["version"])

    async def server_version(self, instance_id: str) -> int:
        server = await self._get(f"/api/v1/agents/servers/{self.servers[instance_id]}")
        return int(server["server"]["version"])

    async def target(self, instance_id: str) -> dict[str, Any] | None:
        server_id = self.servers.get(instance_id)
        if server_id is None:
            return None
        detail = await self.client.get(
            f"/api/v1/agents/servers/{server_id}", headers=self.headers()
        )
        if detail.status_code == 404:
            return None
        server = detail.json()["server"]
        deployment = (await self._get(f"/api/v1/agents/instances/{instance_id}/deployment"))[
            "deployment"
        ]
        return {
            **server["ssh"],
            "install_dir": deployment["install_dir"] if deployment else None,
            "installed_at": deployment["installed_at"] if deployment else None,
            "version": server["version"],
        }

    async def state(self, instance_id: str) -> dict[str, Any]:
        deployment = await self._get(f"/api/v1/agents/instances/{instance_id}/deployment")
        job = deployment["job"]
        if job is None and instance_id in self.servers:
            detail = await self.client.get(
                f"/api/v1/agents/servers/{self.servers[instance_id]}", headers=self.headers()
            )
            if detail.status_code == 200:
                job = detail.json()["job"]
        return {"target": await self.target(instance_id), "job": job, "warning": None}

    async def add_server(
        self, port: int, *, username: str = "deploy", password: str = PASSWORD
    ) -> dict[str, Any]:
        if port in self.by_port:
            return self.by_port[port]
        # T-0178：添加服务器时就加密保存登录密码（之后部署 / 初始化都用保存的密码）
        response = await self.client.post(
            "/api/v1/agents/servers",
            json={
                "name": f"服务器 {port}",
                "host": "127.0.0.1",
                "port": port,
                "username": username,
                "login_password": password,
            },
            headers=self.headers(),
        )
        assert response.status_code == 201, response.text
        server: dict[str, Any] = response.json()["server"]
        self.by_port[port] = server
        return server

    async def prepare(
        self,
        port: int,
        *,
        confirm: bool = True,
        password: str = PASSWORD,
        initialize: bool = True,
    ) -> tuple[str, int]:
        """新建一个实例、添加（或复用）这台服务器并确认指纹；initialize 时再初始化服务器
        （T-0179：部署前服务器要已初始化）。返回 (实例 ID, 服务器的版本号)。"""
        instance_id = await self.instance()
        server = await self.add_server(port, password=password)
        self.servers[instance_id] = server["id"]
        current = (await self._get(f"/api/v1/agents/servers/{server['id']}"))["server"]
        if confirm and not current["ssh"]["host_key_confirmed"]:
            response = await self.client.post(
                f"/api/v1/agents/servers/{server['id']}/confirm",
                json={
                    "version": current["version"],
                    "fingerprint": current["ssh"]["host_key_fingerprint"],
                },
                headers=self.headers(),
            )
            assert response.status_code == 200, response.text
            current = response.json()["server"]
        if confirm and initialize and current["initialization_status"] != "ready":
            job = await self.initialize(server["id"])
            assert job["status"] == "succeeded", job
            current = (await self._get(f"/api/v1/agents/servers/{server['id']}"))["server"]
        return instance_id, int(current["version"])

    async def initialize(self, server_id: str) -> dict[str, Any]:
        """初始化服务器（POST /prepare）并等它结束，返回任务。"""
        current = (await self._get(f"/api/v1/agents/servers/{server_id}"))["server"]
        response = await self.client.post(
            f"/api/v1/agents/servers/{server_id}/prepare",
            json={"version": current["version"]},
            headers=self.headers(),
        )
        assert response.status_code == 202, response.text
        for _ in range(600):
            detail = await self._get(f"/api/v1/agents/servers/{server_id}")
            job: dict[str, Any] = detail["job"]
            if job["kind"] == "prepare" and job["status"] != "running":
                return job
            await asyncio.sleep(0.05)
        raise AssertionError("初始化 30 秒内没有结束")

    async def _instance_version_for(self, instance_id: str, version: int) -> int:
        """测试里传的是服务器的版本号：对得上就用实例当前的版本号，对不上就故意给错的。"""
        current = await self.instance_version(instance_id)
        return current if version == await self.server_version(instance_id) else current + 1000

    async def install(
        self, instance_id: str, version: int, password: str | None = PASSWORD, **extra: Any
    ) -> httpx.Response:
        """部署。password 是已弃用的字段（T-0178 起忽略，用服务器上保存的密码），保留它检查兼容。"""
        current = await self._get(f"/api/v1/agents/instances/{instance_id}")
        if current["limits"]["cpus"] is None or current["limits"]["mem_limit_mb"] is None:
            limited = await self.client.put(
                f"/api/v1/agents/instances/{instance_id}/limits",
                json={"cpus": 1, "mem_limit_mb": 2048, "version": current["version"]},
                headers=self.headers(),
            )
            assert limited.status_code == 200, limited.text
        body: dict[str, Any] = {
            "server_id": self.servers[instance_id],
            "version": await self._instance_version_for(instance_id, version),
            **extra,
        }
        if password is not None:
            body["password"] = password
        return await self.client.post(
            f"/api/v1/agents/instances/{instance_id}/deploy",
            json=body,
            headers=self.headers(),
        )

    async def replace(self, instance_id: str, **body: Any) -> httpx.Response:
        return await self.client.put(
            f"/api/v1/agents/servers/{self.servers[instance_id]}/address",
            json={"host": "127.0.0.1", "username": "deploy", "login_password": PASSWORD, **body},
            headers=self.headers(),
        )

    async def remove(self, instance_id: str, version: int) -> httpx.Response:
        return await self.client.delete(
            f"/api/v1/agents/servers/{self.servers[instance_id]}",
            params={"version": version},
            headers=self.headers(),
        )

    async def finished(self, instance_id: str) -> dict[str, Any]:
        for _ in range(600):
            state = await self.state(instance_id)
            if state["job"] and state["job"]["status"] != "running":
                return state
            await asyncio.sleep(0.05)
        raise AssertionError("安装任务 30 秒内没有结束")


@pytest.fixture
async def owner(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Owner]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Owner(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


@pytest.fixture(autouse=True)
def fast_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_ssh_install, "ONLINE_POLL", 0.05)
    # 复盘任务的重试：测试里不等 10 秒
    monkeypatch.setattr(agent_ssh_install, "REFLECTION_TRIES", 3)
    monkeypatch.setattr(agent_ssh_install, "REFLECTION_WAIT", 0)


def _token(host: FakeHost) -> str:
    for line in (host.directory / "myworkspace.env").read_text().splitlines():
        if line.startswith("MYWS_INSTANCE_TOKEN="):
            return line.split("=", 1)[1]
    raise AssertionError("myworkspace.env 里没有实例凭证")


async def _heartbeat(owner: Owner, host: FakeHost) -> None:
    """模拟新部署的 OpenClaw 用新凭证连上工作台（compose up 之后）。"""
    while not (host.state / "up").exists():  # noqa: ASYNC110 - 等假 docker 写出的文件
        await asyncio.sleep(0.02)
    digest = hashlib.sha256(_token(host).encode()).hexdigest()
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_credentials SET last_used_at = now() WHERE token_hash = $1",
        digest,
    )


async def _install_ok(
    owner: Owner, host: FakeHost, instance_id: str, version: int, password: str | None = PASSWORD
) -> dict[str, Any]:
    beat = asyncio.create_task(_heartbeat(owner, host))
    try:
        response = await owner.install(instance_id, version, password)
        assert response.status_code == 202, response.text
        assert response.json()["job"]["status"] == "running"
        state = await owner.finished(instance_id)
    finally:
        beat.cancel()
    assert state["job"]["status"] == "succeeded", state["job"]
    return state


def _steps(state: dict[str, Any]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in state["job"]["steps"]}


def _detail(state: dict[str, Any], key: str) -> str | None:
    return next((s["detail"] for s in state["job"]["steps"] if s["key"] == key), None)


async def _stored_anywhere(owner: Owner, value: str) -> int:
    count: int = await run_sql(
        owner.database_url,
        """
        SELECT count(*) FROM (
            SELECT row_to_json(j)::text AS t FROM workspace.agent_ssh_jobs j
            UNION ALL SELECT row_to_json(s)::text FROM workspace.agent_ssh_targets s
            UNION ALL SELECT row_to_json(a)::text FROM workspace.audit_logs a
        ) rows WHERE strpos(t, $1) > 0
        """,
        value,
    )
    return count


# ---------------------------------------------------------------- 初始化 + 部署（T-0179：全程密码）


async def test_initialize_then_deploy_with_the_saved_password(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port, initialize=False)
        prepare = await owner.initialize(owner.servers[instance_id])
        assert prepare["status"] == "succeeded", prepare
        assert [s["key"] for s in prepare["steps"]] == list(prov.PREPARE_STEPS)
        assert {s["key"]: s["status"] for s in prepare["steps"]} == {
            key: "done" for key in prov.PREPARE_STEPS
        }
        # 初始化不建实例目录、不发接入包、不装公钥
        root = host.home / prov.INSTALL_ROOT_NAME
        assert root.is_dir() and list(root.iterdir()) == []
        assert host.authorized() == []
        verify = next(s for s in prepare["steps"] if s["key"] == "verify")
        assert "agent.example.com:9443" in (verify["detail"] or "")
        calls = host.calls()
        # 先检查 sudo（密码从标准输入），再用 root 运行 Docker 安装脚本，把登录账号加入 docker 组
        assert "sudo true" in calls
        assert "install-docker user=deploy" in calls
        assert (host.state / "install-docker.sh").read_text() == prov.script("install-docker.sh")
        assert not [p for p in host.home.iterdir() if p.name.startswith(prov.STAGING_DIR_NAME)], (
            "安装脚本用完要删除"
        )
        assert any(
            c.startswith("timeout 10 bash -c") and "agent.example.com 9443" in c for c in calls
        )
        # 装过 Docker：重新用密码登录一次（docker 组生效）
        assert host.logins == ["password", "password"]
        state = await _install_ok(owner, host, instance_id, version)

    assert _steps(state) == {key: "done" for key in prov.STEPS}
    target = state["target"]
    assert target["auth_method"] == "password" and target["client_key_installed"] is False
    assert target["client_key_fingerprint"] is None
    assert target["install_dir"] == str(host.directory)
    assert target["installed_at"] is not None
    deployment = (await owner._get(f"/api/v1/agents/instances/{instance_id}/deployment"))[
        "deployment"
    ]
    assert deployment["runtime_installed"] is True

    # 全程用密码登录，从来没有尝试公钥；服务器上没有工作台的公钥
    assert set(host.logins) == {"password"} and len(host.logins) == 3
    assert host.authorized() == []
    assert not (host.home / ".ssh").exists()

    calls = host.calls()
    # 部署不再装 Docker
    assert calls.count("install-docker user=deploy") == 1
    # 每个实例一个目录：~/myworkspace-openclaw/<实例 ID 去掉连字符的前 12 位>
    assert re.fullmatch(r"[0-9a-f]{12}", host.directory.name)
    assert host.directory.parent == host.home / "myworkspace-openclaw"
    # uid 既不是 0 也不是 1000：用锁定镜像里的 chown 把 state/、bin/、tls/ 交给 uid 1000
    handover = [c for c in calls if c.startswith("docker run") and "1000:1000" in c]
    assert len(handover) == 1
    assert prov.OPENCLAW_IMAGE in handover[0]
    assert "--network none --user 0 --entrypoint chown" in handover[0]
    assert "/target/state /target/bin /target/tls" in handover[0]
    assert "docker compose pull --quiet" in calls
    assert "docker compose run --rm --no-deps -T --entrypoint sh openclaw -s" in calls
    assert "docker compose up -d" in calls
    # 安装插件前先起这个实例的网络（net 服务）：插件要联网下载依赖
    assert calls.index("docker compose up -d net") < calls.index(
        "docker compose run --rm --no-deps -T --entrypoint sh openclaw -s"
    )
    # 每晚复盘（T-0100）：OpenClaw 上线后用接入包里的脚本建定时任务
    assert any(
        "docker compose exec -T openclaw node openclaw.mjs automations add" in c for c in calls
    )
    assert (host.state / "reflection").exists()
    # 插件脚本：锁定版本与 sha256
    plugin = (host.state / "plugin-stdin").read_text()
    assert f'package="{prov.PLUGIN_PACKAGE}"' in plugin
    assert f'"{prov.PLUGIN_NAME}" "{agent_bundle.PLUGIN_VERSION}"' in plugin
    assert f'expected="{prov.PLUGIN_SHA256}"' in plugin
    assert "--force --accept-capabilities" in plugin
    # 装好之后打工作台的补丁（T-0120）
    assert "node /opt/myworkspace/bin/channel-patch.mjs" in plugin
    assert "（已打补丁）" in (_detail(state, "plugin") or "")

    # 接入包：权限与 zip 里一致
    files = {
        "myworkspace.env": 0o600,
        "state/openclaw.json": 0o600,
        "compose.yaml": 0o644,
        "bin/myworkspace-secret-resolver": 0o755,
        "bin/myworkspace-openclaw-start": 0o755,
        "bin/usage-report.mjs": 0o755,
        "bin/host-report.mjs": 0o755,
        "tls/client.key": 0o600,
        "tls/client.crt": 0o644,
    }
    for name, mode in files.items():
        assert stat.S_IMODE((host.directory / name).stat().st_mode) == mode, name
    assert stat.S_IMODE(host.directory.stat().st_mode) == 0o700
    compose = (host.directory / "compose.yaml").read_text()
    assert "tunnel:" in compose and f"name: myworkspace-openclaw-{host.directory.name}" in compose
    assert "network_mode: host" not in compose
    # 服务器的登录密码不进接入包
    for path in host.directory.rglob("*"):
        if path.is_file():
            assert PASSWORD.encode() not in path.read_bytes(), path

    # crontab：七行都带标记（每 10 分钟取密钥、每分钟上报用量与服务器状态、应用主模型、
    # 每分钟领取唤醒（T-0121）与终止令（T-0126）、每周一重启）
    cron = (host.state / "crontab").read_text().splitlines()
    assert len(cron) == 7
    assert all(line.endswith(f"# mw-openclaw:{host.directory.name}") for line in cron)
    assert "secrets reload --json" in cron[0]
    assert cron[1].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/usage-report.mjs" in cron[1]
    assert cron[2].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/host-report.mjs" in cron[2]
    assert cron[3].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/wakeup.mjs" in cron[3]
    assert "exec -T openclaw node /opt/myworkspace/bin/stop-runs.mjs" in cron[4]
    # 每分钟应用工作台设置的主模型（T-0205）
    assert cron[5].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/model-config.mjs" in cron[5]
    assert "docker compose restart openclaw" in cron[6]

    # 密码不在任何表里（任务、目标服务器、审计）；任务日志里也没有令牌
    assert await _stored_anywhere(owner, PASSWORD) == 0
    assert await _stored_anywhere(owner, _token(host)) == 0
    assert "mwa_" not in state["job"]["log"].replace("mwa_***", "")
    actions = await run_sql(
        owner.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.%'",
    )
    for action in (
        "agents.ssh.prepare_started",
        "agents.ssh.prepare_succeeded",
        "agents.ssh.install_started",
        "agents.bundle.issued",
        "agents.ssh.install_succeeded",
    ):
        assert action in actions
    assert "key_authorized" not in actions
    ref = await run_sql(
        owner.database_url, "SELECT client_key_ref FROM workspace.agent_ssh_targets"
    )
    assert ref is None


async def test_redeploy_patches_a_plugin_installed_before_t0120(
    owner: Owner, tmp_path: Path
) -> None:
    """T-0120 之前部署的实例：插件已经装了、还没打补丁；重新部署不重装插件，只补打补丁。"""
    host = FakeHost(tmp_path)
    (host.state / "plugin_installed").touch()
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
    assert _steps(state)["plugin"] == "done"
    assert "这次打上了工作台的补丁" in (_detail(state, "plugin") or "")
    assert (host.state / "plugin_patched").exists()


async def test_redeploy_reuses_the_instance_directory(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await _install_ok(owner, host, instance_id, version)
        (host.state / "up").unlink()
        logins = len(host.logins)
        # 请求里的 password 已弃用、忽略：仍用保存的密码
        second = await _install_ok(owner, host, instance_id, version, password=None)

    steps = _steps(second)
    # 已经安装了锁定版本的插件：不再重装（OpenClaw 运行过后带 --force 重装会被拒绝）
    assert steps["plugin"] == "skipped"
    assert host.logins[logins:] == ["password"]
    assert host.authorized() == []
    # 重装前先把 state/、bin/、tls/ 收回给登录账号（1001），装完再交给 1000
    calls = host.calls()
    runs = [c for c in calls if c.startswith("docker run")]
    reclaim = [i for i, c in enumerate(runs) if f"{prov.OPENCLAW_IMAGE} -R 1001:1001 " in c]
    handover = [i for i, c in enumerate(runs) if f"{prov.OPENCLAW_IMAGE} -R 1000:1000 " in c]
    assert len(reclaim) == 1
    assert len(handover) == 2
    assert handover[0] < reclaim[0] < handover[1]
    cron = (host.state / "crontab").read_text().splitlines()
    assert len(cron) == 7, "重装时替换这个实例的行，不重复"
    # 每晚复盘的定时任务：同名的已有就更新，不重复建（T-0100）
    reflection = next(s for s in second["job"]["steps"] if s["key"] == "reflection")
    assert reflection["status"] == "done" and "已更新" in (reflection["detail"] or "")


async def test_reflection_failure_does_not_fail_the_install(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    (host.state / "reflection_fail").touch()
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
    reflection = next(s for s in state["job"]["steps"] if s["key"] == "reflection")
    assert reflection["status"] == "skipped"
    assert "setup-reflection.sh" in (reflection["detail"] or "")
    assert state["target"]["installed_at"] is not None
    # 网关一直没就绪：脚本按安装器给的次数重试后放弃
    assert sum("automations add" in line for line in host.calls()) == 3
    assert "网关一直没有就绪" in state["job"]["log"]


async def test_reflection_waits_for_the_gateway(owner: Owner, tmp_path: Path) -> None:
    """实例刚连上工作台时 OpenClaw 的网关可能还没就绪：重试到成功，步骤记为完成。"""
    host = FakeHost(tmp_path)
    (host.state / "reflection_fail_once").touch()
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
    reflection = next(s for s in state["job"]["steps"] if s["key"] == "reflection")
    assert reflection["status"] == "done"
    assert sum("automations add" in line for line in host.calls()) == 2
    assert any("MYWS_REFLECTION_TRIES=3" in c for c in host.commands)


async def test_root_account_uses_chown_and_no_sudo(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path, uid="0", gid="0", user="root")
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await _install_ok(owner, host, instance_id, version)
    calls = host.calls()
    assert "sudo true" not in calls
    assert "install-docker user=" in calls
    chowns = [c for c in calls if c.startswith("chown ")]
    assert chowns
    assert all(c.startswith("chown -R 1000:1000 ") for c in chowns)
    assert not any(c.startswith("docker run") for c in calls)


async def test_docker_already_usable_skips_install(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path, uid="1000", gid="1000", crontab="no")
    (host.state / "docker_ok").touch()
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port, initialize=False)
        prepare = await owner.initialize(owner.servers[instance_id])
        state = await _install_ok(owner, host, instance_id, version)
    init_steps = {s["key"]: s["status"] for s in prepare["steps"]}
    # 已有可用的 Docker：沿用、不升级，也不用重新登录
    assert init_steps["docker"] == "skipped" and init_steps["reconnect"] == "skipped"
    assert init_steps["workspace"] == "done" and init_steps["verify"] == "done"
    steps = _steps(state)
    assert steps["schedule"] == "skipped"
    calls = host.calls()
    assert not any(c.startswith(("sudo", "install-docker", "chown")) for c in calls)
    assert not any("prune" in c for c in calls)
    # uid 1000：不用改属主
    assert not any(c.startswith("docker run") for c in calls)
    # 初始化一次登录、部署一次登录
    assert host.logins == ["password", "password"]


# ---------------------------------------------------------------- 失败


async def test_wrong_password_fails_without_key(owner: Owner, tmp_path: Path) -> None:
    """保存的登录密码不对：初始化在 connect 一步失败，不装公钥、不回退到别的凭证。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, _ = await owner.prepare(
            port,
            password="wrong password 1",  # noqa: S106
            initialize=False,
        )
        job = await owner.initialize(owner.servers[instance_id])
        server = (await owner._get(f"/api/v1/agents/servers/{owner.servers[instance_id]}"))[
            "server"
        ]
    assert job["status"] == "failed"
    assert job["error_code"] == "auth_failed"
    assert {s["key"]: s["status"] for s in job["steps"]}["connect"] == "failed"
    assert server["initialization_status"] == "failed" and server["deployable"] is False
    assert server["ssh"]["client_key_installed"] is False
    assert host.authorized() == []
    assert set(host.logins) == {"password-denied"}
    assert await _stored_anywhere(owner, "wrong password 1") == 0


async def test_sudo_denied(owner: Owner, tmp_path: Path) -> None:
    """需要另外的 sudo 密码（或交互式验证）：在 docker 一步明确失败，不算初始化完成。"""
    host = FakeHost(tmp_path)
    (host.state / "sudo_password").write_text("another password")
    async with ssh_host(host) as (port, _):
        instance_id, _ = await owner.prepare(port, initialize=False)
        job = await owner.initialize(owner.servers[instance_id])
        server = (await owner._get(f"/api/v1/agents/servers/{owner.servers[instance_id]}"))[
            "server"
        ]
    assert job["error_code"] == "sudo_denied"
    assert "不支持" in job["error_message"]
    steps = {s["key"]: s["status"] for s in job["steps"]}
    assert steps["docker"] == "failed" and steps["workspace"] == "pending"
    assert server["ssh"]["prepared_at"] is None
    assert server["initialization_status"] == "failed"
    assert host.authorized() == []


async def test_unsupported_os_without_docker(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path, os_id="centos", os_version="9")
    async with ssh_host(host) as (port, _):
        instance_id, _ = await owner.prepare(port, initialize=False)
        job = await owner.initialize(owner.servers[instance_id])
    assert job["error_code"] == "unsupported_os"
    assert "手动安装 Docker" in job["error_message"]
    assert not any(c.startswith("install-docker") for c in host.calls())


async def test_unreachable_ingress_fails_the_initialization(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    (host.state / "docker_ok").touch()
    (host.state / "ingress_fail").touch()
    async with ssh_host(host) as (port, _):
        instance_id, _ = await owner.prepare(port, initialize=False)
        job = await owner.initialize(owner.servers[instance_id])
    assert job["error_code"] == "ingress_unreachable"
    assert "agent.example.com:9443" in job["error_message"]
    assert {s["key"]: s["status"] for s in job["steps"]}["verify"] == "failed"


async def test_verify_checks_the_public_address_when_the_ingress_name_has_no_dns(
    make_client: ClientFactory, migrated_database_url: str, tmp_path: Path
) -> None:
    """没有域名时入口主机名（agent-gateway.myworkspace.internal）只写在接入包隧道的 hosts 里，
    服务器本机解析不了：初始化最后一步改按 AGENT_PUBLIC_ADDRESS 检查，
    与隧道连的地址一致（T-0183）。"""
    host = FakeHost(tmp_path)
    settings = {**SETTINGS, "agent_public_address": "203.0.113.7"}
    async with make_client(**settings) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        owner = Owner(client, setup.json()["workspaces"][0]["id"], migrated_database_url)
        async with ssh_host(host) as (port, _):
            instance_id, _ = await owner.prepare(port, initialize=False)
            job = await owner.initialize(owner.servers[instance_id])
    assert job["status"] == "succeeded", job
    verify = next(s for s in job["steps"] if s["key"] == "verify")
    assert "203.0.113.7:9443" in (verify["detail"] or "")
    probes = [c for c in host.calls() if c.startswith("timeout")]
    assert probes and all("203.0.113.7" in c and "agent.example.com" not in c for c in probes)


async def test_deploy_needs_docker_usable_now(owner: Owner, tmp_path: Path) -> None:
    """初始化之后 Docker 又不能用了：部署在环境预检（inspect）失败，不自己去装 Docker。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        (host.state / "docker_ok").unlink()
        response = await owner.install(instance_id, version)
        assert response.status_code == 202, response.text
        state = await owner.finished(instance_id)
    assert state["job"]["error_code"] == "docker_unavailable"
    assert _steps(state)["inspect"] == "failed" and _steps(state)["upload"] == "pending"
    assert host.calls().count("install-docker user=deploy") == 1


async def test_plugin_checksum_failure(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    (host.state / "plugin_fail").touch()
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await owner.install(instance_id, version)
        state = await owner.finished(instance_id)
    assert state["job"]["error_code"] == "plugin_sha256_mismatch"
    assert _steps(state)["plugin"] == "failed"
    assert _steps(state)["start"] == "pending"
    assert "docker compose up -d" not in host.calls()


async def test_not_online_reports_logs_redacted(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_ssh_install, "ONLINE_TIMEOUT", 0.3)
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await owner.install(instance_id, version)
        state = await owner.finished(instance_id)
    job = state["job"]
    assert job["error_code"] == "not_online"
    assert _steps(state)["online"] == "failed"
    assert "openclaw   running" in job["log"]
    # 假 docker 的日志里打印了实例凭证：任务日志里替换成 ***
    assert _token(host) not in job["log"]
    assert "token ***" in job["log"]
    assert state["target"]["installed_at"] is None
    deployment = (await owner._get(f"/api/v1/agents/instances/{instance_id}/deployment"))[
        "deployment"
    ]
    assert deployment["runtime_installed"] is None, "失败不写运行证据"


async def test_host_key_changed_is_refused(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
    logins = len(host.logins)
    # 同一个端口换了一把主机公钥（例如被冒充）：不登录、不发送密码
    async with ssh_host(host, port=port):
        await owner.install(instance_id, version)
        state = await owner.finished(instance_id)
    assert state["job"]["error_code"] == "host_key_changed"
    assert len(host.logins) == logins


# ---------------------------------------------------------------- 前提与并发


async def test_preconditions(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port, confirm=False)
        response = await owner.install(instance_id, version)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SSH_HOST_KEY_UNCONFIRMED"

        # 确认了指纹、还没初始化：不能部署（T-0179：初始化是独立的一步）
        instance_id, version = await owner.prepare(port, initialize=False)
        response = await owner.install(instance_id, version)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SERVER_NOT_INITIALIZED"
        assert response.json()["error"]["details"]["reason"] == "not_initialized"
        assert host.logins == []

        instance_id, version = await owner.prepare(port)
        # 没有保存登录密码（T-0178 之前添加的旧服务器）：要先补录密码
        await run_sql(
            owner.database_url, "UPDATE workspace.agent_ssh_targets SET login_password_ref = NULL"
        )
        logins = len(host.logins)
        response = await owner.install(instance_id, version, password=None)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SERVER_CREDENTIAL_REQUIRED"
        # 请求里的 password 已弃用：不会拿它登录
        response = await owner.install(instance_id, version, password=PASSWORD)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SERVER_CREDENTIAL_REQUIRED"
        response = await owner.install(instance_id, version + 5)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "VERSION_CONFLICT"
        response = await owner.install(instance_id, version, extra=1)
        assert response.status_code == 422
    assert len(host.logins) == logins
    jobs = await run_sql(
        owner.database_url, "SELECT count(*) FROM workspace.agent_ssh_jobs WHERE kind = 'install'"
    )
    assert jobs == 0


async def test_one_running_job_per_target(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        # 任务不真正运行：停在 running（本进程认为它在跑），检查进行中时的限制
        monkeypatch.setattr(SshJobRunner, "start", lambda self, *a, **k: None)
        monkeypatch.setattr(SshJobRunner, "running", lambda self, job_id: True)
        assert (await owner.install(instance_id, version)).status_code == 202
        again = await owner.install(instance_id, version)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        response = await owner.remove(instance_id, version)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        response = await owner.replace(instance_id, port=port, username="other", version=version)
        assert response.status_code == 409
        # T-0178：这个实例的部署记录已经建好，更换地址先被“有部署记录”拦下（不改目标）
        assert response.json()["error"]["code"] == "AGENT_SERVER_HAS_DEPLOYMENTS"

    # 工作台重启：还是 running 的任务标为中断
    runner = SshJobRunner(owner.app.state.sessionmaker, owner.app.state.settings)
    assert await runner.recover() == 1
    state = await owner.state(instance_id)
    assert state["job"]["status"] == "interrupted"
    assert state["job"]["finished_at"] is not None


async def test_install_during_a_replacement_blocks_the_replacement(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """更换服务器的探测进行中，另一个请求对原目标开始了安装：更换必须失败（409），
    不能在安装进行中改写目标（否则任务会把密码发给新服务器）。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
    monkeypatch.setattr(SshJobRunner, "start", lambda self, *a, **k: None)
    monkeypatch.setattr(SshJobRunner, "running", lambda self, job_id: True)
    other = asyncssh.generate_private_key("ssh-ed25519")
    installs: list[int] = []

    async def probe_while_installing(address: str, port: int) -> ssh.HostKey:
        installs.append((await owner.install(instance_id, version)).status_code)
        return ssh.HostKey(
            algorithm="ssh-ed25519",
            public_key=" ".join(other.export_public_key().decode().split()[:2]),
            fingerprint=other.get_fingerprint("sha256"),
        )

    monkeypatch.setattr(ssh, "probe_host_key", probe_while_installing)
    replaced = await owner.replace(instance_id, port=port + 1, version=version)
    assert installs == [202]
    assert replaced.status_code == 409, replaced.text
    # 探测之后锁行重新检查：安装已经建了部署记录（T-0178 起先报这个，任务也在进行）
    assert replaced.json()["error"]["code"] == "AGENT_SERVER_HAS_DEPLOYMENTS"
    target = (await owner.state(instance_id))["target"]
    assert target["port"] == port
    assert target["host_key_confirmed"] is True


async def test_job_refuses_a_target_that_changed(owner: Owner, tmp_path: Path) -> None:
    """任务开始前目标服务器被改成未确认（例如绕过接口的改动）：任务不登录、不发送密码。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        logins = len(host.logins)
        runner = owner.app.state.ssh_jobs
        original = runner.start

        def start_after_unconfirm(job_id: uuid.UUID, *args: Any, **kwargs: Any) -> None:
            async def later() -> None:
                await run_sql(
                    owner.database_url,
                    "UPDATE workspace.agent_ssh_targets SET host_key_confirmed_at = NULL",
                )
                original(job_id, *args, **kwargs)

            runner.reserve(job_id)
            asyncio.get_running_loop().create_task(later())

        runner.start = start_after_unconfirm
        try:
            assert (await owner.install(instance_id, version)).status_code == 202
            state = await owner.finished(instance_id)
        finally:
            runner.start = original
    assert state["job"]["error_code"] == "host_key_unconfirmed"
    assert len(host.logins) == logins


async def test_password_replaced_after_acceptance_stops_the_job(
    owner: Owner, tmp_path: Path
) -> None:
    """任务接纳之后、登录之前密码被替换（绕过接口的改动）：按接纳时的版本解密失败，不拿新密码登录。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        logins = len(host.logins)
        runner = owner.app.state.ssh_jobs
        original = runner.start

        def start_after_replace(job_id: uuid.UUID, *args: Any, **kwargs: Any) -> None:
            async def later() -> None:
                await run_sql(
                    owner.database_url,
                    "UPDATE workspace.agent_ssh_targets"
                    " SET credential_revision = credential_revision + 1",
                )
                original(job_id, *args, **kwargs)

            runner.reserve(job_id)
            asyncio.get_running_loop().create_task(later())

        runner.start = start_after_replace
        try:
            assert (await owner.install(instance_id, version)).status_code == 202
            state = await owner.finished(instance_id)
        finally:
            runner.start = original
    assert state["job"]["error_code"] == "credential_changed"
    assert _steps(state)["connect"] == "failed"
    assert len(host.logins) == logins


async def test_existing_authorized_keys_are_never_touched(owner: Owner, tmp_path: Path) -> None:
    """服务器上已有的 authorized_keys（最后一行没有换行符）：初始化、部署都不改它。"""
    host = FakeHost(tmp_path)
    existing = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    (host.home / ".ssh").mkdir(mode=0o700)
    (host.home / ".ssh" / "authorized_keys").write_text(existing)  # 没有末尾换行
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await _install_ok(owner, host, instance_id, version)
    assert (host.home / ".ssh" / "authorized_keys").read_text() == existing
    assert "publickey" not in host.logins


async def test_stale_running_job_does_not_block(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """记录是 running、但本进程里没有它的协程（例如协程意外退出）：下一次操作把它标为中断。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        with monkeypatch.context() as patch:
            patch.setattr(SshJobRunner, "start", lambda self, *a, **k: self.release(a[0]))
            assert (await owner.install(instance_id, version)).status_code == 202
        stale = (await owner.state(instance_id))["job"]
        assert stale["status"] == "running"
        state = await _install_ok(owner, host, instance_id, version)
    assert state["job"]["id"] != stale["id"]
    old = await run_sql(
        owner.database_url,
        "SELECT status FROM workspace.agent_ssh_jobs WHERE id = $1",
        uuid.UUID(stale["id"]),
    )
    assert old == "interrupted"


async def test_admin_cannot_install(owner: Owner, make_client: ClientFactory) -> None:
    instance_id = await owner.instance()
    admin = {"email": "admin@example.com", "password": "admin password 123456"}
    user = await add_user(owner.database_url, admin["email"], admin["password"])
    await add_member(owner.database_url, uuid.UUID(owner.workspace), user, "admin")
    async with make_client(**SETTINGS) as other:
        assert (await other.post("/api/v1/auth/login", json=admin)).status_code == 200
        response = await other.post(
            f"/api/v1/agents/instances/{instance_id}/deploy",
            json={"server_id": str(uuid.uuid4()), "version": 1, "password": PASSWORD},
            headers={"X-Workspace-Id": owner.workspace, **csrf(other)},
        )
    assert response.status_code == 403


# ---------------------------------------------------------------- 卸载（B3）与旧的吊销入口


class _Compat:
    """新接口的响应拼回旧的样子（target / job / warning）。"""

    def __init__(self, response: httpx.Response, data: dict[str, Any]) -> None:
        self.status_code = response.status_code
        self.text = response.text
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


async def _post(owner: Owner, instance_id: str, action: str, body: dict[str, Any]) -> _Compat:
    """卸载走实例的 /undeploy，旧的吊销走服务器的 /revoke（T-0179 起 410）。"""
    if action == "uninstall":
        payload = {
            "version": await owner._instance_version_for(instance_id, body["version"]),
            **{k: v for k, v in body.items() if k != "version"},
        }
        response = await owner.client.post(
            f"/api/v1/agents/instances/{instance_id}/undeploy",
            json=payload,
            headers=owner.headers(),
        )
    else:
        response = await owner.client.post(
            f"/api/v1/agents/servers/{owner.servers[instance_id]}/{action}",
            json=body,
            headers=owner.headers(),
        )
    data = response.json()
    if response.status_code < 300:
        data = {
            "job": data.get("job"),
            "warning": data.get("warning"),
            "target": await owner.target(instance_id),
        }
    return _Compat(response, data)


async def test_uninstall_with_the_saved_password_then_remove(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    other_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    (host.home / ".ssh").mkdir(mode=0o700)
    (host.home / ".ssh" / "authorized_keys").write_text(other_key + "\n")
    (host.state / "crontab").write_text("0 1 * * * echo keep-me\n")
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
        # 原有的一行 + 带标记的七行
        assert len((host.state / "crontab").read_text().splitlines()) == 8
        logins = len(host.logins)

        # 卸载，保留目录
        response = await _post(
            owner, instance_id, "uninstall", {"version": state["target"]["version"]}
        )
        assert response.status_code == 202, response.text
        assert response.json()["job"]["kind"] == "uninstall"
        state = await owner.finished(instance_id)
        assert state["job"]["status"] == "succeeded", state["job"]
        assert _steps(state) == {
            "connect": "done",
            "inspect": "done",
            "stop": "done",
            "unschedule": "done",
            "remove_files": "skipped",
        }
        assert host.logins[logins:] == ["password"]
        assert "docker compose down --remove-orphans" in host.calls()
        assert (host.state / "crontab").read_text().splitlines() == ["0 1 * * * echo keep-me"]
        assert host.directory.exists()
        assert state["target"]["installed_at"] is None
        assert state["target"]["install_dir"] == str(host.directory)
        deployment = (await owner._get(f"/api/v1/agents/instances/{instance_id}/deployment"))[
            "deployment"
        ]
        assert deployment["runtime_installed"] is False, "卸载容器、保留目录"
        server = await owner._get(f"/api/v1/agents/servers/{owner.servers[instance_id]}")
        assert server["agents"][0]["deployment_state"] == "retained"

        # 再卸载一次，删除目录（uid 1001：先用镜像里的 chown 收回属主）
        response = await _post(
            owner,
            instance_id,
            "uninstall",
            {"version": state["target"]["version"], "remove_files": True},
        )
        assert response.status_code == 202, response.text
        state = await owner.finished(instance_id)
        assert state["job"]["status"] == "succeeded", state["job"]
        assert _steps(state)["remove_files"] == "done"
        assert not host.directory.exists()
        assert any(
            f"{prov.OPENCLAW_IMAGE} -R 1001:1001 " in c
            for c in host.calls()[-5:]
            if c.startswith("docker run")
        )
        assert state["target"]["install_dir"] is None

        # 旧的吊销入口已退役：410，不登录
        logins = len(host.logins)
        response = await _post(
            owner, instance_id, "revoke", {"version": state["target"]["version"]}
        )
        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "AGENT_SSH_KEY_AUTH_RETIRED"
        assert len(host.logins) == logins
        assert host.authorized() == [other_key]

        # 没有部署记录、没有旧公钥：可以删除服务器
        removed = await owner.remove(instance_id, state["target"]["version"])
        assert removed.status_code == 204, removed.text
    ref = await run_sql(owner.database_url, "SELECT count(*) FROM workspace.agent_ssh_targets")
    assert ref == 0
    actions = await run_sql(
        owner.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.ssh.%' OR action LIKE 'agents.server.%'",
    )
    for action in (
        "agents.ssh.uninstall_started",
        "agents.ssh.uninstall_succeeded",
        "agents.server.removed",
    ):
        assert action in actions, actions
    assert "key_revoked" not in actions


async def test_uninstall_needs_a_deployment_and_revoke_is_retired(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port, initialize=False)
        # 还没部署到这台服务器：没有可卸载的；旧的吊销入口：410
        response = await _post(owner, instance_id, "uninstall", {"version": version})
        assert response.status_code == 404, response.text
        response = await _post(owner, instance_id, "revoke", {"version": version})
        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "AGENT_SSH_KEY_AUTH_RETIRED"
        response = await _post(owner, instance_id, "revoke", {"version": version, "force": True})
        assert response.status_code == 410, response.text
    assert host.logins == []


async def test_uninstall_waits_for_a_running_job(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
        version = state["target"]["version"]
        monkeypatch.setattr(SshJobRunner, "start", lambda self, *a, **k: None)
        monkeypatch.setattr(SshJobRunner, "running", lambda self, job_id: True)
        assert (
            await _post(owner, instance_id, "uninstall", {"version": version})
        ).status_code == 202
        response = await _post(owner, instance_id, "uninstall", {"version": version})
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        busy = await owner.install(instance_id, version, password=None)
        assert busy.status_code == 409
    assert host.authorized() == []


# ---------------------------------------------------------------- 单元


def test_recorder_redacts_password_tokens_and_private_keys() -> None:
    recorder = JobRecorder(None, None, [PASSWORD])  # type: ignore[arg-type]
    key = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()
    recorder.write(f"pw {PASSWORD} token mwa_abcDEF123_-x key\n{key}tail")
    assert PASSWORD not in recorder.log
    assert "mwa_abcDEF123" not in recorder.log
    assert "PRIVATE KEY" not in recorder.log
    assert recorder.log.endswith("tail\n")
    recorder.forget(PASSWORD)
    recorder.write(PASSWORD)
    assert recorder.log.endswith(PASSWORD + "\n")
    recorder.write("x" * (agent_ssh_install.LOG_LIMIT + 10))
    assert len(recorder.log) == agent_ssh_install.LOG_LIMIT


def test_parse_inspect_rejects_bad_home() -> None:
    with pytest.raises(agent_ssh_install.InstallFailed):
        agent_ssh_install.parse_inspect("uid=1\ngid=1\nhome=relative\n")
    info = agent_ssh_install.parse_inspect("uid=0\ngid=0\nhome=/\nos_id=debian\nos_version=12\n")
    assert info.home == ""
    assert info.root


INSTANCE = "d06d9d48-1234-4abc-8def-0123456789ab"
OTHER = "0f0f0f0f-aaaa-4bbb-8ccc-0123456789ab"


def test_remove_dir_only_accepts_an_instance_directory() -> None:
    for bad in (
        "/home/deploy",
        "/root/myworkspace-openclaw",
        "/root/myworkspace-openclaw/../x",
        "/root/myworkspace-openclaw/d06d9d481234/state",
    ):
        with pytest.raises(ValueError):
            prov.remove_dir_command(bad)
    directory = prov.install_dir("/root", INSTANCE)
    assert directory == "/root/myworkspace-openclaw/d06d9d481234"
    assert prov.remove_dir_command(directory) == (
        "rm -rf -- /root/myworkspace-openclaw/d06d9d481234"
        " && { rmdir -- /root/myworkspace-openclaw 2>/dev/null || true; }"
    )


def test_commands_quote_paths() -> None:
    directory = prov.install_dir("/home/a b/$(x)", INSTANCE)
    quoted = "'/home/a b/$(x)/myworkspace-openclaw/d06d9d481234'"
    assert quoted in prov.compose_command(directory, "up", "-d")
    assert prov.reclaim_command(directory, uid=0, gid=0) is None
    assert prov.handover_command(directory, uid=prov.OPENCLAW_UID) is None
    for line in prov.cron_lines(directory, INSTANCE):
        assert quoted in line and line.endswith(" # mw-openclaw:d06d9d481234")
    assert (
        prov.staging_dir("/home/deploy/", INSTANCE)
        == "/home/deploy/.myworkspace-install-d06d9d481234"
    )


def test_cron_only_touches_own_lines(tmp_path: Path) -> None:
    """同一台服务器上的多个实例：重新部署、卸载只改行尾正好是自己标记的行，别的实例和用户自己的行保留。"""
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    (bin_dir / "crontab").write_text(_CRONTAB)
    (bin_dir / "crontab").chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "STATE": str(state)}
    mine = prov.install_dir("/home/deploy", INSTANCE)
    other = prov.install_dir("/home/deploy", OTHER)
    user_line = "5 * * * * echo '# mw-openclaw:d06d9d481234 not at the end'"

    def sh(command: str) -> None:
        subprocess.run(["/bin/sh", "-c", command], env=env, check=True)  # noqa: S603

    (state / "crontab").write_text(user_line + "\n")
    sh(prov.schedule_command(other, OTHER))
    sh(prov.schedule_command(mine, INSTANCE))
    sh(prov.schedule_command(mine, INSTANCE))  # 重新部署：不重复
    lines = (state / "crontab").read_text().splitlines()
    assert lines[0] == user_line and len(lines) == 15
    assert sum(line.endswith("# mw-openclaw:d06d9d481234") for line in lines) == 7
    assert sum(line.endswith("# mw-openclaw:0f0f0f0faaaa") for line in lines) == 7
    sh(prov.unschedule_command(INSTANCE))
    lines = (state / "crontab").read_text().splitlines()
    assert lines[0] == user_line and len(lines) == 8
    assert all("d06d9d481234" not in line for line in lines[1:])


def test_weekly_restart_is_staggered() -> None:
    first, second = prov.restart_schedule(INSTANCE), prov.restart_schedule(OTHER)
    for value in (first, second):
        minute, hour, *rest = value.split()
        assert 0 <= int(minute) < 60 and 3 <= int(hour) <= 5 and rest == ["*", "*", "1"]
    assert first != second


def test_pins_match_bundle_and_docs() -> None:
    compose = (ROOT / "services/api/app/agent_bundle/files/compose.yaml").read_text()
    assert f"image: {prov.OPENCLAW_IMAGE}" in compose
    for path in ("docs/versions.md", "services/api/app/agent_bundle/files/CHECKLIST.md"):
        assert prov.PLUGIN_SHA256 in (ROOT / path).read_text(), path
    assert "@PACKAGE@" not in prov.plugin_script()
    assert "@SHA256@" not in prov.plugin_script()


def test_revoke_command_matches_key_fields_not_spacing(tmp_path: Path) -> None:
    """authorized_keys 被重新排版（制表符、多个空格、行首带选项）时也要删掉专用公钥，其他行保留。"""
    ours = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().split()
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    kind, data = ours[0], ours[1]
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    keys = ssh_dir / "authorized_keys"
    keys.write_text(
        f"{kind}\t{data} tab-separated\n"
        f"   {kind}    {data}   many-spaces\n"
        f'command="echo hi",no-pty {kind} {data} with-options\n'
        f"{other}\n"
        f"{kind} {data}",  # 最后一行没有换行
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 - 命令由工作台自己生成
        ["/bin/sh", "-c", prov.revoke_key_command(f"{kind} {data} myworkspace-agent")],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert keys.read_text(encoding="utf-8").splitlines() == [other]
    assert stat.S_IMODE(keys.stat().st_mode) == 0o600
