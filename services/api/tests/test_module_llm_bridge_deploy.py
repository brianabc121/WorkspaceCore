"""页面上一键部署 MCP 管理器（T-0234，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md）。

和 test_agent_ssh_install.py 用同一台“服务器”：本机起的真实 SSH 服务（asyncssh），里面的 docker
是记录调用的桩程序；工作台走真实的登录、SFTP 上传和命令执行。部署出来的管理器由进程内的
services/llm-bridge 应用代替（工作台连它、写入控制的平台、登记）。

这里的 SSH 服务按 OpenSSH 的规则处理 authorized_keys 里带限制的那一行（在真实的 OpenSSH 9.6 上
核对过，见 docs/decisions/2026-11-14-mcp-manager-deploy-verification.md）：带固定命令的密钥执行
不了命令，只能转发到 permitopen 写的那个端口。
"""

import asyncio
import contextlib
import importlib
import re
import socket
import stat
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import pytest

from app.domain.credentials import CredentialBox
from app.domain.runner_ssh import runner_ssh_context
from app.integrations import ssh
from app.models import RunnerHost, RunnerSshTarget
from app.modules.llm_bridge import deploy, tunnel
from app.modules.llm_bridge import deploy_bundle as bundle
from tests.helpers import add_member, add_user, csrf, run_sql
from tests.test_agent_ssh_install import (  # noqa: F401 - owner 是夹具
    PASSWORD,
    FakeHost,
    Owner,
    _Server,
    owner,
)

pytestmark = pytest.mark.integration
# PyYAML 是锁定的依赖（没有类型桩，所以不直接 import）：用它核对生成的 compose 文件
yaml = importlib.import_module("yaml")
BRIDGE_SOURCE = Path(__file__).resolve().parents[2] / "llm-bridge"
INFRA_COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "llm-bridge" / "compose.yaml"
TOKEN = "a1" * 32
API = "/api/v1/llm_bridge"
SLOT = "abcdef012345"

# 在原来的 docker 桩前面加上部署管理器用到的几条：构建、启动、停止、重启、日志，
# 以及“和工作台是不是同一台机器”。docker_needs_sudo：登录账号不在 docker 组（容器服务器）
_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
if [ -f "$STATE/docker_needs_sudo" ]; then
  if [ "${MW_AS_ROOT:-}" != 1 ]; then
    if [ "$1 ${2:-}" = "compose version" ]; then echo 5.6.0; exit 0; fi
    echo "permission denied while trying to connect to the docker API" >&2; exit 1
  fi
  if [ "$1" = info ]; then echo 29.9.1; exit 0; fi
fi
case "$1" in
  ps) if [ -f "$STATE/same_host" ]; then echo c0ffee000001; fi; exit 0 ;;
  inspect)
    case "$3" in
      *Config.Hostname*) cat "$STATE/same_host" 2>/dev/null ;;
      *) printf 'my-workspace_backend\nmy-workspace_integrations\n' ;;
    esac
    exit 0 ;;
  compose)
    case "$2" in
      build)
        while [ -f "$STATE/build_wait" ]; do sleep 0.05; done
        if [ -f "$STATE/build_fail" ]; then
          echo "failed to solve: npm ERR! network" >&2; exit 17
        fi
        exit 0 ;;
      up)
        if [ -f "$STATE/up_fail" ]; then echo "container mcp-manager-1 is unhealthy" >&2; exit 1; fi
        touch "$STATE/mcp_up"; exit 0 ;;
      restart) touch "$STATE/mcp_up"; exit 0 ;;
      down) rm -f "$STATE/mcp_up"; exit 0 ;;
      # 真实的容器日志里可能带出环境变量：任务日志里不能出现令牌
      logs) echo "mcp-manager-1 | $(cat bridge.env)"; exit 0 ;;
    esac ;;
esac
exec "$(dirname "$0")/docker-orig" "$@"
"""
# sudo：-n 只在免密时成功；否则从标准输入读一行密码。经它运行的命令算 root
_SUDO = r"""#!/bin/sh
nopass=""
while [ $# -gt 0 ]; do
  case "$1" in -n) nopass=1; shift ;; -S|-k) shift ;; -p) shift 2 ;; *) break ;; esac
done
if [ -n "$nopass" ]; then
  if [ ! -f "$STATE/sudo_nopasswd" ]; then echo "sudo: a password is required" >&2; exit 1; fi
else
  IFS= read -r pw || pw=""
  if [ "$pw" != "$(cat "$STATE/sudo_password")" ]; then
    echo "Sorry, try again." >&2; exit 1
  fi
fi
echo "sudo $1" >> "$FAKE_LOG"
MW_AS_ROOT=1; export MW_AS_ROOT
exec "$@"
"""
_SS = r"""#!/bin/sh
cat "$STATE/listening" 2>/dev/null
exit 0
"""


def _key_line(line: str) -> tuple[str, str] | None:
    """authorized_keys 的一行 →（限制，“类型 公钥”）。"""
    parts = line.split()
    for index, part in enumerate(parts):
        if part.startswith("ssh-") and index + 1 < len(parts):
            return " ".join(parts[:index]), f"{part} {parts[index + 1]}"
    return None


@dataclass
class Gate:
    """SSH 服务这一侧看到的：哪些连接是用带限制的密钥登录的、转发了哪些端口、拒绝了什么。"""

    restricted: set[asyncssh.SSHServerConnection] = field(default_factory=set)
    forwards: list[int] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)


class _McpServer(_Server):
    """按 OpenSSH 的规则处理带限制的密钥。"""

    def __init__(self, host: FakeHost, gate: Gate) -> None:
        super().__init__(host)
        self.gate = gate
        self.conn: asyncssh.SSHServerConnection | None = None
        self.forced = False
        self.permit: int | None = None

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self.conn = conn

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        for line in self.host.authorized():
            parsed = _key_line(line)
            if parsed is None:
                continue
            options, public = parsed
            if asyncssh.import_public_key(public).public_data != key.public_data:
                continue
            self.host.logins.append("publickey")
            if "command=" in options and self.conn is not None:
                self.forced = True
                self.gate.restricted.add(self.conn)
                allowed = re.search(r'permitopen="127\.0\.0\.1:(\d+)"', options)
                self.permit = (
                    int(allowed.group(1)) if allowed and "port-forwarding" in options else None
                )
            return username == "deploy"
        self.host.logins.append("publickey-denied")
        return False

    def connection_requested(
        self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
    ) -> bool:
        closed = (self.host.state / "no_forwarding").exists()
        if closed or (self.forced and (dest_host, dest_port) != ("127.0.0.1", self.permit)):
            # OpenSSH 对不允许的转发回的是 administratively prohibited
            self.gate.denied.append(f"forward {dest_port}")
            raise asyncssh.ChannelOpenError(
                asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "open failed"
            )
        self.gate.forwards.append(dest_port)
        return True


@contextlib.asynccontextmanager
async def mcp_ssh_host(host: FakeHost, gate: Gate) -> AsyncIterator[tuple[int, asyncssh.SSHKey]]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")

    async def handle(process: asyncssh.SSHServerProcess[bytes]) -> None:
        if process.get_extra_info("connection") in gate.restricted:
            # 固定命令 /bin/false：客户端要执行什么都不执行
            gate.denied.append(f"exec {process.command or ''}")
            process.exit(1)
            return
        await host.handle(process)

    server = await asyncssh.create_server(
        lambda: _McpServer(host, gate),
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        process_factory=handle,
        sftp_factory=True,
        encoding=None,
        config=[],
        reuse_address=True,
    )
    try:
        yield server.sockets[0].getsockname()[1], host_key
    finally:
        tunnel.POOL.close()
        server.close()
        await server.wait_closed()


@dataclass
class Rig:
    owner: Owner
    host: FakeHost
    gate: Gate
    kind: str
    target_id: str
    down: list[bool]
    seen: list[httpx.URL]

    def headers(self) -> dict[str, str]:
        return self.owner.headers()

    async def api(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self.owner.client.request(
            method, f"{API}{path}", headers=self.headers(), **kwargs
        )

    async def finished(self, job_id: str) -> dict[str, Any]:
        for _ in range(600):
            response = await self.api("GET", f"/deployments/{job_id}")
            assert response.status_code == 200, response.text
            job: dict[str, Any] = response.json()
            if job["status"] != "running":
                return job
            await asyncio.sleep(0.05)
        raise AssertionError("部署任务 30 秒内没有结束")

    async def deploy(self, name: str = "3 号", **extra: Any) -> httpx.Response:
        body = {
            "target_kind": self.kind,
            "target_id": self.target_id,
            "name": name,
            "platforms": ["claude", "workbuddy"],
            "request_id": f"d-{uuid.uuid4().hex[:8]}",
            **extra,
        }
        return await self.api("POST", "/deployments", json=body)

    async def deployed(self, name: str = "3 号", **extra: Any) -> dict[str, Any]:
        started = await self.deploy(name, **extra)
        assert started.status_code == 202, started.text
        job = await self.finished(started.json()["id"])
        assert job["status"] == "succeeded", job
        return job

    async def act(self, bridge_id: str, action: str, **body: Any) -> dict[str, Any]:
        started = await self.api(
            "POST",
            f"/bridges/{bridge_id}/{action}",
            json={"request_id": f"{action}-{uuid.uuid4().hex[:8]}", **body},
        )
        assert started.status_code == 202, started.text
        return await self.finished(started.json()["id"])

    async def bridges(self) -> list[dict[str, Any]]:
        listed: list[dict[str, Any]] = (await self.api("GET", "/bridges")).json()["bridges"]
        return listed

    def directory(self) -> Path:
        root = self.host.home / bundle.INSTALL_ROOT
        found = sorted(root.iterdir()) if root.is_dir() else []
        assert len(found) == 1, found
        return found[0]

    def docker_calls(self) -> list[str]:
        return [line for line in self.host.calls() if line.startswith("docker compose")]

    def tunnel_lines(self) -> list[str]:
        return [line for line in self.host.authorized() if "myworkspace-mcp-" in line]


@contextlib.asynccontextmanager
async def _rig(
    owner: Owner,  # noqa: F811 - 夹具
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> AsyncIterator[Rig]:
    monkeypatch.setattr(deploy, "new_token", lambda: TOKEN)
    monkeypatch.setattr(deploy, "VERIFY_POLL", 0.05)
    monkeypatch.setattr(deploy, "workbench_hostname", lambda: "0a1b2c3d4e5f")
    host = FakeHost(tmp_path)
    (host.bin / "docker").rename(host.bin / "docker-orig")
    for name, body in {"docker": _DOCKER, "ss": _SS, "sudo": _SUDO}.items():
        (host.bin / name).write_text(body)
        (host.bin / name).chmod(0o755)
    gate = Gate()
    sys.path.insert(0, str(BRIDGE_SOURCE))
    try:
        bridge_app = importlib.import_module("bridge.app").create_app(
            importlib.import_module("bridge.config").Settings(
                data_dir=tmp_path / "bridge",
                admin_token=TOKEN,
                # 不用这台机器上装着的官方命令行（和它们的登录状态）
                codex_bin="/nonexistent/codex",
                claude_bin="/nonexistent/claude",
                codebuddy_bin="/nonexistent/codebuddy",
            )
        )
        down = [False]
        seen: list[httpx.URL] = []
        asgi = httpx.ASGITransport(app=bridge_app)

        async def route(request: httpx.Request) -> httpx.Response:
            # 部署出来的管理器：只有“容器启动了”之后才连得上
            seen.append(request.url)
            if down[0] or not (host.state / "mcp_up").exists():
                raise httpx.ConnectError("connection refused", request=request)
            response = await asgi.handle_async_request(request)
            return httpx.Response(
                response.status_code, headers=response.headers, content=await response.aread()
            )

        async with (
            contextlib.AsyncExitStack() as stack,
            bridge_app.router.lifespan_context(bridge_app),
        ):
            port, host_key = await stack.enter_async_context(mcp_ssh_host(host, gate))
            owner.app.state.connectors.transport = httpx.MockTransport(route)
            enabled = await owner.client.patch(
                "/api/v1/modules/llm_bridge", json={"enabled": True}, headers=owner.headers()
            )
            assert enabled.status_code == 200, enabled.text
            if kind == deploy.AGENT_SERVER:
                instance_id, _ = await owner.prepare(port)
                target_id = owner.servers[instance_id]
            else:
                (host.state / "docker_needs_sudo").touch()
                target_id = await add_runner_host(owner, host, port, host_key)
            yield Rig(owner, host, gate, kind, target_id, down, seen)
    finally:
        sys.path.remove(str(BRIDGE_SOURCE))
        for name in [m for m in sys.modules if m == "bridge" or m.startswith("bridge.")]:
            del sys.modules[name]


async def add_runner_host(
    owner: Owner,  # noqa: F811 - 夹具
    host: FakeHost,
    port: int,
    host_key: asyncssh.SSHKey,
    *,
    with_ssh: bool = True,
    name: str = "容器一号",
) -> str:
    """登记一台容器服务器：已经用 SSH 部署过（专用密钥装在服务器上、确认过主机公钥）。"""
    box = CredentialBox.from_settings(owner.app.state.settings)
    key = asyncssh.generate_private_key("ssh-ed25519", comment="myworkspace-runner")
    public = key.export_public_key().decode().strip()
    host_id, target_id = uuid.uuid4(), uuid.uuid4()
    async with owner.app.state.sessionmaker() as db:
        db.add(
            RunnerHost(
                id=host_id,
                workspace_id=uuid.UUID(owner.workspace),
                name=name,
                token_hash=uuid.uuid4().hex * 2,
                token_prefix=f"mwr_{uuid.uuid4().hex[:6]}",
            )
        )
        await db.flush()
        if with_ssh:
            db.add(
                RunnerSshTarget(
                    id=target_id,
                    workspace_id=uuid.UUID(owner.workspace),
                    host_id=host_id,
                    host="127.0.0.1",
                    address="127.0.0.1",
                    port=port,
                    username="deploy",
                    host_key_algorithm="ssh-ed25519",
                    host_key=host_key.export_public_key().decode().strip(),
                    host_key_fingerprint=host_key.get_fingerprint(),
                    host_key_confirmed_at=datetime.now(UTC),
                    client_key_ref=box.seal(
                        key.export_private_key().decode(), context=runner_ssh_context(target_id)
                    ),
                    client_public_key=public,
                )
            )
        await db.commit()
    if with_ssh:
        ssh_dir = host.home / ".ssh"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        with (ssh_dir / "authorized_keys").open("a") as handle:
            handle.write(public + "\n")
    return str(host_id)


@pytest.fixture
async def rig(
    owner: Owner,  # noqa: F811 - 夹具
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Rig]:
    """智能体服务器：保存的密码登录，登录账号在 docker 组。"""
    async with _rig(owner, tmp_path, monkeypatch, deploy.AGENT_SERVER) as built:
        yield built


@pytest.fixture
async def runner_rig(
    owner: Owner,  # noqa: F811 - 夹具
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Rig]:
    """容器服务器：专用密钥登录，登录账号不在 docker 组（Docker 要经 sudo）。"""
    async with _rig(owner, tmp_path, monkeypatch, deploy.RUNNER_HOST) as built:
        yield built


def _steps(job: dict[str, Any]) -> list[tuple[str, str]]:
    return [(s["key"], s["status"]) for s in job["steps"]]


def _step(job: dict[str, Any], key: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in job["steps"] if s["key"] == key)
    return found


async def test_deploy_listens_on_loopback_and_is_reached_through_a_tunnel(rig: Rig) -> None:
    """别的服务器：上传、构建、启动，只监听服务器本机地址；工作台经专用的 SSH 通道连上后登记。"""
    body = {
        "target_kind": "agent_server",
        "target_id": rig.target_id,
        "name": "3 号",
        "platforms": ["claude", "workbuddy"],
        "request_id": "d1",
    }
    started = await rig.api("POST", "/deployments", json=body)
    assert started.status_code == 202, started.text
    assert (started.json()["kind"], started.json()["status"]) == ("deploy", "running")
    job = await rig.finished(started.json()["id"])
    assert job["status"] == "succeeded", job
    assert _steps(job) == [(key, "done") for key in deploy.STEPS["deploy"]]
    assert (job["mode"], job["target_kind"], job["target_id"]) == (
        "tunnel",
        "agent_server",
        rig.target_id,
    )

    # 服务器上的文件：构建文件、生成的 compose.yaml、权限 600 的令牌文件
    directory = rig.directory()
    slot = directory.name
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    env = directory / "bridge.env"
    assert env.read_text() == f"BRIDGE_ADMIN_TOKEN={TOKEN}\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    for name in ("Dockerfile", "uv.lock", "pyproject.toml", "bridge/app.py", "bridge/engine.py"):
        assert (directory / "src" / name).is_file(), name
    assert not (directory / "src" / "tests").exists()
    assert not list((directory / "src").rglob("__pycache__"))
    compose = yaml.safe_load((directory / "compose.yaml").read_text())
    service = compose["services"]["mcp-manager"]
    assert compose["name"] == f"mw-mcp-{slot}"
    # 只监听服务器的本机地址：局域网和公网上都没有这个端口
    assert service["ports"] == ["127.0.0.1:18787:8787"] and "networks" not in service
    assert service["image"].startswith("myworkspace/llm-bridge:deploy-")
    assert TOKEN not in (directory / "compose.yaml").read_text()
    assert rig.docker_calls()[-2:] == [
        "docker compose build",
        "docker compose up -d --wait --wait-timeout 240",
    ]
    # 登录账号在 docker 组：不经 sudo
    assert "sudo sh" not in rig.host.calls()

    # 转发密钥：只能转发到这一个端口，不能执行命令
    (line,) = rig.tunnel_lines()
    assert line.startswith(
        'restrict,port-forwarding,permitopen="127.0.0.1:18787",command="/bin/false" ssh-ed25519 '
    )
    assert line.endswith(f" myworkspace-mcp-{slot}")
    assert 18787 in rig.gate.forwards and not rig.gate.denied

    # 登记好了：带着“部署在哪台服务器上”，控制的平台已经写进管理器
    (bridge,) = await rig.bridges()
    assert (bridge["name"], bridge["base_url"], bridge["has_token"]) == (
        "3 号",
        "http://127.0.0.1:18787",
        True,
    )
    assert bridge["deployment"] == {
        "target_kind": "agent_server",
        "target_id": rig.target_id,
        "server_name": bridge["deployment"]["server_name"],
        "mode": "tunnel",
        "port": 18787,
        "needs_sudo": False,
        "outdated": False,
        "deployed_at": bridge["deployment"]["deployed_at"],
    }
    assert job["bridge_id"] == bridge["id"]
    rig.seen.clear()
    status = (await rig.api("GET", f"/bridges/{bridge['id']}/status")).json()
    assert status["reachable"] and status["platforms"] == ["claude", "workbuddy"]
    # 工作台访问的是通道在本进程里的那一头，不是 18787
    assert {url.host for url in rig.seen} == {"127.0.0.1"}
    assert all(url.port not in (None, 18787) for url in rig.seen)

    # 令牌、转发私钥都不在记录里；同一个 request_id 再提交得到的是同一个任务
    stored = await run_sql(
        rig.owner.database_url,
        "SELECT count(*) FROM workspace.llm_bridge_deploy_jobs "
        "WHERE log LIKE $1 OR steps::text LIKE $1",
        f"%{TOKEN}%",
    )
    assert stored == 0 and TOKEN not in job["log"] and "PRIVATE KEY" not in job["log"]
    key_ref = await run_sql(
        rig.owner.database_url, "SELECT tunnel_key_ref FROM workspace.llm_bridge_installs"
    )
    assert key_ref.startswith("enc:v1:")
    again = await rig.api("POST", "/deployments", json=body)
    assert again.status_code == 202 and again.json()["id"] == job["id"]
    listed = (await rig.api("GET", "/deployments")).json()
    assert listed["available"] is True
    assert [j["id"] for j in listed["jobs"]] == [job["id"]] and listed["jobs"][0]["log"] is None
    actions = await run_sql(
        rig.owner.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at, action) FROM workspace.audit_logs "
        "WHERE action LIKE 'llm_bridge.%'",
    )
    for action in ("deploy_started", "deploy_succeeded", "bridge_created"):
        assert f"llm_bridge.{action}" in actions, actions


async def test_deploy_on_the_workbench_host_joins_the_internal_network(rig: Rig) -> None:
    """和工作台同一台机器（服务器上有工作台的后端容器）：加入内部网络，不开端口，也不装转发密钥。"""
    (rig.host.state / "same_host").write_text("0a1b2c3d4e5f\n")
    job = await rig.deployed()
    slot = rig.directory().name
    assert job["mode"] == "same_host"
    compose = yaml.safe_load((rig.directory() / "compose.yaml").read_text())
    service = compose["services"]["mcp-manager"]
    assert "ports" not in service
    assert service["networks"]["workspace-integrations"] == {"aliases": [f"mcp-{slot}"]}
    assert compose["networks"]["workspace-integrations"] == {
        "name": "my-workspace_integrations",
        "external": True,
    }
    assert "同一台服务器" in _step(job, "inspect")["detail"]
    assert "my-workspace_integrations" in _step(job, "inspect")["detail"]
    assert "不需要通道" in _step(job, "tunnel")["detail"] and rig.tunnel_lines() == []
    (bridge,) = await rig.bridges()
    assert bridge["base_url"] == f"http://mcp-{slot}:8787"
    assert (bridge["deployment"]["mode"], bridge["deployment"]["port"]) == ("same_host", None)
    restarted = await rig.act(bridge["id"], "restart")
    assert restarted["status"] == "succeeded", restarted
    assert {url.host for url in rig.seen} == {f"mcp-{slot}"}


async def test_tunnel_can_be_forced_on_the_workbench_host(rig: Rig) -> None:
    (rig.host.state / "same_host").write_text("0a1b2c3d4e5f\n")
    job = await rig.deployed(network="tunnel")
    assert job["mode"] == "tunnel" and len(rig.tunnel_lines()) == 1
    (bridge,) = await rig.bridges()
    assert (bridge["deployment"]["mode"], bridge["deployment"]["port"]) == ("tunnel", 18787)
    # 重新部署沿用通道，不会因为“同一台服务器”改成内部网络
    again = await rig.act(bridge["id"], "redeploy")
    assert (again["status"], again["mode"]) == ("succeeded", "tunnel"), again
    assert (await rig.deploy(network="direct")).status_code == 422


async def test_second_manager_on_the_same_server_gets_the_next_port(rig: Rig) -> None:
    (rig.host.state / "listening").write_text("LISTEN 0 128 0.0.0.0:18787 0.0.0.0:*\n")
    await rig.deployed("3 号")
    (rig.host.state / "listening").write_text("")
    await rig.deployed("4 号")
    await rig.deployed("5 号")
    ports = {b["name"]: b["deployment"]["port"] for b in await rig.bridges()}
    # 18787 当时有别的程序在监听；之后 18787、18788 已经分给前两个
    assert ports == {"3 号": 18788, "4 号": 18787, "5 号": 18789}
    assert len(rig.tunnel_lines()) == 3
    targets = (await rig.api("GET", "/deployments/targets")).json()["targets"]
    assert [t["managers"] for t in targets if t["id"] == rig.target_id] == [3]


async def test_failures_leave_nothing_behind(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    # 构建失败：容器还没起，不需要清理
    (rig.host.state / "build_fail").touch()
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "build_failed")
    assert _step(job, "build")["status"] == "failed"
    assert "npm ERR! network" in job["log"] and await rig.bridges() == []
    assert not any("down" in call for call in rig.docker_calls())
    # 已经上传的目录（里面有令牌文件）不留在服务器上
    assert not (rig.host.home / bundle.INSTALL_ROOT).exists()
    (rig.host.state / "build_fail").unlink()

    # 容器没启动起来：把日志带回来（令牌打码），停掉并删掉
    (rig.host.state / "up_fail").touch()
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "start_failed")
    assert "is unhealthy" in job["log"] and "BRIDGE_ADMIN_TOKEN=***" in job["log"]
    assert TOKEN not in job["log"]
    assert rig.docker_calls()[-1] == "docker compose down -v --remove-orphans"
    assert not (rig.host.home / bundle.INSTALL_ROOT).exists()
    (rig.host.state / "up_fail").unlink()

    # 容器起来了但工作台连不上：同样清理，转发密钥也从服务器上删掉
    monkeypatch.setattr(deploy, "VERIFY_TIMEOUT", 0.3)
    rig.down[0] = True
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "verify_failed")
    assert "连不上" in job["error_message"]
    assert rig.docker_calls()[-1] == "docker compose down -v --remove-orphans"
    assert await rig.bridges() == [] and not (rig.host.state / "mcp_up").exists()
    assert rig.tunnel_lines() == []
    rig.down[0] = False

    # 这台服务器的 sshd 不允许端口转发：马上说明原因，不等到超时
    (rig.host.state / "no_forwarding").touch()
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "forwarding_disabled")
    assert "AllowTcpForwarding" in job["error_message"]
    assert rig.tunnel_lines() == [] and not (rig.host.home / bundle.INSTALL_ROOT).exists()
    (rig.host.state / "no_forwarding").unlink()

    # 修好之后再部署：成功
    assert (await rig.deployed())["status"] == "succeeded"
    failed = await run_sql(
        rig.owner.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'llm_bridge.deploy_failed'",
    )
    installs = await run_sql(
        rig.owner.database_url, "SELECT count(*) FROM workspace.llm_bridge_installs"
    )
    assert (failed, installs) == (4, 1)


async def test_preconditions_and_roles(rig: Rig) -> None:
    env = rig.owner
    # 名字被占用、服务器不存在、没选平台、服务器类型不对
    manual = await rig.api(
        "POST",
        "/bridges",
        json={"name": "手动", "base_url": "http://x.test:8787", "token": "t" * 40},
    )
    assert manual.status_code == 201, manual.text
    taken = await rig.deploy("手动")
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "NAME_TAKEN"
    assert (await rig.deploy(target_id=str(uuid.uuid4()))).status_code == 404
    assert (await rig.deploy(target_kind="runner_host")).status_code == 404
    assert (await rig.deploy(platforms=[])).status_code == 422
    assert (await rig.deploy(target_kind="somewhere")).status_code == 422
    # 手动添加的管理器不能从页面上重新部署、重启、卸载；仍然可以直接删除登记
    for action in ("redeploy", "restart", "uninstall"):
        refused = await rig.api(
            "POST", f"/bridges/{manual.json()['id']}/{action}", json={"request_id": f"x-{action}"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "NOT_DEPLOYED_HERE"

    # 同一台服务器同时只跑一个：第一个卡在构建时第二个被拒绝
    (rig.host.state / "build_wait").touch()
    first = await rig.deploy("3 号")
    assert first.status_code == 202, first.text
    busy = await rig.deploy("4 号")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "DEPLOY_BUSY"
    (rig.host.state / "build_wait").unlink()
    assert (await rig.finished(first.json()["id"]))["status"] == "succeeded"

    # 工作台部署的管理器：地址和令牌由部署管理，不能直接删除登记（要用卸载）
    deployed = next(b for b in await rig.bridges() if b["name"] == "3 号")
    for response in (
        await rig.api("PATCH", f"/bridges/{deployed['id']}", json={"base_url": "http://y:1"}),
        await rig.api("PATCH", f"/bridges/{deployed['id']}", json={"token": "n" * 40}),
        await rig.api("DELETE", f"/bridges/{deployed['id']}"),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "BRIDGE_MANAGED"
    renamed = await rig.api("PATCH", f"/bridges/{deployed['id']}", json={"name": "三号"})
    assert renamed.status_code == 200, renamed.text
    assert (await rig.api("DELETE", f"/bridges/{manual.json()['id']}")).status_code == 204

    # admin 能看服务器、进度和某台服务器上的管理器，不能部署；member 都不行
    password = "member password 123456"  # noqa: S105 - 测试密码
    for email, role, read in (
        ("admin@example.com", "admin", 200),
        ("m@example.com", "member", 403),
    ):
        user_id = await add_user(env.database_url, email, password)
        await add_member(env.database_url, uuid.UUID(env.workspace), user_id, role)
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as other:
            login = await other.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text
            headers = {"X-Workspace-Id": env.workspace, **csrf(other)}
            for path in (
                "/deployments",
                "/deployments/targets",
                f"/installs?target_kind=agent_server&target_id={rig.target_id}",
            ):
                assert (await other.get(f"{API}{path}", headers=headers)).status_code == read, path
            body = {
                "target_kind": "agent_server",
                "target_id": rig.target_id,
                "name": "x",
                "platforms": ["claude"],
                "request_id": f"r-{role}",
            }
            denied = await other.post(f"{API}/deployments", json=body, headers=headers)
            assert denied.status_code == 403, denied.text
            for action in ("redeploy", "restart", "uninstall"):
                denied = await other.post(
                    f"{API}/bridges/{deployed['id']}/{action}",
                    json={"request_id": f"{action}-{role}"},
                    headers=headers,
                )
                assert denied.status_code == 403, denied.text


async def test_redeploy_restart_and_uninstall(rig: Rig) -> None:
    await rig.deployed()
    directory = rig.directory()
    (bridge,) = await rig.bridges()
    (key_line,) = rig.tunnel_lines()
    stale = directory / "src" / "bridge" / "removed_module.py"
    stale.write_text("# 上一个版本留下的文件\n")
    # 构建文件比部署的新：页面提示可以重新部署
    await run_sql(
        rig.owner.database_url,
        "UPDATE workspace.llm_bridge_installs SET source_tag = 'deploy-000000000000'",
    )
    assert (await rig.bridges())[0]["deployment"]["outdated"] is True

    # 重新部署（升级）：同一个目录、同一枚令牌、同一个端口、同一把转发密钥；旧的构建文件不留下
    again = await rig.act(bridge["id"], "redeploy")
    assert (again["kind"], again["status"]) == ("redeploy", "succeeded"), again
    assert [s["key"] for s in again["steps"]] == list(deploy.STEPS["redeploy"])
    assert rig.directory() == directory
    assert (directory / "bridge.env").read_text() == f"BRIDGE_ADMIN_TOKEN={TOKEN}\n"
    assert not stale.exists() and (directory / "src" / "bridge" / "app.py").is_file()
    assert rig.tunnel_lines() == [key_line]
    (current,) = await rig.bridges()
    assert current["id"] == bridge["id"]
    assert (current["deployment"]["outdated"], current["deployment"]["port"]) == (False, 18787)

    # 重启：只重启这一个 compose 项目，再确认连得上
    restarted = await rig.act(bridge["id"], "restart")
    assert (restarted["kind"], restarted["status"]) == ("restart", "succeeded"), restarted
    assert _steps(restarted) == [(key, "done") for key in deploy.STEPS["restart"]]
    assert rig.docker_calls()[-1] == "docker compose restart"

    # 只允许用这个管理器的实例：卸载后一个都不能用
    instance = await rig.owner.instance()
    granted = await rig.api(
        "PUT", f"/grants/{instance}", json={"enabled": True, "bridge_ids": [bridge["id"]]}
    )
    assert granted.status_code == 200, granted.text

    # 卸载：停掉并删除容器（数据卷保留）、删除目录和转发密钥、去掉登记
    gone = await rig.act(bridge["id"], "uninstall")
    assert (gone["kind"], gone["status"]) == ("uninstall", "succeeded"), gone
    assert rig.docker_calls()[-1] == "docker compose down --remove-orphans"
    assert not directory.exists() and not (rig.host.home / bundle.INSTALL_ROOT).exists()
    assert (
        rig.tunnel_lines() == [] and "转发密钥已从服务器上删除" in _step(gone, "remove")["detail"]
    )
    assert await rig.bridges() == []
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert next(g for g in grants if g["instance_id"] == instance)["bridge_ids"] == []
    assert (
        await run_sql(rig.owner.database_url, "SELECT count(*) FROM workspace.llm_bridge_installs")
        == 0
    )

    # 连数据一起删
    await rig.deployed("4 号")
    (bridge,) = await rig.bridges()
    gone = await rig.act(bridge["id"], "uninstall", remove_data=True)
    assert gone["status"] == "succeeded", gone
    assert rig.docker_calls()[-1] == "docker compose down --remove-orphans -v"
    assert "数据卷也已删除" in _step(gone, "stop")["detail"]


async def test_the_manager_survives_changes_on_the_server_page(rig: Rig) -> None:
    """服务器从工作台移除、登录密码失效，都不影响已经部署的管理器：通道用自己的密钥和服务器信息。"""
    await rig.deployed()
    (bridge,) = await rig.bridges()
    # 服务器上的登录密码换了（工作台保存的那个不再能登录），再把服务器从工作台移除
    rig.host.password = "changed on the server 99"  # noqa: S105 - 测试密码
    await run_sql(
        rig.owner.database_url,
        "DELETE FROM workspace.agent_servers WHERE id = $1",
        uuid.UUID(rig.target_id),
    )
    tunnel.POOL.close()  # 像工作台重启过一样：通道要能重新建立
    (current,) = await rig.bridges()
    assert current["deployment"]["target_id"] is None
    assert current["deployment"]["server_name"] == bridge["deployment"]["server_name"]
    status = (await rig.api("GET", f"/bridges/{bridge['id']}/status")).json()
    assert status["reachable"] is True, status

    # 工作台登录不上那台服务器了：不能重新部署、重启、普通卸载；可以只删除登记
    for action in ("redeploy", "restart", "uninstall"):
        refused = await rig.api(
            "POST", f"/bridges/{bridge['id']}/{action}", json={"request_id": f"gone-{action}"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "SERVER_REMOVED"
    forced = await rig.api(
        "POST", f"/bridges/{bridge['id']}/uninstall", json={"request_id": "forget", "force": True}
    )
    assert forced.status_code == 202, forced.text
    job = forced.json()
    assert (job["kind"], job["status"]) == ("uninstall", "succeeded")
    detail = job["steps"][0]["detail"]
    slot = rig.directory().name
    for left in (f"mw-mcp-{slot}", str(rig.directory()), f"myworkspace-mcp-{slot}"):
        assert left in detail, detail
    assert await rig.bridges() == []
    # 没有连服务器：容器、目录、转发密钥都还在
    assert rig.directory().is_dir() and len(rig.tunnel_lines()) == 1
    assert not any("down" in call for call in rig.docker_calls())


async def test_deploy_to_a_runner_host_uses_sudo(runner_rig: Rig) -> None:
    """容器服务器：专用密钥登录；登录账号不在 docker 组，Docker 经 sudo，密码只用这一次。"""
    rig = runner_rig
    # 没给密码：检查一步就停下，说明要填密码；服务器上什么都没留下
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "sudo_password_required")
    assert _step(job, "inspect")["status"] == "failed"
    assert not (rig.host.home / bundle.INSTALL_ROOT).exists() and rig.tunnel_lines() == []
    # 密码不对
    wrong = await rig.deploy(sudo_password="wrong password")  # noqa: S106 - 测试密码
    job = await rig.finished(wrong.json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "sudo_denied")

    job = await rig.deployed(sudo_password=PASSWORD)
    assert (job["mode"], job["target_kind"], job["target_id"]) == (
        "tunnel",
        "runner_host",
        rig.target_id,
    )
    assert "专用密钥" in _step(job, "connect")["detail"]
    assert "sudo" in _step(job, "inspect")["detail"]
    # Docker 的命令都经 sudo 里的 sh 执行；上传和转发密钥用登录账号自己的权限
    assert rig.host.calls().count("sudo sh") >= 4
    assert rig.docker_calls()[-2:] == [
        "docker compose build",
        "docker compose up -d --wait --wait-timeout 240",
    ]
    assert stat.S_IMODE((rig.directory() / "bridge.env").stat().st_mode) == 0o600
    (bridge,) = await rig.bridges()
    assert bridge["deployment"]["target_kind"] == "runner_host"
    assert bridge["deployment"]["needs_sudo"] is True
    # 容器服务器的专用密钥那一行还在，转发密钥是另外一行
    assert len(rig.host.authorized()) == 2 and len(rig.tunnel_lines()) == 1
    status = (await rig.api("GET", f"/bridges/{bridge['id']}/status")).json()
    assert status["reachable"] is True, status

    # 密码不保存、不进日志
    for table in ("llm_bridge_deploy_jobs", "llm_bridge_installs", "audit_logs"):
        found = await run_sql(
            rig.owner.database_url,
            f"SELECT count(*) FROM workspace.{table} t WHERE t::text LIKE $1",  # noqa: S608
            f"%{PASSWORD}%",
        )
        assert found == 0, table
    assert PASSWORD not in job["log"]

    # 之后的操作同样要密码：没给时马上说明
    for action in ("redeploy", "restart", "uninstall"):
        refused = await rig.api(
            "POST", f"/bridges/{bridge['id']}/{action}", json={"request_id": f"np-{action}"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "SUDO_PASSWORD_REQUIRED"
    restarted = await rig.act(bridge["id"], "restart", sudo_password=PASSWORD)
    assert restarted["status"] == "succeeded", restarted
    assert rig.docker_calls()[-1] == "docker compose restart"

    # 在容器服务器页面吊销专用密钥：工作台不能再登录上去管理，但管理器照常能用
    lines = [line for line in rig.host.authorized() if "myworkspace-mcp-" in line]
    (rig.host.home / ".ssh" / "authorized_keys").write_text("\n".join(lines) + "\n")
    tunnel.POOL.close()
    status = (await rig.api("GET", f"/bridges/{bridge['id']}/status")).json()
    assert status["reachable"] is True, status


async def test_passwordless_sudo_needs_no_password(runner_rig: Rig) -> None:
    rig = runner_rig
    (rig.host.state / "sudo_nopasswd").touch()
    job = await rig.deployed()
    assert "免密 sudo" in _step(job, "inspect")["detail"]
    (bridge,) = await rig.bridges()
    assert bridge["deployment"]["needs_sudo"] is False
    gone = await rig.act(bridge["id"], "uninstall")
    assert gone["status"] == "succeeded", gone
    assert await rig.bridges() == [] and rig.tunnel_lines() == []
    # 容器服务器自己的专用密钥不受影响
    assert len(rig.host.authorized()) == 1


async def test_targets_and_managers_on_a_server(runner_rig: Rig) -> None:
    rig = runner_rig
    bare = await add_runner_host(
        rig.owner,
        rig.host,
        1,
        asyncssh.generate_private_key("ssh-ed25519"),
        with_ssh=False,
        name="还没用 SSH 部署",
    )
    listed = (await rig.api("GET", "/deployments/targets")).json()
    assert listed["available"] is True
    targets = {t["id"]: t for t in listed["targets"]}
    assert targets[rig.target_id] == {
        "kind": "runner_host",
        "id": rig.target_id,
        "name": "容器一号",
        "host": "127.0.0.1",
        "deployable": True,
        "block_reason": None,
        "block_message": None,
        "may_need_password": True,
        "managers": 0,
    }
    assert (targets[bare]["deployable"], targets[bare]["block_reason"]) == (False, "not_ssh")
    assert targets[bare]["block_message"]
    refused = await rig.deploy(target_id=bare)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "DEPLOY_BLOCKED"

    path = f"/installs?target_kind=runner_host&target_id={rig.target_id}"
    assert (await rig.api("GET", path)).json() == {"managers": [], "available": True}
    await rig.deployed(sudo_password=PASSWORD)
    (bridge,) = await rig.bridges()
    (manager,) = (await rig.api("GET", path)).json()["managers"]
    assert manager == {
        "bridge_id": bridge["id"],
        "name": "3 号",
        "enabled": True,
        "reachable": True,
        "version": manager["version"],
        "platforms": ["claude", "workbuddy"],
        "logged_in": [],
        "deployment": bridge["deployment"],
    }
    assert manager["version"]
    # 连不上时照样列出来，标成不在线；停用的不探测
    rig.down[0] = True
    (manager,) = (await rig.api("GET", path)).json()["managers"]
    assert manager["reachable"] is False
    off = await rig.api("PATCH", f"/bridges/{bridge['id']}", json={"enabled": False})
    assert off.status_code == 200, off.text
    (manager,) = (await rig.api("GET", path)).json()["managers"]
    assert (manager["enabled"], manager["reachable"]) == (False, None)
    # 别的服务器、别的类型下没有它
    other = f"/installs?target_kind=agent_server&target_id={rig.target_id}"
    assert (await rig.api("GET", other)).json()["managers"] == []
    assert (await rig.api("GET", "/installs?target_kind=x&target_id=1")).status_code == 422


async def test_a_job_left_running_by_a_restart_is_marked_interrupted(rig: Rig) -> None:
    """工作台重启后任务的协程不在了：读到时标为中断，不挡住后面的部署。"""
    job_id = str(uuid.uuid4())
    await run_sql(
        rig.owner.database_url,
        "INSERT INTO workspace.llm_bridge_deploy_jobs (id, workspace_id, kind, status,"
        " target_kind, server_id, server_name, name, slot, request_id) "
        "VALUES ($1, $2, 'deploy', 'running', 'agent_server', $3, '服务器', '旧任务',"
        " 'abcdef012345', 'old')",
        uuid.UUID(job_id),
        uuid.UUID(rig.owner.workspace),
        uuid.UUID(rig.target_id),
    )
    seen = (await rig.api("GET", f"/deployments/{job_id}")).json()
    assert (seen["status"], seen["error_code"]) == ("interrupted", "interrupted")
    assert (await rig.deployed())["status"] == "succeeded"


async def test_unavailable_without_the_build_files(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(bundle.SOURCE_ENV, str(tmp_path / "nowhere"))
    assert (await rig.api("GET", "/deployments")).json()["available"] is False
    assert (await rig.api("GET", "/deployments/targets")).json()["available"] is False
    refused = await rig.deploy()
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "DEPLOY_UNAVAILABLE"


def _free_port() -> int:
    for port in bundle.PORT_RANGE:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise AssertionError("18787 ~ 18886 都被占用了")


async def test_tunnel_carries_real_requests_and_nothing_else(
    owner: Owner,  # noqa: F811 - 夹具
    tmp_path: Path,
) -> None:
    """通道本身：真的把 HTTP 请求转发到服务器本机的那个端口；这把密钥别的什么都做不了。"""
    settings = owner.app.state.settings
    host, gate = FakeHost(tmp_path), Gate()
    port = _free_port()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(asyncio.IncompleteReadError):
            await reader.readuntil(b"\r\n\r\n")
            body = b'{"manager": true}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        writer.close()

    manager = await asyncio.start_server(serve, "127.0.0.1", port)
    private, public = tunnel.new_key(SLOT)
    install_id = uuid.uuid4()
    ssh_dir = host.home / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    (ssh_dir / "authorized_keys").write_text(bundle.tunnel_line(public, port) + "\n")
    try:
        async with mcp_ssh_host(host, gate) as (ssh_port, host_key):
            spec = tunnel.TunnelSpec(
                install_id=install_id,
                address="127.0.0.1",
                ssh_port=ssh_port,
                username="deploy",
                host_key=host_key.export_public_key().decode().strip(),
                key_ref=CredentialBox.from_settings(settings).seal(
                    private, context=tunnel.key_context(install_id)
                ),
                port=port,
            )
            url = await tunnel.POOL.url(settings, spec)
            assert url.startswith("http://127.0.0.1:") and not url.endswith(f":{port}")
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                assert (await client.get(f"{url}/admin/status")).json() == {"manager": True}
            # 复用同一条通道；探测是通的
            assert await tunnel.POOL.url(settings, spec) == url
            assert await tunnel.POOL.probe(settings, spec) is None
            assert host.logins.count("publickey") >= 1 and "password" not in host.logins

            # 这把密钥执行不了命令
            key = asyncssh.import_private_key(private)
            conn = await ssh.connect("127.0.0.1", ssh_port, "deploy", spec.host_key, client_key=key)
            try:
                result = await ssh.run(conn, "touch pwned; echo ran", seconds=10)
                assert not result.ok and result.stdout == ""
                assert not (host.home / "pwned").exists()
                # 也转发不到别的端口
                with pytest.raises(asyncssh.ChannelOpenError) as refused:
                    await conn.open_connection("127.0.0.1", ssh_port)
                assert refused.value.code == asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED
            finally:
                conn.close()
            assert gate.denied == ["exec touch pwned; echo ran", f"forward {ssh_port}"]

            # 密钥挪到别的部署记录上解不开；通道断了下次访问时重建
            with pytest.raises(Exception, match="转发密钥读不出来"):
                await tunnel.POOL.url(
                    settings,
                    tunnel.TunnelSpec(**{**spec.__dict__, "install_id": uuid.uuid4()}),
                )
            tunnel.POOL.drop(install_id)
            again = await tunnel.POOL.url(settings, spec)
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                assert (await client.get(f"{again}/admin/status")).status_code == 200

            # 管理器没在监听 / sshd 不允许转发：探测给出原因
            manager.close()
            await manager.wait_closed()
            assert await tunnel.POOL.probe(settings, spec) == "refused"
            (host.state / "no_forwarding").touch()
            assert await tunnel.POOL.probe(settings, spec) == "prohibited"
    finally:
        manager.close()


def test_network_plan_and_tunnel_key_line() -> None:
    info = bundle.Inspect(
        home="/home/deploy",
        docker=True,
        access=True,
        compose=True,
        sudo=True,
        sudo_nopasswd=False,
        network="",
        port=18787,
    )
    remote = bundle.plan_network(info, slot=SLOT)
    assert (remote.mode, remote.base_url, remote.port) == (
        "tunnel",
        "http://127.0.0.1:18787",
        18787,
    )
    assert remote.service == '    ports:\n      - "127.0.0.1:18787:8787"\n' and remote.top == ""
    local = bundle.Inspect(**{**info.__dict__, "network": "my-workspace_integrations"})
    same = bundle.plan_network(local, slot=SLOT)
    assert (same.mode, same.base_url, same.port) == ("same_host", f"http://mcp-{SLOT}:8787", None)
    with pytest.raises(bundle.BundleError) as full:
        bundle.plan_network(bundle.Inspect(**{**info.__dict__, "port": None}), slot=SLOT)
    assert full.value.code == "no_free_port"

    _, public = tunnel.new_key(SLOT)
    line = bundle.tunnel_line(public, 18790)
    assert line == (
        f'restrict,port-forwarding,permitopen="127.0.0.1:18790",command="/bin/false" {public}'
    )
    assert public.startswith("ssh-ed25519 ") and public.endswith(f" myworkspace-mcp-{SLOT}")
    for bad_key, bad_port in ((public, 22), (public + "\nssh-rsa AAAA x", 18790), ("x", 18790)):
        with pytest.raises(ValueError, match="转发密钥或端口"):
            bundle.tunnel_line(bad_key, bad_port)

    # 登录账号不能直接用 Docker：整条命令交给 sudo 里的 sh，命令本身不变
    command = bundle.compose_command(bundle.install_dir("/home/deploy", SLOT), "build")
    assert bundle.privileged(command, bundle.ACCESS_DIRECT) == command
    assert bundle.privileged(command, bundle.ACCESS_SUDO) == f"sudo -n sh -c '{command}'"
    assert bundle.privileged(command, bundle.ACCESS_SUDO_PASSWORD) == (
        f"sudo -S -p '' -k sh -c '{command}'"
    )


def test_commands_only_touch_the_install_directory() -> None:
    directory = bundle.install_dir("/home/deploy", SLOT)
    assert directory == f"/home/deploy/myworkspace-llm-bridge/{SLOT}"
    assert bundle.prepare_command(directory).endswith(f"rm -rf -- {directory}/src")
    assert bundle.compose_command(directory, "build") == f"cd {directory} && docker compose build"
    assert bundle.remove_dir_command(directory).startswith(f"rm -rf -- {directory} && ")
    for bad in (
        "/home/deploy",
        "/home/deploy/myworkspace-llm-bridge",
        "/home/deploy/myworkspace-llm-bridge/abc",
        f"/home/deploy/myworkspace-llm-bridge/../../etc/{SLOT}",
        f"relative/myworkspace-llm-bridge/{SLOT}",
        f"/home/deploy/myworkspace-llm-bridge/{SLOT}; rm -rf /",
    ):
        with pytest.raises(ValueError, match="部署目录"):
            bundle.remove_dir_command(bad)
    with pytest.raises(ValueError, match="部署编号"):
        bundle.install_dir("/home/deploy", "../x")
    # 检查脚本的参数：主机名不合规时不传；端口只接受数字
    assert bundle.inspect_command("0a1b2c3d4e5f", [18787, 18788]) == (
        "sh -s -- 0a1b2c3d4e5f '18787 18788'"
    )
    assert bundle.inspect_command("x; rm -rf /", []) == "sh -s -- - ''"
    assert bundle.inspect_inline(None, []).endswith(" sh - ''")
    with pytest.raises(bundle.BundleError):
        bundle.parse_inspect("home=relative\n")
    with pytest.raises(bundle.BundleError):
        bundle.parse_inspect("home=/home/deploy\nnetwork=evil; rm_integrations\n")
    with pytest.raises(ValueError, match="令牌"):
        bundle.env_file("short")
    # 部署程序只在自己的目录里用 docker compose：没有针对整机的清理、停止、重启
    source = Path(bundle.__file__).read_text() + Path(deploy.__file__).read_text()
    for forbidden in ("prune", "docker rm", "docker stop", "docker kill", "systemctl"):
        assert forbidden not in source, forbidden


def test_generated_compose_matches_the_manual_one() -> None:
    """页面部署和手动部署是同一个服务：运行时的限制不能比手动部署的宽；所有对象都带保护标签。"""
    manual = yaml.safe_load(INFRA_COMPOSE.read_text())["services"]["mcp-manager"]
    network = bundle.Network("tunnel", "http://127.0.0.1:18787", '    ports:\n      - "x"\n', "")
    files = bundle.source_files(BRIDGE_SOURCE)
    tag = bundle.content_tag(files)
    generated = yaml.safe_load(bundle.compose_file(SLOT, tag, network))
    service = generated["services"]["mcp-manager"]
    for key in ("restart", "cap_drop", "security_opt", "pids_limit", "volumes"):
        assert service[key] == manual[key], key
    assert service["restart"] == "unless-stopped"
    assert "privileged" not in service and "network_mode" not in service
    assert all("docker.sock" not in str(v) for v in service["volumes"])
    # 容器、数据卷、网络都带标签：两个服务器页面的管理程序不处理带它的对象
    label = {bundle.LABEL: SLOT}
    assert bundle.LABEL == "myworkspace.mcp-manager"
    assert service["labels"] == label
    assert generated["volumes"] == {"llm-bridge-data": {"labels": label}}
    assert generated["networks"] == {"default": {"labels": label}}
    assert generated["name"] == f"mw-mcp-{SLOT}"
    # 容器服务器的管理程序只认这些名字：管理器的项目名不在里面
    assert not re.match(r"^mw-(env|vol|net)-[0-9a-f]{12}$", generated["name"])
    # 镜像标签跟着构建文件的内容走：内容不变标签不变
    assert tag == bundle.content_tag(bundle.source_files(BRIDGE_SOURCE)) == bundle.current_tag()
    assert set(files) >= {
        "src/Dockerfile",
        "src/uv.lock",
        "src/pyproject.toml",
        "src/bridge/app.py",
    }
    assert not any("tests/" in name or ".venv" in name or "__pycache__" in name for name in files)
