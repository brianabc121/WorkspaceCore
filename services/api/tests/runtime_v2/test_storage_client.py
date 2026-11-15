"""Root proof and exact Kubernetes binding; no host storage mutations."""

import copy
import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl, OutcomeUnknown
from app.runtime_v2.storage_client import StorageClient, StorageUnavailable
from app.runtime_v2.storage_host import PROFILE, StorageRequest

UnixServer = tuple[Path, Callable[[bytes], None]]

PREFIX = "workspace.myworkspace.io/"
NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"


def identity() -> RuntimeIdentity:
    return RuntimeIdentity(UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4), 1, "node-a")


class Api(Kubectl):
    def __init__(self) -> None:
        self.node: dict[str, Any] = {
            "metadata": {
                "name": "node-a",
                "uid": str(UUID(int=6)),
                "labels": {NODE_LABEL: "node-a"},
            },
            "status": {"nodeInfo": {"machineID": "a" * 32}},
        }
        ident = identity()
        self.claim: dict[str, Any] = {
            "metadata": {
                "name": "data-" + ident.environment_id.hex,
                "namespace": ident.namespace,
                "uid": str(UUID(int=7)),
                "resourceVersion": "10",
                "labels": ident.labels,
            },
            "spec": {
                "volumeName": StorageClient.volume_name(ident),
                "storageClassName": "",
                "accessModes": ["ReadWriteOnce"],
                "volumeMode": "Filesystem",
                "resources": {"requests": {"storage": "1Gi"}},
            },
            "status": {"phase": "Pending"},
        }
        self.pv: dict[str, Any] = {}
        self.created: list[dict[str, Any]] = []
        self.timeout_create = False

    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        return copy.deepcopy({"node": self.node, "pvc": self.claim, "pv": self.pv}[kind]) or None

    def create(self, obj: dict[str, Any]) -> dict[str, Any]:
        self.created.append(copy.deepcopy(obj))
        if self.timeout_create:
            raise OutcomeUnknown("lost API response")
        self.pv = copy.deepcopy(obj)
        self.pv["metadata"].update(uid=str(UUID(int=8)), resourceVersion="11")
        self.pv["status"] = {"phase": "Bound"}
        self.claim["status"] = {"phase": "Bound", "capacity": {"storage": "1Gi"}}
        return copy.deepcopy(self.pv)


def receipt(request: StorageRequest) -> dict[str, Any]:
    size = request.nominal_gib * (1 << 30)
    volume = request.volume_id
    mount_path = "/trusted/mounts/" + volume
    return {
        "profile": PROFILE,
        "volume_id": volume,
        "identity": request.identity,
        "operation_id": request.operation_id,
        "phase": "ready",
        "filesystem_uuid": str(UUID(int=9)),
        "machine_id_sha256": hashlib.sha256(("machine-id:" + "a" * 32).encode()).hexdigest(),
        "backing_path": "/trusted/backing/" + volume + ".img",
        "mount_path": mount_path,
        "data_path": mount_path + "/data",
        "backing": {
            "device": os.makedev(8, 1),
            "inode": 111,
            "size_bytes": size,
            "allocated_bytes": size,
        },
        "data": {"device": os.makedev(7, 7), "inode": 222, "uid": 1000, "gid": 1000},
        "created_at_ns": time.time_ns(),
        "updated_at_ns": time.time_ns(),
        "live": {
            "usable_bytes": size - 4096,
            "available_bytes": size - 8192,
            "used_bytes": 4096,
            "host_reserved_bytes": size,
            "sampled_at_ns": time.time_ns(),
            "loop_device": "/dev/loop7",
            "mount": {
                "mount_id": 22,
                "device": "7:7",
                "root": "/",
                "target": mount_path,
                "fstype": "ext4",
                "source": "/dev/loop7",
                "options": ["rw", "nodev", "nosuid"],
                "super_options": ["rw"],
            },
        },
    }


class Client(StorageClient):
    def __init__(self, api: Kubectl) -> None:
        super().__init__("/run/test-storage.sock", api, "node-a")
        self.requests: list[tuple[str, StorageRequest]] = []
        self.change: Callable[[dict[str, Any]], None] = lambda value: None
        self.original: str | None = None

    def _rpc(self, action: str, request: StorageRequest) -> dict[str, Any]:
        self.requests.append((action, request))
        value = receipt(request)
        if self.original:
            value["operation_id"] = self.original
        self.change(value)
        return value


def test_provision_bound_and_inspect_no_mutation() -> None:
    api = Api()
    client = Client(api)
    operation = str(uuid4())
    result = client.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    assert len(api.created) == 1
    assert api.created[0]["spec"]["local"] == {"path": result["data_path"]}
    assert api.created[0]["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
    assert result["binding"]["pv_uid"] == api.pv["metadata"]["uid"]
    client.original = operation
    assert client.inspect(identity(), api.claim)["binding"] == result["binding"]
    assert len(api.created) == 1
    assert client.requests[-1][0] == "inspect"


@pytest.mark.parametrize(
    "case",
    [
        "node-uid",
        "machine",
        "identity",
        "volume",
        "profile",
        "inode",
        "size",
        "sparse",
        "stale",
        "mount",
        "data",
        "path",
        "usable",
        "operation",
    ],
)
def test_bad_receipt_refused_before_pv_create(case: str) -> None:
    api = Api()
    client = Client(api)

    def change(value: dict[str, Any]) -> None:
        if case == "node-uid":
            value["identity"]["node_uid"] = str(uuid4())
        elif case == "machine":
            value["machine_id_sha256"] = "b" * 64
        elif case == "identity":
            value["identity"]["environment_id"] = str(uuid4())
        elif case == "volume":
            value["volume_id"] = str(uuid4())
        elif case == "profile":
            value["profile"] = "legacy"
        elif case == "inode":
            value["backing"]["inode"] = 0
        elif case == "size":
            value["backing"]["size_bytes"] -= 1
        elif case == "sparse":
            value["backing"]["allocated_bytes"] -= 512
        elif case == "stale":
            value["live"]["sampled_at_ns"] -= 120_000_000_000
        elif case == "mount":
            value["live"]["mount"]["source"] = "/dev/loop8"
        elif case == "data":
            value["data"]["device"] = os.makedev(8, 1)
        elif case == "path":
            value["data_path"] += "/.."
        elif case == "usable":
            value["live"]["usable_bytes"] = 2 << 30
        elif case == "operation":
            value["operation_id"] = str(uuid4())

    client.change = change
    with pytest.raises(StorageUnavailable, match=r"^storage_refused_or_unknown$"):
        client.provision(identity(), api.claim, operation_id=str(uuid4()), disk_gb=1)
    assert not api.created


@pytest.mark.parametrize(
    "case",
    [
        "claim-uid",
        "pv-uid",
        "claimref",
        "path",
        "capacity",
        "affinity",
        "labels",
        "operation",
        "pv-phase",
        "pvc-phase",
        "storageclass",
    ],
)
def test_inspect_rejects_replaced_binding(case: str) -> None:
    api = Api()
    client = Client(api)
    operation = str(uuid4())
    client.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    client.original = operation
    claim = copy.deepcopy(api.claim)
    if case == "claim-uid":
        api.claim["metadata"]["uid"] = str(uuid4())
    elif case == "pv-uid":
        api.pv["metadata"]["uid"] = str(uuid4())
    elif case == "claimref":
        api.pv["spec"]["claimRef"]["uid"] = str(uuid4())
    elif case == "path":
        api.pv["spec"]["local"]["path"] = "/old/unbounded"
    elif case == "capacity":
        api.pv["spec"]["capacity"]["storage"] = "2Gi"
    elif case == "affinity":
        api.pv["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"].append({})
    elif case == "labels":
        api.pv["metadata"]["labels"] = {}
    elif case == "operation":
        api.pv["metadata"]["annotations"][PREFIX + "storage-operation"] = str(uuid4())
    elif case == "pv-phase":
        api.pv["status"]["phase"] = "Available"
    elif case == "pvc-phase":
        api.claim["status"]["phase"] = "Pending"
    elif case == "storageclass":
        api.claim["spec"]["storageClassName"] = "default"
    with pytest.raises(StorageUnavailable):
        client.inspect(identity(), claim)


def test_timeout_no_duplicate_create() -> None:
    api = Api()
    api.timeout_create = True
    client = Client(api)
    operation = str(uuid4())
    for _ in range(2):
        with pytest.raises(StorageUnavailable):
            client.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    assert len(api.created) == 1


def test_remount_loop_minor_can_change() -> None:
    api = Api()
    client = Client(api)
    operation = str(uuid4())
    client.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    client.original = operation

    def remount(value: dict[str, Any]) -> None:
        value["data"]["device"] = os.makedev(7, 10)
        value["live"]["loop_device"] = "/dev/loop10"
        value["live"]["mount"].update(device="7:10", source="/dev/loop10", mount_id=100)

    client.change = remount
    assert client.inspect(identity(), api.claim)["data"]["inode"] == 222


@pytest.fixture
def unix_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UnixServer]:
    directory = tmp_path / "socket"
    directory.mkdir()
    path = directory / "hook.sock"
    monkeypatch.setattr(StorageClient, "_socket_stat", lambda self: Path(self.socket_path).lstat())
    monkeypatch.setattr("app.runtime_v2.storage_client.struct.unpack", lambda fmt, raw: (123, 0, 0))
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    listener.listen()
    threads: list[threading.Thread] = []

    def serve(payload: bytes) -> None:
        def run() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                with suppress(BrokenPipeError):
                    connection.sendall(payload)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)

    yield path, serve
    listener.close()
    path.unlink(missing_ok=True)
    directory.rmdir()
    for thread in threads:
        thread.join(timeout=1)


@pytest.mark.parametrize(
    "payload",
    [
        b"x\n",
        b"{}\n{}\n",
        b"x" * 65537 + b"\n",
        b'{"version":1,"ok":false,"error":"/secret/root/path"}\n',
    ],
)
def test_unix_frame_errors_generic(unix_server: UnixServer, payload: bytes) -> None:
    path, serve = unix_server
    serve(payload)
    api = Api()
    client = StorageClient(str(path), api, "node-a")
    with pytest.raises(StorageUnavailable, match=r"^storage_refused_or_unknown$"):
        client.provision(identity(), api.claim, operation_id=str(uuid4()), disk_gb=1)


def test_unix_root_peer_accepts_response(unix_server: UnixServer) -> None:
    path, serve = unix_server
    api = Api()
    operation = str(uuid4())
    fake = Client(api)
    fake.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    request = fake.requests[0][1]
    api.pv = {}
    api.claim["status"] = {"phase": "Pending"}
    serve(json.dumps({"version": 1, "ok": True, "receipt": receipt(request)}).encode() + b"\n")
    client = StorageClient(str(path), api, "node-a")
    assert (
        client.provision(identity(), api.claim, operation_id=operation, disk_gb=1)["phase"]
        == "ready"
    )


def test_writable_ancestor_refused(tmp_path: Path) -> None:
    api = Api()
    client = StorageClient(str(tmp_path / "hook.sock"), api, "node-a")
    with pytest.raises(StorageUnavailable):
        client.provision(identity(), api.claim, operation_id=str(uuid4()), disk_gb=1)


class RetireApi(Api):
    def __init__(self) -> None:
        super().__init__()
        self.pods: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.retain = False

    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        if kind == "deployment":
            return None
        return super().get_optional(kind, name, namespace)

    def call(self, args: list[str], *, input_data: str | None = None) -> str:
        if args[:2] == ["get", "pods"]:
            return json.dumps({"items": self.pods})
        if args[:2] == ["delete", "--raw"]:
            assert input_data is not None
            body = json.loads(input_data)
            kind = "pvc" if "/persistentvolumeclaims/" in args[2] else "pv"
            obj = self.claim if kind == "pvc" else self.pv
            assert body["preconditions"] == {
                "uid": obj["metadata"]["uid"],
                "resourceVersion": obj["metadata"]["resourceVersion"],
            }
            self.deleted.append(kind)
            if not self.retain:
                if kind == "pvc":
                    self.claim = {}
                else:
                    self.pv = {}
            return "{}"
        raise AssertionError(args)


class RetireClient(Client):
    def _rpc(self, action: str, request: StorageRequest) -> dict[str, Any]:
        value = super()._rpc(action, request)
        if action == "retire":
            value.update(
                phase="retired",
                retirement_operation_id=request.operation_id,
                retired_at_ns=time.time_ns(),
            )
        return value


def prepared_retirement() -> tuple[RetireApi, RetireClient, dict[str, Any], dict[str, Any]]:
    api = RetireApi()
    client = RetireClient(api)
    original = str(uuid4())
    proof = client.provision(identity(), api.claim, operation_id=original, disk_gb=1)
    client.original = original
    return api, client, proof, copy.deepcopy(api.claim)


def test_retire_uid_deletes_observes_absence_then_root_tombstone() -> None:
    api, client, proof, claim = prepared_retirement()
    operation = str(uuid4())
    result = client.retire(identity(), claim, operation_id=operation, receipt=proof)
    assert api.deleted == ["pvc", "pv"]
    assert result["phase"] == "retired"
    assert result["retirement_operation_id"] == operation
    assert client.requests[-1][0] == "retire"
    assert (
        client.retire(identity(), claim, operation_id=operation, receipt=proof)["phase"]
        == "retired"
    )
    assert api.deleted == ["pvc", "pv"]


@pytest.mark.parametrize(
    "case", ["pod", "helper", "pvc-replaced", "pv-replaced", "wrong-proof", "delete-pending"]
)
def test_retire_unknown_keeps_root_backing(case: str) -> None:
    api, client, proof, claim = prepared_retirement()
    if case in ("pod", "helper"):
        api.pods = [
            {
                "metadata": {"namespace": identity().namespace},
                "spec": {
                    "volumes": [{"persistentVolumeClaim": {"claimName": claim["metadata"]["name"]}}]
                },
            }
        ]
    elif case == "pvc-replaced":
        api.claim["metadata"]["uid"] = str(uuid4())
    elif case == "pv-replaced":
        api.pv["metadata"]["uid"] = str(uuid4())
    elif case == "wrong-proof":
        proof["filesystem_uuid"] = str(uuid4())
    elif case == "delete-pending":
        api.retain = True
    with pytest.raises(StorageUnavailable):
        client.retire(identity(), claim, operation_id=str(uuid4()), receipt=proof)
    assert all(action != "retire" for action, _ in client.requests)


def test_nonroot_unix_peer_refused(
    unix_server: UnixServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, serve = unix_server
    serve(b"{}\n")
    monkeypatch.setattr(
        "app.runtime_v2.storage_client.struct.unpack", lambda fmt, raw: (123, 1234, 1234)
    )
    api = Api()
    with pytest.raises(StorageUnavailable):
        StorageClient(str(path), api, "node-a").provision(
            identity(), api.claim, operation_id=str(uuid4()), disk_gb=1
        )


def test_provenance_contains_only_safe_identity_and_binding() -> None:
    _api, client, proof, claim = prepared_retirement()
    safe = client.provenance(proof)
    assert set(safe) == {"profile", "volume_id", "identity", "binding"}
    assert set(safe["binding"]) == {"pv_name", "pv_uid", "pvc_uid", "node_uid", "machine_id_sha256"}
    assert "/trusted" not in json.dumps(safe)
    operation = str(uuid4())
    assert (
        client.retire(identity(), claim, operation_id=operation, receipt=safe)["phase"] == "retired"
    )


def test_safe_provenance_recovers_when_claim_already_absent() -> None:
    api, client, proof, claim = prepared_retirement()
    safe = client.provenance(proof)
    api.claim = {}
    assert (
        client.retire(identity(), claim, operation_id=str(uuid4()), receipt=safe)["phase"]
        == "retired"
    )
    assert api.deleted == ["pv"]


def test_pv_empty_storage_class_is_omitted_by_kubernetes_json() -> None:
    api, client, proof, claim = prepared_retirement()
    del api.pv["spec"]["storageClassName"]
    assert client.inspect(identity(), claim)["binding"]["pv_uid"] == proof["binding"]["pv_uid"]


def test_duplicate_response_keys_are_refused(unix_server: UnixServer) -> None:
    path, serve = unix_server
    api = Api()
    fake = Client(api)
    operation = str(uuid4())
    fake.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    request = fake.requests[0][1]
    api.pv = {}
    api.claim["status"] = {"phase": "Pending"}
    payload = json.dumps({"version": 1, "ok": True, "receipt": receipt(request)}).replace(
        '"version": 1', '"version":1,"version":1'
    )
    serve(payload.encode() + b"\n")
    with pytest.raises(StorageUnavailable):
        StorageClient(str(path), api, "node-a").provision(
            identity(), api.claim, operation_id=operation, disk_gb=1
        )


def test_safe_recovery_refuses_retired_receipt_from_wrong_machine() -> None:
    api, client, proof, claim = prepared_retirement()
    safe = client.provenance(proof)
    api.claim = {}
    api.pv = {}
    client.change = lambda value: value.update(machine_id_sha256="b" * 64)
    with pytest.raises(StorageUnavailable):
        client.retire(identity(), claim, operation_id=str(uuid4()), receipt=safe)


@pytest.mark.parametrize("case", ["backing-inode", "data-inode", "filesystem"])
def test_inspect_refuses_immutable_root_replacement(case: str) -> None:
    _api, client, _proof, claim = prepared_retirement()

    def change(value: dict[str, Any]) -> None:
        if case == "backing-inode":
            value["backing"]["inode"] = 999
        elif case == "data-inode":
            value["data"]["inode"] = 999
        else:
            value["filesystem_uuid"] = str(uuid4())

    client.change = change
    with pytest.raises(StorageUnavailable):
        client.inspect(identity(), claim)


def test_rpc_timeout_is_unknown_without_pv_create(
    unix_server: UnixServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _serve = unix_server
    # Listening socket accepts the connect but never supplies a reply.
    monkeypatch.setattr("app.runtime_v2.storage_client._TIMEOUT", 0.02)
    api = Api()
    client = StorageClient(str(path), api, "node-a")
    with pytest.raises(StorageUnavailable, match=r"^storage_refused_or_unknown$"):
        client.provision(identity(), api.claim, operation_id=str(uuid4()), disk_gb=1)
    assert not api.created


class AsyncBindingApi(Api):
    def __init__(self, *, bind: bool = True) -> None:
        super().__init__()
        self.bind = bind
        self.reads = 0

    def create(self, obj: dict[str, Any]) -> dict[str, Any]:
        result = super().create(obj)
        self.claim["status"] = {"phase": "Pending"}
        self.pv["status"] = {"phase": "Available"}
        return result

    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        if self.pv:
            self.reads += 1
            if self.bind and self.reads >= 8:
                self.claim["status"] = {"phase": "Bound", "capacity": {"storage": "1Gi"}}
                self.pv["status"] = {"phase": "Bound"}
        return super().get_optional(kind, name, namespace)


def test_provision_waits_for_asynchronous_binding_without_recreate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.runtime_v2.storage_client._POLL_INTERVAL", 0.001, raising=False)
    api = AsyncBindingApi()
    client = Client(api)
    assert (
        client.provision(identity(), api.claim, operation_id=str(uuid4()), disk_gb=1)["phase"]
        == "ready"
    )
    assert len(api.created) == 1


def test_pending_binding_deadline_is_unknown_and_never_recreates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.runtime_v2.storage_client._POLL_INTERVAL", 0.001, raising=False)
    monkeypatch.setattr("app.runtime_v2.storage_client._BIND_TIMEOUT", 0.01, raising=False)
    api = AsyncBindingApi(bind=False)
    client = Client(api)
    operation = str(uuid4())
    for _ in range(2):
        with pytest.raises(StorageUnavailable):
            client.provision(identity(), api.claim, operation_id=operation, disk_gb=1)
    assert len(api.created) == 1


def test_inspect_command_id_does_not_replace_original_provision_operation() -> None:
    _api, client, proof, claim = prepared_retirement()
    inspected = client.inspect(identity(), claim, operation_id=str(uuid4()))
    assert inspected["operation_id"] == proof["operation_id"]


def scratch_setup(
    operation: str | None = None,
) -> tuple[RetireApi, RetireClient, RuntimeIdentity, str]:
    from dataclasses import replace
    from uuid import uuid5

    operation = operation or str(uuid4())
    ident = replace(identity(), allocation_id=uuid5(UUID(operation), "restore-scratch"))
    api = RetireApi()
    api.claim["metadata"].update(
        name=StorageClient.scratch_claim_name(ident), labels=StorageClient.scratch_labels(ident)
    )
    api.claim["spec"]["volumeName"] = StorageClient.volume_name(ident)
    return api, RetireClient(api), ident, operation


def test_scratch_is_distinct_root_volume_with_fixed_non_environment_labels() -> None:
    api, client, ident, operation = scratch_setup()
    proof = client.provision(ident, api.claim, operation_id=operation, disk_gb=1)
    assert proof["identity"]["environment_id"] == str(identity().environment_id)
    assert proof["volume_id"] != StorageClient.volume_name(identity()).removeprefix("pv-")
    assert api.created[0]["metadata"]["labels"][PREFIX + "component"] == "restore-scratch"
    assert PREFIX + "environment" not in api.created[0]["metadata"]["labels"]
    assert PREFIX + "allocation" not in api.claim["metadata"]["labels"]
    client.original = operation
    assert client.inspect(ident, api.claim)["volume_id"] == proof["volume_id"]
    assert (
        client.retire(
            ident, api.claim, operation_id=str(uuid4()), receipt=client.provenance(proof)
        )["phase"]
        == "retired"
    )


@pytest.mark.parametrize(
    "case", ["derivation", "environment-label", "allocation-label", "parent", "name"]
)
def test_scratch_never_adopts_tenant_or_unrelated_claim(case: str) -> None:
    api, client, ident, operation = scratch_setup()
    if case == "derivation":
        operation = str(uuid4())
    elif case == "environment-label":
        api.claim["metadata"]["labels"][PREFIX + "environment"] = ident.environment_id.hex
    elif case == "allocation-label":
        api.claim["metadata"]["labels"][PREFIX + "allocation"] = ident.allocation_id.hex
    elif case == "parent":
        api.claim["metadata"]["labels"][PREFIX + "parent-environment"] = uuid4().hex
    else:
        api.claim["metadata"]["name"] = "tenant-chosen"
    with pytest.raises(StorageUnavailable):
        client.provision(ident, api.claim, operation_id=operation, disk_gb=1)
    assert not api.created


def test_scratch_pv_cannot_gain_canonical_tenant_labels() -> None:
    api, client, ident, operation = scratch_setup()
    client.provision(ident, api.claim, operation_id=operation, disk_gb=1)
    client.original = operation
    api.pv["metadata"]["labels"][PREFIX + "environment"] = ident.environment_id.hex
    with pytest.raises(StorageUnavailable):
        client.inspect(ident, api.claim)
