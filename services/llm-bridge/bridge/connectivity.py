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

延迟怎么算（T-0278）：页面上的“延迟”是建立连接用的时间（TCP 握手，一次网络往返，和 ping 可比），
另外分步给出：域名解析、建立连接、加密握手（TLS）、平台应答（发出请求到收到应答头）。
以前给的是一次完整访问的总耗时（还含读页面正文、等关连接），在一台 ping 只有 2 ms 的服务器上
显示 5 秒，看不出慢在哪；8 秒的超时也按整个过程算，慢一点就被误判成“连不上”。
现在超时只算到收到应答头为止，并写明卡在哪一步；读正文（认拦截页用）和关连接另有上限，不计入延迟。
经 HTTPS 代理时域名由代理解析，“建立连接”是连上代理、再由代理连上平台的时间。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

TIMEOUT_SECONDS = 12.0  # 从开始到收到平台的应答头；超过就算连不上
BODY_SECONDS = 3.0  # 收到应答头之后读页面正文（认拦截页用）最多等多久；不计入延迟
CLOSE_SECONDS = 1.0  # 关连接最多等多久；不计入延迟
CACHE_SECONDS = 60.0
# 一次访问分几步，各自计时；失败时报卡在哪一步
STAGES = ("dns", "connect", "tls", "response")
_STAGE_NAMES = {
    "dns": "域名解析",
    "connect": "建立连接",
    "tls": "加密握手",
    "response": "等平台应答",
}
_TIMEOUT_TEXT = {
    "dns": "域名解析超时：没有解析出平台的地址",
    "connect": "建立连接超时：平台的服务器没有回应",
    "tls": "加密握手超时：连上了，但握手没有完成",
    "response": "平台没有应答：连上了，但一直没有回应",
}
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
    port: int = 443


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


def _ssl_context() -> ssl.SSLContext:
    """系统信任的证书；测试里换成信任自签证书的。"""
    return ssl.create_default_context()


def _proxy() -> tuple[str, int] | None:
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        return None
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    if not parts.hostname:
        return None
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


class _ProxyRefused(OSError):
    """HTTPS 代理不肯建立到这个地址的隧道。"""


@dataclass
class _Run:
    """一次访问的进度：做到哪一步、每一步用了多少毫秒。"""

    stage: str = "dns"
    timings: dict[str, int] = field(default_factory=dict)
    mark: float = field(default_factory=time.monotonic)
    writer: asyncio.StreamWriter | None = None
    address: str | None = None
    via_proxy: bool = False

    def lap(self, stage: str, following: str) -> None:
        now = time.monotonic()
        self.timings[stage] = round((now - self.mark) * 1000)
        self.mark = now
        self.stage = following

    def detail(self) -> dict[str, Any]:
        return {
            "timings": {f"{stage}_ms": self.timings.get(stage) for stage in STAGES},
            "address": self.address,
            "via_proxy": self.via_proxy,
        }


# 访问一次可能遇到的失败：都算连不上（超时 TimeoutError、TLS 的 SSLError 都是 OSError）
_FAILURES = (
    OSError,
    ValueError,
    IndexError,
    asyncio.IncompleteReadError,
    asyncio.LimitOverrunError,
)


def _reason(error: BaseException, stage: str) -> str:
    """连不上的原因和卡在哪一步，给人看：不带地址以外的细节。"""
    if isinstance(error, TimeoutError):
        return f"{_TIMEOUT_TEXT.get(stage, '超时')}（共等了 {TIMEOUT_SECONDS:.0f} 秒）"
    if isinstance(error, _ProxyRefused):
        return "HTTPS 代理拒绝连到这个地址"
    if isinstance(error, ValueError | IndexError | asyncio.LimitOverrunError):
        return "平台的应答读不懂"
    if isinstance(error, ssl.SSLError):
        return "加密握手失败（连接被中途打断或证书不对）"
    if stage == "dns" or isinstance(error, socket.gaierror):
        return "域名解析不了"
    if isinstance(error, ConnectionRefusedError):
        text = "连接被拒绝"
    elif isinstance(error, ConnectionResetError):
        text = "连接被重置"
    elif isinstance(error, asyncio.IncompleteReadError):
        text = "连接被对方关掉了"
    else:
        text = "连不上"
    return f"{text}（在{_STAGE_NAMES.get(stage, stage)}这一步）"


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


async def _connect(infos: list[Any]) -> tuple[socket.socket, str]:
    """按解析结果的顺序逐个地址去连（和 asyncio 自己连时的顺序一样），连上第一个就用它。"""
    loop = asyncio.get_running_loop()
    last: OSError = OSError("没有可用的地址")
    for family, kind, proto, _, address in infos:
        sock = socket.socket(family, kind, proto)
        sock.setblocking(False)
        try:
            await loop.sock_connect(sock, address)
        except OSError as error:
            sock.close()
            last = error
            continue
        except BaseException:
            sock.close()
            raise
        return sock, str(address[0])
    raise last


async def _open(target: Target, run: _Run) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """解析、建立连接、加密握手，每一步记下用时。"""
    context = _ssl_context()
    proxy = _proxy()
    if proxy is None:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
        run.lap("dns", "connect")
        sock, run.address = await _connect(infos)
        run.lap("connect", "tls")
        try:
            reader, run.writer = await asyncio.open_connection(
                sock=sock, ssl=context, server_hostname=target.host
            )
        except BaseException:
            sock.close()
            raise
        run.lap("tls", "response")
        return reader, run.writer
    # 经代理：域名由代理解析；“建立连接”是连上代理、再由代理连上平台
    run.via_proxy = True
    run.stage = "connect"
    reader, run.writer = await asyncio.open_connection(*proxy)
    run.writer.write(
        f"CONNECT {target.host}:{target.port} HTTP/1.1\r\n"
        f"Host: {target.host}:{target.port}\r\n\r\n".encode()
    )
    await run.writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    if b" 200" not in head.split(b"\r\n", 1)[0]:
        raise _ProxyRefused("proxy refused")
    run.lap("connect", "tls")
    await run.writer.start_tls(context, server_hostname=target.host)
    run.lap("tls", "response")
    return reader, run.writer


async def _ask(
    target: Target, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, run: _Run
) -> tuple[int, dict[str, str]]:
    """发一次 GET，等到应答头：这段就是“平台应答”的用时。"""
    writer.write(
        (
            f"GET {target.path} HTTP/1.1\r\nHost: {target.host}\r\nUser-Agent: {_AGENT}\r\n"
            "Accept: */*\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    raw = await reader.readuntil(b"\r\n\r\n")
    run.lap("response", "done")
    lines = raw.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {
        name.strip().lower(): value.strip()
        for name, _, value in (line.partition(":") for line in lines[1:] if ":" in line)
    }
    return status, headers


async def _body(reader: asyncio.StreamReader) -> str:
    """读页面的前一段（认拦截页用）：最多 64 KB、最多等 BODY_SECONDS 秒，读到多少算多少。"""
    body = b""
    with contextlib.suppress(OSError, asyncio.IncompleteReadError):
        async with asyncio.timeout(BODY_SECONDS):
            while len(body) < _MAX_BODY:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                body += chunk
    return body.decode("utf-8", "replace")


async def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    writer.close()
    with contextlib.suppress(OSError):
        await asyncio.wait_for(writer.wait_closed(), CLOSE_SECONDS)


async def probe(target: Target) -> dict[str, Any]:
    """访问一次：状态（ok 可用 / blocked 被平台拒绝 / unreachable 连不上）、延迟、各步用时、说明。

    latency_ms 是建立连接的用时（一次网络往返，和 ping 可比）；连不上时为空，
    但已经做完的那几步照样在 timings 里，stage 是卡住的那一步。
    """
    run = _Run()
    try:
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                reader, writer = await _open(target, run)
                status, headers = await _ask(target, reader, writer, run)
        except _FAILURES as error:
            return {
                "state": "unreachable",
                "latency_ms": None,
                "http_status": None,
                "error": _reason(error, run.stage),
                "stage": run.stage,
                **run.detail(),
            }
        body = await _body(reader)
    finally:
        await _close(run.writer)
    state, note = classify(status, headers, body)
    return {
        "state": state,
        "latency_ms": run.timings["connect"],
        "http_status": status,
        "error": note,
        "stage": None,
        **run.detail(),
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
