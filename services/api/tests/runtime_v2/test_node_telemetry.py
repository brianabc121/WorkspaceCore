"""Trusted sample provenance is required before admitting Kubernetes capacity."""

import copy
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl, workload
from app.runtime_v2.telemetry import NodeTelemetry, TelemetryUnavailable

NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"


class Api(Kubectl):
    def __init__(self) -> None:
        self.events: list[str] = []
        timestamp = datetime.now(UTC).isoformat()
        self.node: dict[str, Any] = {
            "metadata": {"name": "node-a", "uid": "node-uid", "labels": {NODE_LABEL: "node-a"}},
            "status": {
                "nodeInfo": {"machineID": "a" * 32},
                "allocatable": {"cpu": "7500m", "memory": "8Gi"},
                "images": [{"names": ["image"], "sizeBytes": 42}],
            },
        }
        self.summary: dict[str, Any] = {
            "node": {
                "nodeName": "node-a",
                "cpu": {"time": timestamp, "usageNanoCores": 500_000_000},
                "memory": {"time": timestamp, "availableBytes": 6 << 30},
                "fs": {"time": timestamp, "capacityBytes": 100 << 30, "availableBytes": 80 << 30},
            },
            "pods": [],
        }
        self.pods: list[dict[str, Any]] = []
        self.deployments: list[dict[str, Any]] = []
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}

    def validate_connection(self) -> None:
        self.events.append("tls")

    def get_optional(self, kind: str, name: str, namespace: str | None = None) -> Any:
        self.events.append(kind)
        if kind == "node":
            return copy.deepcopy(self.node)
        if kind == "runtimeclass":
            return {"metadata": {"name": "gvisor"}, "handler": "runsc"}
        return copy.deepcopy(self.objects.get((kind, name)))

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        self.events.append("summary" if "--raw" in args else args[1])
        if "--raw" in args:
            assert args == ["get", "--raw", "/api/v1/nodes/node-a/proxy/stats/summary"]
            return json.dumps(self.summary)
        if args[1] == "pvc":
            return json.dumps(
                {"items": [value for (kind, _), value in self.objects.items() if kind == "pvc"]}
            )
        return json.dumps({"items": self.pods if args[1] == "pods" else self.deployments})

    def add_workload(self) -> RuntimeIdentity:
        ident = RuntimeIdentity(UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4), 1, "node-a")
        claim, deploy = workload(
            ident, {"image": "repo@sha256:" + "b" * 64, "cpus": 1, "memory_mb": 128, "disk_gb": 1}
        )
        deploy["metadata"]["uid"] = "deploy-uid"
        pod = {
            "metadata": {
                "name": "pod-1",
                "namespace": ident.namespace,
                "uid": "pod-uid",
                "labels": ident.labels,
            },
            "spec": {
                "nodeName": "node-a",
                "volumes": deploy["spec"]["template"]["spec"]["volumes"],
            },
            "status": {"phase": "Running"},
        }
        claim["metadata"]["uid"] = "claim-uid"
        claim["spec"]["volumeName"] = "pv-1"
        claim["status"] = {"phase": "Bound"}
        self.pods.append(pod)
        self.deployments.append(deploy)
        self.objects.update(
            {
                ("pod", "pod-1"): pod,
                ("pvc", claim["metadata"]["name"]): claim,
                ("namespace", ident.namespace): {
                    "metadata": {"name": ident.namespace, "uid": "ns-uid", "labels": ident.labels}
                },
                ("pv", "pv-1"): {
                    "metadata": {"name": "pv-1", "uid": "pv-uid"},
                    "spec": {
                        "local": {"path": "/runner/data"},
                        "claimRef": {
                            "name": claim["metadata"]["name"],
                            "namespace": ident.namespace,
                            "uid": "claim-uid",
                        },
                        "nodeAffinity": {
                            "required": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchFields": [
                                            {
                                                "key": "metadata.name",
                                                "operator": "In",
                                                "values": ["node-a"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        },
                    },
                },
            }
        )
        timestamp = datetime.now(UTC).isoformat()
        self.summary["pods"] = [
            {
                "podRef": {"name": "pod-1", "namespace": ident.namespace, "uid": "pod-uid"},
                "memory": {"time": timestamp, "workingSetBytes": 64 << 20, "usageBytes": 70 << 20},
                "containers": [
                    {"name": "environment", "rootfs": {"time": timestamp, "usedBytes": 100}}
                ],
                "volume": [
                    {
                        "name": "workspace",
                        "time": timestamp,
                        "usedBytes": 200,
                        "pvcRef": {"name": claim["metadata"]["name"], "namespace": ident.namespace},
                    }
                ],
            }
        ]
        return ident


class Workspace:
    def __init__(self, api: Api) -> None:
        self.api = api
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, body: dict[str, Any]) -> Any:
        self.api.events.append(path)
        self.sent.append((path, body))
        return {"sample_id": str(UUID(int=99))} if path == "/report/start" else {}


def test_complete_sample_starts_before_collection() -> None:
    api = Api()
    api.add_workload()
    telemetry = NodeTelemetry(api, "node-a")
    assert telemetry.capacity()["cpus"] == 7
    assert telemetry.capacity()["mem_bytes"] == 8 << 30
    workspace = Workspace(api)
    api.events.clear()
    telemetry.report(workspace)  # type: ignore[arg-type]
    assert (
        api.events.index("/report/start")
        < api.events.index("summary")
        < api.events.index("/report")
    )
    body = workspace.sent[-1][1]
    assert body["mem_free_bytes"] == 6 << 30
    assert body["containers"][0]["memory_working_set_bytes"] == 64 << 20
    assert body["volumes"][0]["bytes"] == body["volumes_bytes"] == 200


@pytest.mark.parametrize(
    "case", ["wrong-node", "node-label", "machine-id", "missing-disk", "negative", "stale", "nan"]
)
def test_untrusted_node_refuses_capacity_and_report(case: str) -> None:
    api = Api()
    if case == "wrong-node":
        api.summary["node"]["nodeName"] = "node-b"
    elif case == "node-label":
        api.node["metadata"]["labels"] = {}
    elif case == "machine-id":
        api.node["status"]["nodeInfo"]["machineID"] = "unknown"
    elif case == "missing-disk":
        del api.summary["node"]["fs"]["availableBytes"]
    elif case == "negative":
        api.summary["node"]["memory"]["availableBytes"] = -1
    elif case == "stale":
        api.summary["node"]["fs"]["time"] = "2001-01-01T00:00:00Z"
    else:
        api.summary["node"]["cpu"]["usageNanoCores"] = float("nan")
    telemetry = NodeTelemetry(api, "node-a")
    assert telemetry.capacity()["cpus"] is None
    workspace = Workspace(api)
    with pytest.raises(TelemetryUnavailable):
        telemetry.report(workspace)  # type: ignore[arg-type]
    assert all(path != "/report" for path, _ in workspace.sent)


@pytest.mark.parametrize(
    "case",
    [
        "pod-uid",
        "labels",
        "storage-missing",
        "storage-wrong-claim",
        "pv-wrong-node",
        "pod-replaced",
        "stopped-pvc",
    ],
)
def test_unknown_workload_never_sends_incomplete_report(case: str) -> None:
    api = Api()
    ident = api.add_workload()
    if case == "pod-uid":
        api.summary["pods"][0]["podRef"]["uid"] = "old"
    elif case == "labels":
        api.pods[0]["metadata"]["labels"]["workspace.myworkspace.io/allocation"] = UUID(int=50).hex
    elif case == "storage-missing":
        api.summary["pods"][0]["volume"] = []
    elif case == "storage-wrong-claim":
        api.summary["pods"][0]["volume"][0]["pvcRef"]["name"] = "foreign"
    elif case == "pv-wrong-node":
        api.objects[("pv", "pv-1")]["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0][
            "matchFields"
        ][0]["values"] = ["node-b"]
    elif case == "pod-replaced":
        api.objects[("pod", "pod-1")] = copy.deepcopy(api.pods[0])
        api.objects[("pod", "pod-1")]["metadata"]["uid"] = "replacement"
    else:
        api.pods.clear()
        api.summary["pods"].clear()
        api.deployments[0]["spec"]["replicas"] = 0
    workspace = Workspace(api)
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, ident.node_name).report(workspace)  # type: ignore[arg-type]
    assert all(path != "/report" for path, _ in workspace.sent)


def test_orphaned_ownnode_pvc_never_counts_zero() -> None:
    api = Api()
    api.add_workload()
    api.deployments.clear()
    api.pods.clear()
    api.summary["pods"].clear()
    workspace = Workspace(api)
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, "node-a").report(workspace)  # type: ignore[arg-type]
    assert all(path != "/report" for path, _ in workspace.sent)


def test_node_uid_replacement_during_capture_refuses_report() -> None:
    class Replaced(Api):
        def get_optional(self, kind: str, name: str, namespace: str | None = None) -> Any:
            node = super().get_optional(kind, name, namespace)
            if kind == "node" and self.events.count("node") > 1:
                node["metadata"]["uid"] = "replacement"
            return node

    api = Replaced()
    workspace = Workspace(api)
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, "node-a").report(workspace)  # type: ignore[arg-type]
    assert all(path != "/report" for path, _ in workspace.sent)
