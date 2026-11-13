"""Operator-only configuration for the standalone, opt-in controller."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from app.runtime_v2.kubernetes import RuntimeIdentity


@dataclass(frozen=True)
class ControllerConfig:
    kubeconfig: str
    journal: str
    workspace_id: UUID
    host_id: UUID
    node_name: str
    url: str
    token: str = field(repr=False)
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "ControllerConfig":
        def required(name: str) -> str:
            value = environ.get("RUNTIME_V2_" + name, "").strip()
            if not value:
                raise ValueError("missing controller " + name)
            return value

        kubeconfig, journal = required("KUBECONFIG"), required("JOURNAL")
        workspace_id, host_id = UUID(required("WORKSPACE_ID")), UUID(required("HOST_ID"))
        node_name, url, token = required("NODE_NAME"), required("URL"), required("TOKEN")
        if not Path(kubeconfig).is_absolute() or not Path(journal).is_absolute():
            raise ValueError("kubeconfig and journal must be absolute paths")
        RuntimeIdentity(workspace_id, host_id, host_id, host_id, 1, node_name)
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("workspace URL requires HTTPS without embedded credentials")
        _ = parts.port
        optional = {
            name: environ.get("RUNTIME_V2_" + name.upper()) or None
            for name in ("ca_file", "cert_file", "key_file")
        }
        if any(path and not Path(path).is_absolute() for path in optional.values()):
            raise ValueError("TLS files must have absolute paths")
        if bool(optional["cert_file"]) != bool(optional["key_file"]):
            raise ValueError("TLS client certificate and key must be configured together")
        return cls(kubeconfig, journal, workspace_id, host_id, node_name, url, token, **optional)
