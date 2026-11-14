"""MCP 管理器的容器不受两个服务器页面的管理程序限制（T-0233）。

docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 8 节的表里“加回归测试钉住”与“要改”的每一条：

- 智能体服务器：清理只删悬空镜像；重启、启动、部署、卸载只在实例自己的目录里对那一个 Compose 项目
  执行；没有针对整机的停、删容器和重启 Docker。先看写死的命令，再在假的 SSH 服务器上真的走一遍
  部署、运维动作、卸载，逐条检查发到服务器上的命令，并确认管理器的目录原样还在。
- 容器服务器：runner-agent 的回收、空闲清理、磁盘紧急清理、记录外容器的处理只碰带
  `myworkspace.runner=1` 且名字是 `mw-(env|vol|net)-…` 的对象；上报时管理器单独计数。
  假的 Docker 按标签如实过滤，停、删都记下来。
- `install-gvisor.sh`：用假的 docker、systemctl、runsc 等（放进临时 PATH）跑真的脚本，
  覆盖“重新加载后登记上”“没登记且有管理器”“给了允许”三条路径，以及经 sudo 传环境变量。

接口级的 `allow_docker_restart` / `gvisor_needs_docker_restart` 在 tests/test_runner_ssh.py。
"""

import io
import os
import re
import subprocess
import uuid
import zipfile
from collections.abc import AsyncIterator
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest

from app import agent_bundle, runner_bundle
from app import agent_provisioning as prov
from app.domain import agent_ops, agent_ssh_install, runner_ssh
from app.domain import runner_specs as specs
from app.integrations import ssh
from app.modules.llm_bridge import deploy_bundle
from app.runner_agent import containers as c
from app.runner_agent.agent import Agent, Config
from tests import test_agent_ops_api as agent_ops_tests
from tests import test_agent_ssh_install as agent_ssh_tests
from tests import test_runner_api as runner_api_tests
from tests import test_runner_ssh as runner_ssh_tests
from tests.helpers import OWNER, ClientFactory
from tests.workspace_support import Env, open_env

# 一个部署好的 MCP 管理器：Compose 项目 mw-mcp-<编号>，容器、数据卷、网络都带标签
SLOT = "0123456789ab"
PROJECT = f"mw-mcp-{SLOT}"
MANAGER = f"{PROJECT}-mcp-manager-1"
MANAGER_VOLUME = f"{PROJECT}_llm-bridge-data"
MANAGER_NETWORK = f"{PROJECT}_default"
MANAGER_IMAGE = "myworkspace/llm-bridge:0f1e2d3c4b5a"
MANAGER_LABELS = {
    "myworkspace.mcp-manager": SLOT,
    "com.docker.compose.project": PROJECT,
    "com.docker.compose.service": "mcp-manager",
}
MANAGER_OBJECTS = {MANAGER, MANAGER_VOLUME, MANAGER_NETWORK, MANAGER_IMAGE, PROJECT}

# 针对整机的命令：按名字或 ID 停、删容器，清理数据卷 / 网络 / 全部镜像，重启 Docker
HOST_WIDE = re.compile(
    r"""
      \bdocker\s+(?:container\s+)?(?:stop|rm|kill|restart|pause)\b
    | \bdocker\s+(?:system|volume|container|network|builder|buildx)\s+prune\b
    | \bdocker\s+(?:volume|network|image)\s+rm\b
    | \bdocker\s+rmi\b
    | \bsystemctl\s+(?:restart|stop|reload|kill)\s+docker
    | \bservice\s+docker\s+(?:restart|stop|reload)
    | \b(?:pkill|killall)\b
    | \bdown\b[^\n]*(?:\s-v\b|--volumes|--rmi)
    """,
    re.X,
)
_PRUNE = re.compile(r"\bdocker\b[^\n\"']*\bprune\b[^\n\"']*")


def _prunes(text: str) -> list[str]:
    return [found.group(0).strip() for found in _PRUNE.finditer(text)]


def test_the_label_is_the_same_everywhere() -> None:
    """runner-agent 随接入包分发、不能导入工作台的模块；脚本里也只能写死：三处要一致。"""
    assert c.LABEL_MCP_MANAGER == deploy_bundle.LABEL == "myworkspace.mcp-manager"
    assert f"--filter label={deploy_bundle.LABEL}" in _bundle_file("install-gvisor.sh")
    assert deploy_bundle.project_name(SLOT) == PROJECT
    # 管理器的名字不是环境的名字，标签也不是 runner-agent 的标签
    for name in MANAGER_OBJECTS:
        for kind in ("env", "vol", "net"):
            with pytest.raises(c.ParamError):
                c.check_name(name, kind)
    assert c.LABEL_RUNNER not in MANAGER_LABELS
    for name in specs.names(uuid.uuid4()).values():
        assert c.check_name(name, name.split("-")[1]) == name


# ---------------------------------------------------------------- 智能体服务器：写死的命令

INSTANCE = "0b3c2a8e-1234-4000-8000-000000000001"
HOME = "/home/deploy"
DIRECTORY = prov.install_dir(HOME, INSTANCE)
MANAGER_DIR = deploy_bundle.install_dir(HOME, SLOT)


def test_agent_server_prune_only_removes_dangling_images() -> None:
    """清理无用镜像：只删悬空镜像。管理器的镜像有标签并且在用；数据卷、网络、容器都不清理。"""
    assert agent_ops.commands("prune", None, "") == ["docker image prune -f"]
    assert agent_ops.commands("prune", None, DIRECTORY) == ["docker image prune -f"]


def test_agent_server_actions_stay_inside_the_instance_directory() -> None:
    assert DIRECTORY == "/home/deploy/myworkspace-openclaw/0b3c2a8e1234"
    assert agent_ops.commands("restart", None, DIRECTORY) == [
        f"cd {DIRECTORY} && docker compose restart openclaw"
    ]
    assert agent_ops.commands("up", None, DIRECTORY) == [f"cd {DIRECTORY} && docker compose up -d"]
    # 卸载：只在实例目录里 down；删除目录只接受实例自己的目录
    assert prov.compose_command(DIRECTORY, "down", "--remove-orphans") == (
        f"cd {DIRECTORY} && docker compose down --remove-orphans"
    )
    assert 'prov.compose_command(directory, "down", "--remove-orphans")' in _source(
        agent_ssh_install
    )
    assert prov.remove_dir_command(DIRECTORY) == (
        f"rm -rf -- {DIRECTORY} && {{ rmdir -- {HOME}/myworkspace-openclaw 2>/dev/null || true; }}"
    )
    for other in (MANAGER_DIR, f"{HOME}/{deploy_bundle.INSTALL_ROOT}", HOME):
        with pytest.raises(ValueError):
            prov.remove_dir_command(other)
    # 实例的 Compose 项目名与目录都和管理器的不同
    assert agent_bundle.project_name(INSTANCE) == "myworkspace-openclaw-0b3c2a8e1234"
    assert not MANAGER_DIR.startswith(prov.deploy_root(HOME) + "/")


def _agent_server_commands() -> list[str]:
    out: list[str] = []
    for action in ("status", "restart", "up", "prune", "containers", "report"):
        out += agent_ops.commands(action, None, DIRECTORY)
    for service in ("openclaw", "tunnel", "image-proxy"):
        out += agent_ops.commands("logs", service, DIRECTORY)
    out += [
        prov.compose_command(DIRECTORY, "pull", "--quiet"),
        prov.compose_command(DIRECTORY, "up", "-d"),
        prov.compose_command(DIRECTORY, "down", "--remove-orphans"),
        prov.schedule_command(DIRECTORY, INSTANCE),
        prov.unschedule_command(INSTANCE),
        prov.reflection_command(DIRECTORY, 3, 5),
        prov.remove_dir_command(DIRECTORY),
        prov.verify_command(prov.deploy_root(HOME), ("agent.example.com", 9443)),
        *prov.cron_lines(DIRECTORY, INSTANCE),
    ]
    for uid in (0, 1001):
        out += [
            command
            for command in (
                prov.reclaim_command(DIRECTORY, uid=uid, gid=uid),
                prov.handover_command(DIRECTORY, uid=uid),
            )
            if command is not None
        ]
    return out


_COMPOSE = re.compile(r"docker compose\s+(\S+)")


def _compose_stays_in(command: str, directory: str) -> bool:
    """命令里每一处 docker compose 之前都已经进了这个目录（只读的 version、ls 除外）。"""
    prefix = f"cd {prov.q(directory)} && "
    return all(
        found.group(1) in ("version", "ls") or prefix in command[: found.start()]
        for found in _COMPOSE.finditer(command)
    )


def test_agent_server_commands_never_act_on_the_whole_host() -> None:
    commands = _agent_server_commands()
    assert len(commands) > 25
    for command in commands:
        assert not HOST_WIDE.search(command), command
        assert _compose_stays_in(command, DIRECTORY), command
        assert all(prune == "docker image prune -f" for prune in _prunes(command)), command
    # 这些模块的源码和脚本里也没有这类命令（以后加进来就在这里失败）
    sources = {
        "agent_ops.py": _source(agent_ops),
        "agent_ssh_install.py": _source(agent_ssh_install),
        "agent_provisioning/__init__.py": _source(prov),
        **{
            f"agent_provisioning/files/{name}": prov.script(name)
            for name in ("inspect.sh", "install-docker.sh", "install-plugin.sh", "verify.sh")
        },
    }
    for name, text in sources.items():
        assert not HOST_WIDE.search(text), name
        assert all(prune == "docker image prune -f" for prune in _prunes(text)), name
    assert _prunes(sources["agent_ops.py"]) == ["docker image prune -f"]


def _source(module: Any) -> str:
    path = module.__file__
    assert isinstance(path, str)
    return Path(path).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 智能体服务器：假服务器上走一遍


@pytest.fixture(autouse=True)
def fast_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """部署任务里的等待：测试里不等几秒。"""
    monkeypatch.setattr(agent_ssh_install, "ONLINE_POLL", 0.05)
    monkeypatch.setattr(agent_ssh_install, "REFLECTION_TRIES", 3)
    monkeypatch.setattr(agent_ssh_install, "REFLECTION_WAIT", 0)
    monkeypatch.setattr(runner_ssh, "ONLINE_POLL", 0.05)


@pytest.fixture
async def agent_owner(
    make_client: ClientFactory, migrated_database_url: str
) -> AsyncIterator[agent_ssh_tests.Owner]:
    async with make_client(**agent_ssh_tests.SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield agent_ssh_tests.Owner(
            client, setup.json()["workspaces"][0]["id"], migrated_database_url
        )


@pytest.fixture
async def runner_owner(
    make_client: ClientFactory, migrated_database_url: str
) -> AsyncIterator[runner_ssh_tests.Owner]:
    async with make_client(**runner_ssh_tests.SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield runner_ssh_tests.Owner(
            client, setup.json()["workspaces"][0]["id"], migrated_database_url
        )


class Placed:
    """假服务器主目录里一个部署好的管理器：目录里的文件，和 authorized_keys 里通道的那一行公钥。"""

    def __init__(self, home: Path, files: dict[Path, str], key: str) -> None:
        self.home = home
        self.files = files
        self.key = key

    def intact(self) -> bool:
        keys = (self.home / ".ssh" / "authorized_keys").read_text().splitlines()
        return self.key in keys and all(
            path.is_file() and path.read_text() == text for path, text in self.files.items()
        )


def _place_manager(home: Path) -> Placed:
    directory = Path(deploy_bundle.install_dir(str(home), SLOT))
    (directory / "src").mkdir(parents=True)
    files = {
        directory / "compose.yaml": f"name: {PROJECT}\n",
        directory / "bridge.env": "LLM_BRIDGE_ADMIN_TOKEN=not-a-real-token\n",
        directory / "src" / "Dockerfile": "FROM scratch\n",
    }
    for path, text in files.items():
        path.write_text(text)
    public = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    key = f"{public} myworkspace-mcp-{SLOT}"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    (ssh_dir / "authorized_keys").write_text(key + "\n")
    return Placed(home, files, key)


def _mentions_manager(text: str) -> bool:
    return any(
        marker in text
        for marker in (PROJECT, deploy_bundle.INSTALL_ROOT, deploy_bundle.LABEL, "mcp-manager")
    )


@pytest.mark.integration
async def test_agent_server_lifecycle_leaves_the_mcp_manager_alone(
    agent_owner: agent_ssh_tests.Owner, tmp_path: Path
) -> None:
    """部署、运维动作（状态、日志、重启、拉起、清理镜像、查看容器）、重新部署、卸载、删除目录、
    移除服务器：发到服务器上的每一条命令都不针对整机，compose 命令都在实例自己的目录里。"""
    owner = agent_owner
    host = agent_ssh_tests.FakeHost(tmp_path)
    manager = _place_manager(host.home)
    password = {"password": OWNER["password"]}
    async with agent_ssh_tests.ssh_host(host) as (port, _):
        instance_id, version = await owner.prepare(port)
        state = await agent_ssh_tests._install_ok(owner, host, instance_id, version)
        instance_dir = host.directory
        directory = str(instance_dir)
        assert directory == prov.install_dir(str(host.home), instance_id)
        server_id = owner.servers[instance_id]
        for body in (
            {"action": "status"},
            {"action": "logs"},
            {"action": "report"},
            {"action": "restart", **password},
            {"action": "up", **password},
        ):
            done = await agent_ops_tests.run_ok(owner, instance_id, body)
            assert done["status"] == "succeeded", done
        for body in ({"action": "prune", **password}, {"action": "containers"}):
            done = await agent_ops_tests.server_run_ok(owner, server_id, body)
            assert done["status"] == "succeeded", done
        # 重新部署（升级）一次，再卸载并删除目录，最后移除服务器
        (host.state / "up").unlink()
        state = await agent_ssh_tests._install_ok(
            owner, host, instance_id, await owner.server_version(instance_id), password=None
        )
        response = await agent_ssh_tests._post(
            owner,
            instance_id,
            "uninstall",
            {"version": state["target"]["version"], "remove_files": True},
        )
        assert response.status_code == 202, response.text
        state = await owner.finished(instance_id)
        assert state["job"]["status"] == "succeeded", state["job"]
        assert not instance_dir.exists()
        logins = len(host.logins)
        sent = len(host.commands)
        removed = await owner.remove(instance_id, state["target"]["version"])
        assert removed.status_code == 204, removed.text
        # 移除服务器不到服务器上执行任何命令
        assert len(host.logins) == logins and len(host.commands) == sent

    commands = host.commands
    assert f"cd {directory} && docker compose restart openclaw" in commands
    assert f"cd {directory} && docker compose up -d" in commands
    assert f"cd {directory} && docker compose down --remove-orphans" in commands
    assert "docker image prune -f" in commands
    assert any(command.startswith(f"rm -rf -- {directory} ") for command in commands)
    for command in commands:
        assert not HOST_WIDE.search(command), command
        assert _compose_stays_in(command, directory), command
        assert all(prune == "docker image prune -f" for prune in _prunes(command)), command
        assert not _mentions_manager(command), command
    # 经标准输入送去执行的脚本（只读检查、装插件等）同样
    for data in host.stdin:
        text = data.decode("utf-8", "replace")
        assert not HOST_WIDE.search(text) and not _prunes(text), text[:200]
        assert not _mentions_manager(text), text[:200]
    # 假的 docker 记下的每一次调用
    for call in host.calls():
        assert not HOST_WIDE.search(call), call
        assert not _mentions_manager(call), call
    assert [call for call in host.calls() if "prune" in call] == ["docker image prune -f"]
    assert manager.intact()


@pytest.mark.integration
async def test_runner_uninstall_and_revoke_leave_the_mcp_manager_alone(
    runner_owner: runner_ssh_tests.Owner, tmp_path: Path
) -> None:
    """容器服务器：卸载 runner-agent 只停它自己的 Compose 项目、只删它自己的目录；
    吊销专用密钥只删那一把公钥所在的行（管理器的通道用自己的密钥）。"""
    owner = runner_owner
    host = runner_ssh_tests.RunnerHost(tmp_path)
    manager = _place_manager(host.home)
    async with agent_ssh_tests.ssh_host(host) as (port, _):
        version = await owner.prepare(port)
        installed = await runner_ssh_tests._install(
            owner, host, {"version": version, "password": agent_ssh_tests.PASSWORD}
        )
        assert installed["job"]["status"] == "succeeded", installed["job"]
        removed = await owner.run(
            "uninstall",
            {
                "version": installed["target"]["version"],
                "remove_files": True,
                "password": agent_ssh_tests.PASSWORD,
            },
        )
        assert removed["job"]["status"] == "succeeded", removed["job"]
        assert not host.runner_dir.exists()
        revoked = await owner.client.post(
            "/api/v1/runner/host/ssh/revoke",
            json={"version": removed["target"]["version"]},
            headers=owner.headers(),
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["target"]["client_key_installed"] is False

    directory = str(host.runner_dir)
    docker_calls = [call for call in host.calls() if call.startswith("docker ")]
    assert docker_calls == [f"docker compose -f {directory}/compose.yaml down"]
    assert f"sudo rm -rf {directory}" in host.calls()
    for command in host.commands:
        assert not HOST_WIDE.search(command), command
        assert not _mentions_manager(command), command
    assert (host.home / ".ssh" / "authorized_keys").read_text().splitlines() == [manager.key]
    assert manager.intact()
    # 接入包：runner-agent 的 Compose 项目名固定，install.sh 只拉起这一个项目
    assert "\nname: myworkspace-runner\n" in _bundle_file("compose.yaml")
    install = _bundle_file("install.sh")
    assert not HOST_WIDE.search(install)
    assert "docker compose -f compose.yaml up -d --remove-orphans" in install


# ---------------------------------------------------------------- 容器服务器：runner-agent


class FakeDocker:
    """假的 Docker：列表接口按标签如实过滤（和 Docker 的 label 过滤器一样），停、删都记下来。"""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.volume_items: list[dict[str, Any]] = []
        self.network_items: list[dict[str, Any]] = []
        self.image_items: list[dict[str, Any]] = []
        self.changes: list[tuple[str, str]] = []

    def add(
        self,
        name: str,
        labels: dict[str, str],
        *,
        state: str = "running",
        created: int = 1,
        finished: str = "0001-01-01T00:00:00Z",
    ) -> None:
        self.items.append(
            {
                "Id": f"id-{name}",
                "Names": [f"/{name}"],
                "State": state,
                "SizeRw": 10,
                "Created": created,
                "Labels": dict(labels),
                "FinishedAt": finished,
            }
        )

    @staticmethod
    def _has(item: dict[str, Any], selector: str) -> bool:
        key, sep, value = selector.partition("=")
        labels = item.get("Labels") or {}
        return key in labels and (not sep or labels[key] == value)

    def _find(self, name: str) -> dict[str, Any] | None:
        return next((i for i in self.items if name in (i["Id"], i["Names"][0][1:])), None)

    # ------------------------------------------------------------ 只读
    def system_df(self) -> dict[str, Any]:
        volumes = [{"Name": v["Name"], "UsageData": {"Size": 4096}} for v in self.volume_items]
        return {"Volumes": volumes, "Images": list(self.image_items)}

    def containers(self, label: str) -> list[dict[str, Any]]:
        return [i for i in self.items if self._has(i, label)]

    def all_containers(self) -> list[dict[str, Any]]:
        return list(self.items)

    def volumes(self, label: str) -> list[dict[str, Any]]:
        return [v for v in self.volume_items if self._has(v, label)]

    def networks(self, label: str) -> list[dict[str, Any]]:
        return [n for n in self.network_items if self._has(n, label)]

    def images(self, label: str) -> list[dict[str, Any]]:
        return [i for i in self.image_items if self._has(i, label)]

    def inspect(self, name: str) -> dict[str, Any] | None:
        item = self._find(name)
        if item is None:
            return None
        return {
            "Id": item["Id"],
            "Config": {"Labels": dict(item["Labels"])},
            "State": {
                "Running": item["State"] == "running",
                "StartedAt": "2026-11-14T08:00:00Z",
                "FinishedAt": item["FinishedAt"],
            },
        }

    def stats(self, name: str) -> dict[str, Any]:
        return {"cpu_stats": {"cpu_usage": {"total_usage": 1}}, "memory_stats": {"usage": 1}}

    # ------------------------------------------------------------ 改动
    def stop(self, name: str, *, seconds: int = 10) -> None:
        self.changes.append(("stop", name))
        item = self._find(name)
        if item is not None:
            item["State"] = "exited"

    def remove(self, name: str) -> None:
        self.changes.append(("remove", name))
        found = self._find(name)
        self.items = [i for i in self.items if i is not found]

    def remove_volume(self, name: str) -> None:
        self.changes.append(("remove_volume", name))

    def remove_network(self, name: str) -> None:
        self.changes.append(("remove_network", name))

    def remove_image(self, ref: str) -> None:
        self.changes.append(("remove_image", ref))

    def disconnect_network(self, network: str, container: str) -> None:
        self.changes.append(("disconnect_network", network))


class FakeWorkspace:
    def __init__(self, stray: list[str]) -> None:
        self.stray = stray
        self.bodies: list[dict[str, Any]] = []

    def post(self, path: str, body: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        if path == "/report/start":
            return {"sample_id": str(uuid.uuid4()), "expires_in": 90}
        assert path == "/report"
        self.bodies.append(body)
        return {"lost": [], "stray": list(self.stray)}


class FakeProxy:
    class _Stats:
        def drain(self) -> dict[str, Any]:
            return {}

    stats = _Stats()

    def __init__(self) -> None:
        self.closed: list[str] = []

    def envs(self) -> set[str]:
        return set()

    def close(self, env_id: str) -> None:
        self.closed.append(env_id)


def _env_labels(env_id: str, **extra: str) -> dict[str, str]:
    return {c.LABEL_RUNNER: "1", c.LABEL_ROLE: "env", c.LABEL_ENV: env_id, **extra}


SESSION_ENV = "mw-env-111111111111"
ONCE_ENV = "mw-env-222222222222"
STRAY_RUNNING = "mw-env-333333333333"
STRAY_OLD = "mw-env-444444444444"
HELPER = "mw-helper-555555555555"
LOOKALIKE = "mw-env-666666666666"  # 名字像环境，但没有工作台标签（别人的容器）
TEMPLATE_IMAGE = "myworkspace/tpl-0123456789abcdef:3"


def _server() -> FakeDocker:
    """一台容器服务器：几个环境、一个助手容器、runner-agent 自己、别人的容器，还有一个管理器。"""
    docker = FakeDocker()
    docker.add(SESSION_ENV, _env_labels("env-session", **{c.LABEL_LIFETIME: "session"}))
    docker.add(ONCE_ENV, _env_labels("env-once", **{c.LABEL_LIFETIME: "once"}))
    docker.add(STRAY_RUNNING, _env_labels("env-stray-running"))
    docker.add(
        STRAY_OLD,
        _env_labels("env-stray-old"),
        state="exited",
        finished="2020-01-01T00:00:00.000000000Z",
    )
    docker.add(HELPER, {c.LABEL_RUNNER: "1", c.LABEL_ROLE: "helper", c.LABEL_ENV: "env-session"})
    docker.add("myworkspace-runner-agent", {"com.docker.compose.project": "myworkspace-runner"})
    docker.add("someone-elses-db", {})
    docker.add(LOOKALIKE, {})
    docker.add(MANAGER, MANAGER_LABELS)
    docker.volume_items = [
        {"Name": "mw-vol-111111111111", "Labels": _env_labels("env-session")},
        {"Name": MANAGER_VOLUME, "Labels": {"myworkspace.mcp-manager": SLOT}},
    ]
    docker.network_items = [
        {"Name": "mw-net-111111111111", "Labels": _env_labels("env-session")},
        {"Name": MANAGER_NETWORK, "Labels": {"myworkspace.mcp-manager": SLOT}},
    ]
    docker.image_items = [
        {
            "Id": "sha256:aaa",
            "RepoTags": [TEMPLATE_IMAGE],
            "Size": 1000,
            "Labels": {c.LABEL_RUNNER: "1", c.LABEL_TEMPLATE: "tpl-1"},
        },
        {
            "Id": "sha256:bbb",
            "RepoTags": [MANAGER_IMAGE],
            "Size": 2000,
            "Labels": {"com.docker.compose.project": PROJECT},
        },
    ]
    return docker


def _agent(docker: FakeDocker, stray: list[str] | None = None) -> tuple[Agent, FakeWorkspace]:
    workspace = FakeWorkspace(stray or [])
    agent = Agent(
        config=Config(url="https://example.invalid", token="t", workers=1),  # noqa: S106
        docker=docker,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
        proxy=FakeProxy(),  # type: ignore[arg-type]
    )
    agent.self_id = "id-myworkspace-runner-agent"
    return agent, workspace


def _untouched(docker: FakeDocker) -> bool:
    touched = {target for _, target in docker.changes}
    manager = docker._find(MANAGER)
    return (
        touched.isdisjoint(MANAGER_OBJECTS | {f"id-{MANAGER}"})
        and manager is not None
        and manager["State"] == "running"
    )


def _disk(monkeypatch: pytest.MonkeyPatch, used: float) -> None:
    stat = SimpleNamespace(f_blocks=1000, f_bavail=round(1000 * (1 - used)), f_frsize=4096)
    monkeypatch.setattr(os, "statvfs", lambda path: stat)


def test_runner_agent_reports_the_manager_separately_and_cleans_up_around_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次对账上报里的全部清理：过期的助手容器、磁盘告急时的一次性环境、记录外的容器。
    工作台哪怕把管理器（或它的数据卷、网络、项目名）列进“记录外”，runner-agent 也不碰。"""
    _disk(monkeypatch, 0.99)
    docker = _server()
    stray = [MANAGER, MANAGER_VOLUME, MANAGER_NETWORK, PROJECT, LOOKALIKE, STRAY_RUNNING, STRAY_OLD]
    agent, workspace = _agent(docker, stray)

    agent.report()

    body = workspace.bodies[-1]
    assert body["mcp_manager_containers"] == 1
    # runner-agent 自己、别人的数据库、名字像环境的那个：管理器不在里面
    assert body["unlabeled_containers"] == 3
    reported = {item["name"] for item in body["containers"]}
    assert reported == {SESSION_ENV, ONCE_ENV, STRAY_RUNNING, STRAY_OLD}
    assert [volume["name"] for volume in body["volumes"]] == ["mw-vol-111111111111"]
    assert [image["ref"] for image in body["images"]] == [TEMPLATE_IMAGE]
    # 清理确实跑了：过期的助手容器删掉，一次性环境停掉，记录外的停掉 / 删掉
    assert ("remove", HELPER) in docker.changes
    assert ("stop", ONCE_ENV) in docker.changes
    assert ("stop", STRAY_RUNNING) in docker.changes
    assert ("remove", STRAY_OLD) in docker.changes
    assert {target for _, target in docker.changes} == {HELPER, ONCE_ENV, STRAY_RUNNING, STRAY_OLD}
    assert _untouched(docker)
    lookalike = docker._find(LOOKALIKE)
    assert lookalike is not None and lookalike["State"] == "running"  # 没有工作台标签的一律不碰


def test_runner_agent_commands_refuse_the_manager_objects() -> None:
    """回收、空闲停止、归档、删除、执行命令、读写文件：指令里的名字必须是 mw-(env|vol|net)-…。"""
    docker = _server()
    agent, _ = _agent(docker)
    payload: dict[str, Any] = {
        "env_id": "env-x",
        "container": MANAGER,
        "volume": MANAGER_VOLUME,
        "network": MANAGER_NETWORK,
        "cmd": ["true"],
        "path": "notes.txt",
        "files": [{"path": "notes.txt", "b64": ""}],
        "image": "python:3.12-slim",
    }
    handlers = (
        agent.do_stop_env,
        agent.do_delete_env,
        agent.do_archive_env,
        agent.do_start_env,
        agent.do_create_env,
        agent.do_exec,
        agent.do_snapshot,
        agent.do_put_files,
        agent.do_get_files,
        agent.do_list_files,
    )
    for handler in handlers:
        with pytest.raises(c.ParamError):
            handler(dict(payload))
    # 容器名合法、数据卷或网络是管理器的：三个名字都检查过才动手
    for change in (
        {"volume": MANAGER_VOLUME},
        {"network": MANAGER_NETWORK},
        {"volume": PROJECT},
    ):
        mixed = {
            **payload,
            "container": SESSION_ENV,
            "volume": "mw-vol-111111111111",
            "network": "mw-net-111111111111",
            **change,
        }
        with pytest.raises(c.ParamError):
            agent.do_delete_env(mixed)
    assert docker.changes == [] and _untouched(docker)
    # 对照：环境自己的名字照常删除
    agent.do_delete_env(
        {
            "env_id": "env-session",
            "container": SESSION_ENV,
            "volume": "mw-vol-111111111111",
            "network": "mw-net-111111111111",
        }
    )
    assert docker.changes == [
        ("remove", SESSION_ENV),
        ("disconnect_network", "mw-net-111111111111"),
        ("remove_network", "mw-net-111111111111"),
        ("remove_volume", "mw-vol-111111111111"),
    ]


def test_runner_agent_image_prune_only_looks_at_template_images() -> None:
    docker = _server()
    agent, _ = _agent(docker)
    assert agent.do_prune_images({"keep": []}) == {"removed": [TEMPLATE_IMAGE]}
    assert docker.changes == [("remove_image", TEMPLATE_IMAGE)]
    assert _untouched(docker)


def test_the_manager_label_does_not_exempt_an_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """智能体自己写的模板镜像可以带任意标签：环境带上管理器的标签也照样是环境，
    照常上报、照常被磁盘紧急清理停掉，也不算进管理器的数量。"""
    _disk(monkeypatch, 0.99)
    docker = FakeDocker()
    labels = _env_labels("env-spoof", **{c.LABEL_LIFETIME: "once", c.LABEL_MCP_MANAGER: "x"})
    docker.add(ONCE_ENV, labels)
    agent, workspace = _agent(docker)

    agent.report()

    body = workspace.bodies[-1]
    assert body["mcp_manager_containers"] == 0 and body["unlabeled_containers"] == 0
    assert [item["name"] for item in body["containers"]] == [ONCE_ENV]
    assert docker.changes == [("stop", ONCE_ENV)]


@pytest.fixture
async def runner_env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **runner_api_tests.SETTINGS) as value:
        yield value


async def _host_report(env: Env) -> dict[str, Any]:
    response = await env.client.get("/api/v1/runner/overview", headers=env.headers())
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()["host"]["report"]
    return report


@pytest.mark.integration
async def test_workspace_keeps_the_manager_count_and_old_agents_report_none(
    runner_env: Env,
) -> None:
    env = runner_env
    # add_host 里的那份上报没有这个字段（旧版 runner-agent）：按 0
    token = await runner_api_tests.add_host(env)
    report = await _host_report(env)
    assert report["mcp_manager_containers"] == 0 and report["unlabeled_containers"] == 0
    sample_id = await runner_api_tests.begin_sample(env, token)
    posted = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": sample_id,
            "physical_host_id": runner_api_tests.physical_id(token),
            "unlabeled_containers": 2,
            "mcp_manager_containers": 1,
            "mem_bytes": 32 << 30,
            "mem_free_bytes": 24 << 30,
        },
        headers=runner_api_tests.runner_headers(token),
    )
    assert posted.status_code == 200, posted.text
    assert posted.json() == {"lost": [], "stray": []}
    report = await _host_report(env)
    assert report["unlabeled_containers"] == 2 and report["mcp_manager_containers"] == 1


# ---------------------------------------------------------------- install-gvisor.sh

RELOADS = {"systemctl reload docker", "pkill -HUP -x dockerd"}
RESTARTS = {"systemctl restart docker", "service docker restart"}
_STUBS = {
    # 版本和锁定的一致：脚本跳过下载与校验（和已经装过 gVisor 的服务器一样）
    "runsc": r"""#!/bin/sh
echo "runsc $*" >> "$FAKE_LOG"
case "$1" in --version) echo "runsc version release-20261019.0" ;; esac
exit 0
""",
    "docker": r"""#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  info)
    if [ -f "$STATE/registered" ]; then
      echo '{"io.containerd.runc.v2":{"path":"runc"},"runc":{"path":"runc"},"runsc":{"path":"x"}}'
    else
      echo '{"io.containerd.runc.v2":{"path":"runc"},"runc":{"path":"runc"}}'
    fi ;;
  ps)
    # 只认按 MCP 管理器标签过滤的查询：脚本里的标签写错了就数不到
    case "$*" in
      *"--filter label=@LABEL@"*) if [ -f "$STATE/managers" ]; then cat "$STATE/managers"; fi ;;
    esac ;;
  version) echo 29.9.1 ;;
  compose) case "$2" in version) echo 5.6.0 ;; esac ;;
esac
exit 0
""",
    "systemctl": r"""#!/bin/sh
echo "systemctl $*" >> "$FAKE_LOG"
case "$*" in
  "reload docker") if [ -f "$STATE/reload_registers" ]; then touch "$STATE/registered"; fi ;;
  "restart docker") touch "$STATE/registered" ;;
esac
exit 0
""",
    "pkill": r"""#!/bin/sh
echo "pkill $*" >> "$FAKE_LOG"
case "$*" in
  "-HUP -x dockerd") if [ -f "$STATE/reload_registers" ]; then touch "$STATE/registered"; fi ;;
esac
exit 0
""",
    "service": r"""#!/bin/sh
echo "service $*" >> "$FAKE_LOG"
case "$*" in "docker restart") touch "$STATE/registered" ;; esac
exit 0
""",
    "uname": "#!/bin/sh\necho x86_64\n",
    "sleep": "#!/bin/sh\nexit 0\n",
    "id": "#!/bin/sh\necho 0\n",
    # 模拟 sudo 默认的 env_reset：调用者的环境变量不带给命令（只留这些桩程序要用的）
    "sudo": r"""#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in -S|-k) shift ;; -p) shift 2 ;; *) break ;; esac
done
IFS= read -r pw || pw=""
echo "sudo $*" >> "$FAKE_LOG"
exec /usr/bin/env -i PATH="$PATH" STATE="$STATE" FAKE_LOG="$FAKE_LOG" "$@"
""",
}
# 桩程序都在的话脚本不会去下载、解压；万一走到了，这些命令直接失败，不动这台机器
_MUST_NOT_RUN = ("curl", "apt-get", "tar", "sha512sum", "zstd", "usermod", "groupadd")


def _bundle_file(name: str) -> str:
    return (resources.files("app.runner_bundle") / "files" / name).read_text(encoding="utf-8")


class Sandbox:
    """临时 PATH 里放假的 docker、systemctl、runsc 等，跑接入包里真的脚本。"""

    def __init__(self, root: Path, *, managers: int = 0, reload_registers: bool = False) -> None:
        self.bin = root / "bin"
        self.state = root / "state"
        self.log = root / "fake.log"
        self.bin.mkdir()
        self.state.mkdir()
        self.log.write_text("")
        refuse = '#!/bin/sh\necho "{} $*" >> "$FAKE_LOG"\nexit 97\n'
        stubs = {**_STUBS, **{name: refuse.format(name) for name in _MUST_NOT_RUN}}
        for name, body in stubs.items():
            path = self.bin / name
            path.write_text(body.replace("@LABEL@", deploy_bundle.LABEL))
            path.chmod(0o755)
        if managers:
            ids = "".join(f"{index:012x}\n" for index in range(1, managers + 1))
            (self.state / "managers").write_text(ids)
        if reload_registers:
            (self.state / "reload_registers").touch()

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STATE": str(self.state),
            "FAKE_LOG": str(self.log),
            **extra,
        }

    def run(
        self, command: list[str], *, stdin: str = "", **extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - 固定的脚本与桩程序
            command,
            input=stdin,
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def gvisor(self, **extra: str) -> subprocess.CompletedProcess[str]:
        script = resources.files("app.runner_bundle") / "files" / "install-gvisor.sh"
        return self.run(["/bin/bash", str(script)], **extra)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def reloads(self) -> list[str]:
        return [call for call in self.calls() if call in RELOADS]

    def restarts(self) -> list[str]:
        return [call for call in self.calls() if call in RESTARTS]

    def unexpected(self) -> list[str]:
        return [call for call in self.calls() if call.split(" ", 1)[0] in _MUST_NOT_RUN]


def test_gvisor_reload_registers_runsc_without_restarting_docker(tmp_path: Path) -> None:
    """runsc install 之后重新加载就登记上了：不重启 Docker，有没有管理器都一样。"""
    sandbox = Sandbox(tmp_path, managers=2, reload_registers=True)
    result = sandbox.gvisor()
    assert result.returncode == 0, result.stderr
    assert "gvisor_ready 20261019.0" in result.stdout and "docker_reloaded" in result.stdout
    calls = sandbox.calls()
    assert "runsc install" in calls
    assert len(sandbox.reloads()) == 1 and calls.index("runsc install") < calls.index(
        sandbox.reloads()[0]
    )
    assert sandbox.restarts() == [] and sandbox.unexpected() == []
    # 登记上了就不用去数管理器
    assert not any(call.startswith("docker ps") for call in calls)


def test_gvisor_does_not_restart_docker_under_running_mcp_managers(tmp_path: Path) -> None:
    """重新加载后没有登记、有运行中的管理器、没有给允许：说明原因，以 6 退出，不重启 Docker。"""
    sandbox = Sandbox(tmp_path, managers=2)
    result = sandbox.gvisor()
    assert result.returncode == runner_ssh.GVISOR_RESTART_EXIT == 6
    assert result.stderr.splitlines() == ["gvisor_needs_docker_restart 2"]
    assert "gvisor_ready" not in result.stdout
    assert len(sandbox.reloads()) == 1
    assert sandbox.restarts() == [] and sandbox.unexpected() == []
    assert f"docker ps -q --filter label={deploy_bundle.LABEL}" in sandbox.calls()
    # 工作台按这个退出码和这一行认出原因
    parsed = ssh.CommandResult(result.returncode, result.stdout, result.stderr)
    assert runner_ssh.gvisor_restart_needed(parsed) == 2
    # 别的值不算允许
    for value in ("0", "true", ""):
        assert sandbox.gvisor(MW_ALLOW_DOCKER_RESTART=value).returncode == 6
    assert sandbox.restarts() == []


def test_gvisor_restarts_docker_once_allowed(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, managers=2)
    result = sandbox.gvisor(MW_ALLOW_DOCKER_RESTART="1")
    assert result.returncode == 0, result.stderr
    assert "docker_restart_allowed 2" in result.stdout
    assert "gvisor_ready 20261019.0" in result.stdout
    assert len(sandbox.reloads()) == 1 and len(sandbox.restarts()) == 1
    assert sandbox.unexpected() == []


def test_gvisor_restarts_docker_as_before_without_mcp_managers(tmp_path: Path) -> None:
    """没有管理器：重新加载不行就重启，和原来一样，不用确认。"""
    sandbox = Sandbox(tmp_path)
    result = sandbox.gvisor()
    assert result.returncode == 0, result.stderr
    assert "gvisor_ready 20261019.0" in result.stdout
    assert "docker_restart_allowed" not in result.stdout
    assert len(sandbox.reloads()) == 1 and len(sandbox.restarts()) == 1
    assert sandbox.unexpected() == []


def test_gvisor_already_registered_touches_nothing(tmp_path: Path) -> None:
    """重新部署、升级接入包：Docker 已经认得 runsc，不接入、不重新加载、不重启。"""
    sandbox = Sandbox(tmp_path, managers=2)
    (sandbox.state / "registered").touch()
    result = sandbox.gvisor()
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["gvisor_present 20261019.0", "gvisor_ready 20261019.0"]
    assert sandbox.reloads() == [] and sandbox.restarts() == []
    assert "runsc install" not in sandbox.calls()


def test_restart_result_is_only_recognised_by_exit_code_and_marker() -> None:
    marker = "gvisor_needs_docker_restart 3\n"
    assert runner_ssh.gvisor_restart_needed(ssh.CommandResult(6, "", marker)) == 3
    assert runner_ssh.gvisor_restart_needed(ssh.CommandResult(6, "", "x\n" + marker)) == 3
    # 别的失败（退出码不同，或没有这一行）仍然是普通的安装失败
    assert runner_ssh.gvisor_restart_needed(ssh.CommandResult(5, "", marker)) is None
    assert runner_ssh.gvisor_restart_needed(ssh.CommandResult(6, marker, "")) is None
    assert runner_ssh.gvisor_restart_needed(ssh.CommandResult(6, "", "gvisor_broken")) is None
    assert runner_ssh.gvisor_restart_needed(ssh.CommandResult(0, "", "")) is None


def test_install_command_passes_the_permission_through_env() -> None:
    directory = "/home/de ploy/myworkspace-runner"
    script = "'/home/de ploy/myworkspace-runner/install.sh'"
    assert runner_ssh.install_command(directory, root=True) == f"bash {script}"
    assert runner_ssh.install_command(directory, root=False) == f"sudo -S -p '' -k bash {script}"
    assert (
        runner_ssh.install_command(directory, root=True, allow_docker_restart=True)
        == f"env MW_ALLOW_DOCKER_RESTART=1 bash {script}"
    )
    assert (
        runner_ssh.install_command(directory, root=False, allow_docker_restart=True)
        == f"sudo -S -p '' -k env MW_ALLOW_DOCKER_RESTART=1 bash {script}"
    )


def _unpack_bundle(root: Path) -> Path:
    data = runner_bundle.build(
        runner_bundle.RunnerBundleInput(
            host_name="runner-1",
            url="https://agent-gateway.myworkspace.internal:9443",
            token="mwr_x",  # noqa: S106 - 测试值
            block=[],
        )
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(root)
    directory = root / "myworkspace-runner"
    for name in ("install.sh", "install-gvisor.sh", "install-docker.sh"):
        assert os.access(directory / name, os.R_OK)
    return directory


@pytest.mark.skipif(not Path("/etc/os-release").exists(), reason="install-docker.sh 要读它")
def test_install_sh_stops_at_gvisor_and_sudo_gets_the_permission_through_env(
    tmp_path: Path,
) -> None:
    """接入包里真的 install.sh，用工作台实际发出的命令运行（假的 sudo 像真的一样清掉环境变量）：
    没有允许时以 6 停在 gVisor、runner-agent 没有被拉起；带上允许后重启 Docker 并装完。"""
    sandbox = Sandbox(tmp_path, managers=1)
    directory = str(_unpack_bundle(tmp_path))

    # 调用者自己的环境里有这个变量也没用：sudo 不会带过去，要靠命令里的 env
    plain = runner_ssh.install_command(directory, root=False)
    stopped = sandbox.run(["/bin/sh", "-c", plain], stdin="pw\n", MW_ALLOW_DOCKER_RESTART="1")
    assert stopped.returncode == 6, stopped.stderr
    assert "gvisor_needs_docker_restart 1" in stopped.stderr
    assert "runner_agent_started" not in stopped.stdout
    assert sandbox.restarts() == []
    assert not any(call.startswith("docker compose -f") for call in sandbox.calls())
    parsed = ssh.CommandResult(stopped.returncode, stopped.stdout, stopped.stderr)
    assert runner_ssh.gvisor_restart_needed(parsed) == 1

    allowed = runner_ssh.install_command(directory, root=False, allow_docker_restart=True)
    done = sandbox.run(["/bin/sh", "-c", allowed], stdin="pw\n")
    assert done.returncode == 0, done.stderr
    assert "docker_restart_allowed 1" in done.stdout and "runner_agent_started" in done.stdout
    assert len(sandbox.restarts()) == 1
    assert "docker compose -f compose.yaml up -d --remove-orphans" in sandbox.calls()
    assert sandbox.unexpected() == []
