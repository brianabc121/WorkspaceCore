"""Quota mode reports physical backing separately from measured inner data."""

import copy
import json
from typing import Any
from uuid import UUID

import pytest

from app.runner_agent.client import Workspace as RunnerWorkspace
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.storage_client import StorageClient, StorageUnavailable
from app.runtime_v2.telemetry import NodeTelemetry, TelemetryUnavailable
from tests.runtime_v2.test_node_telemetry import Api as LegacyApi


class Api(LegacyApi):
    def add_workload(self, n: int = 1, *, stopped: bool = False) -> RuntimeIdentity:
        identity = super().add_workload(n, stopped=stopped)
        self.objects[("pv", f"pv-{n}")]["metadata"]["labels"] = identity.labels
        return identity

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        if args[:2] == ["get", "pv"]:
            selector = args[args.index("-l") + 1]
            key, _, value = selector.partition("=")
            items = [
                obj
                for (kind, _), obj in self.objects.items()
                if kind == "pv"
                and key in obj["metadata"].get("labels", {})
                and (not value or obj["metadata"]["labels"][key] == value)
            ]
            return json.dumps({"items": items})
        return super().call(args, input_data=input_data)


class Workspace(RunnerWorkspace):
    def __init__(self, api: Api) -> None:
        self.api = api
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, body: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        self.api.events.append(path)
        self.sent.append((path, body))
        return {"sample_id": str(UUID(int=99))} if path == "/report/start" else {}


class Storage(StorageClient):
    def __init__(self) -> None:
        super().__init__("/run/test-storage.sock", Api(), "node-a")
        self.inspected: list[tuple[RuntimeIdentity, dict[str, Any]]] = []
        self.fail = False

    def inspect(
        self, identity: RuntimeIdentity, claim: dict[str, Any], *, operation_id: str | None = None
    ) -> dict[str, Any]:
        self.inspected.append((identity, copy.deepcopy(claim)))
        if self.fail:
            raise StorageUnavailable("storage_refused_or_unknown")
        return {
            "backing": {"allocated_bytes": 1 << 30},
            "live": {"used_bytes": 8192, "usable_bytes": (1 << 30) - 4096},
        }


def test_running_storage_reports_backing_and_inner_data_separately() -> None:
    api = Api()
    api.add_workload()
    storage = Storage()
    workspace = Workspace(api)
    NodeTelemetry(api, "node-a", storage=storage).report(workspace)
    body = workspace.sent[-1][1]
    assert body["volumes"][0]["bytes"] == 1 << 30
    assert body["volumes_bytes"] == 1 << 30
    assert body["containers"][0]["size_rw"] == 100 + 8192
    assert body["disk_free_bytes"] == 80 << 30  # no physical backing credit invented
    assert storage.inspected


def test_stopped_retained_uses_real_backing_not_pv_declaration() -> None:
    api = Api()
    api.add_workload(stopped=True)
    storage = Storage()
    workspace = Workspace(api)
    pv = api.objects[("pv", "pv-1")]
    pv["spec"]["capacity"]["storage"] = "100Gi"
    NodeTelemetry(api, "node-a", storage=storage).report(workspace)
    assert workspace.sent[-1][1]["volumes"][0]["bytes"] == 1 << 30


def test_unknown_proof_refuses_report_and_capacity() -> None:
    api = Api()
    api.add_workload(stopped=True)
    storage = Storage()
    storage.fail = True
    telemetry = NodeTelemetry(api, "node-a", storage=storage)
    workspace = Workspace(api)
    assert telemetry.capacity()["runtimes"] == []
    with pytest.raises(TelemetryUnavailable):
        telemetry.report(workspace)
    assert [path for path, _ in workspace.sent] == ["/report/start"]


def test_orphan_claim_still_refuses_in_quota_mode() -> None:
    api = Api()
    api.add_workload(stopped=True)
    api.deployments.clear()
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, "node-a", storage=Storage()).report(Workspace(api))


def test_real_storage_client_refuses_unbounded_legacy_claim() -> None:
    from tests.runtime_v2.test_storage_client import Client

    api = Api()
    api.add_workload(stopped=True)
    telemetry = NodeTelemetry(api, "node-a", storage=Client(api))
    assert telemetry.capacity()["runtimes"] == []
    with pytest.raises(TelemetryUnavailable):
        telemetry.report(Workspace(api))


def test_unbound_orphan_managed_claim_cannot_disappear_from_quota_sample() -> None:
    api = Api()
    api.add_workload(stopped=True)
    api.deployments.clear()
    api.objects[("pvc", "data-" + "0" * 31 + "3")]["spec"].pop("volumeName")
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, "node-a", storage=Storage()).report(Workspace(api))


def helper_profile() -> tuple[Api, str]:
    api = Api()
    identity = api.add_workload()
    image = "trusted/helper@sha256:" + "b" * 64
    # Independent golden contract: the producer is a separate integration task.
    template = copy.deepcopy(api.deployments[0]["spec"]["template"])
    spec = template["spec"]
    spec["volumes"] = [
        {
            "name": "workspace",
            "persistentVolumeClaim": {"claimName": "data-" + identity.environment_id.hex},
        },
        {"name": "runtime-helper", "emptyDir": {"sizeLimit": "64Mi"}},
        {"name": "runtime-secrets", "emptyDir": {"medium": "Memory", "sizeLimit": "32Mi"}},
        {
            "name": "runtime-identity",
            "downwardAPI": {
                "items": [
                    {"path": "uid", "fieldRef": {"fieldPath": "metadata.uid"}},
                    {
                        "path": "allocation",
                        "fieldRef": {
                            "fieldPath": "metadata.labels['workspace.myworkspace.io/allocation']"
                        },
                    },
                    {
                        "path": "generation",
                        "fieldRef": {
                            "fieldPath": "metadata.labels['workspace.myworkspace.io/generation']"
                        },
                    },
                ]
            },
        },
    ]
    spec["containers"][0]["image"] = "image@sha256:" + "c" * 64
    spec["containers"][0]["volumeMounts"] = [
        {"name": "workspace", "mountPath": "/workspace"},
        {"name": "runtime-helper", "mountPath": "/runtime", "readOnly": True},
        {"name": "runtime-identity", "mountPath": "/run/workspace-identity", "readOnly": True},
        {"name": "runtime-secrets", "mountPath": "/run/secrets"},
    ]
    spec["initContainers"] = [
        {
            "name": "install-runtime-helper",
            "image": image,
            "command": ["/helper", "install", "/runtime/helper"],
            "volumeMounts": [{"name": "runtime-helper", "mountPath": "/runtime"}],
            "resources": {
                "requests": {"cpu": "50m", "memory": "32Mi"},
                "limits": {"cpu": "50m", "memory": "32Mi"},
            },
            "securityContext": {
                "privileged": False,
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
        }
    ]
    api.deployments[0]["spec"]["template"] = copy.deepcopy(template)
    pod = api.pods[0]
    pod["spec"].update(copy.deepcopy(template["spec"]))
    pod["spec"]["nodeName"] = "node-a"
    pod["metadata"]["ownerReferences"] = [
        {
            "apiVersion": "apps/v1",
            "kind": "ReplicaSet",
            "name": "rs-1",
            "uid": "rs-uid",
            "controller": True,
        }
    ]
    api.objects[("pod", "pod-1")] = pod
    api.objects[("replicaset", "rs-1")] = {
        "metadata": {
            "name": "rs-1",
            "namespace": identity.namespace,
            "uid": "rs-uid",
            "labels": identity.labels,
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": identity.pod_name,
                    "uid": "deploy-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {"template": copy.deepcopy(template)},
    }
    api.summary["pods"][0]["volume"].append(
        {
            "name": "runtime-helper",
            "time": api.summary["pods"][0]["memory"]["time"],
            "usedBytes": 4096,
        }
    )
    return api, image


def test_configured_helper_profile_reports_with_exact_owner_and_mounts() -> None:
    api, image = helper_profile()
    workspace = Workspace(api)
    NodeTelemetry(api, "node-a", storage=Storage(), helper_image=image).report(workspace)
    assert workspace.sent[-1][1]["volumes"][0]["bytes"] == 1 << 30


@pytest.mark.parametrize("case", ["image", "owner", "mount", "volume", "downward", "unknown-image"])
def test_helper_profile_mutations_refuse(case: str) -> None:
    api, image = helper_profile()
    configured_image: str | None = image
    if case == "image":
        api.pods[0]["spec"]["initContainers"][0]["image"] = "other@sha256:" + "d" * 64
    elif case == "owner":
        api.objects[("replicaset", "rs-1")]["metadata"]["ownerReferences"][0]["uid"] = "replaced"
    elif case == "mount":
        api.pods[0]["spec"]["containers"][0]["volumeMounts"][1]["readOnly"] = False
    elif case == "volume":
        api.pods[0]["spec"]["volumes"].append({"name": "extra", "emptyDir": {}})
    elif case == "downward":
        api.pods[0]["spec"]["volumes"][3]["downwardAPI"]["items"][0]["fieldRef"]["fieldPath"] = (
            "metadata.name"
        )
    elif case == "unknown-image":
        configured_image = None
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, "node-a", storage=Storage(), helper_image=configured_image).report(
            Workspace(api)
        )


def test_helper_init_extra_arguments_are_not_a_validated_protocol() -> None:
    api, image = helper_profile()
    api.pods[0]["spec"]["initContainers"][0]["args"] = ["unreviewed-mode"]
    with pytest.raises(TelemetryUnavailable):
        NodeTelemetry(api, "node-a", storage=Storage(), helper_image=image).report(Workspace(api))


class ScratchApi(Api):
    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        if args[:2] == ["get", "pvc"]:
            selector = args[args.index("-l") + 1]
            key, _, value = selector.partition("=")
            items = [
                obj
                for (kind, _), obj in self.objects.items()
                if kind == "pvc"
                and key in obj["metadata"]["labels"]
                and (not value or obj["metadata"]["labels"][key] == value)
            ]
            return json.dumps({"items": items})
        return super().call(args, input_data=input_data)


def test_scratch_reservation_never_reports_available_node_capacity() -> None:
    api = ScratchApi()
    # Scratch must remain visible even without a workload Deployment/Pod.
    api.objects[("pvc", "scratch-owned")] = {
        "metadata": {
            "name": "scratch-owned",
            "labels": {"workspace.myworkspace.io/component": "restore-scratch"},
        }
    }
    telemetry = NodeTelemetry(api, "node-a", storage=Storage())
    assert telemetry.capacity()["runtimes"] == []
    assert telemetry.capacity()["disk_free_bytes"] is None
    with pytest.raises(TelemetryUnavailable):
        telemetry.report(Workspace(api))


def test_partial_retirement_pv_only_orphan_refuses_capacity_and_report() -> None:
    api = ScratchApi()
    identity = api.add_workload(stopped=True)
    api.deployments.clear()
    del api.objects[("pvc", "data-" + identity.environment_id.hex)]
    workspace = Workspace(api)
    storage = Storage()
    telemetry = NodeTelemetry(api, "node-a", storage=storage)
    assert telemetry.capacity()["runtimes"] == []
    with pytest.raises(TelemetryUnavailable):
        telemetry.report(workspace)
    assert [path for path, _ in workspace.sent] == ["/report/start"]


@pytest.mark.parametrize("case", ["uid", "machine", "protected-label"])
def test_capacity_fences_node_identity_after_summary(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api()
    original_call = api.call

    def changed_summary(args: list[str], *, input_data: str | None = None) -> str:
        response = original_call(args, input_data=input_data)
        if "--raw" in args:
            if case == "uid":
                api.node["metadata"]["uid"] = "replacement-node"
            elif case == "machine":
                api.node["status"]["nodeInfo"]["machineID"] = "b" * 32
            else:
                api.node["metadata"]["labels"] = {}
        return response

    monkeypatch.setattr(api, "call", changed_summary)
    assert NodeTelemetry(api, "node-a", storage=Storage()).capacity()["runtimes"] == []


def test_shared_helper_layout_rejects_unpinned_image() -> None:
    from app.runtime_v2.helper_profile import helper_layout
    from tests.runtime_v2.test_storage_client import identity

    with pytest.raises(ValueError, match="immutable helper image required"):
        helper_layout(identity(), "tenant/helper:latest")
