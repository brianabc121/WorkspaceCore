#!/usr/bin/env python3
"""Render an offline, IP-only private control relay; never apply or allocate an IP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from pathlib import Path
from typing import Any

NAME = "control-api-private"
NAMESPACE = "runner-system"
PREFIX = "workspace.myworkspace.io/"
LABELS = {PREFIX + "component": "runtime-control-relay"}
NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"
IMAGE = (
    "caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5"
)
REQUIRED = {
    "service_ip",
    "upstream_ip",
    "node_name",
    "node_cidrs",
    "image",
    "tls_secret",
}


def _ip(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("canonical routable IPv4 address required")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("canonical routable IPv4 address required") from exc
    if (
        str(address) != value
        or address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address.is_reserved
        or address.is_link_local
        or int(address) >> 24 == 0
    ):
        raise ValueError("canonical routable IPv4 address required")
    return value


def _name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 253
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
            for part in value.split(".")
        )
    ):
        raise ValueError("valid Kubernetes DNS-subdomain name required")
    return value


def _resource(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": {
            "Deployment": "apps/v1",
            "NetworkPolicy": "networking.k8s.io/v1",
        }.get(kind, "v1"),
        "kind": kind,
        "metadata": {"name": NAME, "namespace": NAMESPACE, "labels": dict(LABELS)},
        **body,
    }


def render(options: dict[str, Any]) -> dict[str, Any]:
    """Return a Kubernetes List from strictly reviewed options, without secret data."""
    if (
        not isinstance(options, dict)
        or not options.keys() >= REQUIRED
        or (options.keys() - REQUIRED - {"upstream_port", "listen_port"})
    ):
        raise ValueError("exact required relay options and no unknown options required")
    service_ip, upstream_ip = _ip(options["service_ip"]), _ip(options["upstream_ip"])
    node_name, tls_secret = _name(options["node_name"]), _name(options["tls_secret"])
    if len(node_name) > 63:
        raise ValueError("node name must fit the protected node label value")
    if options["image"] != IMAGE:
        raise ValueError("reviewed Caddy 2.11.7 image and exact digest required")
    upstream_port = options.get("upstream_port", 8443)
    if type(upstream_port) is not int or not 1 <= upstream_port <= 65535:
        raise ValueError("valid integer upstream port required")
    listen_port = options.get("listen_port", 8443)
    if type(listen_port) is not int or listen_port != 8443:
        raise ValueError("relay listen port must be 8443")
    node_cidrs = options["node_cidrs"]
    if not isinstance(node_cidrs, list) or not node_cidrs:
        raise ValueError("explicit nonempty unique IPv4 node /32 list required")
    checked: list[str] = []
    for cidr in node_cidrs:
        if not isinstance(cidr, str) or not cidr.endswith("/32"):
            raise ValueError("explicit node IPv4 /32 CIDRs required")
        canonical = _ip(cidr[:-3]) + "/32"
        if canonical != cidr or canonical in checked:
            raise ValueError("unique canonical node IPv4 /32 CIDRs required")
        checked.append(canonical)
    caddyfile = f"""{{
    admin off
    auto_https off
    persist_config off
    log {{
        output discard
    }}
    servers {{
        protocols h1 h2
    }}
}}

https://:8443 {{
    tls /relay/tls/server.crt /relay/tls/server.key
    @control path /runner-api/v1/* /healthz /readyz
    handle @control {{
        reverse_proxy https://{upstream_ip}:{upstream_port} {{
            transport http {{
                tls_trust_pool file /relay/tls/ca.crt
                tls_server_name {upstream_ip}
                dial_timeout 5s
                tls_timeout 5s
                response_header_timeout 30s
            }}
        }}
    }}
    handle {{
        respond 404
    }}
}}
"""
    port = {"protocol": "TCP", "port": 8443}
    pod = {
        "automountServiceAccountToken": False,
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "runAsGroup": 1000,
            "fsGroup": 1000,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchFields": [
                                {
                                    "key": "metadata.name",
                                    "operator": "In",
                                    "values": [node_name],
                                }
                            ],
                            "matchExpressions": [
                                {
                                    "key": NODE_LABEL,
                                    "operator": "In",
                                    "values": [node_name],
                                }
                            ],
                        }
                    ],
                }
            }
        },
        "containers": [
            {
                "name": "caddy",
                "image": IMAGE,
                "imagePullPolicy": "IfNotPresent",
                # A plain copy drops the official binary's low-port file capability.
                # Executing the original fails EPERM with capabilities.drop=ALL.
                "command": [
                    "/bin/sh",
                    "-ec",
                    'cp /usr/bin/caddy /tmp/caddy; exec /tmp/caddy "$@"',
                    "caddy",
                ],
                "args": [
                    "run",
                    "--config",
                    "/relay/config/Caddyfile",
                    "--adapter",
                    "caddyfile",
                ],
                "env": [
                    {"name": "XDG_DATA_HOME", "value": "/data"},
                    {"name": "XDG_CONFIG_HOME", "value": "/tmp/config"},  # noqa: S108 — isolated emptyDir
                    {"name": "HOME", "value": "/tmp"},  # noqa: S108 — isolated emptyDir
                ],
                "ports": [{"name": "https", "containerPort": 8443, "protocol": "TCP"}],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "privileged": False,
                    "capabilities": {"drop": ["ALL"]},
                },
                "resources": {
                    "requests": {"cpu": "50m", "memory": "64Mi"},
                    "limits": {"cpu": "500m", "memory": "128Mi"},
                },
                "readinessProbe": {
                    "tcpSocket": {"port": 8443},
                    "periodSeconds": 5,
                    "timeoutSeconds": 2,
                },
                "livenessProbe": {
                    "tcpSocket": {"port": 8443},
                    "periodSeconds": 10,
                    "timeoutSeconds": 2,
                    "initialDelaySeconds": 10,
                },
                "volumeMounts": [
                    {
                        "name": "caddyfile",
                        "mountPath": "/relay/config",
                        "readOnly": True,
                    },
                    {"name": "tls", "mountPath": "/relay/tls", "readOnly": True},
                    {"name": "data", "mountPath": "/data"},
                    {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 — isolated emptyDir
                ],
            }
        ],
        "volumes": [
            {"name": "caddyfile", "configMap": {"name": NAME, "defaultMode": 0o444}},
            {
                "name": "tls",
                "secret": {
                    "secretName": tls_secret,
                    "defaultMode": 0o440,
                    "items": [
                        {"key": key, "path": key} for key in ("server.crt", "server.key", "ca.crt")
                    ],
                },
            },
            {"name": "data", "emptyDir": {"sizeLimit": "16Mi"}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
        ],
    }
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            _resource("ConfigMap", {"data": {"Caddyfile": caddyfile}}),
            _resource(
                "Service",
                {
                    "spec": {
                        "type": "ClusterIP",
                        "clusterIP": service_ip,
                        "ipFamilyPolicy": "SingleStack",
                        "ipFamilies": ["IPv4"],
                        "selector": dict(LABELS),
                        "ports": [{"name": "https", "targetPort": 8443, **port}],
                    }
                },
            ),
            _resource(
                "Deployment",
                {
                    "spec": {
                        "replicas": 1,
                        "strategy": {"type": "Recreate"},
                        "selector": {"matchLabels": dict(LABELS)},
                        "template": {"metadata": {"labels": dict(LABELS)}, "spec": pod},
                    }
                },
            ),
            _resource(
                "NetworkPolicy",
                {
                    "spec": {
                        "podSelector": {"matchLabels": dict(LABELS)},
                        "policyTypes": ["Ingress", "Egress"],
                        "ingress": [
                            {
                                "from": [{"ipBlock": {"cidr": cidr}} for cidr in checked]
                                + [
                                    {
                                        "podSelector": {
                                            "matchLabels": {
                                                PREFIX + "component": "runtime-controller"
                                            }
                                        }
                                    }
                                ],
                                "ports": [dict(port)],
                            }
                        ],
                        "egress": [
                            {
                                "to": [{"ipBlock": {"cidr": upstream_ip + "/32"}}],
                                "ports": [{"protocol": "TCP", "port": upstream_port}],
                            }
                        ],
                    }
                },
            ),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--options", required=True, type=Path, help="reviewed non-secret JSON options"
    )
    args = parser.parse_args()
    try:
        result = render(json.loads(args.options.read_text()))
    except (OSError, ValueError, TypeError):
        parser.exit(2, "invalid control relay options\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
