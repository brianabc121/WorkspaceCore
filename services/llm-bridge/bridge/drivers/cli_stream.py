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
import sys
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bridge import hooks, quota, remote
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
)
from bridge.drivers.web_login import WebLogin
from bridge.redact import mask_identity, redact

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
# 后台任务的事件（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.7）：每种事件的状态
_TASK_EVENTS = {
    "task_started": "running",
    "task_progress": "running",
    "task_updated": "",
    "task_notification": "",
}
_TASK_STATUS = {"completed", "failed", "stopped", "killed", "running", "pending"}


def _task_event(subtype: str, event: dict[str, Any]) -> dict[str, Any] | None:
    """官方程序报的一条后台任务事件 → 管理器记下的一步：编号、状态、说明、摘要。"""
    task_id = str(event.get("task_id") or "")
    if not task_id:
        return None
    raw = event.get("patch")
    patch: dict[str, Any] = raw if isinstance(raw, dict) else {}
    status = _TASK_EVENTS[subtype] or str(event.get("status") or patch.get("status") or "")
    out: dict[str, Any] = {"task_id": task_id[:100], "event": subtype}
    if status in _TASK_STATUS:
        out["status"] = status
    description = event.get("description") or patch.get("description")
    if isinstance(description, str) and description.strip():
        out["description"] = description.strip()[:300]
    summary = event.get("summary")
    if isinstance(summary, str) and summary.strip():
        out["summary"] = summary.strip()[:500]
    for key in ("task_type", "last_tool_name"):
        if isinstance(event.get(key), str) and event[key]:
            out[key] = str(event[key])[:60]
    return out


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
    # 自动压缩触发点（上下文的百分比）的环境变量（核验记录第 1、2 节）
    autocompact_env: str = ""
    # 推理强度的参数（只有核对过的官方程序有）：Claude Code 2.1.323 是 --effort <level>
    effort_flag: str = ""
    # 网页授权登录（T-0287）：管理器替人运行官方程序的 `auth login`，人在自己的浏览器里登录授权，
    # 再把授权页显示的授权码交回来。只有 Claude 有
    code_login: bool = False
    # 关掉官方程序自带的后台任务的环境变量（LONGRUN 5.11）：能开管理器的后台作业时设成 1。
    # Claude Code 2.1.323、CodeBuddy Code 2.184.0 的程序文件里核对过变量名
    background_off_env: str = ""


# 网页授权登录（Claude，T-0287）。在 Claude Code 2.1.323 上核实
# （读安装包源码、实际运行到给出网址）：
# 没有终端时 `claude auth login --claudeai` 在标准输出依次写
#   “Opening browser to sign in…”
#   “If the browser didn't open, visit: <网址>”
#   “Paste code here if prompted > ”
# 然后从标准输入一行一行读授权码（授权页显示的 code#state；没有 # 就报 “Invalid code…”
# 并接着等）。
# 换取成功写 “Login successful.” 并退出 0；授权码被拒绝在错误输出写
# “Login failed: That code wasn't accepted…” 并退出 1。登录凭据由官方程序自己存在它的配置目录里，
# 管理器不读；之后 `auth status` 报告 authMethod claude.ai 和邮箱。
CODE_LOGIN_STEPS = ("start", "request_url", "wait_user", "submit_code", "finish")
CODE_URL_SECONDS = 60.0  # 等官方程序给出登录网址最多多久
CODE_WAIT_SECONDS = 900.0  # 从开始到登录完成最多多久（人要去浏览器里登录、再把授权码交回来）
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")
_URL = re.compile(r"https://\S+")
_REFUSED = "wasn't accepted"


# Claude 的模型（T-0288）：取自锁定版本 Claude Code 2.1.323 内置的模型菜单
# （二进制里 model_selector_config 中 id 为 "cc" 的那份：常用四个，其余在“更多模型”里），
# 推理强度是各模型的 effort_levels；Haiku 4.5、Opus 4.1 不支持推理强度。
# 前面四个是别名（--model 收别名或完整模型名，核验记录同上），总是指向这一族最新的模型；
# 旧会话的别名照常能用。--effort 只收 low / medium / high / xhigh / max；
# 模型不支持所选的强度时，官方程序会静默降到它支持的。
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_EFFORTS_NO_XHIGH = ("low", "medium", "high", "max")
CLAUDE_MODELS = (
    ModelInfo(
        "sonnet", "Sonnet（最新）", "别名：总是用最新的 Sonnet", default=True, efforts=_EFFORTS
    ),
    ModelInfo("opus", "Opus（最新）", "别名：总是用最新的 Opus", efforts=_EFFORTS),
    ModelInfo("fable", "Fable（最新）", "别名：总是用最新的 Fable", efforts=_EFFORTS),
    ModelInfo("haiku", "Haiku（最新）", "别名：总是用最新的 Haiku", efforts=_EFFORTS),
    ModelInfo("claude-opus-5-5", "Opus 5.5", "复杂的工作和日常任务", efforts=_EFFORTS),
    ModelInfo("claude-sonnet-5-5", "Sonnet 5.5", "简单的任务，最省额度", efforts=_EFFORTS),
    ModelInfo("claude-fable-5-2", "Fable 5.2", "最难的任务", efforts=_EFFORTS),
    ModelInfo("claude-haiku-5-5", "Haiku 5.5", "最快，适合简短的问题", efforts=_EFFORTS),
    ModelInfo("claude-fable-5-1", "Fable 5.1", "更早的版本", efforts=_EFFORTS),
    ModelInfo("claude-opus-5", "Opus 5", "更早的版本", efforts=_EFFORTS),
    ModelInfo("claude-sonnet-5", "Sonnet 5", "更早的版本", efforts=_EFFORTS),
    ModelInfo("claude-fable-5", "Fable 5", "更早的版本", efforts=_EFFORTS),
    ModelInfo("claude-opus-4-8", "Opus 4.8", "更早的版本", efforts=_EFFORTS),
    ModelInfo("claude-opus-4-7", "Opus 4.7", "更早的版本", efforts=_EFFORTS),
    ModelInfo("claude-opus-4-6", "Opus 4.6", "更早的版本", efforts=_EFFORTS_NO_XHIGH),
    ModelInfo("claude-sonnet-4-6", "Sonnet 4.6", "更早的版本", efforts=_EFFORTS_NO_XHIGH),
    ModelInfo("claude-haiku-4-5-20251001", "Haiku 4.5", "更早的版本，不支持推理强度"),
    ModelInfo("claude-opus-4-1-20250805", "Opus 4.1", "更早的版本，不支持推理强度"),
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
        autocompact_env="CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
        effort_flag="--effort",
        code_login=True,
        background_off_env="CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
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
        autocompact_env="CODEBUDDY_AUTOCOMPACT_PCT_OVERRIDE",
        background_off_env="CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS",
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
            *(("web",) if profile.code_login else ()),
            *profile.web_login,
            *profile.secret_envs,
            *(("existing",) if profile.allow_existing else ()),
        )
        self._web = WebLogin(profile.binary, profile.label) if profile.web_login else None
        self._config_dir = config_dir
        self._secret_file = secrets_dir / f"{profile.slot}.json"
        self._timeout = rpc_timeout
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # 网页授权登录（T-0287）：这一次登录的进度、正在等授权码的官方程序、看着它的任务
        self._code: LoginChallenge | None = None
        self._code_proc: asyncio.subprocess.Process | None = None
        self._code_task: asyncio.Task[None] | None = None
        # 工具调用的编号 → 工具名：结果报回来时知道是哪个工具的
        self._tool_names: dict[str, str] = {}
        self._open_steps: set[str] = set()  # 已经开始、结果还没回来的工具调用
        self._help_models: list[ModelInfo] | None = None
        # 压缩钩子（4.3）：钩子脚本放在哪、平台写的摘要放在哪（engine 启动时设好）
        self.hook_script: Path | None = None
        self.hook_dir: Path | None = None

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

    def _background_env(self, spec: SessionSpec) -> dict[str, str]:
        """能开管理器的后台作业时，关掉官方程序自带的后台任务（LONGRUN 5.11）：
        它们在回答结束、程序退出时会被停掉。"""
        jobs = spec.jobs or spec.remote is not None  # 远程会话也关（方案第 14 节）
        if jobs and spec.mode == "dev" and self.profile.background_off_env:
            return {self.profile.background_off_env: "1"}
        return {}

    # ---- 远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 6.1、9 节）----
    def remote_credentials(self) -> dict[str, str]:
        """随回合注入远程容器的凭据：只有 API 密钥和 Claude 的长期令牌（不会自己刷新）。"""
        secret = self._secret()
        name = self.profile.secret_envs.get(secret.get("method", ""))
        return {name: str(secret["value"])} if name and secret.get("value") else {}

    def _remote_env(self, spec: SessionSpec) -> dict[str, str]:
        env = remote.remote_env(self.slot)
        env.update(self._autocompact(spec))
        env.update(self._background_env(spec))
        if self.slot == "workbuddy" and spec.mode == "dev" and spec.sandbox == "exec":
            # 帮助文本：“Isolated-sandbox full pass: CODEBUDDY_IS_SANDBOX=1”（2.184.0）；
            # 容器本身是沙箱
            env["CODEBUDDY_IS_SANDBOX"] = "1"
        return env

    async def version(self) -> str | None:
        with contextlib.suppress(DriverError):
            return remote.parse_version(await self._run_short(["--version"]))
        return None

    def _save_secret(self, data: dict[str, str]) -> None:
        self._secret_file.parent.mkdir(parents=True, exist_ok=True)
        self._secret_file.parent.chmod(0o700)
        self._secret_file.write_text(json.dumps(data))
        self._secret_file.chmod(0o600)

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        if method == "web" and self.profile.code_login:
            return await self._start_code_login()
        await self._stop_code_login()
        self._code = None
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
        if self._code is not None:
            return self._code
        return self._web.challenge if self._web is not None else None

    async def stop(self) -> None:
        await self._stop_code_login()
        if self._web is not None:
            await self._web.stop()

    # ---- 网页授权登录（Claude，T-0287） ----
    def _code_env(self) -> dict[str, str]:
        """登录用的环境：不带已保存的密钥 / 令牌，免得官方程序以为已经登录了。"""
        env = self._env()
        for name in self.profile.secret_envs.values():
            env.pop(name, None)
        return env

    async def _start_code_login(self) -> LoginChallenge:
        """启动官方程序的 `auth login`，等它给出登录网址；之后等人交回授权码（submit_code）。
        原来的登录（密钥、令牌）在新的登录成功之前照常可用。"""
        await self._stop_code_login()
        steps, log = progress.new_steps(CODE_LOGIN_STEPS), list[str]()
        self._code = LoginChallenge(
            state="pending", steps=steps, log=log, started_at=progress.iso(progress.now())
        )
        progress.mark(steps, "start", "running")
        progress.note(
            log, f"启动 {self.profile.label} 官方程序：auth login --claudeai（用订阅账号登录）"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                self.profile.binary,
                "auth",
                "login",
                "--claudeai",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._code_env(),
            )
        except OSError as error:
            message = f"启动官方程序失败：{error}"
            self._code_fail("start", message)
            raise DriverError("unavailable", message) from error
        self._code_proc = proc
        progress.mark(steps, "start", "done", "官方程序已启动")
        progress.mark(steps, "request_url", "running")
        progress.note(log, f"等官方程序给出登录网址（最多 {CODE_URL_SECONDS:.0f} 秒）")
        found: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._code_task = asyncio.create_task(self._watch_code_login(proc, found))
        try:
            url = await asyncio.wait_for(asyncio.shield(found), CODE_URL_SECONDS)
        except TimeoutError:
            message = f"等了 {CODE_URL_SECONDS:.0f} 秒，官方程序还没给出登录网址"
            self._code_fail("request_url", message)
            await self._stop_code_login()
            raise DriverError("timeout", message) from None
        if url is None:  # 官方程序没给网址就退出了：原因已经记在失败的那一步
            login = self._code
            raise DriverError(
                "platform_error", login.detail if login else "官方程序没有给出登录网址"
            )
        progress.mark(steps, "request_url", "done", "已取得登录网址")
        progress.mark(
            steps,
            "wait_user",
            "running",
            "在浏览器里打开网址，登录 Claude 订阅账号并授权；"
            "授权页会显示一段授权码，把它粘贴到这里",
        )
        progress.note(log, "已取得登录网址，等你在浏览器里登录授权、交回授权码")
        login = self._code
        assert login is not None  # noqa: S101 - 上面刚建好
        self._code = replace(login, verification_url=url, needs_code=True)
        return self._code

    async def _watch_code_login(
        self, proc: asyncio.subprocess.Process, found: asyncio.Future[str | None]
    ) -> None:
        """读官方程序的输出：找登录网址、成功的那一句、失败的原因；等它退出后定下结果。"""
        errors: list[str] = []
        succeeded = False

        async def read_out() -> None:
            nonlocal succeeded
            assert proc.stdout is not None  # noqa: S101
            buffer = ""
            while chunk := await proc.stdout.read(4096):
                buffer += _ESCAPES.sub("", chunk.decode(errors="replace"))
                lines = buffer.split("\n")
                buffer = lines.pop()
                for line in [*lines, buffer]:
                    match = _URL.search(line) if "visit:" in line else None
                    if match and not found.done():
                        found.set_result(match.group(0))
                if "Login successful." in "\n".join(lines):
                    succeeded = True

        async def read_err() -> None:
            assert proc.stderr is not None  # noqa: S101
            while raw := await proc.stderr.readline():
                line = _ESCAPES.sub("", raw.decode(errors="replace")).strip()
                if line:
                    errors.append(line)
                    if self._code is not None:
                        progress.note(self._code.log, f"官方程序：{line}")

        try:
            await asyncio.wait_for(
                asyncio.gather(read_out(), read_err(), proc.wait()), CODE_WAIT_SECONDS
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            if not found.done():
                found.set_result(None)
            minutes = CODE_WAIT_SECONDS / 60
            self._code_fail(
                self._code_step(), f"等了 {minutes:.0f} 分钟还没有完成登录，已停止这次登录"
            )
            return
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            if not found.done():
                found.set_result(None)
            raise
        if not found.done():
            found.set_result(None)
        if self._code_proc is not proc:
            return  # 已经开始了新的一次登录
        self._code_proc = None
        if proc.returncode == 0 and succeeded:
            await self._finish_code_login()
            return
        self._code_fail(self._code_step(), self._login_failure(errors))

    def _code_step(self) -> str:
        """现在卡在哪一步：最后一个“进行中”的步骤。"""
        login = self._code
        running = [s["key"] for s in login.steps if s["status"] == "running"] if login else []
        return running[-1] if running else "request_url"

    @staticmethod
    def _login_failure(errors: list[str]) -> str:
        text = next((e for e in reversed(errors) if e.startswith("Login failed")), "")
        if _REFUSED in text:
            return (
                "授权码没有被接受：可能不完整、已过期、已经用过，或者是上一次登录的。"
                "请重新开始登录，用新的授权码"
            )
        detail = redact(text or (errors[-1] if errors else ""))[:300]
        return (
            f"官方程序没有完成登录：{detail}" if detail else "官方程序没有完成登录，也没有给出原因"
        )

    async def _finish_code_login(self) -> None:
        """官方程序报告登录成功：记下登录方式，再用 `auth status` 确认登录了哪个账号。"""
        login = self._code
        if login is None:
            return
        steps, log = login.steps, login.log
        progress.mark(steps, "submit_code", "done", "官方程序报告登录成功（Login successful.）")
        progress.mark(steps, "finish", "running")
        progress.note(log, "官方程序报告登录成功，查一次登录状态（auth status）确认账号")
        self._save_secret({"method": "web"})
        try:
            data = json.loads(await self._run_short(["auth", "status"]))
        except (DriverError, ValueError):
            data = {}
        if not isinstance(data, dict) or not data.get("loggedIn"):
            self._code_fail("finish", "官方程序报告登录成功，但查登录状态时没有确认已登录")
            return
        email = data.get("email")
        who = f"已登录：{mask_identity(email)}" if isinstance(email, str) and email else "已登录"
        progress.mark(steps, "finish", "done", who)
        progress.note(log, who)
        self._code = replace(
            login,
            state="done",
            needs_code=False,
            detail="",
            finished_at=progress.iso(progress.now()),
        )

    def _code_fail(self, key: str, message: str) -> None:
        login = self._code
        if login is None:
            return
        progress.mark(login.steps, key, "failed", message)
        progress.note(login.log, f"失败：{message}")
        self._code = replace(
            login,
            state="failed",
            needs_code=False,
            detail=message,
            finished_at=progress.iso(progress.now()),
        )

    async def _stop_code_login(self) -> None:
        proc, task = self._code_proc, self._code_task
        self._code_proc = self._code_task = None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def submit_code(self, code: str) -> LoginChallenge:
        """把授权码交给正在等它的官方程序。授权码不记日志，也不保存。"""
        login, proc = self._code, self._code_proc
        if (
            login is None
            or login.state != "pending"
            or not login.needs_code
            or proc is None
            or proc.returncode is not None
            or proc.stdin is None
        ):
            raise DriverError("conflict", "没有在等授权码的登录：请重新开始网页授权登录")
        code = code.strip()
        head, sep, tail = code.partition("#")
        if not sep or not head or not tail or any(c.isspace() for c in code):
            raise DriverError(
                "invalid",
                "授权码不完整：请复制授权页上显示的整段授权码（中间有一个 #），不要多也不要少",
            )
        try:
            proc.stdin.write(f"{code}\n".encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise DriverError("conflict", "官方程序已经退出：请重新开始网页授权登录") from error
        progress.mark(login.steps, "wait_user", "done", "已收到授权码")
        progress.mark(
            login.steps,
            "submit_code",
            "running",
            "官方程序用授权码换取登录（要访问 platform.claude.com）",
        )
        progress.note(login.log, "已把授权码交给官方程序（授权码不记在这里）")
        self._code = replace(login, needs_code=False)
        return self._code

    async def logout(self) -> None:
        await self._stop_code_login()
        self._code = None
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
                if method == "web" and reported == "claude.ai":
                    reported = "web"  # 网页授权登录（T-0287）存下的就是 claude.ai 登录
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

    def _args(self, spec: SessionSpec, resume: bool | None = None) -> list[str]:
        # --include-partial-messages：逐字的增量（stream_event），聊天页据此边写边显示。
        # 在锁定版本的帮助文本和类型定义里核对过：Claude Code 2.1.323、CodeBuddy Code 2.184.0
        args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        assert spec.native_id is not None  # noqa: S101
        # 官方程序里已经有这个会话的记录就续接；按文件判断，不按回合数
        # （失败的回合也可能已经建了会话）。远程会话由调用方先在容器里查好
        if resume is None:
            resume = self._find_transcript(spec.native_id) is not None
        args += ["--resume" if resume else "--session-id", spec.native_id]
        if spec.model:
            args += ["--model", spec.model]
        if spec.effort and self.profile.effort_flag:
            args += [self.profile.effort_flag, spec.effort]
        if spec.mode == "chat":
            prompt = CHAT_NOTICE + (f"\n\n{spec.pinned}" if spec.pinned else "")
            args += ["--tools", "", "--append-system-prompt", prompt]
        elif spec.sandbox == "read_only":
            args += ["--tools", _READ_TOOLS]
        elif spec.sandbox == "workspace_write":
            args += ["--tools", _WRITE_TOOLS, "--permission-mode", "acceptEdits"]
        else:  # exec：管理员在环境上明确打开的档位
            args += ["--permission-mode", "bypassPermissions"]
            if spec.jobs:  # 能开后台作业：告诉模型用 llm-task（LONGRUN 5.5）
                args += ["--append-system-prompt", JOBS_INSTRUCTIONS]
        if spec.remote is not None and spec.mode == "dev" and spec.pinned:
            # 远程会话的固定参考资料：随系统提示每回合带上（不改仓库里的记忆文件）
            args += ["--append-system-prompt", spec.pinned]
        if spec.images:  # 带图片时提问按一条 stream-json 的用户消息从标准输入给
            args += ["--input-format", "stream-json"]
        if spec.hooks and spec.remote is None and self.hook_script is not None:
            # 压缩钩子：按回合经 --settings 带上（两个官方程序都有这个参数，核验记录第 1、2 节）
            args += ["--settings", hooks.settings_json(sys.executable, self.hook_script)]
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
        proc: Any
        if spec.remote is not None:
            proc = await self._spawn_remote(spec, text)
        else:
            spec.workdir.mkdir(parents=True, exist_ok=True)
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.profile.binary,
                    *self._args(spec),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(spec.workdir),
                    env=self._env(
                        {
                            **spec.env,
                            **self._autocompact(spec),
                            **self._hook_env(spec),
                            **self._background_env(spec),
                        }
                    ),
                    limit=16 * 1024 * 1024,
                )
            except OSError as error:
                raise DriverError(
                    "unavailable", f"启动 {self.profile.binary} 失败：{error}"
                ) from error
        self._procs[spec.native_id] = proc
        try:
            assert proc.stdout and proc.stderr  # noqa: S101
            if spec.remote is None:  # 远程进程的标准输入在起进程时已经一次给全
                assert proc.stdin  # noqa: S101
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
            outcome.compactions = seen.compactions
            return self._finish(result, messages, outcome, limited)
        finally:
            self._procs.pop(spec.native_id, None)
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()

    async def _spawn_remote(self, spec: SessionSpec, text: str) -> remote.RemoteProcess:
        """在远程开发容器里起这一回合的官方程序（凭据随进程注入，不落盘）。"""
        target = spec.remote
        hub = self.remote
        if target is None or hub is None:
            raise DriverError("unavailable", "这个管理器没有接上远程开发容器")
        assert spec.native_id is not None  # noqa: S101
        resume = await hub.transcript_exists(target.container, self.slot, spec.native_id)
        proc: remote.RemoteProcess = await hub.spawn(
            target.container,
            purpose="turn",
            platform=remote.PLATFORM_OF_SLOT[self.slot],
            session_id=target.session_id,
            argv=[remote.PROGRAMS[self.slot], *self._args(spec, resume)],
            cwd=target.workdir,
            env=self._remote_env(spec),
            secret_env=self.remote_credentials(),
            stdin=self._stdin(spec, text),
            timeout_seconds=target.timeout_seconds,
        )
        return proc

    def _hook_env(self, spec: SessionSpec) -> dict[str, str]:
        if spec.hooks and self.hook_dir is not None:
            return {"BRIDGE_HOOK_DIR": str(self.hook_dir)}
        return {}

    def take_summaries(self, native_id: str) -> list[dict[str, Any]]:
        """压缩后钩子留下的摘要（按先后），取走后删掉文件。没有就是空的。"""
        if self.hook_dir is None or not re.fullmatch(r"[\w.\-:]{1,100}", native_id):
            return []
        path = self.hook_dir / "summaries" / f"{native_id}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            path.unlink()
        except OSError:
            return []
        out = []
        for line in lines:
            with contextlib.suppress(ValueError):
                item = json.loads(line)
                if isinstance(item, dict) and isinstance(item.get("summary"), str):
                    out.append(item)
        return out

    def _autocompact(self, spec: SessionSpec) -> dict[str, str]:
        """自动压缩的触发点（管理员在设置里填了才有）：上下文用到百分之多少时压缩。"""
        if spec.autocompact and self.profile.autocompact_env:
            return {self.profile.autocompact_env: str(min(max(spec.autocompact, 1), 100))}
        return {}

    async def compact(self, spec: SessionSpec, instructions: str, sink: EventSink) -> TurnOutcome:
        """手动压缩：把 `/compact [要保留什么]` 当提问发（Claude Code 的非交互模式支持；
        CodeBuddy 没核实，结果以有没有收到压缩事件为准）。"""
        command = "/compact" + (f" {instructions.strip()}" if instructions.strip() else "")
        return await self._turn(replace(spec, images=()), command, sink, manual=True)

    async def _on_system(
        self, event: dict[str, Any], seen: _Seen, sink: EventSink, *, manual: bool
    ) -> None:
        """官方程序的 system 事件里和压缩有关的两种，以及后台任务的事件。

        - Claude Code：`subtype: compact_boundary`，带触发方式和压缩前后的 token 数；
        - CodeBuddy Code：只有状态事件 `subtype: status`，`status: "compacting"` 表示开始压缩，
          之后 `status: null` 表示结束（2.184.0 程序文件里的 StreamJsonView），没有前后的 token 数。
        两种都报的话（以后的版本）只算一次。
        - 后台任务（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.7）：两家都报 `task_started` /
          `task_progress` / `task_notification`，Claude 另有 `task_updated`（带 `patch`）。
          字段在 Claude Code 2.1.323、CodeBuddy Code 2.184.0 的程序文件里核对过。"""
        subtype = event.get("subtype")
        if isinstance(subtype, str) and subtype in _TASK_EVENTS:
            task = _task_event(subtype, event)
            if task is not None:
                await sink("background", task)
            return
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
            step = str(block.get("id") or "")
            if step:  # 结果回来时据此知道这一步做完了（回合进度里的“正在做什么”）
                self._open_steps.add(step)
            if name == "Bash" and data.get("command"):
                outcome.commands.append(str(data["command"])[:500])
                await sink("command", {"command": outcome.commands[-1], "step": step})
            elif name in ("Edit", "Write") and data.get("file_path"):
                outcome.files.append(str(data["file_path"]))
                await sink("files", {"paths": [outcome.files[-1]]})
            elif name:  # 读文件、搜索和别的工具调用：也记进过程
                if block.get("id"):
                    self._tool_names[str(block["id"])] = name
                await sink(
                    "tool",
                    {"name": name, "input": json.dumps(data, ensure_ascii=False), "step": step},
                )

    async def _on_tool_results(self, event: dict[str, Any], sink: EventSink) -> None:
        """官方程序把工具结果作为一条 user 消息报出来：每个 tool_result 内容块记一步。"""
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            step = str(block.get("tool_use_id") or "")
            if step in self._open_steps:
                self._open_steps.discard(step)
                await sink("step_done", {"step": step})
            name = self._tool_names.pop(step, None)
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
