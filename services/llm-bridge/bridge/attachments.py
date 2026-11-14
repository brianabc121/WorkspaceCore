"""会话里的附件：能收哪些文件、多大、叫什么（T-0236 第二步）。

方案见 docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 3 节。

类型只按扩展名认（不信上传时说的类型）；图片再核对文件头。SVG 和网页不当图片收，
下载接口只把这里的四种图片内联显示，别的一律按附件下载。
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

MAX_BYTES = 20 * 1024 * 1024
MAX_PER_MESSAGE = 8
MAX_OUTPUT_PER_TURN = 50
MAX_PENDING_UPLOADS = 32  # 一个会话里上传了还没发出去的附件
STALE_UPLOAD_SECONDS = 24 * 3600

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_TEXT = [
    ".txt",
    ".log",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".xml",
    ".sql",
    ".sh",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".vue",
    ".css",
    ".html",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".diff",
    ".patch",
]
FILE_TYPES = {
    **{suffix: "text/plain" for suffix in _TEXT},
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
}
_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")


def safe_name(name: str) -> str:
    """只留文件名本身：去掉目录、控制字符和文件系统不认的字符，最长 120 个字符。"""
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    if not cleaned:
        return "file"
    suffix = PurePosixPath(cleaned).suffix[:16]
    return cleaned if len(cleaned) <= 120 else cleaned[: 120 - len(suffix)] + suffix


def input_type(name: str) -> str | None:
    """上传的文件算什么类型；不收的返回 None。"""
    suffix = PurePosixPath(name).suffix.lower()
    return IMAGE_TYPES.get(suffix) or FILE_TYPES.get(suffix)


def output_type(name: str) -> str:
    """模型产出的文件：认得的按上面的类型，别的当二进制。"""
    return input_type(name) or "application/octet-stream"


def is_image(media_type: str) -> bool:
    return media_type in IMAGE_TYPES.values()


def looks_like(media_type: str, data: bytes) -> bool:
    """图片的文件头对不对得上扩展名（别的类型不核对）。"""
    if media_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    heads = _MAGIC.get(media_type)
    return heads is None or any(data.startswith(head) for head in heads)
