"""Trusted, node-local fixed ext4 volumes. No Kubernetes or tenant path authority.

Only LinuxOps crosses the kernel/tool boundary. Ordinary socket clients can
provision or inspect; restore/setup are operator-only CLI operations. Ambiguous
phases preserve bytes and require operator reconciliation; deletion is unsupported.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import socket
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
from uuid import UUID, uuid4, uuid5

PROFILE = "workspace-loop-ext4-v1"
MAX_MESSAGE = 65536
GIB = 1024**3
VOLUME_NAMESPACE = UUID("484bf3a8-1f91-4665-aa12-2c469643412f")
TOOLS = {
    "losetup": "/usr/sbin/losetup",
    "mkfs.ext4": "/usr/sbin/mkfs.ext4",
    "mount": "/usr/bin/mount",
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

    def validate(self) -> None:
        _name(self.node_name)
        _uuid(self.node_uid)
        if not re.fullmatch(r"[0-9a-f]{64}", self.machine_id_sha256):
            raise StorageError("invalid machine identity hash")
        _integer(self.controller_uid, 1, 2**31 - 1)
        _integer(self.emergency_bytes, GIB, 2**63 - 1)
        _integer(self.scratch_bytes, GIB, 2**63 - 1)
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
        if set(data) != set(cls.__dataclass_fields__):
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


def _json(raw: bytes) -> Any:
    if len(raw) > MAX_MESSAGE:
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

    def _mounts(self) -> list[dict[str, Any]]:
        def unescape(value: str) -> str:
            return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)

        entries = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
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
                return self._check_setup()
            if (
                list(self.backing.iterdir())
                or list(self.mounts.iterdir())
                or any(p.name != ".lock" for p in self.state.iterdir())
            ):
                raise StorageError("nonempty uninitialized storage bases")
            if self.ops.machine_hash() != self.config.machine_id_sha256:
                raise StorageError("machine identity mismatch")
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

    def _check_setup(self) -> dict[str, Any]:
        value = _read_json(self.state / "setup.json", self.uid)
        if (
            value.get("profile") != PROFILE
            or value.get("config") != asdict(self.config)
            or value.get("machine_id_sha256") != self.ops.machine_hash()
            or value.get("base_device") != self.backing.stat().st_dev
        ):
            raise StorageError("node, configuration or backing filesystem changed")
        current = self.ops.base_mount(self.backing)
        if any(current[key] != value["base_mount"][key] for key in ("device", "root", "target")):
            raise StorageError("backing mount changed")
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
        device = f"{os.major(backing['device'])}:{os.minor(backing['device'])}"
        matches = []
        for row in self.ops.loops():
            same_inode = (
                row.get("back-ino") == backing["inode"] and row.get("back-maj:min") == device
            )
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

    def restore_mounts(self) -> list[dict[str, Any]]:
        with self._lock():
            self._check_setup()
            receipts = []
            for path in self.state.iterdir():
                if path.name in ("setup.json", ".lock", ".listener.lock", ".socket.json"):
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
                if receipt.get("phase") != "ready" or path != self._paths(req)[2]:
                    raise StorageError("incomplete receipt blocks kubelet admission")
                receipts.append((req, receipt))
            known_images = {self._paths(req)[0].name for req, _ in receipts}
            known_targets = {self._paths(req)[1].name for req, _ in receipts}
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
        or value["action"] not in ("provision", "inspect")
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
                        host.provision(request) if action == "provision" else host.inspect(request)
                    )
                    response = {"version": 1, "ok": True, "receipt": result}
                except (StorageError, OSError, ValueError, KeyError, TypeError):
                    response = {"version": 1, "ok": False, "error": "storage_refused_or_unknown"}
                with suppress(OSError):
                    connection.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("setup", "provision", "inspect", "restore-mounts", "serve")
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
            result = host.provision(request) if args.mode == "provision" else host.inspect(request)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StorageError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"storage host refused: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
