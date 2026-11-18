"""远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 6、8、9、21 节）。

官方程序不在管理器里跑，而在容器服务器上的远程开发容器里跑。管理器不连工作台：
它把要做的事排成“远程操作”，工作台用长轮询取走（GET /admin/remote/ops），在容器服务器上做完后
回报结果；托管进程的输出由工作台一块一块推回来（POST /admin/remote/procs/{号}/output），
结束时再推一次（.../exit）。

- RemoteHub：操作队列、等结果、远程进程登记。凭据和标准输入只在内存里（不进库）：
  管理器重启后没做完的操作作废，对应的回合按现有规则记“结果未知”；
- RemoteProcess：和 asyncio.subprocess.Process 一样用（stdout.readline()、stderr.read()、wait()、
  returncode、terminate()、kill()），驱动里读输出的代码不用改；标准输入在起进程时一次给全；
- RemoteLogins：会刷新的登录（ChatGPT 设备码登录、Claude 网页授权）不下发，在容器里单独登录
  同一个账号；登录后在容器里查一次账号，和管理器里这个平台的账号比对。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from bridge.drivers import progress
from bridge.drivers.base import DriverError, ErrorKind, LoginChallenge
from bridge.redact import mask_identity, redact
from bridge.store import Row, Store, new_id

logger = logging.getLogger("bridge.remote")

# ---- 容器里的路径（21.1）----
CONTAINER_ROOT = "/workspace"
SESSIONS_DIR = "/workspace/sessions"
REMOTE_HOME = "/workspace/.home"
REMOTE_PATH = "/opt/mw/cli/bin:/usr/local/bin:/usr/bin:/bin"
# 账号槽位 → (配置目录的环境变量, 容器里的配置目录)
REMOTE_CONFIG = {
    "claude": ("CLAUDE_CONFIG_DIR", "/workspace/.home/.claude"),
    "workbuddy": ("CODEBUDDY_CONFIG_DIR", "/workspace/.home/.codebuddy"),
    "openai": ("CODEX_HOME", "/workspace/.home/.codex"),
}
# 账号槽位 → 容器里的命令行（工作台按它校验 argv[0]）和远程操作里的平台名
PROGRAMS = {"claude": "claude", "workbuddy": "codebuddy", "openai": "codex"}
PLATFORM_OF_SLOT = {"claude": "claude", "workbuddy": "workbuddy", "openai": "codex"}
# 远程 Codex 的图片放在会话目录里的这个文件夹（codex exec -i 要容器里的路径）
IMAGE_DIR = ".mw-images"

_WORKDIR = re.compile(r"^remote:([\w.\-:]{1,100}):(/workspace/sessions/[\w.\-]{1,100})$")
CONTAINER_RE = re.compile(r"^[\w.\-:]{1,100}$")

RESULT_TIMEOUT = 60.0  # 工作台多久没回结果算失败（容器服务器或工作台不可用）
RESEND_SECONDS = 60.0  # 取走了却没回结果的操作，过这么久再给一次（工作台按操作号去重）
MAX_OUTPUT_BYTES = 64 * 1024 * 1024  # 一个托管进程最多收这么多输出（第 7 节）
MAX_STDIN_BYTES = 2 * 1024 * 1024
MAX_PUT_BYTES = 20 * 1024 * 1024
MAX_INPUT_BYTES = 4096
VERSION_CACHE_SECONDS = 300.0
KEEP_DAYS = 7
FINISHED_KEEP = 500  # 记住最近结束的进程号：重复的“结束”回报不算错
_SECRET_ARGS = ("secret_env", "stdin_b64", "data", "files")


def remote_workdir(container: str, session_id: str) -> str:
    """会话记录里远程会话的 workdir：remote:<容器号>:/workspace/sessions/<会话号>。"""
    return f"remote:{container}:{SESSIONS_DIR}/{session_id}"


def parse_workdir(workdir: str | None) -> tuple[str, str] | None:
    """(容器号, 容器里的工作目录)；不是远程会话返回 None。"""
    match = _WORKDIR.match(workdir or "")
    return (match.group(1), match.group(2)) if match else None


def remote_env(slot: str) -> dict[str, str]:
    """容器里官方程序的基本环境：HOME、配置目录、PATH（凭据另外放进 secret_env）。"""
    name, path = REMOTE_CONFIG[slot]
    return {"HOME": REMOTE_HOME, name: path, "PATH": REMOTE_PATH, "LANG": "C.UTF-8"}


def parse_version(text: str) -> str | None:
    match = re.search(r"(\d+\.\d+\.\d+)", text or "")
    return match.group(1) if match else None


class Gap(Exception):
    """推来的输出从已确认处之后开始（中间缺了一段）：告诉工作台从 acked 处重发。"""

    def __init__(self, acked: int) -> None:
        super().__init__(f"从第 {acked} 字节接着发")
        self.acked = acked


class _Tail:
    """和 asyncio 子进程的 stderr 一样用：read() 在进程结束后给出最后一段错误输出。"""

    def __init__(self, proc: RemoteProcess) -> None:
        self._proc = proc

    async def read(self, n: int = -1) -> bytes:
        await self._proc._done.wait()
        return self._proc.stderr_tail.encode()


class RemoteProcess:
    """远程容器里的一个托管进程。用法和 asyncio.subprocess.Process 一样（标准输入除外）。"""

    pid: int | None = None
    stdin: None = None

    def __init__(self, hub: RemoteHub, proc_id: str, container: str, purpose: str) -> None:
        self.hub = hub
        self.id = proc_id
        self.container = container
        self.purpose = purpose
        self.stdout = asyncio.StreamReader(limit=16 * 1024 * 1024)
        self.stderr = _Tail(self)
        self.returncode: int | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.stderr_tail = ""
        self.received = 0
        self.run_id: str | None = None
        self._done = asyncio.Event()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def feed(self, offset: int, data: bytes) -> int:
        """工作台推来的一块输出；返回新的已确认字节数。重复的部分丢掉，中间缺了抛 Gap。"""
        if offset > self.received:
            raise Gap(self.received)
        fresh = data[self.received - offset :]
        if fresh:
            if self.received + len(fresh) > MAX_OUTPUT_BYTES:
                # 输出过大：停掉进程，回合失败并写明原因
                self.hub.signal(self, "KILL")
                self.finish(None, "failed", "输出过大（超过 64 MB），已停止", "")
                return self.received
            self.stdout.feed_data(fresh)
            self.received += len(fresh)
        return self.received

    def finish(
        self, exit_code: int | None, state: str, error: str | None, stderr_tail: str
    ) -> None:
        if self._done.is_set():
            return
        self.state = state
        self.error = error
        tail = redact(stderr_tail or "")
        self.stderr_tail = f"{tail}\n{redact(error)}".strip() if error else tail
        fallback = {"killed": -15, "timeout": -9}.get(state, -1)
        self.returncode = exit_code if exit_code is not None else fallback
        self.stdout.feed_eof()
        self._done.set()
        self.hub.forget(self)

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None  # noqa: S101
        return self.returncode

    def terminate(self) -> None:
        if not self.done:
            self.hub.signal(self, "TERM")

    def kill(self) -> None:
        """停掉并不再等它：本地马上算结束（工作台之后推来的输出会被拒绝，它随后停掉进程）。"""
        if not self.done:
            self.hub.signal(self, "KILL")
            self.finish(None, "killed", None, "")

    def send_signal(self, name: str) -> None:
        if not self.done:
            self.hub.signal(self, name)


@dataclass
class _Op:
    id: str
    kind: str
    container: str
    args: Row
    created_at: float
    future: asyncio.Future[Row]
    sent_at: float | None = None


def _public(args: Row) -> Row:
    """存进库里的参数：去掉凭据、标准输入、文件内容。"""
    out = {k: v for k, v in args.items() if k not in _SECRET_ARGS}
    if "secret_env" in args:
        out["secret_env_names"] = sorted(args["secret_env"])
    if "files" in args:
        out["files"] = [f.get("path") for f in args["files"]]
    return out


def _error_kind(code: str) -> ErrorKind:
    return "invalid" if code in ("VALIDATION_FAILED", "NOT_SUPPORTED") else "unavailable"


class RemoteHub:
    def __init__(self, store: Store, *, result_timeout: float = RESULT_TIMEOUT) -> None:
        self.store = store
        self.result_timeout = result_timeout
        self._ops: OrderedDict[str, _Op] = OrderedDict()
        self._procs: dict[str, RemoteProcess] = {}
        self._finished: OrderedDict[str, None] = OrderedDict()
        self._wake = asyncio.Event()
        self._versions: dict[str, tuple[float, dict[str, str]]] = {}
        self._background: set[asyncio.Task[Any]] = set()

    # ---- 生命周期 ----
    def startup(self) -> None:
        reason = "管理器重启，这个远程操作作废"
        failed = self.store.fail_open_remote_ops(reason)
        lost = self.store.lose_running_remote_procs("管理器重启，不知道远程进程做到哪一步")
        if failed or lost:
            logger.info("重启：作废 %s 个远程操作，%s 个远程进程记为丢失", failed, lost)
        self.purge()

    def purge(self) -> None:
        """远程操作和远程进程的记录留 7 天（只用来查问题，不含凭据和提问）。"""
        before = time.time() - KEEP_DAYS * 86400
        self.store.purge_remote_ops(before)
        self.store.purge_remote_procs(before)

    async def shutdown(self) -> None:
        for op in list(self._ops.values()):
            if not op.future.done():
                op.future.set_exception(DriverError("unavailable", "管理器正在关闭"))
        self._ops.clear()
        for proc in list(self._procs.values()):
            proc.finish(None, "lost", "管理器正在关闭", "")
        for task in list(self._background):
            task.cancel()

    # ---- 操作：管理器这边 ----
    async def call(
        self, container: str, kind: str, args: Row, *, seconds: float | None = None
    ) -> Row:
        """排一个远程操作并等工作台回结果。没人取走或者失败抛 DriverError。"""
        if not CONTAINER_RE.match(container):
            raise DriverError("invalid", "远程容器号不合法")
        op_id = new_id("ro")
        future: asyncio.Future[Row] = asyncio.get_running_loop().create_future()
        op = _Op(op_id, kind, container, args, time.time(), future)
        self.store.create_remote_op(op_id, kind, container, _public(args))
        self._ops[op_id] = op
        self._wake.set()
        wait = seconds if seconds is not None else self.result_timeout
        try:
            return await asyncio.wait_for(asyncio.shield(future), wait)
        except TimeoutError:
            self._ops.pop(op_id, None)
            self.store.update_remote_op(
                op_id, status="failed", error="timeout", finished_at=time.time()
            )
            raise DriverError(
                "unavailable",
                f"工作台没有在 {round(wait)} 秒内处理远程操作（工作台或容器服务器暂时不可用）",
            ) from None
        except asyncio.CancelledError:
            self._ops.pop(op_id, None)
            self.store.update_remote_op(
                op_id, status="failed", error="cancelled", finished_at=time.time()
            )
            raise

    # ---- 操作：工作台这边 ----
    async def take(self, wait: float, limit: int) -> list[Row]:
        """工作台取远程操作：没取过的，加上取走超过 RESEND_SECONDS 秒还没回结果的。"""
        loop = asyncio.get_running_loop()
        until = loop.time() + max(0.0, wait)
        while True:
            now = time.time()
            picked: list[_Op] = []
            for op in self._ops.values():
                if op.sent_at is None or now - op.sent_at >= RESEND_SECONDS:
                    picked.append(op)
                    if len(picked) >= limit:
                        break
            if picked:
                for op in picked:
                    op.sent_at = now
                    self.store.update_remote_op(op.id, status="sent", sent_at=now)
                return [
                    {
                        "id": op.id,
                        "kind": op.kind,
                        "container": op.container,
                        "args": op.args,
                        "created_at": op.created_at,
                    }
                    for op in picked
                ]
            left = until - loop.time()
            if left <= 0:
                return []
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), left)

    def result(self, op_id: str, ok: bool, data: Row | None, error: Row | None) -> bool:
        """工作台回报结果。返回 False 表示不认识这个操作（已经回报过的也认）。"""
        op = self._ops.pop(op_id, None)
        if op is None:
            row = self.store.get_remote_op(op_id)
            return row is not None and row["status"] in ("done", "failed")
        now = time.time()
        if ok:
            self.store.update_remote_op(op_id, status="done", op_result=data or {}, finished_at=now)
            if not op.future.done():
                op.future.set_result(data or {})
        else:
            code = str((error or {}).get("code") or "UNAVAILABLE")
            message = str((error or {}).get("message") or "远程操作失败")[:500]
            self.store.update_remote_op(
                op_id, status="failed", error=f"{code}: {message}", finished_at=now
            )
            if not op.future.done():
                op.future.set_exception(
                    DriverError(_error_kind(code), f"远程容器：{redact(message)}")
                )
        return True

    def pending_count(self) -> int:
        return len(self._ops)

    # ---- 托管进程 ----
    async def spawn(
        self,
        container: str,
        *,
        purpose: str,
        platform: str | None,
        session_id: str | None,
        argv: list[str],
        cwd: str,
        env: dict[str, str],
        secret_env: dict[str, str] | None = None,
        stdin: bytes = b"",
        timeout_seconds: int = 1800,
        interactive: bool = False,
        extra: Row | None = None,
    ) -> RemoteProcess:
        """在远程容器里起一个托管进程（start_proc），返回远程进程对象。"""
        if len(stdin) > MAX_STDIN_BYTES:
            raise DriverError("invalid", "提问太长：远程容器的标准输入最多 2 MB")
        proc_id = new_id("rp")
        proc = RemoteProcess(self, proc_id, container, purpose)
        self._procs[proc_id] = proc  # 先登记：输出可能比 start_proc 的结果先到
        self.store.create_remote_proc(
            id=proc_id,
            container=container,
            purpose=purpose,
            platform=platform,
            session_id=session_id,
        )
        args: Row = {
            "proc_id": proc_id,
            "purpose": purpose,
            "platform": platform,
            "session_id": session_id,
            "argv": argv,
            "cwd": cwd,
            "env": env,
            "secret_env": dict(secret_env or {}),
            "stdin_b64": base64.b64encode(stdin).decode(),
            "timeout_seconds": int(timeout_seconds),
            "interactive": interactive,
            **(extra or {}),
        }
        try:
            data = await self.call(container, "start_proc", args)
        except BaseException:
            self._procs.pop(proc_id, None)
            self.store.update_remote_proc(proc_id, state="failed", finished_at=time.time())
            raise
        proc.run_id = str(data.get("run_id") or "") or None
        self.store.update_remote_proc(proc_id, run_id=proc.run_id, state="running")
        return proc

    def output(self, proc_id: str, offset: int, data: bytes) -> int:
        """工作台推来的输出。不认识的进程抛 KeyError（404，工作台随后停掉它）。"""
        proc = self._procs.get(proc_id)
        if proc is None or proc.done:
            raise KeyError(proc_id)
        return proc.feed(offset, data)

    def exit(self, proc_id: str, info: Row) -> bool:
        """进程结束。返回 False 表示不认识（刚结束过的重复回报算认识）。"""
        proc = self._procs.get(proc_id)
        if proc is None:
            return proc_id in self._finished or self.store.get_remote_proc(proc_id) is not None
        code = info.get("exit_code")
        proc.finish(
            code if isinstance(code, int) and not isinstance(code, bool) else None,
            str(info.get("state") or "exited"),
            str(info["error"]) if info.get("error") else None,
            str(info.get("stderr_tail") or ""),
        )
        return True

    def forget(self, proc: RemoteProcess) -> None:
        """进程结束（或者本地不再等它）：从登记里拿掉，记下结果。"""
        if self._procs.pop(proc.id, None) is None:
            return
        self._finished[proc.id] = None
        while len(self._finished) > FINISHED_KEEP:
            self._finished.popitem(last=False)
        self.store.update_remote_proc(
            proc.id,
            state=proc.state or "exited",
            exit_code=proc.returncode,
            received=proc.received,
            error=proc.error,
            finished_at=time.time(),
        )

    def signal(self, proc: RemoteProcess, name: str) -> None:
        """给远程进程发信号（不等结果）。"""

        async def send() -> None:
            with contextlib.suppress(DriverError):
                await self.call(proc.container, "signal_proc", {"proc_id": proc.id, "signal": name})

        task = asyncio.get_running_loop().create_task(send(), name=f"signal-{proc.id}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def send_input(self, proc: RemoteProcess, data: str) -> None:
        if len(data.encode()) > MAX_INPUT_BYTES:
            raise DriverError("invalid", "输入太长（最多 4 KB）")
        await self.call(proc.container, "input_proc", {"proc_id": proc.id, "data": data})

    # ---- 辅助操作 ----
    async def put_files(
        self, container: str, session_id: str, items: list[tuple[str, bytes]]
    ) -> int:
        """把文件写进远程会话的工作目录（路径相对会话目录）。"""
        if sum(len(data) for _, data in items) > MAX_PUT_BYTES:
            raise DriverError("invalid", "一次写进远程容器的文件合计不能超过 20 MB")
        files = [{"path": path, "b64": base64.b64encode(data).decode()} for path, data in items]
        data = await self.call(
            container, "put_files", {"session_id": session_id, "files": files}, seconds=120
        )
        return int(data.get("written") or 0)

    async def helper(self, container: str, name: str, **args: Any) -> Row:
        return await self.call(container, "helper", {"name": name, **args})

    async def transcript_exists(self, container: str, slot: str, native_id: str) -> bool:
        data = await self.helper(
            container, "transcript_exists", platform=PLATFORM_OF_SLOT[slot], native_id=native_id
        )
        return bool(data.get("exists"))

    async def versions(self, container: str, *, refresh: bool = False) -> dict[str, str]:
        """容器里三个命令行的版本（{"codex": "0.169.0", ...}），缓存几分钟。"""
        cached = self._versions.get(container)
        if cached and not refresh and time.monotonic() - cached[0] < VERSION_CACHE_SECONDS:
            return cached[1]
        data = await self.helper(container, "versions")
        raw = data.get("versions") if isinstance(data.get("versions"), dict) else {}
        found = {str(k): str(v) for k, v in (raw or {}).items() if isinstance(v, str)}
        self._versions[container] = (time.monotonic(), found)
        return found

    def drop_versions(self, container: str) -> None:
        self._versions.pop(container, None)

    async def prepare(
        self,
        container: str,
        session_id: str,
        workdir: str,
        repo_url: str | None,
        branch: str | None,
        limit_seconds: float,
    ) -> None:
        """建远程会话的工作目录，按环境配置克隆仓库（工作台自己拼命令，第 21.2 节）。"""
        proc = await self.spawn(
            container,
            purpose="prepare",
            platform=None,
            session_id=session_id,
            argv=[],
            cwd=workdir,
            env={},
            timeout_seconds=int(limit_seconds),
            extra={"repo_url": repo_url or None, "branch": branch or None},
        )
        try:
            code = await asyncio.wait_for(proc.wait(), limit_seconds + 60)
        except TimeoutError:
            proc.kill()
            raise DriverError("timeout", "准备远程工作目录超时（克隆仓库太慢？）") from None
        if code != 0:
            tail = proc.stderr_tail.strip()[-300:] or f"退出码 {code}"
            raise DriverError("unavailable", f"准备远程工作目录失败：{tail}")

    async def run_short(
        self,
        container: str,
        slot: str,
        argv: list[str],
        *,
        purpose: str = "login",
        limit_seconds: float = 60.0,
    ) -> tuple[int, str]:
        """在容器里跑一条很快结束的命令（例如查登录状态）。

        返回 (退出码, 标准输出加错误输出的末尾)。"""
        proc = await self.spawn(
            container,
            purpose=purpose,
            platform=PLATFORM_OF_SLOT[slot],
            session_id=None,
            argv=argv,
            cwd=REMOTE_HOME,
            env=remote_env(slot),
            timeout_seconds=int(limit_seconds),
        )
        try:
            out = await asyncio.wait_for(proc.stdout.read(), limit_seconds + 30)
            code = await asyncio.wait_for(proc.wait(), 30)
        except TimeoutError:
            proc.kill()
            raise DriverError("timeout", f"{' '.join(argv[:3])} 超时") from None
        return code, out.decode(errors="replace") + "\n" + proc.stderr_tail


# ---- 容器里的登录（方案第 9 节乙）----
LOGIN_STEPS = ("start", "request_url", "wait_user", "submit_code", "finish")
LOGIN_SECONDS = 900.0
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")
_URL = re.compile(r"https://\S+")


@dataclass
class _Recipe:
    """一个平台在容器里怎么登录、怎么查账号（命令在锁定版本上核对过）。"""

    label: str
    argv: list[str]
    status_argv: list[str]
    needs_code: bool
    method: str


# Claude Code 2.1.323：`claude auth login --claudeai` 给出网址，从标准输入读授权码（T-0287 核实）；
#   `claude auth status` 输出 JSON（loggedIn、email）。
# Codex 0.169.0：`codex login --device-auth` 在标准输出给出网址和一次性验证码
#   （codex-rs/login/src/device_code_auth.rs 的 device_code_prompt），成功退出 0；
#   `codex login status` 在错误输出写 “Logged in using ChatGPT” / “Not logged in”
#   （codex-rs/cli/src/login.rs 的 run_login_status），不报邮箱。
RECIPES = {
    "claude": _Recipe(
        "Claude",
        ["claude", "auth", "login", "--claudeai"],
        ["claude", "auth", "status"],
        needs_code=True,
        method="web",
    ),
    "openai": _Recipe(
        "Codex",
        ["codex", "login", "--device-auth"],
        ["codex", "login", "status"],
        needs_code=False,
        method="chatgpt",
    ),
}
NO_REMOTE_LOGIN = {
    "workbuddy": "CodeBuddy 在远程容器里只能用 API 密钥："
    "请在这个管理器里用 API 密钥登录 CodeBuddy，每个回合会自动带上"
    "（网页登录要在容器里开本机服务，远程容器做不到）",
}


@dataclass
class _Login:
    challenge: LoginChallenge
    proc: RemoteProcess | None = None
    task: asyncio.Task[None] | None = None
    # 拿到网址（或者失败）时设上：start() 等它，页面拿到就能显示
    shown: asyncio.Event = field(default_factory=asyncio.Event)

    def set(self, **fields: Any) -> None:
        """LoginChallenge 不可变：换一个新的（步骤、运行信息两个列表还是同一份）。"""
        self.challenge = replace(self.challenge, **fields)


IdentityOf = Callable[[str], Awaitable[tuple[str | None, str | None]]]


def identity_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


class RemoteLogins:
    """容器里的登录：开始、交授权码、取消、查进度；登录后比对账号。"""

    def __init__(self, hub: RemoteHub, store: Store, identity_of: IdentityOf) -> None:
        self.hub = hub
        self.store = store
        self._identity_of = identity_of
        self._logins: dict[tuple[str, str], _Login] = {}

    def state(self, container: str, slot: str) -> LoginChallenge | None:
        login = self._logins.get((container, slot))
        return login.challenge if login else None

    async def start(self, container: str, slot: str) -> LoginChallenge:
        if slot in NO_REMOTE_LOGIN:
            raise DriverError("not_supported", NO_REMOTE_LOGIN[slot])
        recipe = RECIPES.get(slot)
        if recipe is None:
            raise DriverError("not_supported", "这个平台不能在远程容器里登录")
        await self.cancel(container, slot)
        steps, log = progress.new_steps(LOGIN_STEPS), list[str]()
        if not recipe.needs_code:
            progress.mark(steps, "submit_code", "skipped")
        challenge = LoginChallenge(
            state="pending", steps=steps, log=log, started_at=progress.iso(progress.now())
        )
        login = _Login(challenge)
        self._logins[(container, slot)] = login
        progress.mark(steps, "start", "running")
        progress.note(log, f"在远程容器里启动 {recipe.label} 官方程序：{' '.join(recipe.argv[1:])}")
        try:
            login.proc = await self.hub.spawn(
                container,
                purpose="login",
                platform=PLATFORM_OF_SLOT[slot],
                session_id=None,
                argv=list(recipe.argv),
                cwd=REMOTE_HOME,
                env=remote_env(slot),
                timeout_seconds=int(LOGIN_SECONDS),
                interactive=recipe.needs_code,
            )
        except DriverError as error:
            self._fail(login, "start", error.message)
            return login.challenge
        progress.mark(steps, "start", "done")
        progress.mark(steps, "request_url", "running")
        login.task = asyncio.get_running_loop().create_task(
            self._watch(container, slot, recipe, login), name=f"remote-login-{slot}"
        )
        # 等网址出来（最多 60 秒），页面拿到就能显示
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(login.shown.wait(), 60)
        return login.challenge

    async def submit_code(self, container: str, slot: str, code: str) -> LoginChallenge:
        login = self._logins.get((container, slot))
        if login is None or login.proc is None or not login.challenge.needs_code:
            raise DriverError("conflict", "没有在等授权码的登录：请先点“登录”")
        code = code.strip()
        if not code or len(code) > 4000 or "\n" in code:
            raise DriverError("invalid", "授权码不对")
        progress.mark(login.challenge.steps, "wait_user", "done")
        progress.mark(login.challenge.steps, "submit_code", "running")
        progress.note(login.challenge.log, "把授权码交给容器里的官方程序（授权码不记在这里）")
        try:
            await self.hub.send_input(login.proc, code + "\n")
        except DriverError as error:
            self._fail(login, "submit_code", error.message)
            return login.challenge
        login.set(needs_code=False)
        return login.challenge

    async def cancel(self, container: str, slot: str) -> None:
        login = self._logins.pop((container, slot), None)
        if login is None:
            return
        if login.task is not None:
            login.task.cancel()
        if login.proc is not None:
            login.proc.kill()

    async def _watch(self, container: str, slot: str, recipe: _Recipe, login: _Login) -> None:
        proc = login.proc
        assert proc is not None  # noqa: S101
        steps, log = login.challenge.steps, login.challenge.log
        lines: list[str] = []
        try:
            async with asyncio.timeout(LOGIN_SECONDS):
                while raw := await proc.stdout.readline():
                    line = _ESCAPES.sub("", raw.decode(errors="replace")).strip()
                    if not line:
                        continue
                    lines.append(line)
                    self._parse(login, lines, recipe)
                code = await proc.wait()
        except TimeoutError:
            proc.kill()
            self._fail(login, "wait_user", "等了 15 分钟还没有完成登录，已取消")
            return
        if code != 0:
            tail = redact(proc.stderr_tail.strip() or (lines[-1] if lines else ""))[-300:]
            asked = recipe.needs_code and not login.challenge.needs_code
            self._fail(
                login,
                "submit_code" if asked else "wait_user",
                f"登录没有成功：{tail or f'退出码 {code}'}",
            )
            return
        for key in ("request_url", "wait_user", "submit_code"):
            if next(s for s in steps if s["key"] == key)["status"] in ("pending", "running"):
                progress.mark(steps, key, "done")
        progress.mark(steps, "finish", "running")
        progress.note(log, "登录成功，在容器里查一次账号")
        try:
            await self._check(container, slot, recipe)
        except DriverError as error:
            self._fail(login, "finish", f"登录成功了，但查不到账号：{error.message}")
            return
        row = self.store.get_remote_login(container, slot) or {}
        if row.get("state") == "mismatch":
            self._fail(login, "finish", str(row.get("detail") or "登录的不是同一个账号"))
            return
        progress.mark(steps, "finish", "done", str(row.get("detail") or ""))
        login.set(state="done", needs_code=False, finished_at=progress.iso(progress.now()))
        login.shown.set()

    def _parse(self, login: _Login, lines: list[str], recipe: _Recipe) -> None:
        challenge = login.challenge
        line = lines[-1]
        if not challenge.verification_url:
            url = _URL.search(line)
            if url:
                login.set(verification_url=url.group(0), needs_code=recipe.needs_code)
                progress.mark(challenge.steps, "request_url", "done")
                progress.mark(challenge.steps, "wait_user", "running")
                progress.note(challenge.log, "拿到登录网址：请在自己的浏览器里打开并登录")
                if recipe.needs_code:
                    login.shown.set()
            return
        # Codex 设备码：“2. Enter this one-time code …” 下一行就是验证码
        if (
            not recipe.needs_code
            and not challenge.user_code
            and len(lines) >= 2
            and "one-time code" in lines[-2]
            and re.fullmatch(r"[A-Z0-9-]{4,20}", line)
        ):
            login.set(user_code=line)
            login.shown.set()

    def _fail(self, login: _Login, step: str, detail: str) -> None:
        progress.mark(login.challenge.steps, step, "failed", detail)
        progress.note(login.challenge.log, detail)
        login.set(
            state="failed",
            detail=redact(detail)[:500],
            needs_code=False,
            finished_at=progress.iso(progress.now()),
        )
        login.shown.set()
        if login.proc is not None:
            login.proc.kill()

    async def _check(self, container: str, slot: str, recipe: _Recipe) -> None:
        """在容器里查账号，和管理器里这个平台的账号比对，结果存进 remote_logins。"""
        code, text = await self.hub.run_short(container, slot, list(recipe.status_argv))
        identity: str | None = None
        if slot == "claude":
            try:
                data = json.loads(text.strip().splitlines()[0]) if text.strip() else {}
            except (ValueError, IndexError):
                data = {}
            if not isinstance(data, dict) or not data.get("loggedIn"):
                raise DriverError("not_logged_in", "容器里的 Claude 报告没有登录")
            identity = str(data.get("email") or "") or None
        elif code != 0 or "Logged in" not in text:
            raise DriverError("not_logged_in", "容器里的 Codex 报告没有登录")
        await self.record(container, slot, identity, recipe.method)

    async def record(self, container: str, slot: str, identity: str | None, method: str) -> None:
        local_hash, local_label = await self._identity_of(slot)
        if identity is None:
            self.store.save_remote_login(
                container,
                slot,
                state="ready",
                method=method,
                identity_hash=None,
                identity_label=None,
                detail="容器里的官方程序不报告账号：请确认登录的是和这个管理器同一个账号",
            )
            return
        digest = identity_hash(identity)
        label = mask_identity(identity)
        if local_hash and local_label and local_hash != digest:
            self.store.save_remote_login(
                container,
                slot,
                state="mismatch",
                method=method,
                identity_hash=digest,
                identity_label=label,
                detail=f"容器里登录的是另一个账号（{label}），这个管理器登记的是 {local_label}："
                "一个管理器每个平台只能用一个账号，请换成同一个账号重新登录",
            )
            return
        self.store.save_remote_login(
            container,
            slot,
            state="ready",
            method=method,
            identity_hash=digest,
            identity_label=label,
            detail="",
        )
