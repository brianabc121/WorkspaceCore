import json
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Never

import pytest

from app.runner_agent.client import Workspace as WorkspaceClient
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl, KubernetesRunner, OutcomeUnknown, workload

# Kubernetes 对象及工作台命令使用生产入口的 JSON 数据边界。
type JsonObject = dict[str, Any]


class StubKubectl(Kubectl):
    def __init__(self) -> None:
        super().__init__("/etc/kubeconfig")

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        raise AssertionError(f"unexpected Kubernetes call: {args}")


class StubRunner(KubernetesRunner):
    def __init__(self) -> None:
        super().__init__(StubKubectl())


class StubWorkspace(WorkspaceClient):
    def __init__(self) -> None:
        super().__init__("http://workspace.invalid", "test-token")

    def post(self, path: str, body: JsonObject, *, timeout: float = 30) -> JsonObject:
        raise AssertionError(f"unexpected workspace call: {path}")


def test_workload_binds_claim_and_node_without_privileges() -> None:
    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    objects = workload(
        identity,
        {"image": "example/image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10},
    )
    claim, deployment = objects
    assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
    pod = deployment["spec"]["template"]["spec"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == claim["metadata"]["name"]
    assert pod["containers"][0]["volumeMounts"][0]["mountPath"] == "/workspace"
    assert deployment["spec"]["replicas"] == 1
    assert pod["automountServiceAccountToken"] is False


def test_kubectl_timeout_reports_unknown_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(args: list[str], **kwargs: object) -> Never:
        raise subprocess.TimeoutExpired(args, 3)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(OutcomeUnknown):
        Kubectl("/etc/kubeconfig").call(["create", "-f", "-"], input_data=json.dumps({}))


def test_deployment_uses_controller_compatible_restart_policy() -> None:
    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    _, deployment = workload(
        identity,
        {"image": "example/image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10},
    )
    assert deployment["spec"]["template"]["spec"]["restartPolicy"] == "Always"


def test_command_journal_survives_restart_and_never_replays_unknown(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal

    path = str(tmp_path / "receipts.db")
    journal = CommandJournal(path)
    assert journal.begin("first") == "new"
    assert CommandJournal(path).begin("first") == "unknown"
    journal.finish("first", {"status": "done", "result": {"node": "node-a"}})
    assert CommandJournal(path).begin("first") == {"status": "done", "result": {"node": "node-a"}}


def test_controller_rejects_stale_workload_identity() -> None:
    from app.runtime_v2.runner import verify_identity

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 2, "node-a")
    obj = {"metadata": {"labels": {**identity.labels, "workspace.myworkspace.io/generation": "1"}}}
    with pytest.raises(ValueError, match="identity"):
        verify_identity(obj, identity)


def test_create_submits_only_fixed_objects_after_namespace_and_policy_check() -> None:
    from app.runtime_v2.runner import KubernetesRunner

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        def __init__(self) -> None:
            self.created: list[JsonObject] = []

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "namespace":
                return {
                    "metadata": {
                        "labels": {
                            key: identity.labels[key]
                            for key in (
                                "workspace.myworkspace.io/workspace",
                                "workspace.myworkspace.io/instance",
                            )
                        }
                    }
                }
            if kind == "networkpolicy":
                return KubernetesRuntime.network_policy(identity)
            return None

        def create(self, item: JsonObject) -> JsonObject:
            self.created.append(item)
            return item

    from app.runtime_v2.kubernetes import KubernetesRuntime

    api = Api()
    runner = KubernetesRunner(api)
    runner.create(
        identity,
        {"image": "example/image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10},
    )
    assert [o["kind"] for o in api.created] == ["PersistentVolumeClaim", "Deployment"]


def test_stop_checks_identity_then_scales_with_resource_version() -> None:
    from app.runtime_v2.runner import KubernetesRunner

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            return {
                "metadata": {"labels": identity.labels, "resourceVersion": "42", "uid": "dep-uid"},
                "spec": {"replicas": 1},
            }

        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            self.commands.append(args)
            return ""

    api = Api()
    result = KubernetesRunner(api).scale(identity, 0)
    args = api.commands[0]
    patch = json.loads(args[args.index("--patch") + 1])
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "42"} in patch
    assert {"op": "replace", "path": "/spec/replicas", "value": 0} in patch
    assert result["ready"] is False


def test_observe_reports_actual_pod_and_rejects_multiple_replicas() -> None:
    from app.runtime_v2.runner import KubernetesRunner

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    pod: JsonObject = {
        "metadata": {"labels": identity.labels, "uid": "real-pod"},
        "spec": {"nodeName": "node-a"},
        "status": {
            "phase": "Running",
            "podIP": "10.0.1.4",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }

    class Api(StubKubectl):
        def __init__(self) -> None:
            self.items = [pod]

        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            return json.dumps({"items": self.items})

    api = Api()
    runner = KubernetesRunner(api)
    result = runner.observe(identity)
    assert result["pod_uid"] == "real-pod"
    assert result["node"] == "node-a"
    assert result["ready"] is True
    api.items.append(pod)
    with pytest.raises(ValueError, match="multiple"):
        runner.observe(identity)


def test_observe_selects_environment_before_verifying_generation() -> None:
    from app.runtime_v2.runner import KubernetesRunner

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 2, "node-a")

    class Api(StubKubectl):
        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            selector = args[args.index("-l") + 1]
            assert "generation" not in selector
            return json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "labels": {
                                    **ident.labels,
                                    "workspace.myworkspace.io/generation": "1",
                                }
                            }
                        }
                    ]
                }
            )

    with pytest.raises(ValueError, match="identity"):
        KubernetesRunner(Api()).observe(ident)


def test_controller_never_acknowledges_a_pending_create(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal, Controller

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Runner(StubRunner):
        def validate_create(self, identity: RuntimeIdentity, payload: JsonObject) -> None:
            pass

        def create(
            self,
            identity: RuntimeIdentity,
            payload: JsonObject,
            *,
            operation_id: str | None = None,
            receipt: Callable[[str, str], None] | None = None,
        ) -> JsonObject:
            return {"accepted": True, "ready": False}

        def prepare_lifecycle(self, identity: RuntimeIdentity, kind: str) -> JsonObject:
            return {"deployment": "dep", "pvc": "pvc"}

        def reconcile_lifecycle(
            self, identity: RuntimeIdentity, kind: str, provenance: JsonObject
        ) -> JsonObject:
            return {"ready": False, "pod_uid": None}

    class Workspace(StubWorkspace):
        def __init__(self) -> None:
            self.sent: list[tuple[str, JsonObject]] = []

        def post(self, path: str, body: JsonObject, *, timeout: float = 30) -> JsonObject:
            self.sent.append((path, body))
            return {}

    workspace = Workspace()
    journal = CommandJournal(str(tmp_path / "journal.db"))
    controller = Controller(
        Runner(),
        workspace,
        journal,
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
        attempts=1,
        interval=0,
    )
    command: JsonObject = {
        "id": str(uuid.uuid4()),
        "kind": "create_env",
        "payload": {
            "env_id": str(ident.environment_id),
            "host_id": str(ident.workspace_id),
            "runtime_identity": {
                "workspace_id": str(ident.workspace_id),
                "instance_id": str(ident.instance_id),
                "env_id": str(ident.environment_id),
                "allocation_id": str(ident.allocation_id),
                "generation": ident.generation,
                "node_name": ident.node_name,
            },
        },
    }
    controller.process(command)
    assert workspace.sent == []
    assert journal.begin(command["id"]) == "unknown"


def test_controller_reports_only_observed_ready_and_reuses_receipt(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal, Controller

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Runner(StubRunner):
        count = 0

        def validate_create(self, identity: RuntimeIdentity, payload: JsonObject) -> None:
            pass

        def create(
            self,
            identity: RuntimeIdentity,
            payload: JsonObject,
            *,
            operation_id: str | None = None,
            receipt: Callable[[str, str], None] | None = None,
        ) -> JsonObject:
            self.count += 1
            return {}

        def prepare_lifecycle(self, identity: RuntimeIdentity, kind: str) -> JsonObject:
            return {"deployment": "dep", "pvc": "pvc"}

        def reconcile_lifecycle(
            self, identity: RuntimeIdentity, kind: str, provenance: JsonObject
        ) -> JsonObject:
            return {"ready": True, "pod_uid": "actual", "node": "node-a"}

    class Workspace(StubWorkspace):
        def __init__(self) -> None:
            self.sent: list[JsonObject] = []

        def post(self, path: str, body: JsonObject, *, timeout: float = 30) -> JsonObject:
            self.sent.append(body)
            return {}

    runner, workspace = Runner(), Workspace()
    controller = Controller(
        runner,
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
    )
    command: JsonObject = {
        "id": str(uuid.uuid4()),
        "kind": "create_env",
        "payload": {
            "env_id": str(ident.environment_id),
            "host_id": str(ident.workspace_id),
            "runtime_identity": {
                "workspace_id": str(ident.workspace_id),
                "instance_id": str(ident.instance_id),
                "env_id": str(ident.environment_id),
                "allocation_id": str(ident.allocation_id),
                "generation": ident.generation,
                "node_name": ident.node_name,
            },
        },
    }
    controller.process(command)
    controller.process(command)
    assert runner.count == 1
    assert len(workspace.sent) == 2
    assert workspace.sent[0]["result"]["pod_uid"] == "actual"


def test_controller_polls_with_supplied_host_metrics(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal, Controller

    class Workspace(StubWorkspace):
        def __init__(self) -> None:
            self.calls: list[tuple[str, JsonObject]] = []

        def post(self, path: str, body: JsonObject, *, timeout: float = 30) -> JsonObject:
            self.calls.append((path, body))
            return {"commands": []}

    workspace = Workspace()
    controller = Controller(
        StubRunner(),
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        node_name="node-a",
    )
    metrics: JsonObject = {
        "agent_version": "1",
        "docker_version": "",
        "runtimes": ["gvisor"],
        "kvm": False,
        "cpus": 8,
        "mem_bytes": 1,
        "disk_bytes": 2,
        "disk_free_bytes": 1,
        "wait_seconds": 1,
    }
    controller.poll_once(metrics)
    assert workspace.calls == [("/poll", metrics)]


def test_rejected_legacy_create_is_reported_without_mutation(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal, Controller
    from app.runtime_v2.runner import KubernetesRunner

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Workspace(StubWorkspace):
        def __init__(self) -> None:
            self.sent: list[JsonObject] = []

        def post(self, path: str, body: JsonObject, *, timeout: float = 30) -> JsonObject:
            self.sent.append(body)
            return {}

    workspace = Workspace()
    controller = Controller(
        KubernetesRunner(StubKubectl()),
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
    )
    command: JsonObject = {
        "id": str(uuid.uuid4()),
        "kind": "create_env",
        "payload": {
            "env_id": str(ident.environment_id),
            "host_id": str(ident.workspace_id),
            "snapshot": {"id": "legacy"},
            "runtime_identity": {
                "workspace_id": str(ident.workspace_id),
                "instance_id": str(ident.instance_id),
                "env_id": str(ident.environment_id),
                "allocation_id": str(ident.allocation_id),
                "generation": ident.generation,
                "node_name": ident.node_name,
            },
        },
    }
    controller.process(command)
    assert workspace.sent[0]["status"] == "failed"
    assert "unsupported" in workspace.sent[0]["error"]


def test_create_rejects_secret_values_without_acknowledging_them_as_injected() -> None:
    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    payload = {
        "image": "example/image@sha256:" + "a" * 64,
        "cpus": 1,
        "memory_mb": 256,
        "disk_gb": 10,
        "secret_values": {"secret": "value"},
    }
    with pytest.raises(ValueError, match="not supported"):
        workload(ident, payload)


def test_delete_uses_uid_precondition_and_waits_for_pods_before_removing_claim() -> None:
    from app.runtime_v2.runner import KubernetesRunner, OutcomeUnknown

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], JsonObject]] = []

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "deployment":
                return {"metadata": {"uid": "dep-uid", "labels": ident.labels}}
            raise AssertionError("claim must not be read while deployment remains")

        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            assert input_data is not None
            self.calls.append((args, json.loads(input_data)))
            return "{}"

    api = Api()
    with pytest.raises(OutcomeUnknown):
        KubernetesRunner(api).delete(ident)
    args, body = api.calls[0]
    assert args[0:2] == ["delete", "--raw"]
    assert body["preconditions"]["uid"] == "dep-uid"
    assert body["propagationPolicy"] == "Foreground"


def test_controller_acknowledges_delete_only_after_volume_removal(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal, Controller
    from app.runtime_v2.runner import PendingDeletion

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Runner(StubRunner):
        def __init__(self) -> None:
            self.calls = 0

        def prepare_lifecycle(self, identity: RuntimeIdentity, kind: str) -> JsonObject:
            return {"deployment": "dep", "pvc": "pvc"}

        def reconcile_lifecycle(
            self, identity: RuntimeIdentity, kind: str, provenance: JsonObject
        ) -> JsonObject:
            return {"deleted": self.calls >= 2}

        def delete(self, identity: RuntimeIdentity) -> JsonObject:
            self.calls += 1
            if self.calls == 1:
                raise PendingDeletion("deleting")
            return {"deleted": True}

    class Workspace(StubWorkspace):
        def __init__(self) -> None:
            self.sent: list[JsonObject] = []

        def post(self, path: str, body: JsonObject, *, timeout: float = 30) -> JsonObject:
            self.sent.append(body)
            return {}

    runner, workspace = Runner(), Workspace()
    controller = Controller(
        runner,
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
        attempts=2,
        interval=0,
    )
    command: JsonObject = {
        "id": str(uuid.uuid4()),
        "kind": "delete_env",
        "payload": {
            "env_id": str(ident.environment_id),
            "host_id": str(ident.workspace_id),
            "runtime_identity": {
                "workspace_id": str(ident.workspace_id),
                "instance_id": str(ident.instance_id),
                "env_id": str(ident.environment_id),
                "allocation_id": str(ident.allocation_id),
                "generation": ident.generation,
                "node_name": ident.node_name,
            },
        },
    }
    controller.process(command)
    assert runner.calls == 2
    assert workspace.sent[0]["result"]["deleted"] is True


def test_delete_does_not_follow_a_recreated_deployment_with_new_uid() -> None:
    from app.runtime_v2.runner import KubernetesRunner, OutcomeUnknown

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        uid = "first"
        calls = 0

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            return {"metadata": {"uid": self.uid, "labels": ident.labels}}

        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            self.calls += 1
            return "{}"

    api = Api()
    runner = KubernetesRunner(api)
    with pytest.raises(OutcomeUnknown):
        runner.delete(ident)
    api.uid = "replacement"
    with pytest.raises(OutcomeUnknown):
        runner.delete(ident)
    assert api.calls == 1


def test_private_service_requires_observed_api_receipt_before_applied() -> None:
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    service_id = uuid.uuid4()

    class Api(StubKubectl):
        def __init__(self) -> None:
            self.saved: JsonObject | None = None

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            if kind == "networkpolicy":
                return KubernetesRuntime.network_policy(ident)
            return self.saved

        def create(self, obj: JsonObject) -> JsonObject:
            self.saved = obj
            return obj

    api = Api()
    runner = KubernetesRunner(api)
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service_id, 4, 5432, "TCP")
    assert api.saved is not None
    assert api.saved["spec"]["type"] == "ClusterIP"
    assert "nodePort" not in api.saved["spec"]["ports"][0]
    api.saved["metadata"]["uid"] = "actual-service-uid"
    api.saved["metadata"]["resourceVersion"] = "42"
    api.saved["spec"]["clusterIP"] = "10.43.0.12"
    receipt = runner.apply_private_service(ident, service_id, 4, 5432, "TCP")
    assert receipt["applied_revision"] == 4
    assert receipt["cluster_ip"] == "10.43.0.12"
    assert receipt["resource_version"] == "42"


def test_private_service_without_server_uid_is_not_an_applied_receipt() -> None:
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        service: JsonObject | None = None

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            return (
                KubernetesRuntime.network_policy(ident) if kind == "networkpolicy" else self.service
            )

        def create(self, obj: JsonObject) -> JsonObject:
            self.service = obj
            return obj

    api = Api()
    runner = KubernetesRunner(api)
    service = uuid.uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 1, 80, "TCP")
    assert api.service is not None
    api.service["spec"]["clusterIP"] = "10.0.0.1"
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 1, 80, "TCP")


def test_domain_policy_apply_requires_real_server_receipt() -> None:
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        policy: JsonObject | None = None

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "namespace":
                return {
                    "metadata": {
                        "labels": {
                            k: v
                            for k, v in ident.labels.items()
                            if k.endswith("/workspace") or k.endswith("/instance")
                        }
                    }
                }
            return self.policy

        def create(self, obj: JsonObject) -> JsonObject:
            self.policy = obj
            return obj

    api = Api()
    runner = KubernetesRunner(api)
    with pytest.raises(PendingApply):
        runner.apply_domain_policy(ident)
    with pytest.raises(PendingApply):
        runner.apply_domain_policy(ident)
    assert api.policy is not None
    api.policy["metadata"]["uid"] = "server-policy-uid"
    assert runner.apply_domain_policy(ident)["uid"] == "server-policy-uid"


def test_service_revision_update_is_resource_version_guarded() -> None:
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        def __init__(self) -> None:
            self.service: JsonObject | None = None
            self.calls: list[list[str]] = []

        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            return (
                KubernetesRuntime.network_policy(ident) if kind == "networkpolicy" else self.service
            )

        def create(self, obj: JsonObject) -> JsonObject:
            self.service = obj
            return obj

        def call(self, args: list[str], *, input_data: str | None = None) -> str:
            self.calls.append(args)
            return "{}"

    api = Api()
    runner = KubernetesRunner(api)
    service = uuid.uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 1, 80, "TCP")
    assert api.service is not None
    api.service["metadata"].update({"uid": "uid", "resourceVersion": "42"})
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 2, 81, "TCP")
    args = api.calls[0]
    patch = json.loads(args[args.index("--patch") + 1])
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "42"} in patch
    assert {"op": "test", "path": "/metadata/uid", "value": "uid"} in patch


def test_service_apply_rejects_foreign_namespace_even_with_policy() -> None:
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api(StubKubectl):
        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> JsonObject | None:
            if kind == "namespace":
                return {"metadata": {"labels": {}}}
            if kind == "networkpolicy":
                return KubernetesRuntime.network_policy(ident)
            return None

        def create(self, obj: JsonObject) -> JsonObject:
            raise AssertionError("must not write")

    with pytest.raises(ValueError, match="namespace"):
        KubernetesRunner(Api()).apply_private_service(ident, uuid.uuid4(), 1, 80, "TCP")
