"""SSH 自动部署 B2（T-0089）：安装任务。

目标服务器是进程内的 asyncssh 服务器（127.0.0.1 随机端口，密码与公钥登录、SFTP）。
它把收到的命令交给本机的 /bin/sh 在临时目录里真正执行，PATH 里是假的 docker、sudo、bash、
chown、crontab（记录参数、模拟结果），所以 authorized_keys、crontab、上传的文件都是真实写出来的；
只有只读检查（`sh -s` 读入 inspect.sh）由测试直接给出结果，方便模拟不同的服务器。
"""

import asyncio
import hashlib
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
        }.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.password = PASSWORD
        self.inspect = inspect
        self.logins: list[str] = []
        self.commands: list[str] = []
        self.stdin: list[bytes] = []

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
        return self.home / prov.INSTALL_DIR_NAME

    def authorized(self) -> list[str]:
        path = self.home / ".ssh" / "authorized_keys"
        return path.read_text().splitlines() if path.exists() else []

    async def handle(self, process: asyncssh.SSHServerProcess[bytes]) -> None:
        command = process.command or ""
        self.commands.append(command)
        data = await process.stdin.read()
        self.stdin.append(data)
        if command == "sh -s":
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

    async def state(self, instance_id: str) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/v1/agents/instances/{instance_id}/ssh", headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def prepare(self, port: int, *, confirm: bool = True) -> tuple[str, int]:
        instance_id = await self.instance()
        response = await self.client.put(
            f"/api/v1/agents/instances/{instance_id}/ssh",
            json={"host": "127.0.0.1", "port": port, "username": "deploy"},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text
        target = response.json()["target"]
        if not confirm:
            return instance_id, int(target["version"])
        response = await self.client.post(
            f"/api/v1/agents/instances/{instance_id}/ssh/confirm",
            json={"version": target["version"], "fingerprint": target["host_key_fingerprint"]},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text
        return instance_id, int(response.json()["target"]["version"])

    async def install(
        self, instance_id: str, version: int, password: str | None = PASSWORD
    ) -> httpx.Response:
        body: dict[str, Any] = {"version": version}
        if password is not None:
            body["password"] = password
        return await self.client.post(
            f"/api/v1/agents/instances/{instance_id}/ssh/install",
            json=body,
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


# ---------------------------------------------------------------- 完整安装


async def test_install_with_password_then_dedicated_key(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)

    assert _steps(state) == {key: "done" for key in prov.STEPS}
    target = state["target"]
    assert target["client_key_installed"] is True
    assert target["client_key_fingerprint"].startswith("SHA256:")
    assert target["install_dir"] == str(host.directory)
    assert target["installed_at"] is not None

    # 密码只用于第一次登录；之后（重新登录）都用专用密钥
    assert host.logins[0] == "password"
    assert "publickey" in host.logins
    assert host.logins.index("publickey") > 0
    assert "password" not in host.logins[1:]
    # authorized_keys 里只有工作台的专用公钥一行，与保存的指纹一致
    keys = host.authorized()
    assert len(keys) == 1
    assert (
        asyncssh.import_public_key(keys[0]).get_fingerprint("sha256")
        == (target["client_key_fingerprint"])
    )
    assert stat.S_IMODE((host.home / ".ssh" / "authorized_keys").stat().st_mode) == 0o600

    calls = host.calls()
    # 先检查 sudo（密码从标准输入），再用 root 运行 Docker 安装脚本，把登录账号加入 docker 组
    assert "sudo true" in calls
    assert "install-docker user=deploy" in calls
    assert (host.state / "install-docker.sh").read_text() == prov.script("install-docker.sh")
    assert not (host.home / prov.STAGING_DIR_NAME).exists(), "安装脚本用完要删除"
    # uid 既不是 0 也不是 1000：用锁定镜像里的 chown 把 state/、bin/、tls/ 交给 uid 1000
    handover = [c for c in calls if c.startswith("docker run") and "1000:1000" in c]
    assert len(handover) == 1
    assert prov.OPENCLAW_IMAGE in handover[0]
    assert "--network none --user 0 --entrypoint chown" in handover[0]
    assert "/target/state /target/bin /target/tls" in handover[0]
    assert "docker compose pull --quiet" in calls
    assert "docker compose run --rm --no-deps -T --entrypoint sh openclaw -s" in calls
    assert "docker compose up -d" in calls
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
    assert "tunnel:" in (host.directory / "compose.yaml").read_text()

    # crontab：六行都带标记（每 10 分钟取密钥、每分钟上报用量与服务器状态、
    # 每分钟领取唤醒（T-0121）与终止令（T-0126）、每周一重启）
    cron = (host.state / "crontab").read_text().splitlines()
    assert len(cron) == 6
    assert all(line.endswith(prov.CRON_MARKER) for line in cron)
    assert "secrets reload --json" in cron[0]
    assert cron[1].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/usage-report.mjs" in cron[1]
    assert cron[2].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/host-report.mjs" in cron[2]
    assert cron[3].startswith("* * * * * ")
    assert "exec -T openclaw node /opt/myworkspace/bin/wakeup.mjs" in cron[3]
    assert "exec -T openclaw node /opt/myworkspace/bin/stop-runs.mjs" in cron[4]
    assert "docker compose restart openclaw" in cron[5]

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
        "agents.ssh.install_started",
        "agents.ssh.key_authorized",
        "agents.bundle.issued",
        "agents.ssh.install_succeeded",
    ):
        assert action in actions
    ref = await run_sql(
        owner.database_url, "SELECT client_key_ref FROM workspace.agent_ssh_targets"
    )
    assert ref.startswith("enc:v1:")


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


async def test_reinstall_without_password_reuses_key(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        first = await _install_ok(owner, host, instance_id, version)
        fingerprint = first["target"]["client_key_fingerprint"]
        (host.state / "up").unlink()
        logins = len(host.logins)
        second = await _install_ok(
            owner, host, instance_id, first["target"]["version"], password=None
        )

    steps = _steps(second)
    assert steps["authorize_key"] == "skipped"
    assert steps["docker"] == "skipped"
    # 已经装了锁定版本的插件：不再重装（OpenClaw 运行过后带 --force 重装会被拒绝）
    assert steps["plugin"] == "skipped"
    assert second["target"]["client_key_fingerprint"] == fingerprint
    assert "password" not in host.logins[logins:]
    assert len(host.authorized()) == 1
    # 重装前先把 state/、bin/、tls/ 收回给登录账号（1001），装完再交给 1000
    calls = host.calls()
    runs = [c for c in calls if c.startswith("docker run")]
    reclaim = [i for i, c in enumerate(runs) if f"{prov.OPENCLAW_IMAGE} -R 1001:1001 " in c]
    handover = [i for i, c in enumerate(runs) if f"{prov.OPENCLAW_IMAGE} -R 1000:1000 " in c]
    assert len(reclaim) == 1
    assert len(handover) == 2
    assert handover[0] < reclaim[0] < handover[1]
    cron = (host.state / "crontab").read_text().splitlines()
    assert len(cron) == len(prov.cron_lines(str(host.directory))), "重装时替换带标记的行，不重复"
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
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
    steps = _steps(state)
    assert steps["docker"] == "skipped"
    assert steps["schedule"] == "skipped"
    calls = host.calls()
    assert not any(c.startswith(("sudo", "install-docker", "chown")) for c in calls)
    # uid 1000：不用改属主
    assert not any(c.startswith("docker run") for c in calls)


# ---------------------------------------------------------------- 失败


async def test_wrong_password_fails_without_key(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        response = await owner.install(instance_id, version, password="wrong password 1")  # noqa: S106
        assert response.status_code == 202
        state = await owner.finished(instance_id)
    job = state["job"]
    assert job["status"] == "failed"
    assert job["error_code"] == "auth_failed"
    assert _steps(state)["connect"] == "failed"
    assert state["target"]["client_key_installed"] is False
    assert host.authorized() == []
    assert await _stored_anywhere(owner, "wrong password 1") == 0


async def test_sudo_denied(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    (host.state / "sudo_password").write_text("another password")
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        response = await owner.install(instance_id, version)
        assert response.status_code == 202
        state = await owner.finished(instance_id)
    assert state["job"]["error_code"] == "sudo_denied"
    assert _steps(state)["docker"] == "failed"
    # 专用公钥在装 Docker 之前就装好了：修好 sudo 之后可以重新部署
    assert state["target"]["client_key_installed"] is True


async def test_unsupported_os_without_docker(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path, os_id="centos", os_version="9")
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        await owner.install(instance_id, version)
        state = await owner.finished(instance_id)
    assert state["job"]["error_code"] == "unsupported_os"
    assert "手动安装 Docker" in state["job"]["error_message"]
    assert not any(c.startswith("install-docker") for c in host.calls())


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


async def test_host_key_changed_is_refused(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
    # 同一个端口换了一把主机公钥（例如被冒充）：不登录、不发送密码
    async with ssh_host(host, port=port):
        await owner.install(instance_id, version)
        state = await owner.finished(instance_id)
    assert state["job"]["error_code"] == "host_key_changed"
    assert host.logins == []


# ---------------------------------------------------------------- 前提与并发


async def test_preconditions(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port, confirm=False)
        response = await owner.install(instance_id, version)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SSH_HOST_KEY_UNCONFIRMED"

        instance_id, version = await owner.prepare(port)
        response = await owner.install(instance_id, version, password=None)
        assert response.status_code == 422
        # 与全项目的输入错误同一个错误码（T-0090 的网页按它提示）
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"
        response = await owner.install(instance_id, version + 5)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "VERSION_CONFLICT"
        response = await owner.client.post(
            f"/api/v1/agents/instances/{instance_id}/ssh/install",
            json={"version": version, "password": PASSWORD, "extra": 1},
            headers=owner.headers(),
        )
        assert response.status_code == 422
    assert host.logins == []


async def test_one_running_job_per_target(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 任务不真正运行：停在 running（本进程认为它在跑），检查进行中时的限制
    monkeypatch.setattr(SshJobRunner, "start", lambda self, *a, **k: None)
    monkeypatch.setattr(SshJobRunner, "running", lambda self, job_id: True)
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        assert (await owner.install(instance_id, version)).status_code == 202
        again = await owner.install(instance_id, version)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        response = await owner.client.delete(
            f"/api/v1/agents/instances/{instance_id}/ssh",
            params={"version": version},
            headers=owner.headers(),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        response = await owner.client.put(
            f"/api/v1/agents/instances/{instance_id}/ssh",
            json={"host": "127.0.0.1", "port": port, "username": "other", "version": version},
            headers=owner.headers(),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"

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
    monkeypatch.setattr(SshJobRunner, "start", lambda self, *a, **k: None)
    monkeypatch.setattr(SshJobRunner, "running", lambda self, job_id: True)
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
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
    replaced = await owner.client.put(
        f"/api/v1/agents/instances/{instance_id}/ssh",
        json={"host": "127.0.0.1", "port": port + 1, "username": "deploy", "version": version},
        headers=owner.headers(),
    )
    assert installs == [202]
    assert replaced.status_code == 409, replaced.text
    assert replaced.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
    target = (await owner.state(instance_id))["target"]
    assert target["port"] == port
    assert target["host_key_confirmed"] is True


async def test_job_refuses_a_target_that_changed(owner: Owner, tmp_path: Path) -> None:
    """任务开始前目标服务器被改成未确认（例如绕过接口的改动）：任务不登录、不发送密码。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
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
    assert host.logins == []


async def test_authorized_keys_without_trailing_newline(owner: Owner, tmp_path: Path) -> None:
    """已有的 authorized_keys 最后一行没有换行符：专用公钥要另起一行，原来的公钥不受影响。"""
    host = FakeHost(tmp_path)
    existing = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    (host.home / ".ssh").mkdir(mode=0o700)
    (host.home / ".ssh" / "authorized_keys").write_text(existing)  # 没有末尾换行
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
    lines = host.authorized()
    assert len(lines) == 2
    assert lines[0] == existing
    assert (
        asyncssh.import_public_key(lines[1]).get_fingerprint("sha256")
        == (state["target"]["client_key_fingerprint"])
    )


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
            f"/api/v1/agents/instances/{instance_id}/ssh/install",
            json={"version": 1, "password": PASSWORD},
            headers={"X-Workspace-Id": owner.workspace, **csrf(other)},
        )
    assert response.status_code == 403


# ---------------------------------------------------------------- 卸载与吊销（B3）


async def _post(
    owner: Owner, instance_id: str, action: str, body: dict[str, Any]
) -> httpx.Response:
    return await owner.client.post(
        f"/api/v1/agents/instances/{instance_id}/ssh/{action}", json=body, headers=owner.headers()
    )


async def test_uninstall_then_revoke_then_remove(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    other_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    (host.home / ".ssh").mkdir(mode=0o700)
    (host.home / ".ssh" / "authorized_keys").write_text(other_key + "\n")
    (host.state / "crontab").write_text("0 1 * * * echo keep-me\n")
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
        # 原有的一行 + 带标记的六行
        assert len((host.state / "crontab").read_text().splitlines()) == 7

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
        assert "docker compose down --remove-orphans" in host.calls()
        assert (host.state / "crontab").read_text().splitlines() == ["0 1 * * * echo keep-me"]
        assert host.directory.exists()
        assert state["target"]["installed_at"] is None
        assert state["target"]["install_dir"] == str(host.directory)
        assert state["target"]["client_key_installed"] is True

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

        # 吊销：服务器上只删掉专用公钥这一行
        fingerprint = state["target"]["client_key_fingerprint"]
        response = await _post(
            owner, instance_id, "revoke", {"version": state["target"]["version"]}
        )
        assert response.status_code == 200, response.text
        revoked = response.json()
        assert revoked["warning"] is None
        assert revoked["target"]["client_key_installed"] is False
        assert revoked["target"]["client_key_fingerprint"] is None
        assert host.authorized() == [other_key]
        assert stat.S_IMODE((host.home / ".ssh" / "authorized_keys").stat().st_mode) == 0o600

        # 吊销之后可以删除目标服务器
        response = await owner.client.delete(
            f"/api/v1/agents/instances/{instance_id}/ssh",
            params={"version": revoked["target"]["version"]},
            headers=owner.headers(),
        )
        assert response.status_code == 204, response.text
    ref = await run_sql(owner.database_url, "SELECT count(*) FROM workspace.agent_ssh_targets")
    assert ref == 0
    actions = await run_sql(
        owner.database_url,
        "SELECT string_agg(action || ':'"
        " || coalesce(metadata_redacted_json->>'remote_removed', ''), ',' ORDER BY created_at)"
        " FROM workspace.audit_logs WHERE action LIKE 'agents.ssh.%'",
    )
    for action in (
        "agents.ssh.uninstall_started:",
        "agents.ssh.uninstall_succeeded:",
        "agents.ssh.key_revoked:true",
        "agents.ssh.target_removed:",
    ):
        assert action in actions, actions
    assert fingerprint


async def test_uninstall_and_revoke_need_the_key(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        for action in ("uninstall", "revoke"):
            response = await _post(owner, instance_id, action, {"version": version})
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "AGENT_SSH_KEY_MISSING"
    assert host.logins == []


async def test_revoke_when_server_is_gone(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await _install_ok(owner, host, instance_id, version)
    version = state["target"]["version"]
    # 服务器已经关了：不强制时拒绝，并保留专用私钥
    response = await _post(owner, instance_id, "revoke", {"version": version})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "AGENT_SSH_UNREACHABLE"
    assert (await owner.state(instance_id))["target"]["client_key_installed"] is True
    # 强制：只删除工作台保存的私钥，提示手动删除服务器上的那一行
    response = await _post(owner, instance_id, "revoke", {"version": version, "force": True})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["target"]["client_key_installed"] is False
    assert host.authorized()[0] in data["warning"]
    remote = await run_sql(
        owner.database_url,
        "SELECT metadata_redacted_json->>'remote_removed' FROM workspace.audit_logs"
        " WHERE action = 'agents.ssh.key_revoked'",
    )
    assert remote == "false"
    ref = await run_sql(
        owner.database_url, "SELECT client_key_ref FROM workspace.agent_ssh_targets"
    )
    assert ref is None


async def test_revoke_and_uninstall_wait_for_a_running_job(
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
        for action in ("revoke", "uninstall"):
            response = await _post(owner, instance_id, action, {"version": version})
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        response = await owner.install(instance_id, version, password=None)
        assert response.status_code == 409
    assert len(host.authorized()) == 1


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


def test_remove_dir_only_accepts_the_bundle_directory() -> None:
    with pytest.raises(ValueError):
        prov.remove_dir_command("/home/deploy")
    assert prov.remove_dir_command("/root/" + prov.INSTALL_DIR_NAME) == (
        "rm -rf -- /root/myworkspace-openclaw"
    )


def test_commands_quote_paths() -> None:
    directory = "/home/a b/$(x)/" + prov.INSTALL_DIR_NAME
    assert "'/home/a b/$(x)/myworkspace-openclaw'" in prov.compose_command(directory, "up", "-d")
    assert prov.reclaim_command(directory, uid=0, gid=0) is None
    assert prov.handover_command(directory, uid=prov.OPENCLAW_UID) is None
    for line in prov.cron_lines(directory):
        assert "'/home/a b/$(x)/myworkspace-openclaw'" in line


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
