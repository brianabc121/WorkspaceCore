"""远程会话工作目录的浏览（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 10.2）。

管理器对远程会话的文件接口回答 REMOTE_SESSION；工作台改为经容器服务器在容器里读：
- 只读，限定在 /workspace/sessions/<会话号> 里：先在容器里 realpath 解析（符号链接展开后），
  不在会话目录里就拒绝；.home、.mw 在会话目录外，所以永远读不到；
- 预览开头 512 KB，脱敏规则和管理器一样（截断处去掉被切开的词，没闭合的私钥块遮到结尾）；
- 下载原文件（owner / admin，记审计），远程会话最多 20 MB（容器服务器读文件的上限）。
"""

from __future__ import annotations

import base64
import mimetypes
import re
import uuid

from app.core.errors import ApiError
from app.domain import runner_specs as specs
from app.domain.runner import RunnerError
from app.models import RunnerEnvironment
from app.modules.llm_bridge.models import LlmBridge
from app.modules.llm_bridge.remote import RemoteContainers, RemoteError, session_dir
from app.modules.llm_bridge.schemas import (
    LlmBridgeFileEntryOut,
    LlmBridgeFileList,
    LlmBridgeFileOut,
)
from app.modules.llm_bridge.service import LlmBridgeService

PREVIEW_BYTES = 512 * 1024
LIST_MAX = 1000
DOWNLOAD_MAX = specs.FILE_LIMIT
HIDDEN = (".llm-task",)

_RESOLVE = (
    'root="$1"; t="$1${2:+/$2}"; r=$(realpath -e -- "$t" 2>/dev/null) || exit 2; '
    'case "$r" in "$root"|"$root"/*) ;; *) exit 3;; esac; '
)
LIST_SCRIPT = (
    _RESOLVE + '[ -d "$r" ] || exit 4; '
    "find \"$r\" -mindepth 1 -maxdepth 1 -printf '%y\\t%s\\t%T@\\t%f\\n' | head -n 1001"
)
PREVIEW_SCRIPT = (
    _RESOLVE + '[ -f "$r" ] || exit 4; '
    'printf "%s\\n%s\\n" "$(stat -c "%s %Y" "$r")" "${r#"$root"/}"; '
    f'head -c {PREVIEW_BYTES + 1} "$r" | base64 -w0'
)

_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-\.=]{20,}"),
)
_OPEN_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_CUT_WORD = re.compile(r"[A-Za-z0-9_\-.=+/]+\Z")
MASK = "***"
_EXIT = {
    2: ("NOT_FOUND", "没有这个文件或文件夹"),
    3: ("FORBIDDEN", "这个路径不在会话的工作目录里"),
    4: ("VALIDATION_FAILED", "类型不对（要的是文件夹却是文件，或者反过来）"),
}


def redact_preview(text: str, truncated: bool) -> str:
    """和管理器 bridge/workfiles.py 的同名函数一样。"""
    if truncated:
        text = _CUT_WORD.sub("", text)
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    unclosed = _OPEN_KEY.search(text)
    return text[: unclosed.start()] + MASK if unclosed else text


def clean_rel(path: str) -> str:
    rel = path.strip().strip("/")
    if "\x00" in rel or len(rel) > 1000:
        raise ApiError(422, "VALIDATION_FAILED", "路径不合法")
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ApiError(422, "VALIDATION_FAILED", "路径里不能有 ..")
    if parts and parts[0] in HIDDEN:
        raise ApiError(404, "NOT_FOUND", "没有这个文件或文件夹")
    return "/".join(parts)


class RemoteFiles:
    def __init__(self, service: LlmBridgeService, remote: RemoteContainers) -> None:
        self.service = service
        self.remote = remote

    async def _target(self, bridge: LlmBridge, session_id: str) -> tuple[RunnerEnvironment, str]:
        data = await self.service.admin(bridge, "GET", f"/sessions/{session_id}")
        session = data.get("session") if isinstance(data, dict) else None
        info = session.get("remote") if isinstance(session, dict) else None
        if not isinstance(info, dict) or not info.get("container"):
            raise ApiError(404, "NOT_FOUND", "这个会话不在远程开发容器里")
        try:
            folder = session_dir(session_id)
            row = await self.remote.get(
                bridge.workspace_id, bridge.id, uuid.UUID(info["container"])
            )
        except (RemoteError, ValueError):
            raise ApiError(404, "NOT_FOUND", "这个会话的远程容器不在了") from None
        if info.get("path") not in (None, folder):
            raise ApiError(404, "NOT_FOUND", "会话目录不对")
        env = await self.remote.env_of(row)
        if env is None or env.state in ("deleted", "pending_delete", "failed"):
            raise ApiError(409, "ENV_UNAVAILABLE", "这个会话的远程容器现在不能用")
        return env, folder

    async def _run(self, env: RunnerEnvironment, script: str, folder: str, rel: str) -> str:
        managed = self.remote.managed
        try:
            run = await managed.helper(
                env, ["sh", "-c", script, "mw-files", folder, rel], timeout_seconds=60
            )
            await self.service.db.commit()
        except RunnerError as error:
            raise ApiError(409, error.code, error.message) from None
        done = await managed.runner.wait_run(run.id, 45)
        if done is None or done.finished_at is None:
            raise ApiError(503, "TIMEOUT", "容器服务器没有及时返回（容器可能正在启动），稍后再试")
        if done.exit_code in _EXIT:
            code, message = _EXIT[done.exit_code]
            raise ApiError(404 if code == "NOT_FOUND" else 422, code, message)
        if done.exit_code != 0:
            raise ApiError(503, "RUNNER_FAILED", "容器里没读成")
        return done.output or ""

    async def list_dir(self, bridge: LlmBridge, session_id: str, path: str) -> LlmBridgeFileList:
        env, folder = await self._target(bridge, session_id)
        rel = clean_rel(path)
        output = await self._run(env, LIST_SCRIPT, folder, rel)
        entries: list[LlmBridgeFileEntryOut] = []
        for line in output.splitlines()[: LIST_MAX + 1]:
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            kind_char, size, mtime, name = parts
            if not rel and name in HIDDEN:
                continue
            kind = {"d": "dir", "f": "file", "l": "link"}.get(kind_char)
            if kind is None:
                continue
            try:
                modified = float(mtime)
            except ValueError:
                modified = 0.0
            entries.append(
                LlmBridgeFileEntryOut(
                    name=name,
                    kind=kind,
                    size=int(size) if kind == "file" and size.isdigit() else None,
                    modified=modified,
                )
            )
        total = len(entries)
        entries.sort(key=lambda e: (e.kind != "dir", e.name))
        return LlmBridgeFileList(path=rel, entries=entries[:LIST_MAX], total=total)

    async def preview(self, bridge: LlmBridge, session_id: str, path: str) -> LlmBridgeFileOut:
        env, folder = await self._target(bridge, session_id)
        rel = clean_rel(path)
        if not rel:
            raise ApiError(422, "VALIDATION_FAILED", "要给文件路径")
        output = await self._run(env, PREVIEW_SCRIPT, folder, rel)
        head_line, _, rest = output.partition("\n")
        resolved, _, encoded = rest.partition("\n")
        size_text, _, mtime_text = head_line.partition(" ")
        try:
            raw = base64.b64decode(encoded.strip())
        except ValueError:
            raise ApiError(503, "RUNNER_FAILED", "容器里没读成") from None
        size = int(size_text) if size_text.isdigit() else len(raw)
        truncated = size > PREVIEW_BYTES or len(raw) > PREVIEW_BYTES
        head = raw[:PREVIEW_BYTES]
        binary = b"\x00" in head
        text = ""
        if not binary:
            try:
                text = head.decode("utf-8")
            except UnicodeDecodeError as error:
                if truncated and error.start >= len(head) - 4:
                    text = head[: error.start].decode("utf-8", errors="strict")
                else:
                    binary = True
        name = resolved.rsplit("/", 1)[-1] or rel.rsplit("/", 1)[-1]
        return LlmBridgeFileOut(
            path=rel,
            name=name,
            size=size,
            modified=float(mtime_text) if mtime_text.isdigit() else 0.0,
            media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            binary=binary,
            truncated=truncated,
            text="" if binary else redact_preview(text, truncated),
            downloadable=size <= DOWNLOAD_MAX,
        )

    async def download(self, bridge: LlmBridge, session_id: str, path: str) -> tuple[str, bytes]:
        env, folder = await self._target(bridge, session_id)
        rel = clean_rel(path)
        if not rel:
            raise ApiError(422, "VALIDATION_FAILED", "要给文件路径")
        # 先在容器里解析（符号链接展开后必须还在会话目录里），再按解析后的路径读
        output = await self._run(env, PREVIEW_SCRIPT, folder, rel)
        head_line, _, rest = output.partition("\n")
        resolved = rest.partition("\n")[0]
        size_text = head_line.partition(" ")[0]
        if size_text.isdigit() and int(size_text) > DOWNLOAD_MAX:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "远程会话的文件最多下载 20 MB")
        managed = self.remote.managed
        target = f"{folder}/{resolved}".removeprefix("/workspace/")
        try:
            command = await managed.files(env, "read", target)
            await self.service.db.commit()
        except RunnerError as error:
            raise ApiError(409, error.code, error.message) from None
        done = await managed.runner.wait_command(command.id, 60)
        if done is None or done.status != "done":
            raise ApiError(503, "RUNNER_FAILED", "容器服务器没有及时返回文件")
        data = base64.b64decode(str((done.result or {}).get("b64") or ""))
        return resolved.rsplit("/", 1)[-1] or "file", data
