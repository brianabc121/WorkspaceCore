"""Claude Code 与 CodeBuddy Code共用的驱动：每个回合起一次官方命令行，

`<程序> -p --output-format stream-json --verbose [--session-id <id> | --resume <id>]`，
消息从标准输入给，逐行读 JSON 事件。两家的参数与事件格式相同（claude 2.1.323、
codebuddy 2.184.0 上核验，见 docs/decisions/2026-11-13-llm-bridge-verification.md 第 3、4 节）。

- 会话号由桥生成（UUID）；官方程序里还没有这个会话时用 --session-id，已有时用 --resume；
- 官方程序在自己的配置目录里保存会话记录，会话列表和内容读的就是那些文件；
- 子进程的环境变量只给白名单里的几项加上登录凭据，不继承桥自己的环境。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge import quota
from bridge.drivers.base import (
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
)
from bridge.drivers.web_login import WebLogin
from bridge.redact import redact

_ENV_PASS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
)
_AUTH_HINTS = (
    "authentication required",
    "/login",
    "invalid api key",
    "authentication_error",
    "oauth",
)
_RATE_HINTS = ("rate limit", "usage limit", "429", "overloaded", "quota")
# 对话模式不给工具，但官方程序自带的提示仍把模型说成能动手的编程助手：
# 不说明的话，模型会把“调用工具”当成文字写出来，人以为它在执行、其实什么都没发生。
CHAT_NOTICE = (
    "This session is a plain chat with no tools: you cannot run commands, read or write files, "
    "browse, or inspect the machine. Never write tool calls or pretend to run anything. "
    "If the user asks for something that needs it, say plainly, in the user's language, that "
    "this is a chat-mode session that cannot execute code or access files, and that they can "
    "start a new session in dev mode (with a development environment) for that."
)
_READ_TOOLS = "Read,Glob,Grep"
_WRITE_TOOLS = "Read,Glob,Grep,Edit,Write"
_MAX_SCAN_BYTES = 512 * 1024


@dataclass(frozen=True)
class CliProfile:
    slot: str
    label: str
    binary: str
    config_env: str  # 指向配置目录的环境变量名
    secret_envs: dict[str, str]  # 登录方式 → 注入凭据用的环境变量名
    models: tuple[ModelInfo, ...]
    auth_cli: bool  # 有没有 `auth status` / `auth logout` 子命令
    models_from_help: bool = False
    # 网页登录（bridge/drivers/web_login.py）：页面上的登录方式 → 官方程序本机服务里的登录方式
    web_login: dict[str, str] = field(default_factory=dict)
    # 回合输出里带不带额度（已用百分比、重置时间）。CodeBuddy 2.184.0 的 stream-json 输出里没有
    reports_quota: bool = True
    # 能不能用“容器里已有的登录”（要进容器操作官方程序；有网页登录的平台不再提供）
    allow_existing: bool = True


CLAUDE_MODELS = (
    ModelInfo("sonnet", "Claude Sonnet（最新）", "别名：指向最新的 Sonnet", default=True),
    ModelInfo("opus", "Claude Opus（最新）", "别名：指向最新的 Opus"),
    ModelInfo("haiku", "Claude Haiku（最新）", "别名：指向最新的 Haiku"),
)


def claude_profile(binary: str) -> CliProfile:
    return CliProfile(
        slot="claude",
        label="Claude",
        binary=binary,
        config_env="CLAUDE_CONFIG_DIR",
        secret_envs={"api_key": "ANTHROPIC_API_KEY", "oauth_token": "CLAUDE_CODE_OAUTH_TOKEN"},
        models=CLAUDE_MODELS,
        auth_cli=True,
    )


def codebuddy_profile(binary: str) -> CliProfile:
    return CliProfile(
        slot="workbuddy",
        label="CodeBuddy",
        binary=binary,
        config_env="CODEBUDDY_CONFIG_DIR",
        secret_envs={"api_key": "CODEBUDDY_API_KEY"},
        models=(ModelInfo("auto", "自动", "由 CodeBuddy 选择模型", default=True),),
        auth_cli=False,
        models_from_help=True,
        # 网页登录：国内站 / 国际站（官方程序本机服务里叫 internal / external）
        web_login={"web_cn": "internal", "web_intl": "external"},
        allow_existing=False,
        reports_quota=False,
    )


_TURN_USAGE = "codebuddy.ai/turnUsage"


def _turn_usage(result: dict[str, Any]) -> dict[str, Any]:
    """回合结果里的用量。

    CodeBuddy Code 的 `result.usage` 是整个会话累计的 token；这一回合自己的在
    `result._meta["codebuddy.ai/turnUsage"].total` 里，连同这一回合消耗的积分
    （官方文档 stream-json-turn-usage，2.184.0 核验）。有它就用它；没有（Claude Code、
    旧版本）用 `result.usage`。积分只取官方报出来的数，没报就不写，不拿 token 折算。
    """
    raw = result.get("usage")
    usage: dict[str, Any] = raw if isinstance(raw, dict) else {}
    meta = result.get("_meta")
    turn = meta.get(_TURN_USAGE) if isinstance(meta, dict) else None
    total = turn.get("total") if isinstance(turn, dict) else None
    out: dict[str, Any] = {}
    if isinstance(total, dict):
        if isinstance(total.get("usage"), dict):
            usage = total["usage"]
        credits = total.get("credits")
        if (
            isinstance(credits, int | float)
            and not isinstance(credits, bool)
            and math.isfinite(credits)
            and credits >= 0
        ):
            out["credits"] = round(float(credits), 2)
        status = total.get("creditStatus")
        if status in ("known", "partial", "unknown"):
            out["credit_status"] = status
    out["input_tokens"] = int(usage.get("input_tokens") or 0)
    out["output_tokens"] = int(usage.get("output_tokens") or 0)
    return out


_CONTEXT_USED = "codebuddy.ai/contextUsed"
_CONTEXT_WINDOW = "codebuddy.ai/contextWindow"


def _message_context(message: Any) -> int | None:
    """一次模型调用的 usage → 这时上下文里有多少 token（输入 + 缓存读写 + 输出）。

    Claude Code 2.1.323 的 SessionStartHookInput 就是这样算“上一次回答的窗口”的
    （input + cache_read + cache_creation + output，Agent SDK 0.3.323 的 sdk.d.ts）。"""
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    parts = [
        positive_int(usage.get(key)) or 0
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    ]
    return sum(parts) or None


def _result_context(result: dict[str, Any], model: str | None, used: int | None) -> ContextUsage:
    """回合结果里的上下文：用了多少、窗口多大。

    - CodeBuddy Code：`result._meta["codebuddy.ai/contextUsed"]`、`["codebuddy.ai/contextWindow"]`
      （官方文档 stream-json-turn-usage 第 9 节：“当前上下文占用快照”，2.184.0）；
    - Claude Code：窗口在 `result.modelUsage[<模型>].contextWindow`
      （Agent SDK 0.3.323 的 ModelUsage），用了多少取这一回合最后一次模型调用的 usage
      （used，由调用方算好）。"""
    meta = result.get("_meta")
    if isinstance(meta, dict):
        reported_used = positive_int(meta.get(_CONTEXT_USED))
        reported_window = positive_int(meta.get(_CONTEXT_WINDOW))
        if reported_used or reported_window:
            return ContextUsage(reported_used or used, reported_window)
    window = None
    models = result.get("modelUsage")
    if isinstance(models, dict):
        windows = {
            name: positive_int(item.get("contextWindow"))
            for name, item in models.items()
            if isinstance(item, dict)
        }
        window = windows.get(model or "") or max((w for w in windows.values() if w), default=None)
    return ContextUsage(used, window)


def _compaction(event: dict[str, Any]) -> dict[str, Any]:
    """Claude Code 的 compact_boundary 事件 → 压缩记录（触发方式、压缩前后的 token 数）。

    字段见 Agent SDK 0.3.323 的 SDKCompactBoundaryMessage：
    compact_metadata.trigger（manual / auto）、pre_tokens、post_tokens（可能没有）。"""
    meta = event.get("compact_metadata")
    meta = meta if isinstance(meta, dict) else {}
    trigger = meta.get("trigger")
    return {
        "trigger": trigger if trigger in ("manual", "auto") else None,
        "pre_tokens": positive_int(meta.get("pre_tokens")),
        "post_tokens": positive_int(meta.get("post_tokens")),
    }


def _error_kind(text: str) -> ErrorKind:
    low = text.lower()
    if any(hint in low for hint in _AUTH_HINTS):
        return "not_logged_in"
    if any(hint in low for hint in _RATE_HINTS):
        return "rate_limited"
    return "platform_error"


def _text_delta(event: dict[str, Any]) -> str:
    """stream_event 里的一小段回答文字；别的增量（思考、工具参数）和子代理的不要。"""
    if event.get("parent_tool_use_id"):
        return ""
    inner = event.get("event")
    if not isinstance(inner, dict) or inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def _block_text(content: Any) -> str:
    """消息内容 → 文字：字符串，或带 text 的块列表（text / input_text / output_text）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
            and block.get("type") in ("text", "input_text", "output_text")
        ]
        return "".join(parts)
    return ""


def parse_transcript_line(record: dict[str, Any]) -> Message | None:
    """会话记录里的一行 → 一条消息（两家格式不同）；其他类型的行返回 None。"""
    kind = record.get("type")
    at = record.get("timestamp")
    stamp = at / 1000 if isinstance(at, int | float) else None
    if kind in ("user", "assistant") and isinstance(record.get("message"), dict):  # Claude
        if record.get("isSidechain") or record.get("isMeta"):
            return None
        text = _block_text(record["message"].get("content"))
        return Message(kind, text, stamp) if text.strip() else None
    if kind == "message" and record.get("role") in ("user", "assistant"):  # CodeBuddy
        text = _block_text(record.get("content"))
        return Message(record["role"], text, stamp) if text.strip() else None
    return None


@dataclass
class _Seen:
    """跑一个回合时一路记下的：最后一次模型调用后的上下文、主模型、压缩。"""

    used: int | None = None
    model: str | None = None
    compacting: bool = False
    compactions: int = 0


class CliStreamDriver(Driver):
    @property
    def reports_quota(self) -> bool:  # type: ignore[override]
        return self.profile.reports_quota

    def __init__(
        self,
        profile: CliProfile,
        config_dir: Path,
        secrets_dir: Path,
        *,
        rpc_timeout: float = 30.0,
    ) -> None:
        self.profile = profile
        self.slot = profile.slot
        self.login_methods = (
            *profile.web_login,
            *profile.secret_envs,
            *(("existing",) if profile.allow_existing else ()),
        )
        self._web = WebLogin(profile.binary, profile.label) if profile.web_login else None
        self._config_dir = config_dir
        self._secret_file = secrets_dir / f"{profile.slot}.json"
        self._timeout = rpc_timeout
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # 工具调用的编号 → 工具名：结果报回来时知道是哪个工具的
        self._tool_names: dict[str, str] = {}
        self._help_models: list[ModelInfo] | None = None

    def supports(self, mode: Mode) -> bool:
        return mode in ("chat", "dev")

    # ---- 凭据 ----
    def _secret(self) -> dict[str, str]:
        try:
            data = json.loads(self._secret_file.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: os.environ[k] for k in _ENV_PASS if k in os.environ}
        self._config_dir.mkdir(parents=True, exist_ok=True)
        env[self.profile.config_env] = str(self._config_dir)
        env["HOME"] = str(self._config_dir.parent)
        secret = self._secret()
        name = self.profile.secret_envs.get(secret.get("method", ""))
        if name and secret.get("value"):
            env[name] = secret["value"]
        env.update(extra or {})
        return env

    def _save_secret(self, data: dict[str, str]) -> None:
        self._secret_file.parent.mkdir(parents=True, exist_ok=True)
        self._secret_file.parent.chmod(0o700)
        self._secret_file.write_text(json.dumps(data))
        self._secret_file.chmod(0o600)

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        if self._web is not None and method in self.profile.web_login:
            # 网页登录：给出网址，人在自己的浏览器里登录；登录成功后才记下“已登录”
            def done(account: str | None) -> None:
                self._save_secret({"method": method, **({"identity": account} if account else {})})

            with contextlib.suppress(FileNotFoundError):
                self._secret_file.unlink()  # 重新登录期间不算已登录
            env = self._env()
            env.pop("CODEBUDDY_API_KEY", None)
            return await self._web.begin(self.profile.web_login[method], env, done)
        if self._web is not None:
            await self._web.stop()
            self._web.challenge = None
        if method == "existing" and self.profile.allow_existing:
            data = {"method": "existing"}
        elif method in self.profile.secret_envs:
            value = (payload.get(method) or payload.get("value") or "").strip()
            if not value:
                raise DriverError("invalid", f"缺少 {method}")
            data = {"method": method, "value": value}
        else:
            raise DriverError("not_supported", f"{self.profile.label} 不支持登录方式 {method}")
        self._save_secret(data)
        return LoginChallenge(state="done")

    async def login_state(self) -> LoginChallenge | None:
        return self._web.challenge if self._web is not None else None

    async def stop(self) -> None:
        if self._web is not None:
            await self._web.stop()

    async def logout(self) -> None:
        was_web = self._secret().get("method") in self.profile.web_login
        with contextlib.suppress(FileNotFoundError):
            self._secret_file.unlink()
        if self._web is not None and (was_web or self._web.challenge is not None):
            await self._web.logout(self._env())
        if self.profile.auth_cli:
            with contextlib.suppress(DriverError):
                await self._run_short(["auth", "logout"])

    async def _run_short(self, args: list[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.profile.binary,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._env(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
        except OSError as error:
            raise DriverError("unavailable", f"启动 {self.profile.binary} 失败：{error}") from error
        except TimeoutError as error:
            proc.kill()
            raise DriverError("timeout", f"{self.profile.binary} {' '.join(args)} 超时") from error
        return out.decode(errors="replace")

    async def status(self) -> AccountStatus:
        # 账号标识只用官方程序自己报告的（例如邮箱）。用 API 密钥或令牌登录时看不到是哪个账号：
        # 不拿密钥的指纹充当账号，否则换一把密钥会被当成换账号
        secret = self._secret()
        method = secret.get("method")
        if self.profile.auth_cli:
            try:
                data = json.loads(await self._run_short(["auth", "status"]))
            except ValueError:
                data = {}
            if data.get("loggedIn"):
                reported = str(data.get("authMethod") or method or "")
                return AccountStatus(True, data.get("email") or None, reported)
            if secret.get("value"):
                # 配置了密钥或令牌、官方程序却没有确认：不拦在这里，发送时才知道凭据是否有效
                return AccountStatus(
                    True, None, method, "官方程序没有确认这份凭据，发送时才能知道是否有效"
                )
            return AccountStatus(logged_in=False, detail="官方程序报告未登录")
        if method is None:
            return AccountStatus(logged_in=False, detail="还没有配置登录方式")
        if method in self.profile.web_login:
            # 网页登录：账号名是登录成功时官方程序报告的
            return AccountStatus(True, secret.get("identity") or None, method)
        detail = "这个平台没有查询登录状态的命令，发送时才能确认凭据是否有效"
        return AccountStatus(True, None, method, detail)

    async def models(self, mode: Mode) -> list[ModelInfo]:
        if not self.profile.models_from_help:
            return list(self.profile.models)
        if self._help_models is None:
            found = list(self.profile.models)
            with contextlib.suppress(DriverError):
                match = re.search(
                    r"Currently supported: \(([^)]*)\)", await self._run_short(["--help"])
                )
                if match:
                    ids = [m.strip() for m in match.group(1).split(",") if m.strip()]
                    found = [ModelInfo(i, i, default=(i == "auto")) for i in ids]
            self._help_models = found
        return list(self._help_models)

    # ---- 会话 ----
    async def create(self, spec: SessionSpec) -> str:
        return str(uuid.uuid4())

    def _args(self, spec: SessionSpec) -> list[str]:
        # --include-partial-messages：逐字的增量（stream_event），聊天页据此边写边显示。
        # 在锁定版本的帮助文本和类型定义里核对过：Claude Code 2.1.323、CodeBuddy Code 2.184.0
        args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        assert spec.native_id is not None  # noqa: S101
        # 官方程序里已经有这个会话的记录就续接；按文件判断，不按回合数
        # （失败的回合也可能已经建了会话）
        resume = self._find_transcript(spec.native_id) is not None
        args += ["--resume" if resume else "--session-id", spec.native_id]
        if spec.model:
            args += ["--model", spec.model]
        if spec.mode == "chat":
            args += ["--tools", "", "--append-system-prompt", CHAT_NOTICE]
        elif spec.sandbox == "read_only":
            args += ["--tools", _READ_TOOLS]
        elif spec.sandbox == "workspace_write":
            args += ["--tools", _WRITE_TOOLS, "--permission-mode", "acceptEdits"]
        else:  # exec：管理员在环境上明确打开的档位
            args += ["--permission-mode", "bypassPermissions"]
        if spec.images:  # 带图片时提问按一条 stream-json 的用户消息从标准输入给
            args += ["--input-format", "stream-json"]
        return args

    @staticmethod
    def _stdin(spec: SessionSpec, text: str) -> bytes:
        """标准输入的内容：没有图片就是提问本身；有图片是一条带 image 内容块的用户消息。"""
        if not spec.images:
            return text.encode()
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for path, media_type in spec.images:
            data = base64.b64encode(Path(path).read_bytes()).decode()
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            )
        message = {"type": "user", "message": {"role": "user", "content": content}}
        return (json.dumps(message, ensure_ascii=False) + "\n").encode()

    async def run_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
        return await self._turn(spec, text, sink, manual=False)

    async def _turn(
        self, spec: SessionSpec, text: str, sink: EventSink, *, manual: bool
    ) -> TurnOutcome:
        """起一次官方命令行跑一个回合。manual：这一回合是手动压缩（发的是 /compact）。"""
        if not spec.native_id:
            raise DriverError("invalid", "会话没有会话号")
        spec.workdir.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                self.profile.binary,
                *self._args(spec),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(spec.workdir),
                env=self._env(spec.env),
                limit=16 * 1024 * 1024,
            )
        except OSError as error:
            raise DriverError("unavailable", f"启动 {self.profile.binary} 失败：{error}") from error
        self._procs[spec.native_id] = proc
        try:
            assert proc.stdin and proc.stdout and proc.stderr  # noqa: S101
            proc.stdin.write(self._stdin(spec, text))
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(proc.stderr.read())
            outcome = TurnOutcome(text="", native_turn_id=None)
            messages: list[str] = []
            limited: list[dict[str, Any]] = []
            result: dict[str, Any] | None = None
            seen = _Seen()
            while line := await proc.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "stream_event":
                    delta = _text_delta(event)
                    if delta:
                        await sink("delta", {"text": delta})
                elif event.get("type") == "system":
                    await self._on_system(event, seen, sink, manual=manual)
                elif event.get("type") == "assistant":
                    if not event.get("parent_tool_use_id"):  # 子代理的调用不算主会话的上下文
                        message = event.get("message")
                        seen.used = _message_context(message) or seen.used
                        if isinstance(message, dict) and isinstance(message.get("model"), str):
                            seen.model = message["model"]
                    await self._on_assistant(event, messages, outcome, sink)
                elif event.get("type") == "user":
                    await self._on_tool_results(event, sink)
                elif event.get("type") == "rate_limit_event":
                    # 官方程序顺带报出的额度：交给 engine 记下来；被拒绝时记住重置时间
                    windows = quota.from_rate_limit_event(event)
                    if windows:
                        await sink("quota", {"windows": windows})
                        limited += [w for w in windows if w["status"] == "exceeded"]
                elif event.get("type") == "result":
                    result = event
            await proc.wait()
            stderr = (await stderr_task).decode(errors="replace")
            if result is None:
                tail = redact(stderr.strip()[-300:]) or f"退出码 {proc.returncode}"
                raise DriverError(_error_kind(tail), f"{self.profile.label} 没有返回结果：{tail}")
            if seen.compacting:  # 报了“正在压缩”却没等到结束：回合结束时也算压缩过一次
                await self._compacted(seen, sink, {"trigger": "manual" if manual else "auto"})
            outcome.context = _result_context(result, seen.model, seen.used)
            return self._finish(result, messages, outcome, limited)
        finally:
            self._procs.pop(spec.native_id, None)
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()

    async def _on_system(
        self, event: dict[str, Any], seen: _Seen, sink: EventSink, *, manual: bool
    ) -> None:
        """官方程序的 system 事件里和压缩有关的两种。

        - Claude Code：`subtype: compact_boundary`，带触发方式和压缩前后的 token 数；
        - CodeBuddy Code：只有状态事件 `subtype: status`，`status: "compacting"` 表示开始压缩，
          之后 `status: null` 表示结束（2.184.0 程序文件里的 StreamJsonView），没有前后的 token 数。
        两种都报的话（以后的版本）只算一次。"""
        subtype = event.get("subtype")
        if subtype == "compact_boundary":
            seen.compacting = False
            await self._compacted(seen, sink, _compaction(event))
        elif subtype == "status":
            if event.get("status") == "compacting":
                seen.compacting = True
            elif seen.compacting:
                seen.compacting = False
                await self._compacted(seen, sink, {"trigger": "manual" if manual else "auto"})

    @staticmethod
    async def _compacted(seen: _Seen, sink: EventSink, info: dict[str, Any]) -> None:
        seen.compactions += 1
        if info.get("post_tokens"):
            seen.used = info["post_tokens"]
        await sink("compacted", info)

    async def _on_assistant(
        self, event: dict[str, Any], messages: list[str], outcome: TurnOutcome, sink: EventSink
    ) -> None:
        content = (event.get("message") or {}).get("content")
        text = _block_text(content)
        if text.strip():
            messages.append(text)
            await sink("message", {"text": text})
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            raw = block.get("input")
            data: dict[str, Any] = raw if isinstance(raw, dict) else {}
            name = str(block.get("name") or "")
            if name == "Bash" and data.get("command"):
                outcome.commands.append(str(data["command"])[:500])
                await sink("command", {"command": outcome.commands[-1]})
            elif name in ("Edit", "Write") and data.get("file_path"):
                outcome.files.append(str(data["file_path"]))
                await sink("files", {"paths": [outcome.files[-1]]})
            elif name:  # 读文件、搜索和别的工具调用：也记进过程
                if block.get("id"):
                    self._tool_names[str(block["id"])] = name
                await sink("tool", {"name": name, "input": json.dumps(data, ensure_ascii=False)})

    async def _on_tool_results(self, event: dict[str, Any], sink: EventSink) -> None:
        """官方程序把工具结果作为一条 user 消息报出来：每个 tool_result 内容块记一步。"""
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            name = self._tool_names.pop(str(block.get("tool_use_id")), None)
            if name is None:  # 命令、改文件的结果不单独记（已经有“命令”“改动的文件”两步）
                continue
            await sink(
                "tool_result",
                {
                    "name": name,
                    "text": _block_text(block.get("content")),
                    "error": bool(block.get("is_error")),
                },
            )

    def _finish(
        self,
        result: dict[str, Any],
        messages: list[str],
        outcome: TurnOutcome,
        limited: list[dict[str, Any]] | None = None,
    ) -> TurnOutcome:
        if result.get("is_error"):
            errors = result.get("errors")
            text = str(
                result.get("result") or (errors[0] if isinstance(errors, list) and errors else "")
            )
            text = (
                text
                or (messages[-1] if messages else "")
                or str(result.get("subtype") or "回合失败")
            )
            # 额度用完：回合里出现过“被拒绝”的额度事件，或者失败信息是官方程序的额度用语
            if limited or quota.is_quota_text(text):
                resets = [w["resets_at"] for w in limited or [] if w["resets_at"]]
                raise DriverError(
                    "rate_limited",
                    redact(text)[:500],
                    quota=True,
                    resets_at=min(resets) if resets else quota.parse_reset(text),
                )
            raise DriverError(_error_kind(text), redact(text)[:500])
        outcome.usage = _turn_usage(result)
        final = result.get("result")
        outcome.text = (
            final if isinstance(final, str) and final else (messages[-1] if messages else "")
        )
        return outcome

    async def cancel(self, spec: SessionSpec) -> None:
        proc = self._procs.get(spec.native_id or "")
        if proc is not None and proc.returncode is None:
            proc.terminate()

    # ---- 会话记录（官方程序自己保存的文件） ----
    def _transcripts(self) -> list[Path]:
        root = self._config_dir / "projects"
        return [p for p in root.glob("*/*.jsonl") if p.is_file()] if root.is_dir() else []

    @staticmethod
    def _scan(path: Path) -> tuple[str, str | None, list[Message]]:
        """读一个会话文件：标题（第一条用户消息）、工作目录、消息。"""
        title, cwd, messages = "", None, []
        with path.open("rb") as handle:
            for raw in handle.read(_MAX_SCAN_BYTES).splitlines():
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                cwd = cwd or record.get("cwd")
                message = parse_transcript_line(record)
                if message is not None:
                    messages.append(message)
                    if not title and message.role == "user":
                        title = " ".join(message.text.split())[:120]
        return title, cwd, messages

    async def list_native(self, limit: int) -> list[NativeSession]:
        def collect() -> list[NativeSession]:
            files = sorted(self._transcripts(), key=lambda p: p.stat().st_mtime, reverse=True)[
                :limit
            ]
            out = []
            for path in files:
                title, cwd, _ = self._scan(path)
                stat = path.stat()
                out.append(NativeSession(path.stem, title, None, cwd, None, stat.st_mtime))
            return out

        return await asyncio.to_thread(collect)

    def _find_transcript(self, native_id: str) -> Path | None:
        if not re.fullmatch(r"[\w.\-:]{1,100}", native_id):
            return None
        root = self._config_dir / "projects"
        return next(iter(root.glob(f"*/{native_id}.jsonl")), None) if root.is_dir() else None

    async def read(self, native_id: str, workdir: str | None) -> list[Message]:
        def collect() -> list[Message]:
            path = self._find_transcript(native_id)
            return self._scan(path)[2] if path is not None else []

        return await asyncio.to_thread(collect)
