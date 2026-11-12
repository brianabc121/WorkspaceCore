"""Outcome tests at the fake external Kubernetes API boundary."""

import json
from uuid import uuid4

import pytest

from app.runtime_v2.controller import CommandJournal, Controller
from app.runtime_v2.kubernetes import KubernetesRuntime, RuntimeIdentity
from app.runtime_v2.runner import Kubectl, KubernetesRunner, OutcomeUnknown


def identity():
    return RuntimeIdentity(uuid4(), uuid4(), uuid4(), uuid4(), 3, "node-a")


def command(ident, kind="stop_env"):
    return {
        "id": str(uuid4()),
        "kind": kind,
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
            "start_command": ["echo", "do-not-persist-this-secret"],
        },
    }


class Workspace:
    def __init__(self):
        self.sent = []

    def post(self, path, body, **kwargs):
        self.sent.append((path, body))
        return {"commands": []}


class Api:
    def __init__(self, ident):
        self.deployment = {
            "metadata": {
                "uid": "dep-original",
                "resourceVersion": "17",
                "name": ident.pod_name,
                "namespace": ident.namespace,
                "labels": ident.labels,
                "generation": 1,
            },
            "spec": {"replicas": 1},
            "status": {"observedGeneration": 1},
        }
        self.claim = {
            "metadata": {
                "uid": "pvc-original",
                "name": "data-" + ident.environment_id.hex,
                "namespace": ident.namespace,
                "labels": ident.labels,
            }
        }
        self.pods = []
        self.mutations = []
        self.timeout = True

    def get_optional(self, kind, name, namespace=None):
        return self.deployment if kind == "deployment" else self.claim

    def call(self, args, **kwargs):
        if args[0] == "get":
            return json.dumps({"items": self.pods})
        self.mutations.append(args)
        if self.timeout:
            raise OutcomeUnknown("request outcome unknown")
        return "{}"


def controller(tmp_path, ident, api, workspace):
    return Controller(
        KubernetesRunner(api),
        workspace,
        CommandJournal(str(tmp_path / "journal.db")),
        workspace_id=ident.workspace_id,
        host_id=ident.workspace_id,
        node_name=ident.node_name,
        attempts=1,
        interval=0,
    )


def test_restart_proves_stop_read_only_without_replaying_scale(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    first = controller(tmp_path, ident, api, workspace)
    first.process(cmd)
    assert workspace.sent == []
    api.deployment["spec"]["replicas"] = 0
    second = controller(tmp_path, ident, api, workspace)
    second.process(cmd)
    assert len(api.mutations) == 1
    assert workspace.sent[0][1]["status"] == "done"
    assert b"do-not-persist-this-secret" not in (tmp_path / "journal.db").read_bytes()


def test_restart_replaced_uid_stays_unknown(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    controller(tmp_path, ident, api, workspace).process(cmd)
    api.deployment["metadata"]["uid"] = "replacement"
    api.deployment["spec"]["replicas"] = 0
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert workspace.sent == []
    assert len(api.mutations) == 1


def test_stop_is_not_proven_by_empty_pods_while_replicas_still_one(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    api.timeout = False
    controller(tmp_path, ident, api, workspace).process(command(ident))
    assert workspace.sent == []


def test_configured_scope_cannot_be_retargeted_by_payload(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    cmd["payload"]["runtime_identity"]["workspace_id"] = str(uuid4())
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert api.mutations == []
    assert workspace.sent[0][1]["status"] == "failed"


def test_node_affinity_binds_actual_metadata_name():
    ident = identity()
    pod = KubernetesRuntime.pod_manifest(ident, "image@sha256:" + "a" * 64, 500, 256)
    term = pod["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]
    assert term["matchFields"] == [{"key": "metadata.name", "operator": "In", "values": ["node-a"]}]


def test_kubectl_error_does_not_echo_external_secret(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "secret-from-cluster"),
    )
    with pytest.raises(RuntimeError) as error:
        Kubectl("/etc/kubeconfig").call(["get", "pods"])
    assert "secret-from-cluster" not in str(error.value)


def test_cli_requires_operator_scope_and_absolute_paths():
    from app.runtime_v2.config import ControllerConfig

    with pytest.raises(ValueError):
        ControllerConfig.from_environ({})
    settings = {
        "RUNTIME_V2_KUBECONFIG": "/etc/kubeconfig",
        "RUNTIME_V2_JOURNAL": "/var/lib/runtime/receipt.db",
        "RUNTIME_V2_WORKSPACE_ID": str(uuid4()),
        "RUNTIME_V2_HOST_ID": str(uuid4()),
        "RUNTIME_V2_NODE_NAME": "node-a",
        "RUNTIME_V2_URL": "https://workspace.invalid",
        "RUNTIME_V2_TOKEN": "operator-secret",
    }
    config = ControllerConfig.from_environ(settings)
    assert config.node_name == "node-a"
    assert "operator-secret" not in repr(config)
    settings["RUNTIME_V2_KUBECONFIG"] = "relative"
    with pytest.raises(ValueError):
        ControllerConfig.from_environ(settings)


def test_startup_recovers_without_server_redelivery(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    controller(tmp_path, ident, api, workspace).process(command(ident))
    api.deployment["spec"]["replicas"] = 0
    controller(tmp_path, ident, api, workspace).recover_pending()
    assert workspace.sent[0][1]["status"] == "done"
    assert len(api.mutations) == 1


def test_unknown_delete_restart_only_observes_no_new_delete(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident, "delete_env")
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert workspace.sent == []
    api.deployment = None
    # PVC still exists, so a partial delete must retain its reservation.
    controller(tmp_path, ident, api, workspace).recover_pending()
    assert workspace.sent == []
    assert len(api.mutations) == 1
    api.claim = None
    controller(tmp_path, ident, api, workspace).recover_pending()
    assert workspace.sent[0][1]["result"]["deleted"] is True
    assert len(api.mutations) == 1


@pytest.mark.parametrize("field,value", [("host_id", str(uuid4())), ("host_id", None)])
def test_host_scope_is_required_before_mutation(tmp_path, field, value):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    cmd["payload"][field] = value
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert api.mutations == []
    assert workspace.sent[0][1]["status"] == "failed"


def test_changed_command_identity_does_not_reuse_completed_receipt(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    controller(tmp_path, ident, api, workspace).process(cmd)
    api.deployment["spec"]["replicas"] = 0
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert len(workspace.sent) == 1
    cmd["payload"]["runtime_identity"]["allocation_id"] = str(uuid4())
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert len(workspace.sent) == 1


@pytest.mark.parametrize("field,value", [("namespace", "foreign"), ("uid", ""), ("name", "other")])
def test_malformed_resource_never_proves_stop(tmp_path, field, value):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    controller(tmp_path, ident, api, workspace).process(cmd)
    api.deployment["spec"]["replicas"] = 0
    api.deployment["metadata"][field] = value
    controller(tmp_path, ident, api, workspace).recover_pending()
    assert workspace.sent == []


def test_trusted_node_identity_hash_and_unavailable_capacity_fail_closed():
    import hashlib

    from app.runtime_v2.telemetry import NodeTelemetry, TelemetryUnavailable

    class NodeApi:
        def __init__(self):
            self.node = {
                "metadata": {"name": "node-a", "uid": "node-uid"},
                "status": {"nodeInfo": {"machineID": " AABBCCDDEEFF00112233445566778899 "}},
            }

        def get_optional(self, kind, name, namespace=None):
            assert kind == "node" and name == "node-a" and namespace is None
            return self.node

    api = NodeApi()
    telemetry = NodeTelemetry(api, "node-a")
    assert (
        telemetry.physical_host_id()
        == hashlib.sha256(b"machine-id:aabbccddeeff00112233445566778899").hexdigest()
    )
    capacity = telemetry.capacity()
    assert capacity["cpus"] is None and capacity["disk_free_bytes"] is None
    assert capacity["runtimes"] == [] and capacity["docker_version"] is None
    api.node["metadata"]["name"] = "other-node"
    with pytest.raises(TelemetryUnavailable):
        telemetry.physical_host_id()


def test_disabled_cli_never_connects(monkeypatch):
    from app.runtime_v2.__main__ import main

    monkeypatch.setattr("sys.argv", ["runtime-v2"])
    monkeypatch.delenv("RUNTIME_V2_ENABLED", raising=False)
    assert main() == 2


def test_unavailable_telemetry_never_sends_zero_capacity_or_report():
    from app.runtime_v2.telemetry import NodeTelemetry, TelemetryUnavailable

    class Api:
        def get_optional(self, *args, **kwargs):
            return {
                "metadata": {"name": "node-a", "uid": "node-uid"},
                "status": {"nodeInfo": {"machineID": "a" * 32}},
            }

    workspace = Workspace()
    telemetry = NodeTelemetry(Api(), "node-a")
    with pytest.raises(TelemetryUnavailable):
        telemetry.report(workspace)
    assert all(path != "/report" for path, body in workspace.sent)


def test_authentication_failure_stops_loop_without_logging_token(tmp_path, caplog):
    from threading import Event

    from app.runner_agent.client import WorkspaceError
    from app.runtime_v2.__main__ import run

    ident, workspace = identity(), Workspace()
    current = controller(tmp_path, ident, Api(ident), workspace)

    class Telemetry:
        def capacity(self):
            raise WorkspaceError(401, "AUTH", "sensitive-operator-token")

    assert run(current, Telemetry(), Event()) == 3
    assert "sensitive-operator-token" not in caplog.text


def test_loop_uses_bounded_interruptible_backoff(tmp_path):
    from app.runtime_v2.__main__ import run
    from app.runtime_v2.telemetry import TelemetryUnavailable

    ident, workspace = identity(), Workspace()
    current = controller(tmp_path, ident, Api(ident), workspace)

    class Telemetry:
        def capacity(self):
            raise TelemetryUnavailable("unavailable")

    class Stop:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return len(self.waits) >= 8

        def wait(self, delay):
            self.waits.append(delay)
            return self.is_set()

    stop = Stop()
    assert run(current, Telemetry(), stop) == 0
    assert stop.waits == [1, 2, 4, 8, 16, 32, 60, 60]


def test_recovery_does_not_ack_stale_deployment_observation(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    api.deployment["metadata"]["generation"] = 2
    cmd = command(ident)
    controller(tmp_path, ident, api, workspace).process(cmd)
    api.deployment["spec"]["replicas"] = 0
    controller(tmp_path, ident, api, workspace).recover_pending()
    assert workspace.sent == []


def test_tls_verification_cannot_be_disabled_by_kubeconfig(monkeypatch):
    import subprocess

    seen = []

    def execute(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", execute)
    Kubectl("/etc/kubeconfig").call(["get", "node", "node-a"])
    assert "--insecure-skip-tls-verify=false" in seen[0][0]
    assert seen[0][1]["timeout"] <= 120


def test_unknown_capacity_heartbeat_still_polls(tmp_path):
    from threading import Event

    from app.runtime_v2.__main__ import run
    from app.runtime_v2.telemetry import NodeTelemetry

    ident, workspace = identity(), Workspace()
    stop = Event()

    def post(path, body, **kwargs):
        workspace.sent.append((path, body))
        if path == "/poll":
            stop.set()
        return {"commands": []}

    workspace.post = post
    current = controller(tmp_path, ident, Api(ident), workspace)
    assert run(current, NodeTelemetry(Api(ident), ident.node_name), stop) == 0
    body = workspace.sent[0][1]
    assert workspace.sent[0][0] == "/poll"
    assert all(body[key] is None for key in ["cpus", "mem_bytes", "disk_bytes", "disk_free_bytes"])
    assert all(path != "/report" for path, _ in workspace.sent)


def test_scale_fences_both_uid_and_opaque_resource_version():
    ident = identity()
    api = Api(ident)
    api.timeout = False
    api.deployment["metadata"]["resourceVersion"] = "opaque-version"
    KubernetesRunner(api).scale(ident, 0)
    args = api.mutations[0]
    assert args[:2] == ["patch", "deployment"]
    patch = json.loads(args[args.index("--patch") + 1])
    assert {"op": "test", "path": "/metadata/uid", "value": "dep-original"} in patch
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "opaque-version"} in patch


@pytest.mark.parametrize("lost_response", [False, True])
def test_pending_create_recovers_only_its_known_uids(tmp_path, lost_response):
    from copy import deepcopy

    ident, workspace = identity(), Workspace()

    class CreateApi(Api):
        def __init__(self):
            super().__init__(ident)
            self.deployment = self.claim = None
            self.created = 0

        def get_optional(self, kind, name, namespace=None):
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            if kind == "networkpolicy":
                return KubernetesRuntime.network_policy(ident)
            if kind == "replicaset":
                return {
                    "metadata": {
                        "name": "rs",
                        "namespace": ident.namespace,
                        "uid": "rs-uid",
                        "labels": ident.labels,
                        "ownerReferences": [
                            {
                                "kind": "Deployment",
                                "name": ident.pod_name,
                                "uid": "dep-original",
                                "controller": True,
                            }
                        ],
                    }
                }
            return super().get_optional(kind, name, namespace)

        def create(self, obj):
            self.created += 1
            obj = deepcopy(obj)
            obj["metadata"].update(
                {
                    "uid": "dep-original" if obj["kind"] == "Deployment" else "pvc-original",
                    "generation": 1,
                }
            )
            if obj["kind"] == "Deployment":
                self.deployment = obj
                self.deployment["status"] = {"observedGeneration": 1, "readyReplicas": 1}
                if lost_response:
                    raise OutcomeUnknown("lost response after create")
            else:
                self.claim = obj
            return obj

    api = CreateApi()
    cmd = command(ident, "create_env")
    cmd["payload"].update(
        {"image": "image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10}
    )
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert workspace.sent == []
    api.pods = [
        {
            "metadata": {
                "name": "pod",
                "namespace": ident.namespace,
                "uid": "pod-uid",
                "labels": ident.labels,
                "ownerReferences": [
                    {"kind": "ReplicaSet", "name": "rs", "uid": "rs-uid", "controller": True}
                ],
            },
            "spec": {"nodeName": ident.node_name},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
        }
    ]
    controller(tmp_path, ident, api, workspace).recover_pending()
    if lost_response:
        assert workspace.sent == []
    else:
        assert workspace.sent[0][1]["status"] == "done"
    assert api.created == 2
    assert b"do-not-persist-this-secret" not in (tmp_path / "journal.db").read_bytes()


def test_config_rejects_plaintext_or_embedded_credentials():
    from app.runtime_v2.config import ControllerConfig

    settings = {
        "RUNTIME_V2_KUBECONFIG": "/etc/kubeconfig",
        "RUNTIME_V2_JOURNAL": "/var/lib/runtime/receipt.db",
        "RUNTIME_V2_WORKSPACE_ID": str(uuid4()),
        "RUNTIME_V2_HOST_ID": str(uuid4()),
        "RUNTIME_V2_NODE_NAME": "node-a",
        "RUNTIME_V2_URL": "http://workspace.invalid",
        "RUNTIME_V2_TOKEN": "operator-secret",
    }
    with pytest.raises(ValueError):
        ControllerConfig.from_environ(settings)
    settings["RUNTIME_V2_URL"] = "https://user:secret@workspace.invalid"
    with pytest.raises(ValueError):
        ControllerConfig.from_environ(settings)


def test_journal_never_keeps_external_error_text(tmp_path):
    journal = CommandJournal(str(tmp_path / "journal.db"))
    journal.begin("receipt")
    journal.finish("receipt", {"status": "failed", "error": "sensitive external secret"})
    assert b"sensitive external secret" not in (tmp_path / "journal.db").read_bytes()


def test_signal_handler_requests_interruptible_shutdown(monkeypatch):
    import signal
    from threading import Event

    from app.runtime_v2.__main__ import install_signal_handlers

    handlers = {}
    monkeypatch.setattr(
        signal, "signal", lambda kind, callback: handlers.setdefault(kind, callback)
    )
    stopping = Event()
    install_signal_handlers(stopping)
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert stopping.is_set()
    assert signal.SIGINT in handlers


def test_kubeconfig_plaintext_endpoint_is_rejected(monkeypatch):
    import subprocess

    config = {"clusters": [{"cluster": {"server": "http://cluster.invalid"}}]}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(config), ""),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        Kubectl("/etc/kubeconfig").validate_connection()


def test_delete_cannot_ack_absence_while_older_write_is_unresolved(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    controller(tmp_path, ident, api, workspace).process(command(ident, "start_env"))
    assert workspace.sent == []
    api.deployment = api.claim = None
    controller(tmp_path, ident, api, workspace).process(command(ident, "delete_env"))
    controller(tmp_path, ident, api, workspace).recover_pending()
    assert workspace.sent == []
    assert len(api.mutations) == 1


def test_completed_receipt_is_not_reacknowledged_for_replaced_object(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    cmd = command(ident)
    controller(tmp_path, ident, api, workspace).process(cmd)
    api.deployment["spec"]["replicas"] = 0
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert len(workspace.sent) == 1
    api.deployment["metadata"]["uid"] = "replacement"
    controller(tmp_path, ident, api, workspace).process(cmd)
    assert len(workspace.sent) == 1


def test_standalone_journal_has_single_controller_owner(tmp_path):
    from app.runtime_v2.__main__ import controller_lock

    path = str(tmp_path / "journal.db")
    with (
        controller_lock(path),
        pytest.raises(RuntimeError, match="already running"),
        controller_lock(path),
    ):
        pytest.fail("second controller must not start")
    with controller_lock(path):
        pass


def test_shutdown_never_starts_a_new_mutation(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    current = controller(tmp_path, ident, api, workspace)
    current.stopping.set()
    current.process(command(ident))
    assert api.mutations == []
    assert workspace.sent == []


def test_malformed_kubernetes_identity_is_rejected_without_crash(tmp_path):
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    api.deployment["metadata"] = "malformed"
    controller(tmp_path, ident, api, workspace).process(command(ident))
    assert api.mutations == []
    assert workspace.sent[0][1]["status"] == "failed"
