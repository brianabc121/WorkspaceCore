"""Publish exact approved TCP ports using fixed, UID-fenced gateway objects."""

import asyncio
import copy
import json
from ipaddress import IPv4Network
from typing import Any
from uuid import UUID

from app.runtime_v2.gateway import Control
from app.runtime_v2.kubernetes import normalized_policy
from app.runtime_v2.runner import Kubectl, OutcomeUnknown

PREFIX = "workspace.myworkspace.io/"
LABELS = {PREFIX + "component": "network-gateway"}
NAMESPACE = "runner-system"
DISABLED = [{"name": "disabled", "port": 65535, "targetPort": 65535, "protocol": "TCP"}]


class GatewayPortPublisher:
    def __init__(self, api: Kubectl) -> None:
        self.api = api

    def _read(self, kind: str, name: str) -> dict[str, Any]:
        obj = self.api.get_optional(kind, name, NAMESPACE)
        meta = (obj or {}).get("metadata") or {}
        if (
            not obj
            or meta.get("name") != name
            or meta.get("namespace") != NAMESPACE
            or any((meta.get("labels") or {}).get(k) != v for k, v in LABELS.items())
        ):
            raise ValueError("gateway object identity mismatch")
        if not meta.get("uid") or not str(meta.get("resourceVersion", "")).isdigit():
            raise OutcomeUnknown("gateway object receipt missing")
        return obj

    def _patch(self, kind: str, name: str, obj: dict[str, Any], changes: dict[str, Any]) -> None:
        observed_spec = normalized_policy(obj["spec"]) if kind == "networkpolicy" else obj["spec"]
        if all(observed_spec.get(k) == v for k, v in changes.items()):
            return
        meta = obj["metadata"]
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": meta["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
        ]
        patch.extend(
            {"op": "add", "path": "/spec/" + key, "value": value} for key, value in changes.items()
        )
        self.api.call(
            ["patch", kind, name, "-n", NAMESPACE, "--type=json", "--patch", json.dumps(patch)]
        )
        observed = self._read(kind, name)
        observed_spec = (
            normalized_policy(observed["spec"]) if kind == "networkpolicy" else observed["spec"]
        )
        if observed["metadata"]["uid"] != meta["uid"] or any(
            observed_spec.get(k) != v for k, v in changes.items()
        ):
            raise OutcomeUnknown("gateway routing update awaiting observation")

    def reconcile(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(snapshot)
        values = snapshot.get("bindings")
        if not isinstance(values, list) or sum(b.get("active") is True for b in values) > 100:
            raise ValueError("gateway supports at most 100 reviewed port bindings")
        private = self._read("service", "runner-gateway")
        public = self._read("service", "runner-gateway-public")
        public_ready = bool((public.get("status") or {}).get("loadBalancer", {}).get("ingress"))
        if not public_ready:
            for binding in values:
                if binding["kind"] == "published":
                    binding["active"] = False
        policy = self._read("networkpolicy", "runner-gateway-routes")
        for obj, kind in ((private, "ClusterIP"), (public, "LoadBalancer")):
            spec = obj["spec"]
            if (
                spec.get("type") != kind
                or spec.get("selector") != LABELS
                or spec.get("externalIPs")
                or spec.get("externalName")
                or any(p.get("nodePort") for p in spec.get("ports") or [])
            ):
                raise ValueError("unsafe gateway Service")
        if (
            public["spec"].get("externalTrafficPolicy") != "Local"
            or public["spec"].get("allocateLoadBalancerNodePorts") is not False
        ):
            raise ValueError("public gateway must preserve original source without NodePorts")
        if policy["spec"].get("podSelector") != {"matchLabels": LABELS}:
            raise ValueError("gateway policy identity mismatch")
        groups: dict[str, list[dict[str, Any]]] = {"ingress": [], "published": []}
        incoming: list[dict[str, Any]] = []
        outgoing: list[dict[str, Any]] = []
        sources: set[str] = set()
        seen: set[int] = set()
        blocked = [IPv4Network(n, strict=True) for n in snapshot["denied_cidrs"]]
        if not blocked or any(n.version != 4 for n in blocked):
            raise ValueError("explicit managed IPv4 network exclusions required")
        for binding in values:
            if binding.get("active") is not True:
                continue
            kind, port = binding["kind"], binding["port"]
            if (
                kind not in groups
                or type(port) is not int
                or not (30000 <= port <= 39999 if kind == "ingress" else 20000 <= port <= 29999)
                or port in seen
            ):
                raise ValueError("invalid gateway port")
            seen.add(port)
            target = binding["target"]
            labels = {
                PREFIX + field: UUID(str(target[field + "_id"])).hex
                for field in ("workspace", "instance", "environment", "allocation")
            }
            if (
                type(target["generation"]) is not int
                or target["generation"] < 1
                or type(target["port"]) is not int
                or not 1 <= target["port"] <= 65535
            ):
                raise ValueError("invalid destination identity")
            labels[PREFIX + "generation"] = str(target["generation"])
            outgoing.append(
                {
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    PREFIX + "workspace": labels[PREFIX + "workspace"],
                                    PREFIX + "instance": labels[PREFIX + "instance"],
                                }
                            },
                            "podSelector": {"matchLabels": labels},
                        }
                    ],
                    "ports": [{"port": target["port"], "protocol": "TCP"}],
                }
            )
            groups[kind].append(
                {"name": "tcp-" + str(port), "port": port, "targetPort": port, "protocol": "TCP"}
            )
            if kind == "ingress":
                source_id = UUID(str(binding["source_instance_id"])).hex
                peer = {
                    "namespaceSelector": {
                        "matchLabels": {
                            PREFIX + "workspace": labels[PREFIX + "workspace"],
                            PREFIX + "instance": source_id,
                        }
                    },
                    "podSelector": {
                        "matchLabels": {
                            PREFIX + "workspace": labels[PREFIX + "workspace"],
                            PREFIX + "instance": source_id,
                        }
                    },
                }
                incoming.append({"from": [peer], "ports": [{"port": port, "protocol": "TCP"}]})
            else:
                peers = []
                for value in binding["source_cidrs"]:
                    net = IPv4Network(value, strict=True)
                    if net.version != 4 or net.prefixlen == 0:
                        raise ValueError("explicit public IPv4 sources required")
                    if any(net.subnet_of(deny) for deny in blocked):
                        continue
                    excepts = [str(deny) for deny in blocked if deny.subnet_of(net)]
                    peers.append(
                        {"ipBlock": {"cidr": str(net), **({"except": excepts} if excepts else {})}}
                    )
                    sources.add(str(net))
                if not peers:
                    raise ValueError("public sources are entirely forbidden")
                incoming.append({"from": peers, "ports": [{"port": port, "protocol": "TCP"}]})
        self._patch(
            "networkpolicy",
            "runner-gateway-routes",
            policy,
            {"policyTypes": ["Ingress", "Egress"], "ingress": incoming, "egress": outgoing},
        )
        self._patch("service", "runner-gateway", private, {"ports": groups["ingress"] or DISABLED})
        self._patch(
            "service",
            "runner-gateway-public",
            public,
            {
                "ports": groups["published"] or DISABLED,
                "loadBalancerSourceRanges": sorted(sources) or ["0.0.0.0/32"],
            },
        )
        return snapshot


class RoutedControl:
    """Production control transport with routing observation before gateway ACKs."""

    def __init__(self, control: Control, publisher: GatewayPortPublisher) -> None:
        self.control, self.publisher = control, publisher
        self.snapshot: dict[str, Any] | None = None

    async def refresh(self) -> None:
        self.snapshot = None
        snapshot = await self.control.config()
        self.snapshot = await asyncio.to_thread(self.publisher.reconcile, snapshot)

    async def config(self) -> dict[str, Any]:
        if self.snapshot is None:
            raise ValueError("gateway routes have not been observed")
        return self.snapshot

    async def lease(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.control.lease(body)

    async def ack(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.control.ack(body)

    async def ready(self) -> dict[str, Any]:
        return await self.control.ready()
