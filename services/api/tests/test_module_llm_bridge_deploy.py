"""页面上一键部署 MCP 管理器（T-0233，docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）。

和 test_agent_ssh_install.py 用同一台“服务器”：本机起的真实 SSH 服务（asyncssh），里面的 docker
是记录调用的桩程序；工作台走真实的登录、SFTP 上传和命令执行。部署出来的管理器由进程内的
services/llm-bridge 应用代替（工作台连它、写入控制的平台、登记）。
"""

import asyncio
import contextlib
import importlib
import stat
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.modules.llm_bridge import deploy
from app.modules.llm_bridge import deploy_bundle as bundle
from tests.helpers import add_member, add_user, csrf, run_sql
from tests.test_agent_ssh_install import (  # noqa: F401 - owner 是夹具
    FakeHost,
    Owner,
    owner,
    ssh_host,
)

pytestmark = pytest.mark.integration
# PyYAML 是锁定的依赖（没有类型桩，所以不直接 import）：用它核对生成的 compose 文件
yaml = importlib.import_module("yaml")
BRIDGE_SOURCE = Path(__file__).resolve().parents[2] / "llm-bridge"
INFRA_COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "llm-bridge" / "compose.yaml"
TOKEN = "a1" * 32
API = "/api/v1/llm_bridge"

# 在原来的 docker 桩前面加上部署管理器用到的几条：构建、启动、停止、日志，
# 以及“和工作台是不是同一台机器”
_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
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
      down) rm -f "$STATE/mcp_up"; exit 0 ;;
      # 真实的容器日志里可能带出环境变量：任务日志里不能出现令牌
      logs) echo "mcp-manager-1 | $(cat bridge.env)"; exit 0 ;;
    esac ;;
esac
exec "$(dirname "$0")/docker-orig" "$@"
"""
_SS = r"""#!/bin/sh
cat "$STATE/listening" 2>/dev/null
exit 0
"""


@dataclass
class Rig:
    owner: Owner
    host: FakeHost
    server_id: str
    down: list[bool]

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
            "server_id": self.server_id,
            "name": name,
            "platforms": ["claude", "workbuddy"],
            "request_id": f"d-{uuid.uuid4().hex[:8]}",
            **extra,
        }
        return await self.api("POST", "/deployments", json=body)

    async def deployed(self, name: str = "3 号") -> dict[str, Any]:
        started = await self.deploy(name)
        assert started.status_code == 202, started.text
        job = await self.finished(started.json()["id"])
        assert job["status"] == "succeeded", job
        return job

    async def bridges(self) -> list[dict[str, Any]]:
        listed: list[dict[str, Any]] = (await self.api("GET", "/bridges")).json()["bridges"]
        return listed

    def directory(self, job: dict[str, Any]) -> Path:
        root = self.host.home / bundle.INSTALL_ROOT
        found = sorted(root.iterdir()) if root.is_dir() else []
        assert len(found) == 1, found
        return found[0]

    def docker_calls(self) -> list[str]:
        return [line for line in self.host.calls() if line.startswith("docker compose")]


@pytest.fixture
async def rig(
    owner: Owner,  # noqa: F811 - 夹具
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Rig]:
    monkeypatch.setattr(deploy, "new_token", lambda: TOKEN)
    monkeypatch.setattr(deploy, "VERIFY_POLL", 0.05)
    monkeypatch.setattr(deploy, "workbench_hostname", lambda: "0a1b2c3d4e5f")
    host = FakeHost(tmp_path)
    (host.bin / "docker").rename(host.bin / "docker-orig")
    for name, body in {"docker": _DOCKER, "ss": _SS}.items():
        (host.bin / name).write_text(body)
        (host.bin / name).chmod(0o755)
    sys.path.insert(0, str(BRIDGE_SOURCE))
    try:
        bridge_app = importlib.import_module("bridge.app").create_app(
            importlib.import_module("bridge.config").Settings(
                data_dir=tmp_path / "bridge", admin_token=TOKEN
            )
        )
        down = [False]
        asgi = httpx.ASGITransport(app=bridge_app)

        async def route(request: httpx.Request) -> httpx.Response:
            # 部署出来的管理器：只有“容器启动了”之后才连得上
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
            port, _ = await stack.enter_async_context(ssh_host(host))
            owner.app.state.connectors.transport = httpx.MockTransport(route)
            enabled = await owner.client.patch(
                "/api/v1/modules/llm_bridge", json={"enabled": True}, headers=owner.headers()
            )
            assert enabled.status_code == 200, enabled.text
            instance_id, _ = await owner.prepare(port)
            yield Rig(owner, host, owner.servers[instance_id], down)
    finally:
        sys.path.remove(str(BRIDGE_SOURCE))
        for name in [m for m in sys.modules if m == "bridge" or m.startswith("bridge.")]:
            del sys.modules[name]


async def test_deploy_builds_starts_and_registers_a_manager(rig: Rig) -> None:
    """不同机器：上传构建文件、构建、启动，端口开在登记服务器用的地址上，工作台连上后登记。"""
    body = {
        "server_id": rig.server_id,
        "name": "3 号",
        "platforms": ["claude", "workbuddy"],
        "request_id": "d1",
    }
    started = await rig.api("POST", "/deployments", json=body)
    assert started.status_code == 202, started.text
    assert (started.json()["kind"], started.json()["status"]) == ("deploy", "running")
    job = await rig.finished(started.json()["id"])
    assert job["status"] == "succeeded", job
    assert [(s["key"], s["status"]) for s in job["steps"]] == [
        (key, "done")
        for key in ("connect", "inspect", "upload", "build", "start", "verify", "register")
    ]
    assert (job["mode"], job["base_url"]) == ("remote", "http://127.0.0.1:18787")

    # 服务器上的文件：构建文件、生成的 compose.yaml、权限 600 的令牌文件
    directory = rig.directory(job)
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
    assert compose["name"] == f"mw-mcp-{directory.name}"
    assert service["ports"] == ["127.0.0.1:18787:8787"] and "networks" not in service
    assert service["image"].startswith("myworkspace/llm-bridge:deploy-")
    assert TOKEN not in (directory / "compose.yaml").read_text()
    assert rig.docker_calls()[-2:] == [
        "docker compose build",
        "docker compose up -d --wait --wait-timeout 240",
    ]

    # 登记好了：带着“部署在哪台服务器上”，控制的平台已经写进管理器
    (bridge,) = await rig.bridges()
    assert (bridge["name"], bridge["base_url"], bridge["has_token"]) == (
        "3 号",
        "http://127.0.0.1:18787",
        True,
    )
    assert bridge["deployment"]["server_id"] == rig.server_id
    assert bridge["deployment"]["mode"] == "remote"
    assert job["bridge_id"] == bridge["id"]
    status = (await rig.api("GET", f"/bridges/{bridge['id']}/status")).json()
    assert status["reachable"] and status["platforms"] == ["claude", "workbuddy"]

    # 令牌不在任务记录里；同一个 request_id 再提交得到的是同一个任务
    stored = await run_sql(
        rig.owner.database_url,
        "SELECT count(*) FROM workspace.llm_bridge_deploy_jobs "
        "WHERE log LIKE $1 OR steps::text LIKE $1",
        f"%{TOKEN}%",
    )
    assert stored == 0 and TOKEN not in job["log"]
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
    """和工作台同一台机器（服务器上有工作台的后端容器）：加入内部网络，端口不开到宿主机上。"""
    (rig.host.state / "same_host").write_text("0a1b2c3d4e5f\n")
    job = await rig.deployed()
    slot = rig.directory(job).name
    assert (job["mode"], job["base_url"]) == ("same_host", f"http://mcp-{slot}:8787")
    compose = yaml.safe_load((rig.directory(job) / "compose.yaml").read_text())
    service = compose["services"]["mcp-manager"]
    assert "ports" not in service
    assert service["networks"]["workspace-integrations"] == {"aliases": [f"mcp-{slot}"]}
    assert compose["networks"]["workspace-integrations"] == {
        "name": "my-workspace_integrations",
        "external": True,
    }
    inspect = next(s for s in job["steps"] if s["key"] == "inspect")
    assert "同一台机器" in inspect["detail"] and "my-workspace_integrations" in inspect["detail"]
    (bridge,) = await rig.bridges()
    assert bridge["base_url"] == f"http://mcp-{slot}:8787"
    assert bridge["deployment"]["mode"] == "same_host"


async def test_second_manager_on_the_same_server_gets_the_next_port(rig: Rig) -> None:
    (rig.host.state / "listening").write_text("LISTEN 0 128 0.0.0.0:18787 0.0.0.0:*\n")
    first = await rig.deployed("3 号")
    assert first["base_url"] == "http://127.0.0.1:18788"  # 18787 有别的程序在监听
    (rig.host.state / "listening").write_text("")
    second = await rig.deployed("4 号")
    assert second["base_url"] == "http://127.0.0.1:18787"
    third = await rig.deployed("5 号")
    assert third["base_url"] == "http://127.0.0.1:18789"  # 18787、18788 已经分给前两个
    assert sorted(b["name"] for b in await rig.bridges()) == ["3 号", "4 号", "5 号"]


async def test_failures_leave_nothing_registered(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    # 构建失败：容器还没起，不需要清理
    (rig.host.state / "build_fail").touch()
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "build_failed")
    assert next(s for s in job["steps"] if s["key"] == "build")["status"] == "failed"
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

    # 容器起来了但工作台连不上（防火墙）：同样清理，不留下没登记的管理器
    monkeypatch.setattr(deploy, "VERIFY_TIMEOUT", 0.3)
    rig.down[0] = True
    job = await rig.finished((await rig.deploy()).json()["id"])
    assert (job["status"], job["error_code"]) == ("failed", "verify_failed")
    assert "防火墙" in job["error_message"]
    assert rig.docker_calls()[-1] == "docker compose down -v --remove-orphans"
    assert await rig.bridges() == [] and not (rig.host.state / "mcp_up").exists()
    rig.down[0] = False

    # 修好之后再部署：成功
    assert (await rig.deployed())["status"] == "succeeded"
    actions = await run_sql(
        rig.owner.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'llm_bridge.deploy_failed'",
    )
    assert actions == 3


async def test_preconditions_and_roles(rig: Rig, tmp_path: Path) -> None:
    env = rig.owner
    # 名字被占用、服务器不存在、没选平台
    manual = await rig.api(
        "POST",
        "/bridges",
        json={"name": "手动", "base_url": "http://x.test:8787", "token": "t" * 40},
    )
    assert manual.status_code == 201, manual.text
    taken = await rig.deploy("手动")
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "NAME_TAKEN"
    assert (await rig.deploy(server_id=str(uuid.uuid4()))).status_code == 404
    assert (await rig.deploy(platforms=[])).status_code == 422
    # 手动添加的管理器不能从页面上重新部署、卸载
    for action in ("redeploy", "uninstall"):
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
    assert busy.status_code == 409, busy.text
    (rig.host.state / "build_wait").unlink()
    assert (await rig.finished(first.json()["id"]))["status"] == "succeeded"

    # admin 能看进度，不能部署；member 都不行
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
            assert (await other.get(f"{API}/deployments", headers=headers)).status_code == read
            body = {
                "server_id": rig.server_id,
                "name": "x",
                "platforms": ["claude"],
                "request_id": f"r-{role}",
            }
            denied = await other.post(f"{API}/deployments", json=body, headers=headers)
            assert denied.status_code == 403, denied.text


async def test_redeploy_keeps_the_token_and_uninstall_removes_everything(rig: Rig) -> None:
    job = await rig.deployed()
    directory = rig.directory(job)
    (bridge,) = await rig.bridges()
    stale = directory / "src" / "bridge" / "removed_module.py"
    stale.write_text("# 上一个版本留下的文件\n")

    # 重新部署（升级）：同一个目录、同一枚令牌、同一个端口；旧的构建文件不留下
    started = await rig.api("POST", f"/bridges/{bridge['id']}/redeploy", json={"request_id": "re1"})
    assert started.status_code == 202, started.text
    again = await rig.finished(started.json()["id"])
    assert (again["kind"], again["status"]) == ("redeploy", "succeeded"), again
    assert [s["key"] for s in again["steps"]] == [
        "connect",
        "inspect",
        "upload",
        "build",
        "start",
        "verify",
    ]
    assert again["base_url"] == job["base_url"] and rig.directory(again) == directory
    assert (directory / "bridge.env").read_text() == f"BRIDGE_ADMIN_TOKEN={TOKEN}\n"
    assert not stale.exists() and (directory / "src" / "bridge" / "app.py").is_file()
    assert [b["id"] for b in await rig.bridges()] == [bridge["id"]]

    # 只允许用这个管理器的实例：卸载后一个都不能用
    instance = await rig.owner.instance()
    granted = await rig.api(
        "PUT", f"/grants/{instance}", json={"enabled": True, "bridge_ids": [bridge["id"]]}
    )
    assert granted.status_code == 200, granted.text

    # 卸载：停掉并删除容器（数据卷保留）、删除目录、去掉登记
    started = await rig.api(
        "POST", f"/bridges/{bridge['id']}/uninstall", json={"request_id": "un1"}
    )
    assert started.status_code == 202, started.text
    gone = await rig.finished(started.json()["id"])
    assert (gone["kind"], gone["status"]) == ("uninstall", "succeeded"), gone
    assert rig.docker_calls()[-1] == "docker compose down --remove-orphans"
    assert not directory.exists() and not (rig.host.home / bundle.INSTALL_ROOT).exists()
    assert await rig.bridges() == []
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert next(g for g in grants if g["instance_id"] == instance)["bridge_ids"] == []

    # 连数据一起删
    job = await rig.deployed("4 号")
    (bridge,) = await rig.bridges()
    started = await rig.api(
        "POST",
        f"/bridges/{bridge['id']}/uninstall",
        json={"request_id": "un2", "remove_data": True},
    )
    gone = await rig.finished(started.json()["id"])
    assert gone["status"] == "succeeded", gone
    assert rig.docker_calls()[-1] == "docker compose down --remove-orphans -v"
    assert "数据卷也已删除" in next(s for s in gone["steps"] if s["key"] == "stop")["detail"]


async def test_a_job_left_running_by_a_restart_is_marked_interrupted(rig: Rig) -> None:
    """工作台重启后任务的协程不在了：读到时标为中断，不挡住后面的部署。"""
    job_id = str(uuid.uuid4())
    await run_sql(
        rig.owner.database_url,
        "INSERT INTO workspace.llm_bridge_deploy_jobs "
        "(id, workspace_id, kind, status, server_id, server_name, name, slot, request_id) "
        "VALUES ($1, $2, 'deploy', 'running', $3, '服务器', '旧任务', 'abcdef012345', 'old')",
        uuid.UUID(job_id),
        uuid.UUID(rig.owner.workspace),
        uuid.UUID(rig.server_id),
    )
    seen = (await rig.api("GET", f"/deployments/{job_id}")).json()
    assert (seen["status"], seen["error_code"]) == ("interrupted", "interrupted")
    assert (await rig.deployed())["status"] == "succeeded"


async def test_unavailable_without_the_build_files(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(bundle.SOURCE_ENV, str(tmp_path / "nowhere"))
    assert (await rig.api("GET", "/deployments")).json()["available"] is False
    refused = await rig.deploy()
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "DEPLOY_UNAVAILABLE"


def test_only_private_addresses_get_a_port() -> None:
    for address in ("10.0.0.5", "192.168.1.20", "172.16.3.4", "100.100.1.2", "fd00::5"):
        assert bundle.is_private(address), address
    for address in ("8.8.8.8", "1.2.3.4", "2001:4860:4860::8888", "169.254.1.1", "nonsense"):
        assert not bundle.is_private(address), address
    assert not bundle.is_private("127.0.0.1") and bundle.is_private(
        "127.0.0.1", allow_loopback=True
    )

    info = bundle.Inspect(home="/home/deploy", docker_ready=True, network="", port=18787)
    remote = bundle.plan_network(info, address="10.0.0.5", slot="abcdef012345")
    assert (remote.mode, remote.base_url, remote.port) == ("remote", "http://10.0.0.5:18787", 18787)
    six = bundle.plan_network(info, address="fd00::5", slot="abcdef012345")
    assert six.base_url == "http://[fd00::5]:18787" and '"[fd00::5]:18787:8787"' in six.block
    with pytest.raises(bundle.BundleError) as public:
        bundle.plan_network(info, address="8.8.8.8", slot="abcdef012345")
    assert public.value.code == "public_address"
    # 同一台机器时不看地址：公网地址登记的本机也能部署，端口不对外开放
    local = bundle.Inspect("/home/deploy", True, "my-workspace_integrations", None)
    same = bundle.plan_network(local, address="8.8.8.8", slot="abcdef012345")
    assert (same.mode, same.base_url) == ("same_host", "http://mcp-abcdef012345:8787")
    with pytest.raises(bundle.BundleError) as full:
        bundle.plan_network(
            bundle.Inspect("/home/deploy", True, "", None), address="10.0.0.5", slot="abcdef012345"
        )
    assert full.value.code == "no_free_port"


def test_commands_only_touch_the_install_directory() -> None:
    directory = bundle.install_dir("/home/deploy", "abcdef012345")
    assert directory == "/home/deploy/myworkspace-llm-bridge/abcdef012345"
    assert bundle.prepare_command(directory).endswith(f"rm -rf -- {directory}/src")
    assert bundle.compose_command(directory, "build") == f"cd {directory} && docker compose build"
    assert bundle.remove_dir_command(directory).startswith(f"rm -rf -- {directory} && ")
    for bad in (
        "/home/deploy",
        "/home/deploy/myworkspace-llm-bridge",
        "/home/deploy/myworkspace-llm-bridge/abc",
        "/home/deploy/myworkspace-llm-bridge/../../etc/abcdef012345",
        "relative/myworkspace-llm-bridge/abcdef012345",
        "/home/deploy/myworkspace-llm-bridge/abcdef012345; rm -rf /",
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
    with pytest.raises(bundle.BundleError):
        bundle.parse_inspect("home=relative\n")
    with pytest.raises(bundle.BundleError):
        bundle.parse_inspect("home=/home/deploy\nnetwork=evil; rm_integrations\n")
    with pytest.raises(ValueError, match="令牌"):
        bundle.env_file("short")


def test_generated_compose_matches_the_manual_one() -> None:
    """页面部署和手动部署是同一个服务：运行时的限制不能比手动部署的宽。"""
    manual = yaml.safe_load(INFRA_COMPOSE.read_text())["services"]["mcp-manager"]
    network = bundle.Network("remote", "http://10.0.0.5:18787", '    ports:\n      - "x"\n', 18787)
    files = bundle.source_files(BRIDGE_SOURCE)
    tag = bundle.content_tag(files)
    generated = yaml.safe_load(bundle.compose_file("abcdef012345", tag, network))
    service = generated["services"]["mcp-manager"]
    for key in ("restart", "cap_drop", "security_opt", "pids_limit", "volumes"):
        assert service[key] == manual[key], key
    assert "privileged" not in service and "network_mode" not in service
    assert all("docker.sock" not in str(v) for v in service["volumes"])
    assert generated["volumes"] == {"llm-bridge-data": None}
    # 镜像标签跟着构建文件的内容走：内容不变标签不变
    assert tag == bundle.content_tag(bundle.source_files(BRIDGE_SOURCE))
    assert set(files) >= {
        "src/Dockerfile",
        "src/uv.lock",
        "src/pyproject.toml",
        "src/bridge/app.py",
    }
    assert not any("tests/" in name or ".venv" in name or "__pycache__" in name for name in files)
