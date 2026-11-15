"""管理器所在的服务器用不用得了各大模型平台（T-0240）。

有些服务器访问不了 ChatGPT / Claude：登录页要在登录之前就说清楚，并给出延迟。常见的有两种，
只测“连不连得上”分不出第二种：

- 连不上：域名解析不了、超时、连接被重置；
- 连得上但被平台拒绝：TLS 握手都正常，平台回的是 403 的拦截页（例如 OpenAI 的
  “Unable to load site”，页面里带着这台服务器的 IP）——这台服务器的 IP 或所在地区
  不在平台允许的范围里。

所以这里对每个地址真的发一次 HTTPS GET（不带任何凭据），看状态码和页面：
401 / 404 这类说明平台正常应答了，算可用；403 且不是普通的人机验证页，算被拒绝。
容器设置了 HTTPS 代理（HTTPS_PROXY / https_proxy）时经代理的 CONNECT 去连，和官方程序走同一条路。

地址来自各官方程序实际访问的域名：Codex（chatgpt.com、auth.openai.com、api.openai.com）、
Claude Code（api.anthropic.com、claude.ai）、CodeBuddy Code 2.184.0 安装包里出现的
copilot.tencent.com（国内站）与 www.codebuddy.ai（国际站）。各平台拦截页的样子只核对了 OpenAI 的
（用户在被拦截的服务器上实测）；别的平台的 403 按“不是人机验证页就算被拒绝”处理。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

TIMEOUT_SECONDS = 8.0
CACHE_SECONDS = 60.0
_MAX_BODY = 64 * 1024
_AGENT = "Mozilla/5.0 (compatible; mcp-manager-connectivity)"
# 普通的人机验证页（Cloudflare 对非浏览器的请求常常这样回）：平台是通的，不算被拒绝
_CHALLENGE = ("just a moment", "checking your browser", "enable javascript and cookies")
# 明确的拦截页
_BLOCKED = (
    "unable to load site",
    "you have been blocked",
    "unsupported_country_region_territory",
    "not available in your country",
    "unavailable in your region",
    "request not allowed",
)


@dataclass(frozen=True)
class Target:
    host: str
    label: str
    path: str = "/"


# need：all = 列出的地址都要可用才算这个平台能用；any = 有一个可用就行（国内站 / 国际站二选一）
TARGETS: dict[str, tuple[str, tuple[Target, ...]]] = {
    "openai": (
        "all",
        (
            Target("chatgpt.com", "ChatGPT（订阅登录、对话）"),
            Target("auth.openai.com", "OpenAI 登录（设备码）"),
            Target("api.openai.com", "OpenAI API（API 密钥）", "/v1/models"),
        ),
    ),
    "claude": (
        "all",
        (
            Target("api.anthropic.com", "Anthropic API", "/v1/models"),
            Target("claude.ai", "Claude（订阅登录）"),
        ),
    ),
    "workbuddy": (
        "any",
        (
            Target("copilot.tencent.com", "CodeBuddy 国内站"),
            Target("www.codebuddy.ai", "CodeBuddy 国际站"),
        ),
    ),
}


def _proxy() -> tuple[str, int] | None:
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        return None
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    if not parts.hostname:
        return None
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def _reason(error: BaseException) -> str:
    """连不上的原因，给人看：不带地址以外的细节。"""
    if isinstance(error, TimeoutError):
        return f"超时（{TIMEOUT_SECONDS:.0f} 秒内没有应答）"
    if isinstance(error, ssl.SSLError):
        return "TLS 握手失败（连接被中途打断或证书不对）"
    if isinstance(error, ConnectionRefusedError):
        return "连接被拒绝"
    if isinstance(error, ConnectionResetError):
        return "连接被重置"
    if isinstance(error, OSError) and getattr(error, "errno", None) in (-2, -3, -5, 8):
        return "域名解析不了"
    return "连不上"


def classify(status: int, headers: dict[str, str], body: str) -> tuple[str, str | None]:
    """平台回了什么 →（ok / blocked，说明）。能走到这里说明网络是通的。"""
    text = body.lower()
    if any(mark in text for mark in _BLOCKED):
        return "blocked", f"平台拒绝了这台服务器的访问（HTTP {status}，拦截页）"
    if status == 403:
        challenge = headers.get("cf-mitigated") == "challenge" or any(
            mark in text for mark in _CHALLENGE
        )
        if not challenge:
            return "blocked", "平台拒绝了这台服务器的访问（HTTP 403）"
    if status == 451:
        return "blocked", "平台拒绝了这台服务器的访问（HTTP 451，所在地区不可用）"
    return "ok", None


async def _fetch(target: Target) -> tuple[int, dict[str, str], str]:
    """发一次 GET，读回状态码、响应头和页面的前一段。"""
    context = ssl.create_default_context()
    proxy = _proxy()
    if proxy is None:
        reader, writer = await asyncio.open_connection(
            target.host, 443, ssl=context, server_hostname=target.host
        )
    else:
        reader, writer = await asyncio.open_connection(*proxy)
        writer.write(
            f"CONNECT {target.host}:443 HTTP/1.1\r\nHost: {target.host}:443\r\n\r\n".encode()
        )
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        if b" 200" not in head.split(b"\r\n", 1)[0]:
            writer.close()
            raise ConnectionRefusedError("proxy refused")
        await writer.start_tls(context, server_hostname=target.host)
    try:
        writer.write(
            (
                f"GET {target.path} HTTP/1.1\r\nHost: {target.host}\r\nUser-Agent: {_AGENT}\r\n"
                "Accept: */*\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        raw = await reader.readuntil(b"\r\n\r\n")
        lines = raw.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ", 2)[1])
        headers = {
            name.strip().lower(): value.strip()
            for name, _, value in (line.partition(":") for line in lines[1:] if ":" in line)
        }
        body = b""
        with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            while len(body) < _MAX_BODY:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                body += chunk
        return status, headers, body.decode("utf-8", "replace")
    finally:
        writer.close()
        with contextlib.suppress(TimeoutError, OSError, ssl.SSLError):
            await asyncio.wait_for(writer.wait_closed(), 1)


async def probe(target: Target) -> dict[str, Any]:
    """访问一次：状态（ok 可用 / blocked 被平台拒绝 / unreachable 连不上）、延迟（毫秒）、说明。"""
    started = time.monotonic()
    answered: float | None = None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            status, headers, body = await _fetch(target)
            answered = time.monotonic()
    except (TimeoutError, OSError, ssl.SSLError, ValueError, IndexError) as error:
        reason = (
            "平台的应答读不懂" if isinstance(error, ValueError | IndexError) else _reason(error)
        )
        return {"state": "unreachable", "latency_ms": None, "http_status": None, "error": reason}
    state, note = classify(status, headers, body)
    return {
        "state": state,
        "latency_ms": round((answered - started) * 1000),
        "http_status": status,
        "error": note,
    }


async def check(slots: list[str], probe_target: Any = probe) -> dict[str, Any]:
    """这些槽位各自的平台地址：逐个访问一次（同时进行），汇总成每个槽位能不能用。"""
    wanted = [(slot, target) for slot in slots for target in TARGETS.get(slot, ("all", ()))[1]]
    results = await asyncio.gather(*(probe_target(target) for _, target in wanted))
    now = time.time()
    out: dict[str, Any] = {}
    for (slot, target), result in zip(wanted, results, strict=True):
        entry = out.setdefault(slot, {"ok": False, "checked_at": now, "targets": []})
        row = {"host": target.host, "label": target.label, "ok": result["state"] == "ok", **result}
        entry["targets"].append(row)
    for slot, entry in out.items():
        oks = [t["ok"] for t in entry["targets"]]
        entry["need"] = TARGETS[slot][0]
        entry["ok"] = all(oks) if entry["need"] == "all" else any(oks)
    return out
