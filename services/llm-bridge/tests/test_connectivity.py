"""到平台的连通性怎么测、延迟怎么算（T-0278），一个域名有几个地址时怎么连（T-0284）。

用本机的 TLS 服务扮演平台：应答慢、正文慢慢吐、一直不应答、经 HTTPS 代理。
要看的是：延迟只算建立连接（和 ping 可比），每一步分开计时，读正文和关连接不计入，
超时时写明卡在哪一步；第一个地址不应答时像官方程序一样很快换下一个，并列出试过的每个地址。
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import ssl
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bridge import connectivity

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[object]]


def _certificate(folder: Path) -> tuple[Path, Path]:
    """给 127.0.0.1 签一张自签证书。"""
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class Platform:
    """扮演平台的本机 HTTPS 服务；handler 决定它怎么应答。"""

    def __init__(self, tmp_path: Path) -> None:
        self.cert, self.key = _certificate(tmp_path)
        self.stop = asyncio.Event()
        self.servers: list[asyncio.Server] = []

    async def serve(self, handler: Handler, *, tls: bool = True) -> int:
        context = None
        if tls:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(self.cert, self.key)

        async def guarded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await handler(reader, writer)
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(guarded, "127.0.0.1", 0, ssl=context)
        self.servers.append(server)
        port: int = server.sockets[0].getsockname()[1]
        return port

    async def close(self) -> None:
        self.stop.set()
        for server in self.servers:
            server.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(server.wait_closed(), 2)


@pytest.fixture
async def platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Platform]:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    item = Platform(tmp_path)
    monkeypatch.setattr(
        connectivity, "_ssl_context", lambda: ssl.create_default_context(cafile=str(item.cert))
    )
    monkeypatch.setattr(connectivity, "TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(connectivity, "BODY_SECONDS", 0.3)
    monkeypatch.setattr(connectivity, "CLOSE_SECONDS", 0.2)
    yield item
    await item.close()


async def test_latency_is_the_connect_time_and_each_stage_is_timed(platform: Platform) -> None:
    """平台过 0.4 秒才应答、正文一直吐不完：延迟仍只算建立连接，平台应答单独计时，读正文不计入。"""

    async def slow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.sleep(0.4)
        writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n\r\n")
        writer.write(b'{"error": "missing api key", "more": "')
        await writer.drain()
        await platform.stop.wait()  # 不关连接，也不把正文写完

    port = await platform.serve(slow)
    started = time.monotonic()
    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", "/v1/models", port))
    elapsed = time.monotonic() - started

    assert (result["state"], result["http_status"], result["error"]) == ("ok", 401, None)
    timings = result["timings"]
    assert set(timings) == {"dns_ms", "connect_ms", "tls_ms", "response_ms"}
    assert all(isinstance(value, int) for value in timings.values())
    # 延迟是连上的那个地址建立连接的用时；只试了一个地址，和“建立连接”这一步差不多
    assert result["latency_ms"] <= timings["connect_ms"] < 300
    assert 350 <= timings["response_ms"] < 1500  # 等平台应答的 0.4 秒算在这里
    assert result["stage"] is None
    assert (result["address"], result["via_proxy"]) == ("127.0.0.1", False)
    assert result["attempts"] == [
        {"address": "127.0.0.1", "family": 4, "ms": result["latency_ms"], "error": None}
    ]
    # 正文吐不完时最多再读 BODY_SECONDS，不会一直等到超时被判成连不上
    assert elapsed < 0.4 + 0.3 + 0.2 + 0.8


async def test_a_block_page_sent_in_pieces_is_still_recognised(platform: Platform) -> None:
    async def block(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\n\r\n<p>Unable to ")
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(b"load site</p>")
        await writer.drain()

    port = await platform.serve(block)
    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", port=port))
    assert (result["state"], result["http_status"]) == ("blocked", 403)
    assert "拦截页" in result["error"]
    assert result["latency_ms"] <= result["timings"]["connect_ms"]


async def test_a_timeout_names_the_stage_it_got_stuck_in(platform: Platform) -> None:
    """连上了、握手也做完了，平台就是不应答：报“平台没有应答”，前面几步的用时照样给。"""

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        await platform.stop.wait()

    port = await platform.serve(silent)
    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", port=port))
    assert (result["state"], result["latency_ms"], result["http_status"]) == (
        "unreachable",
        None,
        None,
    )
    assert result["stage"] == "response"
    assert result["error"].startswith("平台没有应答")
    assert "共等了 2 秒" in result["error"]
    timings = result["timings"]
    assert timings["response_ms"] is None
    assert all(isinstance(timings[k], int) for k in ("dns_ms", "connect_ms", "tls_ms"))


async def test_a_refused_connection_says_which_stage(platform: Platform) -> None:
    port = await platform.serve(lambda r, w: platform.stop.wait())
    for server in platform.servers:
        server.close()
        await server.wait_closed()
    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", port=port))
    assert (result["state"], result["stage"]) == ("unreachable", "connect")
    assert result["error"] == "连接被拒绝（在建立连接这一步）"
    assert isinstance(result["timings"]["dns_ms"], int) and result["timings"]["connect_ms"] is None


async def test_through_a_proxy_the_proxy_resolves_and_connect_includes_it(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    """经 HTTPS 代理：域名由代理解析（没有解析这一步），建立连接包括代理连上平台。"""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()

    target_port = await platform.serve(answer)
    seen: list[bytes] = []

    async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head.split(b"\r\n", 1)[0])
        if not head.startswith(f"CONNECT 127.0.0.1:{target_port} ".encode()):
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            await writer.drain()
            return
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", target_port)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            with contextlib.suppress(OSError):
                while chunk := await src.read(65536):
                    dst.write(chunk)
                    await dst.drain()
            dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    proxy_port = await platform.serve(proxy, tls=False)
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy_port}")

    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", port=target_port))
    assert (result["state"], result["http_status"], result["via_proxy"]) == ("ok", 404, True)
    assert result["timings"]["dns_ms"] is None and result["address"] is None
    assert result["latency_ms"] == result["timings"]["connect_ms"]
    assert isinstance(result["timings"]["tls_ms"], int)
    assert isinstance(result["timings"]["response_ms"], int)

    refused = await connectivity.probe(connectivity.Target("example.invalid", "别处", port=443))
    assert (refused["state"], refused["stage"]) == ("unreachable", "connect")
    assert refused["error"] == "HTTPS 代理拒绝连到这个地址"
    assert seen[-1] == b"CONNECT example.invalid:443 HTTP/1.1"


def _info(address: str, port: int, family: int = socket.AF_INET) -> tuple[Any, ...]:
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))


@pytest.fixture
async def addresses(monkeypatch: pytest.MonkeyPatch) -> Any:
    """让域名解析出给定的几个地址；hang 里的地址连了也不应答（像不通的 IPv6）。"""
    loop = asyncio.get_running_loop()
    connect = loop.sock_connect

    def use(infos: list[tuple[Any, ...]], hang: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        async def resolve(*_: Any, **__: Any) -> list[tuple[Any, ...]]:
            return infos

        async def sock_connect(sock: socket.socket, address: Any) -> None:
            if address[0] in hang:
                await asyncio.sleep(3600)
            await connect(sock, address)

        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(loop, "sock_connect", sock_connect)

    return use


async def answer_404(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.readuntil(b"\r\n\r\n")
    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
    await writer.drain()


async def test_a_silent_first_address_is_not_waited_for(platform: Platform, addresses: Any) -> None:
    """第一个地址不应答（服务器的 IPv6 不通时就是这样）：0.25 秒后同时试下一个，连上就算可用。
    以前一直等这一个到超时，报“连不上”，官方程序却照常能用。"""
    port = await platform.serve(answer_404)
    addresses([_info("192.0.2.1", port), _info("127.0.0.1", port)], hang={"192.0.2.1"})
    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", port=port))
    assert (result["state"], result["http_status"], result["address"]) == ("ok", 404, "127.0.0.1")
    first, second = result["attempts"]
    assert (first["address"], first["error"]) == ("192.0.2.1", "没有回应")
    assert (second["address"], second["error"]) == ("127.0.0.1", None)
    # 延迟只算连上的那个地址；“建立连接”这一步含等第一个地址的 0.25 秒
    assert result["latency_ms"] == second["ms"] < 200
    assert result["timings"]["connect_ms"] >= 240


async def test_a_refused_address_moves_on_at_once(platform: Platform, addresses: Any) -> None:
    """第一个地址马上拒绝：不用等 0.25 秒，立刻试下一个。"""
    port = await platform.serve(answer_404)
    addresses([_info("127.0.0.2", port), _info("127.0.0.1", port)])
    result = await connectivity.probe(connectivity.Target("127.0.0.1", "本机", port=port))
    assert result["state"] == "ok"
    assert [a["error"] for a in result["attempts"]] == ["拒绝连接", None]
    assert result["timings"]["connect_ms"] < 200


async def test_when_no_address_answers_every_attempt_is_listed(
    platform: Platform, addresses: Any
) -> None:
    """哪个地址都不应答：报建立连接超时，试过的地址都列出来（页面上能看出是不是 IPv6 的问题）。"""
    addresses(
        [
            _info("2001:db8::1", 443, socket.AF_INET6),
            _info("192.0.2.1", 443),
            _info("192.0.2.2", 443),
        ],
        hang={"2001:db8::1", "192.0.2.1", "192.0.2.2"},
    )
    result = await connectivity.probe(connectivity.Target("example.test", "别处"))
    assert (result["state"], result["stage"]) == ("unreachable", "connect")
    assert result["error"].startswith("建立连接超时")
    attempts = result["attempts"]
    assert [(a["address"], a["family"]) for a in attempts] == [
        ("2001:db8::1", 6),
        ("192.0.2.1", 4),
        ("192.0.2.2", 4),
    ]
    # 没开 IPv6 的机器上建不了 IPv6 的连接：记下来，接着试下一个，不会整个失败
    assert attempts[0]["error"] in ("没有回应", "这台服务器没开 IPv6")
    assert [a["error"] for a in attempts[1:]] == ["没有回应", "没有回应"]
    assert all(isinstance(a["ms"], int) for a in attempts)


def test_addresses_alternate_between_ipv6_and_ipv4() -> None:
    """IPv6 / IPv4 交替着试（RFC 8305），解析结果里排第一的那一类在前；重复的地址只试一次。"""
    infos = [
        _info("2001:db8::1", 443, socket.AF_INET6),
        _info("2001:db8::2", 443, socket.AF_INET6),
        _info("2001:db8::1", 443, socket.AF_INET6),
        _info("192.0.2.1", 443),
        _info("192.0.2.2", 443),
    ]
    order = [info[4][0] for info in connectivity._interleave(infos)]
    assert order == ["2001:db8::1", "192.0.2.1", "2001:db8::2", "192.0.2.2"]


async def test_slow_name_resolution_has_its_own_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """域名解析单独限时：超时时写的是解析等了多久。"""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setattr(connectivity, "DNS_SECONDS", 1.0)
    loop = asyncio.get_running_loop()

    async def never(*_: Any, **__: Any) -> list[Any]:
        await asyncio.sleep(3600)
        return []

    monkeypatch.setattr(loop, "getaddrinfo", never)
    result = await connectivity.probe(connectivity.Target("example.test", "别处"))
    assert (result["state"], result["stage"]) == ("unreachable", "dns")
    assert result["error"] == "域名解析超时：没有解析出平台的地址（共等了 1 秒）"
    assert result["attempts"] == []
