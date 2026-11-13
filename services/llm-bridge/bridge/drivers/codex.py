"""Codex 驱动：常驻一个 `codex app-server`，用 JSON-RPC（每行一条）对话。

接口在 codex-cli 0.169.0 上核验（生成的 schema 加实测），见
docs/decisions/2026-11-13-llm-bridge-verification.md 第 2 节。

- 所有会话共用这一个进程和一个 CODEX_HOME（一个账号）；
- 审批策略固定为 never，沙箱由环境决定；万一收到审批请求一律拒绝；
- `chatgpt.chat` 也走这里：同一个 ChatGPT 登录，只读沙箱、空目录，当作纯聊天。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from bridge import __version__
from bridge.drivers.base import (
    AccountStatus,
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
)

logger = logging.getLogger("bridge.codex")

CHAT_INSTRUCTIONS = (
    "This session is a plain chat. Answer the user's message directly. "
    "Do not read or modify files and do not run commands."
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


def _error_kind(info: Any) -> ErrorKind:
    name = info if isinstance(info, str) else next(iter(info), "") if isinstance(info, dict) else ""
    if name == "unauthorized":
        return "not_logged_in"
    if name in _RATE:
        return "rate_limited"
    if name in _INVALID:
        return "invalid"
    return "platform_error"


class CodexDriver(Driver):
    slot = "openai"
    login_methods = ("device_code", "api_key")

    def __init__(self, binary: str, home: Path, *, rpc_timeout: float = 30.0) -> None:
        self._binary = binary
        self._home = home
        self._timeout = rpc_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._subs: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._loaded: set[str] = set()
        self._active: dict[str, str] = {}
        self._login: LoginChallenge | None = None
        self._login_id: str | None = None
        # “登录完成”的通知可能紧跟着 account/login/start 的应答到达，早于 login() 拿到应答：
        # 那时还不知道是哪一次登录，先按 loginId 记下，login() 拿到应答后再认领
        self._early_logins: dict[str, LoginChallenge] = {}

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

    async def _ensure(self) -> None:
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            self._home.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "CODEX_HOME": str(self._home)}
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
            finished = LoginChallenge(
                state="done" if ok else "failed",
                detail="" if ok else str(params.get("error") or ""),
            )
            raw_id = params.get("loginId")
            key = raw_id if isinstance(raw_id, str) else ""
            waiting = self._login is not None and self._login.state == "pending"
            if waiting:
                # 只认当前这一次登录的结果（没带编号的也算）；更早那次没做完的登录的结果不要
                if key in ("", self._login_id or ""):
                    self._login = finished
            else:
                self._early_logins[key] = finished
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
            self._login_id = None
            self._login = LoginChallenge(state="done")
            return self._login
        if method == "device_code":
            # 上一次登录的结果作废；从这里起到拿到应答之前到达的完成通知都先记下
            self._login, self._login_id = None, None
            self._early_logins.clear()
            result = await self._call("account/login/start", {"type": "chatgptDeviceCode"}, wait=60)
            raw_id = result.get("loginId")
            self._login_id = raw_id if isinstance(raw_id, str) else None
            # 完成通知已经先到了：直接是最终结果，不再让页面去等
            early = self._early_logins.pop(self._login_id or "", None) or self._early_logins.pop(
                "", None
            )
            self._early_logins.clear()
            self._login = early or LoginChallenge(
                state="pending",
                verification_url=result.get("verificationUrl"),
                user_code=result.get("userCode"),
            )
            return self._login
        raise DriverError("not_supported", f"Codex 不支持登录方式 {method}")

    async def login_state(self) -> LoginChallenge | None:
        return self._login

    async def logout(self) -> None:
        await self._call("account/logout", {})
        self._login = None
        self._login_id = None

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
        params: dict[str, Any] = {
            "cwd": str(spec.workdir),
            "approvalPolicy": "never",
            "sandbox": _SANDBOX[spec.sandbox],
        }
        if spec.model:
            params["model"] = spec.model
        if spec.mode == "chat":
            params["developerInstructions"] = CHAT_INSTRUCTIONS
        result = await self._call("thread/start", params)
        thread_id = str(result["thread"]["id"])
        self._loaded.add(thread_id)
        return thread_id

    def _sandbox_policy(self, spec: SessionSpec) -> dict[str, Any]:
        if spec.sandbox == "read_only":
            return {"type": "readOnly", "networkAccess": spec.network}
        return {"type": "workspaceWrite", "networkAccess": spec.network}

    async def run_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
        thread_id = spec.native_id
        if not thread_id:
            raise DriverError("invalid", "会话没有 Codex 线程号")
        await self._ensure()
        if thread_id not in self._loaded:
            await self._call("thread/resume", {"threadId": thread_id})
            self._loaded.add(thread_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs[thread_id] = queue
        try:
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
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
            if method == "item/completed":
                item = params.get("item") or {}
                kind = item.get("type")
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
            elif method == "thread/tokenUsage/updated":
                last = (params.get("tokenUsage") or {}).get("last") or {}
                outcome.usage = {
                    "input_tokens": int(last.get("inputTokens") or 0),
                    "output_tokens": int(last.get("outputTokens") or 0),
                    "total_tokens": int(last.get("totalTokens") or 0),
                }
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
                    return outcome
                error = turn.get("error") or {}
                text = str(error.get("message") or last_error or "Codex 回合失败")[:500]
                if status == "interrupted":
                    raise DriverError("platform_error", "回合被中断")
                raise DriverError(_error_kind(error.get("codexErrorInfo")), text)

    async def cancel(self, spec: SessionSpec) -> None:
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
