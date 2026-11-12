import json
import subprocess
import uuid

import pytest

from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl, OutcomeUnknown, workload


def test_workload_binds_claim_and_node_without_privileges():
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


def test_kubectl_timeout_reports_unknown_completion(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 3)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(OutcomeUnknown):
        Kubectl("/etc/kubeconfig").call(["create", "-f", "-"], input_data=json.dumps({}))


def test_deployment_uses_controller_compatible_restart_policy():
    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    _, deployment = workload(
        identity,
        {"image": "example/image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10},
    )
    assert deployment["spec"]["template"]["spec"]["restartPolicy"] == "Always"


def test_command_journal_survives_restart_and_never_replays_unknown(tmp_path):
    from app.runtime_v2.controller import CommandJournal

    path = str(tmp_path / "receipts.db")
    journal = CommandJournal(path)
    assert journal.begin("first") == "new"
    assert CommandJournal(path).begin("first") == "unknown"
    journal.finish("first", {"status": "done", "result": {"node": "node-a"}})
    assert CommandJournal(path).begin("first") == {"status": "done", "result": {"node": "node-a"}}


def test_controller_rejects_stale_workload_identity():
    from app.runtime_v2.runner import verify_identity

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 2, "node-a")
    obj = {"metadata": {"labels": {**identity.labels, "workspace.myworkspace.io/generation": "1"}}}
    with pytest.raises(ValueError, match="identity"):
        verify_identity(obj, identity)


def test_create_submits_only_fixed_objects_after_namespace_and_policy_check():
    from app.runtime_v2.runner import KubernetesRunner

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        def __init__(self):
            self.created = []

        def get_optional(self, kind, name, namespace=None):
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

        def create(self, item):
            self.created.append(item)

    from app.runtime_v2.kubernetes import KubernetesRuntime

    api = Api()
    runner = KubernetesRunner(api)
    runner.create(
        identity,
        {"image": "example/image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10},
    )
    assert [o["kind"] for o in api.created] == ["PersistentVolumeClaim", "Deployment"]


def test_stop_checks_identity_then_scales_with_resource_version():
    from app.runtime_v2.runner import KubernetesRunner

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        def __init__(self):
            self.commands = []

        def get_optional(self, kind, name, namespace=None):
            return {
                "metadata": {"labels": identity.labels, "resourceVersion": "42", "uid": "dep-uid"},
                "spec": {"replicas": 1},
            }

        def call(self, args):
            self.commands.append(args)
            return ""

    api = Api()
    result = KubernetesRunner(api).scale(identity, 0)
    args = api.commands[0]
    patch = json.loads(args[args.index("--patch") + 1])
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "42"} in patch
    assert {"op": "replace", "path": "/spec/replicas", "value": 0} in patch
    assert result["ready"] is False


def test_observe_reports_actual_pod_and_rejects_multiple_replicas():
    from app.runtime_v2.runner import KubernetesRunner

    identity = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    pod = {
        "metadata": {"labels": identity.labels, "uid": "real-pod"},
        "spec": {"nodeName": "node-a"},
        "status": {
            "phase": "Running",
            "podIP": "10.0.1.4",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }

    class Api:
        def __init__(self):
            self.items = [pod]

        def call(self, args):
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


def test_observe_selects_environment_before_verifying_generation():
    from app.runtime_v2.runner import KubernetesRunner

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 2, "node-a")

    class Api:
        def call(self, args):
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


def test_controller_never_acknowledges_a_pending_create(tmp_path):
    from app.runtime_v2.controller import CommandJournal, Controller

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Runner:
        def validate_create(self, identity, payload):
            pass

        def create(self, identity, payload, **kwargs):
            return {"accepted": True, "ready": False}

        def prepare_lifecycle(self, identity, kind):
            return {"deployment": "dep", "pvc": "pvc"}

        def reconcile_lifecycle(self, identity, kind, provenance):
            return {"ready": False, "pod_uid": None}

    class Workspace:
        def __init__(self):
            self.sent = []

        def post(self, path, body, **kwargs):
            self.sent.append((path, body))

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
    command = {
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


def test_controller_reports_only_observed_ready_and_reuses_receipt(tmp_path):
    from app.runtime_v2.controller import CommandJournal, Controller

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Runner:
        count = 0

        def validate_create(self, identity, payload):
            pass

        def create(self, identity, payload, **kwargs):
            self.count += 1

        def prepare_lifecycle(self, identity, kind):
            return {"deployment": "dep", "pvc": "pvc"}

        def reconcile_lifecycle(self, identity, kind, provenance):
            return {"ready": True, "pod_uid": "actual", "node": "node-a"}

    class Workspace:
        def __init__(self):
            self.sent = []

        def post(self, path, body, **kwargs):
            self.sent.append(body)

    runner, workspace = Runner(), Workspace()
    controller = Controller(
        runner,
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
    )
    command = {
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


def test_controller_polls_with_supplied_host_metrics(tmp_path):
    from app.runtime_v2.controller import CommandJournal, Controller

    class Workspace:
        def __init__(self):
            self.calls = []

        def post(self, path, body, **kwargs):
            self.calls.append((path, body))
            return {"commands": []}

    workspace = Workspace()
    controller = Controller(
        object(),
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        node_name="node-a",
    )
    metrics = {
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


def test_rejected_legacy_create_is_reported_without_mutation(tmp_path):
    from app.runtime_v2.controller import CommandJournal, Controller
    from app.runtime_v2.runner import KubernetesRunner

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Workspace:
        def __init__(self):
            self.sent = []

        def post(self, path, body, **kwargs):
            self.sent.append(body)

    workspace = Workspace()
    controller = Controller(
        KubernetesRunner(object()),
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
    )
    command = {
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


def test_create_rejects_secret_values_without_acknowledging_them_as_injected():
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


def test_delete_uses_uid_precondition_and_waits_for_pods_before_removing_claim():
    from app.runtime_v2.runner import KubernetesRunner, OutcomeUnknown

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        def __init__(self):
            self.calls = []

        def get_optional(self, kind, name, namespace=None):
            if kind == "deployment":
                return {"metadata": {"uid": "dep-uid", "labels": ident.labels}}
            raise AssertionError("claim must not be read while deployment remains")

        def call(self, args, *, input_data=None):
            self.calls.append((args, json.loads(input_data)))
            return "{}"

    api = Api()
    with pytest.raises(OutcomeUnknown):
        KubernetesRunner(api).delete(ident)
    args, body = api.calls[0]
    assert args[0:2] == ["delete", "--raw"]
    assert body["preconditions"]["uid"] == "dep-uid"
    assert body["propagationPolicy"] == "Foreground"


def test_controller_acknowledges_delete_only_after_volume_removal(tmp_path):
    from app.runtime_v2.controller import CommandJournal, Controller
    from app.runtime_v2.runner import PendingDeletion

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Runner:
        def __init__(self):
            self.calls = 0

        def prepare_lifecycle(self, identity, kind):
            return {"deployment": "dep", "pvc": "pvc"}

        def reconcile_lifecycle(self, identity, kind, provenance):
            return {"deleted": self.calls >= 2}

        def delete(self, identity):
            self.calls += 1
            if self.calls == 1:
                raise PendingDeletion("deleting")
            return {"deleted": True}

    class Workspace:
        def __init__(self):
            self.sent = []

        def post(self, path, body, **kwargs):
            self.sent.append(body)

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
    command = {
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


def test_delete_does_not_follow_a_recreated_deployment_with_new_uid():
    from app.runtime_v2.runner import KubernetesRunner, OutcomeUnknown

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        uid = "first"
        calls = 0

        def get_optional(self, kind, name, namespace=None):
            return {"metadata": {"uid": self.uid, "labels": ident.labels}}

        def call(self, args, *, input_data=None):
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


def test_private_service_requires_observed_api_receipt_before_applied():
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")
    service_id = uuid.uuid4()

    class Api:
        def __init__(self):
            self.saved = None

        def get_optional(self, kind, name, namespace=None):
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            if kind == "networkpolicy":
                return KubernetesRuntime.network_policy(ident)
            return self.saved

        def create(self, obj):
            self.saved = obj

    api = Api()
    runner = KubernetesRunner(api)
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service_id, 4, 5432, "TCP")
    assert api.saved["spec"]["type"] == "ClusterIP"
    assert "nodePort" not in api.saved["spec"]["ports"][0]
    api.saved["metadata"]["uid"] = "actual-service-uid"
    api.saved["spec"]["clusterIP"] = "10.43.0.12"
    receipt = runner.apply_private_service(ident, service_id, 4, 5432, "TCP")
    assert receipt["applied_revision"] == 4
    assert receipt["cluster_ip"] == "10.43.0.12"


def test_private_service_without_server_uid_is_not_an_applied_receipt():
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        service = None

        def get_optional(self, kind, name, namespace=None):
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            return (
                KubernetesRuntime.network_policy(ident) if kind == "networkpolicy" else self.service
            )

        def create(self, obj):
            self.service = obj

    api = Api()
    runner = KubernetesRunner(api)
    service = uuid.uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 1, 80, "TCP")
    api.service["spec"]["clusterIP"] = "10.0.0.1"
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 1, 80, "TCP")


def test_domain_policy_apply_requires_real_server_receipt():
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        policy = None

        def get_optional(self, kind, name, namespace=None):
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

        def create(self, obj):
            self.policy = obj

    api = Api()
    runner = KubernetesRunner(api)
    with pytest.raises(PendingApply):
        runner.apply_domain_policy(ident)
    with pytest.raises(PendingApply):
        runner.apply_domain_policy(ident)
    api.policy["metadata"]["uid"] = "server-policy-uid"
    assert runner.apply_domain_policy(ident)["uid"] == "server-policy-uid"


def test_service_revision_update_is_resource_version_guarded():
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner, PendingApply

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        def __init__(self):
            self.service = None
            self.calls = []

        def get_optional(self, kind, name, namespace=None):
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            return (
                KubernetesRuntime.network_policy(ident) if kind == "networkpolicy" else self.service
            )

        def create(self, obj):
            self.service = obj

        def call(self, args, **kwargs):
            self.calls.append(args)
            return "{}"

    api = Api()
    runner = KubernetesRunner(api)
    service = uuid.uuid4()
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 1, 80, "TCP")
    api.service["metadata"].update({"uid": "uid", "resourceVersion": "42"})
    with pytest.raises(PendingApply):
        runner.apply_private_service(ident, service, 2, 81, "TCP")
    args = api.calls[0]
    patch = json.loads(args[args.index("--patch") + 1])
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "42"} in patch
    assert {"op": "test", "path": "/metadata/uid", "value": "uid"} in patch


def test_service_apply_rejects_foreign_namespace_even_with_policy():
    from app.runtime_v2.kubernetes import KubernetesRuntime
    from app.runtime_v2.runner import KubernetesRunner

    ident = RuntimeIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, "node-a")

    class Api:
        def get_optional(self, kind, name, namespace=None):
            if kind == "namespace":
                return {"metadata": {"labels": {}}}
            if kind == "networkpolicy":
                return KubernetesRuntime.network_policy(ident)
            return None

        def create(self, obj):
            raise AssertionError("must not write")

    with pytest.raises(ValueError, match="namespace"):
        KubernetesRunner(Api()).apply_private_service(ident, uuid.uuid4(), 1, 80, "TCP")
