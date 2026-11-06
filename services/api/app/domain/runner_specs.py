"""运行服务器的规格、默认值与限额（docs/v2/RUNNER_PLAN.md 第 4、6、8 节）。

智能体决定镜像、运行时、用户、网络与资源（D1、D11）；这里只把选项规整成固定的字段、补上默认值，
再按限额检查资源。宿主保护（不开特权、不挂 socket 与宿主目录、私有网段不可达）不在这里，
由 runner-agent 生成容器参数时强制（app/runner_agent/containers.py），不看指令里的值。
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# 每个实例的默认限额（第 8.4 节）；管理员在实例卡片上改的值存在 agent_instances.runner_limits
DEFAULT_LIMITS: dict[str, float] = {
    "envs": 3,  # 同时运行的环境
    "persistent": 2,  # 其中常驻
    "memory_mb": 16384,
    "cpus": 8,
    "disk_gb": 100,
    "execs_per_hour": 120,
    "run_hours_per_day": 8,
}
# 没说的时候每个环境的资源
DEFAULT_RESOURCES: dict[str, float] = {"memory_mb": 2048, "cpus": 2, "pids": 1024, "disk_gb": 20}
# 回收策略的默认值（第 6.1、6.4 节）；runner_hosts.policy 里可以覆盖
DEFAULT_POLICY: dict[str, float] = {
    "session_idle_minutes": 15,
    "session_archive_hours": 24,
    "persistent_idle_minutes": 30,
    "persistent_archive_days": 14,
    "archive_retention_days": 30,
    "failed_once_keep_minutes": 30,
    "startup_factor": 20,  # 空闲 → 停止至少是启动耗时的这么多倍
    "max_idle_minutes": 120,
    "mem_high": 0.85,
    "mem_low": 0.70,
    "disk_high": 0.80,
    "disk_low": 0.65,
    "disk_emergency": 0.95,
    "snapshot_max_gb": 5,
    "grace_minutes": 5,  # 一次性、会话的待删除宽限期
    "persistent_grace_hours": 24,
    "session_max_hours": 12,
}
RUNTIMES = ("gvisor", "runc", "kvm")
USERS = ("user", "root")
NETWORKS = ("none", "allowlist", "all")
LIMIT_KEYS = tuple(DEFAULT_LIMITS)
MAX_RUN_SECONDS = 7200
DEFAULT_RUN_SECONDS = 1800
SYNC_WAIT_SECONDS = 55
OUTPUT_LIMIT = 1024 * 1024  # 运行记录里最多存 1 MB
OUTPUT_RETURN = 16 * 1024  # 工具最多返回最后 16 KB
FILE_LIMIT = 20 * 1024 * 1024
WORKDIR = "/workspace"
# 环境里不能被覆盖的变量：出口代理与 HOME 由 runner-agent 设置
RESERVED_ENV = frozenset(
    {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "HOME"}
)
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_DOMAIN = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@+-]{0,511}$", re.IGNORECASE)


class SpecError(ValueError):
    """规格不合法：说明写给智能体看。"""


def limits_for(overrides: dict[str, Any] | None) -> dict[str, float]:
    limits = dict(DEFAULT_LIMITS)
    for key, value in (overrides or {}).items():
        if key in limits and isinstance(value, int | float) and not isinstance(value, bool):
            limits[key] = float(value)
    return limits


def policy_for(overrides: dict[str, Any] | None) -> dict[str, float]:
    policy = dict(DEFAULT_POLICY)
    for key, value in (overrides or {}).items():
        if key in policy and isinstance(value, int | float) and not isinstance(value, bool):
            policy[key] = float(value)
    return policy


def clean_domains(domains: list[str]) -> list[str]:
    out: list[str] = []
    for raw in domains:
        domain = raw.strip().lower().rstrip(".")
        if not domain:
            continue
        if not _DOMAIN.match(domain) or len(domain) > 253:
            raise SpecError(f"域名不合法：{raw[:80]}（写成 example.com 或 *.example.com）")
        if domain not in out:
            out.append(domain)
    return out


def clean_env(env: dict[str, str]) -> dict[str, str]:
    if len(env) > 50:
        raise SpecError("环境变量最多 50 个")
    out: dict[str, str] = {}
    for key, value in env.items():
        if not _ENV_KEY.match(key):
            raise SpecError(f"环境变量名不合法：{key[:64]}")
        if key in RESERVED_ENV or key.startswith("MYWS_"):
            raise SpecError(f"{key} 由运行服务器设置，不能改")
        if len(value) > 4096:
            raise SpecError(f"环境变量 {key} 太长（最多 4096 字）")
        out[key] = value
    return out


def clean_relative(path: str, *, allow_root: bool = True) -> str:
    """/workspace 下的相对路径：不能是绝对路径，不能有 ..。"""
    text = path.strip().replace("\\", "/")
    if text.startswith(WORKDIR + "/"):
        text = text[len(WORKDIR) + 1 :]
    elif text == WORKDIR:
        text = ""
    text = text.strip("/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or text.startswith("/"):
        raise SpecError(f"路径要在 {WORKDIR} 里面：{path[:120]}")
    if not parts and not allow_root:
        raise SpecError("要给一个文件路径")
    joined = "/".join(parts)
    if len(joined) > 500:
        raise SpecError("路径太长")
    return joined


def command_list(command: list[str] | str) -> list[str]:
    """参数数组原样；一段字符串当 shell 脚本（sh -lc）。"""
    if isinstance(command, str):
        if not command.strip():
            raise SpecError("命令不能是空的")
        if len(command) > 100_000:
            raise SpecError("命令太长")
        return ["sh", "-lc", command]
    if not command or not all(isinstance(part, str) for part in command):
        raise SpecError("命令要是非空的字符串数组")
    if sum(len(part) for part in command) > 100_000 or len(command) > 1000:
        raise SpecError("命令太长")
    return list(command)


def command_text(command: list[str]) -> str:
    """执行记录里的命令（再经过密钥脱敏）。"""
    if len(command) == 3 and command[:2] == ["sh", "-lc"]:
        return command[2][:4000]
    return " ".join(command)[:4000]


def normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """把模板或 env_create 给的规格补全成固定字段（配置记录也是这个形状）。"""
    image = str(spec.get("image") or "").strip()
    if not _IMAGE.match(image):
        raise SpecError("镜像写法不合法（例如 python:3.12-slim 或 ghcr.io/org/app:1.0）")
    steps = [str(s) for s in spec.get("steps") or []]
    if len(steps) > 200 or any(len(s) > 8000 for s in steps):
        raise SpecError("安装步骤最多 200 行，每行最多 8000 字")
    if any(s.lstrip().upper().startswith("FROM ") for s in steps):
        raise SpecError("安装步骤里不用写 FROM：基础镜像写在 image 里")
    runtime = spec.get("runtime") or "gvisor"
    user = spec.get("user") or "user"
    network = spec.get("network") or "none"
    if runtime not in RUNTIMES or user not in USERS or network not in NETWORKS:
        raise SpecError("runtime / user / network 的取值不对")
    domains = clean_domains(list(spec.get("domains") or []))
    if network == "allowlist" and not domains:
        raise SpecError("network 是 allowlist 时要给 domains")
    start = spec.get("start_command")
    start_command = command_list(start) if start else None
    resources: dict[str, float] = {}
    for key, default in DEFAULT_RESOURCES.items():
        value = spec.get(key)
        resources[key] = float(value) if isinstance(value, int | float) else default
    persist = [clean_relative(p) for p in spec.get("persist") or []]
    secrets = [str(s) for s in spec.get("secrets") or []]
    if len(secrets) > 20 or any(not s or len(s) > 128 for s in secrets):
        raise SpecError("密钥最多 20 个，写密钥库授权给你的名称")
    return {
        "image": image,
        "steps": steps,
        "start_command": start_command,
        "runtime": runtime,
        "user": user,
        "network": network,
        "domains": domains,
        "memory_mb": int(resources["memory_mb"]),
        "cpus": float(resources["cpus"]),
        "pids": int(resources["pids"]),
        "disk_gb": int(resources["disk_gb"]),
        "env": clean_env({str(k): str(v) for k, v in (spec.get("env") or {}).items()}),
        "secrets": secrets,
        "persist": [p for p in persist if p],
        "lifetime": spec.get("lifetime") or "session",
    }


def non_default(spec: dict[str, Any]) -> list[str]:
    """页面上要标出来、并通知管理员一次的非默认选项（第 8.6 节）。"""
    notes: list[str] = []
    if spec.get("runtime") in ("runc", "kvm"):
        notes.append(f"运行时 {spec['runtime']}")
    if spec.get("user") == "root":
        notes.append("root 用户")
    if spec.get("network") == "all":
        notes.append("全部外网")
    return notes


# ---------------------------------------------------------------- 删除条件


@dataclass(frozen=True)
class Condition:
    kind: str
    ref_id: uuid.UUID | None = None
    at: datetime | None = None
    seconds: int | None = None


def parse_conditions(items: list[str]) -> list[Condition]:
    """`run_finished`、`released`、`task_done:<任务ID>`、`issue_closed:<IssueID>`、
    `project_archived:<项目ID>`、`at:<ISO 时间>`、`idle:<分钟数>`。"""
    out: list[Condition] = []
    for raw in items[:20]:
        kind, _, value = raw.strip().partition(":")
        if kind in ("run_finished", "released") and not value:
            out.append(Condition(kind))
        elif kind in ("task_done", "issue_closed", "project_archived"):
            try:
                out.append(Condition(kind, ref_id=uuid.UUID(value)))
            except ValueError:
                raise SpecError(f"{kind} 后面要跟 ID：{raw[:80]}") from None
        elif kind == "at":
            try:
                when = datetime.fromisoformat(value)
            except ValueError:
                raise SpecError(f"时间写法不对：{raw[:80]}") from None
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            out.append(Condition(kind, at=when))
        elif kind == "idle":
            minutes = value.removesuffix("m")
            if not minutes.isdigit() or not 1 <= int(minutes) <= 60 * 24 * 90:
                raise SpecError(f"idle 后面跟分钟数：{raw[:80]}")
            out.append(Condition(kind, seconds=int(minutes) * 60))
        else:
            raise SpecError(f"不认识的删除条件：{raw[:80]}")
    return out


def condition_text(condition: Condition) -> str:
    if condition.ref_id is not None:
        return f"{condition.kind}:{condition.ref_id}"
    if condition.at is not None:
        return f"at:{condition.at.isoformat()}"
    if condition.seconds is not None:
        return f"idle:{condition.seconds // 60}"
    return condition.kind


# ---------------------------------------------------------------- 名称与时间


def short(env_id: uuid.UUID) -> str:
    return env_id.hex[:12]


def names(env_id: uuid.UUID) -> dict[str, str]:
    tag = short(env_id)
    return {"container": f"mw-env-{tag}", "volume": f"mw-vol-{tag}", "network": f"mw-net-{tag}"}


def idle_stop_after(lifetime: str, start_seconds: float | None, policy: dict[str, float]) -> float:
    """空闲多少秒后停止（第 6.1 节）：类别默认与启动耗时 × 倍数取大，最多 max_idle_minutes。"""
    base = policy["persistent_idle_minutes" if lifetime == "persistent" else "session_idle_minutes"]
    seconds = base * 60
    if start_seconds:
        seconds = max(seconds, start_seconds * policy["startup_factor"])
    return min(seconds, policy["max_idle_minutes"] * 60)


def now() -> datetime:
    return datetime.now(UTC)


def later(seconds: float) -> datetime:
    return now() + timedelta(seconds=seconds)
