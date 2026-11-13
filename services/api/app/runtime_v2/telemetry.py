"""Fail-closed node samples from the verified API and kubelet stats summary.

The supported storage profile is node-local PVs on the node filesystem. Missing
measurements (including detached PVCs) refuse the whole sample, never imply zero.
Kubernetes lifecycle reconciliation remains fenced by the controller's UID receipts.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.runner_agent import VERSION
from app.runner_agent.client import Workspace
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl, KubernetesCommandError, OutcomeUnknown, verify_identity
from app.schemas.runner import RunnerReport

_PREFIX = "workspace.myworkspace.io/"
_NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"
_ERRORS = (
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
    ArithmeticError,
    OSError,
    KubernetesCommandError,
    OutcomeUnknown,
)


class TelemetryUnavailable(RuntimeError):
    """A complete, trusted host sample cannot currently be produced."""


def _uint(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1 << 56:
        raise TelemetryUnavailable("missing or invalid node measurement")
    return value


def _fresh(stats: dict[str, Any]) -> None:
    sampled = datetime.fromisoformat(stats["time"].replace("Z", "+00:00"))
    if sampled.tzinfo is None or not -5 <= (datetime.now(UTC) - sampled).total_seconds() <= 60:
        raise TelemetryUnavailable("node measurement stale or untrusted")


def _quantity(value: str) -> Decimal:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(m|Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?", value)
    if match is None:
        raise TelemetryUnavailable("unsupported allocatable quantity")
    suffix = match[2] or ""
    factors: dict[str, Decimal] = {"": Decimal(1), "m": Decimal("0.001")}
    factors.update({unit + "i": Decimal(1024**power) for power, unit in enumerate("KMGTPE", 1)})
    factors.update({unit: Decimal(1000**power) for power, unit in enumerate("kMGTPE", 1)})
    result = Decimal(match[1]) * factors[suffix]
    if not 0 < result <= 1 << 56:
        raise TelemetryUnavailable("invalid allocatable quantity")
    return result


def _identity(obj: dict[str, Any], node_name: str) -> RuntimeIdentity:
    meta = obj["metadata"]
    labels = meta["labels"]
    identity = RuntimeIdentity(
        workspace_id=UUID(labels[_PREFIX + "workspace"]),
        instance_id=UUID(labels[_PREFIX + "instance"]),
        environment_id=UUID(labels[_PREFIX + "environment"]),
        allocation_id=UUID(labels[_PREFIX + "allocation"]),
        generation=int(labels[_PREFIX + "generation"]),
        node_name=node_name,
    )
    verify_identity(obj, identity)
    if meta["namespace"] != identity.namespace or not meta.get("uid"):
        raise TelemetryUnavailable("managed object namespace or UID unavailable")
    return identity


def _pinned(affinity: dict[str, Any], node_name: str) -> bool:
    terms = affinity.get("nodeSelectorTerms")
    if not isinstance(terms, list) or not terms:
        return False
    return all(
        any(
            field.get("key") == "metadata.name"
            and field.get("operator") == "In"
            and field.get("values") == [node_name]
            for field in term.get("matchFields", [])
        )
        for term in terms
    )


class NodeTelemetry:
    def __init__(self, api: Kubectl, node_name: str) -> None:
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", node_name) is None:
            raise ValueError("invalid configured node")
        self.api, self.node_name = api, node_name

    def _node(self) -> dict[str, Any]:
        node = self.api.get_optional("node", self.node_name)
        meta = (node or {}).get("metadata") or {}
        if meta.get("name") != self.node_name or not meta.get("uid"):
            raise TelemetryUnavailable("configured Node identity unavailable")
        return node or {}

    @staticmethod
    def _physical(node: dict[str, Any]) -> str:
        machine_id = ((node.get("status") or {}).get("nodeInfo") or {}).get("machineID")
        if not isinstance(machine_id, str):
            raise TelemetryUnavailable("Node machine identity unavailable")
        normalized = machine_id.strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", normalized) is None:
            raise TelemetryUnavailable("Node machine identity unavailable")
        return hashlib.sha256(("machine-id:" + normalized).encode("ascii")).hexdigest()

    def physical_host_id(self) -> str:
        return self._physical(self._node())

    def _node_sample(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        node = self._node()
        self._physical(node)
        if node["metadata"].get("labels", {}).get(_NODE_LABEL) != self.node_name:
            raise TelemetryUnavailable("protected Node label unavailable")
        self.api.validate_connection()
        runtime = self.api.get_optional("runtimeclass", "gvisor")
        if not runtime or runtime.get("handler") != "runsc":
            raise TelemetryUnavailable("gVisor runtime class unavailable")
        summary = json.loads(
            self.api.call(["get", "--raw", f"/api/v1/nodes/{self.node_name}/proxy/stats/summary"])
        )
        stats = summary["node"]
        if stats["nodeName"] != self.node_name:
            raise TelemetryUnavailable("kubelet summary Node mismatch")
        for key in ("cpu", "memory", "fs"):
            _fresh(stats[key])
        allocatable = node["status"]["allocatable"]
        cpus = int(_quantity(allocatable["cpu"]))
        memory = int(_quantity(allocatable["memory"]))
        disk = _uint(stats["fs"]["capacityBytes"])
        free_disk = _uint(stats["fs"]["availableBytes"])
        free_memory = _uint(stats["memory"]["availableBytes"])
        load = _uint(stats["cpu"]["usageNanoCores"]) / 1_000_000_000
        if cpus < 1 or memory < 1 or disk < 1 or free_disk > disk:
            raise TelemetryUnavailable("invalid available node capacity")
        capacity = {
            "cpus": cpus,
            "mem_bytes": memory,
            "disk_bytes": disk,
            "disk_free_bytes": free_disk,
            "mem_free_bytes": min(memory, free_memory),
            "load": load,
        }
        return node, summary, capacity

    def capacity(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "agent_version": VERSION,
            "docker_version": None,
            "runtimes": [],
            "kvm": False,
            "cpus": None,
            "mem_bytes": None,
            "disk_bytes": None,
            "disk_free_bytes": None,
            "wait_seconds": 25,
        }
        try:
            _, _, capacity = self._node_sample()
        except (*_ERRORS, TelemetryUnavailable):
            return base
        return {
            **base,
            **{
                key: capacity[key] for key in ("cpus", "mem_bytes", "disk_bytes", "disk_free_bytes")
            },
            "runtimes": ["runsc"],
        }

    def _list(self, kind: str, *args: str) -> list[dict[str, Any]]:
        result = json.loads(self.api.call(["get", kind, "--all-namespaces", *args, "-o", "json"]))
        items = result["items"]
        if (
            not isinstance(items, list)
            or len(items) > 2000
            or not all(isinstance(item, dict) for item in items)
        ):
            raise TelemetryUnavailable("node inventory unavailable or unbounded")
        return items

    def _workloads(self, summary: dict[str, Any]) -> tuple[list[Any], list[Any], int]:
        pods = self._list("pods", "--field-selector", f"spec.nodeName={self.node_name}")
        deployments = self._list("deployments", "-l", _PREFIX + "environment")
        own: dict[str, tuple[RuntimeIdentity, dict[str, Any]]] = {}
        for deployment in deployments:
            template = deployment["spec"]["template"]
            affinity = (
                template["spec"]
                .get("affinity", {})
                .get("nodeAffinity", {})
                .get("requiredDuringSchedulingIgnoredDuringExecution", {})
            )
            if not _pinned(affinity, self.node_name):
                continue
            identity = _identity(deployment, self.node_name)
            verify_identity(template, identity)
            namespace = self.api.get_optional("namespace", identity.namespace)
            labels = ((namespace or {}).get("metadata") or {}).get("labels") or {}
            if any(
                labels.get(_PREFIX + key) != value
                for key, value in (
                    ("workspace", identity.workspace_id.hex),
                    ("instance", identity.instance_id.hex),
                )
            ):
                raise TelemetryUnavailable("managed namespace identity unavailable")
            key = str(identity.environment_id)
            if key in own:
                raise TelemetryUnavailable("ambiguous managed deployment")
            own[key] = identity, deployment
        # Retained PVCs survive Deployments. An orphan on this node must not
        # disappear from the usage total merely because its Pod no longer runs.
        own_claims: dict[str, dict[str, Any]] = {}
        for listed_claim in self._list("pvc", "-l", _PREFIX + "environment"):
            volume_name = listed_claim.get("spec", {}).get("volumeName")
            if not volume_name:
                # The deployment loop below rejects an unbound own-node claim.
                continue
            pv = self.api.get_optional("pv", volume_name)
            if pv is None:
                raise TelemetryUnavailable("PVC volume binding unavailable")
            affinity = pv.get("spec", {}).get("nodeAffinity", {}).get("required", {})
            if not _pinned(affinity, self.node_name):
                continue
            identity = _identity(listed_claim, self.node_name)
            key = str(identity.environment_id)
            if key not in own or own[key][0] != identity or key in own_claims:
                raise TelemetryUnavailable("retained PVC ownership or usage unknown")
            own_claims[key] = listed_claim
        containers, volumes = [], []
        unknown = 0
        seen = set()
        for pod in pods:
            labels = pod.get("metadata", {}).get("labels", {})
            if (
                pod.get("metadata", {}).get("namespace") == "runner-system"
                and labels.get(_PREFIX + "component") == "network-gateway"
                and not any(
                    _PREFIX + key in labels
                    for key in ("workspace", "instance", "environment", "allocation", "generation")
                )
            ):
                continue  # infrastructure use is included in the node free-capacity sample
            if not any(key.startswith(_PREFIX) for key in labels):
                unknown += 1
                continue
            identity = _identity(pod, self.node_name)
            key = str(identity.environment_id)
            if key not in own or own[key][0] != identity or key in seen:
                raise TelemetryUnavailable("Pod allocation or deployment mismatch")
            if pod["spec"].get("nodeName") != self.node_name:
                raise TelemetryUnavailable("Pod scheduled outside configured Node")
            meta = pod["metadata"]
            matches = [
                stats
                for stats in summary["pods"]
                if stats.get("podRef")
                == {"name": meta["name"], "namespace": identity.namespace, "uid": meta["uid"]}
            ]
            if (
                len(matches) != 1
                or pod.get("status", {}).get("phase") != "Running"
                or meta.get("deletionTimestamp")
            ):
                raise TelemetryUnavailable("current Pod stats unavailable")
            stats = matches[0]
            _fresh(stats["memory"])
            memory = _uint(stats["memory"]["workingSetBytes"])
            usage = _uint(stats["memory"]["usageBytes"])
            container_stats = stats["containers"]
            if len(container_stats) != 1 or container_stats[0].get("name") != "environment":
                raise TelemetryUnavailable("container stats identity unavailable")
            _fresh(container_stats[0]["rootfs"])
            writable = _uint(container_stats[0]["rootfs"]["usedBytes"])
            claim_name = "data-" + identity.environment_id.hex
            expected_volumes = [
                {"name": "workspace", "persistentVolumeClaim": {"claimName": claim_name}}
            ]
            if pod["spec"].get("volumes") != expected_volumes:
                raise TelemetryUnavailable("Pod storage identity unavailable")
            claim = self.api.get_optional("pvc", claim_name, identity.namespace)
            if (
                claim is None
                or _identity(claim, self.node_name) != identity
                or claim.get("status", {}).get("phase") != "Bound"
            ):
                raise TelemetryUnavailable("current PVC identity unavailable")
            inventoried = own_claims.get(key)
            if inventoried is None or inventoried["metadata"]["uid"] != claim["metadata"]["uid"]:
                raise TelemetryUnavailable("PVC changed during sampling")
            pv = self.api.get_optional("pv", claim["spec"]["volumeName"])
            pv_spec = (pv or {}).get("spec") or {}
            ref = pv_spec.get("claimRef") or {}
            if (
                not (pv or {}).get("metadata", {}).get("uid")
                or not pv_spec.get("local")
                or not _pinned(pv_spec.get("nodeAffinity", {}).get("required", {}), self.node_name)
                or any(
                    ref.get(k) != v
                    for k, v in {
                        "name": claim_name,
                        "namespace": identity.namespace,
                        "uid": claim["metadata"]["uid"],
                    }.items()
                )
            ):
                raise TelemetryUnavailable("persistent volume Node or PVC binding unavailable")
            volume_stats = [
                volume
                for volume in stats.get("volume", [])
                if volume.get("name") == "workspace"
                and volume.get("pvcRef") == {"name": claim_name, "namespace": identity.namespace}
            ]
            if len(volume_stats) != 1:
                raise TelemetryUnavailable("persistent volume usage unknown")
            _fresh(volume_stats[0])
            used = _uint(volume_stats[0]["usedBytes"])
            current = self.api.get_optional("pod", meta["name"], identity.namespace)
            if (
                current is None
                or current["metadata"].get("uid") != meta["uid"]
                or _identity(current, self.node_name) != identity
                or current["spec"].get("nodeName") != self.node_name
            ):
                raise TelemetryUnavailable("Pod changed during sampling")
            containers.append(
                {
                    "env_id": key,
                    "name": meta["name"],
                    "state": "running",
                    "memory_working_set_bytes": memory,
                    "mem_bytes": usage,
                    "size_rw": writable,
                }
            )
            volumes.append({"env_id": key, "name": claim_name, "bytes": used})
            seen.add(key)
        if seen != set(own):
            raise TelemetryUnavailable("managed deployment has unknown or detached storage")
        return containers, volumes, unknown

    def report(self, workspace: Workspace) -> None:
        # A ticket must precede API reads; failed collection never sends /report.
        ticket = workspace.post("/report/start", {})
        try:
            sample_id = UUID(ticket["sample_id"])
            node, summary, capacity = self._node_sample()
            containers, volumes, unknown = self._workloads(summary)
            current = self._node()
            if (
                current["metadata"]["uid"] != node["metadata"]["uid"]
                or self._physical(current) != self._physical(node)
                or current["metadata"].get("labels", {}).get(_NODE_LABEL) != self.node_name
            ):
                raise TelemetryUnavailable("Node changed during sampling")
            images = node["status"]["images"]
            images_bytes = sum(_uint(image["sizeBytes"]) for image in images)
            body = RunnerReport(
                sample_id=sample_id,
                physical_host_id=self._physical(node),
                containers=containers,
                volumes=volumes,
                unlabeled_containers=unknown,
                **{key: value for key, value in capacity.items() if key != "cpus"},
                images_bytes=images_bytes,
                volumes_bytes=sum(volume["bytes"] for volume in volumes),
                containers_bytes=sum(container["size_rw"] for container in containers),
            ).model_dump(mode="json")
        except _ERRORS as error:
            raise TelemetryUnavailable("complete trusted Node sample unavailable") from error
        workspace.post("/report", body)
