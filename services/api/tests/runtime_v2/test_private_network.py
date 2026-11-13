"""Private network behavior at the Kubernetes/host JSON boundaries."""

import copy
import json
from pathlib import Path
from typing import Any, TypedDict
from uuid import UUID, uuid4

import pytest

from app.runner_agent.client import Workspace as WorkspaceClient
from app.runtime_v2.kubernetes import KubernetesRuntime, RuntimeIdentity
from app.runtime_v2.runner import (
    Kubectl,
    KubernetesCommandError,
    KubernetesRunner,
    OutcomeUnknown,
    PendingApply,
)

PREFIX = "workspace.myworkspace.io/"


class ReconcilerArgs(TypedDict):
    workspace_id: UUID
    host_id: UUID | str
    node_name: str
    journal_path: str


def identity() -> RuntimeIdentity:
    return RuntimeIdentity(uuid4(), uuid4(), uuid4(), uuid4(), 1, "node-a")


class Api(Kubectl):
    def __init__(self) -> None:
        super().__init__("/etc/kubeconfig")
        self.objects: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        self.mutations: list[dict[str, Any]] = []
        self.version = 0

    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        return copy.deepcopy(self.objects.get((kind.lower(), name, namespace)))

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        saved = copy.deepcopy(item)
        self.version += 1
        saved["metadata"].update(uid=str(uuid4()), resourceVersion=str(self.version))
        if saved["kind"] == "Service":
            saved["spec"]["clusterIP"] = "10.43.0.12"
        meta = saved["metadata"]
        self.objects[(saved["kind"].lower(), meta["name"], meta.get("namespace"))] = saved
        self.mutations.append(copy.deepcopy(saved))
        return saved

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        if args[0] == "patch":
            key = (args[1], args[2], args[args.index("-n") + 1])
            obj = self.objects[key]
            patch = json.loads(args[args.index("--patch") + 1])
            for op in patch:
                pieces = [v.replace("~1", "/") for v in op["path"].strip("/").split("/")]
                target = obj
                for piece in pieces[:-1]:
                    target = target[piece]
                if op["op"] == "test":
                    if target.get(pieces[-1]) != op["value"]:
                        raise KubernetesCommandError("conflict")
                else:
                    target[pieces[-1]] = op["value"]
            self.version += 1
            obj["metadata"]["resourceVersion"] = str(self.version)
            self.mutations.append({"patch": patch})
            return "{}"
        if args[:2] == ["delete", "--raw"]:
            assert input_data is not None
            options = json.loads(input_data)
            parts = args[2].split("/")
            namespace, name = parts[parts.index("namespaces") + 1], parts[-1]
            kind = "service" if parts[-2] == "services" else "networkpolicy"
            key = (kind, name, namespace)
            obj = self.objects[key]
            assert options["preconditions"]["uid"] == obj["metadata"]["uid"]
            self.mutations.append({"delete": options})
            del self.objects[key]
            return "{}"
        raise AssertionError(args)


def provision(runner: KubernetesRunner, ident: RuntimeIdentity) -> None:
    for _ in range(8):
        try:
            runner.ensure_domain(ident)
            return
        except PendingApply:
            continue
    raise AssertionError("domain did not converge")


def service_row(ident: RuntimeIdentity, **updates: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "workspace_id": str(ident.workspace_id),
        "instance_id": str(ident.instance_id),
        "environment_id": str(ident.environment_id),
        "allocation_id": str(ident.allocation_id),
        "generation": ident.generation,
        "node_name": ident.node_name,
        "backend": "kubernetes",
        "state": "approved_pending_apply",
        "desired_revision": 1,
        "port": 5432,
        "protocol": "TCP",
        **updates,
    }


def test_domain_policy_allows_only_own_domain_and_managed_dns() -> None:
    ident = identity()
    policy = KubernetesRuntime.network_policy(ident)["spec"]
    peer = {
        "podSelector": {
            "matchLabels": {
                PREFIX + "workspace": ident.workspace_id.hex,
                PREFIX + "instance": ident.instance_id.hex,
            }
        }
    }
    assert policy["ingress"] == [{"from": [peer]}]
    assert policy["egress"][0] == {"to": [peer]}
    assert policy["egress"][1] == {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    assert "nodeName" not in json.dumps(policy)  # peers on any managed node share this domain


def test_ensure_domain_creates_controlled_namespace_and_default_deny_before_workload() -> None:
    ident, api = identity(), Api()
    provision(KubernetesRunner(api), ident)
    namespace = api.get_optional("namespace", ident.namespace)
    assert namespace is not None
    assert namespace["metadata"]["labels"][PREFIX + "instance"] == ident.instance_id.hex
    deny = api.get_optional("networkpolicy", "runner-default-deny", ident.namespace)
    assert deny is not None
    assert deny["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [],
        "egress": [],
    }
    assert [v["kind"] for v in api.mutations] == ["Namespace", "NetworkPolicy", "NetworkPolicy"]


def test_domain_rejects_foreign_namespace_and_updates_policy_with_uid_version_tests() -> None:
    ident, api = identity(), Api()
    api.create({"kind": "Namespace", "metadata": {"name": ident.namespace, "labels": {}}})
    with pytest.raises(ValueError, match="namespace"):
        KubernetesRunner(api).ensure_domain(ident)
    api.objects.clear()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    policy = api.objects[("networkpolicy", "runner-domain", ident.namespace)]
    uid, version = policy["metadata"]["uid"], policy["metadata"]["resourceVersion"]
    policy["spec"]["egress"] = []
    assert runner.apply_domain_policy(ident)["uid"] == uid
    patch = api.mutations[-1]["patch"]
    assert {"op": "test", "path": "/metadata/uid", "value": uid} in patch
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": version} in patch
    assert runner.apply_domain_policy(ident)["uid"] == uid


def test_reconciler_reads_created_services_before_ack_and_continues_revoke_after_failure() -> None:
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api = identity(), Api()
    row = service_row(ident)
    domain = {
        k: row[k]
        for k in (
            "workspace_id",
            "instance_id",
            "environment_id",
            "allocation_id",
            "generation",
            "node_name",
        )
    }
    desired: dict[str, Any] = {"host_id": str(uuid4()), "domains": [domain], "services": [row]}

    class LocalWorkspace(WorkspaceClient):
        def __init__(self) -> None:
            super().__init__("http://workspace.invalid", "test-token")
            self.sent: list[dict[str, Any]] = []

        def post(self, path: str, body: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
            if path == "/network/desired":
                return desired
            assert path == "/network/ack"
            self.sent.append(body)
            return {"accepted": True}

    workspace = LocalWorkspace()
    runner = KubernetesRunner(api)
    reconciler = PrivateNetworkReconciler(
        runner,
        workspace,
        workspace_id=ident.workspace_id,
        host_id=desired["host_id"],
        node_name=ident.node_name,
    )
    for _ in range(8):
        reconciler.reconcile()
        if workspace.sent:
            break
    observed = api.get_optional("service", "svc-" + row["id"].replace("-", ""), ident.namespace)
    assert observed is not None
    assert workspace.sent[0] == {
        "record_id": row["id"],
        "revision": 1,
        "generation": 1,
        "success": True,
        "receipt_uid": observed["metadata"]["uid"],
        "resource_version": observed["metadata"]["resourceVersion"],
    }
    row["state"], row["desired_revision"] = "revoked", 2
    desired["services"].insert(0, service_row(ident, protocol="INVALID"))
    reconciler.reconcile()
    assert workspace.sent[-1]["record_id"] == row["id"]
    assert workspace.sent[-1]["revision"] == 2
    assert api.get_optional("service", "svc-" + row["id"].replace("-", ""), ident.namespace) is None
    assert "delete" in api.mutations[-1]


def test_revocation_refuses_replaced_service_uid() -> None:
    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    sid = uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, sid, 1, 80, "TCP")
    receipt = runner.apply_private_service(ident, sid, 1, 80, "TCP")
    api.objects[("service", "svc-" + sid.hex, ident.namespace)]["metadata"]["uid"] = "replacement"
    with pytest.raises(OutcomeUnknown, match="UID"):
        runner.delete_private_service(ident, sid, 2, expected_uid=receipt["uid"])
    assert not any("delete" in mutation for mutation in api.mutations)


def test_service_apply_rejects_older_generation_even_with_newer_revision() -> None:
    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    sid = uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, sid, 1, 80, "TCP")
    api.objects[("service", "svc-" + sid.hex, ident.namespace)]["metadata"]["labels"][
        PREFIX + "generation"
    ] = "2"
    with pytest.raises(ValueError, match="generation"):
        runner.apply_private_service(ident, sid, 2, 81, "TCP")


def test_gateway_permissions_are_exact_identity_and_ports_then_removed() -> None:
    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    sid = uuid4()
    for _ in range(3):
        try:
            runner.apply_gateway_egress(ident, [30001, 30009], 1)
            runner.apply_gateway_ingress(ident, sid, 2, 5432, True)
            break
        except PendingApply:
            continue
    peer = {
        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "runner-system"}},
        "podSelector": {"matchLabels": {PREFIX + "component": "network-gateway"}},
    }
    egress = api.get_optional("networkpolicy", "runner-gateway-egress", ident.namespace)
    assert egress is not None
    assert egress["spec"]["egress"] == [
        {
            "to": [peer],
            "ports": [
                {"protocol": "TCP", "port": 30001},
                {"protocol": "TCP", "port": 30009},
            ],
        }
    ]
    ingress = api.get_optional("networkpolicy", "gateway-" + sid.hex, ident.namespace)
    assert ingress is not None
    assert ingress["spec"]["podSelector"] == {"matchLabels": ident.labels}
    assert ingress["spec"]["ingress"] == [
        {
            "from": [peer],
            "ports": [
                {"protocol": "TCP", "port": 5432},
            ],
        }
    ]
    assert "Egress" not in ingress["spec"]["policyTypes"]
    runner.apply_gateway_ingress(ident, sid, 3, 5432, False)
    assert api.get_optional("networkpolicy", "gateway-" + sid.hex, ident.namespace) is None


def test_service_requires_resource_version_for_ack_receipt() -> None:
    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    sid = uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, sid, 1, 80, "TCP")
    del api.objects[("service", "svc-" + sid.hex, ident.namespace)]["metadata"]["resourceVersion"]
    with pytest.raises(PendingApply, match="version"):
        runner.apply_private_service(ident, sid, 1, 80, "TCP")


class Workspace(WorkspaceClient):
    def __init__(self, desired: dict[str, Any]) -> None:
        super().__init__("http://workspace.invalid", "test-token")
        self.desired = desired
        self.sent: list[dict[str, Any]] = []

    def post(self, path: str, body: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        if path == "/network/desired":
            return copy.deepcopy(self.desired)
        assert path == "/network/ack"
        self.sent.append(body)
        return {"accepted": True}


def test_stale_gateway_snapshot_cannot_reopen_ports_after_restart(tmp_path: Path) -> None:
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    domain = service_row(ident, gateway_ports=[30001], gateway_revision=10)
    desired: dict[str, Any] = {"host_id": str(uuid4()), "domains": [domain], "services": []}
    workspace = Workspace(desired)
    args: ReconcilerArgs = {
        "workspace_id": ident.workspace_id,
        "host_id": desired["host_id"],
        "node_name": ident.node_name,
        "journal_path": str(tmp_path / "journal.db"),
    }
    first = PrivateNetworkReconciler(runner, workspace, **args)
    first.reconcile()
    domain.update(gateway_ports=[], gateway_revision=11)
    first.reconcile()
    first.reconcile()
    # Simulate namespace/policy recreation and an old delayed response arriving
    # after process restart: API object version tests alone cannot fence this.
    del api.objects[("networkpolicy", "runner-gateway-egress", ident.namespace)]
    domain.update(gateway_ports=[30001], gateway_revision=10)
    PrivateNetworkReconciler(KubernetesRunner(api), workspace, **args).reconcile()
    policy = api.get_optional("networkpolicy", "runner-gateway-egress", ident.namespace)
    assert policy is None or policy["spec"]["egress"] == []


def test_unversioned_domain_cannot_clear_existing_gateway_revision_fence() -> None:
    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    with pytest.raises(PendingApply):
        runner.apply_gateway_egress(ident, [], 20)
    with pytest.raises(ValueError, match="revision"):
        runner.apply_gateway_egress(ident, [30001])


def test_unknown_service_delete_recovers_with_durable_uid_receipt(tmp_path: Path) -> None:
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident = identity()

    class UnknownDelete(Api):
        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            result = super().call(args, input_data=input_data)
            if args[0] == "delete":
                raise OutcomeUnknown("response lost after committed delete")
            return result

    api = UnknownDelete()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    row = service_row(ident)
    desired: dict[str, Any] = {"host_id": str(uuid4()), "domains": [], "services": [row]}
    workspace = Workspace(desired)
    args: ReconcilerArgs = {
        "workspace_id": ident.workspace_id,
        "host_id": desired["host_id"],
        "node_name": ident.node_name,
        "journal_path": str(tmp_path / "journal.db"),
    }
    first = PrivateNetworkReconciler(runner, workspace, **args)
    first.reconcile()
    first.reconcile()
    assert len(workspace.sent) == 1
    row.update(state="revoked", desired_revision=2)
    first.reconcile()
    assert len(workspace.sent) == 1
    PrivateNetworkReconciler(KubernetesRunner(api), workspace, **args).reconcile()
    assert workspace.sent[-1]["revision"] == 2
    assert workspace.sent[-1]["receipt_uid"] == workspace.sent[0]["receipt_uid"]


def test_gateway_ingress_refuses_stale_snapshot_after_policy_recreation(tmp_path: Path) -> None:
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    row = service_row(ident, gateway_ingress=True)
    domain = {**row, "gateway_revision": 10, "gateway_ports": []}
    desired: dict[str, Any] = {"host_id": str(uuid4()), "domains": [domain], "services": [row]}
    workspace = Workspace(desired)
    args: ReconcilerArgs = {
        "workspace_id": ident.workspace_id,
        "host_id": desired["host_id"],
        "node_name": ident.node_name,
        "journal_path": str(tmp_path / "journal.db"),
    }
    first = PrivateNetworkReconciler(runner, workspace, **args)
    for _ in range(5):
        first.reconcile()
    row["gateway_ingress"] = False
    domain["gateway_revision"] = 11
    first.reconcile()
    domain["gateway_revision"], row["gateway_ingress"] = 10, True
    PrivateNetworkReconciler(KubernetesRunner(api), workspace, **args).reconcile()
    assert (
        api.get_optional("networkpolicy", "gateway-" + row["id"].replace("-", ""), ident.namespace)
        is None
    )


def test_controller_provisions_domain_before_recording_create_mutation(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal, Controller
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api, host = identity(), Api(), uuid4()
    runner = KubernetesRunner(api)
    workspace = Workspace({"host_id": str(host), "domains": [], "services": []})
    journal = CommandJournal(str(tmp_path / "journal.db"))
    network = PrivateNetworkReconciler(
        runner, workspace, workspace_id=ident.workspace_id, host_id=host, node_name=ident.node_name
    )
    controller = Controller(
        runner,
        workspace,
        journal,
        workspace_id=ident.workspace_id,
        host_id=host,
        node_name=ident.node_name,
        network=network,
    )
    row = service_row(ident)
    command = {
        "id": str(uuid4()),
        "kind": "create_env",
        "payload": {
            "host_id": str(host),
            "env_id": str(ident.environment_id),
            "runtime_identity": {**row, "env_id": str(ident.environment_id)},
            "image": "example/image@sha256:" + "a" * 64,
            "cpus": 1,
            "memory_mb": 256,
            "disk_gb": 10,
        },
    }
    controller.process(command)
    assert [v["kind"] for v in api.mutations] == ["Namespace"]
    assert journal.pending() == []
    provision(runner, ident)
    controller.process(command)
    assert [v["kind"] for v in api.mutations] == [
        "Namespace",
        "NetworkPolicy",
        "NetworkPolicy",
        "PersistentVolumeClaim",
        "Deployment",
    ]


def test_main_loop_continuously_reconciles_network(tmp_path: Path) -> None:
    from threading import Event

    from app.runtime_v2.__main__ import run
    from app.runtime_v2.controller import CommandJournal, Controller
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api, host, stopping = identity(), Api(), uuid4(), Event()
    calls: list[str] = []

    class LoopWorkspace(Workspace):
        def post(self, path: str, body: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
            calls.append(path)
            if path == "/poll":
                if calls.count("/poll") == 2:
                    stopping.set()
                return {"commands": []}
            return super().post(path, body, timeout=timeout)

    class Telemetry:
        def capacity(self) -> dict[str, Any]:
            return {}

        def report(self, workspace: Workspace) -> None:
            pass

    workspace = LoopWorkspace({"host_id": str(host), "domains": [], "services": []})
    runner = KubernetesRunner(api)
    network = PrivateNetworkReconciler(
        runner, workspace, workspace_id=ident.workspace_id, host_id=host, node_name=ident.node_name
    )
    controller = Controller(
        runner,
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=host,
        node_name=ident.node_name,
        network=network,
        stopping=stopping,
    )
    assert run(controller, Telemetry(), stopping) == 0
    assert calls == ["/network/desired", "/poll", "/network/desired", "/poll"]


def test_advancing_snapshot_revisions_do_not_starve_gateway_service_ack() -> None:
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api, host = identity(), Api(), uuid4()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    row = service_row(ident, gateway_ingress=True)
    domain = {**row, "gateway_revision": 1, "gateway_ports": []}
    desired = {"host_id": str(host), "domains": [domain], "services": [row]}
    workspace = Workspace(desired)
    network = PrivateNetworkReconciler(
        runner, workspace, workspace_id=ident.workspace_id, host_id=host, node_name=ident.node_name
    )
    for revision in range(1, 8):
        domain["gateway_revision"] = revision
        network.reconcile()
    assert workspace.sent
    assert workspace.sent[-1]["record_id"] == row["id"]


def test_second_node_policy_revision_fences_delayed_first_node_ingress() -> None:
    from app.runtime_v2.network import PrivateNetworkReconciler

    ident, api, host = identity(), Api(), uuid4()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    row = service_row(ident, gateway_ingress=True)
    domain = {**row, "gateway_revision": 10, "gateway_ports": []}
    desired = {"host_id": str(host), "domains": [domain], "services": [row]}
    first_workspace = Workspace(copy.deepcopy(desired))
    first = PrivateNetworkReconciler(
        runner,
        first_workspace,
        workspace_id=ident.workspace_id,
        host_id=host,
        node_name=ident.node_name,
    )
    for _ in range(5):
        first.reconcile()
    domain["gateway_revision"], row["gateway_ingress"] = 11, False
    second = PrivateNetworkReconciler(
        KubernetesRunner(api),
        Workspace(desired),
        workspace_id=ident.workspace_id,
        host_id=host,
        node_name=ident.node_name,
    )
    second.reconcile()
    first.reconcile()
    assert (
        api.get_optional("networkpolicy", "gateway-" + row["id"].replace("-", ""), ident.namespace)
        is None
    )


def test_service_revocation_fences_concurrent_revision_updates() -> None:
    ident, api = identity(), Api()
    runner = KubernetesRunner(api)
    provision(runner, ident)
    sid = uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, sid, 1, 80, "TCP")
    receipt = runner.apply_private_service(ident, sid, 1, 80, "TCP")
    runner.delete_private_service(ident, sid, 2, expected_uid=receipt["uid"])
    assert api.mutations[-1]["delete"]["preconditions"] == {
        "uid": receipt["uid"],
        "resourceVersion": receipt["resource_version"],
    }
