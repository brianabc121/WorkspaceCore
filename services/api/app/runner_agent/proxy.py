"""环境的出口代理（docs/v2/RUNNER_PLAN.md 第 8.3 节）。

每个环境一个 internal 网络，直连外网不通；runner-agent 以别名 `egress` 连进这个网络，
并在它在这个网络里的地址上单独监听 3128：**从哪个监听口进来就是哪个环境**，不靠来源地址判断。

- 网络选项：none 全部拒绝；allowlist 只放行列表里的域名（支持 `*.example.com`）；
  all 放行任何公网地址。
- 先解析域名，**只连检查过的公网 IP**（防止 DNS 重绑定）；私有网段、本机、链路本地（含云厂商元数据
  169.254.169.254）、组播、保留地址，以及配置里的工作台与 OpenClaw 地址一律拒绝。
- 只支持 HTTP 代理（绝对 URL）与 CONNECT（HTTPS 等）；不转发 SMTP（25 端口）。
- 记录每个目标（域名:端口）的次数、字节数、放行与否，随对账上报工作台。
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import select
import socket
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("runner_agent.proxy")

HEAD_LIMIT = 64 * 1024
IDLE_SECONDS = 300
CONNECT_TIMEOUT = 15
BLOCKED_PORTS = frozenset({25})


@dataclass
class Policy:
    mode: str  # none / allowlist / all
    domains: list[str] = field(default_factory=list)


def domain_allowed(host: str, domains: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in domains:
        entry = entry.lower().rstrip(".")
        if entry.startswith("*."):
            base = entry[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == entry:
            return True
    return False


def ip_allowed(
    value: str, blocked: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        return False
    return not any(ip in network for network in blocked)


def parse_blocked(text: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """MYWS_RUNNER_BLOCK：逗号分隔的地址或网段（工作台、OpenClaw 的公网地址）。"""
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            try:
                for info in socket.getaddrinfo(part, None):
                    out.append(ipaddress.ip_network(str(info[4][0])))
            except OSError:
                logger.warning("无法解析要拦截的地址 %s", part)
    return out


def split_target(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port = target.rpartition(":") if target.count(":") == 1 else (target, "", "")
        if not host:
            host, port = target, ""
    return host, int(port) if port.isdigit() else default_port


def _stop(server: socket.socket) -> None:
    """关掉监听：先 shutdown，阻塞在 accept() 的线程才返回、端口才释放（只 close 不够，实测）。"""
    with contextlib.suppress(OSError):
        server.shutdown(socket.SHUT_RDWR)
    server.close()


class Stats:
    """每个环境、每个目标的访问次数与字节数（上报后清零）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[tuple[str, int, bool], list[int]]] = {}

    def add(self, env_id: str, host: str, port: int, allowed: bool, size: int) -> None:
        with self._lock:
            entry = self._data.setdefault(env_id, {}).setdefault((host, port, allowed), [0, 0])
            entry[0] += 1
            entry[1] += size

    def drain(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            data, self._data = self._data, {}
        return {
            env_id: [
                {"host": h[:253], "port": p, "allowed": a, "count": c, "bytes": b}
                for (h, p, a), (c, b) in sorted(items.items(), key=lambda kv: -kv[1][1])[:100]
            ]
            for env_id, items in data.items()
        }


class EgressProxy:
    def __init__(
        self,
        blocked: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
        *,
        port: int = 3128,
        resolver: Any = None,
    ) -> None:
        self.blocked = blocked
        self.port = port
        self.stats = Stats()
        self._resolver = resolver or socket.getaddrinfo
        self._lock = threading.Lock()
        self._listeners: dict[str, tuple[socket.socket, str]] = {}  # env_id → (监听, 地址)
        self._policies: dict[str, Policy] = {}

    # ---------------------------------------------------------------- 监听
    def set_policy(self, env_id: str, policy: Policy) -> None:
        with self._lock:
            self._policies[env_id] = policy

    def listen(self, env_id: str, address: str, policy: Policy) -> None:
        """在 runner-agent 在这个环境网络里的地址上监听；已经在听同一个地址就只更新策略。"""
        self.set_policy(env_id, policy)
        with self._lock:
            current = self._listeners.get(env_id)
            if current is not None and current[1] == address:
                return
            if current is not None:
                _stop(current[0])
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            server = socket.socket(family, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((address, self.port))
            server.listen(64)
            self._listeners[env_id] = (server, address)
        threading.Thread(target=self._accept, args=(env_id, server), daemon=True).start()

    def close(self, env_id: str) -> None:
        with self._lock:
            current = self._listeners.pop(env_id, None)
            self._policies.pop(env_id, None)
        if current is not None:
            _stop(current[0])

    def envs(self) -> set[str]:
        with self._lock:
            return set(self._listeners)

    def _accept(self, env_id: str, server: socket.socket) -> None:
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(env_id, client), daemon=True).start()

    # ---------------------------------------------------------------- 处理一个连接
    def _serve(self, env_id: str, client: socket.socket) -> None:
        client.settimeout(30)
        try:
            read = self._read_head(client)
            if read is None:
                return
            head, extra = read
            line, _, rest = head.partition(b"\r\n")
            parts = line.decode("latin-1").split(" ")
            if len(parts) != 3:
                self._reply(client, 400, "Bad Request")
                return
            method, target, version = parts
            if method == "CONNECT":
                host, port = split_target(target, 443)
                upstream = self._open(env_id, host, port, client)
                if upstream is None:
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self._pipe(env_id, host, port, client, upstream, extra)
                return
            if not target.lower().startswith("http://"):
                self._reply(client, 400, "Only absolute http:// URLs or CONNECT")
                return
            authority, _, path = target[7:].partition("/")
            host, port = split_target(authority, 80)
            upstream = self._open(env_id, host, port, client)
            if upstream is None:
                return
            headers = [
                h
                for h in rest.split(b"\r\n")
                if h and not h.lower().startswith((b"proxy-", b"connection:"))
            ]
            request = (
                f"{method} /{path} {version}\r\n".encode("latin-1")
                + b"".join(h + b"\r\n" for h in headers)
                + b"Connection: close\r\n\r\n"
                + extra
            )
            self._pipe(env_id, host, port, client, upstream, request)
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        finally:
            client.close()

    def _read_head(self, client: socket.socket) -> tuple[bytes, bytes] | None:
        """读到请求头结束；返回 (请求头, 已经读到的后续数据)。"""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                return None
            data += chunk
            if len(data) > HEAD_LIMIT:
                self._reply(client, 431, "Request Header Fields Too Large")
                return None
        head, _, extra = data.partition(b"\r\n\r\n")
        return head, extra

    def _reply(self, client: socket.socket, status: int, reason: str, detail: str = "") -> None:
        """状态行只用 ASCII（客户端按 latin-1 显示）；中文说明放在正文里。"""
        body = f"{status} {reason}\n{detail}\n（工作台出口代理）\n".encode()
        with contextlib.suppress(OSError):
            client.sendall(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + body
            )

    def check(self, env_id: str, host: str, port: int) -> tuple[str | None, str]:
        """返回 (要连接的 IP, 拒绝原因)；拒绝时 IP 为 None。"""
        with self._lock:
            policy = self._policies.get(env_id)
        if policy is None or policy.mode == "none":
            return None, "这个环境没有开网络"
        if port in BLOCKED_PORTS or not 0 < port < 65536:
            return None, f"不转发 {port} 端口"
        host = host.strip().lower().rstrip(".")
        if not host or len(host) > 253:
            return None, "目标不合法"
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if policy.mode == "allowlist" and (
            literal is not None or not domain_allowed(host, policy.domains)
        ):
            return None, f"{host} 不在这个环境的域名列表里"
        try:
            infos = self._resolver(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return None, f"解析不了 {host}"
        addresses = [str(info[4][0]) for info in infos]
        if not addresses:
            return None, f"解析不了 {host}"
        # 任何一个地址不是公网就整体拒绝（防止 DNS 重绑定与混合记录）
        if not all(ip_allowed(a, self.blocked) for a in addresses):
            return None, f"{host} 指向私有或受保护的地址"
        return addresses[0], ""

    def _open(
        self, env_id: str, host: str, port: int, client: socket.socket
    ) -> socket.socket | None:
        address, reason = self.check(env_id, host, port)
        if address is None:
            self.stats.add(env_id, host, port, False, 0)
            if reason.startswith("解析不了"):
                self._reply(client, 502, "Bad Gateway: cannot resolve host", reason)
            else:
                self._reply(client, 403, "Forbidden by workspace egress policy", reason)
            return None
        try:
            upstream = socket.create_connection((address, port), timeout=CONNECT_TIMEOUT)
        except OSError:
            self.stats.add(env_id, host, port, True, 0)
            self._reply(client, 502, "Bad Gateway")
            return None
        return upstream

    def _pipe(
        self,
        env_id: str,
        host: str,
        port: int,
        client: socket.socket,
        upstream: socket.socket,
        first: bytes,
    ) -> None:
        total = 0
        try:
            if first:
                upstream.sendall(first)
                total += len(first)
            sockets = [client, upstream]
            last = time.monotonic()
            while time.monotonic() - last < IDLE_SECONDS:
                readable, _, _ = select.select(sockets, [], [], 30)
                if not readable:
                    continue
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    other = upstream if sock is client else client
                    other.sendall(data)
                    total += len(data)
                    last = time.monotonic()
        except OSError:
            return
        finally:
            upstream.close()
            self.stats.add(env_id, host, port, True, total)


__all__ = [
    "EgressProxy",
    "Policy",
    "Stats",
    "domain_allowed",
    "ip_allowed",
    "parse_blocked",
    "split_target",
]
