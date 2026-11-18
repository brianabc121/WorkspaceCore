"""runner-agent 的主循环与指令（docs/v2/RUNNER_PLAN.md 第 9 节）。

- 长轮询 `/runner-api/v1/poll` 领指令；同一个环境的指令按顺序执行（每个环境一把锁），不同环境并行；
  指令按 id 去重：执行中的不重复执行，执行过的直接重发结果。
- 定期上报（`/report`）本机带工作台标签的容器、数据卷、镜像与占用和出口访问记录；
  工作台返回记录外的容器（stray）：先停止，停了 24 小时以上的删除。**没有工作台标签的一律不碰。**
- 断线保护：exec 超时在本机执行（停止这个容器），工作台掉线也守得住。
"""

from __future__ import annotations

import base64
import collections
import hashlib
import io
import ipaddress
import logging
import os
import re
import socket
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import VERSION
from . import containers as c
from .client import Workspace, WorkspaceError
from .docker import Docker, DockerError, tar_of
from .metrics import (
    UNKNOWN,
    ContainerCPUSampler,
    cpu_total_ns,
    memory_bytes,
    memory_working_set_bytes,
    nonnegative_int,
    started_generation,
)
from .proxy import EgressProxy, Policy

logger = logging.getLogger("runner_agent")

OUTPUT_LIMIT = 1024 * 1024
FILE_LIMIT = 20 * 1024 * 1024
BUILD_NETWORK = "mw-net-build0000000"
BUILD_ENV = "build"
STRAY_DELETE_SECONDS = 24 * 3600
HELPER_MAX_AGE = 3600
DONE_KEEP = 200
LIST_MAX_ENTRIES = 1000
DISK_EMERGENCY = 0.95
HOST_MACHINE_ID = Path("/run/host-machine-id")
# 改变环境状态的指令：同一个环境里按下发顺序一条一条执行；
# exec 与文件读写等前面的这类指令做完再开始，彼此之间可以并行（停止不用等长时间运行的 exec）
LIFECYCLE = frozenset(
    {"create_env", "start_env", "stop_env", "delete_env", "snapshot", "restore", "build_template"}
)
# 停止、送输入：不等同一个环境里更早的指令，立即执行（停不下来的进程不能排在它自己后面）
NO_WAIT = frozenset({"proc_signal", "proc_input"})

# ---------------------------------------------------------------- 托管进程
# docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 7 节、第 21.3 节。脚本都作为 sh -c 的参数下发，
# 不写进容器里可写的位置（免得被容器里的程序改掉）；全部按 dash 的写法（核验记录 V9）。
PROC_TICK = 0.5  # 有输出时每 0.5 秒读一次
PROC_SLOW = 2.0  # 连续 PROC_IDLE_AFTER 秒没有新输出后每 2 秒读一次
PROC_IDLE_AFTER = 10.0
PROC_CHUNK = 256 * 1024  # 一次最多回传的字节数
PROC_TERM_GRACE = 10.0  # TERM 之后多久还在就 KILL
PROC_MISSING_GRACE = 60.0  # 进程目录一直不见（多半是容器被重建过）多久后算丢失
PROC_STDIN_MAX = 4 * 1024 * 1024
PROC_STDIN_PIECE = 64 * 1024  # 一次 exec 写多少（base64 后不超过单个环境变量的上限 128 KB）
PROC_INPUT_MAX = 4096
PROC_MAX_SECONDS = 7 * 86400
PROC_PID_WAIT = 3.0
PROC_ERR_TAIL = 8192

# 参数：$1 进程目录 $2 工作目录 $3 是否交互（1/0），其后是命令。
# 标准输出写 out；标准错误先全量写 err，结束后只留最后 64 KB；
# 进程组号写 pid；退出码写 exit（原子改名）。
# 主进程结束后把同一进程组里还在的子进程也停掉，不留孤儿。
PROC_WRAPPER = r"""
d=$1; cwd=$2; inter=$3; shift 3
umask 077
mkdir -p "$d" && : > "$d/out" && : > "$d/err" || exit 1
lead='echo $$ > "$0/pid.tmp" && mv -f "$0/pid.tmp" "$0/pid"; exec "$@"'
code=126
if [ "$inter" = 1 ] && ! { rm -f "$d/in" && mkfifo "$d/in"; }; then
  echo "建不了输入管道" >> "$d/err"
elif cd "$cwd" 2>>"$d/err"; then
  if [ "$inter" = 1 ]; then
    setsid sh -c "$lead" "$d" "$@" <>"$d/in" >>"$d/out" 2>>"$d/err" &
  elif [ -f "$d/stdin" ]; then
    setsid sh -c "$lead" "$d" "$@" <"$d/stdin" >>"$d/out" 2>>"$d/err" &
  else
    setsid sh -c "$lead" "$d" "$@" </dev/null >>"$d/out" 2>>"$d/err" &
  fi
  wait $!
  code=$?
  pg=$(cat "$d/pid" 2>/dev/null)
  if [ -n "$pg" ]; then
    kill -TERM "-$pg" 2>/dev/null
    (sleep 5; kill -KILL "-$pg" 2>/dev/null) >/dev/null 2>&1 &
  fi
fi
rm -f "$d/stdin" "$d/in"
tail -c 65536 "$d/err" > "$d/err.tmp" 2>/dev/null && mv -f "$d/err.tmp" "$d/err"
echo "$code" > "$d/exit.tmp" && mv -f "$d/exit.tmp" "$d/exit"
"""
# $1 目录 $2 已确认的字节数 $3 最多读多少。
# 第一行“大小 退出码”（没有退出码是 -，目录不在大小是 -1），
# 之后是从已确认处开始的输出
PROC_READ = r"""
exec 2>/dev/null
d=$1; o=$2; n=$3
s=$(wc -c < "$d/out") || s=-1
e=-
[ -f "$d/exit" ] && e=$(cat "$d/exit")
printf '%s %s\n' "$s" "${e:--}"
if [ "$s" -gt "$o" ]; then tail -c +$((o + 1)) "$d/out" | head -c "$n"; fi
"""
# $1 目录 $2 new / append；内容在 MW_CHUNK（base64）
PROC_STDIN = r"""
exec 2>/dev/null
umask 077
mkdir -p "$1" || exit 1
if [ "$2" = new ]; then : > "$1/stdin" || exit 1; fi
[ -z "$MW_CHUNK" ] || printf '%s' "$MW_CHUNK" | base64 -d >> "$1/stdin"
"""
PROC_PID = 'exec 2>/dev/null; cat "$1/pid" || true'
# $1 目录 $2 TERM / KILL；没有进程组号时退出码 3
PROC_SIGNAL = r"""
exec 2>/dev/null
pg=$(cat "$1/pid") || exit 3
[ -n "$pg" ] || exit 3
kill -"$2" "-$pg"
"""
# 输入管道用读写方式打开：进程已经不在时也不会卡住
PROC_INPUT = r"""
exec 2>/dev/null
[ -p "$1/in" ] || exit 3
printf '%s' "$MW_INPUT" 1<>"$1/in"
"""
PROC_ERR = 'exec 2>/dev/null; tail -c "$2" "$1/err" || true'
PROC_CLEAN = 'exec 2>/dev/null; rm -rf -- "$1"'


@dataclass
class _Proc:
    """读取线程盯着的一个托管进程。时间都是 time.monotonic()。"""

    run_id: str
    env_id: str
    container: str
    folder: str
    acked: int = 0
    deadline: float = float("inf")
    last_output: float = 0.0  # 最近一次有新输出（起进程时记为当时）
    next_check: float = 0.0
    signaled: str | None = None
    signaled_at: float = 0.0
    reason: str | None = None  # timeout：到时限停的；killed：工作台叫停的
    busy: bool = False
    errors: int = 0
    missing_since: float | None = None


@dataclass
class Config:
    url: str
    token: str
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None
    socket: str = "/var/run/docker.sock"
    helper_image: str = "python:3.12-slim"
    block: str = ""
    self_name: str = ""
    kvm: bool = False
    disk_path: str = "/"
    workers: int = 16
    report_seconds: int = 30

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Config:
        def opt(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        return cls(
            url=env["MYWS_RUNNER_URL"],
            token=env["MYWS_RUNNER_TOKEN"],
            ca_file=opt("MYWS_RUNNER_CA_FILE"),
            cert_file=opt("MYWS_RUNNER_CERT_FILE"),
            key_file=opt("MYWS_RUNNER_KEY_FILE"),
            socket=env.get("MYWS_RUNNER_DOCKER_SOCKET", "/var/run/docker.sock"),
            helper_image=env.get("MYWS_RUNNER_HELPER_IMAGE", "python:3.12-slim"),
            block=env.get("MYWS_RUNNER_BLOCK", ""),
            self_name=env.get("MYWS_RUNNER_SELF", "") or env.get("HOSTNAME", ""),
            kvm=env.get("MYWS_RUNNER_KVM", "") in ("1", "true", "yes"),
            disk_path=env.get("MYWS_RUNNER_DISK_PATH", "/"),
        )


class CommandError(Exception):
    """指令执行失败：message 回报给工作台（写给智能体或管理员看）。"""


def physical_host_id() -> str | None:
    """Read only the host's mounted machine-id; never the agent container's ID."""
    try:
        machine_id = HOST_MACHINE_ID.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if re.fullmatch(r"[0-9a-f]{32}", machine_id) is None:
        return None
    return hashlib.sha256(("machine-id:" + machine_id).encode("ascii")).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def registry_host(image: str) -> str | None:
    """镜像引用里的仓库地址（没有就是 Docker Hub）。"""
    first, sep, _rest = image.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return first.rsplit(":", 1)[0] if not first.startswith("[") else first
    return None


def check_image(image: str, resolver: Callable[..., Any] = socket.getaddrinfo) -> None:
    """拉镜像由 Docker daemon（宿主网络）完成：仓库地址不能是私有或本机地址。"""
    host = registry_host(image)
    if host is None:
        return
    host = host.strip("[]")
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise CommandError(f"解析不了镜像仓库 {host}") from None
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]))
        if not ip.is_global:
            raise CommandError(f"镜像仓库 {host} 指向私有或本机地址，不能使用")


def dockerfile_of(image: str, steps: list[str]) -> str:
    """模板的 Dockerfile：FROM 基础镜像，加上智能体写的步骤。

    带网址的 ADD 由 daemon 在宿主网络上下载（绕过出口代理），不允许；FROM 里的仓库同样检查。
    """
    lines = [f"FROM {image}"]
    for step in steps:
        for raw in step.splitlines():
            line = raw.strip()
            word = line.split(" ", 1)[0].upper()
            if word == "ADD" and "://" in line:
                raise CommandError("ADD 不能带网址：用 RUN curl / wget（经出口代理）下载")
            if word == "FROM":
                parts = line.split()
                if len(parts) > 1:
                    check_image(parts[-1] if parts[1].startswith("--") else parts[1])
            lines.append(raw)
    return "\n".join(lines) + "\n"


@dataclass
class Agent:
    config: Config
    docker: Docker
    workspace: Workspace
    proxy: EgressProxy
    self_id: str = ""
    pool: ThreadPoolExecutor = field(init=False)
    exec_pool: ThreadPoolExecutor = field(init=False)
    _guard: threading.Lock = field(default_factory=threading.Lock)
    _turns: threading.Condition = field(default_factory=threading.Condition)
    _seq: int = 0
    _pending: dict[str, list[int]] = field(default_factory=dict)
    _inflight: set[str] = field(default_factory=set)
    _done: collections.OrderedDict[str, dict[str, Any]] = field(
        default_factory=collections.OrderedDict
    )
    _unsent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stopping: threading.Event = field(default_factory=threading.Event)
    cpu: ContainerCPUSampler = field(default_factory=ContainerCPUSampler)
    proc_pool: ThreadPoolExecutor = field(init=False)
    _procs: dict[str, _Proc] = field(default_factory=dict)
    _proc_guard: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # exec 单独一个线程池：长时间运行的命令占满了也不耽误停止、删除、读写文件
        self.pool = ThreadPoolExecutor(max_workers=self.config.workers)
        self.exec_pool = ThreadPoolExecutor(max_workers=self.config.workers)
        # 读托管进程的输出：单独的小线程池，不占指令池
        self.proc_pool = ThreadPoolExecutor(max_workers=max(2, min(8, self.config.workers)))

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        info = self.docker.inspect(self.config.self_name) if self.config.self_name else None
        if info is None:
            raise SystemExit("找不到 runner-agent 自己的容器（MYWS_RUNNER_SELF）")
        self.self_id = str(info["Id"])
        self._restore_proxies()

    def _restore_proxies(self) -> None:
        """重启后：给还在的环境重新挂上出口代理（策略在容器标签里）。"""
        for item in self.docker.containers(f"{c.LABEL_ROLE}=env"):
            labels = item.get("Labels") or {}
            env_id = labels.get(c.LABEL_ENV)
            network = labels.get("myworkspace.network_name")
            if not env_id or not network or item.get("State") != "running":
                continue
            policy = Policy(
                labels.get("myworkspace.network", "none"),
                [d for d in labels.get("myworkspace.domains", "").split(",") if d],
            )
            try:
                self._attach_proxy(env_id, network, policy)
            except (DockerError, OSError) as error:
                logger.warning("恢复 %s 的出口代理失败：%s", env_id, error)

    # ------------------------------------------------------------------ 主循环
    def capacity(self) -> dict[str, Any]:
        info = self.docker.info()
        version = self.docker.version()
        stat = os.statvfs(self.config.disk_path)
        return {
            "agent_version": VERSION,
            "docker_version": str(version.get("Version", ""))[:32],
            "runtimes": sorted((info.get("Runtimes") or {}).keys())[:10],
            "kvm": self.config.kvm,
            "cpus": int(info.get("NCPU") or 0),
            "mem_bytes": int(info.get("MemTotal") or 0),
            "disk_bytes": stat.f_blocks * stat.f_frsize,
            "disk_free_bytes": stat.f_bavail * stat.f_frsize,
            "wait_seconds": 25,
        }

    def run_forever(self) -> None:
        threading.Thread(target=self._report_loop, daemon=True).start()
        threading.Thread(target=self._proc_loop, daemon=True).start()
        delay = 1.0
        capacity = self.capacity()
        refreshed = time.monotonic()
        while not self.stopping.is_set():
            self._flush_unsent()
            if time.monotonic() - refreshed > 300:
                capacity = self.capacity()
                refreshed = time.monotonic()
            try:
                response = self.workspace.post("/poll", capacity, timeout=45)
                delay = 1.0
            except WorkspaceError as error:
                wait = 60.0 if error.status == 401 else delay
                logger.warning("长轮询失败：%s；%s 秒后重试", error, round(wait))
                self.stopping.wait(wait)
                delay = min(delay * 2, 60)
                continue
            for command in response.get("commands") or []:
                self.dispatch(command)

    def dispatch(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id"))
        kind = str(command.get("kind"))
        payload = command.get("payload") or {}
        key = str(payload.get("env_id") or payload.get("template_id") or command_id)
        if kind in NO_WAIT:
            key = command_id
        with self._guard:
            if command_id in self._inflight:
                return
            if command_id in self._done:
                self._unsent.append((command_id, self._done[command_id]))
                return
            self._inflight.add(command_id)
        with self._turns:
            self._seq += 1
            seq = self._seq
            if kind in LIFECYCLE:
                self._pending.setdefault(key, []).append(seq)
        pool = self.exec_pool if kind == "exec" else self.pool
        pool.submit(self._execute, command_id, command, key, seq)

    def _wait_turn(self, key: str, seq: int) -> None:
        """等同一个环境里更早下发的改变状态的指令做完。"""
        with self._turns:
            while any(s < seq for s in self._pending.get(key, [])):
                self._turns.wait(timeout=5)

    def _end_turn(self, key: str, seq: int) -> None:
        with self._turns:
            pending = self._pending.get(key)
            if pending and seq in pending:
                pending.remove(seq)
                if not pending:
                    self._pending.pop(key, None)
            self._turns.notify_all()

    def _execute(self, command_id: str, command: dict[str, Any], key: str, seq: int) -> None:
        kind = str(command.get("kind"))
        payload = dict(command.get("payload") or {})
        handler = getattr(self, f"do_{kind}", None)
        result: dict[str, Any]
        try:
            self._wait_turn(key, seq)
            if handler is None:
                raise CommandError(f"不支持的指令：{kind}")
            body = handler(payload)
            result = {"status": "done", "result": body}
        except (CommandError, c.ParamError) as error:
            result = {"status": "failed", "error": str(error)[:4000]}
        except DockerError as error:
            result = {"status": "failed", "error": f"Docker：{error.message}"[:4000]}
        except Exception as error:  # 不让一条指令打断主循环
            logger.exception("指令 %s（%s）出错", command_id, kind)
            result = {"status": "failed", "error": f"runner-agent 出错：{error}"[:4000]}
        finally:
            self._end_turn(key, seq)
        with self._guard:
            self._inflight.discard(command_id)
            self._done[command_id] = result
            while len(self._done) > DONE_KEEP:
                self._done.popitem(last=False)
        self._send_result(command_id, result)

    def _send_result(self, command_id: str, result: dict[str, Any]) -> None:
        delay = 1.0
        for _ in range(6):
            try:
                self.workspace.post(f"/commands/{command_id}/result", result, timeout=60)
                return
            except WorkspaceError as error:
                if error.status == 404 or (not error.retryable and error.status != 401):
                    logger.warning("结果 %s 被拒绝：%s", command_id, error)
                    return
                time.sleep(delay)
                delay *= 2
        with self._guard:
            self._unsent.append((command_id, result))

    def _flush_unsent(self) -> None:
        with self._guard:
            pending, self._unsent = self._unsent, []
        for command_id, result in pending:
            self._send_result(command_id, result)

    # ------------------------------------------------------------------ 公共步骤
    def _ensure_image(self, image: str) -> dict[str, Any]:
        found = self.docker.image(image)
        if found is None:
            check_image(image)
            self.docker.pull(image)
            found = self.docker.image(image)
            if found is None:
                raise CommandError(f"拉取后找不到镜像 {image}")
        return found

    def _self_ip(self, network: str) -> str:
        info = self.docker.inspect(self.self_id) or {}
        networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
        address = str((networks.get(network) or {}).get("IPAddress") or "")
        if not address:
            raise CommandError("runner-agent 没能连进环境网络（出口代理不可用）")
        return address

    def _fixed_proxy(self, network: str) -> str | None:
        """固定网段的环境网络里出口代理的地址；旧的（自动网段）网络返回 None，只能靠别名找代理。"""
        info = self.docker.inspect_network(network) or {}
        if (info.get("Labels") or {}).get(c.LABEL_FIXED_SUBNET) != "1":
            return None
        configs = (info.get("IPAM") or {}).get("Config") or []
        subnets = [str(x.get("Subnet")) for x in configs if ":" not in str(x.get("Subnet") or ":")]
        return c.proxy_address(subnets[0]) if subnets else None

    def _env_network(self, network: str, labels: dict[str, str]) -> None:
        """环境的 internal 网络，网段固定：先让 Docker 自动分配一个不和本机冲突的网段，
        再用这个网段重建（成为“指定网段”的网络），runner-agent 才能在里面用固定地址。
        gVisor 访问不到 Docker 内置的 DNS，环境只能按 IP 找出口代理（核验记录第 5 节）。"""
        if self.docker.inspect_network(network) is not None:
            return
        fixed = {**labels, c.LABEL_FIXED_SUBNET: "1"}
        for _ in range(3):
            try:
                self.docker.create_network(network, labels, internal=True)
            except DockerError as error:
                if error.status != 409:
                    raise
                return
            info = self.docker.inspect_network(network) or {}
            configs = (info.get("IPAM") or {}).get("Config") or []
            subnets = [str(x.get("Subnet") or "") for x in configs]
            subnet = next((n for n in subnets if n and ":" not in n), "")
            self.docker.remove_network(network)
            if not subnet:
                raise CommandError("Docker 没有给环境网络分配网段")
            try:
                self.docker.create_network(network, fixed, internal=True, subnet=subnet)
                return
            except DockerError as error:
                if error.status == 409 and self.docker.inspect_network(network) is not None:
                    return
                logger.warning("按网段 %s 重建环境网络失败，重试：%s", subnet, error)
        raise CommandError("没能建好环境网络（网段冲突）")

    def _attach_proxy(self, env_id: str, network: str, policy: Policy) -> str | None:
        """接入环境网络，在 runner-agent 的地址上开出口代理；返回固定地址（旧网络为 None）。"""
        fixed = self._fixed_proxy(network)
        self.docker.connect_network(network, self.self_id, [c.PROXY_ALIAS], ip=fixed)
        address = self._self_ip(network)
        if fixed and address != fixed:
            raise CommandError(f"runner-agent 在环境网络里的地址是 {address}，不是固定的 {fixed}")
        self.proxy.listen(env_id, address, policy)
        return fixed

    def _policy(self, payload: dict[str, Any]) -> Policy:
        return Policy(
            str(payload.get("network_mode") or "none"),
            [str(d) for d in payload.get("domains") or []],
        )

    def _helper(
        self, payload: dict[str, Any], mounts: list[dict[str, Any]], cmd: list[str] | None
    ) -> str:
        name = f"mw-helper-{os.urandom(6).hex()}"
        config = c.helper_config(self.config.helper_image, payload, mounts, cmd)
        self._ensure_image(self.config.helper_image)
        self.docker.create_container(name, config)
        return name

    def _run_helper(
        self, payload: dict[str, Any], mounts: list[dict[str, Any]], cmd: list[str]
    ) -> bytes:
        name = self._helper(payload, mounts, cmd)
        try:
            self.docker.start(name)
            code = self.docker.wait(name, timeout=1800)
            output = self.docker.logs(name)
            if code != 0:
                raise CommandError(f"助手容器退出码 {code}")
            return output
        finally:
            self.docker.remove(name)

    def _inject_secrets(self, payload: dict[str, Any], container: str) -> None:
        user = c.USERS[str(payload.get("user") or "user")]
        for name, value in (payload.get("secret_values") or {}).items():
            result = self.docker.exec(
                container,
                c.secret_command(str(name)),
                user=user,
                env=[f"MYWS_SECRET_VALUE={value}"],
                timeout=30,
                limit=4096,
            )
            if result.exit_code != 0:
                raise CommandError(f"写入密钥 {name} 失败（镜像里要有 sh）")

    def _ensure_running(self, payload: dict[str, Any]) -> tuple[str, bool]:
        container = c.check_name(payload.get("container"), "env")
        info = self.docker.inspect(container)
        if info is None:
            raise CommandError("容器不存在（可能已被删除或丢失）")
        network = c.check_name(payload.get("network"), "net")
        started = False
        if not (info.get("State") or {}).get("Running"):
            self.docker.start(container)
            started = True
        self._attach_proxy(str(payload["env_id"]), network, self._policy(payload))
        if started:
            self._inject_secrets(payload, container)
        return container, started

    # ------------------------------------------------------------------ 指令
    def do_build_template(self, p: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        image = str(p.get("image") or "")
        steps = [str(s) for s in p.get("steps") or []]
        tag = str(p.get("tag") or "")
        if not image or not tag.startswith("myworkspace/tpl-"):
            raise CommandError("模板参数不合法")
        if not steps:
            found = self._ensure_image(image)
            digests = found.get("RepoDigests") or []
            return {
                "image_ref": digests[0] if digests else str(found.get("Id")),
                "image_digest": str(found.get("Id")),
                "image_bytes": int(found.get("Size") or 0),
                "seconds": round(time.monotonic() - started, 2),
                "log": f"已拉取 {image}",
            }
        check_image(image)
        dockerfile = dockerfile_of(image, steps)
        self._build_network()
        proxy = f"http://{c.PROXY_ALIAS}:{c.PROXY_PORT}"
        args = {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
        labels = {c.LABEL_RUNNER: "1", c.LABEL_TEMPLATE: str(p.get("template_id") or "")}
        try:
            lines = self.docker.build(
                dockerfile, tag, labels, network=BUILD_NETWORK, build_args=args, timeout=3500
            )
        except DockerError as error:
            raise CommandError(f"构建失败：{error.message}") from None
        log = "".join(str(e.get("stream") or "") for e in lines)[-20000:]
        built = self.docker.image(tag) or {}
        return {
            "image_ref": tag,
            "image_digest": str(built.get("Id") or ""),
            "image_bytes": int(built.get("Size") or 0),
            "seconds": round(time.monotonic() - started, 2),
            "log": log,
        }

    def _build_network(self) -> None:
        if not any(n.get("Name") == BUILD_NETWORK for n in self.docker.networks(c.LABEL_RUNNER)):
            try:
                self.docker.create_network(
                    BUILD_NETWORK, {c.LABEL_RUNNER: "1", c.LABEL_ROLE: "build"}, internal=True
                )
            except DockerError as error:
                if error.status != 409:
                    raise
        # 构建经出口代理访问公网（私有网段同样不可达）
        self.docker.connect_network(BUILD_NETWORK, self.self_id, [c.PROXY_ALIAS])
        self.proxy.listen(BUILD_ENV, self._self_ip(BUILD_NETWORK), Policy("all"))

    def do_create_env(self, p: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        env_id = str(p["env_id"])
        container = c.check_name(p.get("container"), "env")
        volume = c.check_name(p.get("volume"), "vol")
        network = c.check_name(p.get("network"), "net")
        image = self._image_for(p)
        self._env_network(network, c.labels(p))
        policy = self._policy(p)
        proxy = self._attach_proxy(env_id, network, policy)  # 固定地址：写进环境的代理变量与 hosts
        existed = self.docker.inspect(container) is not None
        # 更新镜像（recreate）：容器换成载荷里的新镜像、按原参数重建，数据卷原样保留——
        # 不重新改属主、不恢复快照、不写初始文件（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 5.2）
        recreate = bool(p.get("recreate")) and existed
        if recreate:
            self.docker.stop(container, seconds=10)
            self.docker.remove(container)
        if not existed:
            self.docker.create_volume(volume, c.labels(p))
            uid, gid = c.user_ids(p)
            mount = [{"Source": volume, "Target": "/workspace"}]
            source = p.get("copy_from_volume")
            if source:
                c.check_name(source, "vol")
                self._run_helper(
                    p,
                    [*mount, {"Source": str(source), "Target": "/src", "ReadOnly": True}],
                    ["sh", "-c", f"cp -a /src/. /workspace/ && chown -R {uid}:{gid} /workspace"],
                )
            else:
                self._run_helper(p, mount, ["chown", f"{uid}:{gid}", "/workspace"])
            snapshot = p.get("snapshot")
            if snapshot:
                self._restore(p, snapshot)
            files = p.get("files") or []
            if files:
                self._put(p, files)
        if not existed or recreate:
            config = c.container_config(p, host_memory=None, proxy=proxy)
            config["Labels"].update(
                {
                    "myworkspace.network": policy.mode,
                    "myworkspace.domains": ",".join(policy.domains)[:4000],
                    "myworkspace.network_name": network,
                }
            )
            self.docker.create_container(container, config)
        self.docker.start(container)
        self._inject_secrets(p, container)
        for step in p.get("setup") or []:
            result = self.docker.exec(
                container,
                ["sh", "-lc", str(step)],
                user=c.USERS[str(p.get("user") or "user")],
                workdir=c.WORKDIR,
                timeout=1800,
                limit=64 * 1024,
            )
            if result.exit_code != 0:
                tail = result.output[-2000:].decode("utf-8", "replace")
                raise CommandError(f"初始化步骤失败（退出码 {result.exit_code}）：{tail}")
        digests = image.get("RepoDigests") or []
        return {
            "seconds": round(time.monotonic() - started, 2),
            "image_digest": digests[0] if digests else str(image.get("Id") or ""),
            "existed": existed,
            "recreated": recreate,
        }

    def _image_for(self, p: dict[str, Any]) -> dict[str, Any]:
        """环境的镜像；模板镜像被清理了就按模板重新构建（重建归档的环境时）。"""
        image = str(p.get("image") or "")
        found = self.docker.image(image)
        if found is not None:
            return found
        build = p.get("template_build")
        if build and image.startswith("myworkspace/tpl-"):
            self.do_build_template({**build, "tag": image, "revision": 0})
            built = self.docker.image(image)
            if built is None:
                raise CommandError("模板镜像重新构建后仍然找不到")
            return built
        return self._ensure_image(image)

    def _restore(self, p: dict[str, Any], snapshot: dict[str, Any]) -> None:
        """从快照恢复：逐块下载（工作台解密后下发），流式写回新的数据卷。"""
        volume = c.check_name(p.get("volume"), "vol")
        snapshot_id = str(snapshot.get("id") or "")
        uid, gid = c.user_ids(p)
        mount = [{"Source": volume, "Target": "/workspace"}]
        for entry in snapshot.get("entries") or []:
            path = str(entry.get("path") or ".")
            if entry.get("missing"):
                continue
            if path.startswith("/") or ".." in path.split("/"):
                raise CommandError(f"快照里的路径不合法：{path[:100]}")
            parent = os.path.dirname(path) if path != "." else ""
            if parent:
                # 路径作为参数传入（不拼进脚本）；新建的每一级目录都改成环境用户
                script = (
                    'cd /workspace && mkdir -p -- "$2" && d="$2"; '
                    'while [ -n "$d" ] && [ "$d" != "." ]; do chown "$1" -- "$d"; '
                    'd=$(dirname -- "$d"); done'
                )
                self._run_helper(p, mount, ["sh", "-c", script, "sh", f"{uid}:{gid}", parent])
            first = int(entry.get("first", 0))
            count = int(entry.get("parts", 0))

            def chunks(start: int = first, total: int = count) -> Iterator[bytes]:
                for index in range(start, start + total):
                    yield self.workspace.get_bytes(f"/snapshots/{snapshot_id}/parts/{index}")

            name = self._helper(p, mount, None)
            try:
                target = f"/workspace/{parent}".rstrip("/") or "/workspace"
                self.docker.put_archive_stream(name, target, chunks())
            finally:
                self.docker.remove(name)

    def do_snapshot(self, p: dict[str, Any]) -> dict[str, Any]:
        """常驻环境的快照：要保存的目录按 tar 流读出、分块上传（环境已停止，数据一致）。"""
        volume = c.check_name(p.get("volume"), "vol")
        snapshot_id = str(p.get("snapshot_id") or "")
        part_bytes = int(p.get("part_bytes") or 16 * 1024 * 1024)
        max_bytes = int(p.get("max_bytes") or 5 * 1024**3)
        name = self._helper(p, [{"Source": volume, "Target": "/workspace", "ReadOnly": True}], None)
        entries: list[dict[str, Any]] = []
        index = 0
        total = 0
        try:
            for raw in p.get("paths") or ["."]:
                path = str(raw).strip("/") or "."
                if ".." in path.split("/"):
                    raise CommandError(f"要保存的目录不合法：{path[:100]}")
                first = index
                size = 0
                try:
                    for chunk in self.docker.stream_archive(
                        name, f"/workspace/{path}", chunk=part_bytes
                    ):
                        total += len(chunk)
                        size += len(chunk)
                        if total > max_bytes:
                            raise CommandError(
                                f"快照超过上限（{max_bytes // 1024**3} GB）；调小要保存的目录"
                            )
                        self.workspace.put_bytes(f"/snapshots/{snapshot_id}/parts/{index}", chunk)
                        index += 1
                except DockerError as error:
                    if error.status != 404:
                        raise
                    entries.append({"path": path, "missing": True, "first": first, "parts": 0})
                    continue
                entries.append(
                    {"path": path, "first": first, "parts": index - first, "bytes": size}
                )
        except WorkspaceError as error:
            raise CommandError(f"上传快照失败：{error.message}") from None
        finally:
            self.docker.remove(name)
        return {"entries": entries, "parts": index, "bytes": total}

    def do_archive_env(self, p: dict[str, Any]) -> dict[str, Any]:
        """归档：删掉容器、网络与数据卷（配置记录和快照在工作台）。"""
        return self.do_delete_env(p)

    def do_prune_images(self, p: dict[str, Any]) -> dict[str, Any]:
        """清理没人用的模板镜像（只看带工作台模板标签的镜像；正在用的 Docker 会拒绝删除）。"""
        keep = {str(k) for k in p.get("keep") or []}
        removed: list[str] = []
        for image in self.docker.images(c.LABEL_TEMPLATE):
            tags = [str(t) for t in image.get("RepoTags") or [] if t]
            if any(tag in keep for tag in tags):
                continue
            for tag in tags or [str(image.get("Id"))]:
                self.docker.remove_image(tag)
                removed.append(tag)
        return {"removed": removed[:200]}

    def do_start_env(self, p: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        self._ensure_running(p)
        return {"seconds": round(time.monotonic() - started, 2)}

    def do_stop_env(self, p: dict[str, Any]) -> dict[str, Any]:
        container = c.check_name(p.get("container"), "env")
        self.docker.stop(container, seconds=10)
        self.proxy.close(str(p["env_id"]))
        return {}

    def do_delete_env(self, p: dict[str, Any]) -> dict[str, Any]:
        container = c.check_name(p.get("container"), "env")
        volume = c.check_name(p.get("volume"), "vol")
        network = c.check_name(p.get("network"), "net")
        self.docker.remove(container)
        self.proxy.close(str(p["env_id"]))
        self.docker.disconnect_network(network, self.self_id)
        self.docker.remove_network(network)
        self.docker.remove_volume(volume)
        return {}

    def do_exec(self, p: dict[str, Any]) -> dict[str, Any]:
        container, _ = self._ensure_running(p)
        cmd = p.get("cmd")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            raise CommandError("命令不合法")
        timeout = int(p.get("timeout") or 600)
        extra = c.env_list({"env": p.get("exec_env") or {}, "network_mode": "none"})
        extra = [e for e in extra if not e.startswith("HOME=")]
        started_at = datetime.now(UTC).isoformat()
        result = self.docker.exec(
            container,
            cmd,
            user=c.USERS[str(p.get("user") or "user")],
            workdir=c.exec_workdir(p.get("workdir")),
            env=extra or None,
            timeout=timeout,
            limit=OUTPUT_LIMIT,
        )
        if result.timed_out:
            # 单个 exec 不能单独终止：停止这个容器（数据卷保留）
            self.docker.stop(container, seconds=5)
            self.proxy.close(str(p["env_id"]))
        return {
            "exit_code": result.exit_code,
            "output_b64": _b64(result.output),
            "truncated": result.truncated,
            "timed_out": result.timed_out,
            "seconds": round(result.seconds, 2),
            "started_at": started_at,
        }

    def _put(self, p: dict[str, Any], files: list[dict[str, Any]]) -> int:
        volume = c.check_name(p.get("volume"), "vol")
        uid, gid = c.user_ids(p)
        content: dict[str, bytes] = {}
        for item in files:
            path = str(item.get("path") or "")
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise CommandError(f"路径不合法：{path[:100]}")
            content[path] = base64.b64decode(str(item.get("b64") or ""))
        if sum(len(v) for v in content.values()) > FILE_LIMIT:
            raise CommandError("文件合计最多 20 MB")
        name = self._helper(p, [{"Source": volume, "Target": "/workspace"}], None)
        try:
            self.docker.put_archive(name, "/workspace", tar_of(content, uid=uid, gid=gid))
        finally:
            self.docker.remove(name)
        return sum(len(v) for v in content.values())

    def do_put_files(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"size": self._put(p, list(p.get("files") or []))}

    def do_get_files(self, p: dict[str, Any]) -> dict[str, Any]:
        volume = c.check_name(p.get("volume"), "vol")
        path = str(p.get("path") or "")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise CommandError("路径不合法")
        name = self._helper(p, [{"Source": volume, "Target": "/workspace"}], None)
        try:
            try:
                data = self.docker.get_archive(name, f"/workspace/{path}", limit=FILE_LIMIT + 65536)
            except DockerError as error:
                if error.status == 404:
                    raise CommandError(f"没有这个文件：{path}") from None
                if error.status == 413:
                    raise CommandError("文件超过 20 MB") from None
                raise
        finally:
            self.docker.remove(name)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            member = tar.next()
            if member is None or not member.isfile():
                raise CommandError(f"{path} 不是文件（列目录用 action=list）")
            extracted = tar.extractfile(member)
            content = extracted.read() if extracted else b""
        return {"b64": _b64(content), "size": len(content)}

    def do_list_files(self, p: dict[str, Any]) -> dict[str, Any]:
        volume = c.check_name(p.get("volume"), "vol")
        path = str(p.get("path") or "")
        if path.startswith("/") or ".." in path.split("/"):
            raise CommandError("路径不合法")
        target = f"/workspace/{path}".rstrip("/")
        output = self._run_helper(
            p,
            [{"Source": volume, "Target": "/workspace", "ReadOnly": True}],
            ["find", target, "-mindepth", "1", "-maxdepth", "3", "-printf", "%y %s %P\\n"],
        )
        entries: list[dict[str, Any]] = []
        kinds = {"f": "file", "d": "dir", "l": "link"}
        for line in output.decode("utf-8", "replace").splitlines()[:LIST_MAX_ENTRIES]:
            kind, _, rest = line.partition(" ")
            size, _, name = rest.partition(" ")
            entries.append(
                {
                    "path": f"{path}/{name}".lstrip("/") if path else name,
                    "kind": kinds.get(kind, "other"),
                    "size": int(size) if size.isdigit() else 0,
                }
            )
        return {"entries": entries}

    # ------------------------------------------------------------------ 托管进程
    def _sh(
        self,
        container: str,
        script: str,
        args: list[str],
        *,
        env: list[str] | None = None,
        timeout: float = 30,
        limit: int = 64 * 1024,
    ) -> Any:
        """以普通用户在容器里跑一段固定脚本（托管进程的辅助操作）。"""
        return self.docker.exec(
            container,
            ["sh", "-c", script, "mw", *args],
            user=c.USERS["user"],
            workdir=c.WORKDIR,
            env=env,
            timeout=timeout,
            limit=limit,
        )

    def _proc_pid(self, container: str, folder: str) -> int | None:
        result = self._sh(container, PROC_PID, [folder])
        text = result.output.decode("ascii", "replace").strip()
        return int(text) if text.isdigit() else None

    def _write_stdin(self, container: str, folder: str, data: bytes) -> None:
        """标准输入分段写进进程目录（每段经一个环境变量传，不经 Docker 的文件接口：
        gVisor 下直接往运行中的容器里写文件不可靠）。"""
        pieces = [data[i : i + PROC_STDIN_PIECE] for i in range(0, len(data), PROC_STDIN_PIECE)]
        for index, piece in enumerate(pieces or [b""]):
            result = self._sh(
                container,
                PROC_STDIN,
                [folder, "new" if index == 0 else "append"],
                env=[f"MW_CHUNK={_b64(piece)}"],
            )
            if result.exit_code != 0:
                raise CommandError("写入标准输入失败（镜像里要有 sh 和 base64）")

    def do_proc_start(self, p: dict[str, Any]) -> dict[str, Any]:
        run_id, folder = c.proc_dir(p)
        argv = c.proc_argv(p.get("argv"))
        cwd = c.proc_cwd(p.get("cwd") or c.WORKDIR)
        given = p.get("proc_input")
        if not isinstance(given, dict) or given.get("missing"):
            raise CommandError("输入已过期（工作台重启过或指令排队太久），请重新发起")
        env = c.proc_env(p.get("env"), given.get("secret_env"))
        timeout = p.get("timeout") or 1800
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise CommandError("时限不合法")
        timeout = min(timeout, PROC_MAX_SECONDS)
        try:
            stdin = base64.b64decode(str(given.get("stdin_b64") or ""), validate=True)
        except ValueError:
            raise CommandError("标准输入不是合法的 base64") from None
        if len(stdin) > PROC_STDIN_MAX:
            raise CommandError("标准输入太大")
        container, _ = self._ensure_running(p)
        # 同一个运行号已经起过（runner-agent 重启后工作台重发了指令）：不再起第二个
        pgid = self._proc_pid(container, folder)
        if pgid is None:
            self._write_stdin(container, folder, stdin)
            self.docker.exec_detached(
                container,
                [
                    "sh",
                    "-c",
                    PROC_WRAPPER,
                    "mw-proc",
                    folder,
                    cwd,
                    "1" if p.get("interactive") else "0",
                    *argv,
                ],
                user=c.USERS["user"],
                workdir=c.WORKDIR,
                env=env or None,
            )
            until = time.monotonic() + PROC_PID_WAIT
            while pgid is None and time.monotonic() < until:
                time.sleep(0.1)
                pgid = self._proc_pid(container, folder)
        self.watch_proc(
            run_id,
            str(p.get("env_id") or ""),
            container,
            folder,
            deadline=time.monotonic() + timeout,
        )
        return {"started": True, "pgid": pgid}

    def do_proc_signal(self, p: dict[str, Any]) -> dict[str, Any]:
        run_id, folder = c.proc_dir(p)
        container = c.check_name(p.get("container"), "env")
        sig = p.get("signal")
        if sig not in ("TERM", "KILL"):
            raise CommandError("信号只能是 TERM 或 KILL")
        with self._proc_guard:
            proc = self._procs.get(run_id)
        if proc is not None:
            return {"signaled": self._signal_proc(proc, str(sig), "killed")}
        try:
            result = self._sh(container, PROC_SIGNAL, [folder, str(sig)])
        except DockerError as error:
            if error.status in (404, 409):
                return {"signaled": False}
            raise
        return {"signaled": result.exit_code == 0}

    def do_proc_input(self, p: dict[str, Any]) -> dict[str, Any]:
        _run_id, folder = c.proc_dir(p)
        container = c.check_name(p.get("container"), "env")
        given = p.get("proc_input")
        if not isinstance(given, dict) or given.get("missing"):
            raise CommandError("输入已过期（工作台重启过或指令排队太久），请重新发起")
        data = given.get("data")
        if not isinstance(data, str) or "\x00" in data or len(data.encode()) > PROC_INPUT_MAX:
            raise CommandError("输入不合法（最多 4 KB 文字）")
        result = self._sh(container, PROC_INPUT, [folder], env=[f"MW_INPUT={data}"])
        if result.exit_code == 3:
            raise CommandError("这个进程不接受输入（已经结束或不是交互进程）")
        if result.exit_code != 0:
            raise CommandError("送输入失败")
        return {"written": len(data.encode())}

    def watch_proc(
        self,
        run_id: str,
        env_id: str,
        container: str,
        folder: str,
        *,
        acked: int = 0,
        deadline: float = float("inf"),
    ) -> None:
        """开始（或继续）盯一个托管进程；已经在盯的只把已确认数往前推。"""
        with self._proc_guard:
            proc = self._procs.get(run_id)
            if proc is None:
                self._procs[run_id] = _Proc(
                    run_id,
                    env_id,
                    container,
                    folder,
                    acked=acked,
                    deadline=deadline,
                    last_output=time.monotonic(),
                )
            elif acked > proc.acked:
                proc.acked = acked

    def sync_procs(self, items: list[Any]) -> None:
        """对账应答里的托管进程（工作台认为还在跑的）：runner-agent 重启后据此接着读。
        工作台没列出的不停止：可能是刚起来、对账还没看到；工作台真不认识时读到输出会回 404。"""
        now_wall, now = time.time(), time.monotonic()
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                run_id, folder = c.proc_dir(item)
                container = c.check_name(item.get("container"), "env")
            except c.ParamError:
                continue
            acked = item.get("acked")
            acked = acked if isinstance(acked, int) and not isinstance(acked, bool) else 0
            deadline = float("inf")
            raw = item.get("timeout_at")
            if isinstance(raw, str) and raw:
                try:
                    when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    deadline = now + (when.timestamp() - now_wall)
                except ValueError:
                    pass
            self.watch_proc(
                run_id,
                str(item.get("env_id") or ""),
                container,
                folder,
                acked=max(acked, 0),
                deadline=deadline,
            )

    def _proc_loop(self) -> None:
        while not self.stopping.wait(PROC_TICK):
            self.proc_round(wait=False)

    def proc_round(self, *, wait: bool = True) -> None:
        """把到点的托管进程各读一次；wait=True 时在当前线程里做完（测试用）。"""
        now = time.monotonic()
        with self._proc_guard:
            due = [p for p in self._procs.values() if not p.busy and p.next_check <= now]
            for proc in due:
                proc.busy = True
        for proc in due:
            if wait:
                self._check_proc_safely(proc)
            else:
                self.proc_pool.submit(self._check_proc_safely, proc)

    def _check_proc_safely(self, proc: _Proc) -> None:
        try:
            self.check_proc(proc)
        except Exception as error:  # 一个进程出错不影响别的
            proc.errors += 1
            proc.next_check = time.monotonic() + min(2.0**proc.errors, 30.0)
            logger.warning("读托管进程 %s 失败：%s", proc.run_id, error)
        finally:
            proc.busy = False

    def _drop_proc(self, proc: _Proc, *, clean: bool) -> None:
        if clean:
            try:
                self._sh(proc.container, PROC_CLEAN, [proc.folder])
            except DockerError as error:
                logger.info("清理托管进程 %s 的目录失败：%s", proc.run_id, error)
        with self._proc_guard:
            self._procs.pop(proc.run_id, None)

    def _signal_proc(self, proc: _Proc, sig: str, reason: str) -> bool:
        try:
            result = self._sh(proc.container, PROC_SIGNAL, [proc.folder, sig])
        except DockerError as error:
            if error.status in (404, 409):
                return False
            raise
        proc.reason = proc.reason or reason
        proc.signaled, proc.signaled_at = sig, time.monotonic()
        proc.next_check = 0.0
        return bool(result.exit_code == 0)

    def check_proc(self, proc: _Proc) -> None:
        """读一次：先看时限（TERM，10 秒后 KILL），再回传新的输出，最后在输出全部确认后回报结束。"""
        now = time.monotonic()
        if proc.signaled is None and now >= proc.deadline:
            self._signal_proc(proc, "TERM", "timeout")
        elif proc.signaled == "TERM" and now - proc.signaled_at >= PROC_TERM_GRACE:
            self._signal_proc(proc, "KILL", proc.reason or "killed")
        try:
            result = self._sh(
                proc.container,
                PROC_READ,
                [proc.folder, str(proc.acked), str(PROC_CHUNK)],
                limit=PROC_CHUNK + 4096,
            )
        except DockerError as error:
            if error.status in (404, 409):  # 容器没了或停了：进程不可能还在
                self._report_exit(proc, None, "lost", "容器已经不在运行，进程结果未知")
                return
            raise
        header, _, data = bytes(result.output).partition(b"\n")
        fields = header.decode("ascii", "replace").split()
        try:
            size = int(fields[0])
        except (IndexError, ValueError):
            size = -1
        code_text = fields[1] if len(fields) > 1 else "-"
        if size < 0:
            proc.missing_since = proc.missing_since or now
            if now - proc.missing_since >= PROC_MISSING_GRACE:
                self._report_exit(proc, None, "lost", "进程目录不见了（容器可能被重建过）")
            else:
                proc.next_check = now + PROC_SLOW
            return
        proc.missing_since = None
        if data:
            sent_from = proc.acked
            try:
                answer = self.workspace.post(
                    f"/procs/{proc.run_id}/output",
                    {"offset": sent_from, "data_b64": _b64(data)},
                    timeout=30,
                )
            except WorkspaceError as error:
                if error.status == 404:  # 工作台不认识这个进程了：停掉它，不留在容器里
                    self._signal_proc(proc, "KILL", "killed")
                    self._drop_proc(proc, clean=True)
                    return
                raise
            acked = answer.get("acked") if isinstance(answer, dict) else None
            if isinstance(acked, int) and not isinstance(acked, bool) and 0 <= acked <= size:
                if acked > proc.acked:
                    proc.last_output = now
                proc.acked = acked
        proc.errors = 0
        if code_text != "-" and proc.acked >= size:
            code = int(code_text) if code_text.lstrip("-").isdigit() else None
            state = (
                "timeout"
                if proc.reason == "timeout"
                else "killed"
                if proc.reason == "killed"
                else "exited"
            )
            self._report_exit(proc, code, state, None)
            return
        more = proc.acked < size
        idle = now - proc.last_output >= PROC_IDLE_AFTER
        proc.next_check = now if more else now + (PROC_SLOW if idle else PROC_TICK)

    def _report_exit(self, proc: _Proc, code: int | None, state: str, error: str | None) -> None:
        tail = ""
        if state != "lost":
            try:
                result = self._sh(proc.container, PROC_ERR, [proc.folder, str(PROC_ERR_TAIL)])
                tail = bytes(result.output).decode("utf-8", "replace")
            except DockerError:
                tail = ""
        body = {"exit_code": code, "state": state, "stderr_tail": tail, "error": error}
        try:
            self.workspace.post(f"/procs/{proc.run_id}/exit", body, timeout=30)
        except WorkspaceError as failure:
            if failure.status != 404 and (failure.retryable or failure.status == 401):
                raise  # 下一轮再报；输出和退出码还在容器里
        self._drop_proc(proc, clean=state != "lost")

    # ------------------------------------------------------------------ 对账
    def _report_loop(self) -> None:
        while not self.stopping.wait(self.config.report_seconds):
            try:
                self.report()
            except (DockerError, WorkspaceError, OSError) as error:
                logger.warning("对账上报失败：%s", error)

    def _usage(self, item: dict[str, Any], name: str) -> dict[str, Any]:
        """一个运行中环境的内存与 CPU 读数（T-0184）。

        读不到的项上报 None（未知），不补 0；这里的异常只影响这一个容器的读数，容器本身照常上报。
        """
        try:
            stats = self.docker.stats(name)
            at = time.monotonic_ns()
            info = self.docker.inspect(name)
        except (DockerError, OSError, ValueError) as error:
            logger.info("读取 %s 的用量失败：%s", name, error)
            return {}
        out: dict[str, Any] = {
            "mem_bytes": memory_bytes(stats),
            "memory_working_set_bytes": memory_working_set_bytes(stats),
        }
        generation = started_generation(info)
        container_id = str(item.get("Id") or (info or {}).get("Id") or "")
        if generation and container_id:
            sample = self.cpu.sample(container_id, generation, cpu_total_ns(stats), at)
        else:
            sample = UNKNOWN
        out["cpu_used_cores"] = sample.used_cores
        out["cpu_window_seconds"] = sample.window_seconds
        return out

    def report(self) -> dict[str, Any]:
        sample = self.workspace.post("/report/start", {})
        _, memory_before = _meminfo()
        df = self.docker.system_df()
        # 数据卷占用：Docker 没算出来时是 -1，缺失也是未知；都报 None，不当成 0
        volume_sizes = {
            str(v.get("Name")): nonnegative_int((v.get("UsageData") or {}).get("Size"))
            for v in df.get("Volumes") or []
        }
        labeled = self.docker.containers(f"{c.LABEL_RUNNER}=1")
        envs: list[dict[str, Any]] = []
        sampled: set[str] = set()
        now = time.time()
        for item in labeled:
            labels = item.get("Labels") or {}
            role = labels.get(c.LABEL_ROLE)
            name = str((item.get("Names") or ["/"])[0]).lstrip("/")
            if role == "helper" and now - int(item.get("Created") or now) > HELPER_MAX_AGE:
                self.docker.remove(name)
                continue
            if role != "env" or not labels.get(c.LABEL_ENV):
                continue
            entry: dict[str, Any] = {
                "env_id": labels[c.LABEL_ENV],
                "name": name,
                "state": str(item.get("State") or ""),
                "size_rw": int(item.get("SizeRw") or 0),
            }
            if item.get("State") == "running":
                entry.update(self._usage(item, name))
                sampled.add(str(item.get("Id") or ""))
            envs.append(entry)
        self.cpu.prune(sampled)
        volumes = [
            {
                "env_id": (v.get("Labels") or {}).get(c.LABEL_ENV),
                "name": v.get("Name"),
                "bytes": volume_sizes.get(str(v.get("Name"))),
            }
            for v in self.docker.volumes(f"{c.LABEL_RUNNER}=1")
            if (v.get("Labels") or {}).get(c.LABEL_ENV)
        ]
        images = [
            {
                "template_id": (i.get("Labels") or {}).get(c.LABEL_TEMPLATE) or None,
                "ref": str((i.get("RepoTags") or [i.get("Id")])[0])[:300],
                "bytes": int(i.get("Size") or 0),
            }
            for i in df.get("Images") or []
            if (i.get("Labels") or {}).get(c.LABEL_TEMPLATE)
        ]
        # 不是 runner-agent 建的容器只数一数，不做任何操作。其中带 MCP 管理器标签的
        # （工作台另行部署、另行管理的）单独计数，不算进“不是工作台建的容器”
        unlabeled = managers = 0
        for item in self.docker.all_containers():
            labels = item.get("Labels") or {}
            if labels.get(c.LABEL_RUNNER):
                continue
            if c.LABEL_MCP_MANAGER in labels:
                managers += 1
            else:
                unlabeled += 1
        stat = os.statvfs(self.config.disk_path)
        if stat.f_blocks and 1 - stat.f_bavail / stat.f_blocks > DISK_EMERGENCY:
            self._disk_emergency(labeled)
        mem_total, mem_free = _meminfo()
        body = {
            "sample_id": sample["sample_id"],
            "physical_host_id": physical_host_id(),
            "containers": envs,
            "volumes": volumes,
            "images": images,
            "unlabeled_containers": unlabeled,
            "mcp_manager_containers": managers,
            "egress": self.proxy.stats.drain(),
            "mem_bytes": mem_total,
            "mem_free_bytes": min(memory_before, mem_free),
            "disk_bytes": stat.f_blocks * stat.f_frsize,
            "disk_free_bytes": stat.f_bavail * stat.f_frsize,
            "images_bytes": int(sum(int(i.get("Size") or 0) for i in df.get("Images") or [])),
            "volumes_bytes": int(sum(v for v in volume_sizes.values() if v is not None)),
            "containers_bytes": int(sum(int(i.get("SizeRw") or 0) for i in labeled)),
            "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        }
        result: dict[str, Any] = self.workspace.post("/report", body, timeout=60)
        self._handle_stray([str(n) for n in result.get("stray") or []])
        self.sync_procs(result.get("procs") or [])
        live = {e["env_id"] for e in envs if e["state"] == "running"}
        for env_id in self.proxy.envs() - live - {BUILD_ENV}:
            self.proxy.close(env_id)
        return result

    def _disk_emergency(self, labeled: list[dict[str, Any]]) -> None:
        """磁盘告急（> 95%）：本机直接停止一次性运行，工作台掉线也守得住。"""
        for item in labeled:
            labels = item.get("Labels") or {}
            if labels.get(c.LABEL_LIFETIME) == "once" and item.get("State") == "running":
                name = str((item.get("Names") or ["/"])[0]).lstrip("/")
                logger.warning("磁盘告急，停止一次性环境 %s", name)
                self.docker.stop(name, seconds=5)

    def _handle_stray(self, names: list[str]) -> None:
        """记录外的容器（都带工作台标签）：先停止，停了 24 小时以上的删除。"""
        for name in names:
            try:
                c.check_name(name, "env")
            except c.ParamError:
                continue
            info = self.docker.inspect(name)
            if (
                info is None
                or (info.get("Config") or {}).get("Labels", {}).get(c.LABEL_RUNNER) != "1"
            ):
                continue
            state = info.get("State") or {}
            if state.get("Running"):
                self.docker.stop(name, seconds=10)
                continue
            finished = str(state.get("FinishedAt") or "")
            try:
                ended = datetime.fromisoformat(finished.replace("Z", "+00:00")[:26] + "+00:00")
            except ValueError:
                continue
            if (datetime.now(UTC) - ended).total_seconds() > STRAY_DELETE_SECONDS:
                self.docker.remove(name)


def _meminfo() -> tuple[int, int]:
    total = free = 0
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                amount = int(value.strip().split()[0]) * 1024 if value.strip() else 0
                if key == "MemTotal":
                    total = amount
                elif key == "MemAvailable":
                    free = amount
    except (OSError, ValueError, IndexError):
        pass
    return total, free


__all__ = ["Agent", "CommandError", "Config", "check_image", "dockerfile_of", "registry_host"]
