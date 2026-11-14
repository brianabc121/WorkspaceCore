"""一键部署 MCP 管理器时上传到服务器的文件和执行的命令（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md）。

管理器的镜像没有发布到镜像仓库：在服务器上用上传的构建文件现场构建（和手动部署时
`docker compose up -d --build` 做的是同一件事）。构建文件是 services/llm-bridge 的
Dockerfile、依赖锁文件和源码，构建工作台镜像时带进来（services/api/Dockerfile）。

这里只拼文件和命令，不连服务器。写进命令和文件的值都是工作台自己生成或校验过的
（十六进制的编号、端口号、校验过的地址和网络名），没有用户输入的自由文本。
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

_FILES = Path(__file__).parent / "deploy_files"
# 工作台镜像里构建文件的位置；开发和测试时没有镜像，用仓库里的 services/llm-bridge
IMAGE_SOURCE = Path("/opt/myworkspace/llm-bridge")
REPO_SOURCE = Path(__file__).resolve().parents[4] / "llm-bridge"
SOURCE_ENV = "LLM_BRIDGE_SOURCE_DIR"
# 构建只需要这些；测试、虚拟环境、缓存不上传
SOURCE_TOP = ("Dockerfile", "pyproject.toml", "uv.lock", ".dockerignore")
SOURCE_PACKAGE = "bridge"
MAX_SOURCE_BYTES = 8 * 1024 * 1024

INSTALL_ROOT = "myworkspace-llm-bridge"
PORT_RANGE = range(18787, 18887)
BRIDGE_PORT = 8787
_SLOT = re.compile(r"^[0-9a-f]{12}$")
_NETWORK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}_integrations$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_INSTALL_DIR = re.compile(rf"^/(?:[^/\x00\n\r]+/)*{INSTALL_ROOT}/[0-9a-f]{{12}}$")
_PUBLIC_KEY = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+ myworkspace-mcp-[0-9a-f]{12}$")
# 容器、数据卷、网络都带这个标签：两个服务器页面的管理程序不处理带它的对象
LABEL = "myworkspace.mcp-manager"
# 登录账号怎么用 Docker：直接用 / 免密 sudo / sudo 要密码
ACCESS_DIRECT, ACCESS_SUDO, ACCESS_SUDO_PASSWORD = "direct", "sudo_nopasswd", "sudo_password"


class BundleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def q(value: str) -> str:
    return shlex.quote(value)


def source_dir() -> Path | None:
    """管理器构建文件所在的目录；没有时返回 None（这个工作台不能一键部署）。"""
    override = os.environ.get(SOURCE_ENV)
    candidates = [Path(override)] if override else [IMAGE_SOURCE, REPO_SOURCE]
    for path in candidates:
        if (path / "Dockerfile").is_file() and (path / SOURCE_PACKAGE).is_dir():
            return path
    return None


def source_files(root: Path) -> dict[str, tuple[bytes, int]]:
    """构建文件：相对路径（都在 src/ 下）→ (内容, 权限)。"""
    files: dict[str, tuple[bytes, int]] = {}
    for name in SOURCE_TOP:
        path = root / name
        if path.is_file():
            files[f"src/{name}"] = (path.read_bytes(), 0o644)
    for path in sorted((root / SOURCE_PACKAGE).rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files[f"src/{path.relative_to(root).as_posix()}"] = (path.read_bytes(), 0o644)
    if "src/Dockerfile" not in files or "src/uv.lock" not in files:
        raise BundleError("source_missing", "工作台里没有 MCP 管理器的构建文件")
    if sum(len(content) for content, _ in files.values()) > MAX_SOURCE_BYTES:
        raise BundleError("source_invalid", "MCP 管理器的构建文件大得不正常")
    return files


def content_tag(files: dict[str, tuple[bytes, int]]) -> str:
    """镜像的标签：构建文件内容的摘要。内容没变时重新部署用的是同一个标签。"""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name][0])
        digest.update(b"\0")
    return f"deploy-{digest.hexdigest()[:12]}"


_TAGS: dict[Path, str] = {}


def current_tag() -> str | None:
    """这个工作台带的构建文件的版本；没带时为 None。一个进程里只算一次（镜像里的文件不会变）。"""
    root = source_dir()
    if root is None:
        return None
    if root not in _TAGS:
        try:
            _TAGS[root] = content_tag(source_files(root))
        except BundleError:
            return None
    return _TAGS[root]


@dataclass(frozen=True)
class Inspect:
    home: str
    docker: bool  # 装了 Docker
    access: bool  # 这个账号现在能直接用 Docker
    compose: bool
    sudo: bool
    sudo_nopasswd: bool
    network: str
    port: int | None


def inspect_args(hostname: str | None, skip_ports: list[int]) -> str:
    name = hostname if hostname and _HOSTNAME.match(hostname) else "-"
    ports = " ".join(str(int(p)) for p in skip_ports)
    return f"{q(name)} {q(ports)}"


def inspect_command(hostname: str | None, skip_ports: list[int]) -> str:
    """只读检查脚本的调用方式（脚本从标准输入给）：参数是工作台后端容器的主机名和不要选的端口。"""
    return f"sh -s -- {inspect_args(hostname, skip_ports)}"


def inspect_script() -> str:
    return (_FILES / "inspect.sh").read_text(encoding="utf-8")


def inspect_inline(hostname: str | None, skip_ports: list[int]) -> str:
    """同一个检查脚本，写成一条命令（经 sudo 运行时标准输入要留给密码）。"""
    return f"sh -c {q(inspect_script())} sh {inspect_args(hostname, skip_ports)}"


def parse_inspect(stdout: str) -> Inspect:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    home = values.get("home", "").rstrip("/")
    if not home.startswith("/") or any(c in home for c in "\n\r\0"):
        raise BundleError("inspect_failed", "读取服务器信息失败：登录账号的主目录无效")
    network = values.get("network", "")
    if network and not _NETWORK.match(network):
        raise BundleError("inspect_failed", "读取服务器信息失败：内部网络的名字无效")
    port_text = values.get("port", "")
    port = int(port_text) if port_text.isdigit() and int(port_text) in PORT_RANGE else None
    return Inspect(
        home=home,
        docker=values.get("docker") == "yes",
        access=values.get("docker_access") == "yes",
        compose=values.get("compose") == "yes",
        sudo=values.get("sudo") == "yes",
        sudo_nopasswd=values.get("sudo_nopasswd") == "yes",
        network=network,
        port=port,
    )


def privileged(command: str, access: str) -> str:
    """登录账号不能直接用 Docker 时，整条命令交给 sudo 里的 sh 执行。

    要密码时密码从标准输入给（`-S`），`-k` 不用上一次缓存的授权——和容器服务器的安装是同一种写法。
    """
    if access == ACCESS_DIRECT:
        return command
    flags = "-n" if access == ACCESS_SUDO else "-S -p '' -k"
    return f"sudo {flags} sh -c {q(command)}"


def install_dir(home: str, slot: str) -> str:
    if not _SLOT.match(slot):
        raise ValueError("部署编号无效")
    return f"{home.rstrip('/')}/{INSTALL_ROOT}/{slot}"


def project_name(slot: str) -> str:
    return f"mw-mcp-{slot}"


def alias(slot: str) -> str:
    return f"mcp-{slot}"


@dataclass(frozen=True)
class Network:
    mode: str  # same_host / tunnel
    base_url: str  # 登记的地址；tunnel 时是服务器本机地址上的那个端口
    service: str  # 写进 compose.yaml 里服务的那一段
    top: str  # 写进 compose.yaml 里 networks 的那一段
    port: int | None = None


def plan_network(info: Inspect, *, slot: str) -> Network:
    """管理器的网络怎么接。

    - 和工作台同一台机器：加入工作台的内部网络，工作台按网络里的名字访问，端口不开到宿主机上。
    - 其他情况：端口只开在这台服务器的本机地址上，工作台经专用的 SSH 通道访问；
      局域网和公网上都没有这个端口，同机的其他容器也连不到它。
    """
    if info.network:
        name = alias(slot)
        service = (
            "    networks:\n"
            "      default: {}\n"
            "      workspace-integrations:\n"
            "        aliases:\n"
            f"          - {name}\n"
        )
        top = f"  workspace-integrations:\n    name: {info.network}\n    external: true\n"
        return Network("same_host", f"http://{name}:{BRIDGE_PORT}", service, top)
    if info.port is None:
        raise BundleError("no_free_port", "这台服务器上 18787 ~ 18886 的端口都被占用了")
    service = f'    ports:\n      - "127.0.0.1:{info.port}:{BRIDGE_PORT}"\n'
    return Network("tunnel", f"http://127.0.0.1:{info.port}", service, "", info.port)


def tunnel_comment(slot: str) -> str:
    if not _SLOT.match(slot):
        raise ValueError("部署编号无效")
    return f"myworkspace-mcp-{slot}"


def tunnel_line(public_key: str, port: int) -> str:
    """装进 authorized_keys 的那一行：这把密钥不能执行命令、不能开终端，只能转发到这一个端口。

    `restrict` 关掉终端、代理转发、X11 等，再单独放开端口转发并用 `permitopen` 限定目的地；
    `restrict` 本身不禁止执行命令，所以再加一条固定命令 `/bin/false`——不管客户端要执行什么，
    跑的都是它。在 OpenSSH 9.6 上实测过，
    见 docs/decisions/2026-11-14-mcp-manager-deploy-verification.md。
    """
    if not _PUBLIC_KEY.match(public_key) or port not in PORT_RANGE:
        raise ValueError("转发密钥或端口无效")
    options = f'restrict,port-forwarding,permitopen="127.0.0.1:{port}",command="/bin/false"'
    return f"{options} {public_key}"


def compose_file(slot: str, tag: str, network: Network) -> bytes:
    if not _SLOT.match(slot) or not re.fullmatch(r"deploy-[0-9a-f]{12}", tag):
        raise ValueError("部署编号或镜像标签无效")
    text = (_FILES / "compose.yaml").read_text(encoding="utf-8")
    return (
        text.replace("@PROJECT@", project_name(slot))
        .replace("@TAG@", tag)
        .replace("@SLOT@", slot)
        .replace("@SERVICE_NET@\n", network.service)
        .replace("@TOP_NET@\n", network.top)
        .encode()
    )


def env_file(token: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{32,200}", token):
        raise ValueError("管理令牌的格式无效")
    return f"BRIDGE_ADMIN_TOKEN={token}\n".encode()


def bundle(
    source: dict[str, tuple[bytes, int]], slot: str, network: Network, token: str
) -> dict[str, tuple[bytes, int]]:
    """要上传的全部文件：构建文件、compose.yaml、bridge.env（权限 600）。"""
    return {
        **source,
        "compose.yaml": (compose_file(slot, content_tag(source), network), 0o644),
        "bridge.env": (env_file(token), 0o600),
    }


# ---- 在服务器上执行的命令：固定的几条，目录都是工作台自己生成的 ----
def _dir(directory: str) -> str:
    if not _INSTALL_DIR.match(directory) or "/../" in directory:
        raise ValueError("只能操作 MCP 管理器的部署目录")
    return q(directory)


def prepare_command(directory: str) -> str:
    """建目录（权限 700），清掉上一次上传的构建文件（重新部署时旧文件不留下）。"""
    d = _dir(directory)
    return f"mkdir -p {d} && chmod 700 {d} && rm -rf -- {d}/src"


def compose_command(directory: str, *args: str) -> str:
    return f"cd {_dir(directory)} && docker compose " + " ".join(args)


def remove_dir_command(directory: str) -> str:
    """删除部署目录；上一级目录空了就一起删掉（rmdir 只删空目录）。"""
    d = _dir(directory)
    parent = q(directory.rsplit("/", 1)[0])
    return f"rm -rf -- {d} && {{ rmdir -- {parent} 2>/dev/null || true; }}"
