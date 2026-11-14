"""平台额度的读取与“额度用完”的识别（docs/v2/LLM_BRIDGE_QUOTA_PLAN.md 第 1、3.1 节）。

三家给的都是“已用百分比 + 重置时间”，没有剩余 token 数。字段的形状：

- Codex：`account/rateLimits/read` / `account/rateLimits/updated` 的 `rateLimits`
  （生成的接口定义里核对过）；
- Claude Code / CodeBuddy Code：回合输出里的 `rate_limit_event`（带 `rate_limit_info`）。
  字段和取值没有账号、不能实测，所以这里按几种可能的写法宽松地读；读不出来就当没有，不猜。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

Window = dict[str, Any]
# 官方程序在额度用完时的用语（小写匹配）。只有匹配这些、或者平台明确给出“用量上限”的错误类型，
# 才算额度中断；普通的限速、服务器繁忙不算
_QUOTA_HINTS = (
    "usage limit",
    "usage_limit",
    "limit reached",
    "5-hour limit",
    "five-hour limit",
    "weekly limit",
    "out of extra usage",
    "quota exceeded",
    "exceeded your current quota",
    "额度已用完",
    "用量已达上限",
)
_KINDS = {
    "five_hour": "five_hour",
    "5h": "five_hour",
    "primary": "five_hour",
    "seven_day": "weekly",
    "weekly": "weekly",
    "secondary": "weekly",
}
_RESET_AT = re.compile(
    r"(?:resets?|重置)(?:\s*(?:at|in|于|时间))?\s*[:：]?\s*"
    r"(?:(?P<epoch>\d{10})\b"
    r"|(?P<iso>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"|(?P<hours>\d+)\s*h(?:ours?)?(?:\s*(?P<mins>\d+)\s*m(?:in(?:utes?)?)?)?"
    r"|(?P<only_mins>\d+)\s*m(?:in(?:utes?)?)?)",
    re.IGNORECASE,
)


def is_quota_text(text: str) -> bool:
    low = text.lower()
    return any(hint in low for hint in _QUOTA_HINTS)


def parse_reset(text: str, now: float | None = None) -> float | None:
    """失败信息里“resets at … / resets in 3h 20m / 重置于 …”后面的时间；读不出来返回 None。"""
    found = _RESET_AT.search(text)
    if not found:
        return None
    now = time.time() if now is None else now
    if found["epoch"]:
        return float(found["epoch"])
    if found["iso"]:
        try:
            parsed = datetime.fromisoformat(found["iso"].replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
        return parsed.timestamp()
    if found["hours"]:
        return now + int(found["hours"]) * 3600 + int(found["mins"] or 0) * 60
    return now + int(found["only_mins"]) * 60


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value) / 1000 if value > 10**11 else float(value)  # 毫秒也认
    if isinstance(value, str):
        if value.isdigit():
            return _timestamp(int(value))
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = (
        float(value) * 100 if 0 < float(value) <= 1 and not float(value).is_integer() else value
    )
    return round(max(0.0, float(number)), 1)


def window(
    kind: str,
    used: Any,
    resets_at: Any,
    status: str = "",
    plan: Any = None,
    *,
    fraction: bool = False,
) -> Window:
    """fraction：used 明确是 0–1 的比例（不靠数值大小去猜）。"""
    if fraction and isinstance(used, int | float) and not isinstance(used, bool) and used <= 1:
        percent: float | None = round(max(0.0, float(used)) * 100, 1)
    else:
        percent = _percent(used)
    if status not in ("ok", "warning", "exceeded"):
        status = (
            "exceeded"
            if percent is not None and percent >= 100
            else "warning"
            if percent is not None and percent >= 80
            else "ok"
        )
    return {
        "kind": _KINDS.get(kind, kind or "other")[:40],
        "used_percent": percent,
        "resets_at": _timestamp(resets_at),
        "status": status,
        "plan": str(plan)[:40] if plan else None,
    }


def from_codex(result: Any) -> list[Window]:
    """`account/rateLimits/read` 的应答或 `account/rateLimits/updated` 的参数 → 各窗口。"""
    limits = result.get("rateLimits") if isinstance(result, dict) else None
    if not isinstance(limits, dict):
        return []
    reached = bool(limits.get("rateLimitReachedType"))
    out: list[Window] = []
    for name in ("primary", "secondary"):
        item = limits.get(name)
        if not isinstance(item, dict):
            continue
        minutes = item.get("windowDurationMins")
        kind = "five_hour" if minutes == 300 else "weekly" if minutes == 10080 else _KINDS[name]
        row = window(
            kind, item.get("usedPercent"), item.get("resetsAt"), plan=limits.get("planType")
        )
        if reached and row["used_percent"] is not None and row["used_percent"] >= 100:
            row["status"] = "exceeded"
        out.append(row)
    return out


def from_rate_limit_event(event: Any) -> list[Window]:
    """Claude Code / CodeBuddy Code 回合输出里的一条 `rate_limit_event` → 窗口（读不出来给空）。"""
    info = event.get("rate_limit_info") if isinstance(event, dict) else None
    if not isinstance(info, dict):
        info = event if isinstance(event, dict) else {}
    kind = info.get("rateLimitType") or info.get("rate_limit_type") or info.get("kind")
    resets = info.get("resetsAt") or info.get("resets_at")
    # utilization 是 0–1 的比例（Anthropic 的统一限额响应头也是这样给的）：1.0 是用完，不是 1%
    used, fraction = info.get("utilization"), True
    if used is None:
        used, fraction = info.get("percentUsed", info.get("used_percent")), False
    raw = str(info.get("status") or "").lower()
    status = (
        "exceeded"
        if raw in ("rejected", "exceeded", "blocked")
        else "warning"
        if "warning" in raw
        else "ok"
        if raw in ("allowed", "ok")
        else ""
    )
    if not isinstance(kind, str) or (resets is None and used is None and not status):
        return []
    return [window(kind, used, resets, status, fraction=fraction)]
