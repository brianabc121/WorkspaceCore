"""Bounded storage safety tests; fake kernel boundary never claims real quota proof."""

import json
import os
import socket
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from app.runtime_v2.storage_host import (
    Config,
    LinuxOps,
    StorageError,
    StorageHost,
    StorageRequest,
    authorize_peer,
    decode_message,
)


def request():
    return StorageRequest.from_dict(
        {
            "workspace_id": str(uuid4()),
            "instance_id": str(uuid4()),
            "environment_id": str(uuid4()),
            "allocation_id": str(uuid4()),
            "generation": 1,
            "pvc_namespace": "workspace-runtime",
            "pvc_name": "volume-test",
            "pvc_uid": str(uuid4()),
            "node_name": "node-a",
            "node_uid": str(uuid4()),
            "nominal_gib": 1,
            "operation_id": str(uuid4()),
        }
    )


class Kernel(LinuxOps):
    """Real files/journals, fake allocation, filesystem tools and mount namespace."""

    def __init__(self):
        self.commands = []
        self.loop_rows = {}
        self.mounts = {}
        self.detached_data = {}
        self.fs_uuid = ""
        self.sparse = False
        self.hole_on_format = False
        self.fail_format = False
        self.free = 20 * 1024**3

    def machine_hash(self):
        return "a" * 64

    def tool_versions(self):
        return {"losetup": "test", "mkfs.ext4": "test", "mount": "test", "blkid": "test"}

    def base_mount(self, path):
        return {"mount_id": 10, "device": "8:1", "root": "/", "target": "/"}

    def free_bytes(self, path):
        return self.free

    def allocate(self, fd, size):
        os.ftruncate(fd, size)
        self.sparse = False

    def allocated_bytes(self, fd):
        return 0 if self.sparse else os.fstat(fd).st_size

    def _execute(self, argv):
        self.commands.append(argv)
        if argv[0] == "/usr/sbin/losetup":
            if "--json" in argv:
                return json.dumps({"loopdevices": list(self.loop_rows.values())})
            path = argv[-1]
            st = os.stat(path)
            self.loop_rows["/dev/loop7"] = {
                "name": "/dev/loop7",
                "back-file": path,
                "back-ino": st.st_ino,
                "back-maj:min": f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}",
                "offset": 0,
                "sizelimit": 0,
                "ro": False,
                "autoclear": False,
            }
            return "/dev/loop7\n"
        if argv[0] == "/usr/sbin/mkfs.ext4":
            if self.fail_format:
                raise StorageError("simulated mkfs interruption")
            self.fs_uuid = argv[argv.index("-U") + 1]
            self.sparse = self.hole_on_format
            return ""
        if argv[0] == "/usr/sbin/blkid":
            return f"UUID={self.fs_uuid}\nTYPE=ext4\n"
        if argv[0] == "/usr/bin/mount":
            if argv[-1] in self.detached_data:
                self.detached_data.pop(argv[-1]).rename(Path(argv[-1]) / "data")
            self.mounts[argv[-1]] = {
                "mount_id": 22,
                "device": self.loop_device("/dev/loop7"),
                "root": "/",
                "target": argv[-1],
                "options": ["rw", "nodev", "nosuid"],
                "fstype": "ext4",
                "source": "/dev/loop7",
                "super_options": ["rw"],
            }
            return ""
        raise AssertionError(argv)

    def lose_mounts(self):
        for target in self.mounts:
            data = Path(target) / "data"
            detached = Path(target).parent.parent / (Path(target).name + "-detached")
            data.rename(detached)
            self.detached_data[target] = detached
        self.mounts.clear()

    def mount_at(self, path):
        return self.mounts.get(str(path))

    def loop_device(self, path):
        return "7:7"

    def data_evidence(self, target, trusted_uid):
        evidence = super().data_evidence(target, trusted_uid)
        major, minor = map(int, self.loop_device("/dev/loop7").split(":"))
        return {**evidence, "device": os.makedev(major, minor)}

    def capacity(self, path):
        return {
            "usable_bytes": 1000_000_000,
            "available_bytes": 900_000_000,
            "used_bytes": 100_000_000,
        }


@pytest.fixture
def host(tmp_path):
    req = request()
    cfg = Config(
        node_name=req.node_name,
        node_uid=req.node_uid,
        machine_id_sha256="a" * 64,
        backing_base=str(tmp_path / "images"),
        mount_base=str(tmp_path / "mounts"),
        state_base=str(tmp_path / "state"),
        socket_path=str(tmp_path / "socket" / "storage.sock"),
        controller_uid=os.getuid(),
        emergency_bytes=2 * 1024**3,
        scratch_bytes=1024**3,
    )
    kernel = Kernel()
    instance = StorageHost(cfg, kernel, trusted_uid=os.getuid(), check_ancestors=False)
    instance.setup()
    return instance, kernel, req


def test_provision_and_retry_require_live_proof_and_never_reformat(host):
    service, kernel, req = host
    receipt = service.provision(req)
    assert receipt["phase"] == "ready"
    assert receipt["backing"]["size_bytes"] == 1073741824
    assert receipt["live"]["usable_bytes"] == 1000_000_000
    assert receipt["identity"]["pvc_uid"] == req.pvc_uid
    assert receipt["live"]["available_bytes"] == 900_000_000
    retried = service.provision(replace(req, operation_id=str(uuid4())))
    assert retried["volume_id"] == receipt["volume_id"]
    formats = [a for a in kernel.commands if a[0] == "/usr/sbin/mkfs.ext4"]
    assert formats == [
        [
            "/usr/sbin/mkfs.ext4",
            "-t",
            "ext4",
            "-m",
            "0",
            "-U",
            receipt["filesystem_uuid"],
            "-E",
            "nodiscard,lazy_itable_init=0,lazy_journal_init=0",
            "/dev/loop7",
        ]
    ]


@pytest.mark.parametrize(
    "field,value", [("nominal_gib", 2), ("pvc_uid", str(uuid4())), ("workspace_id", str(uuid4()))]
)
def test_allocation_rejects_identity_or_size_change(host, field, value):
    service, _, req = host
    service.provision(req)
    with pytest.raises(StorageError):
        service.provision(replace(req, **{field: value}))


@pytest.mark.parametrize("damage", ["missing", "filesystem", "inode", "sparse", "discard"])
def test_inspection_never_reports_unmounted_or_wrong_backing_ready(host, damage):
    service, kernel, req = host
    receipt = service.provision(req)
    if damage == "missing":
        kernel.mounts.clear()
    elif damage == "filesystem":
        kernel.fs_uuid = str(uuid4())
    elif damage == "inode":
        kernel.loop_rows["/dev/loop7"]["back-ino"] += 1
    elif damage == "sparse":
        kernel.sparse = True
    else:
        kernel.mounts[receipt["mount_path"]]["super_options"].append("discard")
    with pytest.raises(StorageError):
        service.inspect(req)


def test_mkfs_holes_refilled_only_while_owned_provisioning(host):
    service, kernel, req = host
    kernel.hole_on_format = True
    assert service.provision(req)["backing"]["allocated_bytes"] >= 1073741824
    kernel.sparse = True
    with pytest.raises(StorageError):
        service.provision(req)
    assert kernel.sparse


def test_crash_phase_never_reformats_and_keeps_backing(host):
    service, kernel, req = host
    kernel.fail_format = True
    with pytest.raises(StorageError):
        service.provision(req)
    images = list(Path(service.config.backing_base).iterdir())
    assert len(images) == 1
    kernel.fail_format = False
    before = len(kernel.commands)
    with pytest.raises(StorageError, match="incomplete"):
        service.provision(req)
    assert len(kernel.commands) == before
    assert images[0].exists()


def test_host_margin_prevents_any_image_allocation(host):
    service, kernel, req = host
    kernel.free = 3 * 1024**3
    with pytest.raises(StorageError, match="margin"):
        service.provision(req)
    assert not list(Path(service.config.backing_base).iterdir())


def test_unknown_backing_symlink_and_hardlink_are_preserved(host, tmp_path):
    service, _, req = host
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"keep")
    image = Path(service.config.backing_base) / f"{req.volume_id}.img"
    image.symlink_to(foreign)
    with pytest.raises(StorageError):
        service.provision(req)
    assert image.is_symlink()
    assert foreign.read_bytes() == b"keep"


def test_stopped_retained_mount_restored_without_formatting(host):
    service, kernel, req = host
    first = service.provision(req)
    kernel.lose_mounts()
    restored = service.restore_mounts()
    assert restored[0]["volume_id"] == first["volume_id"]
    assert service.inspect(req)["phase"] == "ready"
    assert len([a for a in kernel.commands if "mkfs.ext4" in a[0]]) == 1


def test_unknown_artifact_blocks_boot_restore(host):
    service, _, _ = host
    foreign = Path(service.config.backing_base) / "unknown.img"
    foreign.write_bytes(b"retain")
    with pytest.raises(StorageError):
        service.restore_mounts()
    assert foreign.read_bytes() == b"retain"


@pytest.mark.parametrize(
    "change",
    [{"path": "/etc/passwd"}, {"nominal_gib": True}, {"generation": 0}, {"pvc_name": "../../bad"}],
)
def test_request_is_exact_typed_and_path_free(change):
    data = request().as_dict()
    data.update(change)
    with pytest.raises(StorageError):
        StorageRequest.from_dict(data)


def test_protocol_rejects_oversize_duplicate_keys_and_cleanup():
    for raw in [
        b" " * 65537,
        b'{"version":1,"version":1}',
        b'{"version":1,"action":"delete","request":{}}',
    ]:
        with pytest.raises(StorageError):
            decode_message(raw)


def test_wrong_peer_uid_refused_before_read():
    left, right = socket.socketpair()
    try:
        with pytest.raises(StorageError, match="peer"):
            authorize_peer(left, os.getuid() + 1)
    finally:
        left.close()
        right.close()


def test_controller_can_traverse_socket_parent(host):
    service, _, _ = host
    assert Path(service.config.socket_path).parent.stat().st_mode & 0o111 == 0o111


def test_backing_hardlink_added_after_ready_rejected(host, tmp_path):
    service, _, req = host
    receipt = service.provision(req)
    os.link(receipt["backing_path"], tmp_path / "second-link")
    with pytest.raises(StorageError):
        service.inspect(req)


def test_inode_replacement_during_allocation_never_reaches_mkfs(host):
    service, kernel, req = host
    original = kernel.allocate

    def race(fd, size):
        original(fd, size)
        image = Path(service.config.backing_base) / f"{req.volume_id}.img"
        image.rename(image.with_suffix(".retained"))
        image.write_bytes(b"foreign")

    kernel.allocate = race
    with pytest.raises(StorageError):
        service.provision(req)
    assert not any("mkfs.ext4" in argv[0] for argv in kernel.commands)
    assert (Path(service.config.backing_base) / f"{req.volume_id}.img").read_bytes() == b"foreign"


def test_bad_receipt_preserves_allocated_file(host):
    service, _, req = host
    receipt = service.provision(req)
    journal = Path(service.config.state_base) / f"{req.volume_id}.json"
    journal.write_text('{"phase":"ready"}')
    with pytest.raises(StorageError):
        service.inspect(req)
    assert Path(receipt["backing_path"]).exists()


def test_node_replacement_refused(host):
    service, kernel, req = host
    kernel.machine_hash = lambda: "b" * 64
    with pytest.raises(StorageError):
        service.provision(req)
    assert not list(Path(service.config.backing_base).iterdir())


def test_incomplete_format_blocks_boot_restore(host):
    service, kernel, req = host
    kernel.fail_format = True
    with pytest.raises(StorageError):
        service.provision(req)
    with pytest.raises(StorageError):
        service.restore_mounts()


def test_root_config_requires_exact_fields_owner_mode_and_no_symlink(host, tmp_path):
    service, _, _ = host
    from dataclasses import asdict

    path = tmp_path / "config.json"
    data = asdict(service.config)
    data["controller_uid"] = 1001
    path.write_text(json.dumps(data))
    path.chmod(0o644)
    with pytest.raises(StorageError):
        Config.load(str(path))
    path.chmod(0o600)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(StorageError):
        Config.load(str(alias))


def test_concurrent_retry_serialized_to_one_image_and_one_format(host):
    from concurrent.futures import ThreadPoolExecutor

    service, kernel, req = host
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.provision, [req, req]))
    assert results[0]["volume_id"] == results[1]["volume_id"]
    assert len(list(Path(service.config.backing_base).iterdir())) == 1
    assert len([a for a in kernel.commands if "mkfs.ext4" in a[0]]) == 1


@pytest.mark.parametrize("phase", ["created", "allocated", "formatting", "formatted", "mounting"])
def test_crash_after_each_durable_phase_never_reformats(host, phase):
    service, kernel, req = host
    write_phase = service._phase

    def crash(journal, receipt, next_phase):
        write_phase(journal, receipt, next_phase)
        if next_phase == phase:
            raise StorageError("simulated process death")

    service._phase = crash
    with pytest.raises(StorageError):
        service.provision(req)
    before = len(kernel.commands)
    with pytest.raises(StorageError, match="incomplete"):
        service.provision(req)
    assert len(kernel.commands) == before
    assert len(list(Path(service.config.backing_base).iterdir())) == 1


def test_listener_restart_and_duplicate_process_never_unlink_live_socket(host):
    from app.runtime_v2.storage_host import _listener

    service, _, _ = host
    with _listener(service):
        original = Path(service.config.socket_path).stat().st_ino
        with pytest.raises(StorageError), _listener(service):
            pytest.fail("second server acquired the socket")
        assert Path(service.config.socket_path).stat().st_ino == original
    with _listener(service):
        assert Path(service.config.socket_path).is_socket()


def test_listener_never_removes_unknown_existing_socket(host):
    from app.runtime_v2.storage_host import _listener

    service, _, _ = host
    foreign = socket.socket(socket.AF_UNIX)
    foreign.bind(service.config.socket_path)
    try:
        with pytest.raises(StorageError), _listener(service):
            pytest.fail("unknown socket was replaced")
        assert Path(service.config.socket_path).is_socket()
    finally:
        foreign.close()


def test_only_dedicated_data_child_is_exposed_and_lost_found_preserved(host):
    service, _, req = host
    receipt = service.provision(req)
    assert receipt["data_path"] == receipt["mount_path"] + "/data"
    assert Path(receipt["data_path"]).is_dir()
    lost_found = Path(receipt["mount_path"]) / "lost+found"
    lost_found.mkdir()
    (lost_found / "retain").write_text("filesystem metadata")
    service.inspect(req)
    assert (lost_found / "retain").read_text() == "filesystem metadata"


def test_data_child_replacement_rejected_even_with_valid_loop_mount(host):
    service, _, req = host
    receipt = service.provision(req)
    data = Path(receipt["data_path"])
    data.rename(data.with_name("old-data"))
    data.mkdir()
    with pytest.raises(StorageError):
        service.inspect(req)


def test_restore_accepts_changed_loop_device_but_preserves_data_inode(host):
    service, kernel, req = host
    first = service.provision(req)
    data_file = Path(first["data_path"]) / "checksum.txt"
    data_file.write_text("retained checksum")
    kernel.lose_mounts()
    kernel.loop_device = lambda path: "7:8"
    restored = service.restore_mounts()[0]
    assert restored["data"]["inode"] == first["data"]["inode"]
    assert Path(restored["data_path"]).joinpath("checksum.txt").read_text() == "retained checksum"
    assert restored["live"]["mount"]["device"] == "7:8"


def test_standalone_source_parses_as_python39():
    import ast

    from app.runtime_v2 import storage_host

    ast.parse(Path(storage_host.__file__).read_text(), feature_version=(3, 9))


def test_machine_hash_matches_telemetry_normalization(monkeypatch):
    import hashlib

    monkeypatch.setattr(Path, "read_bytes", lambda self: b" 0123456789ABCDEF0123456789ABCDEF\n")
    expected = hashlib.sha256(b"machine-id:0123456789abcdef0123456789abcdef").hexdigest()
    assert LinuxOps().machine_hash() == expected
