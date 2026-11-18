"""远程开发容器的固定设置（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 5、21 节）。

命令行版本必须和 MCP 管理器镜像里的一致（services/llm-bridge/Dockerfile、docs/versions.md）：
管理器解析的是锁定版本的输出格式。tests/test_llm_bridge_remote.py 核对两边一致。
"""

from __future__ import annotations

from typing import Any, Literal

OWNER = "llm_bridge"

# 平台 → 锁定的命令行版本（npm 包）
CLI_PACKAGES: dict[str, tuple[str, str]] = {
    "codex": ("@openai/codex", "0.169.0"),
    "claude": ("@anthropic-ai/claude-code", "2.1.323"),
    "workbuddy": ("@tencent-ai/codebuddy-code", "2.184.0"),
}
CLI_VERSIONS: dict[str, str] = {p: v for p, (_, v) in CLI_PACKAGES.items()}
# 平台 → 容器里的命令（/opt/mw/cli/bin 下）
PROGRAMS: dict[str, str] = {"codex": "codex", "claude": "claude", "workbuddy": "codebuddy"}
CLI_DIR = "/opt/mw/cli"
CLI_BIN = f"{CLI_DIR}/bin"
PATH_ENV = f"{CLI_BIN}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# 内置“大模型开发环境”的基础镜像（核验记录 2026-11-18：命令行目录在 gVisor、非 root 下能跑）
BASE_IMAGE = (
    "node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee"
)
_APT = (
    "RUN apt-get update && apt-get install -y --no-install-recommends "
    "git ca-certificates ripgrep python3 python3-venv build-essential procps curl less "
    "openssh-client && rm -rf /var/lib/apt/lists/*"
)
_CLI = (
    f"RUN mkdir -p {CLI_BIN} && cp /usr/local/bin/node {CLI_BIN}/node "
    f"&& npm install -g --prefix {CLI_DIR} --no-audit --no-fund "
    + " ".join(f"{name}@{version}" for name, version in CLI_PACKAGES.values())
    + " && npm cache clean --force"
)
BUILTIN_STEPS: list[str] = [_APT, _CLI, f"ENV PATH={PATH_ENV}"]
BUILTIN_DESCRIPTION = (
    "大模型开发环境（MCP 管理器的远程开发容器用）：Node 22、Python 3、Git、构建工具，"
    "Codex / Claude Code / CodeBuddy Code 命令行装在 /opt/mw/cli"
)


# 托管进程的外壳（wc、tail、head、mv、mkfifo、setsid）、辅助命令（timeout、date）、文件浏览
# （realpath、stat、base64、find -printf）要用的基础命令。内置镜像里都有；自定义镜像或模板的基础镜像
# 缺了，构建时用它自带的包管理器补装：只装缺的那几个包（例如 ubi-minimal 自带 coreutils-single，
# 再装 coreutils 会冲突）。装完再查一遍，还缺就让构建失败、写明原因（人类 2026-11-18 要求）。
BASE_TOOLS = (
    "timeout", "realpath", "stat", "base64", "head", "tail", "wc", "mv", "date", "mkfifo",
    "setsid", "find",
)  # fmt: skip
TOOLS_SCRIPT = (
    "set -e; "
    "missing() { m=; for c in " + " ".join(BASE_TOOLS) + "; do "
    'command -v "$c" >/dev/null 2>&1 || m="$m $c"; done; '
    "find / -maxdepth 0 -printf '' >/dev/null 2>&1 || m=\"$m find-printf\"; echo $m; }; "
    'miss=$(missing); if [ -z "$miss" ]; then exit 0; fi; '
    "pkgs=; for c in $miss; do case $c in "
    "find|find-printf) p=findutils;; setsid) p=util-linux;; *) p=coreutils;; esac; "
    'case " $pkgs " in *" $p "*) ;; *) pkgs="$pkgs $p";; esac; done; '
    'echo "基础镜像缺这些命令：$miss；补装：$pkgs"; '
    "if command -v apt-get >/dev/null 2>&1; then apt-get update "
    "&& apt-get install -y --no-install-recommends $pkgs && rm -rf /var/lib/apt/lists/*; "
    "elif command -v dnf >/dev/null 2>&1; then dnf install -y $pkgs && dnf clean all; "
    "elif command -v microdnf >/dev/null 2>&1; then "
    "microdnf install -y $pkgs && microdnf clean all; "
    "elif command -v yum >/dev/null 2>&1; then yum install -y $pkgs && yum clean all; "
    "elif command -v zypper >/dev/null 2>&1; then zypper -n install $pkgs && zypper clean -a; "
    "elif command -v apk >/dev/null 2>&1; then apk add --no-cache $pkgs; "
    'else echo "没有能补装的包管理器（apt-get、dnf、microdnf、yum、zypper、apk），'
    '请换一个带这些命令的基础镜像" >&2; exit 1; fi; '
    'left=$(missing); if [ -n "$left" ]; then echo "补装之后还缺：$left" >&2; exit 1; fi'
)


def derived_steps(cli_image: str) -> list[str]:
    """在别的镜像上加一层命令行：先补装缺的基础命令（用 root 装；容器照常按设定的用户运行），
    再从内置镜像拷整个命令行目录（自带 Node 运行时）。"""
    return [
        "USER root",
        f"RUN {TOOLS_SCRIPT}",
        f"COPY --from={cli_image} {CLI_DIR} {CLI_DIR}",
        f"ENV PATH={PATH_ENV}",
    ]


# 资源预设（方案 5.3）
RESOURCE_PRESETS: dict[str, dict[str, int | float]] = {
    "small": {"cpus": 1, "memory_mb": 2048, "disk_gb": 10},
    "medium": {"cpus": 2, "memory_mb": 4096, "disk_gb": 20},
    "large": {"cpus": 4, "memory_mb": 8192, "disk_gb": 50},
}

# 网络（方案 5.4）。域名只列核验过的（核验记录 V3：不登录时启动会连的）；
# 订阅账号的登录、刷新用到的还没核验，所以页面上暂时推荐“全部外网”
NetworkChoice = Literal["models", "models_registries", "all"]
PLATFORM_DOMAINS: dict[str, list[str]] = {
    "claude": ["api.anthropic.com"],
    "codex": ["api.openai.com", "chatgpt.com", "api.github.com"],
    "workbuddy": ["www.codebuddy.ai", "download.codebuddy.cn"],
}
REGISTRY_DOMAINS: list[str] = [
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "proxy.golang.org",
    "sum.golang.org",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "deb.debian.org",
    "github.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
]


def runner_network(choice: str, extra: list[str], repo_hosts: list[str]) -> dict[str, Any]:
    """页面上的网络选项 → 容器服务器的规格（network、domains）。"""
    if choice == "all":
        return {"network": "all", "domains": []}
    domains: list[str] = []
    for names in PLATFORM_DOMAINS.values():
        domains += names
    if choice == "models_registries":
        domains += REGISTRY_DOMAINS
    domains += repo_hosts + extra
    seen: list[str] = []
    for name in domains:
        name = name.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return {"network": "allowlist", "domains": seen[:200]}


# 容器里的路径（21.1）
SESSIONS_ROOT = "/workspace/sessions"
HOME = "/workspace/.home"
CONFIG_DIRS = {
    "claude": f"{HOME}/.claude",
    "codex": f"{HOME}/.codex",
    "workbuddy": f"{HOME}/.codebuddy",
}

# 工作空间一级的上限（D8）。设置页面以后再做，现在是固定值
LIMITS = {"count": 5, "cpus": 16.0, "memory_mb": 32768, "disk_gb": 200}
IDLE_CHOICES = (0, 30, 120)  # 分钟；0 = 不停止
