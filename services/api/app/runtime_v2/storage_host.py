"""Trusted, node-local fixed ext4 volumes. No Kubernetes or tenant path authority.

Only LinuxOps crosses the kernel/tool boundary. Ordinary socket clients use
fixed provision/inspect/retire actions; restore/setup are operator-only CLI operations. Ambiguous
phases preserve bytes and require operator reconciliation. Retirement independently
verifies cluster absence and host namespace references before inode-bound cleanup.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import socket
import ssl
import stat
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

PROFILE = "workspace-loop-ext4-v1"
MAX_MESSAGE = 65536
KUBE_TIMEOUT = 20
GIB = 1024**3
RETIRE_CONFIG = {"kubeconfig_path", "kubectl_path", "cluster_uid"}
RETIRE_PHASES = {"retire_verified", "unmounting", "unmounted", "detaching", "detached", "unlinking"}
VOLUME_NAMESPACE = UUID("484bf3a8-1f91-4665-aa12-2c469643412f")
TOOLS = {
    "losetup": "/usr/sbin/losetup",
    "mkfs.ext4": "/usr/sbin/mkfs.ext4",
    "mount": "/usr/bin/mount",
    "umount": "/usr/bin/umount",
    "blkid": "/usr/sbin/blkid",
}


class StorageError(RuntimeError):
    """Refusal or unknown outcome; all owned data is retained."""


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise StorageError("UUID must be a string")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise StorageError("noncanonical UUID") from exc
    return value


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise StorageError("integer out of range")
    return value


def _name(value: Any, limit: int = 253) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
    ):
        raise StorageError("invalid Kubernetes identity")
    return value


@dataclass(frozen=True)
class StorageRequest:
    workspace_id: str
    instance_id: str
    environment_id: str
    allocation_id: str
    generation: int
    pvc_namespace: str
    pvc_name: str
    pvc_uid: str
    node_name: str
    node_uid: str
    nominal_gib: int
    operation_id: str

    @classmethod
    def from_dict(cls, data: Any) -> StorageRequest:
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise StorageError("request fields must match the fixed schema")
        for key in (
            "workspace_id",
            "instance_id",
            "environment_id",
            "allocation_id",
            "pvc_uid",
            "node_uid",
            "operation_id",
        ):
            _uuid(data[key])
        _integer(data["generation"], 1, 2**63 - 1)
        _integer(data["nominal_gib"], 1, 1000)
        for key in ("pvc_namespace", "pvc_name", "node_name"):
            _name(data[key], 63 if key == "pvc_namespace" else 253)
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def identity(self) -> dict[str, Any]:
        return {k: v for k, v in self.as_dict().items() if k != "operation_id"}

    @property
    def volume_id(self) -> str:
        return str(uuid5(VOLUME_NAMESPACE, f"{self.allocation_id}:{self.generation}"))


@dataclass(frozen=True)
class Config:
    node_name: str
    node_uid: str
    machine_id_sha256: str
    backing_base: str
    mount_base: str
    state_base: str
    socket_path: str
    controller_uid: int
    emergency_bytes: int
    scratch_bytes: int
    kubeconfig_path: str | None = None
    kubectl_path: str | None = None
    cluster_uid: str | None = None

    def validate(self) -> None:
        _name(self.node_name)
        _uuid(self.node_uid)
        if not re.fullmatch(r"[0-9a-f]{64}", self.machine_id_sha256):
            raise StorageError("invalid machine identity hash")
        _integer(self.controller_uid, 1, 2**31 - 1)
        _integer(self.emergency_bytes, GIB, 2**63 - 1)
        _integer(self.scratch_bytes, GIB, 2**63 - 1)
        retirement = [getattr(self, key) for key in RETIRE_CONFIG]
        if any(value is not None for value in retirement):
            if any(not isinstance(value, str) or not value for value in retirement):
                raise StorageError("retirement configuration must be a complete static bundle")
            _uuid(self.cluster_uid)
            for value in (self.kubeconfig_path, self.kubectl_path):
                path = Path(cast(str, value))
                if not path.is_absolute() or ".." in path.parts:
                    raise StorageError("retirement paths must be fixed absolute paths")
        paths = [
            Path(self.backing_base),
            Path(self.mount_base),
            Path(self.state_base),
            Path(self.socket_path).parent,
        ]
        for path in paths:
            if not path.is_absolute() or ".." in path.parts or str(path) == "/":
                raise StorageError("fixed dedicated absolute paths required")
        for i, left in enumerate(paths):
            for right in paths[i + 1 :]:
                if left == right or left in right.parents or right in left.parents:
                    raise StorageError("storage bases must be disjoint")
        if len(os.fsencode(self.socket_path)) > 100:
            raise StorageError("Unix socket path too long")

    @classmethod
    def load(cls, path: str) -> Config:
        _secure_ancestors(Path(path).parent, 0)
        data = _read_json(Path(path), 0)
        if set(data) - set(cls.__dataclass_fields__) or set(
            cls.__dataclass_fields__
        ) - RETIRE_CONFIG - set(data):
            raise StorageError("config fields must match fixed schema")
        try:
            config = cls(**data)
            config.validate()
        except (TypeError, ValueError) as exc:
            raise StorageError("invalid config") from exc
        return config


def _secure_ancestors(path: Path, uid: int) -> None:
    for item in [*reversed(path.parents), path]:
        st = item.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o022:
            raise StorageError("untrusted path ancestor")


def _file_stat(st: os.stat_result, uid: int, mode: int | None = None) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != uid
        or st.st_nlink != 1
        or (mode is not None and stat.S_IMODE(st.st_mode) != mode)
    ):
        raise StorageError("untrusted file type/owner/link count/mode")


def _read_json(path: Path, uid: int) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            _file_stat(os.fstat(stream.fileno()), uid, 0o600)
            raw = stream.read(MAX_MESSAGE + 1)
        data = _json(raw)
        if not isinstance(data, dict):
            raise StorageError("expected object")
        return data
    except OSError as exc:
        raise StorageError("missing or unsafe durable state") from exc


def _json(raw: bytes, max_bytes: int = MAX_MESSAGE) -> Any:
    if len(raw) > max_bytes:
        raise StorageError("message too large")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise StorageError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(StorageError("nonfinite")),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StorageError("invalid JSON") from exc


def _dirsync(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.parent / f".pending-{uuid4()}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _dirsync(path.parent)
    except BaseException:
        # Preserve incomplete state instead of deleting evidence.
        raise


class LinuxOps:
    """Fixed operations only; requests cannot select executable, flags or devices."""

    def _execute(self, argv: list[str]) -> str:
        try:
            completed = subprocess.run(  # noqa: S603 -- fixed internal argv only
                argv,
                check=True,
                capture_output=True,
                timeout=600,
                env={"PATH": "/usr/sbin:/usr/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise StorageError("host tool failed; preserve volume for reconciliation") from exc
        if len(completed.stdout) + len(completed.stderr) > 1024 * 1024:
            raise StorageError("host tool output too large")
        return (completed.stdout + completed.stderr).decode("utf-8", errors="strict")

    def machine_hash(self) -> str:
        normalized = Path("/etc/machine-id").read_bytes().strip().lower()
        if re.fullmatch(rb"[0-9a-f]{32}", normalized) is None:
            raise StorageError("invalid machine ID")
        return hashlib.sha256(b"machine-id:" + normalized).hexdigest()

    def tool_versions(self) -> dict[str, str]:
        result = {}
        for name, path in TOOLS.items():
            resolved = Path(path).resolve(strict=True)
            _secure_ancestors(resolved.parent, 0)
            st = resolved.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise StorageError("untrusted executable")
            result[name] = self._execute([path, "-V" if name == "mkfs.ext4" else "--version"])
        return result

    def allocate(self, fd: int, size: int) -> None:
        os.posix_fallocate(fd, 0, size)
        os.fsync(fd)

    def allocated_bytes(self, fd: int) -> int:
        return os.fstat(fd).st_blocks * 512

    def free_bytes(self, path: Path) -> int:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    def loops(self) -> list[dict[str, Any]]:
        value = _json(
            self._execute(
                [
                    TOOLS["losetup"],
                    "--json",
                    "--list",
                    "--output",
                    "NAME,BACK-FILE,BACK-INO,BACK-MAJ:MIN,OFFSET,SIZELIMIT,RO,AUTOCLEAR",
                ]
            ).encode()
        )
        if not isinstance(value, dict) or not isinstance(value.get("loopdevices"), list):
            raise StorageError("invalid loop inventory")
        return cast(list[dict[str, Any]], value["loopdevices"])

    def attach(self, path: Path) -> str:
        device = self._execute(
            [TOOLS["losetup"], "--find", "--show", "--nooverlap", "--", str(path)]
        ).strip()
        if not re.fullmatch(r"/dev/loop[0-9]+", device):
            raise StorageError("invalid allocated loop device")
        return device

    def format_new(self, device: str, filesystem_uuid: str) -> None:
        self._execute(
            [
                TOOLS["mkfs.ext4"],
                "-t",
                "ext4",
                "-m",
                "0",
                "-U",
                filesystem_uuid,
                "-E",
                "nodiscard,lazy_itable_init=0,lazy_journal_init=0",
                device,
            ]
        )

    def filesystem(self, device: str) -> dict[str, str]:
        text = self._execute([TOOLS["blkid"], "-p", "-o", "export", "--", device])
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def mount(self, device: str, target: Path) -> None:
        self._execute(
            [TOOLS["mount"], "-t", "ext4", "-o", "rw,nodev,nosuid", "--", device, str(target)]
        )

    def _parse_mounts(self, text: str) -> list[dict[str, Any]]:
        def unescape(value: str) -> str:
            return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)

        entries = []
        for line in text.splitlines():
            left, right = line.split(" - ", 1)
            fields, extra = left.split(), right.split()
            entries.append(
                {
                    "mount_id": int(fields[0]),
                    "device": fields[2],
                    "root": unescape(fields[3]),
                    "target": unescape(fields[4]),
                    "options": fields[5].split(","),
                    "fstype": extra[0],
                    "source": unescape(extra[1]),
                    "super_options": extra[2].split(","),
                }
            )
        return entries

    def _mounts(self) -> list[dict[str, Any]]:
        return self._parse_mounts(Path("/proc/self/mountinfo").read_text())

    def base_mount(self, path: Path) -> dict[str, Any]:
        matches = [
            entry
            for entry in self._mounts()
            if path == Path(entry["target"]) or Path(entry["target"]) in path.parents
        ]
        if not matches:
            raise StorageError("backing filesystem not visible")
        entry = max(matches, key=lambda item: len(item["target"]))
        return {key: entry[key] for key in ("mount_id", "device", "root", "target")}

    def mount_at(self, path: Path) -> dict[str, Any] | None:
        matches = [entry for entry in self._mounts() if entry["target"] == str(path)]
        if len(matches) > 1:
            raise StorageError("stacked mount ambiguity")
        return matches[0] if matches else None

    def loop_device(self, path: str) -> str:
        st = os.lstat(path)
        if not stat.S_ISBLK(st.st_mode):
            raise StorageError("loop device is not a block device")
        return f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"

    def capacity(self, path: Path) -> dict[str, int]:
        st = os.statvfs(path)
        return {
            "usable_bytes": st.f_blocks * st.f_frsize,
            "available_bytes": st.f_bavail * st.f_frsize,
            "used_bytes": (st.f_blocks - st.f_bfree) * st.f_frsize,
        }

    def validate_kube_config(self, config: Config, uid: int, ancestors: bool) -> str:
        if not config.kubeconfig_path or not config.kubectl_path or not config.cluster_uid:
            raise StorageError("root retirement cluster audit is not configured")
        _uuid(config.cluster_uid)
        path = Path(config.kubeconfig_path)
        executable = Path(config.kubectl_path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or not executable.is_absolute()
            or ".." in executable.parts
        ):
            raise StorageError("fixed absolute audit paths required")
        if ancestors:
            _secure_ancestors(path.parent, uid)
            _secure_ancestors(executable.parent, uid)
            _secure_ancestors(executable.resolve(strict=True).parent, uid)
        st = executable.resolve(strict=True).stat()
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != uid
            or st.st_mode & 0o022
            or not st.st_mode & 0o111
        ):
            raise StorageError("untrusted kubectl executable")
        data = _read_json(path, uid)
        required = {"apiVersion", "kind", "current-context", "clusters", "contexts", "users"}
        if (
            not required.issubset(data)
            or set(data) - required - {"preferences"}
            or data["apiVersion"] != "v1"
            or data["kind"] != "Config"
            or data.get("preferences", {}) != {}
        ):
            raise StorageError("flattened strict kubeconfig JSON required")
        for key in ("clusters", "contexts", "users"):
            if not isinstance(data[key], list) or len(data[key]) != 1:
                raise StorageError("one static Kubernetes context required")
        cluster_row, context_row, user_row = (
            data[key][0] for key in ("clusters", "contexts", "users")
        )
        if (
            not isinstance(cluster_row, dict)
            or set(cluster_row) != {"name", "cluster"}
            or not isinstance(context_row, dict)
            or set(context_row) != {"name", "context"}
            or not isinstance(user_row, dict)
            or set(user_row) != {"name", "user"}
        ):
            raise StorageError("invalid static context entries")
        cluster, context, user = cluster_row["cluster"], context_row["context"], user_row["user"]
        if (
            not isinstance(cluster, dict)
            or set(cluster) != {"server", "certificate-authority-data"}
            or not isinstance(context, dict)
            or set(context) - {"cluster", "user", "namespace"}
            or context.get("cluster") != cluster_row["name"]
            or context.get("user") != user_row["name"]
            or data["current-context"] != context_row["name"]
            or not isinstance(user, dict)
        ):
            raise StorageError("dynamic or insecure Kubernetes configuration refused")
        if "namespace" in context:
            _name(context["namespace"], 63)
        try:
            endpoint = urlsplit(cluster["server"])
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.path not in ("", "/")
                or endpoint.query
                or endpoint.fragment
                or (endpoint.port is not None and not 1 <= endpoint.port <= 65535)
            ):
                raise StorageError("Kubernetes API requires verified HTTPS")
            ca = base64.b64decode(cluster["certificate-authority-data"], validate=True)
            ssl.create_default_context(cadata=ca.decode("ascii"))
            if set(user) == {"token"}:
                if (
                    not isinstance(user["token"], str)
                    or not user["token"]
                    or len(user["token"]) > 8192
                    or any(c.isspace() for c in user["token"])
                ):
                    raise StorageError("invalid embedded Kubernetes token")
            elif set(user) == {"client-certificate-data", "client-key-data"}:
                certificate = base64.b64decode(user["client-certificate-data"], validate=True)
                private_key = base64.b64decode(user["client-key-data"], validate=True)
                if b"-----BEGIN CERTIFICATE-----" not in certificate or not re.match(
                    rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", private_key
                ):
                    raise StorageError("embedded certificate/key PEM required")
            else:
                raise StorageError("external credentials or credential plugins refused")
        except (ValueError, TypeError, UnicodeError, ssl.SSLError) as exc:
            raise StorageError("invalid static TLS configuration") from exc
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _execute_kube(self, argv: list[str]) -> str:
        # Separate bounded reader: API output must never fill unbounded communicate buffers.
        deadline = time.monotonic() + KUBE_TIMEOUT
        output = bytearray()
        error = bytearray()
        process = subprocess.Popen(  # noqa: S603 -- root-owned static executable, fixed read-only argv
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={"PATH": "/usr/sbin:/usr/bin:/usr/local/bin", "LC_ALL": "C"},
        )
        try:
            with selectors.DefaultSelector() as selector:
                for pipe, destination in ((process.stdout, output), (process.stderr, error)):
                    if pipe is None:
                        raise StorageError("missing command output pipe")
                    selector.register(pipe, selectors.EVENT_READ, destination)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StorageError("Kubernetes audit timed out")
                    for key, _ in selector.select(min(remaining, 1)):
                        chunk = os.read(
                            key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno(),
                            65536,
                        )
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        key.data.extend(chunk)
                        if len(output) > 8 * 1024**2 or len(error) > 16384:
                            raise StorageError("Kubernetes audit output too large")
                if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
                    raise StorageError("Kubernetes audit failed")
            return output.decode("utf-8", errors="strict")
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise StorageError("Kubernetes audit failed or unknown") from exc
        finally:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def kube_audit(
        self, config: Config, request: StorageRequest, uid: int, ancestors: bool
    ) -> dict[str, Any]:
        fingerprint = self.validate_kube_config(config, uid, ancestors)
        cache = Path(config.state_base) / ".kube-cache"
        with suppress(FileExistsError):
            cache.mkdir(mode=0o700)
        st = cache.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or stat.S_IMODE(st.st_mode) != 0o700:
            raise StorageError("untrusted Kubernetes cache")
        prefix = [
            cast(str, config.kubectl_path),
            "--kubeconfig",
            cast(str, config.kubeconfig_path),
            "--request-timeout=10s",
            "--cache-dir",
            str(cache),
        ]

        def get(resource: str, *args: str) -> Any:
            text = self._execute_kube([*prefix, "get", resource, *args, "-o", "json"])
            return _json(text.encode(), 8 * 1024**2) if text.strip() else None

        namespace = get("namespace", "kube-system")
        node = get("node", config.node_name)
        for value, kind, name, expected_uid in (
            (namespace, "Namespace", "kube-system", config.cluster_uid),
            (node, "Node", config.node_name, config.node_uid),
        ):
            if (
                not isinstance(value, dict)
                or value.get("kind") != kind
                or value.get("apiVersion") != "v1"
                or not isinstance(value.get("metadata"), dict)
                or value["metadata"].get("name") != name
                or value["metadata"].get("uid") != expected_uid
                or not isinstance(value["metadata"].get("resourceVersion"), str)
                or not value["metadata"]["resourceVersion"]
            ):
                raise StorageError("cluster or node UID audit failed")
        try:
            machine = node["status"]["nodeInfo"]["machineID"].strip().lower()
            if (
                not re.fullmatch(r"[0-9a-f]{32}", machine)
                or hashlib.sha256(("machine-id:" + machine).encode("ascii")).hexdigest()
                != config.machine_id_sha256
                or self.machine_hash() != config.machine_id_sha256
            ):
                raise StorageError("current node/machine identity changed")
        except (KeyError, TypeError, AttributeError) as exc:
            raise StorageError("node machine identity missing") from exc
        pv_name = "pv-" + request.volume_id
        if (
            get(
                "pvc",
                request.pvc_name,
                "--namespace",
                request.pvc_namespace,
                "--ignore-not-found=true",
            )
            is not None
        ):
            raise StorageError("PVC name exists; UID replacement is not deletion proof")
        if get("pv", pv_name, "--ignore-not-found=true") is not None:
            raise StorageError("PV name exists; UID replacement is not deletion proof")
        # Preserve API list kind/resourceVersion; kubectl's list printer may rebuild List.
        pods = _json(
            self._execute_kube([*prefix, "get", "--raw", "/api/v1/pods"]).encode(),
            8 * 1024**2,
        )
        attachments = _json(
            self._execute_kube(
                [*prefix, "get", "--raw", "/apis/storage.k8s.io/v1/volumeattachments"]
            ).encode(),
            8 * 1024**2,
        )
        for value, kind in ((pods, "PodList"), (attachments, "VolumeAttachmentList")):
            if (
                not isinstance(value, dict)
                or value.get("kind") != kind
                or not isinstance(value.get("items"), list)
                or not isinstance(value.get("metadata"), dict)
                or not isinstance(value["metadata"].get("resourceVersion"), str)
                or not value["metadata"]["resourceVersion"]
                or value["metadata"].get("continue")
            ):
                raise StorageError("incomplete or malformed cluster reference inventory")
        target = Path(config.mount_base) / request.volume_id
        for pod in pods["items"]:
            if (
                not isinstance(pod, dict)
                or not isinstance(pod.get("metadata"), dict)
                or not isinstance(pod.get("spec"), dict)
                or not isinstance(pod["metadata"].get("namespace"), str)
                or not isinstance(pod["metadata"].get("name"), str)
                or not isinstance(pod["spec"].get("volumes", []), list)
            ):
                raise StorageError("malformed Pod inventory")
            for volume in pod["spec"].get("volumes", []):
                if not isinstance(volume, dict):
                    raise StorageError("malformed Pod volume")
                claim = volume.get("persistentVolumeClaim")
                if claim is not None:
                    if not isinstance(claim, dict) or not isinstance(claim.get("claimName"), str):
                        raise StorageError("malformed PVC reference")
                    if (
                        pod["metadata"]["namespace"] == request.pvc_namespace
                        and claim["claimName"] == request.pvc_name
                    ):
                        raise StorageError("Pod or helper still references the PVC")
                host_path = volume.get("hostPath")
                if host_path is not None:
                    if not isinstance(host_path, dict) or not isinstance(
                        host_path.get("path"), str
                    ):
                        raise StorageError("malformed host path reference")
                    path = Path(os.path.normpath(host_path["path"]))
                    if path == target or path in target.parents or target in path.parents:
                        raise StorageError("Pod host path overlaps the owned mount")
        for attachment in attachments["items"]:
            if (
                not isinstance(attachment, dict)
                or not isinstance(attachment.get("spec"), dict)
                or not isinstance(attachment["spec"].get("source"), dict)
            ):
                raise StorageError("malformed volume attachment inventory")
            source = attachment["spec"]["source"]
            if source.get("persistentVolumeName") == pv_name:
                raise StorageError("volume attachment still references owned PV")
            if "inlineVolumeSpec" in source:
                # Inline attachment cannot be proven unrelated from a name-only inventory.
                raise StorageError("inline volume attachment requires operator audit")
        if self.validate_kube_config(config, uid, ancestors) != fingerprint:
            raise StorageError("Kubernetes audit configuration changed during sample")
        # Re-read the named resources after the potentially large inventory sample.
        if (
            get(
                "pvc",
                request.pvc_name,
                "--namespace",
                request.pvc_namespace,
                "--ignore-not-found=true",
            )
            is not None
            or get("pv", pv_name, "--ignore-not-found=true") is not None
        ):
            raise StorageError("PVC/PV changed during audit")
        for resource, name, expected_uid in (
            ("namespace", "kube-system", config.cluster_uid),
            ("node", config.node_name, config.node_uid),
        ):
            refreshed = get(resource, name)
            if (
                not isinstance(refreshed, dict)
                or not isinstance(refreshed.get("metadata"), dict)
                or refreshed["metadata"].get("uid") != expected_uid
            ):
                raise StorageError("node or cluster changed during reference sample")
            if (
                resource == "node"
                and refreshed.get("status", {}).get("nodeInfo", {}).get("machineID")
                != node["status"]["nodeInfo"]["machineID"]
            ):
                raise StorageError("node machine identity changed during reference sample")
        return {
            "cluster_uid": config.cluster_uid,
            "node_uid": config.node_uid,
            "pvc_name": request.pvc_name,
            "pvc_namespace": request.pvc_namespace,
            "pv_name": pv_name,
            "pods_resource_version": pods["metadata"]["resourceVersion"],
            "attachments_resource_version": attachments["metadata"]["resourceVersion"],
            "sampled_at_ns": time.time_ns(),
        }

    def _task_inventory(self) -> list[Path]:
        tasks: list[Path] = []
        try:
            for process in Path("/proc").iterdir():
                if process.name.isdecimal():
                    tasks.extend(
                        path for path in (process / "task").iterdir() if path.name.isdecimal()
                    )
                    if len(tasks) > 20000:
                        raise StorageError("process inventory exceeds safe audit bound")
        except OSError as exc:
            raise StorageError("unreadable or changing host process inventory") from exc
        return sorted(tasks)

    def _proc_mountinfo(self, path: Path) -> list[dict[str, Any]]:
        try:
            with path.open("r") as stream:
                text = stream.read(8 * 1024**2 + 1)
            if len(text) > 8 * 1024**2:
                raise StorageError("mount namespace inventory exceeds bound")
            return self._parse_mounts(text)
        except (OSError, ValueError, IndexError) as exc:
            raise StorageError("unreadable or malformed mount namespace") from exc

    def _task_identity(self, task: Path) -> tuple[int, bool, bool]:
        # Start time fences PID/TID reuse; scheduling state changes do not replace identity.
        with (task / "stat").open("r") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096 or ")" not in raw:
            raise StorageError("malformed process identity")
        fields = raw.rsplit(")", 1)[1].split()
        if len(fields) < 20:
            raise StorageError("incomplete process identity")
        flags, started = int(fields[6]), int(fields[19])
        if started < 0:
            raise StorageError("invalid process start identity")
        return started, bool(flags & 0x00200000), fields[0] == "Z"

    def _task_snapshot(self, task: Path) -> tuple[tuple[int, bool, bool], tuple[int, int] | None]:
        identity = self._task_identity(task)
        try:
            namespace = os.stat(task / "ns/mnt")
            namespace_key: tuple[int, int] | None = (namespace.st_dev, namespace.st_ino)
        except FileNotFoundError:
            namespace_key = None
        if self._task_identity(task) != identity:
            raise StorageError("task identity changed while reading its namespace")
        return identity, namespace_key

    def _empty_kernel_or_zombie(self, task: Path) -> tuple[int, bool, bool] | None:
        # PF_KTHREAD is a kernel-maintained proc stat flag, never a caller claim.
        started, kernel, zombie = self._task_identity(task)
        if not (kernel or zombie):
            return None
        if list((task / "fd").iterdir()):
            return None
        for link in ("cwd", "root"):
            try:
                os.stat(task / link)
            except FileNotFoundError:
                continue
            return None
        return started, kernel, zombie

    def mount_references(
        self,
        device: str,
        target: Path,
        expected: dict[str, Any] | None,
        backing: dict[str, int],
        own_fd: int | None,
    ) -> dict[str, Any]:
        # No setns is needed. Root must have host /proc visibility and ptrace read permission.
        descriptors = []
        try:
            host = os.stat("/proc/1/ns/mnt")
            own = os.stat("/proc/self/ns/mnt")
            host_key = (host.st_dev, host.st_ino)
            if (own.st_dev, own.st_ino) != host_key:
                raise StorageError("storage service is not in the host mount namespace")
            proc_mount = next((m for m in self._mounts() if m["target"] == "/proc"), None)
            if (
                proc_mount is None
                or proc_mount["fstype"] != "proc"
                or any(
                    option.startswith("hidepid=") and option != "hidepid=0"
                    for option in proc_mount["options"] + proc_mount["super_options"]
                )
            ):
                raise StorageError("host process visibility cannot be proven")
            major, minor = (int(value) for value in device.split(":"))
            fs_device = os.makedev(major, minor)
            tasks = self._task_inventory()
            seen = set()
            seen_expected = 0
            fd_count = 0
            namespace_bytes = 0
            nonuserspace: dict[Path, tuple[int, bool, bool]] = {}
            snapshots: dict[Path, tuple[tuple[int, bool, bool], tuple[int, int] | None]] = {}
            for task in tasks:
                snapshot = self._task_snapshot(task)
                snapshots[task] = snapshot
                try:
                    namespace_fd = os.open(task / "ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
                except FileNotFoundError:
                    identity = self._empty_kernel_or_zombie(task)
                    if identity is None:
                        raise StorageError("live process mount namespace is missing") from None
                    nonuserspace[task] = identity
                    continue
                # Own every successful open before fstat/snapshot can raise.
                descriptors.append(namespace_fd)
                namespace = os.fstat(namespace_fd)
                namespace_key = (namespace.st_dev, namespace.st_ino)
                if namespace_key != snapshot[1] or self._task_snapshot(task) != snapshot:
                    raise StorageError("task namespace or identity changed before mountinfo")
                if namespace_key in seen:
                    os.close(namespace_fd)
                    descriptors.pop()
                else:
                    seen.add(namespace_key)
                    mounts = self._proc_mountinfo(task / "mountinfo")
                    if self._task_snapshot(task) != snapshot:
                        raise StorageError("task namespace or identity changed during mountinfo")
                    namespace_bytes += len(mounts)
                    if namespace_bytes > 250000:
                        raise StorageError("namespace inventory exceeds safe bound")
                    for mount in mounts:
                        mount_path = Path(mount["target"])
                        allowed = (
                            namespace_key == host_key and expected is not None and mount == expected
                        )
                        if allowed:
                            seen_expected += 1
                        elif (
                            mount["device"] == device
                            or mount_path == target
                            or target in mount_path.parents
                        ):
                            raise StorageError("live mount reference outside the owned host mount")
                for link in ("cwd", "root"):
                    try:
                        process_device = os.stat(task / link).st_dev
                    except FileNotFoundError:
                        identity = self._empty_kernel_or_zombie(task)
                        if identity is None:
                            raise StorageError("live process filesystem evidence missing") from None
                        nonuserspace[task] = identity
                        break
                    if process_device == fs_device:
                        raise StorageError("process cwd/root references the owned filesystem")
                if task in nonuserspace:
                    continue
                for fd_path in (task / "fd").iterdir():
                    fd_count += 1
                    if fd_count > 250000:
                        raise StorageError("open file inventory exceeds safe bound")
                    try:
                        value = fd_path.stat()
                    except FileNotFoundError:
                        # Closed descriptors are absent; the task inventory fences process churn.
                        continue
                    if value.st_dev == fs_device or (
                        stat.S_ISBLK(value.st_mode) and value.st_rdev == fs_device
                    ):
                        raise StorageError("open descriptor references owned filesystem/device")
                    if (
                        value.st_dev == backing["device"]
                        and value.st_ino == backing["inode"]
                        and not (
                            task.parts[2] == str(os.getpid())
                            and own_fd is not None
                            and fd_path.name == str(own_fd)
                        )
                    ):
                        raise StorageError("another descriptor retains the backing inode")
            if seen_expected != (1 if expected is not None else 0):
                raise StorageError("owned host mount was not uniquely observed")
            if tasks != self._task_inventory():
                raise StorageError("host process inventory changed during reference audit")
            for task, snapshot in snapshots.items():
                if self._task_snapshot(task) != snapshot:
                    raise StorageError("task identity or namespace changed before audit completed")
            for task, identity in nonuserspace.items():
                if self._empty_kernel_or_zombie(task) != identity:
                    raise StorageError("nonuserspace process identity changed during audit")
            return {
                "host_namespace": f"{host.st_dev}:{host.st_ino}",
                "namespace_count": len(seen),
                "nonuserspace_tasks": len(nonuserspace),
                "task_count": len(tasks),
                "fd_count": fd_count,
                "sampled_at_ns": time.time_ns(),
            }
        except (OSError, ValueError, KeyError, IndexError) as exc:
            raise StorageError("incomplete host namespace/reference proof") from exc
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def unmount_owned(self, target: Path) -> None:
        self._execute([TOOLS["umount"], "--", str(target)])

    def detach_owned(self, device: str) -> None:
        self._execute([TOOLS["losetup"], "--detach", "--", device])

    def prepare_data(self, target: Path, trusted_uid: int) -> dict[str, int]:
        root_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root = os.fstat(root_fd)
            if root.st_uid != trusted_uid:
                raise StorageError("new filesystem root owner mismatch")
            os.fchmod(root_fd, 0o700)
            # Exclusively create the fixed child; never adopt existing filesystem data.
            os.mkdir("data", 0o700, dir_fd=root_fd)
            data_fd = os.open("data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                os.fchown(data_fd, 1000, 1000)
                os.fchmod(data_fd, 0o700)
                os.fsync(data_fd)
            finally:
                os.close(data_fd)
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return self.data_evidence(target, trusted_uid)

    def data_evidence(self, target: Path, trusted_uid: int) -> dict[str, int]:
        root_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root = os.fstat(root_fd)
            if root.st_uid != trusted_uid or stat.S_IMODE(root.st_mode) != 0o700:
                raise StorageError("filesystem root must remain root-owned0700")
            data_fd = os.open("data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                data = os.fstat(data_fd)
                if data.st_dev != root.st_dev or data.st_uid != 1000 or data.st_gid != 1000:
                    raise StorageError("data child owner or filesystem mismatch")
                return {
                    "device": data.st_dev,
                    "inode": data.st_ino,
                    "uid": data.st_uid,
                    "gid": data.st_gid,
                }
            finally:
                os.close(data_fd)
        finally:
            os.close(root_fd)


class StorageHost:
    def __init__(
        self,
        config: Config,
        ops: LinuxOps | None = None,
        *,
        trusted_uid: int = 0,
        check_ancestors: bool = True,
    ):
        # CLI always uses root identity and full ancestor checks.
        self.config = config
        self.ops = ops or LinuxOps()
        self.uid = trusted_uid
        self.check_ancestors = check_ancestors
        self.backing = Path(config.backing_base)
        self.mounts = Path(config.mount_base)
        self.state = Path(config.state_base)

    def _directories(self, create: bool = False) -> None:
        for path in (self.backing, self.mounts, self.state, Path(self.config.socket_path).parent):
            required_mode = 0o711 if path == Path(self.config.socket_path).parent else 0o700
            if create:
                if self.check_ancestors:
                    _secure_ancestors(path.parent, self.uid)
                try:
                    path.mkdir(mode=required_mode)
                    path.chmod(required_mode)
                    _dirsync(path.parent)
                except FileExistsError:
                    pass
            if self.check_ancestors:
                _secure_ancestors(path, self.uid)
            st = path.lstat()
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_uid != self.uid
                or stat.S_IMODE(st.st_mode) != required_mode
            ):
                raise StorageError("storage base has unsafe owner or permissions")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._directories()
        fd = os.open(self.state / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _file_stat(os.fstat(fd), self.uid, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def setup(self) -> dict[str, Any]:
        self._directories(create=True)
        with self._lock():
            setup_path = self.state / "setup.json"
            if setup_path.exists():
                return self._check_setup(upgrade=True)
            if (
                list(self.backing.iterdir())
                or list(self.mounts.iterdir())
                or any(p.name != ".lock" for p in self.state.iterdir())
            ):
                raise StorageError("nonempty uninitialized storage bases")
            if self.ops.machine_hash() != self.config.machine_id_sha256:
                raise StorageError("machine identity mismatch")
            if self.config.kubeconfig_path is not None:
                self.ops.validate_kube_config(self.config, self.uid, self.check_ancestors)
            evidence = {
                "profile": PROFILE,
                "config": asdict(self.config),
                "machine_id_sha256": self.ops.machine_hash(),
                "base_mount": self.ops.base_mount(self.backing),
                "base_device": self.backing.stat().st_dev,
                "tools": self.ops.tool_versions(),
                "created_at_ns": time.time_ns(),
            }
            _write_json(setup_path, evidence)
            return evidence

    def _check_setup(self, *, upgrade: bool = False) -> dict[str, Any]:
        value = _read_json(self.state / "setup.json", self.uid)
        saved = value.get("config")
        current_config = asdict(self.config)
        if not isinstance(saved, dict):
            raise StorageError("invalid setup configuration")
        if (
            value.get("profile") != PROFILE
            or {k: v for k, v in saved.items() if k not in RETIRE_CONFIG}
            != {k: v for k, v in current_config.items() if k not in RETIRE_CONFIG}
            or value.get("machine_id_sha256") != self.ops.machine_hash()
            or value.get("base_device") != self.backing.stat().st_dev
        ):
            raise StorageError("node, configuration or backing filesystem changed")
        current = self.ops.base_mount(self.backing)
        if any(current[key] != value["base_mount"][key] for key in ("device", "root", "target")):
            raise StorageError("backing mount changed")
        old_retirement = {key: saved.get(key) for key in RETIRE_CONFIG}
        new_retirement = {key: current_config[key] for key in RETIRE_CONFIG}
        if old_retirement != new_retirement:
            if (
                not upgrade
                or any(item is not None for item in old_retirement.values())
                or any(item is None for item in new_retirement.values())
            ):
                raise StorageError("retirement bundle changed; explicit additive setup required")
            self.ops.validate_kube_config(self.config, self.uid, self.check_ancestors)
            value["config"] = current_config
            value["retirement_configured_at_ns"] = time.time_ns()
            _write_json(self.state / "setup.json", value)
        if self.config.kubeconfig_path is not None:
            self.ops.validate_kube_config(self.config, self.uid, self.check_ancestors)
        cache = self.state / ".kube-cache"
        if os.path.lexists(cache):
            st = cache.lstat()
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_uid != self.uid
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                raise StorageError("untrusted Kubernetes cache directory")
        return value

    def _paths(self, request: StorageRequest) -> tuple[Path, Path, Path]:
        return (
            self.backing / f"{request.volume_id}.img",
            self.mounts / request.volume_id,
            self.state / f"{request.volume_id}.json",
        )

    def _validate_request(self, request: StorageRequest) -> None:
        StorageRequest.from_dict(request.as_dict())
        if request.node_uid != self.config.node_uid or request.node_name != self.config.node_name:
            raise StorageError("request node identity mismatch")

    def _load(self, request: StorageRequest) -> dict[str, Any]:
        image, target, journal = self._paths(request)
        value = _read_json(journal, self.uid)
        if (
            value.get("profile") != PROFILE
            or value.get("identity") != request.identity
            or value.get("volume_id") != request.volume_id
            or value.get("mount_path") != str(target)
            or value.get("data_path") != str(target / "data")
            or value.get("backing_path") != str(image)
            or value.get("machine_id_sha256") != self.config.machine_id_sha256
        ):
            raise StorageError("immutable receipt identity mismatch")
        _uuid(value.get("filesystem_uuid"))
        _uuid(value.get("operation_id"))
        return value

    def _backing(
        self, request: StorageRequest, receipt: dict[str, Any], *, allow_sparse: bool = False
    ) -> dict[str, int]:
        image = self._paths(request)[0]
        try:
            fd = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                st = os.fstat(fd)
                _file_stat(st, self.uid, 0o600)
                old = receipt["backing"]
                allocated = self.ops.allocated_bytes(fd)
                if (
                    st.st_dev != old["device"]
                    or st.st_ino != old["inode"]
                    or st.st_size != request.nominal_gib * GIB
                    or (not allow_sparse and allocated < st.st_size)
                ):
                    raise StorageError("backing inode/size/allocation mismatch")
                return {
                    "device": st.st_dev,
                    "inode": st.st_ino,
                    "size_bytes": st.st_size,
                    "allocated_bytes": allocated,
                }
            finally:
                os.close(fd)
        except (OSError, KeyError, TypeError) as exc:
            raise StorageError("missing or unsafe backing file") from exc

    def _loop(self, image: Path, backing: dict[str, int]) -> str | None:
        device = (os.major(backing["device"]), os.minor(backing["device"]))
        matches = []
        for row in self.ops.loops():
            value = row.get("back-maj:min")
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9]+:[0-9]+", value.strip(" \t\r\n")) is None
            ):
                raise StorageError("invalid loop backing device numbers")
            major, minor = (int(part) for part in value.strip(" \t\r\n").split(":"))
            same_inode = row.get("back-ino") == backing["inode"] and (major, minor) == device
            if row.get("back-file") == str(image) or same_inode:
                if (
                    not same_inode
                    or row.get("back-file") != str(image)
                    or row.get("offset") != 0
                    or row.get("sizelimit") != 0
                    or row.get("ro") is not False
                    or row.get("autoclear") is not False
                    or not re.fullmatch(r"/dev/loop[0-9]+", row.get("name", ""))
                ):
                    raise StorageError("loop backing mismatch")
                matches.append(row["name"])
        if len(matches) > 1:
            raise StorageError("multiple loop attachments")
        return matches[0] if matches else None

    def _filesystem(self, device: str, receipt: dict[str, Any]) -> None:
        value = self.ops.filesystem(device)
        if value.get("TYPE") != "ext4" or value.get("UUID") != receipt["filesystem_uuid"]:
            raise StorageError("filesystem identity mismatch")

    def _target(self, target: Path, *, create: bool = False) -> None:
        if create:
            try:
                target.mkdir(mode=0o700)
                _dirsync(target.parent)
            except FileExistsError:
                pass
        st = target.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise StorageError("mount target is not a real directory")
        if self.ops.mount_at(target) is None and (
            st.st_uid != self.uid or stat.S_IMODE(st.st_mode) != 0o700 or list(target.iterdir())
        ):
            raise StorageError("uncovered mount target is unsafe or nonempty")

    def _proof(
        self, request: StorageRequest, receipt: dict[str, Any], *, check_data: bool = True
    ) -> dict[str, Any]:
        backing = self._backing(request, receipt)
        image, target, _ = self._paths(request)
        self._target(target)
        device = self._loop(image, backing)
        mount = self.ops.mount_at(target)
        if device is None or mount is None:
            raise StorageError("volume mount missing")
        mount_device = self.ops.loop_device(device)
        if (
            mount["device"] != mount_device
            or mount["root"] != "/"
            or mount["fstype"] != "ext4"
            or mount["source"] != device
            or not {"rw", "nodev", "nosuid"}.issubset(mount["options"])
            or "discard" in mount["options"]
            or "discard" in mount["super_options"]
        ):
            raise StorageError("mount identity/options mismatch")
        self._filesystem(device, receipt)
        data = None
        if check_data:
            data = self.ops.data_evidence(target, self.uid)
            if (
                not isinstance(receipt.get("data"), dict)
                or any(data[key] != receipt["data"].get(key) for key in ("inode", "uid", "gid"))
                or f"{os.major(data['device'])}:{os.minor(data['device'])}" != mount_device
            ):
                raise StorageError("data child immutable inode/filesystem mismatch")
        capacity = self.ops.capacity(target)
        if (
            not 0 < capacity["usable_bytes"] <= request.nominal_gib * GIB
            or not 0 <= capacity["available_bytes"] <= capacity["usable_bytes"]
            or not 0 <= capacity["used_bytes"] <= capacity["usable_bytes"]
        ):
            raise StorageError("filesystem capacity mismatch")
        # Detect mount or backing replacement across the sample.
        if (
            self.ops.mount_at(target) != mount
            or self._backing(request, receipt) != backing
            or self._loop(image, backing) != device
        ):
            raise StorageError("volume changed during inspection")
        return {
            **receipt,
            "backing": backing,
            **({"data": data} if data is not None else {}),
            "live": {
                **capacity,
                "mount": mount,
                "loop_device": device,
                "sampled_at_ns": time.time_ns(),
                "host_reserved_bytes": backing["allocated_bytes"],
            },
        }

    def provision(self, request: StorageRequest) -> dict[str, Any]:
        self._validate_request(request)
        with self._lock():
            self._check_setup()
            image, target, journal = self._paths(request)
            if os.path.lexists(journal):
                receipt = self._load(request)
                if receipt.get("phase") != "ready":
                    raise StorageError(
                        "incomplete allocation retained; operator reconciliation required"
                    )
                return self._proof(request, receipt)
            self._no_pending_retirement()
            if os.path.lexists(image) or os.path.lexists(target):
                raise StorageError("unknown artifact; never adopt or format")
            if self.ops.free_bytes(self.backing) < (
                request.nominal_gib * GIB + self.config.emergency_bytes + self.config.scratch_bytes
            ):
                raise StorageError("host emergency/scratch margin would be consumed")
            receipt = {
                "profile": PROFILE,
                "volume_id": request.volume_id,
                "identity": request.identity,
                "operation_id": request.operation_id,
                "machine_id_sha256": self.config.machine_id_sha256,
                "filesystem_uuid": str(uuid4()),
                "backing_path": str(image),
                "mount_path": str(target),
                "data_path": str(target / "data"),
                "phase": "claimed",
                "created_at_ns": time.time_ns(),
            }
            _write_json(journal, receipt)
            fd = os.open(image, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                st = os.fstat(fd)
                _file_stat(st, self.uid, 0o600)
                receipt["backing"] = {
                    "device": st.st_dev,
                    "inode": st.st_ino,
                    "size_bytes": 0,
                    "allocated_bytes": 0,
                }
                self._phase(journal, receipt, "created")
                _dirsync(image.parent)
                self.ops.allocate(fd, request.nominal_gib * GIB)
                receipt["backing"] = self._backing(request, receipt)
                self._phase(journal, receipt, "allocated")
                if self._loop(image, receipt["backing"]) is not None:
                    raise StorageError("new backing already attached")
                device = self.ops.attach(image)
                if self._loop(image, self._backing(request, receipt)) != device:
                    raise StorageError("attached loop did not match newly created backing")
                self._phase(journal, receipt, "formatting")
                self._backing(request, receipt)
                self.ops.format_new(device, receipt["filesystem_uuid"])
                self._filesystem(device, receipt)
                after = self._backing(request, receipt, allow_sparse=True)
                if after["allocated_bytes"] < request.nominal_gib * GIB:
                    self.ops.allocate(fd, request.nominal_gib * GIB)
                receipt["backing"] = self._backing(request, receipt)
                self._phase(journal, receipt, "formatted")
                self._target(target, create=True)
                if self.ops.mount_at(target) is not None:
                    raise StorageError("new target unexpectedly mounted")
                self._phase(journal, receipt, "mounting")
                self.ops.mount(device, target)
                self._proof(request, receipt, check_data=False)
                receipt["data"] = self.ops.prepare_data(target, self.uid)
                proof = self._proof(request, receipt)
                receipt.update(proof)
                self._phase(journal, receipt, "ready")
                return receipt
            except OSError as exc:
                raise StorageError("allocation failed; incomplete volume retained") from exc
            finally:
                os.close(fd)

    def _phase(self, journal: Path, receipt: dict[str, Any], phase: str) -> None:
        receipt["phase"] = phase
        receipt["updated_at_ns"] = time.time_ns()
        _write_json(journal, receipt)

    def inspect(self, request: StorageRequest) -> dict[str, Any]:
        self._validate_request(request)
        with self._lock():
            self._check_setup()
            receipt = self._load(request)
            if receipt.get("phase") != "ready":
                raise StorageError("incomplete volume")
            return self._proof(request, receipt)

    def _retirement_audit(
        self,
        request: StorageRequest,
        receipt: dict[str, Any],
        expected: dict[str, Any] | None,
        own_fd: int | None,
    ) -> None:
        cluster = self.ops.kube_audit(self.config, request, self.uid, self.check_ancestors)
        references = self.ops.mount_references(
            receipt["retirement"]["mount_device"],
            self._paths(request)[1],
            expected,
            receipt["backing"],
            own_fd,
        )
        receipt["retirement"]["cluster_audit"] = cluster
        receipt["retirement"]["reference_audit"] = references

    def _retired_absence(self, request: StorageRequest, receipt: dict[str, Any]) -> None:
        image, target, _ = self._paths(request)
        if os.path.lexists(image) or self.ops.mount_at(target) is not None:
            raise StorageError("retired volume unexpectedly has backing or mount")
        self._target(target)
        for row in self.ops.loops():
            if row.get("back-file") in (str(image), str(image) + " (deleted)"):
                raise StorageError("retired volume still has a loop attachment")
        if receipt.get("phase") == "retired" and (
            not isinstance(receipt.get("retired_at_ns"), int)
            or not isinstance(receipt.get("retirement_operation_id"), str)
        ):
            raise StorageError("invalid retirement tombstone")

    def retire(self, request: StorageRequest) -> dict[str, Any]:
        self._validate_request(request)
        with self._lock():
            self._check_setup()
            image, target, journal = self._paths(request)
            receipt = self._load(request)
            phase = receipt.get("phase")
            if phase not in {"ready", "retired", *RETIRE_PHASES}:
                raise StorageError("unproven incomplete allocation cannot be retired")
            # Authentication, node/cluster identity and absence are independently re-read.
            self.ops.kube_audit(self.config, request, self.uid, self.check_ancestors)
            if phase == "retired":
                if receipt.get("retirement_operation_id") != request.operation_id:
                    raise StorageError("retirement operation differs from durable tombstone")
                self._retired_absence(request, receipt)
                return receipt
            if phase == "ready":
                proof = self._proof(request, receipt)
                receipt["retirement_operation_id"] = request.operation_id
                receipt["retirement"] = {
                    "loop_device": proof["live"]["loop_device"],
                    "mount_device": proof["live"]["mount"]["device"],
                    "started_at_ns": time.time_ns(),
                    "parent_free_before_bytes": self.ops.free_bytes(self.backing),
                }
                self._retirement_audit(request, receipt, proof["live"]["mount"], None)
                self._phase(journal, receipt, "retire_verified")
                phase = "retire_verified"
            elif receipt.get("retirement_operation_id") != request.operation_id:
                raise StorageError("incomplete retirement belongs to another operation")
            retirement = receipt.get("retirement")
            if (
                not isinstance(retirement, dict)
                or not re.fullmatch(r"/dev/loop[0-9]+", retirement.get("loop_device", ""))
                or not re.fullmatch(r"[0-9]+:[0-9]+", retirement.get("mount_device", ""))
            ):
                raise StorageError("invalid durable retirement identity")
            file_fd: int | None = None
            try:
                if os.path.lexists(image):
                    file_fd = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    self._backing(request, receipt)
                    current = os.fstat(file_fd)
                    _file_stat(current, self.uid, 0o600)
                    if (
                        current.st_dev != receipt["backing"]["device"]
                        or current.st_ino != receipt["backing"]["inode"]
                    ):
                        raise StorageError("pinned retirement inode changed")
                    self._filesystem(str(image), receipt)
                elif phase != "unlinking":
                    raise StorageError("backing disappeared before durable unlink intent")
                if phase in ("retire_verified", "unmounting"):
                    mounted = self.ops.mount_at(target)
                    if mounted is not None:
                        proof = self._proof(request, receipt)
                        if (
                            proof["live"]["loop_device"] != retirement["loop_device"]
                            or proof["live"]["mount"]["device"] != retirement["mount_device"]
                        ):
                            raise StorageError("loop changed during retirement")
                        self._retirement_audit(request, receipt, proof["live"]["mount"], file_fd)
                        self._phase(journal, receipt, "unmounting")
                        self.ops.unmount_owned(target)
                    elif phase != "unmounting":
                        raise StorageError("mount disappeared before durable unmount intent")
                    if self.ops.mount_at(target) is not None:
                        raise StorageError("unmount did not remove the owned mount")
                    self._target(target)
                    self._retirement_audit(request, receipt, None, file_fd)
                    self._phase(journal, receipt, "unmounted")
                    phase = "unmounted"
                if phase in ("unmounted", "detaching"):
                    if self.ops.mount_at(target) is not None:
                        raise StorageError("owned target remounted during retirement")
                    backing = self._backing(request, receipt)
                    device = self._loop(image, backing)
                    if device is not None:
                        if device != retirement["loop_device"]:
                            raise StorageError("loop attachment changed during retirement")
                        self._filesystem(device, receipt)
                        self._retirement_audit(request, receipt, None, file_fd)
                        self._phase(journal, receipt, "detaching")
                        self.ops.detach_owned(device)
                    elif phase != "detaching":
                        raise StorageError("loop disappeared before durable detach intent")
                    # losetup may implement lazy detach; readback, not exit status, is proof.
                    if self._loop(image, self._backing(request, receipt)) is not None:
                        raise StorageError("loop detach still pending; backing remains reserved")
                    self._retirement_audit(request, receipt, None, file_fd)
                    self._phase(journal, receipt, "detached")
                    phase = "detached"
                if phase not in ("detached", "unlinking"):
                    raise StorageError("unsupported retirement recovery phase")
                if self.ops.mount_at(target) is not None:
                    raise StorageError("unexpected mount before backing unlink")
                self._target(target)
                if self._loop(image, receipt["backing"]) is not None:
                    raise StorageError("loop attachment exists before backing unlink")
                self._retirement_audit(request, receipt, None, file_fd)
                if file_fd is not None:
                    self._backing(request, receipt)
                    self._filesystem(str(image), receipt)
                    self._phase(journal, receipt, "unlinking")
                    base_fd = os.open(self.backing, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        current = os.stat(image.name, dir_fd=base_fd, follow_symlinks=False)
                        pinned = os.fstat(file_fd)
                        _file_stat(current, self.uid, 0o600)
                        if (current.st_dev, current.st_ino) != (
                            pinned.st_dev,
                            pinned.st_ino,
                        ) or current.st_size != request.nominal_gib * GIB:
                            raise StorageError("backing changed immediately before unlink")
                        os.unlink(image.name, dir_fd=base_fd)
                        os.fsync(base_fd)
                        if os.fstat(file_fd).st_nlink != 0:
                            raise StorageError("backing inode still has another link")
                    finally:
                        os.close(base_fd)
                    os.close(file_fd)
                    file_fd = None
                # This also detects descriptors left after an unlink-to-tombstone crash.
                self._retirement_audit(request, receipt, None, None)
                self._retired_absence(request, receipt)
                retirement["parent_free_after_bytes"] = self.ops.free_bytes(self.backing)
                retirement["observed_reclaimed_bytes"] = max(
                    0,
                    retirement["parent_free_after_bytes"] - retirement["parent_free_before_bytes"],
                )
                retirement["released_backing_bytes"] = receipt["backing"]["allocated_bytes"]
                receipt["retired_at_ns"] = time.time_ns()
                self._phase(journal, receipt, "retired")
                return receipt
            except OSError as exc:
                raise StorageError("retirement failed or unknown; durable phase retained") from exc
            finally:
                if file_fd is not None:
                    os.close(file_fd)

    def _no_pending_retirement(self) -> None:
        for path in self.state.glob("*.json"):
            if path.name in ("setup.json", ".socket.json"):
                continue
            if _read_json(path, self.uid).get("phase") in RETIRE_PHASES:
                raise StorageError("pending retirement fences new allocations and loop reuse")

    def restore_mounts(self) -> list[dict[str, Any]]:
        with self._lock():
            self._check_setup()
            receipts = []
            tombstones = []
            for path in self.state.iterdir():
                if path.name in (
                    "setup.json",
                    ".lock",
                    ".listener.lock",
                    ".socket.json",
                    ".kube-cache",
                ):
                    continue
                if not re.fullmatch(r"[0-9a-f-]{36}\.json", path.name):
                    raise StorageError("unknown state artifact blocks kubelet admission")
                raw = _read_json(path, self.uid)
                try:
                    req = StorageRequest.from_dict(
                        {**raw["identity"], "operation_id": raw["operation_id"]}
                    )
                except (KeyError, TypeError) as exc:
                    raise StorageError("invalid receipt") from exc
                self._validate_request(req)
                receipt = self._load(req)
                if receipt.get("phase") == "retired" and path == self._paths(req)[2]:
                    self._retired_absence(req, receipt)
                    tombstones.append((req, receipt))
                    continue
                if receipt.get("phase") != "ready" or path != self._paths(req)[2]:
                    raise StorageError("incomplete receipt blocks kubelet admission")
                receipts.append((req, receipt))
            known_images = {self._paths(req)[0].name for req, _ in receipts}
            known_targets = {self._paths(req)[1].name for req, _ in receipts + tombstones}
            if {p.name for p in self.backing.iterdir()} != known_images or {
                p.name for p in self.mounts.iterdir()
            } != known_targets:
                raise StorageError("unknown or missing volume artifacts block kubelet admission")
            # Validate every retained inode before performing any restoration.
            for req, receipt in receipts:
                self._backing(req, receipt)
            result = []
            for req, receipt in receipts:
                image, target, journal = self._paths(req)
                self._target(target)
                if self.ops.mount_at(target) is None:
                    backing = self._backing(req, receipt)
                    device = self._loop(image, backing)
                    if device is None:
                        device = self.ops.attach(image)
                    if self._loop(image, self._backing(req, receipt)) != device:
                        raise StorageError("restore loop mismatch")
                    self._filesystem(device, receipt)
                    self.ops.mount(device, target)
                proof = self._proof(req, receipt)
                _write_json(journal, proof)
                result.append(proof)
            return result


def decode_message(raw: bytes) -> tuple[str, StorageRequest]:
    value = _json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "action", "request"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["action"] not in ("provision", "inspect", "retire")
    ):
        raise StorageError("unsupported fixed protocol")
    return value["action"], StorageRequest.from_dict(value["request"])


def authorize_peer(connection: socket.socket, allowed_uid: int) -> None:
    _, uid, _ = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    if uid != allowed_uid:
        raise StorageError("Unix peer UID refused")


def _socket_evidence(path: Path, uid: int) -> dict[str, int]:
    st = path.lstat()
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != uid or st.st_nlink != 1:
        raise StorageError("unknown Unix socket artifact")
    return {"device": st.st_dev, "inode": st.st_ino, "uid": st.st_uid}


@contextmanager
def _listener(host: StorageHost) -> Iterator[socket.socket]:
    host._directories()
    path = Path(host.config.socket_path)
    state = host.state / ".socket.json"
    lock_fd = os.open(host.state / ".listener.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owned: dict[str, int] | None = None
    try:
        _file_stat(os.fstat(lock_fd), host.uid, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageError("storage server already running") from exc
        if os.path.lexists(path):
            prior = _read_json(state, host.uid)
            current = _socket_evidence(path, host.config.controller_uid)
            if prior != current:
                raise StorageError("existing socket has no exact durable ownership")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(path))
                except OSError as exc:
                    if exc.errno != errno.ECONNREFUSED:
                        raise StorageError("socket state ambiguous") from exc
                else:
                    raise StorageError("existing socket is live")
            if _socket_evidence(path, host.config.controller_uid) != current:
                raise StorageError("socket changed before stale removal")
            path.unlink()
            _dirsync(path.parent)
        listener.bind(str(path))
        os.chown(path, host.config.controller_uid, -1, follow_symlinks=False)
        os.chmod(path, 0o600, follow_symlinks=False)
        owned = _socket_evidence(path, host.config.controller_uid)
        _write_json(state, owned)
        _dirsync(path.parent)
        listener.listen(16)
        yield listener
    finally:
        listener.close()
        try:
            if (
                owned is not None
                and os.path.lexists(path)
                and _socket_evidence(path, host.config.controller_uid) == owned
            ):
                path.unlink()
                _dirsync(path.parent)
        finally:
            os.close(lock_fd)


def serve(host: StorageHost) -> None:
    host.restore_mounts()
    with _listener(host) as listener:
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                try:
                    authorize_peer(connection, host.config.controller_uid)
                    data = bytearray()
                    while b"\n" not in data:
                        chunk = connection.recv(min(4096, MAX_MESSAGE + 1 - len(data)))
                        if not chunk:
                            raise StorageError("incomplete frame")
                        data.extend(chunk)
                        if len(data) >= MAX_MESSAGE and b"\n" not in data:
                            raise StorageError("message too large")
                        if len(data) > MAX_MESSAGE:
                            raise StorageError("message too large")
                    frame, trailing = bytes(data).split(b"\n", 1)
                    if trailing:
                        raise StorageError("one frame per connection")
                    action, request = decode_message(frame)
                    result = (
                        host.provision(request)
                        if action == "provision"
                        else host.inspect(request)
                        if action == "inspect"
                        else host.retire(request)
                    )
                    response = {"version": 1, "ok": True, "receipt": result}
                except (StorageError, OSError, ValueError, KeyError, TypeError):
                    response = {"version": 1, "ok": False, "error": "storage_refused_or_unknown"}
                with suppress(OSError):
                    connection.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("setup", "provision", "inspect", "retire", "restore-mounts", "serve")
    )
    parser.add_argument("--config", default="/etc/workspace/runtime-v2/storage-host.json")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise StorageError("root operator required")
        host = StorageHost(Config.load(args.config))
        if args.mode == "serve":
            serve(host)
            return 0
        if args.mode == "setup":
            result: Any = host.setup()
        elif args.mode == "restore-mounts":
            result = host.restore_mounts()
        else:
            request = StorageRequest.from_dict(_json(sys.stdin.buffer.read(MAX_MESSAGE + 1)))
            result = (
                host.provision(request)
                if args.mode == "provision"
                else host.inspect(request)
                if args.mode == "inspect"
                else host.retire(request)
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StorageError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"storage host refused: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
