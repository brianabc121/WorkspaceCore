"""开发会话工作目录里的文件（T-0289）：页面上“开发环境”里浏览会话的工作目录，看文件、下载文件。

管理器给每个开发会话一个工作目录（work_dir/<会话号>），仓库克隆在里面，智能体在里面改文件。
这里只读，不改：
- 只认管理器自己建的会话目录（work_dir 下一层）；
  官方程序里同步来的会话（origin=external）不在这里，不能浏览。
- 路径是相对工作目录的；不许 ..、不许空字符；解析符号链接之后必须还在这个工作目录里，否则当作没有。
- 会话可能正在改这个目录，所以不能“先检查路径、再按路径打开”（中间可以把文件换成指出去的符号链接）。
  这里从工作目录的文件描述符开始，一级一级用 O_NOFOLLOW 打开；遇到符号链接由这里自己展开，
  只认工作目录以内的。类型、大小检查和读取都对同一个已打开的文件做，下载也直接从它发送。
- 列目录最多 LIST_LIMIT 项；预览最多读 PREVIEW_BYTES，先脱敏（redact），
  截断时去掉末尾被切开的词，没闭合的私钥块一直遮到结尾；
  有空字符或不是 UTF-8 的当作二进制，不显示内容；
  下载给原文件（人类 2026-11-17 决定：允许 owner / admin 下载原文件），最大 DOWNLOAD_BYTES。
"""

from __future__ import annotations

import errno
import mimetypes
import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import IO, Any

from bridge.redact import MASK, redact
from bridge.session_cli import SESSION_DIR

LIST_LIMIT = 1000
PREVIEW_BYTES = 512 * 1024
DOWNLOAD_BYTES = 50 * 1024 * 1024
PATH_CHARS = 1000
LINK_HOPS = 40  # 展开符号链接最多几次（和操作系统的上限一样），防止链接绕圈
# 不给看的：回合令牌所在的文件夹（bridge/longrun.py），回合进行中里面有这一回合的令牌（T-0292 审查）
HIDDEN = frozenset({SESSION_DIR})
CHUNK = 64 * 1024

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# 最后一级可能是文件也可能是文件夹；O_NONBLOCK：遇到命名管道不会卡住（打开后按类型拒绝）
_ANY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_OPEN_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_CUT_WORD = re.compile(r"[A-Za-z0-9_\-.=+/]+\Z")


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


def _parts(rel: str) -> list[str]:
    text = (rel or "").strip()
    if len(text) > PATH_CHARS or "\x00" in text:
        raise FileError("VALIDATION_FAILED", "路径不对")
    parts = [part for part in PurePosixPath(text.lstrip("/")).parts if part not in ("", ".")]
    if ".." in parts:
        raise FileError("VALIDATION_FAILED", "路径里不能有 ..")
    return parts


_GONE = "没有这个文件或文件夹"
_OUTSIDE = "没有这个文件，或者它在工作目录以外"


def _open(root: Path, rel: str) -> tuple[int, list[str]]:
    """打开工作目录里的一个文件或文件夹，返回它的文件描述符和它在工作目录里的真实路径（各级名字）。

    每一级都是相对上一级已经打开的文件夹、用 O_NOFOLLOW 打开的，所以不会被中途换掉的链接带出去；
    走过的每一级都是真的文件夹，链接里的 .. 按走过的路径退一级就是它真正的上一级。
    真实路径在 HIDDEN 里的（包括经链接绕进去的）当作没有。"""
    fd, names = _walk(root, _parts(rel))
    if names and names[0] in HIDDEN:
        os.close(fd)
        raise FileError("NOT_FOUND", _GONE)
    return fd, names


def _walk(root: Path, parts: list[str]) -> tuple[int, list[str]]:
    queue = list(parts)
    stack = [os.open(root, _DIR_FLAGS)]
    names: list[str] = []
    hops = 0
    try:
        while queue:
            name = queue.pop(0)
            if name in ("", "."):
                continue
            if name == "..":
                if not names:
                    raise FileError("NOT_FOUND", _OUTSIDE)
                os.close(stack.pop())
                names.pop()
                continue
            try:
                fd = os.open(name, _DIR_FLAGS if queue else _ANY_FLAGS, dir_fd=stack[-1])
            except OSError as error:
                target = _link_target(stack[-1], name, error)
                hops += 1
                if hops > LINK_HOPS:
                    raise FileError("NOT_FOUND", "符号链接绕圈了，打不开") from None
                if target.startswith("/"):
                    inner = _beneath(root, target)
                    for fd in stack[1:]:
                        os.close(fd)
                    del stack[1:]
                    names.clear()
                    queue = inner + queue
                else:
                    queue = list(PurePosixPath(target).parts) + queue
                continue
            stack.append(fd)
            names.append(name)
        top = os.dup(stack[-1])
    finally:
        for fd in stack:
            os.close(fd)
    return top, names


def _link_target(dir_fd: int, name: str, error: OSError) -> str:
    """打开失败的这一级是不是符号链接：是就返回它指向哪儿，不是就按打不开报错。"""
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        raise FileError("NOT_FOUND", _GONE) from None
    if not stat.S_ISLNK(info.st_mode):
        if error.errno == errno.ENOTDIR:
            raise FileError("NOT_FOUND", _GONE) from None
        raise FileError("NOT_FOUND", "打不开这个文件或文件夹") from None
    try:
        return os.readlink(name, dir_fd=dir_fd)
    except OSError:
        raise FileError("NOT_FOUND", _GONE) from None


def _beneath(root: Path, target: str) -> list[str]:
    """绝对路径的链接：只认指向这个工作目录里的，返回它在工作目录里的各级名字。"""
    pointed = PurePosixPath(target).parts[1:]
    base = root.parts[1:]
    if tuple(pointed[: len(base)]) != tuple(base):
        raise FileError("NOT_FOUND", _OUTSIDE)
    return list(pointed[len(base) :])


def _kind(root: Path, parent: list[str], entry: os.DirEntry[str]) -> str:
    """dir / file；符号链接看它指向哪儿：
    指向工作目录里的照常是 dir / file，指出去的或打不开的是 link。"""
    if not entry.is_symlink():
        return "dir" if entry.is_dir(follow_symlinks=False) else "file"
    try:
        fd, _ = _walk(root, [*parent, entry.name])
    except (FileError, OSError):
        return "link"
    try:
        mode = os.fstat(fd).st_mode
    finally:
        os.close(fd)
    return "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "link"


def list_dir(root: Path, rel: str) -> dict[str, Any]:
    fd, names = _open(root, rel)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise FileError("VALIDATION_FAILED", "这不是文件夹")
        entries: list[dict[str, Any]] = []
        total = 0
        with os.scandir(fd) as found:
            for entry in found:
                if not names and entry.name in HIDDEN:
                    continue
                total += 1
                if len(entries) >= LIST_LIMIT:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = _kind(root, names, entry)
                entries.append(
                    {
                        "name": entry.name,
                        "kind": kind,
                        "size": info.st_size if kind == "file" else None,
                        "modified": info.st_mtime,
                    }
                )
    finally:
        os.close(fd)
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].casefold()))
    return {"path": "/".join(names), "entries": entries, "total": total}


def media_type(name: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _open_file(root: Path, rel: str) -> tuple[IO[bytes], os.stat_result, list[str]]:
    """打开一个普通文件；返回已打开的文件和它的信息（之后的检查和读取都用这一个）。"""
    fd, names = _open(root, rel)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not names:
            raise FileError("VALIDATION_FAILED", "这不是文件")
        os.set_blocking(fd, True)
        handle = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    return handle, info, names


def redact_preview(text: str, truncated: bool) -> str:
    """预览的脱敏：截断处可能把密钥切成半截，规则就认不出来了。
    截断时去掉末尾被切开的词；没闭合的私钥块（结束标记在截断处以后，或者文件本身就不完整）一直遮到结尾。"""
    if truncated:
        text = _CUT_WORD.sub("", text)
    text = redact(text)
    unclosed = _OPEN_KEY.search(text)  # 完整的私钥块已经换成了 ***，剩下的开始标记都没闭合
    return text[: unclosed.start()] + MASK if unclosed else text


def preview(root: Path, rel: str) -> dict[str, Any]:
    """文件的信息和开头一段文字（脱敏）；二进制或不是 UTF-8 的不给内容。"""
    handle, info, names = _open_file(root, rel)
    with handle:
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
        "path": "/".join(names),
        "name": names[-1],
        "size": info.st_size,
        "modified": info.st_mtime,
        "media_type": media_type(names[-1]),
        "binary": binary,
        "truncated": truncated and not binary,
        "text": redact_preview(text, truncated) if text else "",
        "downloadable": info.st_size <= DOWNLOAD_BYTES,
    }


class Download:
    """下载的原文件（不改内容）：已经打开、检查过的文件，按打开时的大小发送，发完关掉。"""

    def __init__(self, handle: IO[bytes], size: int, name: str) -> None:
        self.handle, self.size, self.name = handle, size, name

    def chunks(self) -> Iterator[bytes]:
        left = self.size
        try:
            while left > 0:
                chunk = self.handle.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk
        finally:
            self.handle.close()

    def close(self) -> None:
        self.handle.close()


def download(root: Path, rel: str) -> Download:
    """下载的原文件；太大的不给。"""
    handle, info, names = _open_file(root, rel)
    if info.st_size > DOWNLOAD_BYTES:
        handle.close()
        raise FileError("NOT_SUPPORTED", f"文件太大，最多下载 {DOWNLOAD_BYTES // 1024 // 1024} MB")
    return Download(handle, info.st_size, names[-1])
