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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from bridge.drivers.base import DriverError, LoginChallenge

START_SECONDS = 40.0  # 本机服务启动最多等多久
LOGIN_SECONDS = 600.0  # 人去网页上登录最多等多久
POLL_SECONDS = 2.0


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
        self.challenge: LoginChallenge | None = None
        self.account: str | None = None  # 登录成功后官方程序报告的账号名

    async def _call(self, method: str, path: str, body: Any = None, wait: float = 10) -> Any:
        assert self._server is not None  # noqa: S101
        return await asyncio.to_thread(_request, self._server, method, path, body, wait)

    async def _start(self, env: dict[str, str]) -> None:
        password = secrets.token_hex(24)
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
                stdout=asyncio.subprocess.DEVNULL,  # 启动信息里有访问口令：不留
                stderr=asyncio.subprocess.DEVNULL,
                env={**env, "CODEBUDDY_GATEWAY_PASSWORD": password},
            )
        except OSError as error:
            raise DriverError("unavailable", f"启动 {self._binary} 失败：{error}") from error
        self._server = _Server(proc, port, password)
        deadline = asyncio.get_running_loop().time() + START_SECONDS
        while True:
            if proc.returncode is not None:
                await self.stop()
                raise DriverError("unavailable", f"{self._label} 的登录服务没有启动起来")
            with contextlib.suppress(OSError, ValueError, urllib.error.URLError):
                await self._call("GET", "/api/v1/auth/account/status", wait=3)
                return
            if asyncio.get_running_loop().time() > deadline:
                await self.stop()
                raise DriverError("timeout", f"{self._label} 的登录服务启动超时")
            await asyncio.sleep(0.5)

    async def stop(self) -> None:
        """关掉本机服务（登录完成、失败、超时、重新开始时）。"""
        watch, self._watch = self._watch, None
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()
        server, self._server = self._server, None
        if server is not None and server.proc.returncode is None:
            server.proc.terminate()
            try:
                await asyncio.wait_for(server.proc.wait(), 5)
            except TimeoutError:
                server.proc.kill()

    async def begin(self, site: str, env: dict[str, str], done: Any) -> LoginChallenge:
        """开始一次登录：返回要人打开的网址。done(账号名) 在登录成功时调用一次。"""
        await self.stop()
        self.challenge, self.account = None, None
        await self._start(env)
        try:
            answer = await self._call(
                "POST", "/api/v1/auth/account/login", {"method": site}, wait=60
            )
        except (OSError, ValueError, urllib.error.URLError) as error:
            await self.stop()
            raise DriverError("platform_error", f"{self._label} 的登录服务没有应答") from error
        url = answer.get("authUrl")
        if not isinstance(url, str) or not url.startswith("https://"):
            await self.stop()
            reason = str(answer.get("loginError") or "没有给出登录网址")[:200]
            raise DriverError(
                "platform_error",
                f"向 {self._label} 申请登录网址没有成功：{reason}。"
                "常见原因：这个 MCP 管理器所在的服务器访问不了这个站点"
                "（看这个平台卡片上的“平台连通性”），可以换另一个站点或改用 API 密钥",
            )
        self.challenge = LoginChallenge(state="pending", verification_url=url)
        self._watch = asyncio.create_task(self._wait(done), name="web-login")
        return self.challenge

    async def _wait(self, done: Any) -> None:
        """等人在网页上登录完：登录成功、失败或超时后关掉本机服务。"""
        deadline = asyncio.get_running_loop().time() + LOGIN_SECONDS
        result = LoginChallenge(state="failed", detail="等了 10 分钟没有完成登录，请重新开始")
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
                    result = LoginChallenge(state="done")
                    break
                if status.get("loginError") and not status.get("loginPending"):
                    reason = str(status["loginError"])[:200]
                    result = LoginChallenge(state="failed", detail=f"登录没有完成：{reason}")
                    break
        finally:
            self.challenge = result
            await self.stop()

    async def logout(self, env: dict[str, str]) -> None:
        """让官方程序退出它自己保存的登录（尽力而为）。"""
        await self.stop()
        self.challenge, self.account = None, None
        try:
            await self._start(env)
            await self._call("POST", "/api/v1/auth/account/logout", {}, wait=20)
        except (DriverError, OSError, ValueError, urllib.error.URLError):
            pass
        finally:
            await self.stop()
