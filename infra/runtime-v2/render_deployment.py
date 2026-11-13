#!/usr/bin/env python3
"""Render reviewed gateway objects offline; never applies or acknowledges bindings."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

PREFIX = "workspace.myworkspace.io/"
LABELS = {PREFIX + "component": "network-gateway"}
NAME = "runner-gateway"
NAMESPACE = "runner-system"
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[a-f0-9]{64}")


def networks(values: Any) -> list[ipaddress.IPv4Network]:
    if not isinstance(values, list) or not values:
        raise ValueError("nonempty explicit IPv4 CIDRs required")
    result = [ipaddress.ip_network(value, strict=True) for value in values]
    if any(not isinstance(value, ipaddress.IPv4Network) for value in result):
        raise ValueError("IPv6 is unsupported")
    return result  # type: ignore[return-value]


def resource(kind: str, spec: dict[str, Any], name: str = NAME) -> dict[str, Any]:
    version = {"Deployment": "apps/v1", "NetworkPolicy": "networking.k8s.io/v1"}.get(kind, "v1")
    return {
        "apiVersion": version,
        "kind": kind,
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": LABELS},
        **spec,
    }


def render(snapshot: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Input snapshot must come from the authenticated gateway /config endpoint."""
    if not IMAGE.fullmatch(options["image"]):
        raise ValueError("operator runtime image must be pinned by sha256 digest")
    origin = urlsplit(options["control_url"])
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        raise ValueError("verified HTTPS control origin required")
    if snapshot.get("revision") != 1 or type(snapshot.get("revision")) is not int:
        raise ValueError("unsupported control configuration revision")
    for key in ("credential_secret", "tls_secret", "ca_secret"):
        if not isinstance(options[key], str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", options[key]
        ):
            raise ValueError("existing Secret names required")
    pod_cidrs = networks(options["pod_cidrs"])
    denied = (
        networks(options["node_cidrs"])
        + pod_cidrs
        + networks(options["platform_cidrs"])
        + networks(snapshot["denied_cidrs"])
    )
    control_cidrs, api_cidrs = (
        networks(options["control_cidrs"]),
        networks(options["api_cidrs"]),
    )
    api_port = options.get("api_port", 6443)
    if type(api_port) is not int or not 1 <= api_port <= 65535:
        raise ValueError("invalid API port")
    bindings = snapshot["bindings"]
    if not isinstance(bindings, list) or len(bindings) > 100:
        raise ValueError("at most 100 reviewed service ports supported by this renderer")
    groups: dict[str, list[dict[str, Any]]] = {"ingress": [], "published": []}
    seen_ports, seen_records = set(), set()
    egress = []
    public_ingress = []
    for binding in bindings:
        kind, port = binding["kind"], binding["port"]
        record = str(UUID(binding["record_id"]))
        low = {"ingress": 30000, "published": 20000}.get(kind)
        if (
            low is None
            or type(port) is not int
            or not low <= port < low + 10000
            or type(binding["active"]) is not bool
            or any(
                type(binding[key]) is not int or binding[key] < 1
                for key in ("revision", "generation")
            )
        ):
            raise ValueError("invalid binding fence, type or port")
        if port in seen_ports or (kind, record) in seen_records:
            raise ValueError("duplicate binding port or identity")
        seen_ports.add(port)
        seen_records.add((kind, record))
        if not binding["active"]:
            continue
        target = binding["target"]
        target_ip = ipaddress.ip_address(target["pod_ip"])
        if not isinstance(target_ip, ipaddress.IPv4Address) or not any(
            target_ip in network for network in pod_cidrs
        ):
            raise ValueError("target must be a managed IPv4 Pod")
        if (
            type(target["port"]) is not int
            or not 1 <= target["port"] <= 65535
            or not target.get("pod_uid")
            or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", target.get("node_name", "")
            )
        ):
            raise ValueError("current trusted target required")
        labels = {
            PREFIX + key: UUID(target[key + "_id"]).hex
            for key in ("workspace", "instance", "environment", "allocation")
        }
        if type(target["generation"]) is not int or target["generation"] < 1:
            raise ValueError("invalid target generation")
        labels[PREFIX + "generation"] = str(target["generation"])
        egress.append(
            {
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {
                                "kubernetes.io/metadata.name": "oc-"
                                + UUID(target["instance_id"]).hex
                            }
                        },
                        "podSelector": {"matchLabels": labels},
                    }
                ],
                "ports": [{"protocol": "TCP", "port": target["port"]}],
            }
        )
        if kind == "published":
            allowed = networks(binding["source_cidrs"])
            blocks = []
            for network in allowed:
                if any(network.subnet_of(blocked) for blocked in denied):
                    raise ValueError("public allowlist is entirely within a denied range")
                exceptions = sorted(
                    {str(blocked) for blocked in denied if blocked.subnet_of(network)}
                )
                blocks.append(
                    {
                        "ipBlock": {
                            "cidr": str(network),
                            **({"except": exceptions} if exceptions else {}),
                        }
                    }
                )
            public_ingress.append({"from": blocks, "ports": [{"protocol": "TCP", "port": port}]})
        groups[kind].append(
            {
                "name": "tcp-" + str(port),
                "protocol": "TCP",
                "port": port,
                "targetPort": port,
            }
        )
    annotation = {
        PREFIX + "reviewed-config-sha256": hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    }
    env = [
        {"name": "RUNTIME_V2_GATEWAY_" + key, "value": value}
        for key, value in {
            "ENABLED": "true",
            "URL": options["control_url"],
            "CA_FILE": "/gateway/ca/ca.crt",
            "TLS_CERT": "/gateway/tls/tls.crt",
            "TLS_KEY": "/gateway/tls/tls.key",
            "DENIED_CIDRS": ",".join(sorted({str(value) for value in denied})),
            "LISTEN_HOST": "0.0.0.0",  # noqa: S104 — isolated gateway listener
            "MAX_BINDINGS": "100",
        }.items()
    ]
    env.append(
        {
            "name": "RUNTIME_V2_GATEWAY_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": options["credential_secret"], "key": "token"}},
        }
    )
    env.extend(
        [{"name": "HOME", "value": "/tmp"}, {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}]
    )
    objects = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NAMESPACE,
                "labels": {
                    PREFIX + "managed": "true",
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "v1.35",
                },
            },
        },
        resource("ServiceAccount", {"automountServiceAccountToken": True}),
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": NAME + "-identity"},
            "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]}],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": NAME + "-identity"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": NAME + "-identity",
            },
            "subjects": [{"kind": "ServiceAccount", "name": NAME, "namespace": NAMESPACE}],
        },
        resource(
            "Deployment",
            {
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": LABELS},
                    "template": {
                        "metadata": {"labels": LABELS, "annotations": annotation},
                        "spec": {
                            "serviceAccountName": NAME,
                            "hostNetwork": False,
                            "hostPID": False,
                            "hostIPC": False,
                            "securityContext": {
                                "runAsNonRoot": True,
                                "runAsUser": 1000,
                                "runAsGroup": 1000,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [
                                {
                                    "name": "gateway",
                                    "image": options["image"],
                                    "command": [
                                        "python",
                                        "-m",
                                        "app.runtime_v2.gateway_main",
                                    ],
                                    "env": env,
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "128Mi"},
                                        "limits": {"cpu": "2", "memory": "512Mi"},
                                    },
                                    "volumeMounts": [
                                        {"name": "tmp", "mountPath": "/tmp"},
                                        {
                                            "name": "ca",
                                            "mountPath": "/gateway/ca",
                                            "readOnly": True,
                                        },
                                        {
                                            "name": "tls",
                                            "mountPath": "/gateway/tls",
                                            "readOnly": True,
                                        },
                                    ],
                                }
                            ],
                            "volumes": [
                                {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                                {
                                    "name": "ca",
                                    "secret": {"secretName": options["ca_secret"]},
                                },
                                {
                                    "name": "tls",
                                    "secret": {"secretName": options["tls_secret"]},
                                },
                            ],
                        },
                    },
                }
            },
        ),
    ]
    private_ports = groups["ingress"]
    ingress = list(public_ingress)
    if private_ports:
        objects.append(
            resource(
                "Service",
                {
                    "spec": {
                        "type": "ClusterIP",
                        "ipFamilyPolicy": "SingleStack",
                        "ipFamilies": ["IPv4"],
                        "selector": LABELS,
                        "ports": private_ports,
                    }
                },
            )
        )
        ingress.append(
            {
                "from": [
                    {
                        "namespaceSelector": {
                            "matchExpressions": [
                                {"key": PREFIX + key, "operator": "Exists"}
                                for key in ("workspace", "instance")
                            ]
                        },
                        "podSelector": {
                            "matchExpressions": [
                                {"key": PREFIX + "environment", "operator": "Exists"}
                            ]
                        },
                    }
                ],
                "ports": [{"protocol": "TCP", "port": value["port"]} for value in private_ports],
            }
        )
    if groups["published"]:
        objects.append(
            resource(
                "Service",
                {
                    "spec": {
                        "type": "LoadBalancer",
                        "allocateLoadBalancerNodePorts": False,
                        "externalTrafficPolicy": "Local",
                        "ipFamilyPolicy": "SingleStack",
                        "ipFamilies": ["IPv4"],
                        "selector": LABELS,
                        "ports": groups["published"],
                        "loadBalancerSourceRanges": sorted(
                            {
                                cidr
                                for binding in bindings
                                if binding["active"] and binding["kind"] == "published"
                                for cidr in binding["source_cidrs"]
                            }
                        ),
                    }
                },
                NAME + "-public",
            )
        )
    egress.extend(
        [
            {
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                        },
                        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                    }
                ],
                "ports": [{"protocol": protocol, "port": 53} for protocol in ("TCP", "UDP")],
            },
            {
                "to": [{"ipBlock": {"cidr": str(cidr)}} for cidr in control_cidrs],
                "ports": [{"protocol": "TCP", "port": origin.port or 443}],
            },
            {
                "to": [{"ipBlock": {"cidr": str(cidr)}} for cidr in api_cidrs],
                "ports": [{"protocol": "TCP", "port": api_port}],
            },
        ]
    )
    objects.append(
        resource(
            "NetworkPolicy",
            {
                "spec": {
                    "podSelector": {"matchLabels": LABELS},
                    "policyTypes": ["Ingress", "Egress"],
                    "ingress": [],
                    "egress": egress[-3:],
                }
            },
            NAME + "-control",
        )
    )
    # Fixed names and inert bootstrap ports let the restricted gateway update
    # exact approved routes without permission to create arbitrary Services.
    for name, kind in ((NAME, "ClusterIP"), (NAME + "-public", "LoadBalancer")):
        if not any(obj["kind"] == "Service" and obj["metadata"]["name"] == name for obj in objects):
            objects.append(
                resource(
                    "Service",
                    {
                        "spec": {
                            "type": kind,
                            "ipFamilyPolicy": "SingleStack",
                            "ipFamilies": ["IPv4"],
                            "selector": LABELS,
                            "ports": [
                                {
                                    "name": "disabled",
                                    "port": 65535,
                                    "targetPort": 65535,
                                    "protocol": "TCP",
                                }
                            ],
                            **(
                                {
                                    "externalTrafficPolicy": "Local",
                                    "allocateLoadBalancerNodePorts": False,
                                    "loadBalancerSourceRanges": ["0.0.0.0/32"],
                                }
                                if kind == "LoadBalancer"
                                else {}
                            ),
                        }
                    },
                    name,
                )
            )
    objects.append(
        resource(
            "NetworkPolicy",
            {
                "spec": {
                    "podSelector": {"matchLabels": LABELS},
                    "policyTypes": ["Ingress", "Egress"],
                    "ingress": ingress,
                    "egress": egress[:-3],
                }
            },
            NAME + "-routes",
        )
    )
    objects.extend(
        [
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": NAME + "-routes", "namespace": NAMESPACE},
                "rules": [
                    {
                        "apiGroups": [""],
                        "resources": ["services"],
                        "resourceNames": [NAME, NAME + "-public"],
                        "verbs": ["get", "patch"],
                    },
                    {
                        "apiGroups": ["networking.k8s.io"],
                        "resources": ["networkpolicies"],
                        "resourceNames": [NAME + "-routes"],
                        "verbs": ["get", "patch"],
                    },
                ],
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": NAME + "-routes", "namespace": NAMESPACE},
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": NAME + "-routes",
                },
                "subjects": [{"kind": "ServiceAccount", "name": NAME, "namespace": NAMESPACE}],
            },
        ]
    )
    return {"apiVersion": "v1", "kind": "List", "items": objects}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-snapshot", required=True, type=Path)
    parser.add_argument("--options", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = render(
            json.loads(args.config_snapshot.read_text()),
            json.loads(args.options.read_text()),
        )
    except (KeyError, TypeError, ValueError, OSError):
        parser.error("invalid reviewed snapshot/options; no deployment rendered")
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
