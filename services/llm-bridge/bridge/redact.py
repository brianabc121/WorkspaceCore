"""把文本里像密钥的片段打码：工具结果、事件和日志都先过这里。"""

from __future__ import annotations

import re

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
MASK = "***"


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def mask_identity(value: str) -> str:
    """账号标识只显示头尾：abc@example.com → a***@example.com。"""
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:1]}***@{domain}"
    return f"{value[:2]}***{value[-2:]}" if len(value) > 6 else MASK
