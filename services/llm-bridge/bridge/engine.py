"""会话与回合（docs/v2/LLM_BRIDGE_PLAN.md 第 5、7、8 节）。

- 会话归新建它的调用方；默认只能看到、续发自己的会话。权限范围（scopes）：
  write（新建、发送、取消、归档）、account（列出账号下所有会话的标题等）、takeover（读取、续发不属于自己的会话）；
- 一个会话一条串行队列；不同会话并行，每个账号槽位有并发上限；
- 发送带幂等号：同一个调用方用同一个 request_id 重发，返回原来的回合；
- 一个槽位只认第一次登录的那个账号：发现换了账号就拒绝调用，直到管理员明确更换；
- 进程重启后：运行中的回合记为 unknown（不知道平台做完没有），排队中的继续排。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.channels import SLOT_LABELS, Channel, find
from bridge.config import Settings
from bridge.drivers.base import Driver, DriverError, Mode, SessionSpec
from bridge.redact import mask_identity, redact
from bridge.store import Row, Store

logger = logging.getLogger("bridge.engine")

TERMINAL = ("completed", "failed", "cancelled", "unknown")
DEFAULTS: dict[str, Any] = {
    "concurrency": 2,  # 每个账号槽位同时进行的回合数
    "turn_timeout_seconds": 1800,
    "send_per_hour": 120,  # 每个调用方每小时的发送次数
    "max_text_chars": 100_000,
    # Claude 用订阅登录（oauth_token / existing）前，管理员要先确认了解合规风险（方案 6.3、D3）
    "claude_subscription_ack": False,
}
SETTING_LIMITS = {
    "concurrency": (1, 16),
    "turn_timeout_seconds": (30, 6 * 3600),
    "send_per_hour": (1, 10_000),
    "max_text_chars": (1, 1_000_000),
}
MAX_WAIT_SECONDS = 50
ACCOUNT_CACHE_SECONDS = 60
_MODEL_RE = re.compile(r"^[\w.\-:\[\]/]{1,100}$")
_REQUEST_ID_RE = re.compile(r"^[\w.\-:]{1,100}$")
_ERROR_CODES = {
    "not_logged_in": "NOT_LOGGED_IN",
    "rate_limited": "RATE_LIMITED",
    "invalid": "VALIDATION_FAILED",
    "not_supported": "NOT_SUPPORTED",
    "platform_error": "UNAVAILABLE",
    "timeout": "UNAVAILABLE",
    "unavailable": "UNAVAILABLE",
}
EXTERNAL_PLATFORM = {
    "openai": "codex",
    "claude": "claude",
    "workbuddy": "workbuddy",
    "fake": "fake",
}


class BridgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def from_driver(error: DriverError) -> BridgeError:
    return BridgeError(_ERROR_CODES.get(error.kind, "UNAVAILABLE"), error.message)


@dataclass(frozen=True)
class Caller:
    id: str
    label: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        return scope in self.scopes


class SlotLimiter:
    """并发上限可以在运行中调整的信号量。"""

    def __init__(self) -> None:
        self._running = 0
        self._cond = asyncio.Condition()

    @property
    def running(self) -> int:
        return self._running

    async def acquire(self, limit: Any) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self._running < limit())
            self._running += 1

    async def release(self) -> None:
        async with self._cond:
            self._running -= 1
            self._cond.notify_all()


class Engine:
    def __init__(
        self,
        store: Store,
        drivers: dict[str, Driver],
        channels: tuple[Channel, ...],
        settings: Settings,
    ) -> None:
        self.store = store
        self.drivers = drivers
        self.channels = channels
        self.settings = settings
        self._queues: dict[str, asyncio.Queue[str]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._done: dict[str, asyncio.Event] = {}
        self._cancel_requested: set[str] = set()
        self._limiters = {slot: SlotLimiter() for slot in drivers}
        self._accounts: dict[str, tuple[float, Row]] = {}
        self._idle_seconds = 60.0

    # ---- 设置 ----
    def setting(self, key: str) -> Any:
        return self.store.get_setting(key, DEFAULTS[key])

    def settings_view(self) -> Row:
        return {key: self.setting(key) for key in DEFAULTS}

    def update_settings(self, values: Row) -> Row:
        for key, value in values.items():
            if key not in DEFAULTS:
                raise BridgeError("VALIDATION_FAILED", f"没有这个设置：{key}")
            if isinstance(DEFAULTS[key], bool):
                if not isinstance(value, bool):
                    raise BridgeError("VALIDATION_FAILED", f"{key} 必须是 true 或 false")
            else:
                low, high = SETTING_LIMITS[key]
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not low <= value <= high
                ):
                    raise BridgeError("VALIDATION_FAILED", f"{key} 必须是 {low} 到 {high} 的整数")
            self.store.set_setting(key, value)
        return self.settings_view()

    # ---- 启动与停止 ----
    async def startup(self) -> None:
        for turn in self.store.list_turns(statuses=("running",), limit=10_000):
            self.store.update_turn(
                turn["id"],
                status="unknown",
                finished_at=time.time(),
                error_kind="unknown",
                error_message="会话桥在回合进行中重启，不确定平台是否已经做完",
            )
        for turn in self.store.list_turns(statuses=("queued",), limit=10_000):
            self._enqueue(turn)

    async def shutdown(self) -> None:
        for task in list(self._workers.values()):
            task.cancel()
        for task in list(self._workers.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for driver in self.drivers.values():
            await driver.stop()

    # ---- 账号 ----
    def _driver(self, slot: str) -> Driver:
        driver = self.drivers.get(slot)
        if driver is None:
            raise BridgeError("NOT_SUPPORTED", "这座桥没有这个平台")
        return driver

    async def account(self, slot: str, *, refresh: bool = False) -> Row:
        cached = self._accounts.get(slot)
        if cached and not refresh and time.monotonic() - cached[0] < ACCOUNT_CACHE_SECONDS:
            return cached[1]
        driver = self._driver(slot)
        stored = self.store.get_account(slot) or self.store.upsert_account(slot)
        detail = ""
        try:
            status = await driver.status()
        except DriverError as error:
            view = {**stored, "state": "unavailable", "detail": error.message}
            self._accounts[slot] = (time.monotonic(), view)
            return view
        if not status.logged_in:
            stored = self.store.upsert_account(slot, state="logged_out")
            detail = status.detail
        else:
            identity = status.identity or f"method:{status.method}"
            digest = hashlib.sha256(identity.encode()).hexdigest()
            if stored.get("identity_hash") and stored["identity_hash"] != digest:
                stored = self.store.upsert_account(slot, state="mismatch")
                detail = (
                    f"登录目录里现在是另一个账号（{mask_identity(identity)}），"
                    f"这座桥登记的是 {stored.get('identity_label')}。要换账号请在管理页“更换账号”"
                )
            else:
                stored = self.store.upsert_account(
                    slot,
                    state="logged_in",
                    identity_hash=digest,
                    identity_label=mask_identity(identity),
                    method=status.method,
                    first_login_at=stored.get("first_login_at") or time.time(),
                )
                detail = status.detail
        view = {**stored, "detail": detail}
        self._accounts[slot] = (time.monotonic(), view)
        return view

    async def require_ready(self, slot: str) -> None:
        account = await self.account(slot)
        label = SLOT_LABELS.get(slot, slot)
        if account["state"] == "mismatch":
            raise BridgeError("ACCOUNT_MISMATCH", f"{label}：{account['detail']}")
        if account["state"] == "unavailable":
            raise BridgeError("UNAVAILABLE", f"{label} 暂时不可用：{account['detail']}")
        if account["state"] != "logged_in":
            raise BridgeError("NOT_LOGGED_IN", f"{label} 还没有登录，请管理员在会话桥设置页登录")

    async def login(self, slot: str, method: str, payload: dict[str, str]) -> Row:
        driver = self._driver(slot)
        if method not in driver.login_methods:
            raise BridgeError("VALIDATION_FAILED", f"不支持的登录方式：{method}")
        if slot == "claude" and method != "api_key" and not self.setting("claude_subscription_ack"):
            raise BridgeError(
                "FORBIDDEN",
                "Claude 用订阅登录前，需要先在设置里确认了解合规风险（claude_subscription_ack）",
            )
        try:
            challenge = await driver.login(method, payload)
        except DriverError as error:
            raise from_driver(error) from error
        self._accounts.pop(slot, None)
        return {
            "state": challenge.state,
            "verification_url": challenge.verification_url,
            "user_code": challenge.user_code,
            "detail": challenge.detail,
        }

    async def login_state(self, slot: str) -> Row | None:
        challenge = await self._driver(slot).login_state()
        if challenge is None:
            return None
        if challenge.state != "pending":
            self._accounts.pop(slot, None)
        return {
            "state": challenge.state,
            "verification_url": challenge.verification_url,
            "user_code": challenge.user_code,
            "detail": challenge.detail,
        }

    async def logout(self, slot: str) -> None:
        try:
            await self._driver(slot).logout()
        except DriverError as error:
            raise from_driver(error) from error
        self._accounts.pop(slot, None)

    async def reset_account(self, slot: str) -> int:
        """更换账号：归档这个槽位的全部会话登记，忘掉登记的账号。"""
        self._driver(slot)
        sessions = self.store.list_sessions(slot=slot, limit=100_000)
        for session in sessions:
            await self._archive(session, remote=False)
        self.store.upsert_account(
            slot, identity_hash=None, identity_label=None, method=None, first_login_at=None
        )
        self._accounts.pop(slot, None)
        return len(sessions)

    # ---- 通道、模型、环境 ----
    def _channel(self, platform: str, mode: str) -> Channel:
        channel = find(self.channels, platform, mode)
        if channel is None:
            raise BridgeError("VALIDATION_FAILED", f"没有这个通道：{platform}.{mode}")
        if not channel.available or channel.slot not in self.drivers:
            raise BridgeError(
                "NOT_SUPPORTED", f"{platform}.{mode} 还不能用：{channel.note or '未启用'}"
            )
        return channel

    async def models(self, platform: str, mode: str) -> list[Row]:
        channel = self._channel(platform, mode)
        try:
            models = await self._driver(channel.slot).models(channel.mode)
        except DriverError as error:
            raise from_driver(error) from error
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "default": m.default,
                "efforts": list(m.efforts),
            }
            for m in models
        ]

    def environments(self, platform: str, mode: str) -> list[Row]:
        self._channel(platform, mode)
        return [
            env_view(env) for env in self.store.list_environments(platform, mode) if env["enabled"]
        ]

    # ---- 会话 ----
    def _spec(self, session: Row) -> SessionSpec:
        options = session["options"] or {}
        return SessionSpec(
            mode=session["mode"],
            model=session["model"],
            workdir=Path(session["workdir"]),
            sandbox=options.get("sandbox", "read_only"),
            network=bool(options.get("network", False)),
            effort=options.get("effort"),
            native_id=session["native_id"],
            turn_count=session["turn_count"],
        )

    async def _pick_model(self, channel: Channel, model: str | None) -> str | None:
        models = await self.models(channel.platform, channel.mode)
        ids = [m["id"] for m in models]
        if model is None:
            return next((m["id"] for m in models if m["default"]), ids[0] if ids else None)
        if not _MODEL_RE.match(model):
            raise BridgeError("VALIDATION_FAILED", "模型名称不合法")
        # Codex 的模型列表来自官方接口，必须在列表里；命令行类平台也接受完整的模型名
        if channel.slot == "openai" and model not in ids:
            raise BridgeError("VALIDATION_FAILED", f"没有这个模型，可选：{', '.join(ids)}")
        return model

    async def _clone(self, config: Row, workdir: Path) -> None:
        url = str(config.get("repo_url") or "")
        if not url:
            return
        args = [self.settings.git_bin, "clone", "--depth", "1"]
        if config.get("branch"):
            args += ["--branch", str(config["branch"])]
        proc = await asyncio.create_subprocess_exec(
            *args,
            "--",
            url,
            str(workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": "/nonexistent",
            },
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), self.settings.clone_timeout)
        except TimeoutError as error:
            proc.kill()
            raise BridgeError("UNAVAILABLE", "克隆仓库超时") from error
        if proc.returncode != 0:
            tail = redact(stderr.decode(errors="replace").strip()[-300:])
            raise BridgeError("UNAVAILABLE", f"克隆仓库失败：{tail}")

    async def create_session(
        self,
        caller: Caller,
        *,
        platform: str,
        mode: str,
        model: str | None = None,
        environment: str | None = None,
        title: str = "",
        effort: str | None = None,
    ) -> Row:
        self._need(caller, "write")
        channel = self._channel(platform, mode)
        await self.require_ready(channel.slot)
        options: Row = {"sandbox": "read_only", "network": False}
        env_config: Row = {}
        env_id = None
        if channel.mode == "chat":
            if environment:
                raise BridgeError("VALIDATION_FAILED", "聊天模式不需要环境")
        else:
            if not environment:
                raise BridgeError(
                    "VALIDATION_FAILED", "开发模式必须选择环境，先用 llm_environments 查看"
                )
            env = self.store.get_environment(environment)
            if (
                env is None
                or not env["enabled"]
                or (env["platform"], env["mode"]) != (platform, mode)
            ):
                raise BridgeError("VALIDATION_FAILED", "没有这个环境，或它不属于这个通道")
            env_config, env_id = env["config"] or {}, env["id"]
            options = {
                "sandbox": env_config.get("sandbox", "read_only"),
                "network": bool(env_config.get("network", False)),
            }
        if effort:
            options["effort"] = effort
        picked = await self._pick_model(channel, model)
        from bridge.store import new_id

        session_id = new_id("bs")
        workdir = self.settings.work_dir / session_id
        workdir.parent.mkdir(parents=True, exist_ok=True)
        try:
            if env_config.get("repo_url"):
                await self._clone(env_config, workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            spec = SessionSpec(
                mode=channel.mode,
                model=picked,
                workdir=workdir,
                sandbox=options["sandbox"],
                network=options["network"],
                effort=effort,
            )
            native = await self._driver(channel.slot).create(spec)
        except DriverError as error:
            shutil.rmtree(workdir, ignore_errors=True)
            raise from_driver(error) from error
        except BridgeError:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        session = self.store.create_session(
            id=session_id,
            platform=platform,
            mode=mode,
            slot=channel.slot,
            native_id=native,
            model=picked,
            environment_id=env_id,
            caller_id=caller.id,
            caller_label=caller.label,
            title=" ".join(title.split())[:120],
            workdir=str(workdir),
            options=options,
        )
        self.store.add_event(session_id, None, "created", {"by": caller.label, "model": picked})
        return session_view(session, full=True)

    def _need(self, caller: Caller, scope: str) -> None:
        if not caller.can(scope):
            raise BridgeError("FORBIDDEN", f"这个调用方没有 {scope} 权限")

    def _session(self, caller: Caller, session_id: str, *, metadata_only: bool = False) -> Row:
        session = self.store.get_session(session_id)
        if session is not None and (
            session["caller_id"] == caller.id
            or caller.can("takeover")
            or (metadata_only and caller.can("account"))
        ):
            return session
        raise BridgeError("NOT_FOUND", "没有这个会话")  # 不区分“不存在”和“不是你的”

    async def sync_external(self, slots: list[str]) -> list[str]:
        """把官方程序里有、桥里没有登记的会话登记成 external；返回同步失败的槽位。"""
        failed = []
        for slot in slots:
            try:
                natives = await self._driver(slot).list_native(100)
            except DriverError:
                failed.append(slot)
                continue
            for native in natives:
                if self.store.session_by_native(slot, native.native_id) is not None:
                    continue
                stamp = native.updated_at or native.created_at or time.time()
                self.store.create_session(
                    platform=EXTERNAL_PLATFORM[slot],
                    mode="dev",
                    slot=slot,
                    native_id=native.native_id,
                    model=native.model,
                    origin="external",
                    title=redact(native.title),
                    workdir=native.workdir or str(self.settings.work_dir / "external"),
                    options={"sandbox": "read_only", "network": False},
                    turn_count=1,
                    created_at=native.created_at or stamp,
                    updated_at=stamp,
                )
        return failed

    async def list_sessions(
        self,
        caller: Caller,
        *,
        scope: str = "mine",
        platform: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Row:
        note = ""
        if scope == "account":
            self._need(caller, "account")
            slots = sorted(
                {
                    c.slot
                    for c in self.channels
                    if c.slot in self.drivers and platform in (None, c.platform)
                }
            )
            failed = await self.sync_external(slots)
            if failed:
                note = "这些平台的会话列表暂时读不到：" + "、".join(
                    SLOT_LABELS.get(s, s) for s in failed
                )
            rows = self.store.list_sessions(platform=platform, limit=limit + 1, offset=offset)
        elif scope == "mine":
            rows = self.store.list_sessions(
                caller_id=caller.id, platform=platform, limit=limit + 1, offset=offset
            )
        else:
            raise BridgeError("VALIDATION_FAILED", "scope 只能是 mine 或 account")
        items = [
            {**session_view(row), "mine": row["caller_id"] == caller.id} for row in rows[:limit]
        ]
        return {
            "sessions": items,
            "next_offset": offset + limit if len(rows) > limit else None,
            "note": note,
        }

    async def get_session(
        self, caller: Caller, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> Row:
        session = self._session(caller, session_id, metadata_only=True)
        view: Row = {
            "session": session_view(session, full=True),
            "messages": [],
            "next_offset": None,
        }
        if session["caller_id"] != caller.id and not caller.can("takeover"):
            view["note"] = "不是你的会话：只能看到基本信息，没有读取内容的权限"
            return view
        messages: list[Row] = []
        if session["origin"] == "external":
            try:
                native = await self._driver(session["slot"]).read(
                    session["native_id"], session["workdir"]
                )
            except DriverError as error:
                raise from_driver(error) from error
            messages = [{"role": m.role, "text": redact(m.text), "at": m.at} for m in native]
        else:
            for turn in self.store.list_turns(session_id=session_id, limit=10_000):
                messages.append(
                    {
                        "role": "user",
                        "text": turn["text"],
                        "at": turn["queued_at"],
                        "turn_id": turn["id"],
                    }
                )
                if turn["status"] == "completed":
                    messages.append(
                        {
                            "role": "assistant",
                            "text": turn["result_text"] or "",
                            "at": turn["finished_at"],
                            "turn_id": turn["id"],
                        }
                    )
                elif turn["status"] != "cancelled":
                    messages.append(
                        {
                            "role": "status",
                            "text": turn["status"],
                            "at": turn["finished_at"],
                            "turn_id": turn["id"],
                        }
                    )
        view["messages"] = messages[offset : offset + limit]
        view["next_offset"] = offset + limit if len(messages) > offset + limit else None
        return view

    async def _archive(self, session: Row, *, remote: bool) -> None:
        for turn in self.store.list_turns(
            session_id=session["id"], statuses=("queued", "running"), limit=1000
        ):
            await self._cancel_turn(turn, session)
        if remote and session["native_id"]:
            with contextlib.suppress(DriverError):
                await self._driver(session["slot"]).archive(session["native_id"])
        workdir = Path(session["workdir"] or "")
        if session["origin"] == "bridge" and workdir.parent == self.settings.work_dir:
            shutil.rmtree(workdir, ignore_errors=True)
        self.store.update_session(session["id"], status="archived")

    async def archive_session(self, caller: Caller, session_id: str) -> Row:
        self._need(caller, "write")
        session = self._session(caller, session_id)
        if session["status"] != "archived":
            await self._archive(session, remote=session["origin"] == "bridge")
        refreshed = self.store.get_session(session_id)
        assert refreshed is not None  # noqa: S101
        return session_view(refreshed, full=True)

    # ---- 回合 ----
    async def send(
        self,
        caller: Caller,
        *,
        session_id: str,
        text: str,
        request_id: str,
        wait_seconds: float = 0,
    ) -> Row:
        self._need(caller, "write")
        if not _REQUEST_ID_RE.match(request_id or ""):
            raise BridgeError(
                "VALIDATION_FAILED", "request_id 只能用字母、数字和 . - _ :，最多 100 个字符"
            )
        existing = self.store.turn_by_request(caller.id, request_id)
        if existing is not None:
            if existing["session_id"] != session_id:
                raise BridgeError("CONFLICT", "这个 request_id 已经用在另一个会话上")
            return await self.wait_turn(existing["id"], wait_seconds)
        if not text.strip():
            raise BridgeError("VALIDATION_FAILED", "消息不能为空")
        if len(text) > self.setting("max_text_chars"):
            raise BridgeError(
                "VALIDATION_FAILED", f"消息太长（最多 {self.setting('max_text_chars')} 个字符）"
            )
        session = self._session(caller, session_id)
        if session["status"] != "active":
            raise BridgeError("CONFLICT", "会话已归档，不能再发送")
        if self.store.count_turns_since(caller.id, time.time() - 3600) >= self.setting(
            "send_per_hour"
        ):
            raise BridgeError("RATE_LIMITED", "这个调用方本小时的发送次数已到上限")
        await self.require_ready(session["slot"])
        turn = self.store.create_turn(session_id, caller.id, request_id, text)
        self.store.add_event(session_id, turn["id"], "queued", {"by": caller.label})
        self._enqueue(turn)
        return await self.wait_turn(turn["id"], wait_seconds)

    def _enqueue(self, turn: Row) -> None:
        session_id = turn["session_id"]
        self._done.setdefault(turn["id"], asyncio.Event())
        queue = self._queues.setdefault(session_id, asyncio.Queue())
        queue.put_nowait(turn["id"])
        worker = self._workers.get(session_id)
        if worker is None or worker.done():
            self._workers[session_id] = asyncio.create_task(
                self._work(session_id, queue), name=f"session-{session_id}"
            )

    async def _work(self, session_id: str, queue: asyncio.Queue[str]) -> None:
        while True:
            try:
                turn_id = await asyncio.wait_for(queue.get(), self._idle_seconds)
            except TimeoutError:
                if queue.empty():  # 这里到 return 之间没有 await，不会和 _enqueue 交错
                    self._queues.pop(session_id, None)
                    self._workers.pop(session_id, None)
                    return
                continue
            try:
                await self._process(turn_id)
            except Exception:
                logger.exception("turn %s crashed", turn_id)
                self._finish(
                    turn_id,
                    status="failed",
                    error_kind="platform_error",
                    error_message="会话桥内部错误",
                )

    def _finish(self, turn_id: str, **fields: Any) -> None:
        self.store.update_turn(turn_id, finished_at=time.time(), **fields)
        self._cancel_requested.discard(turn_id)
        event = self._done.pop(turn_id, None)
        if event is not None:
            event.set()

    async def _process(self, turn_id: str) -> None:
        turn = self.store.get_turn(turn_id)
        if turn is None or turn["status"] != "queued":
            return
        session = self.store.get_session(turn["session_id"])
        assert session is not None  # noqa: S101
        limiter = self._limiters[session["slot"]]
        await limiter.acquire(lambda: self.setting("concurrency"))
        try:
            turn = self.store.get_turn(turn_id)
            if turn is None or turn["status"] != "queued":  # 排队等并发名额时被取消了
                return
            self.store.update_turn(turn_id, status="running", started_at=time.time())
            self.store.add_event(session["id"], turn_id, "started", {})
            await self._run(turn, session)
        finally:
            await limiter.release()

    async def _run(self, turn: Row, session: Row) -> None:
        turn_id, session_id = turn["id"], session["id"]
        driver = self._driver(session["slot"])
        spec = self._spec(session)

        async def sink(kind: str, payload: Row) -> None:
            clean = {k: redact(v) if isinstance(v, str) else v for k, v in payload.items()}
            self.store.add_event(session_id, turn_id, kind, clean)

        try:
            async with asyncio.timeout(self.setting("turn_timeout_seconds")):
                outcome = await driver.run_turn(spec, turn["text"], sink)
        except TimeoutError:
            await driver.cancel(spec)
            self.store.add_event(session_id, turn_id, "failed", {"kind": "timeout"})
            self._finish(
                turn_id,
                status="failed",
                error_kind="timeout",
                error_message="超过单回合时限，已中断",
            )
            return
        except DriverError as error:
            if turn_id in self._cancel_requested:
                self.store.add_event(session_id, turn_id, "cancelled", {})
                self._finish(turn_id, status="cancelled")
                return
            if error.kind in ("not_logged_in", "unavailable"):
                self._accounts.pop(session["slot"], None)
            status = "unknown" if error.kind == "timeout" else "failed"
            self.store.add_event(session_id, turn_id, status, {"kind": error.kind})
            self._finish(
                turn_id, status=status, error_kind=error.kind, error_message=redact(error.message)
            )
            return
        summary = {
            "usage": outcome.usage,
            "commands": outcome.commands[:50],
            "files": outcome.files[:200],
        }
        fields: Row = {"turn_count": session["turn_count"] + 1}
        if not session["title"]:
            fields["title"] = " ".join(turn["text"].split())[:120]
        self.store.update_session(session_id, **fields)
        self.store.add_event(session_id, turn_id, "completed", {})
        self._finish(
            turn_id,
            status="completed",
            result_text=redact(outcome.text),
            summary=summary,
            native_turn_id=outcome.native_turn_id,
        )

    async def wait_turn(self, turn_id: str, wait_seconds: float = 0) -> Row:
        turn = self.store.get_turn(turn_id)
        if turn is None:
            raise BridgeError("NOT_FOUND", "没有这个回合")
        event = self._done.get(turn_id)
        wait = max(0.0, min(float(wait_seconds or 0), MAX_WAIT_SECONDS))
        if turn["status"] not in TERMINAL and event is not None and wait > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), wait)
            turn = self.store.get_turn(turn_id)
            assert turn is not None  # noqa: S101
        return self.turn_view(turn)

    async def get_turn(self, caller: Caller, turn_id: str, wait_seconds: float = 0) -> Row:
        turn = self.store.get_turn(turn_id)
        if turn is None:
            raise BridgeError("NOT_FOUND", "没有这个回合")
        self._session(caller, turn["session_id"])
        return await self.wait_turn(turn_id, wait_seconds)

    async def _cancel_turn(self, turn: Row, session: Row) -> None:
        if turn["status"] == "queued":
            self.store.add_event(session["id"], turn["id"], "cancelled", {})
            self._finish(turn["id"], status="cancelled")
        elif turn["status"] == "running":
            self._cancel_requested.add(turn["id"])
            await self._driver(session["slot"]).cancel(self._spec(session))

    async def cancel_turn(self, caller: Caller, turn_id: str) -> Row:
        self._need(caller, "write")
        turn = self.store.get_turn(turn_id)
        if turn is None:
            raise BridgeError("NOT_FOUND", "没有这个回合")
        session = self._session(caller, turn["session_id"])
        await self._cancel_turn(turn, session)
        return await self.wait_turn(turn_id, 5 if turn["status"] == "running" else 0)

    def turn_view(self, turn: Row) -> Row:
        session = self.store.get_session(turn["session_id"]) or {}
        view: Row = {
            "turn_id": turn["id"],
            "session_id": turn["session_id"],
            "platform": session.get("platform"),
            "mode": session.get("mode"),
            "model": session.get("model"),
            "status": turn["status"],
            "queued_at": turn["queued_at"],
            "started_at": turn["started_at"],
            "finished_at": turn["finished_at"],
        }
        if turn["status"] == "queued":
            view["ahead_in_session"] = self.store.queue_position(turn)
        elif turn["status"] == "running":
            events = self.store.list_events(turn["session_id"], limit=10_000)
            recent = [
                e
                for e in events
                if e["turn_id"] == turn["id"] and e["kind"] in ("message", "notice", "command")
            ]
            if recent:
                last = recent[-1]["payload"] or {}
                view["progress"] = str(last.get("text") or last.get("command") or "")[:300]
        elif turn["status"] == "completed":
            view["result"] = turn["result_text"] or ""
            view["summary"] = turn["summary"] or {}
        elif turn["status"] != "cancelled":
            # 不叫 error：工具出错时的返回才用 error
            view["failure"] = {"kind": turn["error_kind"], "message": turn["error_message"]}
        return view

    # ---- 管理页用 ----
    async def overview(self) -> Row:
        accounts = {}
        for slot in self.drivers:
            account = await self.account(slot)
            accounts[slot] = {
                "label": SLOT_LABELS.get(slot, slot),
                "state": account["state"],
                "identity": account.get("identity_label"),
                "method": account.get("method"),
                "detail": account.get("detail", ""),
                "first_login_at": account.get("first_login_at"),
                "login_methods": list(self.drivers[slot].login_methods),
                "running": self._limiters[slot].running,
            }
        channels = [
            {
                "platform": c.platform,
                "mode": c.mode,
                "slot": c.slot,
                "available": c.available and c.slot in self.drivers,
                "note": c.note,
            }
            for c in self.channels
        ]
        queued = len(self.store.list_turns(statuses=("queued",), limit=100_000))
        running = len(self.store.list_turns(statuses=("running",), limit=100_000))
        return {"accounts": accounts, "channels": channels, "queued": queued, "running": running}


def env_view(env: Row) -> Row:
    config = env["config"] or {}
    return {
        "id": env["id"],
        "platform": env["platform"],
        "mode": env["mode"],
        "name": env["name"],
        "enabled": env["enabled"],
        "repo_url": config.get("repo_url"),
        "branch": config.get("branch"),
        "sandbox": config.get("sandbox", "read_only"),
        "network": bool(config.get("network", False)),
        "description": config.get("description", ""),
    }


def session_view(session: Row, *, full: bool = False) -> Row:
    view: Row = {
        "session_id": session["id"],
        "platform": session["platform"],
        "mode": session["mode"],
        "model": session["model"],
        "title": session["title"],
        "status": session["status"],
        "origin": session["origin"],
        "owner": session["caller_label"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
    }
    if full:
        view["environment_id"] = session["environment_id"]
        view["turns"] = session["turn_count"]
        view["options"] = session["options"]
    return view


def valid_mode(mode: str) -> Mode:
    if mode not in ("chat", "dev", "cloud"):
        raise BridgeError("VALIDATION_FAILED", "mode 只能是 chat、dev 或 cloud")
    return mode  # type: ignore[return-value]
