"""会话与回合（docs/v2/LLM_BRIDGE_PLAN.md 第 5、7、8 节）。

- 会话归新建它的调用方；默认只能看到、续发自己的会话。权限范围（scopes）：
  write（新建、发送、取消、归档）、account（列出账号下所有会话的标题等）、takeover（读取、续发不属于自己的会话）；
- 一个会话一条串行队列；不同会话并行，每个账号槽位有并发上限；
- 发送带幂等号：同一个调用方用同一个 request_id 重发，返回原来的回合；
- 一个槽位只认第一次登录的那个账号：发现换了账号就拒绝调用，直到管理员明确更换；
- 进程重启后：运行中的回合记为 unknown（不知道平台做完没有），排队中的继续排；
- 一座桥可以只控制一部分平台（设置 platforms）：没勾选的平台不能登录、不能新建会话和发送，
  已有的登录和会话记录保留，还能查看和退出登录。
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
    # 每个平台同时进行的会话数（T-0236：按平台计数；一个会话同一时间只跑一个回合）
    "concurrency": 3,
    # 同一个平台两次开始之间至少隔几秒：几个名额不会同时起跑
    "min_start_interval_seconds": 2,
    "turn_timeout_seconds": 1800,
    "send_per_hour": 120,  # 每个调用方每小时的发送次数
    "max_text_chars": 100_000,
    # Claude 用订阅登录（oauth_token / existing）前，管理员要先确认了解合规风险（方案 6.3、D3）
    "claude_subscription_ack": False,
}
SETTING_LIMITS = {
    "concurrency": (1, 16),
    "min_start_interval_seconds": (0, 60),
    "turn_timeout_seconds": (30, 6 * 3600),
    "send_per_hour": (1, 10_000),
    "max_text_chars": (1, 1_000_000),
}
MAX_WAIT_SECONDS = 50
MAX_EVENT_WAIT_SECONDS = 25
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
UNKNOWN_IDENTITY = "unknown"
_ANONYMOUS = "用密钥或令牌登录的账号（看不到是哪个）"
_ANONYMOUS_NOTE = (
    "用密钥或令牌登录时看不到是哪个账号：换一把密钥不算换账号；要换成另一个账号，请先点“更换账号”"
)
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
    """一个平台的名额：上限可以在运行中调整；两次开始之间隔开一段时间。"""

    def __init__(self) -> None:
        self._running = 0
        self._cond = asyncio.Condition()
        self._next_start = 0.0

    @property
    def running(self) -> int:
        return self._running

    async def acquire(self, limit: Any, interval: Any = lambda: 0) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self._running < limit())
            self._running += 1
            # 在锁里排好自己的开始时刻：同时拿到名额的几个依次错开
            now = time.monotonic()
            start = max(now, self._next_start)
            self._next_start = start + max(0.0, float(interval()))
        try:
            if start > now:
                await asyncio.sleep(start - now)
        except BaseException:
            await self.release()
            raise

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
        self._limiters: dict[str, SlotLimiter] = {}
        self._accounts: dict[str, tuple[float, Row]] = {}
        self._idle_seconds = 60.0

    # ---- 设置 ----
    def setting(self, key: str) -> Any:
        default = (
            self.settings.default_min_start_interval
            if key == "min_start_interval_seconds"
            else DEFAULTS[key]
        )
        return self.store.get_setting(key, default)

    def settings_view(self) -> Row:
        return {**{key: self.setting(key) for key in DEFAULTS}, "platforms": self.platforms()}

    # ---- 这座桥控制哪些平台 ----
    def all_platforms(self) -> list[str]:
        """这座桥装了驱动的平台，按通道表的顺序。"""
        seen: list[str] = []
        for channel in self.channels:
            if channel.slot in self.drivers and channel.platform not in seen:
                seen.append(channel.platform)
        return seen

    def platforms(self) -> list[str]:
        """管理员让这座桥控制的平台；没设置过就是全部。"""
        chosen = self.store.get_setting("platforms", None)
        known = self.all_platforms()
        return known if chosen is None else [p for p in known if p in chosen]

    def platform_enabled(self, platform: str) -> bool:
        return platform in self.platforms()

    def slot_enabled(self, slot: str) -> bool:
        """这个账号槽位上有没有被勾选的平台（ChatGPT 和 Codex 共用 openai 槽位）。"""
        enabled = self.platforms()
        return any(c.slot == slot and c.platform in enabled for c in self.channels)

    def _set_platforms(self, value: Any) -> None:
        known = self.all_platforms()
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(p, str) for p in value)
            or len(set(value)) != len(value)
        ):
            raise BridgeError("VALIDATION_FAILED", "platforms 要填至少一个平台，不能重复")
        unknown = [p for p in value if p not in known]
        if unknown:
            raise BridgeError("VALIDATION_FAILED", f"这座桥没有这些平台：{'、'.join(unknown)}")
        self.store.set_setting("platforms", [p for p in known if p in value])

    def update_settings(self, values: Row) -> Row:
        for key, value in values.items():
            if key == "platforms":
                self._set_platforms(value)
                continue
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

    def _limiter(self, platform: str) -> SlotLimiter:
        return self._limiters.setdefault(platform, SlotLimiter())

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
            # 平台报告了账号（例如邮箱）就按账号比对；用密钥或令牌登录时看不到是哪个账号，
            # 都记成“未知账号”：换一把密钥不算换账号，从未知账号换成具体账号（或反过来）才算
            identity = status.identity
            digest = hashlib.sha256((identity or UNKNOWN_IDENTITY).encode()).hexdigest()
            if stored.get("identity_hash") and stored["identity_hash"] != digest:
                now = mask_identity(identity) if identity else _ANONYMOUS
                registered = stored.get("identity_label") or _ANONYMOUS
                stored = self.store.upsert_account(slot, state="mismatch")
                detail = (
                    f"现在登录的是另一个账号（{now}），这座会话桥登记的是 {registered}。"
                    "要换账号请在管理页点“更换账号”"
                )
            else:
                stored = self.store.upsert_account(
                    slot,
                    state="logged_in",
                    identity_hash=digest,
                    identity_label=mask_identity(identity) if identity else None,
                    method=status.method,
                    first_login_at=stored.get("first_login_at") or time.time(),
                )
                detail = status.detail
                if identity is None:
                    detail = "；".join(part for part in (detail, _ANONYMOUS_NOTE) if part)
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
            raise BridgeError(
                "NOT_LOGGED_IN", f"{label} 还没有登录，请管理员在“大模型客户端”页面登录"
            )

    async def login(self, slot: str, method: str, payload: dict[str, str]) -> Row:
        driver = self._driver(slot)
        if not self.slot_enabled(slot):
            raise BridgeError(
                "CONFLICT",
                f"这座桥没有启用 {SLOT_LABELS.get(slot, slot)}：先在“控制的平台”里勾选，再登录",
            )
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
        self._require_platform(platform)
        return channel

    def _require_platform(self, platform: str) -> None:
        if not self.platform_enabled(platform):
            raise BridgeError(
                "NOT_SUPPORTED",
                f"这座桥没有启用 {platform}（管理员没有让它控制这个平台）；"
                f"它控制的平台：{'、'.join(self.platforms())}",
            )

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
                    if c.slot in self.drivers
                    and platform in (None, c.platform)
                    and self.platform_enabled(c.platform)
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
        self._require_platform(session["platform"])
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
            # 队列里的记号只表示“有一条在等”；真正做哪一条按队列里的顺序取（可以被调整过）
            turn = self.store.next_queued(session_id)
            if turn is None:
                continue
            turn_id = turn["id"]
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
        limiter = self._limiter(session["platform"])
        await limiter.acquire(
            lambda: self.setting("concurrency"),
            lambda: self.setting("min_start_interval_seconds"),
        )
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

    # ---- 聊天页用（管理接口；docs/v2/LLM_CHAT_PLAN.md 第 4.1 节） ----
    def admin_session_view(self, session: Row, busy: set[str] | None = None) -> Row:
        busy = self.store.busy_sessions() if busy is None else busy
        return {
            **session_view(session, full=True),
            "slot": session["slot"],
            "owner_id": session["caller_id"],
            "busy": session["id"] in busy,
        }

    def _required_session(self, session_id: str) -> Row:
        session = self.store.get_session(session_id)
        if session is None:
            raise BridgeError("NOT_FOUND", "没有这个会话")
        return session

    async def conversation(self, session_id: str) -> Row:
        """一个会话的信息和消息：谁发的、回答、过程、失败原因、进行中已经给出的部分。"""
        session = self._required_session(session_id)
        view = self.admin_session_view(session)
        environment = (
            self.store.get_environment(session["environment_id"])
            if session["environment_id"]
            else None
        )
        view["environment"] = env_view(environment) if environment else None
        events = self.store.list_events(session_id, limit=100_000)
        last_event_id = events[-1]["id"] if events else 0
        messages: list[Row] = []
        if session["origin"] == "external":
            try:
                native = await self._driver(session["slot"]).read(
                    session["native_id"], session["workdir"]
                )
            except DriverError as error:
                raise from_driver(error) from error
            messages = [{"role": m.role, "text": redact(m.text), "at": m.at} for m in native]
            return {"session": view, "messages": messages, "last_event_id": last_event_id}
        by_turn: dict[str, list[Row]] = {}
        for event in events:
            if event["turn_id"]:
                by_turn.setdefault(event["turn_id"], []).append(event)
        for turn in self.store.list_turns(session_id=session_id, limit=10_000):
            mine = by_turn.get(turn["id"], [])
            queued = next((e for e in mine if e["kind"] == "queued"), None)
            messages.append(
                {
                    "role": "user",
                    "turn_id": turn["id"],
                    "text": turn["text"],
                    "at": turn["queued_at"],
                    "by": (queued["payload"] or {}).get("by") if queued else None,
                    "by_id": turn["caller_id"],
                }
            )
            steps = [step for step in (_step(e) for e in mine) if step]
            status = turn["status"]
            reply: Row = {
                "role": "assistant",
                "turn_id": turn["id"],
                "status": status,
                "at": turn["finished_at"] or turn["started_at"] or turn["queued_at"],
                "text": "",
                "steps": steps,
            }
            if status == "completed":
                reply["text"] = turn["result_text"] or ""
                # 和结果一样的那一段不在“过程”里再出现一次。它不一定排在最后：
                # 官方程序在同一条消息里既给文字又调工具时，文字的事件先到
                for index in range(len(steps) - 1, -1, -1):
                    if steps[index]["kind"] == "message" and steps[index]["text"] == reply["text"]:
                        reply["steps"] = steps[:index] + steps[index + 1 :]
                        break
            else:
                # 没做完（进行中、失败、结果未知、已取消）：显示已经给出的部分
                reply["text"] = "\n\n".join(s["text"] for s in steps if s["kind"] == "message")
                reply["steps"] = [s for s in steps if s["kind"] != "message"]
                if status in ("failed", "unknown"):
                    reply["failure"] = {
                        "kind": turn["error_kind"],
                        "message": turn["error_message"],
                    }
                elif status == "queued":
                    reply["ahead_in_session"] = self.store.queue_position(turn)
            messages.append(reply)
        return {"session": view, "messages": messages, "last_event_id": last_event_id}

    async def session_events(self, session_id: str, after: int, wait_seconds: float) -> Row:
        """这个会话在 after 之后的事件；没有新事件且还有回合没做完时，最多等 wait_seconds 秒。"""
        self._required_session(session_id)
        deadline = time.monotonic() + max(0.0, min(wait_seconds, MAX_EVENT_WAIT_SECONDS))
        while True:
            events = self.store.list_events(session_id, after_id=after, limit=500)
            busy = session_id in self.store.busy_sessions()
            if events or not busy or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
        return {
            "events": [_event_view(e) for e in events],
            "busy": busy,
            "last_id": events[-1]["id"] if events else after,
        }

    # ---- 队列（T-0236）----
    def platform_load(self) -> Row:
        """每个平台进行中的会话数、在等的会话数和上限。"""
        waiting = self.store.waiting_by_platform()
        limit = self.setting("concurrency")
        return {
            platform: {
                "running": self._limiter(platform).running,
                "waiting": waiting.get(platform, 0),
                "limit": limit,
            }
            for platform in self.platforms()
        }

    def _queue_item(self, turn: Row, labels: dict[str, Any]) -> Row:
        return {
            "turn_id": turn["id"],
            "status": turn["status"],
            "text": turn["text"],
            "by": labels.get(turn["id"]),
            "caller_id": turn["caller_id"],
            "queued_at": turn["queued_at"],
            "started_at": turn["started_at"],
        }

    def queue_view(self, session_id: str) -> Row:
        """这个会话正在跑的那一条和排队中的各条（按将要执行的顺序）。"""
        self._required_session(session_id)
        labels = {
            e["turn_id"]: (e["payload"] or {}).get("by")
            for e in self.store.list_events(session_id, limit=10_000)
            if e["kind"] == "queued"
        }
        running = self.store.running_turn(session_id)
        return {
            "session_id": session_id,
            "running": self._queue_item(running, labels) if running else None,
            "queued": [self._queue_item(t, labels) for t in self.store.queued_turns(session_id)],
        }

    def caller_queue(self, caller: Caller, session_id: str) -> Row:
        self._session(caller, session_id)
        return self.queue_view(session_id)

    def _queued_turn(self, turn_id: str) -> Row:
        turn = self.store.get_turn(turn_id)
        if turn is None:
            raise BridgeError("NOT_FOUND", "没有这条消息")
        if turn["status"] != "queued":
            raise BridgeError("CONFLICT", "这条消息已经开始处理，不能再改")
        return turn

    def edit_queued(self, turn_id: str, text: str) -> Row:
        turn = self._queued_turn(turn_id)
        if not text.strip():
            raise BridgeError("VALIDATION_FAILED", "消息不能为空")
        if len(text) > self.setting("max_text_chars"):
            raise BridgeError(
                "VALIDATION_FAILED", f"消息太长（最多 {self.setting('max_text_chars')} 个字符）"
            )
        self.store.update_turn(turn_id, text=text)
        self.store.add_event(turn["session_id"], turn_id, "edited", {})
        return self.queue_view(turn["session_id"])

    def move_queued(self, turn_id: str, to: str) -> Row:
        """把排队中的一条调到队首或队尾。"""
        turn = self._queued_turn(turn_id)
        if to not in ("first", "last"):
            raise BridgeError("VALIDATION_FAILED", "to 只能是 first 或 last")
        edge = self.store.queue_edge(turn["session_id"], first=to == "first")
        if edge is not None:
            self.store.update_turn(turn_id, position=edge - 1 if to == "first" else edge + 1)
            self.store.add_event(turn["session_id"], turn_id, "moved", {"to": to})
        return self.queue_view(turn["session_id"])

    def rename_session(self, session_id: str, title: str) -> Row:
        session = self._required_session(session_id)
        clean = " ".join(redact(title).split())[:120]
        if not clean:
            raise BridgeError("VALIDATION_FAILED", "标题不能为空")
        self.store.update_session(session_id, title=clean, updated_at=session["updated_at"])
        return self.admin_session_view(self._required_session(session_id))

    # ---- 管理页用 ----
    async def overview(self) -> Row:
        enabled = self.platforms()
        accounts = {}
        for slot in self.drivers:
            account = await self.account(slot)
            accounts[slot] = {
                "label": SLOT_LABELS.get(slot, slot),
                # 这个槽位上有没有被勾选的平台；没有时不能登录，已有的登录可以退出
                "enabled": self.slot_enabled(slot),
                "state": account["state"],
                # 登记过账号（哪怕看不到是哪个）就算 registered：
                # 退出登录后仍然保留，用来发现换了账号
                "registered": bool(account.get("identity_hash")),
                "identity": account.get("identity_label"),
                "method": account.get("method") if account["state"] == "logged_in" else None,
                "detail": account.get("detail", ""),
                "first_login_at": account.get("first_login_at"),
                "login_methods": list(self.drivers[slot].login_methods),
                "running": sum(
                    self._limiter(c).running
                    for c in {c.platform for c in self.channels if c.slot == slot}
                ),
            }
        channels = [
            {
                "platform": c.platform,
                "mode": c.mode,
                "slot": c.slot,
                "enabled": c.platform in enabled,
                "available": c.available and c.slot in self.drivers and c.platform in enabled,
                "note": c.note,
            }
            for c in self.channels
        ]
        queued = len(self.store.list_turns(statuses=("queued",), limit=100_000))
        running = len(self.store.list_turns(statuses=("running",), limit=100_000))
        return {
            "platforms": enabled,
            "accounts": accounts,
            "channels": channels,
            "queued": queued,
            # 每个平台进行中的会话数、在等的会话数和上限（T-0236）
            "load": self.platform_load(),
            "running": running,
        }

    async def caller_overview(self) -> Row:
        """给调用方（智能体）看的概况：只有这座桥控制的平台。"""
        view = await self.overview()
        return {
            **view,
            "accounts": {s: a for s, a in view["accounts"].items() if a["enabled"]},
            "channels": [c for c in view["channels"] if c["enabled"]],
        }


def _event_view(event: Row) -> Row:
    """给聊天页的一条事件：种类、属于哪个回合，以及正文（一段回答、一条命令、改动的文件）。"""
    payload = event["payload"] or {}
    view: Row = {
        "id": event["id"],
        "turn_id": event["turn_id"],
        "kind": event["kind"],
        "at": event["at"],
    }
    for key in ("text", "command", "paths", "by"):
        if key in payload:
            view[key] = payload[key]
    if event["kind"] in ("failed", "unknown") and payload.get("kind"):
        view["reason"] = payload["kind"]
    return view


def _step(event: Row) -> Row | None:
    """回合进行中的一条事件 → 页面“过程”里的一步；别的事件（排队、完成等）不算。"""
    payload = event["payload"] or {}
    kind = event["kind"]
    if kind in ("message", "notice") and payload.get("text"):
        return {"kind": kind, "text": str(payload["text"]), "at": event["at"]}
    if kind == "command" and payload.get("command"):
        return {"kind": "command", "text": str(payload["command"]), "at": event["at"]}
    if kind == "files" and payload.get("paths"):
        return {
            "kind": "files",
            "text": "、".join(str(p) for p in payload["paths"]),
            "at": event["at"],
        }
    return None


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
