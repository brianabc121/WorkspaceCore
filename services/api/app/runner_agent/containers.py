"""按工作台下发的选项生成容器参数（docs/v2/RUNNER_PLAN.md 第 8.1、8.2 节）。

智能体决定镜像、运行时、用户、网络、资源；**宿主保护在这里强制**，与工作台下发什么无关：
不开特权、不加 Docker 默认之外的 capability、始终 no-new-privileges；
不挂 Docker socket、不挂宿主目录（只挂工作台建的命名数据卷）；
不用宿主网络、宿主 PID / IPC；只连这个环境自己的 internal 网络。
指令里不能带任意 Docker 参数：这里只认固定的几个字段，其余一律忽略。
"""

from __future__ import annotations

import re
from typing import Any

LABEL_RUNNER = "myworkspace.runner"
LABEL_ENV = "myworkspace.env"
LABEL_WORKSPACE = "myworkspace.workspace"
LABEL_TEMPLATE = "myworkspace.template"
LABEL_ROLE = "myworkspace.role"
LABEL_LIFETIME = "myworkspace.lifetime"

RUNTIMES = {"gvisor": "runsc", "runc": "runc", "kvm": "runc"}
USERS = {"user": "1000:1000", "root": "0:0"}
NETWORKS = ("none", "allowlist", "all")
WORKDIR = "/workspace"
HOME = "/workspace/.home"
PROXY_ALIAS = "egress"
PROXY_PORT = 3128
# 没有启动命令时保持容器运行（收到 TERM 立即退出）
KEEPALIVE = ["sh", "-c", "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"]
RESERVED_ENV = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "HOME"}
_NAME = re.compile(r"^mw-(env|vol|net)-[0-9a-f]{12}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SECRET = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MIN_MEMORY = 64 * 1024 * 1024


class ParamError(ValueError):
    """下发的选项不合法：runner-agent 拒绝执行，原因回报给工作台。"""


def check_name(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value) or not value.startswith(f"mw-{kind}-"):
        raise ParamError(f"名称不合法：{str(value)[:40]}")
    return value


def labels(payload: dict[str, Any], role: str = "env") -> dict[str, str]:
    return {
        LABEL_RUNNER: "1",
        LABEL_ENV: str(payload["env_id"]),
        LABEL_WORKSPACE: str(payload.get("workspace_id") or ""),
        LABEL_ROLE: role,
    }


def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ParamError(f"{what} 不是数字")
    if not low <= value <= high:
        raise ParamError(f"{what} 超出范围")
    return float(value)


def user_ids(payload: dict[str, Any]) -> tuple[int, int]:
    return (0, 0) if payload.get("user") == "root" else (1000, 1000)


def proxy_env(network_mode: str) -> list[str]:
    if network_mode == "none":
        return []
    url = f"http://{PROXY_ALIAS}:{PROXY_PORT}"
    no_proxy = "localhost,127.0.0.1,::1"
    return [
        f"HTTP_PROXY={url}",
        f"HTTPS_PROXY={url}",
        f"http_proxy={url}",
        f"https_proxy={url}",
        f"NO_PROXY={no_proxy}",
        f"no_proxy={no_proxy}",
    ]


def env_list(payload: dict[str, Any], extra: dict[str, Any] | None = None) -> list[str]:
    out: list[str] = []
    merged: dict[str, Any] = dict(payload.get("env") or {})
    merged.update(extra or {})
    for key, value in merged.items():
        if not isinstance(key, str) or not _ENV_KEY.match(key):
            raise ParamError(f"环境变量名不合法：{str(key)[:64]}")
        if (
            key.upper() in RESERVED_ENV
            or key.startswith("MYWS_")
            or key.lower()
            in {
                "http_proxy",
                "https_proxy",
                "no_proxy",
            }
        ):
            continue
        out.append(f"{key}={str(value)[:4096]}")
    out.append(f"HOME={HOME}")
    out.extend(proxy_env(str(payload.get("network_mode") or "none")))
    return out


def container_config(payload: dict[str, Any], *, host_memory: int | None = None) -> dict[str, Any]:
    """环境容器的 /containers/create 请求体。"""
    image = payload.get("image")
    if not isinstance(image, str) or not image or len(image) > 512:
        raise ParamError("镜像不合法")
    runtime = payload.get("runtime")
    if runtime not in RUNTIMES:
        raise ParamError(f"运行时不合法：{runtime}")
    user = payload.get("user")
    if user not in USERS:
        raise ParamError(f"用户不合法：{user}")
    network_mode = payload.get("network_mode")
    if network_mode not in NETWORKS:
        raise ParamError(f"网络选项不合法：{network_mode}")
    network = check_name(payload.get("network"), "net")
    volume = check_name(payload.get("volume"), "vol")
    memory = int(_number(payload.get("memory_mb"), 64, 1_048_576, "memory_mb")) * 1024 * 1024
    if host_memory:
        memory = min(memory, host_memory)
    memory = max(memory, MIN_MEMORY)
    cpus = _number(payload.get("cpus"), 0.1, 256, "cpus")
    pids = int(_number(payload.get("pids"), 16, 100_000, "pids"))
    uid, gid = user_ids(payload)
    start = payload.get("start_command")
    if start is not None and (
        not isinstance(start, list) or not start or not all(isinstance(a, str) for a in start)
    ):
        raise ParamError("start_command 不合法")
    host_config: dict[str, Any] = {
        "Runtime": RUNTIMES[runtime],
        "Privileged": False,
        "CapDrop": ["ALL"] if user == "user" else [],
        "CapAdd": [],
        "SecurityOpt": ["no-new-privileges:true"],
        "Init": True,
        "Memory": memory,
        "MemorySwap": memory,
        "NanoCpus": int(cpus * 1_000_000_000),
        "PidsLimit": pids,
        "NetworkMode": network,
        "IpcMode": "private",
        "PidMode": "",
        "Binds": [],
        "Mounts": [{"Type": "volume", "Source": volume, "Target": WORKDIR}],
        "Tmpfs": {
            "/tmp": "rw,nosuid,nodev,size=1g",  # noqa: S108 -- 容器里的 tmpfs，不是本机目录
            "/run/secrets": f"rw,nosuid,nodev,noexec,size=1m,mode=0700,uid={uid},gid={gid}",
        },
        "Devices": [],
        "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "2"}},
        "RestartPolicy": {"Name": "no"},
    }
    if runtime == "kvm":
        host_config["Devices"] = [
            {"PathOnHost": "/dev/kvm", "PathInContainer": "/dev/kvm", "CgroupPermissions": "rwm"}
        ]
    config: dict[str, Any] = {
        "Image": image,
        "User": USERS[user],
        "Env": env_list(payload),
        "Labels": {**labels(payload), LABEL_LIFETIME: str(payload.get("lifetime") or "session")},
        "HostConfig": host_config,
        # 不设 WorkingDir：镜像里没有 /workspace 时 Docker 会按 root 建它并把属主复制到空数据卷上
        # （实测），uid 1000 就写不进去了。exec 时再设工作目录
    }
    if start is None:
        config["Entrypoint"] = KEEPALIVE[:1]
        config["Cmd"] = KEEPALIVE[1:]
    else:
        config["Cmd"] = start
    return config


def helper_config(
    image: str, payload: dict[str, Any], mounts: list[dict[str, Any]], cmd: list[str] | None
) -> dict[str, Any]:
    """助手容器（改属主、复制数据卷、读写文件）：root、不联网、只挂工作台的数据卷。"""
    for mount in mounts:
        check_name(mount["Source"], "vol")
    return {
        "Image": image,
        "User": "0:0",
        "Entrypoint": cmd[:1] if cmd else ["true"],
        "Cmd": cmd[1:] if cmd else [],
        "Labels": labels(payload, role="helper"),
        "HostConfig": {
            "Privileged": False,
            "NetworkMode": "none",
            "SecurityOpt": ["no-new-privileges:true"],
            "CapDrop": ["ALL"],
            "CapAdd": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": m["Source"],
                    "Target": m["Target"],
                    "ReadOnly": bool(m.get("ReadOnly")),
                }
                for m in mounts
            ],
            "Memory": 256 * 1024 * 1024,
            "PidsLimit": 64,
            "RestartPolicy": {"Name": "no"},
        },
    }


def secret_command(name: str) -> list[str]:
    """把密钥写进 /run/secrets/<名称>（值经 exec 的环境变量传入，不出现在命令行里）。"""
    if not _SECRET.match(name):
        raise ParamError(f"密钥名称不合法：{name[:64]}")
    return [
        "sh",
        "-c",
        f'umask 077 && printf %s "$MYWS_SECRET_VALUE" > /run/secrets/{name}',
    ]


def exec_workdir(relative: Any) -> str:
    if relative in (None, ""):
        return WORKDIR
    if not isinstance(relative, str) or relative.startswith("/") or ".." in relative.split("/"):
        raise ParamError("工作目录要是 /workspace 下的相对路径")
    return f"{WORKDIR}/{relative}"


__all__ = [
    "KEEPALIVE",
    "LABEL_ENV",
    "LABEL_LIFETIME",
    "LABEL_ROLE",
    "LABEL_RUNNER",
    "LABEL_TEMPLATE",
    "LABEL_WORKSPACE",
    "PROXY_ALIAS",
    "PROXY_PORT",
    "ParamError",
    "check_name",
    "container_config",
    "env_list",
    "exec_workdir",
    "helper_config",
    "labels",
    "secret_command",
    "user_ids",
]
