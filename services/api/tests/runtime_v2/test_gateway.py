"""Real TCP gateway tests; no forwarding mock."""

import asyncio
import contextlib
import copy
import socket
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypedDict, Unpack
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from app.runtime_v2.gateway import Gateway, GatewayControlClient, KubectlPodResolver, PodIdentity

IDS = {
    name: str(UUID(int=i))
    for i, name in enumerate(("workspace_id", "instance_id", "environment_id", "allocation_id"), 1)
}
SOURCE: dict[str, Any] = dict(**IDS, generation=1, pod_uid="current-uid")


def free_port(start: int = 30000) -> int:
    for port in range(start, start + 1000):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise AssertionError("no free port")


class Pods:
    changed = False
    unavailable = False

    async def ready(self) -> None:
        if self.unavailable:
            raise OSError("pods unavailable")

    async def source(self, ip: str) -> dict[str, Any]:
        await self.ready()
        if self.changed:
            raise ValueError("recycled source")
        assert ip == "127.0.0.1"
        return SOURCE.copy()

    async def target(self, target: Mapping[str, Any]) -> None:
        await self.ready()
        if self.changed:
            raise ValueError("recycled target")


class Control:
    allowed = True
    unavailable = False
    lease_seconds: float = 60
    ready_override: Callable[[], Awaitable[dict[str, Any]]] | None = None
    lease_override: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None

    def __init__(self, target_port: int) -> None:
        self.port = free_port()
        self.target: dict[str, Any] = dict(
            **SOURCE, pod_ip="127.0.0.1", node_name="node-a", port=target_port
        )
        self.binding: dict[str, Any] = dict(
            record_id="record",
            kind="ingress",
            port=self.port,
            revision=1,
            generation=1,
            active=True,
            source_cidrs=[],
            target=self.target,
        )
        self.acks: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    async def config(self) -> dict[str, Any]:
        if self.unavailable:
            raise OSError("control unavailable")
        return dict(revision=1, bindings=[copy.deepcopy(self.binding)], denied_cidrs=["10.0.0.0/8"])

    async def lease(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.lease_override is not None:
            return await self.lease_override(body)
        self.requests.append(body)
        if self.unavailable:
            raise OSError("control unavailable")
        return dict(
            allowed=self.allowed,
            lease_seconds=self.lease_seconds,
            revision=1,
            generation=1,
            target=self.target if self.allowed else None,
        )

    async def ready(self) -> dict[str, Any]:
        if self.ready_override is not None:
            return await self.ready_override()
        return {"accepted": True}

    async def ack(self, body: dict[str, Any]) -> dict[str, Any]:
        self.acks.append(body)
        return {"accepted": True}


class GatewayOptions(TypedDict, total=False):
    public_tls: ssl.SSLContext
    denied_cidrs: tuple[str, ...]
    max_connections: int
    operation_timeout: float
    idle_timeout: float


@asynccontextmanager
async def running_gateway(
    **options: Unpack[GatewayOptions],
) -> AsyncIterator[tuple[Gateway, Control, Pods]]:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(32768):
                writer.write(data)
                await writer.drain()
            writer.write_eof()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    control, pods = Control(server.sockets[0].getsockname()[1]), Pods()
    gateway = Gateway(control, pods, listen_host="127.0.0.1", renew_interval=0.03, **options)
    try:
        await gateway.reconcile()
        yield gateway, control, pods
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()


async def connect(control: Control) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", control.port)


async def exchange(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    payload: bytes = b"actual tcp payload",
) -> None:
    writer.write(payload)
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(len(payload)), 1) == payload


@pytest.mark.asyncio
async def test_private_tcp_identity_bidirectional_and_half_close() -> None:
    async with running_gateway() as (_, control, _):
        reader, writer = await connect(control)
        await exchange(reader, writer)
        assert control.requests[0]["source"] == SOURCE
        assert control.requests[0]["peer_ip"] == "127.0.0.1"
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["denied", "control", "pods", "replacement", "expiry", "generation"]
)
async def test_existing_stream_closes_on_lost_authorization(failure: str) -> None:
    async with running_gateway() as (_, control, pods):
        reader, writer = await connect(control)
        await exchange(reader, writer)
        if failure == "denied":
            control.allowed = False
        elif failure == "control":
            control.unavailable = True
        elif failure == "pods":
            pods.unavailable = True
        elif failure == "replacement":
            pods.changed = True
        elif failure == "expiry":
            control.lease_seconds = 0
        else:
            control.target = dict(control.target, generation=2)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_revoke_closes_listener_and_session_before_ack() -> None:
    async with running_gateway() as (gateway, control, _):
        reader, writer = await connect(control)
        await exchange(reader, writer)
        control.binding.update(active=False, revision=2)
        await gateway.reconcile()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert control.acks[-1] == dict(
            record_id="record", kind="ingress", revision=2, generation=1, success=True, active=False
        )
        with pytest.raises(OSError):
            await connect(control)
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_config_outage_closes_all_streams_and_listener() -> None:
    async with running_gateway() as (gateway, control, _):
        reader, writer = await connect(control)
        await exchange(reader, writer)
        control.unavailable = True
        with pytest.raises(OSError):
            await gateway.reconcile()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        with pytest.raises(OSError):
            await connect(control)
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_unknown_source_never_trusts_proxy_headers() -> None:
    async with running_gateway() as (_, control, pods):
        pods.changed = True
        reader, writer = await connect(control)
        writer.write(b"PROXY TCP4 10.0.0.1 127.0.0.1 12345 80\r\n")
        await writer.drain()
        with contextlib.suppress(ConnectionResetError):
            assert await asyncio.wait_for(reader.read(), 1) == b""
        assert not control.requests
        writer.close()
        with contextlib.suppress(ConnectionResetError):
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_established_connection_limit() -> None:
    async with running_gateway(max_connections=1) as (_, control, _):
        reader, writer = await connect(control)
        await exchange(reader, writer)
        other_reader, other_writer = await connect(control)
        assert await asyncio.wait_for(other_reader.read(), 1) == b""
        await exchange(reader, writer, b"alive")
        for stream in (writer, other_writer):
            stream.close()
            await stream.wait_closed()


@pytest.mark.asyncio
async def test_active_ack_requires_live_target() -> None:
    async with running_gateway() as (gateway, control, pods):
        assert control.acks[-1]["success"]
        pods.changed = True
        await gateway.reconcile()
        assert control.acks[-1]["success"] is False
        with pytest.raises(OSError):
            await connect(control)


@pytest.mark.asyncio
async def test_invalid_binding_port_closes_listener() -> None:
    async with running_gateway() as (gateway, control, _):
        control.binding["port"] = 8080
        with pytest.raises(ValueError):
            await gateway.reconcile()
        with pytest.raises(OSError):
            await connect(control)


def pod(ip: str = "127.0.0.1") -> dict[str, Any]:
    labels = {
        "workspace.myworkspace.io/" + field.removesuffix("_id"): UUID(value).hex
        for field, value in IDS.items()
    }
    labels["workspace.myworkspace.io/generation"] = "1"
    return dict(
        metadata=dict(
            name="env-" + UUID(IDS["environment_id"]).hex,
            namespace="oc-" + UUID(IDS["instance_id"]).hex,
            labels=labels,
            uid="current-uid",
        ),
        spec=dict(nodeName="node-a", hostNetwork=False),
        status=dict(podIP=ip, phase="Running", conditions=[dict(type="Ready", status="True")]),
    )


def test_pod_identity_enforces_protected_labels_namespace_uid_and_ready() -> None:
    assert PodIdentity.from_pod(pod(), "127.0.0.1").source_body() == SOURCE
    for failure in ("namespace", "uid", "ready", "ip", "label", "hostnetwork", "deleting"):
        item = pod()
        if failure == "namespace":
            item["metadata"]["namespace"] = "default"
        elif failure == "uid":
            item["metadata"]["uid"] = ""
        elif failure == "ready":
            item["status"]["conditions"] = []
        elif failure == "ip":
            item["status"]["podIP"] = "10.1.1.1"
        elif failure == "label":
            item["metadata"]["labels"].pop("workspace.myworkspace.io/workspace")
        elif failure == "hostnetwork":
            item["spec"]["hostNetwork"] = True
        else:
            item["metadata"]["deletionTimestamp"] = "now"
        with pytest.raises(ValueError):
            PodIdentity.from_pod(item, "127.0.0.1")


@pytest.mark.asyncio
async def test_control_https_independent_bearer_and_contract() -> None:
    with pytest.raises(ValueError):
        GatewayControlClient("http://control.invalid", SecretStr("gateway-secret"))
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"revision": 1, "bindings": [], "denied_cidrs": []})

    async with GatewayControlClient(
        "https://control.invalid",
        SecretStr("gateway-secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.config()
    assert seen[0].url.path == "/api/v1/internal/runner-network/gateway/config"
    assert seen[0].headers["authorization"] == "Bearer gateway-secret"
    assert seen[0].content == b"{}"


@pytest.mark.asyncio
async def test_live_resolver_rejects_duplicate_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def query(self: KubectlPodResolver, ip: str) -> list[dict[str, Any]]:
        return [pod(ip), pod(ip)]

    monkeypatch.setattr(KubectlPodResolver, "_pods", query)
    with pytest.raises(ValueError):
        await KubectlPodResolver().source("127.0.0.1")


def test_gateway_settings_requires_opt_in_tls_public_and_bounded_poll() -> None:
    from app.runtime_v2.gateway_main import GatewaySettings

    with pytest.raises(ValueError):
        GatewaySettings.from_environ({})
    env = {
        "RUNTIME_V2_GATEWAY_ENABLED": "true",
        "RUNTIME_V2_GATEWAY_URL": "https://control.invalid",
        "RUNTIME_V2_GATEWAY_TOKEN": "gateway-secret",
    }
    settings = GatewaySettings.from_environ(env)
    assert settings.poll_interval == 1
    assert "gateway-secret" not in repr(settings)
    with pytest.raises(ValueError):
        GatewaySettings.from_environ(dict(env, RUNTIME_V2_GATEWAY_POLL_INTERVAL="3"))
    with pytest.raises(ValueError):
        GatewaySettings.from_environ(dict(env, RUNTIME_V2_GATEWAY_TLS_CERT="/operator/cert"))


@pytest.fixture
def tls_pair(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from app.runtime_v2.gateway import public_tls_context

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = public_tls_context(str(cert_path), str(key_path))
    assert server.minimum_version >= ssl.TLSVersion.TLSv1_2
    return server, ssl.create_default_context(cafile=str(cert_path))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_cidrs,denied,allowed",
    [
        (["127.0.0.1/32"], ("10.0.0.0/8",), True),
        (["192.0.2.0/24"], ("10.0.0.0/8",), False),
        (["127.0.0.1/32"], ("127.0.0.0/8",), False),
    ],
)
async def test_public_tls_uses_real_socket_allowlist_and_denied_ranges(
    tls_pair: tuple[ssl.SSLContext, ssl.SSLContext],
    source_cidrs: list[str],
    denied: tuple[str, ...],
    allowed: bool,
) -> None:
    server_tls, client_tls = tls_pair
    async with running_gateway(public_tls=server_tls, denied_cidrs=denied) as (gateway, control, _):
        control.port = free_port(20000)
        control.binding.update(kind="published", port=control.port, source_cidrs=source_cidrs)
        await gateway.reconcile()
        reader, writer = await asyncio.open_connection("127.0.0.1", control.port, ssl=client_tls)
        if allowed:
            await exchange(reader, writer, b"X-Forwarded-For: 10.0.0.1\r\n")
            assert "source" not in control.requests[-1]
            assert control.requests[-1]["peer_ip"] == "127.0.0.1"
        else:
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert not control.requests
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_lease_expiry_deadline_wins_over_stalled_renewal() -> None:
    async with running_gateway(operation_timeout=0.2) as (_, control, _):
        control.lease_seconds = 0.1
        reader, writer = await connect(control)
        await exchange(reader, writer)

        async def stalled(body: dict[str, Any]) -> dict[str, Any]:
            await asyncio.Event().wait()
            raise AssertionError("stalled authorization unexpectedly resumed")

        control.lease_override = stalled
        assert await asyncio.wait_for(reader.read(), 0.4) == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_real_tcp_large_payload_backpressure() -> None:
    async with running_gateway() as (_, control, _):
        reader, writer = await connect(control)
        payload = b"bounded-buffer-" * 100000
        sending = asyncio.create_task(exchange(reader, writer, payload))
        await asyncio.wait_for(sending, 3)
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_target_uid_recycling_is_rejected_by_live_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def query(self: KubectlPodResolver, ip: str) -> list[dict[str, Any]]:
        return [pod(ip)]

    monkeypatch.setattr(KubectlPodResolver, "_pods", query)
    target = dict(**SOURCE, pod_ip="127.0.0.1", node_name="node-a", port=8080)
    resolver = KubectlPodResolver()
    await resolver.target(target)
    with pytest.raises(ValueError):
        await resolver.target(dict(target, pod_uid="recycled"))


@pytest.mark.parametrize("invalid", ["::1", "::ffff:127.0.0.1"])
def test_ipv6_sources_and_listeners_are_rejected(invalid: str) -> None:
    from app.runtime_v2.gateway import ipv4

    with pytest.raises(ValueError):
        ipv4(invalid)


@pytest.mark.asyncio
async def test_untrusted_control_server_tls_certificate_rejected(
    tls_pair: tuple[ssl.SSLContext, ssl.SSLContext],
) -> None:
    server_tls, _ = tls_pair
    reached: list[bool] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        reached.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_tls)
    try:
        url = "https://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
        async with GatewayControlClient(url, SecretStr("independent-secret")) as client:
            with pytest.raises(httpx.ConnectError):
                await client.config()
        assert not reached
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [61, float("nan"), float("inf"), True, -1])
async def test_invalid_lease_duration_never_forwards(duration: float) -> None:
    async with running_gateway() as (_, control, _):
        control.lease_seconds = duration
        reader, writer = await connect(control)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_read_side_idle_timeout_closes_existing_stream() -> None:
    async with running_gateway(idle_timeout=0.05) as (_, control, _):
        reader, writer = await connect(control)
        await exchange(reader, writer)
        assert await asyncio.wait_for(reader.read(), 0.5) == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_ready_requires_tls_denied_ranges_and_current_kubernetes(
    tls_pair: tuple[ssl.SSLContext, ssl.SSLContext],
) -> None:
    calls: list[bool] = []

    async def ready() -> dict[str, Any]:
        calls.append(True)
        return {"accepted": True}

    async with running_gateway(public_tls=tls_pair[0], denied_cidrs=("10.0.0.0/8",)) as (
        gateway,
        control,
        pods,
    ):
        control.ready_override = ready
        await gateway.reconcile()
        assert calls == [True]
        pods.unavailable = True
        await gateway.reconcile()
        assert calls == [True]


@pytest.mark.asyncio
async def test_unready_target_does_not_block_gateway_permission_readiness(
    tls_pair: tuple[ssl.SSLContext, ssl.SSLContext],
) -> None:
    calls: list[bool] = []

    async def ready() -> dict[str, Any]:
        calls.append(True)
        return {"accepted": True}

    async with running_gateway(public_tls=tls_pair[0], denied_cidrs=("10.0.0.0/8",)) as (
        gateway,
        control,
        pods,
    ):
        control.ready_override = ready
        pods.changed = True  # target identity changed; Kubernetes itself is still reachable
        await gateway.reconcile()
        assert control.acks[-1]["success"] is False
        assert not gateway.listeners
        assert calls == [True]


@pytest.mark.asyncio
async def test_control_ready_posts_empty_body_and_requires_gateway_auth() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"accepted": True})

    async with GatewayControlClient(
        "https://control.invalid",
        SecretStr("gateway-secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await client.ready() == {"accepted": True}
    assert seen[0].url.path == "/api/v1/internal/runner-network/gateway/ready"
    assert seen[0].headers["authorization"] == "Bearer gateway-secret"
    assert seen[0].content == b"{}"


def test_pod_identity_accepts_real_deployment_pod_name_suffix() -> None:
    item = pod()
    item["metadata"]["name"] += "-7c8dfd89fb-h7qkv"
    assert PodIdentity.from_pod(item, "127.0.0.1").source_body() == SOURCE


@pytest.mark.asyncio
async def test_tls_handshakes_count_toward_connection_cap(
    tls_pair: tuple[ssl.SSLContext, ssl.SSLContext],
) -> None:

    async with running_gateway(
        public_tls=tls_pair[0], denied_cidrs=("10.0.0.0/8",), max_connections=1
    ) as (gateway, control, _):
        control.port = free_port(20000)
        control.binding.update(kind="published", port=control.port, source_cidrs=["127.0.0.1/32"])
        await gateway.reconcile()
        _, stalled = await connect(control)
        await asyncio.sleep(0.03)
        with pytest.raises((OSError, ssl.SSLError)):
            await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", control.port, ssl=tls_pair[1]), 1
            )
        stalled.close()
        await stalled.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("operator_denied,expected", [((), 0), (("10.0.0.0/8",), 1)])
async def test_private_only_readiness_requires_operator_denied_ranges(
    operator_denied: tuple[str, ...],
    expected: int,
) -> None:
    calls: list[bool] = []

    async def ready() -> dict[str, Any]:
        calls.append(True)
        return {"accepted": True}

    async with running_gateway(denied_cidrs=operator_denied) as (gateway, control, _):
        control.ready_override = ready
        await gateway.reconcile()
        assert len(calls) == expected


@pytest.mark.asyncio
async def test_readiness_control_outage_withdraws_existing_connections() -> None:
    async with running_gateway(denied_cidrs=("10.0.0.0/8",)) as (gateway, control, _):
        reader, writer = await connect(control)
        await exchange(reader, writer)

        async def outage() -> dict[str, Any]:
            raise OSError("control unavailable")

        control.ready_override = outage
        with pytest.raises(OSError):
            await gateway.reconcile()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        with pytest.raises(OSError):
            await connect(control)
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["bounded-output", "bounded-time", "fresh-identity"])
async def test_kubectl_subprocess_is_bounded_and_does_not_cache_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import json
    import os
    import sys

    executable = tmp_path / "kubectl"
    payload = tmp_path / "pods.json"
    payload.write_text(json.dumps({"items": [pod()]}))
    if mode == "bounded-output":
        script = 'import sys\nsys.stdout.write("x" * (2 * 1024 * 1024))\n'
    elif mode == "bounded-time":
        script = "import time\ntime.sleep(60)\n"
    else:
        script = f"import pathlib\nprint(pathlib.Path({str(payload)!r}).read_text())\n"
    executable.write_text(f"#!{sys.executable}\n" + script)
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    resolver = KubectlPodResolver(timeout=0.2)
    if mode == "fresh-identity":
        assert await resolver.source("127.0.0.1") == SOURCE
        changed = pod()
        changed["metadata"]["uid"] = "new-current-uid"
        payload.write_text(json.dumps({"items": [changed]}))
        assert (await resolver.source("127.0.0.1"))["pod_uid"] == "new-current-uid"
    else:
        with pytest.raises((ValueError, TimeoutError)):
            await asyncio.wait_for(resolver.source("127.0.0.1"), 1)
