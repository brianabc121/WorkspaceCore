"""CodeBuddy Code 的网页登录（T-0241）：页面给出一个网址，人在自己的浏览器里打开并登录。

不用进容器操作官方程序。做法取自锁定版本 CodeBuddy Code 2.184.0 的本机服务模式
（`codebuddy --serve`），在本会话里实测过接口的形状（没有账号，登录成功之后的那一段没有实测）：

- 启动：`codebuddy --serve --port <端口> --host 127.0.0.1`，访问口令用环境变量
  `CODEBUDDY_GATEWAY_PASSWORD`（32–256 个字符）给；每个请求要带 `x-codebuddy-request: 1` 和
  `Authorization: Bearer <口令>`，不带口令是 401。
- `GET /api/v1/auth/account/status` → `{"data": {"authenticated": false, "loginMethods": [...],
  "loginPending"?, "loginError"?}}`，登录后是
  `{"authenticated": true, "account": {"userId", "userName", ...}}`。
  登录方式的 id：`internal`（国内站）、`external`（国际站）、`selfhosted`、`iOA`。
- `POST /api/v1/auth/account/login`，请求体 `{"method": "<id>"}` → `{"data": {"success": true,
  "authUrl"?, "loginError"?}}`：`authUrl` 就是要人打开的网址。
- `POST /api/v1/auth/account/logout`。

这个本机服务只在登录期间开着：只监听容器里的 127.0.0.1、口令每次随机、登录完成或超时就关掉。
登录信息由官方程序写进它自己的配置目录，之后的回合（`codebuddy -p`）直接用。

逐步进度（T-0276）：每次登录记下四步（启动登录服务、申请登录网址、等人在网页上登录、确认结果）的
状态、起止时间、说明，以及脱敏后的运行信息（这边做了什么、官方程序本机服务的输出）。
“开始登录”照旧在拿到网址或失败时才返回；“查登录进度”随时能读到这些，页面一边等一边显示。
访问口令、看起来像密钥或令牌的内容一律替换成 ***。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from bridge.drivers.base import DriverError, LoginChallenge

START_SECONDS = 40.0  # 本机服务启动最多等多久
REQUEST_SECONDS = 60.0  # 申请登录网址最多等多久
LOGIN_SECONDS = 600.0  # 人去网页上登录最多等多久
POLL_SECONDS = 2.0
# 逐步进度的四步（顺序即显示顺序）；运行信息最多留多少行、每行多长
STEPS = ("start", "request_url", "wait_user", "finish")
LOG_LINES = 120
LINE_CHARS = 300
# 脱敏：“口令 / 令牌 / 密钥”后面跟着的值，以及 32 个字符以上像密钥的一串
_LABELLED = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|bearer|cookie)\b(\s*[:=]?\s*)(\S+)"
)
_LONG = re.compile(r"[A-Za-z0-9_\-+=]{32,}")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


@dataclass
class _Server:
    proc: asyncio.subprocess.Process
    port: int
    password: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(server: _Server, method: str, path: str, body: Any = None, timeout: float = 10) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}",
        data=data,
        method=method,
        headers={
            "x-codebuddy-request": "1",
            "Authorization": f"Bearer {server.password}",
            "Content-Type": "application/json",
        },
    )
    # 不走代理：这是本机的服务
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode() or "{}")
    data_part = payload.get("data") if isinstance(payload, dict) else None
    return data_part if isinstance(data_part, dict) else {}


class WebLogin:
    """一个平台的网页登录：同一时间只有一次在进行。"""

    def __init__(self, binary: str, label: str) -> None:
        self._binary = binary
        self._label = label
        self._server: _Server | None = None
        self._watch: asyncio.Task[None] | None = None
        self._reader: asyncio.Task[None] | None = None
        self.challenge: LoginChallenge | None = None
        self.account: str | None = None  # 登录成功后官方程序报告的账号名
        # 这一次登录的逐步进度与运行信息（challenge 里引用的就是这两个列表，原地更新）
        self._steps: list[dict[str, Any]] = []
        self._log: list[str] = []
        self._secrets: list[str] = []

    # ---- 逐步进度与运行信息 ----
    def _clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = _LABELLED.sub(lambda m: f"{m[1]}{m[2]}***", text)
        text = _LONG.sub("***", text)
        return " ".join(text.split())[:LINE_CHARS]

    def _note(self, text: str) -> None:
        line = self._clean(text)
        if line:
            self._log.append(f"{_now():%H:%M:%S} {line}")
            del self._log[:-LOG_LINES]

    def _mark(self, key: str, status: str, detail: str | None = None) -> None:
        for step in self._steps:
            if step["key"] != key:
                continue
            step["status"] = status
            if status == "running" and not step["started_at"]:
                step["started_at"] = _iso(_now())
            if status in ("done", "failed", "skipped"):
                step["started_at"] = step["started_at"] or _iso(_now())
                step["finished_at"] = _iso(_now())
            if detail is not None:
                step["detail"] = self._clean(detail)

    def _fail(self, key: str, message: str) -> None:
        """这一步失败：记下原因，后面没轮到的步骤保持“等待”（页面显示为未执行）。"""
        self._mark(key, "failed", message)
        self._note(f"失败：{message}")
        if self.challenge is not None:
            self.challenge = replace(
                self.challenge, state="failed", detail=message, finished_at=_iso(_now())
            )

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        """官方程序本机服务的输出：逐行脱敏后记进运行信息（也免得管道写满卡住它）。"""
        with contextlib.suppress(Exception):
            while line := await stream.readline():
                self._note(line.decode(errors="replace"))

    async def _call(self, method: str, path: str, body: Any = None, wait: float = 10) -> Any:
        assert self._server is not None  # noqa: S101
        return await asyncio.to_thread(_request, self._server, method, path, body, wait)

    async def _start(self, env: dict[str, str]) -> None:
        password = secrets.token_hex(24)
        self._secrets = [password]
        port = _free_port()
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                "--serve",
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
                stdin=asyncio.subprocess.DEVNULL,
                # 启动信息里有访问口令：逐行脱敏后才记下
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**env, "CODEBUDDY_GATEWAY_PASSWORD": password},
            )
        except OSError as error:
            self._note(f"启动 {self._binary} 失败：{error}")
            raise DriverError("unavailable", f"启动 {self._binary} 失败：{error}") from error
        self._server = _Server(proc, port, password)
        self._note(f"启动 {self._binary} --serve（只监听 127.0.0.1:{port}）")
        if proc.stdout is not None:
            self._reader = asyncio.create_task(self._drain(proc.stdout), name="web-login-output")
        deadline = asyncio.get_running_loop().time() + START_SECONDS
        while True:
            if proc.returncode is not None:
                self._note(f"登录服务退出了（退出码 {proc.returncode}）")
                await self.stop()
                raise DriverError("unavailable", f"{self._label} 的登录服务没有启动起来")
            with contextlib.suppress(OSError, ValueError, urllib.error.URLError):
                await self._call("GET", "/api/v1/auth/account/status", wait=3)
                return
            if asyncio.get_running_loop().time() > deadline:
                self._note(f"登录服务 {START_SECONDS:.0f} 秒内没有就绪")
                await self.stop()
                raise DriverError("timeout", f"{self._label} 的登录服务启动超时")
            await asyncio.sleep(0.5)

    async def stop(self) -> None:
        """关掉本机服务（登录完成、失败、超时、重新开始时）。

        取消上一次的等待任务后等它收完尾再往下走，免得它的收尾和新的一次登录交错。
        """
        watch, self._watch = self._watch, None
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watch
        server, self._server = self._server, None
        if server is not None and server.proc.returncode is None:
            server.proc.terminate()
            try:
                await asyncio.wait_for(server.proc.wait(), 5)
            except TimeoutError:
                server.proc.kill()
        reader, self._reader = self._reader, None
        if reader is not None and reader is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(reader, 2)

    async def begin(self, site: str, env: dict[str, str], done: Any) -> LoginChallenge:
        """开始一次登录：返回要人打开的网址。done(账号名) 在登录成功时调用一次。"""
        await self.stop()
        self.account = None
        self._steps = [
            {"key": key, "status": "pending", "detail": "", "started_at": None, "finished_at": None}
            for key in STEPS
        ]
        self._log = []
        self.challenge = LoginChallenge(
            state="pending", steps=self._steps, log=self._log, started_at=_iso(_now())
        )
        loop = asyncio.get_running_loop()
        began = loop.time()
        self._mark("start", "running")
        try:
            await self._start(env)
        except DriverError as error:
            self._fail("start", error.message)
            raise
        self._mark("start", "done", f"用时 {loop.time() - began:.1f} 秒")
        self._note("登录服务已就绪")
        asked = loop.time()
        self._mark("request_url", "running")
        self._note(f"向 {self._label} 申请登录网址（登录方式 {site}）")
        try:
            answer = await self._call(
                "POST", "/api/v1/auth/account/login", {"method": site}, wait=REQUEST_SECONDS
            )
        except (OSError, ValueError, urllib.error.URLError) as error:
            await self.stop()
            message = f"{self._label} 的登录服务没有应答"
            self._note(f"申请登录网址出错：{type(error).__name__} {error}")
            self._fail("request_url", f"{message}（{loop.time() - asked:.0f} 秒）")
            raise DriverError("platform_error", message) from error
        url = answer.get("authUrl")
        if not isinstance(url, str) or not url.startswith("https://"):
            await self.stop()
            reason = str(answer.get("loginError") or "没有给出登录网址")[:200]
            message = (
                f"向 {self._label} 申请登录网址没有成功：{reason}。"
                "常见原因：这个 MCP 管理器所在的服务器访问不了这个站点"
                "（看这个平台卡片上的“平台连通性”），可以换另一个站点或改用 API 密钥"
            )
            self._fail("request_url", message)
            raise DriverError("platform_error", message)
        self._mark("request_url", "done", f"用时 {loop.time() - asked:.1f} 秒")
        self._note("收到登录网址，等你在浏览器里登录")
        minutes = LOGIN_SECONDS / 60
        self._mark(
            "wait_user", "running", f"在你自己的浏览器里打开网址并登录（最多等 {minutes:.0f} 分钟）"
        )
        self.challenge = replace(self.challenge, verification_url=url)
        self._watch = asyncio.create_task(self._wait(done), name="web-login")
        return self.challenge

    async def _wait(self, done: Any) -> None:
        """等人在网页上登录完：登录成功、失败或超时后关掉本机服务。被取消（重新开始、退出）时不改进度。"""
        deadline = asyncio.get_running_loop().time() + LOGIN_SECONDS
        minutes = LOGIN_SECONDS / 60
        outcome: tuple[str, str] | None = (
            "failed",
            f"等了 {minutes:.0f} 分钟没有完成登录，请重新开始",
        )
        try:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(POLL_SECONDS)
                try:
                    status = await self._call("GET", "/api/v1/auth/account/status")
                except (OSError, ValueError, urllib.error.URLError):
                    continue
                if status.get("authenticated"):
                    account = (
                        status.get("account") if isinstance(status.get("account"), dict) else {}
                    )
                    name = account.get("userName") or account.get("userId")
                    self.account = str(name)[:80] if name else None
                    done(self.account)
                    outcome = ("done", "")
                    break
                if status.get("loginError") and not status.get("loginPending"):
                    reason = str(status["loginError"])[:200]
                    outcome = ("failed", f"登录没有完成：{reason}")
                    break
        except asyncio.CancelledError:
            outcome = None
            raise
        finally:
            if outcome is not None:
                state, message = outcome
                if state == "done":
                    self._mark("wait_user", "done", "已在浏览器里登录")
                    self._mark("finish", "done", f"账号：{self.account}" if self.account else "")
                    self._note("登录完成，已关掉登录服务")
                    if self.challenge is not None:
                        self.challenge = replace(
                            self.challenge, state="done", detail="", finished_at=_iso(_now())
                        )
                else:
                    self._fail("wait_user", message)
            await self.stop()

    async def logout(self, env: dict[str, str]) -> None:
        """让官方程序退出它自己保存的登录（尽力而为）。"""
        await self.stop()
        self.challenge, self.account = None, None
        self._steps, self._log = [], []
        try:
            await self._start(env)
            await self._call("POST", "/api/v1/auth/account/logout", {}, wait=20)
        except (DriverError, OSError, ValueError, urllib.error.URLError):
            pass
        finally:
            await self.stop()
