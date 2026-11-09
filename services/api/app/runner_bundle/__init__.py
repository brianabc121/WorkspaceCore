"""运行服务器接入包（T-0136，docs/v2/RUNNER_PLAN.md 第 9 节）。

每次生成都签发新的运行服务器凭证；令牌、客户端证书私钥只出现在这个 zip 里，工作台只存哈希与指纹。

| 路径 | 权限 | 用途 |
|---|---|---|
| `runner.env` | 600 | 工作台地址、运行服务器凭证、要拦截的地址 |
| `compose.yaml` | 644 | runner-agent（锁定 digest 的 python 镜像，挂 Docker socket，无端口） |
| `agent/runner_agent/*.py` | 644 | runner-agent 的代码（只用标准库，来自 app/runner_agent/） |
| `install*.sh` | 755 | 以 root 运行：Docker 29.9.1、gVisor 20261019.0、启动 |
| `tls/ca.pem` | 644 | 工作台内部 CA（接入入口用它签发的证书时需要） |
| `tls/client.crt`、`tls/client.key` | 644 / 600 | 外网入口：客户端证书 |
| `CHECKLIST.md` | 644 | 部署检查清单 |
"""

import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from urllib.parse import urlsplit

from app import runner_agent

# runner-agent 与助手容器用的镜像（锁定 digest；docs/decisions/2026-11-06-runner-verification.md）
AGENT_IMAGE = (
    "python:3.12.15-slim-trixie"
    "@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704"
)
# runner-agent 的全部模块：新增模块要加在这里，否则接入包里缺文件、agent 起不来（有测试）
AGENT_MODULES = (
    "__init__",
    "__main__",
    "agent",
    "client",
    "containers",
    "docker",
    "metrics",
    "proxy",
)
_FILES = resources.files("app.runner_bundle") / "files"
_AGENT = resources.files("app.runner_agent")
_DOCKER = resources.files("app.agent_provisioning") / "files" / "install-docker.sh"


@dataclass(frozen=True)
class RunnerBundleInput:
    host_name: str
    url: str
    token: str
    block: list[str]
    ca_pem: str | None = None
    client_cert_pem: str | None = None
    client_key_pem: str | None = None
    # 入口主机名不在公网 DNS 里（没有配置域名，T-0088）：在 runner-agent 容器里映射到这个地址
    address: str | None = None


def _add(zf: zipfile.ZipFile, name: str, content: str, mode: int, when: datetime) -> None:
    info = zipfile.ZipInfo(f"myworkspace-runner/{name}", date_time=when.timetuple()[:6])
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, content)


def _env(data: RunnerBundleInput) -> str:
    lines = [
        f"# 运行服务器「{data.host_name}」的凭证：只在这个文件里出现，重新下载接入包会作废",
        f"MYWS_RUNNER_URL={data.url}",
        f"MYWS_RUNNER_TOKEN={data.token}",
        f"MYWS_RUNNER_BLOCK={','.join(data.block)}",
    ]
    if data.ca_pem:
        lines.append("MYWS_RUNNER_CA_FILE=/tls/ca.pem")
    if data.client_cert_pem and data.client_key_pem:
        lines.append("MYWS_RUNNER_CERT_FILE=/tls/client.crt")
        lines.append("MYWS_RUNNER_KEY_FILE=/tls/client.key")
    return "\n".join(lines) + "\n"


def _compose(data: RunnerBundleInput) -> str:
    text = (_FILES / "compose.yaml").read_text().replace("__IMAGE__", AGENT_IMAGE)
    host = urlsplit(data.url).hostname
    hosts = (
        "    # 入口主机名不在公网 DNS 里：指向工作台服务器\n"
        f'    extra_hosts: ["{host}:{data.address}"]\n'
        if data.address and host
        else ""
    )
    return text.replace("    # @EXTRA_HOSTS@\n", hosts)


def build(data: RunnerBundleInput, *, now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _add(zf, "runner.env", _env(data), 0o600, now)
        compose = _compose(data)
        _add(zf, "compose.yaml", compose, 0o644, now)
        for module in AGENT_MODULES:
            source = (_AGENT / f"{module}.py").read_text()
            _add(zf, f"agent/runner_agent/{module}.py", source, 0o644, now)
        _add(zf, "install.sh", (_FILES / "install.sh").read_text(), 0o755, now)
        _add(zf, "install-gvisor.sh", (_FILES / "install-gvisor.sh").read_text(), 0o755, now)
        _add(zf, "install-docker.sh", _DOCKER.read_text(), 0o755, now)
        _add(zf, "CHECKLIST.md", (_FILES / "CHECKLIST.md").read_text(), 0o644, now)
        _add(zf, "tls/ca.pem", data.ca_pem or "", 0o644, now)
        if data.client_cert_pem and data.client_key_pem:
            _add(zf, "tls/client.crt", data.client_cert_pem, 0o644, now)
            _add(zf, "tls/client.key", data.client_key_pem, 0o600, now)
    return buffer.getvalue()


__all__ = ["AGENT_IMAGE", "AGENT_MODULES", "RunnerBundleInput", "build", "runner_agent"]
