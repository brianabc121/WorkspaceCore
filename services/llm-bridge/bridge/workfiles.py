"""开发会话工作目录里的文件（T-____）：页面上“开发环境”里浏览会话的工作目录，看文件、下载文件。

管理器给每个开发会话一个工作目录（work_dir/<会话号>），仓库克隆在里面，智能体在里面改文件。
这里只读，不改：
- 只认管理器自己建的会话目录（work_dir 下一层）；
  官方程序里同步来的会话（origin=external）不在这里，不能浏览。
- 路径是相对工作目录的；不许 ..、不许空字符；解析符号链接之后必须还在这个工作目录里，否则当作没有。
- 列目录最多 LIST_LIMIT 项；预览最多读 PREVIEW_BYTES，先脱敏（redact），
  有空字符或不是 UTF-8 的当作二进制，不显示内容；
  下载给原文件（人类 2026-11-17 决定：允许 owner / admin 下载原文件），最大 DOWNLOAD_BYTES。
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Any

from bridge.redact import redact

LIST_LIMIT = 1000
PREVIEW_BYTES = 512 * 1024
DOWNLOAD_BYTES = 50 * 1024 * 1024
PATH_CHARS = 1000


class FileError(Exception):
    """code 用 BridgeError 的错误码（NOT_FOUND / VALIDATION_FAILED / NOT_SUPPORTED）；
    engine 原样转成 BridgeError。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def session_root(work_dir: Path, session: dict[str, Any]) -> Path:
    """这个会话的工作目录（已解析）；不是管理器建的、或者目录已经不在了就报错。"""
    raw = str(session.get("workdir") or "")
    base = work_dir.resolve()
    if session.get("origin") != "bridge" or not raw:
        raise FileError(
            "NOT_SUPPORTED", "这个会话没有管理器里的工作目录（官方程序里同步来的会话不能浏览）"
        )
    root = Path(raw).resolve()
    if root.parent != base:
        raise FileError("NOT_SUPPORTED", "这个会话的工作目录不在管理器的工作区里，不能浏览")
    if not root.is_dir():
        raise FileError("NOT_FOUND", "这个会话的工作目录已经不在了（会话删除或清理过）")
    return root


def _inside(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def resolve(root: Path, rel: str) -> Path:
    """相对工作目录的路径 → 绝对路径；跳出工作目录（.. 或符号链接）当作没有这个文件。"""
    text = (rel or "").strip()
    if len(text) > PATH_CHARS or "\x00" in text:
        raise FileError("VALIDATION_FAILED", "路径不对")
    parts = PurePosixPath(text.lstrip("/")).parts
    if any(part == ".." for part in parts):
        raise FileError("VALIDATION_FAILED", "路径里不能有 ..")
    target = root.joinpath(*parts).resolve()
    if not _inside(root, target):
        raise FileError("NOT_FOUND", "没有这个文件，或者它在工作目录以外")
    if not target.exists():
        raise FileError("NOT_FOUND", "没有这个文件或文件夹")
    return target


def relative(root: Path, target: Path) -> str:
    return "" if target == root else target.relative_to(root).as_posix()


def _kind(root: Path, entry: os.DirEntry[str]) -> str:
    """dir / file；符号链接看它指向哪儿：
    指向工作目录里的照常是 dir / file，指出去的是 link（打不开）。"""
    if entry.is_symlink():
        try:
            pointed = Path(entry.path).resolve()
        except (OSError, RuntimeError):
            return "link"
        if not _inside(root, pointed) or not pointed.exists():
            return "link"
        return "dir" if pointed.is_dir() else "file"
    return "dir" if entry.is_dir(follow_symlinks=False) else "file"


def list_dir(root: Path, rel: str) -> dict[str, Any]:
    target = resolve(root, rel)
    if not target.is_dir():
        raise FileError("VALIDATION_FAILED", "这不是文件夹")
    entries: list[dict[str, Any]] = []
    total = 0
    with os.scandir(target) as found:
        for entry in found:
            total += 1
            if len(entries) >= LIST_LIMIT:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            kind = _kind(root, entry)
            entries.append(
                {
                    "name": entry.name,
                    "kind": kind,
                    "size": info.st_size if kind == "file" else None,
                    "modified": info.st_mtime,
                }
            )
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].casefold()))
    return {"path": relative(root, target), "entries": entries, "total": total}


def media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def preview(root: Path, rel: str) -> dict[str, Any]:
    """文件的信息和开头一段文字（脱敏）；二进制或不是 UTF-8 的不给内容。"""
    target = resolve(root, rel)
    if not target.is_file():
        raise FileError("VALIDATION_FAILED", "这不是文件")
    info = target.stat()
    with target.open("rb") as handle:
        head = handle.read(PREVIEW_BYTES)
    truncated = info.st_size > len(head)
    binary = b"\x00" in head
    text = ""
    if not binary:
        try:
            text = head.decode("utf-8")
        except UnicodeDecodeError as error:
            # 截断时最后一个字可能被切开：退回到切开的位置之前；中间就解不开的当作二进制
            if truncated and error.start >= len(head) - 4:
                text = head[: error.start].decode("utf-8", errors="strict")
            else:
                binary = True
    return {
        "path": relative(root, target),
        "name": target.name,
        "size": info.st_size,
        "modified": info.st_mtime,
        "media_type": media_type(target),
        "binary": binary,
        "truncated": truncated and not binary,
        "text": redact(text) if text else "",
        "downloadable": info.st_size <= DOWNLOAD_BYTES,
    }


def download(root: Path, rel: str) -> Path:
    """下载的原文件（不改内容）；太大的不给。"""
    target = resolve(root, rel)
    if not target.is_file():
        raise FileError("VALIDATION_FAILED", "这不是文件")
    if target.stat().st_size > DOWNLOAD_BYTES:
        raise FileError("NOT_SUPPORTED", f"文件太大，最多下载 {DOWNLOAD_BYTES // 1024 // 1024} MB")
    return target
