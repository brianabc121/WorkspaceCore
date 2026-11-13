"""Standalone opt-in gateway: python -m app.runtime_v2.gateway_main."""

import asyncio
import contextlib
import logging
import math
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.runtime_v2.gateway import (
    Gateway,
    GatewayControlClient,
    KubectlPodResolver,
    ipv4,
    networks,
    public_tls_context,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewaySettings:
    url: str
    token: SecretStr
    listen_host: str = "0.0.0.0"  # noqa: S104 — dedicated operator-managed gateway
    kubeconfig: str | None = None
    ca_file: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None
    denied_cidrs: tuple[str, ...] = ()
    max_connections: int = 256
    max_bindings: int = 1024
    poll_interval: float = 1.0

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "GatewaySettings":
        prefix = "RUNTIME_V2_GATEWAY_"
        if environ.get(prefix + "ENABLED") != "true":
            raise ValueError("gateway requires explicit operator enablement")

        def value(key: str, default: str = "") -> str:
            return environ.get(prefix + key, default).strip()

        url, token = value("URL"), value("TOKEN")
        parts = urlsplit(url)
        if (
            not token
            or parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ValueError("gateway requires independent token and HTTPS control origin")
        _ = parts.port
        optional = {
            key.lower(): value(key) or None
            for key in ("KUBECONFIG", "CA_FILE", "TLS_CERT", "TLS_KEY")
        }
        if any(path and not Path(path).is_absolute() for path in optional.values()):
            raise ValueError("gateway credential paths must be absolute")
        denied = tuple(item.strip() for item in value("DENIED_CIDRS").split(",") if item.strip())
        networks(list(denied))
        if bool(optional["tls_cert"]) != bool(optional["tls_key"]):
            raise ValueError("public TLS certificate and key must be configured together")
        if optional["tls_cert"] and not denied:
            raise ValueError("public gateway requires all managed node/pod/platform denied CIDRs")
        host = value("LISTEN_HOST", "0.0.0.0")  # noqa: S104 — operator bind
        ipv4(host)
        maximum = int(value("MAX_CONNECTIONS", "256"))
        bindings = int(value("MAX_BINDINGS", "1024"))
        poll = float(value("POLL_INTERVAL", "1"))
        if (
            not 1 <= maximum <= 10000
            or not 1 <= bindings <= 10000
            or not math.isfinite(poll)
            or not 0 < poll <= 2
        ):
            raise ValueError("invalid gateway capacity or poll interval")
        return cls(
            url=url,
            token=SecretStr(token),
            listen_host=host,
            denied_cidrs=denied,
            max_connections=maximum,
            max_bindings=bindings,
            poll_interval=poll,
            **optional,
        )


async def serve(settings: GatewaySettings, stopping: asyncio.Event | None = None) -> None:
    from app.runtime_v2.gateway_ports import GatewayPortPublisher, RoutedControl
    from app.runtime_v2.runner import Kubectl, KubernetesCommandError, OutcomeUnknown

    stopping = stopping or asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopping.set)
    tls = (
        public_tls_context(settings.tls_cert, settings.tls_key)
        if (settings.tls_cert and settings.tls_key)
        else None
    )
    async with GatewayControlClient(
        settings.url, settings.token, ca_file=settings.ca_file
    ) as control:
        api = Kubectl(settings.kubeconfig, timeout=3)
        await asyncio.to_thread(api.validate_connection)
        routed = RoutedControl(control, GatewayPortPublisher(api))
        gateway = Gateway(
            routed,
            KubectlPodResolver(settings.kubeconfig),
            listen_host=settings.listen_host,
            public_tls=tls,
            denied_cidrs=settings.denied_cidrs,
            max_connections=settings.max_connections,
            max_bindings=settings.max_bindings,
        )
        try:
            while not stopping.is_set():
                try:
                    await routed.refresh()
                    await gateway.reconcile()
                except (
                    OSError,
                    ValueError,
                    TimeoutError,
                    KeyError,
                    TypeError,
                    httpx.HTTPError,
                    KubernetesCommandError,
                    OutcomeUnknown,
                ):
                    await gateway.close()
                    # Exception text may contain control URL credentials or backend bodies.
                    logger.warning("gateway dependency unavailable; listeners and sessions closed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), settings.poll_interval)
        finally:
            await gateway.close()
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx request logging includes URLs; the gateway emits only static operator messages.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = GatewaySettings.from_environ(os.environ)
        asyncio.run(serve(settings))
    except (ValueError, OSError):
        logger.error("gateway disabled or invalid operator configuration")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
