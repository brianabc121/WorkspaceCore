"""Fail-closed Kubernetes workload primitives for the managed runner pool.

This module is deliberately not registered as a runner backend until storage,
network enforcement, command receipts, and node identity have been verified.
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

_PREFIX = "workspace.myworkspace.io/"
_NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"
_IMAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[a-f0-9]{64}$")
_NODE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class UnsupportedOperation(RuntimeError):
    """The adapter cannot safely provide this legacy operation yet."""


@dataclass(frozen=True)
class RuntimeIdentity:
    workspace_id: UUID
    instance_id: UUID
    environment_id: UUID
    allocation_id: UUID
    generation: int
    node_name: str

    def __post_init__(self) -> None:
        if self.generation < 1 or not _NODE.fullmatch(self.node_name):
            raise ValueError("invalid generation or node identity")

    @property
    def namespace(self) -> str:
        return "oc-" + self.instance_id.hex

    @property
    def pod_name(self) -> str:
        return "env-" + self.environment_id.hex

    @property
    def labels(self) -> dict[str, str]:
        return {
            _PREFIX + "workspace": self.workspace_id.hex,
            _PREFIX + "instance": self.instance_id.hex,
            _PREFIX + "environment": self.environment_id.hex,
            _PREFIX + "allocation": self.allocation_id.hex,
            _PREFIX + "generation": str(self.generation),
        }


class KubernetesRuntime:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    @staticmethod
    def pod_manifest(
        identity: RuntimeIdentity, image: str, cpu_millis: int, memory_mib: int
    ) -> dict[str, Any]:
        if not _IMAGE.fullmatch(image) or not (
            1 <= cpu_millis <= 64000 and 1 <= memory_mib <= 1048576
        ):
            raise ValueError("invalid immutable image or resource limits")
        limits = {"cpu": f"{cpu_millis}m", "memory": f"{memory_mib}Mi"}
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": identity.pod_name,
                "namespace": identity.namespace,
                "labels": identity.labels,
            },
            "spec": {
                "automountServiceAccountToken": False,
                "hostNetwork": False,
                "hostPID": False,
                "hostIPC": False,
                "restartPolicy": "Never",
                "affinity": {
                    "nodeAffinity": {
                        "requiredDuringSchedulingIgnoredDuringExecution": {
                            "nodeSelectorTerms": [
                                {
                                    "matchFields": [
                                        {
                                            "key": "metadata.name",
                                            "operator": "In",
                                            "values": [identity.node_name],
                                        }
                                    ],
                                    "matchExpressions": [
                                        {
                                            "key": _NODE_LABEL,
                                            "operator": "In",
                                            "values": [identity.node_name],
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                },
                "securityContext": {
                    "runAsNonRoot": True,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "environment",
                        "image": image,
                        "resources": {"requests": limits.copy(), "limits": limits.copy()},
                        "securityContext": {
                            "privileged": False,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            },
        }

    @staticmethod
    def network_policy(identity: RuntimeIdentity) -> dict[str, Any]:
        """Allow only managed pods of the same identity within its namespace.

        The deployment must prohibit other policies and tenant label mutation.
        This policy intentionally provides neither DNS nor public egress.
        """
        scope = {
            _PREFIX + "workspace": identity.workspace_id.hex,
            _PREFIX + "instance": identity.instance_id.hex,
        }
        peer = {"podSelector": {"matchLabels": scope}}
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "runner-domain", "namespace": identity.namespace},
            "spec": {
                "podSelector": {"matchLabels": scope},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [peer]}],
                "egress": [{"to": [peer]}],
            },
        }

    async def inspect(self, identity: RuntimeIdentity) -> dict[str, Any]:
        path = f"/api/v1/namespaces/{identity.namespace}/pods/{identity.pod_name}"
        response = await self._client.get(path, timeout=10.0)
        response.raise_for_status()
        pod = response.json()
        metadata = pod.get("metadata") or {}
        labels = metadata.get("labels") or {}
        if any(labels.get(key) != value for key, value in identity.labels.items()):
            raise ValueError("pod identity mismatch")
        spec, status = pod.get("spec") or {}, pod.get("status") or {}
        uid, node = metadata.get("uid"), spec.get("nodeName")
        ready = bool(
            uid
            and node == identity.node_name
            and status.get("phase") == "Running"
            and any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in status.get("conditions") or []
            )
        )
        return {
            "pod_uid": uid,
            "node": node,
            "pod_ip": status.get("podIP"),
            "generation": identity.generation,
            "allocation_id": str(identity.allocation_id),
            "ready": ready,
        }

    async def execute(
        self, kind: str, payload: dict[str, Any], identity: RuntimeIdentity
    ) -> dict[str, Any]:
        # No command is acknowledged as successful until its semantics and fencing exist.
        raise UnsupportedOperation(f"Kubernetes command {kind!r} is not safely supported")
