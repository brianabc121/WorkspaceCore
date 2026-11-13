"""Claude Code 与 CodeBuddy Code（WorkBuddy）共用的驱动：每个回合起一次官方命令行，

`<程序> -p --output-format stream-json --verbose [--session-id <id> | --resume <id>]`，
消息从标准输入给，逐行读 JSON 事件。两家的参数与事件格式相同（claude 2.1.323、
codebuddy 2.184.0 上核验，见 docs/decisions/2026-11-13-llm-bridge-verification.md 第 3、4 节）。

- 会话号由桥生成（UUID）；官方程序里还没有这个会话时用 --session-id，已有时用 --resume；
- 官方程序在自己的配置目录里保存会话记录，会话列表和内容读的就是那些文件；
- 子进程的环境变量只给白名单里的几项加上登录凭据，不继承桥自己的环境。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        label="WorkBuddy（CodeBuddy Code）",
        binary=binary,
        config_env="CODEBUDDY_CONFIG_DIR",
        secret_envs={"api_key": "CODEBUDDY_API_KEY"},
        models=(ModelInfo("auto", "自动", "由 CodeBuddy 选择模型", default=True),),
        auth_cli=False,
        models_from_help=True,
    )


def _error_kind(text: str) -> ErrorKind:
    low = text.lower()
    if any(hint in low for hint in _AUTH_HINTS):
        return "not_logged_in"
    if any(hint in low for hint in _RATE_HINTS):
        return "rate_limited"
    return "platform_error"


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


class CliStreamDriver(Driver):
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
        self.login_methods = (*profile.secret_envs, "existing")
        self._config_dir = config_dir
        self._secret_file = secrets_dir / f"{profile.slot}.json"
        self._timeout = rpc_timeout
        self._procs: dict[str, asyncio.subprocess.Process] = {}
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

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        if method == "existing":
            data = {"method": "existing"}
        elif method in self.profile.secret_envs:
            value = (payload.get(method) or payload.get("value") or "").strip()
            if not value:
                raise DriverError("invalid", f"缺少 {method}")
            data = {"method": method, "value": value}
        else:
            raise DriverError("not_supported", f"{self.profile.label} 不支持登录方式 {method}")
        self._secret_file.parent.mkdir(parents=True, exist_ok=True)
        self._secret_file.parent.chmod(0o700)
        self._secret_file.write_text(json.dumps(data))
        self._secret_file.chmod(0o600)
        return LoginChallenge(state="done")

    async def logout(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._secret_file.unlink()
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
        secret = self._secret()
        method = secret.get("method")
        fingerprint = None
        if secret.get("value"):
            digest = hashlib.sha256(secret["value"].encode()).hexdigest()[:12]
            fingerprint = f"{method}:{digest}"
        if self.profile.auth_cli:
            try:
                data = json.loads(await self._run_short(["auth", "status"]))
            except ValueError:
                data = {}
            if not data.get("loggedIn"):
                return AccountStatus(logged_in=False, detail="官方程序报告未登录")
            identity = data.get("email") or fingerprint
            return AccountStatus(True, identity, str(data.get("authMethod") or method or ""))
        if method is None:
            return AccountStatus(logged_in=False, detail="还没有配置登录方式")
        detail = "这个平台没有查询登录状态的命令，发送时才能确认凭据是否有效"
        return AccountStatus(True, fingerprint, method, detail)

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
        args = ["-p", "--output-format", "stream-json", "--verbose"]
        assert spec.native_id is not None  # noqa: S101
        # 官方程序里已经有这个会话的记录就续接；按文件判断，不按回合数
        # （失败的回合也可能已经建了会话）
        resume = self._find_transcript(spec.native_id) is not None
        args += ["--resume" if resume else "--session-id", spec.native_id]
        if spec.model:
            args += ["--model", spec.model]
        if spec.mode == "chat":
            args += ["--tools", ""]
        elif spec.sandbox == "read_only":
            args += ["--tools", _READ_TOOLS]
        elif spec.sandbox == "workspace_write":
            args += ["--tools", _WRITE_TOOLS, "--permission-mode", "acceptEdits"]
        else:  # exec：管理员在环境上明确打开的档位
            args += ["--permission-mode", "bypassPermissions"]
        return args

    async def run_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
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
            proc.stdin.write(text.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(proc.stderr.read())
            outcome = TurnOutcome(text="", native_turn_id=None)
            messages: list[str] = []
            result: dict[str, Any] | None = None
            while line := await proc.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "assistant":
                    await self._on_assistant(event, messages, outcome, sink)
                elif event.get("type") == "result":
                    result = event
            await proc.wait()
            stderr = (await stderr_task).decode(errors="replace")
            if result is None:
                tail = redact(stderr.strip()[-300:]) or f"退出码 {proc.returncode}"
                raise DriverError(_error_kind(tail), f"{self.profile.label} 没有返回结果：{tail}")
            return self._finish(result, messages, outcome)
        finally:
            self._procs.pop(spec.native_id, None)
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()

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
            if block.get("name") == "Bash" and data.get("command"):
                outcome.commands.append(str(data["command"])[:500])
                await sink("command", {"command": outcome.commands[-1]})
            elif block.get("name") in ("Edit", "Write") and data.get("file_path"):
                outcome.files.append(str(data["file_path"]))
                await sink("files", {"paths": [outcome.files[-1]]})

    def _finish(
        self, result: dict[str, Any], messages: list[str], outcome: TurnOutcome
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
            raise DriverError(_error_kind(text), redact(text)[:500])
        raw = result.get("usage")
        usage: dict[str, Any] = raw if isinstance(raw, dict) else {}
        outcome.usage = {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }
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
