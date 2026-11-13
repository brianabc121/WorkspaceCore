"""Fail-closed TCP lease gateway; Kubernetes is the sole private identity source."""

import asyncio
import contextlib
import json
import math
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Any, Protocol, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

_PREFIX = "workspace.myworkspace.io/"
_FIELDS = ("workspace_id", "instance_id", "environment_id", "allocation_id")
_API = "/api/v1/internal/runner-network/gateway/"
_LIMIT = 1024 * 1024


def ipv4(value: str) -> IPv4Address:
    address = ip_address(value)
    if not isinstance(address, IPv4Address):
        raise ValueError("IPv6 is unsupported")
    return address


def networks(values: list[str]) -> tuple[IPv4Network, ...]:
    result = tuple(ip_network(value, strict=True) for value in values)
    if any(not isinstance(value, IPv4Network) for value in result):
        raise ValueError("IPv6 is unsupported")
    return result  # type: ignore[return-value]


@dataclass(frozen=True)
class PodIdentity:
    workspace_id: str
    instance_id: str
    environment_id: str
    allocation_id: str
    generation: int
    pod_uid: str
    pod_ip: str
    node_name: str

    @classmethod
    def from_pod(cls, pod: Mapping[str, Any], peer_ip: str) -> Self:
        ipv4(peer_ip)
        metadata, spec, status = (pod.get(key) or {} for key in ("metadata", "spec", "status"))
        labels = metadata.get("labels") or {}
        try:
            identifiers = {
                key: str(UUID(labels[_PREFIX + key.removesuffix("_id")])) for key in _FIELDS
            }
            if any(
                labels[_PREFIX + key.removesuffix("_id")] != UUID(value).hex
                for key, value in identifiers.items()
            ):
                raise ValueError("noncanonical identity label")
            generation = int(labels[_PREFIX + "generation"])
            if generation < 1 or str(generation) != labels[_PREFIX + "generation"]:
                raise ValueError("invalid generation")
            if (
                metadata.get("namespace") != "oc-" + UUID(identifiers["instance_id"]).hex
                or not metadata.get("name")
                or not metadata.get("uid")
                or metadata.get("deletionTimestamp")
                or status.get("podIP") != peer_ip
                or status.get("phase") != "Running"
                or spec.get("hostNetwork", False) is not False
                or not spec.get("nodeName")
                or not any(
                    condition.get("type") == "Ready" and condition.get("status") == "True"
                    for condition in status.get("conditions", [])
                )
            ):
                raise ValueError("pod is not a current ready managed identity")
            return cls(
                **identifiers,
                generation=generation,
                pod_uid=metadata["uid"],
                pod_ip=peer_ip,
                node_name=spec["nodeName"],
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("incomplete managed Pod identity") from error

    def source_body(self) -> dict[str, Any]:
        return {
            **{key: getattr(self, key) for key in _FIELDS},
            "generation": self.generation,
            "pod_uid": self.pod_uid,
        }


class PodResolver(Protocol):
    async def ready(self) -> None: ...
    async def source(self, ip: str) -> dict[str, Any]: ...
    async def target(self, target: Mapping[str, Any]) -> None: ...


class KubectlPodResolver:
    """Fresh exact-IP lookups, bounded subprocess count, deadline and output."""

    def __init__(
        self, kubeconfig: str | None = None, *, timeout: float = 1.0, concurrency: int = 16
    ):
        self.kubeconfig = kubeconfig
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _pods(self, ip: str) -> list[dict[str, Any]]:
        ipv4(ip)
        command = ["kubectl"]
        if self.kubeconfig:
            command += ["--kubeconfig", self.kubeconfig]
        command += [
            "--request-timeout=1s",
            "get",
            "pods",
            "--all-namespaces",
            "--field-selector=status.podIP=" + ip,
            "-o",
            "json",
        ]
        async with self._semaphore, asyncio.timeout(self.timeout):
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            try:
                if process.stdout is None:
                    raise OSError("missing kubectl output")
                chunks, length = [], 0
                while data := await process.stdout.read(65536):
                    length += len(data)
                    if length > _LIMIT:
                        raise ValueError("Kubernetes response exceeds limit")
                    chunks.append(data)
                if await process.wait() != 0:
                    raise OSError("Kubernetes identity lookup failed")
                result = json.loads(b"".join(chunks))
                items = result["items"]
                if not isinstance(items, list):
                    raise ValueError("invalid Kubernetes response")
                return items
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    async def ready(self) -> None:
        # Tests RBAC and current API reachability without granting unknown peers identity.
        await self._pods("0.0.0.0")  # noqa: S104 — lookup, not a bind

    async def source(self, ip: str) -> dict[str, Any]:
        items = await self._pods(ip)
        if len(items) != 1:
            raise ValueError("unknown or ambiguous source Pod")
        return PodIdentity.from_pod(items[0], ip).source_body()

    async def target(self, target: Mapping[str, Any]) -> None:
        items = await self._pods(target["pod_ip"])
        if len(items) != 1:
            raise ValueError("unknown or ambiguous target Pod")
        identity = PodIdentity.from_pod(items[0], target["pod_ip"])
        expected = dict(
            identity.source_body(), pod_ip=identity.pod_ip, node_name=identity.node_name
        )
        if any(target.get(key) != value for key, value in expected.items()):
            raise ValueError("target identity changed")
        if type(target.get("port")) is not int or not 1 <= target["port"] <= 65535:
            raise ValueError("invalid target port")


class GatewayControlClient:
    def __init__(
        self,
        url: str,
        token: SecretStr,
        *,
        ca_file: str | None = None,
        timeout: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ValueError("control URL requires a verified HTTPS origin")
        if not token.get_secret_value().strip() or timeout <= 0 or timeout > 2:
            raise ValueError("invalid gateway credential or timeout")
        self._client = httpx.AsyncClient(
            base_url=url,
            verify=ssl.create_default_context(cafile=ca_file),
            trust_env=False,
            headers={"Authorization": "Bearer " + token.get_secret_value()},
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        self.timeout = timeout

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def _post(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        async with (
            asyncio.timeout(self.timeout),
            self._client.stream("POST", _API + operation, json=body) as response,
        ):
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > _LIMIT:
                    raise ValueError("control response exceeds limit")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("invalid control response")
            return result

    async def ready(self) -> dict[str, Any]:
        return await self._post("ready", {})

    async def config(self) -> dict[str, Any]:
        return await self._post("config", {})

    async def lease(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("lease", body)

    async def ack(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("ack", body)


class Control(Protocol):
    async def ready(self) -> dict[str, Any]: ...
    async def config(self) -> dict[str, Any]: ...
    async def lease(self, body: dict[str, Any]) -> dict[str, Any]: ...
    async def ack(self, body: dict[str, Any]) -> dict[str, Any]: ...


def public_tls_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file, key_file)
    return context


@dataclass
class SocketListener:
    socket: socket.socket
    accepting: asyncio.Task[None]

    def close(self) -> None:
        self.accepting.cancel()
        self.socket.close()

    async def wait_closed(self) -> None:
        await asyncio.gather(self.accepting, return_exceptions=True)


@dataclass
class Listener:
    binding: dict[str, Any]
    server: SocketListener
    sessions: set[asyncio.Task[None]] = field(default_factory=set)


def _release_socket(sock: socket.socket, _task: asyncio.Task[None]) -> None:
    # Also covers a task cancelled before its coroutine executes.
    sock.close()


class Gateway:
    def __init__(
        self,
        control: Control,
        pods: PodResolver,
        *,
        listen_host: str = "0.0.0.0",  # noqa: S104 — operator gateway bind
        public_tls: ssl.SSLContext | None = None,
        denied_cidrs: tuple[str, ...] = (),
        max_connections: int = 256,
        renew_interval: float = 2.0,
        operation_timeout: float = 1.5,
        idle_timeout: float = 60.0,
        max_bindings: int = 1024,
    ):
        ipv4(listen_host)
        if (
            not 0 < renew_interval <= 2
            or not 0 < operation_timeout <= 2
            or not 1 <= max_connections <= 10000
            or not 0 < idle_timeout <= 300
            or not 1 <= max_bindings <= 10000
        ):
            raise ValueError("invalid gateway limits")
        self.control, self.pods = control, pods
        self.listen_host, self.public_tls = listen_host, public_tls
        self.operator_denied = networks(list(denied_cidrs))
        self.denied = self.operator_denied
        self.max_connections, self.max_bindings = max_connections, max_bindings
        self.renew_interval, self.operation_timeout = renew_interval, operation_timeout
        self.idle_timeout = idle_timeout
        self.listeners: dict[tuple[str, str], Listener] = {}
        self._sessions: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def _binding(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("invalid binding")
        value = dict(raw)
        kind, port = value.get("kind"), value.get("port")
        low = {"ingress": 30000, "published": 20000}.get(kind) if isinstance(kind, str) else None
        if (
            low is None
            or type(port) is not int
            or not low <= port < low + 10000
            or not isinstance(value.get("record_id"), str)
            or not value["record_id"]
            or type(value.get("revision")) is not int
            or value["revision"] < 1
            or type(value.get("generation")) is not int
            or value["generation"] < 1
            or type(value.get("active")) is not bool
        ):
            raise ValueError("invalid binding identity or fence")
        networks(value.get("source_cidrs", []))
        if value["active"] and not isinstance(value.get("target"), dict):
            raise ValueError("active binding requires trusted target")
        return value

    async def _stop(self, key: tuple[str, str]) -> None:
        listener = self.listeners.pop(key, None)
        if listener is None:
            return
        listener.server.close()
        tasks = list(listener.sessions)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await listener.server.wait_closed()

    async def close(self) -> None:
        for key in list(self.listeners):
            await self._stop(key)

    async def _ack(self, binding: dict[str, Any], success: bool) -> None:
        async with asyncio.timeout(self.operation_timeout):
            result = await self.control.ack(
                {key: binding[key] for key in ("record_id", "kind", "revision", "generation")}
                | {"success": success, "active": bool(binding["active"] and success)}
            )
            if result.get("accepted") is not True:
                raise ValueError("control rejected binding acknowledgement")

    async def reconcile(self) -> None:
        async with self._lock:
            try:
                async with asyncio.timeout(self.operation_timeout):
                    snapshot = await self.control.config()
                values = snapshot["bindings"]
                if not isinstance(values, list) or len(values) > self.max_bindings:
                    raise ValueError("invalid or excessive bindings")
                bindings = [self._binding(value) for value in values]
                keys = [(value["kind"], value["record_id"]) for value in bindings]
                if len(set(keys)) != len(keys) or len({b["port"] for b in bindings}) != len(
                    bindings
                ):
                    raise ValueError("duplicate binding or port")
                denied = networks(snapshot["denied_cidrs"]) + self.operator_denied
                if denied != self.denied:
                    await self.close()
                self.denied = denied
                for key in set(self.listeners) - set(keys):
                    await self._stop(key)
                # Withdraw every changed/revoked binding before any slow readiness check.
                for key, binding in zip(keys, bindings, strict=True):
                    current = self.listeners.get(key)
                    if current and (
                        current.binding != binding
                        or not binding["active"]
                        or current.server.accepting.done()
                    ):
                        await self._stop(key)
                for key, binding in zip(keys, bindings, strict=True):
                    if not binding["active"]:
                        await self._ack(binding, True)
                        continue
                    try:
                        async with asyncio.timeout(self.operation_timeout):
                            await self.pods.target(binding["target"])
                        if key not in self.listeners:
                            if binding["kind"] == "published" and (
                                self.public_tls is None
                                or not self.operator_denied
                                or not self.denied
                            ):
                                raise ValueError(
                                    "public gateway requires TLS and operator denied CIDRs"
                                )
                            listener_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            try:
                                listener_socket.setsockopt(
                                    socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
                                )
                                listener_socket.bind((self.listen_host, binding["port"]))
                                listener_socket.listen(min(self.max_connections, 128))
                                listener_socket.setblocking(False)
                            except BaseException:
                                listener_socket.close()
                                raise
                            server = SocketListener(
                                listener_socket,
                                asyncio.create_task(self._accept_loop(key, listener_socket)),
                            )
                            self.listeners[key] = Listener(binding, server)
                    except (OSError, ValueError, TimeoutError):
                        await self._stop(key)
                        await self._ack(binding, False)
                        continue
                    await self._ack(binding, True)
                if self.operator_denied:
                    # Target readiness belongs to each binding. One stale target must
                    # not prevent unrelated approved grants from becoming usable.
                    try:
                        async with asyncio.timeout(self.operation_timeout):
                            await self.pods.ready()
                    except (OSError, ValueError, TimeoutError):
                        await self.close()
                        return
                    async with asyncio.timeout(self.operation_timeout):
                        if (await self.control.ready()).get("accepted") is not True:
                            raise ValueError("control rejected gateway readiness")
            except BaseException:
                await self.close()
                raise

    async def _accept_loop(self, key: tuple[str, str], listener_socket: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        while True:
            accepted, _ = await loop.sock_accept(listener_socket)
            listener = self.listeners.get(key)
            if listener is None or len(self._sessions) >= self.max_connections:
                accepted.close()
                await asyncio.sleep(0)
                continue
            # Reserve capacity before starting TLS, including incomplete handshakes.
            task = asyncio.create_task(self._accepted_session(listener.binding, accepted))
            self._sessions.add(task)
            listener.sessions.add(task)
            task.add_done_callback(self._sessions.discard)
            task.add_done_callback(listener.sessions.discard)
            task.add_done_callback(partial(_release_socket, accepted))
            # Keep lease deadlines runnable even under a continuously full accept queue.
            await asyncio.sleep(0)

    async def _accepted_session(self, binding: dict[str, Any], accepted: socket.socket) -> None:
        transport: asyncio.Transport | None = None
        try:
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=65536)
            protocol = asyncio.StreamReaderProtocol(reader)
            kwargs: dict[str, Any] = {}
            if binding["kind"] == "published":
                kwargs = {
                    "ssl": self.public_tls,
                    "ssl_handshake_timeout": 2.0,
                    "ssl_shutdown_timeout": 1.0,
                }
            transport, _ = await loop.connect_accepted_socket(lambda: protocol, accepted, **kwargs)
            writer = asyncio.StreamWriter(transport, protocol, reader, loop)
            await self._session(binding, reader, writer)
        except (OSError, TimeoutError):
            pass
        finally:
            if transport is not None:
                transport.close()
            else:
                accepted.close()

    def _peer(self, binding: dict[str, Any], writer: asyncio.StreamWriter) -> str:
        peer = writer.get_extra_info("peername")
        if not peer:
            raise ValueError("unknown socket peer")
        address = ipv4(peer[0])
        if binding["kind"] == "published":
            if any(address in denied for denied in self.denied):
                raise ValueError("managed source forbidden at public listener")
            if not any(address in allowed for allowed in networks(binding["source_cidrs"])):
                raise ValueError("public source not allowed")
        return str(address)

    async def _authorize(
        self, binding: dict[str, Any], peer: str, source: dict[str, Any] | None
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        body = {"record_id": binding["record_id"], "kind": binding["kind"], "peer_ip": peer}
        if binding["kind"] == "ingress":
            current_source = await self.pods.source(peer)
            if source is not None and current_source != source:
                raise ValueError("source Pod identity changed")
            source = current_source
            body["source"] = source
        started = asyncio.get_running_loop().time()
        lease = await self.control.lease(body)
        seconds = lease.get("lease_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError("invalid lease duration")
        if (
            lease.get("allowed") is not True
            or not math.isfinite(seconds)
            or not 0 < seconds <= 60
            or lease.get("revision") != binding["revision"]
            or lease.get("generation") != binding["generation"]
            or lease.get("target") != binding["target"]
        ):
            raise ValueError("lease denied or binding changed")
        target = lease["target"]
        ipv4(target["pod_ip"])
        await self.pods.target(target)
        expires = started + seconds
        if asyncio.get_running_loop().time() >= expires:
            raise ValueError("lease expired during authorization")
        return target, expires, source

    async def _session(
        self, binding: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream: asyncio.StreamWriter | None = None
        tasks: list[asyncio.Task[None]] = []
        try:
            peer = self._peer(binding, writer)
            async with asyncio.timeout(self.operation_timeout):
                target, expires, source = await self._authorize(binding, peer, None)
            async with asyncio.timeout(
                min(self.operation_timeout, expires - asyncio.get_running_loop().time())
            ):
                target_reader, upstream = await asyncio.open_connection(
                    target["pod_ip"], target["port"], family=socket.AF_INET, limit=65536
                )

            async def renew() -> None:
                nonlocal expires, source
                while True:
                    remaining = expires - asyncio.get_running_loop().time()
                    await asyncio.sleep(min(self.renew_interval, max(remaining, 0)))
                    remaining = expires - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise ValueError("lease expired")
                    async with asyncio.timeout(min(self.operation_timeout, remaining)):
                        _, expires, source = await self._authorize(binding, peer, source)

            async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
                while True:
                    async with asyncio.timeout(self.idle_timeout):
                        data = await src.read(32768)
                        if not data:
                            if dst.can_write_eof():
                                dst.write_eof()
                            else:
                                dst.close()
                            return
                        dst.write(data)
                        await dst.drain()

            async def forward() -> None:
                pumps = [
                    asyncio.create_task(pipe(reader, upstream)),
                    asyncio.create_task(pipe(target_reader, writer)),
                ]
                try:
                    await asyncio.gather(*pumps)
                finally:
                    for pump in pumps:
                        pump.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)

            tasks = [asyncio.create_task(forward()), asyncio.create_task(renew())]
            # One half-close leaves the reverse direction live; lease failure ends both.
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for completed in done:
                completed.result()
        except (OSError, ValueError, TimeoutError, KeyError, TypeError, httpx.HTTPError):
            pass
        finally:
            # Close both transports before awaiting: cancellation can arrive during cleanup.
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (upstream, writer):
                if stream is not None:
                    with contextlib.suppress(OSError, TimeoutError):
                        async with asyncio.timeout(1):
                            await stream.wait_closed()
