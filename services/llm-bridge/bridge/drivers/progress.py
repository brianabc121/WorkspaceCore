"""登录的逐步进度与运行信息（T-0276 起用于 CodeBuddy 网页登录，T-0279 起 ChatGPT 设备码登录也用）。

每一步是 {key, status, detail, started_at, finished_at}，
status 是 pending / running / done / failed / skipped；运行信息是带时间的文字行。
驱动原地更新这两个列表，LoginChallenge 引用的就是它们，“查登录进度”随时读到最新的。
写进去的文字一律先脱敏：调用方给的口令原文、“口令 / 令牌 / 密钥”后面跟着的值、
32 个字符以上像密钥的一串，都换成 ***。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

LOG_LINES = 120  # 运行信息最多留多少行
LINE_CHARS = 300  # 每行最多多长
_LABELLED = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|bearer|cookie)\b(\s*[:=]?\s*)(\S+)"
)
_LONG = re.compile(r"[A-Za-z0-9_\-+=]{32,}")


def now() -> datetime:
    return datetime.now(UTC)


def iso(at: datetime) -> str:
    """精确到毫秒：页面用开始时间分辨“这一次”和“上一次”登录，一秒内重试也分得开。"""
    return at.isoformat(timespec="milliseconds")


def new_steps(keys: Iterable[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "status": "pending", "detail": "", "started_at": None, "finished_at": None}
        for key in keys
    ]


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = _LABELLED.sub(lambda m: f"{m[1]}{m[2]}***", text)
    text = _LONG.sub("***", text)
    return " ".join(text.split())[:LINE_CHARS]


def note(log: list[str], text: str, secrets: Iterable[str] = ()) -> None:
    """记一行运行信息（脱敏、带时间），只留最近的 LOG_LINES 行。"""
    line = scrub(text, secrets)
    if line:
        log.append(f"{now():%H:%M:%S} {line}")
        del log[:-LOG_LINES]


def mark(
    steps: list[dict[str, Any]],
    key: str,
    status: str,
    detail: str | None = None,
    secrets: Iterable[str] = (),
) -> None:
    """改一步的状态：开始时记开始时间，结束（完成、失败、跳过）时记结束时间。"""
    for step in steps:
        if step["key"] != key:
            continue
        step["status"] = status
        if status == "running" and not step["started_at"]:
            step["started_at"] = iso(now())
        if status in ("done", "failed", "skipped"):
            step["started_at"] = step["started_at"] or iso(now())
            step["finished_at"] = iso(now())
        if detail is not None:
            step["detail"] = scrub(detail, secrets)
