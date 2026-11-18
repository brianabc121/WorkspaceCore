"""Node-local root quota proof and exact, UID-fenced Kubernetes binding.

Receipts contain host paths and are strictly internal controller state. A refused
or unknown operation retains its reservation; this client never guesses cleanup.
"""

import hashlib
import json
import os
import re
import socket
import stat
import struct
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl, KubernetesCommandError, OutcomeUnknown, verify_identity
from app.runtime_v2.storage_host import PROFILE, VOLUME_NAMESPACE, StorageError, StorageRequest

_PREFIX = "workspace.myworkspace.io/"
_NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"
_LIMIT = 65536
_TIMEOUT = 5.0
_BIND_TIMEOUT = 10.0
_POLL_INTERVAL = 0.2


class StorageUnavailable(RuntimeError):
    """Refused or unknown storage state; callers must retain the reservation."""


class _BindingPending(StorageUnavailable):
    """Verified fixed resources exist; the binder has not completed yet."""


def _refuse() -> StorageUnavailable:
    return StorageUnavailable("storage_refused_or_unknown")


def _uint(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 1 << 63:
        raise _refuse()
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise _refuse()
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _refuse()
        result[key] = value
    return result


def _machine(node: dict[str, Any]) -> str:
    value = node["status"]["nodeInfo"]["machineID"].strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise _refuse()
    return hashlib.sha256(("machine-id:" + value).encode("ascii")).hexdigest()


def _path(value: Any) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise _refuse()
    path = Path(value)
    if str(path) != value or any(part in (".", "..") for part in path.parts):
        raise _refuse()
    return path


class StorageClient:
    def __init__(self, socket_path: str, api: Kubectl, node_name: str):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", node_name):
            raise ValueError("invalid configured Node")
        self.socket_path, self.api, self.node_name = socket_path, api, node_name
        self._receipts: dict[str, dict[str, Any]] = {}
        self._pending_create: set[str] = set()
        self._created_uids: dict[str, str] = {}

    @staticmethod
    def volume_name(identity: RuntimeIdentity) -> str:
        return "pv-" + str(
            uuid5(VOLUME_NAMESPACE, f"{identity.allocation_id}:{identity.generation}")
        )

    @staticmethod
    def scratch_claim_name(identity: RuntimeIdentity) -> str:
        return "scratch-" + identity.allocation_id.hex

    @staticmethod
    def scratch_labels(identity: RuntimeIdentity) -> dict[str, str]:
        return {
            _PREFIX + "workspace": identity.workspace_id.hex,
            _PREFIX + "instance": identity.instance_id.hex,
            _PREFIX + "generation": str(identity.generation),
            _PREFIX + "component": "restore-scratch",
            _PREFIX + "parent-environment": identity.environment_id.hex,
            _PREFIX + "scratch-allocation": identity.allocation_id.hex,
        }

    @staticmethod
    def _claim_labels(identity: RuntimeIdentity, name: str) -> dict[str, str]:
        if name == "data-" + identity.environment_id.hex:
            return identity.labels
        if name == StorageClient.scratch_claim_name(identity):
            return StorageClient.scratch_labels(identity)
        raise _refuse()

    def _socket_stat(self) -> os.stat_result:
        path = _path(self.socket_path)
        for parent in reversed(path.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise _refuse()
        info = path.lstat()
        # The hook owns the enclosing directory and chowns this 0600 socket to
        # the configured controller UID. Its process must still be root below.
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid not in (0, os.geteuid())
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise _refuse()
        return info

    def probe_peer(self) -> None:
        """Authenticate socket availability only; send no storage operation."""
        try:
            before = self._socket_stat()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(_TIMEOUT)
                connection.connect(self.socket_path)
                _, uid, _ = struct.unpack(
                    "3i",
                    connection.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                    ),
                )
                after = self._socket_stat()
                if uid != 0 or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise _refuse()
        except (OSError, ValueError, TypeError, struct.error) as error:
            raise _refuse() from error

    def _rpc(self, action: str, request: StorageRequest) -> dict[str, Any]:
        before = self._socket_stat()
        frame = (
            json.dumps(
                {"version": 1, "action": action, "request": request.as_dict()},
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        if len(frame) > _LIMIT:
            raise _refuse()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_TIMEOUT)
            connection.connect(self.socket_path)
            _, uid, _ = struct.unpack(
                "3i",
                connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
            )
            after = self._socket_stat()
            if uid != 0 or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise _refuse()
            connection.sendall(frame)
            received = bytearray()
            deadline = time.monotonic() + _TIMEOUT
            while b"\n" not in received:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _refuse()
                connection.settimeout(remaining)
                chunk = connection.recv(min(4096, _LIMIT + 1 - len(received)))
                if not chunk:
                    raise _refuse()
                received.extend(chunk)
                if len(received) > _LIMIT:
                    raise _refuse()
            response_frame, trailing = received.split(b"\n", 1)
            if trailing:
                raise _refuse()
            value = json.loads(response_frame, object_pairs_hook=_unique_object)
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "ok", "receipt"}
                or type(value["version"]) is not int
                or value["version"] != 1
                or value["ok"] is not True
                or not isinstance(value["receipt"], dict)
            ):
                raise _refuse()
            return value["receipt"]

    def _node(self) -> dict[str, Any]:
        node = self.api.get_optional("node", self.node_name)
        if (
            node is None
            or node["metadata"].get("name") != self.node_name
            or node["metadata"].get("labels", {}).get(_NODE_LABEL) != self.node_name
        ):
            raise _refuse()
        _uuid(node["metadata"]["uid"])
        _machine(node)
        return node

    def _claim(
        self, identity: RuntimeIdentity, claim: dict[str, Any], disk_gb: int | None
    ) -> tuple[dict[str, Any], int]:
        if identity.node_name != self.node_name:
            raise _refuse()
        name = claim["metadata"]["name"]
        expected_labels = self._claim_labels(identity, name)
        current = self.api.get_optional("pvc", name, identity.namespace)
        if current is None:
            raise _refuse()
        if name == self.scratch_claim_name(identity):
            labels = current["metadata"].get("labels", {})
            if any(labels.get(k) != v for k, v in expected_labels.items()) or any(
                _PREFIX + key in labels for key in ("environment", "allocation")
            ):
                raise _refuse()
        else:
            verify_identity(current, identity)
        meta, spec = current["metadata"], current["spec"]
        if (
            meta.get("name") != name
            or meta.get("namespace") != identity.namespace
            or meta.get("deletionTimestamp")
            or _uuid(meta["uid"]) != claim["metadata"]["uid"]
            or not meta.get("resourceVersion")
            or spec.get("volumeName") != self.volume_name(identity)
            or spec.get("storageClassName") != ""
            or spec.get("accessModes") != ["ReadWriteOnce"]
            or spec.get("volumeMode", "Filesystem") != "Filesystem"
        ):
            raise _refuse()
        requested = spec["resources"]["requests"]["storage"]
        match = re.fullmatch(r"([1-9][0-9]{0,3})Gi", requested)
        if match is None:
            raise _refuse()
        size = int(match[1])
        if not 1 <= size <= 1000 or (
            disk_gb is not None and (type(disk_gb) is not int or disk_gb != size)
        ):
            raise _refuse()
        return current, size

    def _request(
        self,
        identity: RuntimeIdentity,
        claim: dict[str, Any],
        node: dict[str, Any],
        size: int,
        operation: str,
    ) -> StorageRequest:
        return StorageRequest.from_dict(
            {
                **{
                    key: str(getattr(identity, key))
                    for key in ("workspace_id", "instance_id", "environment_id", "allocation_id")
                },
                "generation": identity.generation,
                "pvc_namespace": identity.namespace,
                "pvc_name": claim["metadata"]["name"],
                "pvc_uid": claim["metadata"]["uid"],
                "node_name": self.node_name,
                "node_uid": node["metadata"]["uid"],
                "nominal_gib": size,
                "operation_id": operation,
            }
        )

    def _validate_receipt(
        self,
        request: StorageRequest,
        node: dict[str, Any],
        value: dict[str, Any],
        *,
        original: str | None,
    ) -> None:
        if (
            value.get("profile") != PROFILE
            or value.get("phase") != "ready"
            or value.get("identity") != request.identity
            or value.get("volume_id") != request.volume_id
            or value.get("machine_id_sha256") != _machine(node)
        ):
            raise _refuse()
        StorageRequest.from_dict({**value["identity"], "operation_id": value["operation_id"]})
        _uuid(value["filesystem_uuid"])
        operation = _uuid(value["operation_id"])
        if original is not None and operation != original:
            raise _refuse()
        backing_path, mount_path, data_path = (
            _path(value[key]) for key in ("backing_path", "mount_path", "data_path")
        )
        if (
            backing_path.name != request.volume_id + ".img"
            or mount_path.name != request.volume_id
            or data_path != mount_path / "data"
            or backing_path == data_path
        ):
            raise _refuse()
        backing, data, live = value["backing"], value["data"], value["live"]
        nominal = request.nominal_gib << 30
        for key in ("device", "inode"):
            _uint(backing[key], 1)
            _uint(data[key], 1)
        if (
            backing["size_bytes"] != nominal
            or _uint(backing["allocated_bytes"]) < nominal
            or data["uid"] != 1000
            or data["gid"] != 1000
            or live["host_reserved_bytes"] != backing["allocated_bytes"]
        ):
            raise _refuse()
        usable = _uint(live["usable_bytes"], 1)
        if (
            usable > nominal
            or _uint(live["available_bytes"]) > usable
            or _uint(live["used_bytes"]) > usable
            or not -5_000_000_000 <= time.time_ns() - _uint(live["sampled_at_ns"]) <= 60_000_000_000
        ):
            raise _refuse()
        mount = live["mount"]
        _uint(mount["mount_id"], 1)
        if (
            mount["target"] != str(mount_path)
            or mount["root"] != "/"
            or mount["fstype"] != "ext4"
            or not re.fullmatch(r"/dev/loop[0-9]+", live["loop_device"])
            or mount["source"] != live["loop_device"]
            or mount["device"] != f"{os.major(data['device'])}:{os.minor(data['device'])}"
            or not {"rw", "nodev", "nosuid"}.issubset(mount["options"])
            or "discard" in mount["options"]
            or "discard" in mount["super_options"]
        ):
            raise _refuse()
        previous = self._receipts.get(request.volume_id)
        if previous and any(
            previous[key] != value[key]
            for key in (
                "filesystem_uuid",
                "operation_id",
                "backing_path",
                "mount_path",
                "data_path",
            )
        ):
            raise _refuse()
        if previous and (
            any(
                previous["backing"][key] != backing[key]
                for key in ("device", "inode", "size_bytes")
            )
            or previous["data"]["inode"] != data["inode"]
        ):
            raise _refuse()

    @staticmethod
    def _manifest(
        identity: RuntimeIdentity, claim: dict[str, Any], value: dict[str, Any]
    ) -> dict[str, Any]:
        annotations = {
            _PREFIX + "storage-" + key: str(item)
            for key, item in {
                "profile": PROFILE,
                "operation": value["operation_id"],
                "filesystem": value["filesystem_uuid"],
                "backing-device": value["backing"]["device"],
                "backing-inode": value["backing"]["inode"],
                "data-inode": value["data"]["inode"],
                "pvc-uid": claim["metadata"]["uid"],
            }.items()
        }
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": StorageClient.volume_name(identity),
                "labels": StorageClient._claim_labels(identity, claim["metadata"]["name"]),
                "annotations": annotations,
            },
            "spec": {
                "capacity": {"storage": f"{value['identity']['nominal_gib']}Gi"},
                "volumeMode": "Filesystem",
                "accessModes": ["ReadWriteOnce"],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "",
                "local": {"path": value["data_path"]},
                "claimRef": {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "name": claim["metadata"]["name"],
                    "namespace": identity.namespace,
                    "uid": claim["metadata"]["uid"],
                },
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchFields": [
                                    {
                                        "key": "metadata.name",
                                        "operator": "In",
                                        "values": [identity.node_name],
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        }

    def _binding(
        self,
        identity: RuntimeIdentity,
        claim: dict[str, Any],
        value: dict[str, Any],
        node: dict[str, Any],
        created_uid: str | None = None,
    ) -> dict[str, Any]:
        current, _ = self._claim(identity, claim, value["identity"]["nominal_gib"])
        pv = self.api.get_optional("pv", self.volume_name(identity))
        expected = self._manifest(identity, current, value)
        if pv is None:
            raise _refuse()
        meta, spec = pv["metadata"], pv["spec"]
        if claim["metadata"]["name"] == self.scratch_claim_name(identity) and any(
            _PREFIX + key in meta.get("labels", {}) for key in ("environment", "allocation")
        ):
            raise _refuse()
        uid = _uuid(meta["uid"])
        previous = self._receipts.get(value["volume_id"], {}).get("binding", {})
        if (
            meta.get("name") != expected["metadata"]["name"]
            or meta.get("deletionTimestamp")
            or not meta.get("resourceVersion")
            or (created_uid is not None and uid != created_uid)
            or (previous and uid != previous["pv_uid"])
            or any(
                meta.get("labels", {}).get(k) != v
                for k, v in expected["metadata"]["labels"].items()
            )
            or any(
                meta.get("annotations", {}).get(k) != v
                for k, v in expected["metadata"]["annotations"].items()
            )
        ):
            raise _refuse()
        for key, item in expected["spec"].items():
            if key == "claimRef":
                if any(spec.get(key, {}).get(k) != v for k, v in item.items()):
                    raise _refuse()
            elif key == "storageClassName" and spec.get(key, "") == "":
                continue
            elif spec.get(key) != item:
                raise _refuse()
        claim_phase = current.get("status", {}).get("phase")
        pv_phase = pv.get("status", {}).get("phase")
        if claim_phase != "Bound" or pv_phase != "Bound":
            if claim_phase in ("Pending", "Bound") and pv_phase in (
                "Pending",
                "Available",
                "Bound",
            ):
                raise _BindingPending("storage_refused_or_unknown")
            raise _refuse()
        if (
            current["status"].get("capacity", {}).get("storage")
            != expected["spec"]["capacity"]["storage"]
        ):
            raise _refuse()
        now = self._node()
        if now["metadata"]["uid"] != node["metadata"]["uid"] or _machine(now) != _machine(node):
            raise _refuse()
        return {
            "pv_name": meta["name"],
            "pv_uid": uid,
            "pv_resource_version": meta["resourceVersion"],
            "pvc_uid": current["metadata"]["uid"],
            "pvc_resource_version": current["metadata"]["resourceVersion"],
            "node_uid": node["metadata"]["uid"],
            "machine_id_sha256": _machine(node),
        }

    def provision(
        self, identity: RuntimeIdentity, claim: dict[str, Any], *, operation_id: str, disk_gb: int
    ) -> dict[str, Any]:
        return self._observe(
            identity, claim, operation_id=operation_id, disk_gb=disk_gb, provision=True
        )

    def inspect(
        self, identity: RuntimeIdentity, claim: dict[str, Any], *, operation_id: str | None = None
    ) -> dict[str, Any]:
        return self._observe(
            identity, claim, operation_id=operation_id, disk_gb=None, provision=False
        )

    def _observe(
        self,
        identity: RuntimeIdentity,
        claim: dict[str, Any],
        *,
        operation_id: str | None,
        disk_gb: int | None,
        provision: bool,
    ) -> dict[str, Any]:
        try:
            current, size = self._claim(identity, claim, disk_gb)
            node = self._node()
            pv = self.api.get_optional("pv", self.volume_name(identity))
            recorded_operation = (
                (pv or {})
                .get("metadata", {})
                .get("annotations", {})
                .get(_PREFIX + "storage-operation")
            )
            original = operation_id if provision else recorded_operation
            if original is None:
                raise _refuse()
            if current["metadata"]["name"] == self.scratch_claim_name(
                identity
            ) and identity.allocation_id != uuid5(UUID(_uuid(original)), "restore-scratch"):
                raise _refuse()
            request = self._request(identity, current, node, size, _uuid(operation_id or original))
            value = self._rpc("provision" if provision else "inspect", request)
            self._validate_receipt(request, node, value, original=original)
            created_uid = self._created_uids.get(self.volume_name(identity))
            if pv is None and provision:
                name = self.volume_name(identity)
                if name in self._pending_create:
                    raise _refuse()
                self._pending_create.add(name)
                created = self.api.create(self._manifest(identity, current, value))
                created_uid = _uuid(created["metadata"]["uid"])
                self._created_uids[name] = created_uid
            deadline = time.monotonic() + _BIND_TIMEOUT
            while True:
                try:
                    binding = self._binding(identity, current, value, node, created_uid)
                    break
                except _BindingPending:
                    remaining = deadline - time.monotonic()
                    if not provision or remaining <= 0:
                        raise _refuse() from None
                    time.sleep(min(_POLL_INTERVAL, remaining))
            value = {**value, "binding": binding}
            self._receipts[request.volume_id] = value
            self._pending_create.discard(self.volume_name(identity))
            return value
        except (
            StorageError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            KubernetesCommandError,
            OutcomeUnknown,
        ) as error:
            raise _refuse() from error

    @staticmethod
    def provenance(receipt: dict[str, Any]) -> dict[str, Any]:
        """A path-free allowlist suitable for the controller's durable journal."""
        try:
            request = StorageRequest.from_dict(
                {**receipt["identity"], "operation_id": str(UUID(int=0))}
            )
            binding = receipt["binding"]
            volume_id = _uuid(receipt["volume_id"])
            machine = binding["machine_id_sha256"]
            if (
                receipt["profile"] != PROFILE
                or volume_id != request.volume_id
                or binding["pv_name"] != "pv-" + volume_id
                or _uuid(binding["pvc_uid"]) != request.pvc_uid
                or _uuid(binding["node_uid"]) != request.node_uid
                or not isinstance(machine, str)
                or not re.fullmatch(r"[0-9a-f]{64}", machine)
            ):
                raise _refuse()
            return {
                "profile": PROFILE,
                "volume_id": volume_id,
                "identity": request.identity,
                "binding": {
                    "pv_name": binding["pv_name"],
                    "pv_uid": _uuid(binding["pv_uid"]),
                    "pvc_uid": binding["pvc_uid"],
                    "node_uid": binding["node_uid"],
                    "machine_id_sha256": machine,
                },
            }
        except (StorageError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise _refuse() from error

    def retire(
        self,
        identity: RuntimeIdentity,
        claim: dict[str, Any],
        *,
        operation_id: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            node = self._node()
            size = receipt["identity"]["nominal_gib"]
            request = self._request(identity, claim, node, size, _uuid(operation_id))
            self._retirement_provenance(request, node, receipt)
            self._no_references(identity, claim["metadata"]["name"])
            current = self.api.get_optional("pvc", request.pvc_name, identity.namespace)
            pv = self.api.get_optional("pv", self.volume_name(identity))
            if current is not None or pv is not None:
                live = self._rpc("inspect", request)
                self._validate_receipt(request, node, live, original=receipt.get("operation_id"))
                self._same_volume(receipt, live)
                if current is not None:
                    verified, _ = self._claim(identity, claim, size)
                    if verified["metadata"]["uid"] != receipt["binding"]["pvc_uid"]:
                        raise _refuse()
                if pv is not None:
                    self._retirement_pv(identity, claim, receipt, live, pv)
                if current is not None:
                    self._delete_uid(
                        f"/api/v1/namespaces/{identity.namespace}/persistentvolumeclaims/{request.pvc_name}",
                        current,
                    )
                    if (
                        self.api.get_optional("pvc", request.pvc_name, identity.namespace)
                        is not None
                    ):
                        raise _refuse()
                self._no_references(identity, request.pvc_name)
                if pv is not None:
                    self._delete_uid(f"/api/v1/persistentvolumes/{self.volume_name(identity)}", pv)
            self._no_references(identity, request.pvc_name)
            if (
                self.api.get_optional("pvc", request.pvc_name, identity.namespace) is not None
                or self.api.get_optional("pv", self.volume_name(identity)) is not None
            ):
                raise _refuse()
            result = self._rpc("retire", request)
            if (
                result.get("phase") != "retired"
                or result.get("retirement_operation_id") != operation_id
                or result.get("machine_id_sha256") != _machine(node)
            ):
                raise _refuse()
            self._retirement_provenance(request, node, {**result, "binding": receipt["binding"]})
            self._same_volume(receipt, result)
            if _uint(result["retired_at_ns"], 1) > time.time_ns() + 5_000_000_000:
                raise _refuse()
            current_node = self._node()
            if current_node["metadata"]["uid"] != node["metadata"]["uid"] or _machine(
                current_node
            ) != _machine(node):
                raise _refuse()
            return {**result, "binding": receipt["binding"]}
        except (
            StorageError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            KubernetesCommandError,
            OutcomeUnknown,
        ) as error:
            raise _refuse() from error

    def _retirement_provenance(
        self, request: StorageRequest, node: dict[str, Any], value: dict[str, Any]
    ) -> None:
        safe = self.provenance(value)
        if safe["identity"] != request.identity or safe["binding"]["machine_id_sha256"] != _machine(
            node
        ):
            raise _refuse()

    @staticmethod
    def _same_volume(original: dict[str, Any], current: dict[str, Any]) -> None:
        if any(original[key] != current[key] for key in ("profile", "volume_id", "identity")):
            raise _refuse()
        # A caller may hold the original private in-memory receipt; verify its
        # immutable evidence too. Recovery needs only the path-free provenance:
        # the authenticated root journal owns the inode/fsUUID/path authority.
        if "filesystem_uuid" in original:
            if any(
                original[key] != current[key]
                for key in ("operation_id", "filesystem_uuid", "machine_id_sha256")
            ):
                raise _refuse()
            if any(
                original["backing"][key] != current["backing"][key]
                for key in ("device", "inode", "size_bytes")
            ) or any(
                original["data"][key] != current["data"][key] for key in ("inode", "uid", "gid")
            ):
                raise _refuse()

    def _retirement_pv(
        self,
        identity: RuntimeIdentity,
        claim: dict[str, Any],
        original: dict[str, Any],
        live: dict[str, Any],
        pv: dict[str, Any],
    ) -> None:
        expected = self._manifest(identity, claim, live)
        meta = pv["metadata"]
        if claim["metadata"]["name"] == self.scratch_claim_name(identity) and any(
            _PREFIX + key in meta.get("labels", {}) for key in ("environment", "allocation")
        ):
            raise _refuse()
        if (
            meta["uid"] != original["binding"]["pv_uid"]
            or not meta.get("resourceVersion")
            or meta["name"] != expected["metadata"]["name"]
            or any(
                meta.get("labels", {}).get(k) != v
                for k, v in expected["metadata"]["labels"].items()
            )
            or any(
                meta.get("annotations", {}).get(k) != v
                for k, v in expected["metadata"]["annotations"].items()
            )
        ):
            raise _refuse()
        for key, item in expected["spec"].items():
            if key == "claimRef":
                if any(pv["spec"].get(key, {}).get(k) != v for k, v in item.items()):
                    raise _refuse()
            elif key == "storageClassName" and pv["spec"].get(key, "") == "":
                continue
            elif pv["spec"].get(key) != item:
                raise _refuse()

    def _no_references(self, identity: RuntimeIdentity, claim_name: str) -> None:
        scratch = claim_name == self.scratch_claim_name(identity)
        if (
            not scratch
            and self.api.get_optional("deployment", identity.pod_name, identity.namespace)
            is not None
        ):
            raise _refuse()
        result = json.loads(self.api.call(["get", "pods", "--all-namespaces", "-o", "json"]))
        pods = result["items"]
        if not isinstance(pods, list) or len(pods) > 10000:
            raise _refuse()
        for pod in pods:
            meta = pod["metadata"]
            if meta.get("namespace") != identity.namespace:
                continue
            labels = meta.get("labels", {})
            if not scratch and labels.get(_PREFIX + "environment") == identity.environment_id.hex:
                raise _refuse()
            for volume in pod["spec"].get("volumes", []):
                if volume.get("persistentVolumeClaim", {}).get("claimName") == claim_name:
                    raise _refuse()

    def _delete_uid(self, path: str, obj: dict[str, Any]) -> None:
        metadata = obj["metadata"]
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "propagationPolicy": "Foreground",
            "preconditions": {
                "uid": _uuid(metadata["uid"]),
                "resourceVersion": metadata["resourceVersion"],
            },
        }
        self.api.call(["delete", "--raw", path, "-f", "-"], input_data=json.dumps(options))
