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
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bridge import attachments as files
from bridge import quota
from bridge.channels import SLOT_LABELS, Channel, find
from bridge.config import Settings
from bridge.drivers.base import Driver, DriverError, Mode, SessionSpec
from bridge.redact import mask_identity, redact
from bridge.store import Row, Store, message_of, new_id

logger = logging.getLogger("bridge.engine")

# interrupted：被平台的额度打断，到重置时间后可以自动续跑（T-0237）
TERMINAL = ("completed", "failed", "cancelled", "unknown", "interrupted")
RESUME_TEXT = "继续意外中断的任务"
SYSTEM_CALLER = "system"
SYSTEM_LABEL = "系统（自动续跑）"
# 插入消息降级成“中断后合并重发”时，接在新消息后面给模型看的一句
MERGED_SUFFIX = (
    "\n\n（上面是在你做上一件事的中途插进来的新消息。请先处理它，然后接着刚才没做完的事继续。）"
)
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
    # 额度用完被打断的会话，到重置时间后自动发一句“继续”（T-0237）
    "auto_resume": True,
    "auto_resume_max": 3,  # 同一次中断最多自动续几次
    "resume_text": RESUME_TEXT,
}
SETTING_LIMITS = {
    "concurrency": (1, 16),
    "min_start_interval_seconds": (0, 60),
    "turn_timeout_seconds": (30, 6 * 3600),
    "send_per_hour": (1, 10_000),
    "max_text_chars": (1, 1_000_000),
    "auto_resume_max": (1, 10),
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
        # 被插入的消息打断的回合 → 打断它的那个新回合
        self._superseded: dict[str, str] = {}
        self._limiters: dict[str, SlotLimiter] = {}
        # 额度用完后的暂停与续跑（T-0237）
        self._pause_wake: dict[str, asyncio.Event] = {}
        self._resume_wake = asyncio.Event()
        self._resumer: asyncio.Task[None] | None = None
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
            if isinstance(DEFAULTS[key], str):
                if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
                    raise BridgeError("VALIDATION_FAILED", f"{key} 要填 1 到 200 个字")
                value = value.strip()
            elif isinstance(DEFAULTS[key], bool):
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
        # 上传了但一直没有随消息发出去的附件：过一天就清掉
        for stale in self.store.stale_uploads(time.time() - files.STALE_UPLOAD_SECONDS):
            (self.attachment_root / stale["path"]).unlink(missing_ok=True)
            self.store.delete_attachment(stale["id"])
        self._resumer = asyncio.create_task(self._resume_loop(), name="quota-resume")

    def _limiter(self, platform: str) -> SlotLimiter:
        return self._limiters.setdefault(platform, SlotLimiter())

    async def shutdown(self) -> None:
        tasks = list(self._workers.values()) + ([self._resumer] if self._resumer else [])
        for task in tasks:
            task.cancel()
        for task in tasks:
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
        attachments: list[str] | None = None,
        mode: str = "queue",
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
        inserted = self.store.inserted_by_request(caller.id, request_id)
        if inserted is not None:  # 重试一条已经插进去的消息：不再插一次
            if inserted["session_id"] != session_id:
                raise BridgeError("CONFLICT", "这个 request_id 已经用在另一个会话上")
            view = await self.wait_turn(inserted["turn_id"], wait_seconds)
            return {**view, "delivered": "inserted"}
        if not text.strip():
            raise BridgeError("VALIDATION_FAILED", "消息不能为空")
        if len(text) > self.setting("max_text_chars"):
            raise BridgeError(
                "VALIDATION_FAILED", f"消息太长（最多 {self.setting('max_text_chars')} 个字符）"
            )
        session = self._session(caller, session_id)
        if session["status"] != "active":
            raise BridgeError("CONFLICT", "会话已归档，不能再发送")
        hour_ago = time.time() - 3600
        sent = self.store.count_turns_since(caller.id, hour_ago)
        if sent + self.store.count_inserted_since(caller.id, hour_ago) >= self.setting(
            "send_per_hour"
        ):
            raise BridgeError("RATE_LIMITED", "这个调用方本小时的发送次数已到上限")
        self._require_platform(session["platform"])
        await self.require_ready(session["slot"])
        attached = self._pending_attachments(session, attachments or [])
        running = self.store.running_turn(session_id) if mode == "insert" else None
        merged = False
        if running is not None:
            # 插入：先试着把消息送进正在跑的回合（带附件的不试，直接合并重发）
            if not attached and await self._steer(session, running, text):
                self.store.add_message(
                    session_id,
                    running["id"],
                    "user",
                    "text",
                    text,
                    by=caller.label,
                    delivery="inserted",
                    data={"caller_id": caller.id, "request_id": request_id},
                )
                self.store.add_event(session_id, running["id"], "inserted", {"by": caller.label})
                view = await self.wait_turn(running["id"], wait_seconds)
                return {**view, "delivered": "inserted"}
            merged = True
        busy = session_id in self.store.busy_sessions()
        edge = self.store.queue_edge(session_id, first=True) if mode == "insert" else None
        turn = self.store.create_turn(
            session_id,
            caller.id,
            request_id,
            text,
            by=caller.label,
            delivery="merged" if merged else "turn",
        )
        if edge is not None:  # 插入的消息排在这个会话队列的最前面
            self.store.update_turn(turn["id"], position=edge - 1)
        asked = self.store.user_message(turn["id"])
        if attached and asked is not None:
            self.store.bind_attachments([a["id"] for a in attached], turn["id"], asked["id"])
        self.store.add_event(session_id, turn["id"], "queued", {"by": caller.label})
        if merged and running is not None:
            # 降级：中断正在跑的回合（记成被打断，不是失败），新回合里带上“接着刚才的做”
            self._superseded[running["id"]] = turn["id"]
            await self._driver(session["slot"]).cancel(self._spec(session))
        self._enqueue(self.store.get_turn(turn["id"]) or turn)
        view = await self.wait_turn(turn["id"], wait_seconds)
        return {**view, "delivered": "merged" if merged else "queued" if busy else "started"}

    async def _steer(self, session: Row, running: Row, text: str) -> bool:
        """把消息送进正在跑的回合；平台在规定时间内确认收下、回合也还在跑，才算成功。"""
        try:
            async with asyncio.timeout(self.settings.steer_timeout_seconds):
                accepted = await self._driver(session["slot"]).steer(self._spec(session), text)
        except (TimeoutError, DriverError):
            return False
        now = self.store.get_turn(running["id"])
        return bool(accepted) and now is not None and now["status"] == "running"

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
            # 这个平台因为额度用完暂停着：等到重置时间（或有人点“现在就试”）再取下一条
            session = self.store.get_session(session_id)
            if session is not None:
                await self._wait_platform(session["platform"])
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
        self._superseded.pop(turn_id, None)
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

        text = turn["text"]
        first = self.store.user_message(turn_id)
        if first is not None and first["delivery"] == "merged":
            text += MERGED_SUFFIX  # 存下来的提问还是原话，这一句只给模型看
        inputs = self.store.turn_attachments(turn_id, "input")
        if inputs:
            # 图片直接交给模型；别的文件放进工作目录，在提问里注明，由模型自己读
            spec = replace(
                spec,
                images=tuple(
                    (str(self.attachment_root / a["path"]), a["media_type"])
                    for a in inputs
                    if files.is_image(a["media_type"])
                ),
            )
            placed = self._place_files(spec.workdir, inputs)
            if placed:
                text += f"\n\n（随这条消息带来的文件已放在工作目录里：{'、'.join(placed)}）"

        async def sink(kind: str, payload: Row) -> None:
            if kind == "quota":  # 官方程序报出的额度：记下来，不算会话里的事件
                self._save_quota(session["slot"], payload.get("windows") or [])
                return
            if kind == "image":  # 模型画的图：收进附件，事件里只留名字
                self._collect_output(session, turn_id, Path(str(payload.get("path"))), image=True)
                return
            clean = {k: redact(v) if isinstance(v, str) else v for k, v in payload.items()}
            self.store.add_event(session_id, turn_id, kind, clean)
            made = message_of(kind, clean)
            if made is not None:  # 会话内容落库：每段话、每个过程事件各一行
                role, message_kind, text, data = made
                self.store.add_message(session_id, turn_id, role, message_kind, text, data=data)

        try:
            async with asyncio.timeout(self.setting("turn_timeout_seconds")):
                outcome = await driver.run_turn(spec, text, sink)
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
            if turn_id in self._superseded:
                # 被后面插入的消息打断：不是失败，新回合里会接着做
                self.store.add_event(
                    session_id,
                    turn_id,
                    "interrupted",
                    {"kind": "superseded", "by_turn": self._superseded[turn_id]},
                )
                self._finish(
                    turn_id,
                    status="interrupted",
                    error_kind="superseded",
                    error_message="被后面插入的消息打断，新消息里会接着做",
                )
                return
            if turn_id in self._cancel_requested:
                self.store.add_event(session_id, turn_id, "cancelled", {})
                self._finish(turn_id, status="cancelled")
                return
            if error.quota:
                self._interrupt(turn, session, error)
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
        if session["mode"] == "dev":  # 开发会话里改动、新建的文件收进附件
            for path in list(dict.fromkeys(outcome.files))[: files.MAX_OUTPUT_PER_TURN]:
                self._collect_output(session, turn_id, Path(path))
        result = redact(outcome.text)
        said = [
            m["text"]
            for m in self.store.turn_messages(turn_id)
            if m["role"] == "assistant" and m["kind"] == "text"
        ]
        if result and result not in said:  # 结果没有作为一段话报出来过：补一行
            self.store.add_message(session_id, turn_id, "assistant", "text", result)
        self.store.add_event(session_id, turn_id, "completed", {})
        self._finish(
            turn_id,
            status="completed",
            result_text=result,
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
        elif turn["status"] == "interrupted":
            view["failure"] = {"kind": turn["error_kind"], "message": turn["error_message"]}
            view["resume"] = self._resume_view(turn)
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

    # ---- 附件（T-0236 第二步）----
    @property
    def attachment_root(self) -> Path:
        return self.settings.data_dir / "attachments"

    def _put_attachment(
        self,
        session_id: str,
        direction: str,
        name: str,
        media_type: str,
        data: bytes,
        *,
        turn_id: str | None = None,
        message_id: str | None = None,
    ) -> Row:
        attachment_id = new_id("ba")
        folder = self.attachment_root / session_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / attachment_id  # 文件名是自己生成的编号，不用上传的名字
        target.write_bytes(data)
        target.chmod(0o600)
        return self.store.add_attachment(
            session_id,
            direction,
            name,
            media_type,
            len(data),
            hashlib.sha256(data).hexdigest(),
            f"{session_id}/{attachment_id}",
            attachment_id=attachment_id,
            turn_id=turn_id,
            message_id=message_id,
        )

    def upload_attachment(self, session_id: str, name: str, data: bytes) -> Row:
        """收下一个要随下一条消息发出去的文件；发送时用返回的 id 带上。"""
        session = self._required_session(session_id)
        if session["status"] != "active":
            raise BridgeError("CONFLICT", "会话已归档，不能再发送")
        name = files.safe_name(name)
        media_type = files.input_type(name)
        if media_type is None:
            raise BridgeError(
                "VALIDATION_FAILED",
                "不支持这种文件：可以带图片（PNG / JPEG / GIF / WebP）、文本和代码、PDF、ZIP",
            )
        if not data:
            raise BridgeError("VALIDATION_FAILED", "文件是空的")
        if len(data) > files.MAX_BYTES:
            raise BridgeError("VALIDATION_FAILED", "文件太大（单个最多 20 MB）")
        if not files.looks_like(media_type, data):
            raise BridgeError("VALIDATION_FAILED", "文件内容和扩展名对不上")
        if session["mode"] != "dev" and not files.is_image(media_type):
            raise BridgeError(
                "VALIDATION_FAILED",
                "对话会话只能带图片；别的文件要在开发会话里用（模型在工作目录里读）",
            )
        pending = [
            a
            for a in self.store.list_attachments(session_id)
            if a["direction"] == "input" and a["turn_id"] is None
        ]
        if len(pending) >= files.MAX_PENDING_UPLOADS:
            raise BridgeError("CONFLICT", "这个会话里还没发出去的附件太多了，先发送或等一会儿")
        return attachment_view(self._put_attachment(session_id, "input", name, media_type, data))

    def discard_upload(self, session_id: str, attachment_id: str) -> None:
        """去掉一个上传了还没发出去的附件。"""
        row = self.store.get_attachment(attachment_id)
        if row is None or row["session_id"] != session_id:
            raise BridgeError("NOT_FOUND", "没有这个附件")
        if row["turn_id"] is not None or row["direction"] != "input":
            raise BridgeError("CONFLICT", "这个附件已经随消息发出去了")
        (self.attachment_root / row["path"]).unlink(missing_ok=True)
        self.store.delete_attachment(attachment_id)

    def _pending_attachments(self, session: Row, ids: list[str]) -> list[Row]:
        if len(ids) > files.MAX_PER_MESSAGE or len(set(ids)) != len(ids):
            raise BridgeError(
                "VALIDATION_FAILED", f"一条消息最多带 {files.MAX_PER_MESSAGE} 个附件，不能重复"
            )
        rows: list[Row] = []
        for attachment_id in ids:
            row = self.store.get_attachment(attachment_id)
            if row is None or row["session_id"] != session["id"] or row["direction"] != "input":
                raise BridgeError("VALIDATION_FAILED", "没有这个附件，或者它不是这个会话的")
            if row["turn_id"] is not None:
                raise BridgeError("CONFLICT", "这个附件已经随另一条消息发出去了")
            rows.append(row)
        return rows

    def _place_files(self, workdir: Path, inputs: list[Row]) -> list[str]:
        """把不是图片的附件放进工作目录的 attachments/ 下，返回相对路径。"""
        placed: list[str] = []
        for attachment in inputs:
            if files.is_image(attachment["media_type"]):
                continue
            folder = workdir / "attachments"
            folder.mkdir(parents=True, exist_ok=True)
            # 前面带上附件编号的后几位：同名的文件不互相覆盖
            target = folder / f"{attachment['id'][-6:]}-{attachment['name']}"
            shutil.copyfile(self.attachment_root / attachment["path"], target)
            placed.append(f"attachments/{target.name}")
        return placed

    def _collect_output(
        self, session: Row, turn_id: str, path: Path, *, image: bool = False
    ) -> None:
        """模型产出的文件收进附件：只收数据卷里（图片）或这个会话工作目录里的普通文件。"""
        root = self.settings.data_dir if image else Path(session["workdir"])
        try:
            real = (path if path.is_absolute() else root / path).resolve()
            if not real.is_relative_to(root.resolve()) or not real.is_file():
                return
            if real.is_relative_to(self.attachment_root.resolve()):
                return  # 附件目录里的不再收一遍
            if real.stat().st_size > files.MAX_BYTES:
                return
            data = real.read_bytes()
        except OSError:
            return
        media_type = files.output_type(real.name)
        if image and not (files.is_image(media_type) and files.looks_like(media_type, data)):
            return
        if len(self.store.turn_attachments(turn_id, "output")) >= files.MAX_OUTPUT_PER_TURN:
            return
        name = files.safe_name(real.name)
        message_id = self.store.add_message(
            session["id"], turn_id, "process", "image" if image else "file", name
        )
        self._put_attachment(
            session["id"], "output", name, media_type, data, turn_id=turn_id, message_id=message_id
        )
        if image:
            self.store.add_event(session["id"], turn_id, "image", {"name": name})

    def attachment_file(self, session_id: str, attachment_id: str) -> tuple[Row, Path]:
        row = self.store.get_attachment(attachment_id)
        if row is None or row["session_id"] != session_id:
            raise BridgeError("NOT_FOUND", "没有这个附件")
        path = self.attachment_root / row["path"]
        if not path.is_file():
            raise BridgeError("NOT_FOUND", "这个附件的文件已经不在了")
        return row, path

    async def _imported(self, session: Row, *, again: bool = False) -> list[Row]:
        """官方程序里已有的会话：第一次打开时向官方程序读一次全部内容存进 messages，之后读库。

        经这座桥发的回合在官方程序的记录里也有，它们已经按回合存着，导入时不重复存。
        """
        if session["origin"] != "external":
            return []
        session_id = session["id"]
        if again or session.get("imported_at") is None:
            try:
                native = await self._driver(session["slot"]).read(
                    session["native_id"], session["workdir"]
                )
            except DriverError as error:
                raise from_driver(error) from error
            known: Counter[tuple[str, str]] = Counter()
            for turn in self.store.list_turns(session_id=session_id, limit=10_000):
                known[("user", turn["text"])] += 1
                if turn["result_text"]:
                    known[("assistant", turn["result_text"])] += 1
            self.store.delete_imported_messages(session_id)
            now = time.time()
            for item in native:
                text = redact(item.text)
                if known[(item.role, text)] > 0:
                    known[(item.role, text)] -= 1
                    continue
                self.store.add_message(
                    session_id,
                    None,
                    item.role,
                    "text",
                    text,
                    delivery="imported",
                    at=item.at or now,
                )
            self.store.mark_imported(session_id, now)
        return self.store.list_messages(session_id, imported=True)

    async def reimport(self, session_id: str) -> Row:
        """重新向官方程序读一次这个会话的内容（官方程序里又有了新内容时用）。"""
        session = self._required_session(session_id)
        if session["origin"] != "external":
            raise BridgeError("CONFLICT", "只有官方程序里已有的会话才需要重新导入")
        await self._imported(session, again=True)
        return await self.conversation(session_id)

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
        # 官方程序里已有的内容（第一次打开时导入）排在前面，后面是经这座桥发的各个回合
        messages: list[Row] = [
            {"role": m["role"], "text": m["text"], "at": m["at"], "imported": True}
            for m in await self._imported(session)
        ]
        by_turn: dict[str, list[Row]] = {}
        for message in self.store.list_messages(session_id, imported=False):
            by_turn.setdefault(message["turn_id"], []).append(message)
        files: dict[tuple[str, str], list[Row]] = {}
        for attachment in self.store.list_attachments(session_id):
            if attachment["turn_id"]:
                key = (attachment["turn_id"], attachment["direction"])
                files.setdefault(key, []).append(attachment_view(attachment))
        for turn in self.store.list_turns(session_id=session_id, limit=10_000):
            mine = by_turn.get(turn["id"], [])
            asked = next((m for m in mine if m["role"] == "user"), None)
            messages.append(
                {
                    "role": "user",
                    "turn_id": turn["id"],
                    "text": turn["text"],
                    "at": turn["queued_at"],
                    "by": asked["by"] if asked else None,
                    "by_id": turn["caller_id"],
                    "delivery": asked["delivery"] if asked else None,
                    "attachments": files.get((turn["id"], "input"), []),
                }
            )
            # 回合中途插进来的消息：排在这一问之后、回答之前
            for extra in mine:
                if extra["role"] == "user" and asked is not None and extra["id"] != asked["id"]:
                    messages.append(
                        {
                            "role": "user",
                            "turn_id": turn["id"],
                            "text": extra["text"],
                            "at": extra["at"],
                            "by": extra["by"],
                            "by_id": (extra["data"] or {}).get("caller_id"),
                            "delivery": extra["delivery"],
                            "attachments": [],
                        }
                    )
            steps = [step for step in (_step(m) for m in mine) if step]
            status = turn["status"]
            reply: Row = {
                "role": "assistant",
                "turn_id": turn["id"],
                "status": status,
                "at": turn["finished_at"] or turn["started_at"] or turn["queued_at"],
                "text": "",
                "steps": steps,
                "attachments": files.get((turn["id"], "output"), []),
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
                if status in ("failed", "unknown", "interrupted"):
                    reply["failure"] = {
                        "kind": turn["error_kind"],
                        "message": turn["error_message"],
                    }
                    if status == "interrupted":
                        reply["resume"] = self._resume_view(turn)
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

    # ---- 额度与自动续跑（T-0237，docs/v2/LLM_BRIDGE_QUOTA_PLAN.md）----
    def _platforms_of(self, slot: str) -> list[str]:
        return [
            p
            for p in self.all_platforms()
            if any(c.slot == slot and c.platform == p for c in self.channels)
        ]

    def _save_quota(self, slot: str, windows: list[Row]) -> None:
        """同一个账号上的平台（ChatGPT 和 Codex）共用一份额度。"""
        clean = [w for w in windows if isinstance(w, dict) and w.get("kind")]
        for platform in self._platforms_of(slot):
            self.store.save_quota(platform, clean)

    def _resume_view(self, turn: Row) -> Row:
        return {
            "state": turn[
                "resume_state"
            ],  # pending / forced / done / cancelled / skipped / exhausted
            "at": turn["resume_at"],
            "attempt": turn["resume_attempt"],
            "resumed_by": turn["resumed_by"],
        }

    async def _wait_platform(self, platform: str) -> None:
        while True:
            pause = self.store.pauses().get(platform)
            remaining = pause["until"] - time.time() if pause else 0
            if remaining <= 0 and pause is None:
                return
            # 到点后由续跑的循环解除暂停（它先把“继续”排到队首），这里只等它
            event = self._pause_wake.setdefault(platform, asyncio.Event())
            event.clear()
            self._resume_wake.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), max(0.05, min(remaining, 30)))

    def _interrupt(self, turn: Row, session: Row, error: DriverError) -> None:
        """这个回合是被平台的额度打断的：记成中断，暂停这个平台，到重置时间后续跑。"""
        now = time.time()
        resets = error.resets_at or quota.parse_reset(error.message)
        estimated = not resets
        if not resets:
            resets = (
                now + self.settings.quota_fallback_seconds
            )  # 平台没说什么时候重置：按一个窗口估
        until = max(resets, now) + self.settings.resume_margin_seconds
        attempt = int(turn["resume_attempt"] or 0)
        state = "pending" if attempt < self.setting("auto_resume_max") else "exhausted"
        platform = session["platform"]
        current = self.store.pauses().get(platform)
        self.store.set_pause(
            platform,
            max(until, current["until"]) if current else until,
            estimated,
            redact(error.message)[:200],
        )
        self.store.add_event(
            session["id"],
            turn["id"],
            "interrupted",
            {"kind": "quota_exceeded", "resume_at": until, "estimated": estimated, "state": state},
        )
        self.store.update_turn(turn["id"], resume_at=until, resume_state=state)
        self._finish(
            turn["id"],
            status="interrupted",
            error_kind="quota_exceeded",
            error_message=redact(error.message),
        )
        self._resume_wake.set()

    async def _resume_loop(self) -> None:
        """到重置时间后：解除平台的暂停，给被打断的会话各发一句“继续”。"""
        while True:
            try:
                now = time.time()
                for platform, pause in self.store.pauses().items():
                    if pause["until"] <= now:
                        await self._resume_platform(platform)
                waits = [p["until"] - time.time() for p in self.store.pauses().values()]
                timeout = min(60.0, max(0.05, min(waits))) if waits else 60.0
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("quota resume loop failed")
                timeout = 30.0
            self._resume_wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._resume_wake.wait(), timeout)

    async def _resume_platform(self, platform: str) -> None:
        channel = next((c for c in self.channels if c.platform == platform), None)
        driver = self.drivers.get(channel.slot) if channel else None
        if driver is not None and channel is not None:
            # 能随时查额度的平台：先确认真的回来了，没回来就按新的重置时间接着等
            windows: list[Row] | None = None
            with contextlib.suppress(DriverError):
                windows = await driver.quota()
            if windows:
                self._save_quota(channel.slot, windows)
                later = [
                    w["resets_at"]
                    for w in windows
                    if w.get("status") == "exceeded" and (w.get("resets_at") or 0) > time.time()
                ]
                if later:
                    self.store.set_pause(
                        platform,
                        min(later) + self.settings.resume_margin_seconds,
                        False,
                        "额度还没有重置",
                    )
                    return
        pending = self.store.pending_resumes(platform)
        for turn in pending:
            self._auto_resume(turn)
        self.store.clear_pause(platform)
        event = self._pause_wake.get(platform)
        if event is not None:
            event.set()

    def _auto_resume(self, turn: Row) -> None:
        session = self.store.get_session(turn["session_id"])
        forced = turn["resume_state"] == "forced"
        reason = ""
        if session is None or session["status"] != "active":
            reason = "会话已归档"
        elif not self.platform_enabled(session["platform"]):
            reason = "这个平台已经不由这座桥控制"
        elif not forced and not self.setting("auto_resume"):
            reason = "没有打开“额度用完后自动继续”"
        if reason or session is None:
            self.store.update_turn(turn["id"], resume_state="skipped")
            if session is not None:
                self.store.add_event(
                    session["id"], turn["id"], "resume_skipped", {"reason": reason}
                )
            return
        attempt = int(turn["resume_attempt"] or 0) + 1
        new = self.store.create_turn(
            session["id"],
            SYSTEM_CALLER,
            f"resume:{turn['id']}:{attempt}",
            self.setting("resume_text"),
            by=SYSTEM_LABEL,
        )
        edge = self.store.queue_edge(session["id"], first=True)
        self.store.update_turn(
            new["id"],
            position=(edge if edge is not None else new["position"])
            - 1,  # 排在这个会话队列的最前面
            resume_attempt=attempt,
            resumes_turn_id=turn["id"],
        )
        self.store.update_turn(turn["id"], resume_state="done", resumed_by=new["id"])
        self.store.add_event(
            session["id"],
            new["id"],
            "queued",
            {"by": SYSTEM_LABEL, "auto_resume": True, "resumes": turn["id"], "attempt": attempt},
        )
        self._enqueue(self.store.get_turn(new["id"]) or new)

    def _interrupted_turn(self, turn_id: str) -> tuple[Row, Row]:
        turn = self.store.get_turn(turn_id)
        if turn is None:
            raise BridgeError("NOT_FOUND", "没有这个回合")
        if turn["status"] != "interrupted" or turn["error_kind"] != "quota_exceeded":
            raise BridgeError("CONFLICT", "这个回合不是被额度打断的")
        if turn["resume_state"] == "done":
            raise BridgeError("CONFLICT", "这个回合已经续跑过了")
        return turn, self._required_session(turn["session_id"])

    def resume_now(self, turn_id: str) -> Row:
        """不等重置时间，现在就试着续跑（也用于“自动继续”关着、或已经超过次数的时候）。"""
        turn, session = self._interrupted_turn(turn_id)
        self.store.update_turn(turn_id, resume_state="forced", resume_at=time.time())
        self.store.set_pause(session["platform"], time.time(), False, "手动：现在就试")
        self._resume_wake.set()
        return self.turn_view(self.store.get_turn(turn_id) or turn)

    def cancel_resume(self, turn_id: str) -> Row:
        turn, _ = self._interrupted_turn(turn_id)
        self.store.update_turn(turn_id, resume_state="cancelled")
        return self.turn_view(self.store.get_turn(turn_id) or turn)

    async def quota_view(self, refresh: bool = False) -> Row:
        """各平台的额度：每个窗口已用多少、什么时候重置、什么时候看到的；以及是否因为额度暂停着。"""
        if refresh:
            for slot, driver in self.drivers.items():
                windows: list[Row] | None = None
                with contextlib.suppress(DriverError):
                    windows = await driver.quota()
                if windows:
                    self._save_quota(slot, windows)
        now = time.time()
        pauses, tokens = self.store.pauses(), self.store.tokens_since(now - 5 * 3600)
        rows: dict[str, list[Row]] = {}
        for row in self.store.quota_windows():
            passed = bool(row["resets_at"]) and row["resets_at"] <= now
            rows.setdefault(row["platform"], []).append(
                {
                    "kind": row["kind"],
                    # 重置时间已经过了：旧的百分比不再算数，等下一次回合确认
                    "used_percent": None if passed else row["used_percent"],
                    "resets_at": row["resets_at"],
                    "reset_passed": passed,
                    "status": "ok" if passed else row["status"],
                    "plan": row["plan"],
                    "observed_at": row["observed_at"],
                }
            )
        out: Row = {}
        for platform in self.platforms():
            channel = next((c for c in self.channels if c.platform == platform), None)
            owner = self.drivers.get(channel.slot) if channel else None
            pause = pauses.get(platform)
            out[platform] = {
                "windows": rows.get(platform, []),
                "can_refresh": bool(getattr(owner, "can_query_quota", False)),
                "paused_until": pause["until"] if pause else None,
                "pause_estimated": bool(pause["estimated"]) if pause else False,
                "waiting_resume": len(self.store.pending_resumes(platform)),
                "tokens_5h": tokens.get(platform, 0),
            }
        return {"platforms": out}

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
        asked = self.store.user_message(turn_id)
        if asked is not None:
            self.store.update_message_text(asked["id"], text)
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
            # 各平台的额度与因为额度暂停的情况（T-0237）
            "quota": (await self.quota_view())["platforms"],
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


def _step(message: Row) -> Row | None:
    """回合里的一行内容 → 页面“过程”里的一步；提问不算。"""
    if message["role"] == "user":
        return None
    kind = "message" if message["role"] == "assistant" else message["kind"]
    step: Row = {"kind": kind, "text": message["text"], "at": message["at"]}
    data = message["data"] or {}
    for key in ("name", "truncated", "error", "exit_code"):
        if data.get(key) not in (None, False, ""):
            step[key] = data[key]
    return step


def attachment_view(attachment: Row) -> Row:
    return {
        "id": attachment["id"],
        "name": attachment["name"],
        "media_type": attachment["media_type"],
        "size": attachment["size"],
        "direction": attachment["direction"],
        "at": attachment["at"],
    }


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
