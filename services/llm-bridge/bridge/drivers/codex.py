"""Codex 驱动：常驻一个 `codex app-server`，用 JSON-RPC（每行一条）对话。

接口在 codex-cli 0.169.0 上核验（生成的 schema 加实测），见
docs/decisions/2026-11-13-llm-bridge-verification.md 第 2 节。

- 所有会话共用这一个进程和一个 CODEX_HOME（一个账号）；
- 审批策略固定为 never，沙箱由环境决定；万一收到审批请求一律拒绝；
- `chatgpt.chat` 也走这里：同一个 ChatGPT 登录，只读沙箱、空目录，当作纯聊天。

设备码登录的逐步进度（T-0279，和 CodeBuddy 网页登录同一个样子）：启动 Codex 的本机程序、
向 OpenAI 申请设备码、等人在浏览器里输入验证码并登录、确认结果。每一步的状态、起止时间、说明，
以及这边做了什么（脱敏后的运行信息）都记在 LoginChallenge 里，“查登录进度”随时读到；
验证码只给页面，不记进运行信息。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from bridge import __version__, quota, remote
from bridge.drivers import progress
from bridge.drivers.base import (
    JOBS_INSTRUCTIONS,
    AccountStatus,
    ContextUsage,
    Driver,
    DriverError,
    ErrorKind,
    EventSink,
    LoginChallenge,
    Message,
    Mode,
    ModelInfo,
    NativeSession,
    SessionSpec,
    TurnOutcome,
    positive_int,
    program_env,
)
from bridge.redact import redact

logger = logging.getLogger("bridge.codex")

# 设备码登录的四步（顺序即显示顺序）；申请设备码最多等多久
DEVICE_STEPS = ("start", "request_code", "wait_user", "finish")
REQUEST_CODE_SECONDS = 60.0

CHAT_INSTRUCTIONS = (
    "This session is a plain chat. Answer the user's message directly. "
    "Do not read or modify files and do not run commands, and never pretend to. "
    "If the user asks for something that needs it, say plainly, in the user's language, that "
    "this is a chat-mode session that cannot execute code or access files, and that they can "
    "start a new session in dev mode (with a development environment) for that."
)
_SANDBOX = {
    "read_only": "read-only",
    "workspace_write": "workspace-write",
    "exec": "workspace-write",
}
_RATE = {"usageLimitExceeded", "rateLimitExceeded", "serverOverloaded", "sessionBudgetExceeded"}
_INVALID = {"modelNotFound", "badRequest", "contextWindowExceeded"}
_APPROVALS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "applyPatchApproval",
    "execCommandApproval",
}
_CLOSED = {"method": "$closed", "params": {}}
REMOTE_EXEC_ONLY = (
    "远程容器里的 Codex 只能用“可以执行命令”档：Codex 自带的沙箱在容器里起不来，"
    "容器本身就是沙箱（核验记录 V2）。请把这个开发环境的访问权限改成“可以执行命令”"
)
_REMOTE_AUTH_HINTS = ("401", "unauthorized", "not logged in", "login", "api key")


def _remote_error_kind(text: str) -> ErrorKind:
    lowered = text.lower()
    if any(hint in lowered for hint in _REMOTE_AUTH_HINTS):
        return "not_logged_in"
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limited"
    return "platform_error"


# 手动压缩：看到压缩条目之后再等这么久，没有回合结束的通知也算做完了
COMPACT_SETTLE_SECONDS = 5.0


def _error_kind(info: Any) -> ErrorKind:
    name = info if isinstance(info, str) else next(iter(info), "") if isinstance(info, dict) else ""
    if name == "unauthorized":
        return "not_logged_in"
    if name in _RATE:
        return "rate_limited"
    if name in _INVALID:
        return "invalid"
    return "platform_error"


def token_context(usage: Any) -> ContextUsage | None:
    """`thread/tokenUsage/updated` 的 tokenUsage → 上下文用了多少、窗口多大。

    0.169.0 的 ThreadTokenUsage：last（最近一次模型调用）、total（会话累计）、modelContextWindow。
    上下文里有多少取最近一次的 totalTokens 去掉推理用的输出（推理内容不留在上下文里）。"""
    if not isinstance(usage, dict):
        return None
    raw = usage.get("last")
    last: dict[str, Any] = raw if isinstance(raw, dict) else {}
    total = positive_int(last.get("totalTokens")) or 0
    reasoning = positive_int(last.get("reasoningOutputTokens")) or 0
    used = total - reasoning if total > reasoning else None
    window = positive_int(usage.get("modelContextWindow"))
    return ContextUsage(used, window) if used or window else None


class CodexDriver(Driver):
    can_query_quota = True  # account/rateLimits/read
    slot = "openai"
    login_methods = ("device_code", "api_key")

    def __init__(
        self,
        binary: str,
        home: Path,
        *,
        rpc_timeout: float = 30.0,
        secrets_dir: Path | None = None,
    ) -> None:
        self._binary = binary
        self._home = home
        self._timeout = rpc_timeout
        # 用 API 密钥登录时另存一份（权限 0600）：远程开发容器里的 codex exec 每回合经
        # CODEX_API_KEY 注入（方案第 9 节）。app-server 自己存的那份在 CODEX_HOME 里，管理器不读
        self._secret_file = secrets_dir / "openai.json" if secrets_dir is not None else None
        # 远程会话正在跑的回合（会话号 → 远程进程），取消时停掉它
        self._remote_procs: dict[str, remote.RemoteProcess] = {}
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self.latest_quota: list[dict[str, Any]] = []
        self._subs: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._loaded: set[str] = set()
        self._active: dict[str, str] = {}
        self._login: LoginChallenge | None = None
        self._login_id: str | None = None
        # 正在申请设备码（还没拿到 loginId）
        self._requesting = False
        # “登录完成”的通知可能紧跟着 account/login/start 的应答到达，早于 login() 拿到应答：
        # 那时还不知道是哪一次登录，先按 loginId 记下（成功与否、原因），login() 拿到应答后再认领
        self._early_logins: dict[str, tuple[bool, str]] = {}

    def supports(self, mode: Mode) -> bool:
        return mode in ("chat", "dev")

    # ---- 进程与 JSON-RPC ----
    async def start(self) -> None:
        await self._ensure()

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(proc.wait(), 5)
            if proc.returncode is None:
                proc.kill()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None

    def _program_env(self) -> dict[str, str]:
        """只给白名单里的环境变量，不带管理器的 BRIDGE_*（管理令牌）：Codex 跑的命令继承它的环境，
        它的 /proc/<pid>/environ 同一个用户也读得到（T-0293）。app-server 和查版本都用这一份。"""
        env = program_env(CODEX_HOME=str(self._home))
        if os.environ.get("HOME"):
            env["HOME"] = os.environ["HOME"]
        return env

    async def _ensure(self) -> None:
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            self._home.mkdir(parents=True, exist_ok=True)
            # 只给白名单里的环境变量，不带管理器的 BRIDGE_*（管理令牌）：Codex 跑的命令继承
            # app-server 的环境，它的 /proc/<pid>/environ 同一个用户也读得到（T-0293）
            env = self._program_env()
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    self._binary,
                    "app-server",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                    limit=16 * 1024 * 1024,
                )
            except OSError as error:
                raise DriverError("unavailable", f"启动 codex 失败：{error}") from error
            self._loaded.clear()
            self._active.clear()
            self._reader = asyncio.create_task(self._read_loop(self._proc), name="codex-reader")
            await self._call_raw(
                "initialize",
                {
                    "clientInfo": {
                        "name": "llm-bridge",
                        "title": "LLM Bridge",
                        "version": __version__,
                    }
                },
            )
            await self._send({"method": "initialized"})

    async def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise DriverError("unavailable", "codex app-server 没有在运行")
        proc.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
        await proc.stdin.drain()

    async def _call_raw(
        self, method: str, params: dict[str, Any], wait: float | None = None
    ) -> Any:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, wait or self._timeout)
        except TimeoutError as error:
            raise DriverError("timeout", f"codex 没有在时限内响应 {method}") from error
        finally:
            self._pending.pop(request_id, None)

    async def _call(self, method: str, params: dict[str, Any], wait: float | None = None) -> Any:
        await self._ensure()
        return await self._call_raw(method, params, wait)

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None  # noqa: S101
        try:
            while line := await proc.stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    await self._dispatch(message)
        finally:
            error = DriverError("unavailable", "codex app-server 退出了")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            for queue in self._subs.values():
                queue.put_nowait(_CLOSED)
            login = self._login
            if login is not None and login.state == "pending" and not self._requesting:
                self._fail_login(
                    "wait_user",
                    "Codex 的本机程序（codex app-server）退出了，登录没有完成，请重新开始",
                )

    async def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            future = self._pending.get(message.get("id", -1))
            if future is None or future.done():
                return
            if "error" in message:
                detail = message["error"] if isinstance(message["error"], dict) else {}
                text = str(detail.get("message") or "codex 返回错误")
                kind: ErrorKind = (
                    "invalid" if detail.get("code") in (-32602, -32600) else "platform_error"
                )
                future.set_exception(DriverError(kind, text[:500]))
            else:
                future.set_result(message.get("result"))
            return
        if "id" in message:  # 服务端发来的请求：审批一律拒绝，其余不支持
            if method in _APPROVALS:
                reply: dict[str, Any] = {"id": message["id"], "result": {"decision": "decline"}}
            else:
                reply = {"id": message["id"], "error": {"code": -32601, "message": "not supported"}}
            with contextlib.suppress(DriverError):
                await self._send(reply)
            return
        raw = message.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if method == "account/login/completed":
            ok = bool(params.get("success"))
            reason = "" if ok else str(params.get("error") or "")
            raw_id = params.get("loginId")
            key = raw_id if isinstance(raw_id, str) else ""
            waiting = (
                self._login is not None and self._login.state == "pending" and not self._requesting
            )
            if waiting:
                # 只认当前这一次登录的结果（没带编号的也算）；更早那次没做完的登录的结果不要
                if key in ("", self._login_id or ""):
                    self._complete_login(ok, reason)
            else:
                self._early_logins[key] = (ok, reason)
            return
        if method == "account/rateLimits/updated":
            # 账号级的通知，不属于哪个会话：记下最新的，回合结束或有人查的时候取
            self.latest_quota = quota.from_codex(params) or self.latest_quota
            return
        thread = params.get("thread")
        thread_id = params.get("threadId") or (
            thread.get("id") if isinstance(thread, dict) else None
        )
        queue = self._subs.get(thread_id) if thread_id else None
        if queue is not None:
            queue.put_nowait(message)

    # ---- 账号 ----
    async def status(self) -> AccountStatus:
        result = await self._call("account/read", {"refreshToken": False})
        account = result.get("account") if isinstance(result, dict) else None
        if not isinstance(account, dict):
            return AccountStatus(logged_in=False)
        kind = account.get("type")
        if kind == "chatgpt":
            plan = account.get("planType")
            return AccountStatus(
                True, account.get("email"), "chatgpt", f"plan={plan}" if plan else ""
            )
        return AccountStatus(True, None, "api_key" if kind == "apiKey" else str(kind))

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        if method == "api_key":
            key = payload.get("api_key", "").strip()
            if not key:
                raise DriverError("invalid", "缺少 api_key")
            await self._call("account/login/start", {"type": "apiKey", "apiKey": key})
            self._save_key(key)
            self._login_id = None
            self._login = LoginChallenge(state="done")
            return self._login
        if method == "device_code":
            self._save_key(None)
            # 上一次登录的结果作废；从这里起到拿到应答之前到达的完成通知都先记下
            self._login_id = None
            self._early_logins.clear()
            self._login = LoginChallenge(
                state="pending",
                steps=progress.new_steps(DEVICE_STEPS),
                log=[],
                started_at=progress.iso(progress.now()),
            )
            self._requesting = True
            try:
                return await self._device_login()
            finally:
                self._requesting = False
        raise DriverError("not_supported", f"Codex 不支持登录方式 {method}")

    async def _device_login(self) -> LoginChallenge:
        """设备码登录的前两步（启动本机程序、申请设备码），拿到网址和验证码后开始等人去登录。"""
        login = self._login
        assert login is not None  # noqa: S101
        steps, log = login.steps, login.log
        loop = asyncio.get_running_loop()
        began = loop.time()
        running = self._proc is not None and self._proc.returncode is None
        progress.mark(steps, "start", "running")
        if not running:
            progress.note(log, "启动 Codex 的本机程序（codex app-server）")
        try:
            await self._ensure()
        except DriverError as error:
            self._fail_login("start", error.message)
            raise
        if running:
            progress.mark(steps, "start", "done", "已在运行")
            progress.note(log, "Codex 的本机程序（codex app-server）已在运行")
        else:
            progress.mark(steps, "start", "done", f"已启动（用时 {loop.time() - began:.1f} 秒）")
            progress.note(log, "Codex 的本机程序已就绪")
        asked = loop.time()
        progress.mark(steps, "request_code", "running")
        progress.note(
            log,
            "向 OpenAI 申请设备码（account/login/start，"
            f"最多等 {REQUEST_CODE_SECONDS:.0f} 秒；要访问 auth.openai.com）",
        )
        try:
            result = await self._call(
                "account/login/start", {"type": "chatgptDeviceCode"}, wait=REQUEST_CODE_SECONDS
            )
        except DriverError as error:
            progress.note(log, f"申请设备码出错（{loop.time() - asked:.1f} 秒）：{error.message}")
            if error.kind not in ("platform_error", "timeout", "unavailable"):
                self._fail_login("request_code", error.message)
                raise
            # 申请设备码要访问 OpenAI：把原因和常见的两种情况写给人看
            message = (
                f"向 OpenAI 申请设备码没有成功：{error.message}。"
                "常见原因：这个 MCP 管理器所在的服务器访问不了 OpenAI"
                "（auth.openai.com、chatgpt.com）；或者这个 ChatGPT 账号（工作区）"
                "没有打开设备码登录。也可以改用“API 密钥”登录"
            )
            self._fail_login("request_code", message)
            raise DriverError(error.kind, message) from error
        answer = result if isinstance(result, dict) else {}
        raw_id = answer.get("loginId")
        self._login_id = raw_id if isinstance(raw_id, str) else None
        url, code = answer.get("verificationUrl"), answer.get("userCode")
        progress.mark(steps, "request_code", "done", f"用时 {loop.time() - asked:.1f} 秒")
        progress.note(log, f"收到设备码和登录网址：{url}" if url else "收到设备码")
        progress.mark(
            steps, "wait_user", "running", "Codex 会自己向 OpenAI 查登录结果，完成后自动进入下一步"
        )
        progress.note(log, "等你在浏览器里输入验证码并登录")
        self._login = replace(
            login,
            verification_url=url if isinstance(url, str) else None,
            user_code=code if isinstance(code, str) else None,
        )
        # 完成通知已经先到了：直接是最终结果，不再让页面去等
        key = self._login_id or ""
        early = self._early_logins.pop(key, None) or self._early_logins.pop("", None)
        self._early_logins.clear()
        if early is not None:
            self._complete_login(*early)
        return self._login

    def _complete_login(self, ok: bool, reason: str) -> None:
        """收到“登录完成”的通知：记下结果，最后两步结束。"""
        login = self._login
        if login is None:
            return
        steps, log = login.steps, login.log
        finished = progress.iso(progress.now())
        if ok:
            progress.note(log, "收到 account/login/completed：登录成功")
            progress.mark(steps, "wait_user", "done", "已在浏览器里完成登录")
            progress.mark(steps, "finish", "done", "OpenAI 确认登录成功")
            self._login = replace(login, state="done", detail="", finished_at=finished)
            return
        progress.note(log, f"收到 account/login/completed：没有成功（{reason or '没有给出原因'}）")
        self._fail_login("wait_user", f"登录没有完成：{reason}" if reason else "登录没有完成")

    def _fail_login(self, key: str, message: str) -> None:
        """这一步失败：记下原因，后面没轮到的步骤保持“等待”（页面显示为未执行）。"""
        login = self._login
        if login is None:
            return
        progress.mark(login.steps, key, "failed", message)
        progress.note(login.log, f"失败：{message}")
        self._login = replace(
            login, state="failed", detail=message, finished_at=progress.iso(progress.now())
        )

    async def login_state(self) -> LoginChallenge | None:
        return self._login

    async def logout(self) -> None:
        await self._call("account/logout", {})
        self._save_key(None)
        self._login = None
        self._login_id = None

    # ---- 远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 6.1、9 节）----
    def _save_key(self, key: str | None) -> None:
        if self._secret_file is None:
            return
        if key is None:
            self._secret_file.unlink(missing_ok=True)
            return
        self._secret_file.parent.mkdir(parents=True, exist_ok=True)
        self._secret_file.parent.chmod(0o700)
        self._secret_file.write_text(json.dumps({"method": "api_key", "value": key}))
        self._secret_file.chmod(0o600)

    def remote_credentials(self) -> dict[str, str]:
        """API 密钥随回合注入（codex exec 认 CODEX_API_KEY，0.169.0 的 exec/src/lib.rs）。
        ChatGPT 登录会自己刷新，不下发：要在远程容器里单独登录。"""
        if self._secret_file is None:
            return {}
        try:
            data = json.loads(self._secret_file.read_text())
        except (OSError, ValueError):
            return {}
        value = data.get("value") if isinstance(data, dict) else None
        return {"CODEX_API_KEY": value} if isinstance(value, str) and value else {}

    async def version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._program_env(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
        except (OSError, TimeoutError):
            return None
        return remote.parse_version(out.decode(errors="replace"))

    def _remote_argv(self, spec: SessionSpec, images: list[str]) -> list[str]:
        """远程回合的 codex exec 参数（0.169.0 的 codex-rs/exec/src/cli.rs）。

        --json、-m、-c、--dangerously-bypass-approvals-and-sandbox、--skip-git-repo-check
        是全局参数，放在 resume 子命令前后都可以；-C 不是，工作目录用进程的 cwd。
        容器本身是沙箱（V2：Codex 自带的沙箱在容器里起不来），所以只有“可以执行命令”这一档。"""
        argv = [
            remote.PROGRAMS[self.slot],
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if spec.model:
            argv += ["-m", spec.model]
        if spec.effort:
            argv += ["-c", f"model_reasoning_effort={json.dumps(spec.effort)}"]
        if spec.autocompact:
            argv += ["-c", f"model_auto_compact_token_limit={int(spec.autocompact)}"]
        if spec.pinned:
            # 固定带着的参考资料：开发者说明（config.toml 的 developer_instructions，每回合带上）。
            # json.dumps 默认只用 TOML 基本字符串也认的转义
            argv += ["-c", f"developer_instructions={json.dumps(spec.pinned)}"]
        if spec.native_id:
            argv += ["resume", spec.native_id]
        for path in images:
            argv += ["-i", path]
        argv.append("-")  # 提问从标准输入读
        return argv

    async def _remote_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
        target, hub = spec.remote, self.remote
        if target is None or hub is None:
            raise DriverError("unavailable", "这个管理器没有接上远程开发容器")
        if spec.sandbox != "exec":
            raise DriverError("invalid", REMOTE_EXEC_ONLY)
        images: list[str] = []
        if spec.images:
            uploads = []
            for index, (path, _) in enumerate(spec.images):
                name = f"{remote.IMAGE_DIR}/{index}-{Path(path).name}"
                uploads.append((name, await asyncio.to_thread(Path(path).read_bytes)))
                images.append(f"{target.workdir}/{name}")
            await hub.put_files(target.container, target.session_id, uploads)
        proc = await hub.spawn(
            target.container,
            purpose="turn",
            platform=remote.PLATFORM_OF_SLOT[self.slot],
            session_id=target.session_id,
            argv=self._remote_argv(spec, images),
            cwd=target.workdir,
            env=remote.remote_env(self.slot),
            secret_env=self.remote_credentials(),
            stdin=text.encode(),
            timeout_seconds=target.timeout_seconds,
        )
        self._remote_procs[target.session_id] = proc
        try:
            return await self._remote_collect(spec, proc, sink)
        finally:
            self._remote_procs.pop(target.session_id, None)
            if proc.returncode is None:
                proc.kill()

    async def _remote_collect(
        self, spec: SessionSpec, proc: remote.RemoteProcess, sink: EventSink
    ) -> TurnOutcome:
        """读 codex exec --json 的事件（0.169.0 的 codex-rs/exec/src/exec_events.rs）。"""
        outcome = TurnOutcome(text="")
        messages: list[str] = []
        seen_output: dict[str, int] = {}  # 命令的输出是累计的：记下已经报过多少
        failure: str | None = None
        completed = False
        last_error = ""
        while line := await proc.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            raw_item = event.get("item")
            item: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
            step = str(item.get("id") or "")
            if kind == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and thread_id and thread_id != spec.native_id:
                    # 第一回合才知道原生会话号：交给 engine 记到会话上，之后的回合用 resume
                    await sink("native_session", {"native_id": thread_id})
            elif kind == "item.started":
                if item.get("type") == "command_execution" and item.get("command"):
                    await sink(
                        "command_started", {"command": str(item["command"])[:500], "step": step}
                    )
                elif item.get("type") in ("mcp_tool_call", "web_search"):
                    name = f"{item.get('server') or ''}.{item.get('tool') or ''}".strip(".")
                    await sink("tool_started", {"name": name or "web_search", "step": step})
            elif kind == "item.updated":
                if item.get("type") == "command_execution":
                    output = str(item.get("aggregated_output") or "")
                    fresh = output[seen_output.get(step, 0) :]
                    seen_output[step] = len(output)
                    if fresh:
                        await sink("output", {"text": fresh})
            elif kind == "item.completed":
                if (
                    item.get("type") == "command_execution"
                ):  # 最后一段输出（没经 item.updated 报过的）
                    output = str(item.get("aggregated_output") or "")
                    fresh = output[seen_output.pop(step, 0) :]
                    if fresh:
                        await sink("output", {"text": fresh})
                await self._remote_item(item, step, messages, outcome, sink)
            elif kind == "turn.completed":
                usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                used_in = int((usage or {}).get("input_tokens") or 0)
                used_out = int((usage or {}).get("output_tokens") or 0)
                outcome.usage = {
                    "input_tokens": used_in,
                    "output_tokens": used_out,
                    "total_tokens": used_in + used_out,
                    "cached_input_tokens": int((usage or {}).get("cached_input_tokens") or 0),
                }
                completed = True
            elif kind == "turn.failed":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                failure = str((error or {}).get("message") or "Codex 回合失败")
            elif kind == "error":
                last_error = str(event.get("message") or "")
                if last_error:  # 例如“正在重连”：让调用方在 llm_turn_get 里看得到卡在哪
                    await sink("notice", {"text": last_error[:200]})
        code = await proc.wait()
        if failure is not None or not completed:
            text = failure or last_error or proc.stderr_tail.strip()[-300:] or f"退出码 {code}"
            text = redact(text)[:500]
            if quota.is_quota_text(text):
                raise DriverError(
                    "rate_limited", text, quota=True, resets_at=quota.parse_reset(text)
                )
            raise DriverError(_remote_error_kind(text), f"Codex 回合失败：{text}")
        outcome.text = messages[-1] if messages else ""
        return outcome

    @staticmethod
    async def _remote_item(
        item: dict[str, Any],
        step: str,
        messages: list[str],
        outcome: TurnOutcome,
        sink: EventSink,
    ) -> None:
        kind = item.get("type")
        if kind in ("command_execution", "mcp_tool_call", "web_search") and step:
            await sink("step_done", {"step": step})
        if kind == "agent_message" and item.get("text"):
            messages.append(str(item["text"]))
            await sink("message", {"text": messages[-1]})
        elif kind == "command_execution":
            outcome.commands.append(str(item.get("command", ""))[:500])
            await sink(
                "command", {"command": outcome.commands[-1], "exit_code": item.get("exit_code")}
            )
        elif kind == "file_change":
            paths = [
                str(c.get("path"))
                for c in item.get("changes") or []
                if isinstance(c, dict) and c.get("path")
            ]
            outcome.files.extend(paths)
            if paths:
                await sink("files", {"paths": paths})
        elif kind == "mcp_tool_call":
            name = f"{item.get('server') or ''}.{item.get('tool') or ''}".strip(".")
            await sink(
                "tool",
                {"name": name, "input": json.dumps(item.get("arguments"), ensure_ascii=False)},
            )
            if item.get("result") is not None or item.get("error") is not None:
                body = item.get("error") or item.get("result")
                await sink(
                    "tool_result",
                    {
                        "name": name,
                        "text": json.dumps(body, ensure_ascii=False),
                        "error": item.get("error") is not None,
                    },
                )
        elif kind == "web_search" and item.get("query"):
            await sink("tool", {"name": "web_search", "input": str(item["query"])})
        elif kind == "error" and item.get("message"):
            await sink("notice", {"text": str(item["message"])[:200]})

    async def models(self, mode: Mode) -> list[ModelInfo]:
        result = await self._call("model/list", {})
        out = []
        for item in result.get("data", []):
            if item.get("hidden"):
                continue
            efforts = tuple(
                e["reasoningEffort"]
                for e in item.get("supportedReasoningEfforts") or []
                if "reasoningEffort" in e
            )
            out.append(
                ModelInfo(
                    id=item["id"],
                    name=item.get("displayName") or item["id"],
                    description=item.get("description") or "",
                    default=bool(item.get("isDefault")),
                    efforts=efforts,
                )
            )
        return out

    # ---- 会话 ----
    async def create(self, spec: SessionSpec) -> str:
        if spec.remote is not None:
            # 远程会话：原生会话号要等第一回合的 thread.started 才知道
            if spec.sandbox != "exec":
                raise DriverError("invalid", REMOTE_EXEC_ONLY)
            return ""
        params: dict[str, Any] = {
            "cwd": str(spec.workdir),
            "approvalPolicy": "never",
            "sandbox": _SANDBOX[spec.sandbox],
        }
        if spec.model:
            params["model"] = spec.model
        if spec.mode == "chat":
            # 固定带着的参考资料（6.3）放在开发者说明里：随线程保存，压缩不会把它压掉
            pinned = f"\n\n{spec.pinned}" if spec.pinned else ""
            params["developerInstructions"] = CHAT_INSTRUCTIONS + pinned
        elif spec.jobs:  # 能开后台作业的开发会话：告诉模型用 llm-task（LONGRUN 5.5）
            params["developerInstructions"] = JOBS_INSTRUCTIONS
        if self._config(spec):
            params["config"] = self._config(spec)
        result = await self._call("thread/start", params)
        thread_id = str(result["thread"]["id"])
        self._loaded.add(thread_id)
        return thread_id

    def _sandbox_policy(self, spec: SessionSpec) -> dict[str, Any]:
        if spec.sandbox == "read_only":
            return {"type": "readOnly", "networkAccess": spec.network}
        return {"type": "workspaceWrite", "networkAccess": spec.network}

    async def run_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
        if spec.remote is not None:
            return await self._remote_turn(spec, text, sink)
        thread_id = spec.native_id
        if not thread_id:
            raise DriverError("invalid", "会话没有 Codex 线程号")
        await self._ensure()
        await self._load(spec)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs[thread_id] = queue
        try:
            params: dict[str, Any] = {
                "threadId": thread_id,
                # 图片按本机路径给（UserInput 的 localImage，app-server v2 schema）
                "input": [
                    {"type": "text", "text": text},
                    *({"type": "localImage", "path": path} for path, _ in spec.images),
                ],
                "approvalPolicy": "never",
                "sandboxPolicy": self._sandbox_policy(spec),
            }
            if spec.model:
                params["model"] = spec.model
            if spec.effort:
                params["effort"] = spec.effort
            started = await self._call("turn/start", params)
            turn_id = str(started["turn"]["id"])
            self._active[thread_id] = turn_id
            return await self._collect(queue, turn_id, sink)
        finally:
            self._subs.pop(thread_id, None)
            self._active.pop(thread_id, None)

    @staticmethod
    def _config(spec: SessionSpec) -> dict[str, Any]:
        """线程的配置项（thread/start、thread/resume 的 config，0.169.0 的 ThreadStartParams）：
        自动压缩的触发点 model_auto_compact_token_limit（token 数，管理员在设置里填了才有）。"""
        if spec.autocompact:
            return {"model_auto_compact_token_limit": int(spec.autocompact)}
        return {}

    async def _load(self, spec: SessionSpec) -> None:
        thread_id = spec.native_id or ""
        if thread_id in self._loaded:
            return
        params: dict[str, Any] = {"threadId": thread_id}
        if self._config(spec):
            params["config"] = self._config(spec)
        await self._call("thread/resume", params)
        self._loaded.add(thread_id)

    async def compact(self, spec: SessionSpec, instructions: str, sink: EventSink) -> TurnOutcome:
        """手动压缩：`thread/compact/start {threadId}`（0.169.0，不能带说明），应答是空的；
        压缩在后台做，做完报一个 contextCompaction 条目、回合结束。等到回合结束（或者压缩条目出现后
        短时间内没有别的动静）就返回。"""
        if spec.remote is not None:
            raise DriverError("not_supported", "远程容器里的 Codex 不能手动压缩上下文")
        thread_id = spec.native_id
        if not thread_id:
            raise DriverError("invalid", "会话没有 Codex 线程号")
        await self._ensure()
        await self._load(spec)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs[thread_id] = queue
        outcome = TurnOutcome(text="")
        try:
            await self._call("thread/compact/start", {"threadId": thread_id})
            while True:
                try:
                    wait = COMPACT_SETTLE_SECONDS if outcome.compactions else None
                    message = await asyncio.wait_for(queue.get(), wait)
                except TimeoutError:
                    return outcome
                method, params = message["method"], message.get("params") or {}
                if method == "$closed":
                    raise DriverError("unavailable", "codex app-server 在压缩时退出了")
                item = params.get("item") or {}
                if method == "item/completed" and item.get("type") == "contextCompaction":
                    outcome.compactions += 1
                    await sink("compacted", {"trigger": "manual"})
                elif method == "thread/tokenUsage/updated":
                    outcome.context = token_context(params.get("tokenUsage"))
                elif method == "turn/completed":
                    turn = params.get("turn") or {}
                    if turn.get("status") == "failed":
                        error = turn.get("error") or {}
                        text = str(error.get("message") or "Codex 压缩上下文失败")[:500]
                        raise DriverError(_error_kind(error.get("codexErrorInfo")), text)
                    return outcome
        finally:
            self._subs.pop(thread_id, None)

    async def _collect(
        self, queue: asyncio.Queue[dict[str, Any]], turn_id: str, sink: EventSink
    ) -> TurnOutcome:
        outcome = TurnOutcome(text="", native_turn_id=turn_id)
        messages: list[str] = []
        last_error = ""
        while True:
            message = await queue.get()
            method, params = message["method"], message.get("params") or {}
            if method == "$closed":
                raise DriverError("unavailable", "codex app-server 在回合进行中退出了")
            if params.get("turnId") not in (None, turn_id) and method != "turn/completed":
                continue
            if method == "item/agentMessage/delta":
                # 逐字的增量（0.169.0 生成的接口定义里有：threadId / turnId / itemId / delta）
                if isinstance(params.get("delta"), str) and params["delta"]:
                    await sink("delta", {"text": params["delta"]})
                continue
            if method == "item/started":
                # 一步开始了（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.7）：只用于“正在做什么”，
                # 不写进记录；跑完时 item/completed 照旧记一步
                # （0.169.0 生成的接口定义：ItemStartedNotification）
                item = params.get("item") or {}
                step = str(item.get("id") or "")
                if item.get("type") == "commandExecution" and item.get("command"):
                    await sink(
                        "command_started", {"command": str(item["command"])[:500], "step": step}
                    )
                elif item.get("type") in ("mcpToolCall", "webSearch"):
                    name = f"{item.get('server') or ''}.{item.get('tool') or ''}".strip(".")
                    await sink("tool_started", {"name": name or "web_search", "step": step})
                continue
            if method == "item/commandExecution/outputDelta":
                # 命令的输出一段一段地来：只留最后几十行给回合进度看，不写进记录
                if isinstance(params.get("delta"), str) and params["delta"]:
                    await sink("output", {"text": params["delta"]})
                continue
            if method == "item/completed":
                item = params.get("item") or {}
                kind = item.get("type")
                if kind in ("commandExecution", "mcpToolCall", "webSearch") and item.get("id"):
                    await sink("step_done", {"step": str(item["id"])})
                if kind == "agentMessage" and item.get("text"):
                    messages.append(item["text"])
                    await sink("message", {"text": item["text"]})
                elif kind == "commandExecution":
                    outcome.commands.append(str(item.get("command", ""))[:500])
                    await sink(
                        "command",
                        {"command": outcome.commands[-1], "exit_code": item.get("exitCode")},
                    )
                elif kind == "fileChange":
                    paths = [str(c.get("path")) for c in item.get("changes") or [] if c.get("path")]
                    outcome.files.extend(paths)
                    await sink("files", {"paths": paths})
                elif kind == "mcpToolCall":
                    name = f"{item.get('server') or ''}.{item.get('tool') or ''}".strip(".")
                    await sink(
                        "tool",
                        {
                            "name": name,
                            "input": json.dumps(item.get("arguments"), ensure_ascii=False),
                        },
                    )
                    if item.get("result") is not None or item.get("error") is not None:
                        body = item.get("error") or item.get("result")
                        await sink(
                            "tool_result",
                            {
                                "name": name,
                                "text": json.dumps(body, ensure_ascii=False),
                                "error": item.get("error") is not None,
                            },
                        )
                elif kind == "webSearch" and item.get("query"):
                    await sink("tool", {"name": "web_search", "input": str(item["query"])})
                elif kind == "contextCompaction":
                    # 压缩了一次（0.169.0 的 ContextCompactionThreadItem 只有 id，
                    # 没有前后的 token 数）
                    outcome.compactions += 1
                    await sink("compacted", {"trigger": "auto"})
                elif kind == "imageGeneration" and isinstance(item.get("savedPath"), str):
                    # 模型画的图：官方程序已经存成文件，交给 engine 收进附件
                    await sink("image", {"path": item["savedPath"]})
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage") or {}
                last = usage.get("last") or {}
                outcome.usage = {
                    "input_tokens": int(last.get("inputTokens") or 0),
                    "output_tokens": int(last.get("outputTokens") or 0),
                    "total_tokens": int(last.get("totalTokens") or 0),
                }
                outcome.context = token_context(usage)
            elif method == "error":
                detail = params.get("error") or {}
                last_error = str(detail.get("additionalDetails") or detail.get("message") or "")
                # 例如“正在重连”：让调用方在 llm_turn_get 里看得到卡在哪
                await sink("notice", {"text": str(detail.get("message") or last_error)[:200]})
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("id") != turn_id:
                    continue
                status = turn.get("status")
                if status == "completed":
                    outcome.text = messages[-1] if messages else ""
                    if self.latest_quota:
                        await sink("quota", {"windows": self.latest_quota})
                    return outcome
                error = turn.get("error") or {}
                text = str(error.get("message") or last_error or "Codex 回合失败")[:500]
                if status == "interrupted":
                    raise DriverError("platform_error", "回合被中断")
                info = error.get("codexErrorInfo")
                name = info if isinstance(info, str) else next(iter(info), "") if info else ""
                if name == "usageLimitExceeded":
                    # 额度用完：带上重置时间（先实查，查不到用最近一次通知里的）
                    windows = await self._quota_quietly()
                    if windows:
                        await sink("quota", {"windows": windows})
                    resets = [
                        w["resets_at"]
                        for w in windows
                        if w["status"] == "exceeded" and w["resets_at"]
                    ]
                    raise DriverError(
                        "rate_limited",
                        text,
                        quota=True,
                        resets_at=min(resets) if resets else quota.parse_reset(text),
                    )
                raise DriverError(_error_kind(info), text)

    async def quota(self) -> list[dict[str, Any]] | None:
        result = await self._call("account/rateLimits/read", {})
        self.latest_quota = quota.from_codex(result)
        return self.latest_quota

    async def _quota_quietly(self) -> list[dict[str, Any]]:
        try:
            return await self.quota() or []
        except DriverError:
            return self.latest_quota

    async def steer(self, spec: SessionSpec, text: str) -> bool:
        """`turn/steer`（app-server v2）：要给出正在跑的回合号，应答里的回合号对得上才算收下。"""
        if spec.remote is not None:  # codex exec 不能插话：降级成中断后合并重发
            return False
        thread_id = spec.native_id
        turn_id = self._active.get(thread_id) if thread_id else None
        if not thread_id or not turn_id:
            return False
        try:
            answer = await self._call(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": turn_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
        except DriverError:
            return False
        return isinstance(answer, dict) and str(answer.get("turnId")) == turn_id

    async def cancel(self, spec: SessionSpec) -> None:
        if spec.remote is not None:
            proc = self._remote_procs.get(spec.remote.session_id)
            if proc is not None:
                proc.terminate()
            return
        thread_id = spec.native_id
        turn_id = self._active.get(thread_id) if thread_id else None
        if thread_id and turn_id:
            with contextlib.suppress(DriverError):
                await self._call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def list_native(self, limit: int) -> list[NativeSession]:
        result = await self._call("thread/list", {"limit": limit})
        return [
            NativeSession(
                native_id=str(item["id"]),
                title=str(item.get("name") or item.get("preview") or "")[:120],
                model=item.get("model"),
                workdir=item.get("cwd"),
                created_at=item.get("createdAt"),
                updated_at=item.get("updatedAt"),
            )
            for item in result.get("data", [])
        ]

    async def read(self, native_id: str, workdir: str | None) -> list[Message]:
        out: list[Message] = []
        cursor: str | None = None
        for _ in range(50):  # 每页 100 个回合，最多读 5000 个
            params: dict[str, Any] = {
                "threadId": native_id,
                "itemsView": "full",
                "sortDirection": "asc",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                result = await self._call("thread/turns/list", params)
            except DriverError as error:
                # 还没有发过消息的线程没有历史，官方程序会报这两种错（0.169.0 实测）：当作空会话
                if "not supported yet" in error.message or "not materialized" in error.message:
                    return out
                raise
            for turn in result.get("data") or []:
                at = turn.get("startedAt")
                for item in turn.get("items") or []:
                    if item.get("type") == "userMessage":
                        text = "".join(
                            c.get("text", "")
                            for c in item.get("content") or []
                            if c.get("type") == "text"
                        )
                        out.append(Message("user", text, at))
                    elif item.get("type") == "agentMessage" and item.get("text"):
                        out.append(Message("assistant", item["text"], at))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return out

    async def archive(self, native_id: str) -> None:
        await self._call("thread/archive", {"threadId": native_id})
        self._loaded.discard(native_id)
