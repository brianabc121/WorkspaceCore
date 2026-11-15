"""Retirement exercises real journals/inodes; kernel and Kubernetes reads are injected."""

import base64
import json
import os
import ssl
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.runtime_v2.storage_host import (
    Config,
    LinuxOps,
    StorageError,
    StorageHost,
    StorageRequest,
    decode_message,
)
from tests.runtime_v2.test_storage_host import Kernel, request


class RetireKernel(Kernel):
    def __init__(self) -> None:
        super().__init__()
        self.kube_calls: list[list[str]] = []
        self.objects: dict[str, Any] = {}
        self.references: list[str] = []
        self.fail_unmount = False
        self.fail_detach = False
        self.fail_kube = False
        self.reclaimed = False
        self.audit_calls = 0
        self.physical_hash = "a" * 64

    def machine_hash(self) -> str:
        return self.physical_hash

    def _execute_kube(self, argv: list[str]) -> str:
        self.kube_calls.append(argv)
        if self.fail_kube:
            raise StorageError("simulated Kubernetes timeout")
        command = argv[argv.index("get") + 1 :]
        resource = command[0]
        if resource == "--raw":
            assert len(command) == 2
            resource = {
                "/api/v1/pods": "pods",
                "/apis/storage.k8s.io/v1/volumeattachments": "volumeattachments.storage.k8s.io",
            }[command[1]]
        return json.dumps(self.objects[resource]) if self.objects[resource] is not None else ""

    def mount_references(
        self,
        device: str,
        target: Path,
        expected: dict[str, Any] | None,
        backing: dict[str, int],
        own_fd: int | None,
    ) -> dict[str, Any]:
        self.audit_calls += 1
        if self.references:
            raise StorageError("live namespace or fd reference")
        return {
            "namespace_count": 3,
            "task_count": 5,
            "sampled_at_ns": 123,
            "host_namespace": "0:1",
        }

    def _execute(self, argv: list[str]) -> str:
        if argv[0] == "/usr/bin/umount":
            self.commands.append(argv)
            if self.fail_unmount:
                raise StorageError("busy")
            self.lose_mounts()
            return ""
        if argv[0] == "/usr/sbin/losetup" and "--detach" in argv:
            self.commands.append(argv)
            if self.fail_detach:
                raise StorageError("detach unknown")
            self.loop_rows.clear()
            return ""
        return super()._execute(argv)


RetiredHost = tuple[StorageHost, RetireKernel, StorageRequest, dict[str, Any]]
NamespaceAudit = tuple[LinuxOps, list[Path], Path, dict[str, Any], dict[str, int], Path]


@pytest.fixture
def retired_host(tmp_path: Path) -> RetiredHost:
    req = request()
    ca = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
    kube = tmp_path / "kube.json"
    kube.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "storage",
                "clusters": [
                    {
                        "name": "cluster",
                        "cluster": {
                            "server": "https://127.0.0.1:6443",
                            "certificate-authority-data": base64.b64encode(
                                ssl.DER_cert_to_PEM_cert(ca).encode()
                            ).decode(),
                        },
                    }
                ],
                "contexts": [
                    {"name": "storage", "context": {"cluster": "cluster", "user": "reader"}}
                ],
                "users": [{"name": "reader", "user": {"token": "test-only-token"}}],
            }
        )
    )
    kube.chmod(0o600)
    executable = tmp_path / "kubectl-root"
    executable.write_text("#!/bin/sh\nexit 1\n")
    executable.chmod(0o700)
    cfg = Config(
        node_name=req.node_name,
        node_uid=req.node_uid,
        machine_id_sha256="a" * 64,
        backing_base=str(tmp_path / "images"),
        mount_base=str(tmp_path / "mounts"),
        state_base=str(tmp_path / "state"),
        socket_path=str(tmp_path / "socket" / "host.sock"),
        controller_uid=os.getuid(),
        emergency_bytes=2 * 1024**3,
        scratch_bytes=1024**3,
        kubeconfig_path=str(kube),
        kubectl_path=str(executable),
        cluster_uid=str(uuid4()),
    )
    kernel = RetireKernel()
    kernel.objects = {
        "namespace": {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "kube-system", "uid": cfg.cluster_uid, "resourceVersion": "10"},
        },
        "node": {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {"name": req.node_name, "uid": req.node_uid, "resourceVersion": "11"},
            "status": {"nodeInfo": {"machineID": "0123456789abcdef0123456789abcdef"}},
        },
        "pvc": None,
        "pv": None,
        "pods": {
            "apiVersion": "v1",
            "kind": "PodList",
            "metadata": {"resourceVersion": "12"},
            "items": [],
        },
        "volumeattachments.storage.k8s.io": {
            "apiVersion": "storage.k8s.io/v1",
            "kind": "VolumeAttachmentList",
            "metadata": {"resourceVersion": "13"},
            "items": [],
        },
    }
    import hashlib

    machine_hash = hashlib.sha256(b"machine-id:0123456789abcdef0123456789abcdef").hexdigest()
    cfg = replace(cfg, machine_id_sha256=machine_hash)
    kernel.physical_hash = machine_hash
    host = StorageHost(cfg, kernel, trusted_uid=os.getuid(), check_ancestors=False)
    host.setup()
    receipt = host.provision(req)
    delete = replace(req, operation_id=str(uuid4()))
    return host, kernel, delete, receipt


def test_retire_independently_proves_absence_then_releases_only_owned_image(
    retired_host: RetiredHost,
) -> None:
    host, kernel, req, ready = retired_host
    receipt = host.retire(req)
    assert receipt["phase"] == "retired"
    assert receipt["retirement_operation_id"] == req.operation_id
    assert receipt["operation_id"] == ready["operation_id"]
    assert receipt["retired_at_ns"] > ready["created_at_ns"]
    assert receipt["backing"] == ready["backing"]
    assert not Path(ready["backing_path"]).exists()
    assert Path(ready["mount_path"]).is_dir()
    assert list(Path(ready["mount_path"]).iterdir()) == []
    assert not kernel.loop_rows
    assert [a for a in kernel.commands if a[0] == "/usr/bin/umount"] == [
        ["/usr/bin/umount", "--", ready["mount_path"]]
    ]
    assert [a for a in kernel.commands if "--detach" in a] == [
        ["/usr/sbin/losetup", "--detach", "--", "/dev/loop7"]
    ]
    assert len(kernel.kube_calls) >= 12
    assert kernel.audit_calls >= 3
    assert host.retire(req)["retired_at_ns"] == receipt["retired_at_ns"]
    assert host.restore_mounts() == []
    with pytest.raises(StorageError):
        host.provision(req)


@pytest.mark.parametrize(
    "unsafe",
    [
        "pvc",
        "pv",
        "pod",
        "attachment",
        "node_uid",
        "cluster_uid",
        "machine_id",
        "pod_page",
        "pod_malformed",
        "timeout",
    ],
)
def test_root_audit_refuses_before_unmount_even_if_client_deleted_resources(
    retired_host: RetiredHost, unsafe: str
) -> None:
    host, kernel, req, ready = retired_host
    if unsafe in ("pvc", "pv"):
        kernel.objects[unsafe] = {"metadata": {"uid": str(uuid4())}}
    elif unsafe == "pod":
        kernel.objects["pods"]["items"] = [
            {
                "metadata": {"name": "helper", "namespace": req.pvc_namespace},
                "spec": {
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": req.pvc_name}}
                    ]
                },
            }
        ]
    elif unsafe == "attachment":
        kernel.objects["volumeattachments.storage.k8s.io"]["items"] = [
            {"spec": {"source": {"persistentVolumeName": "pv-" + req.volume_id}}}
        ]
    elif unsafe == "node_uid":
        kernel.objects["node"]["metadata"]["uid"] = str(uuid4())
    elif unsafe == "cluster_uid":
        kernel.objects["namespace"]["metadata"]["uid"] = str(uuid4())
    elif unsafe == "machine_id":
        kernel.objects["node"]["status"]["nodeInfo"]["machineID"] = "b" * 32
    elif unsafe == "pod_page":
        kernel.objects["pods"]["metadata"]["continue"] = "missing-page"
    elif unsafe == "pod_malformed":
        kernel.objects["pods"]["items"] = [{"spec": {}}]
    else:
        kernel.fail_kube = True
    with pytest.raises(StorageError):
        host.retire(req)
    assert Path(ready["backing_path"]).exists()
    assert not any(a[0] == "/usr/bin/umount" for a in kernel.commands)


def test_live_reference_in_any_namespace_or_fd_blocks_retirement(retired_host: RetiredHost) -> None:
    host, kernel, req, ready = retired_host
    kernel.references.append("foreign namespace bind")
    with pytest.raises(StorageError):
        host.retire(req)
    assert Path(ready["backing_path"]).exists()
    assert not any("--detach" in a for a in kernel.commands)


@pytest.mark.parametrize("boundary", ["unmount", "detach"])
def test_failed_destructive_stage_keeps_backing_and_boot_fence_until_verified_retry(
    retired_host: RetiredHost, boundary: str
) -> None:
    host, kernel, req, ready = retired_host
    setattr(kernel, "fail_" + boundary, True)
    with pytest.raises(StorageError):
        host.retire(req)
    assert Path(ready["backing_path"]).exists()
    with pytest.raises(StorageError):
        host.restore_mounts()
    setattr(kernel, "fail_" + boundary, False)
    assert host.retire(req)["phase"] == "retired"


@pytest.mark.parametrize(
    "phase", ["retire_verified", "unmounting", "unmounted", "detaching", "detached", "unlinking"]
)
def test_retirement_crash_phases_resume_without_reformat_or_unknown_data_loss(
    retired_host: RetiredHost, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, kernel, req, ready = retired_host
    original = host._phase

    def die(journal: Path, receipt: dict[str, Any], next_phase: str) -> None:
        original(journal, receipt, next_phase)
        if next_phase == phase:
            raise StorageError("crash")

    monkeypatch.setattr(host, "_phase", die)
    with pytest.raises(StorageError):
        host.retire(req)
    monkeypatch.setattr(host, "_phase", original)
    assert host.retire(req)["phase"] == "retired"
    assert not Path(ready["backing_path"]).exists()
    assert len([a for a in kernel.commands if "mkfs.ext4" in a[0]]) == 1


def test_replaced_backing_never_unlinked_during_retirement(retired_host: RetiredHost) -> None:
    host, _, req, ready = retired_host
    path = Path(ready["backing_path"])
    retained = path.with_suffix(".retained")
    path.rename(retained)
    path.write_text("unrelated replacement")
    with pytest.raises(StorageError):
        host.retire(req)
    assert path.read_text() == "unrelated replacement"
    assert retained.exists()


def test_legacy_setup_allows_only_explicit_additive_root_retirement_config(
    retired_host: RetiredHost,
) -> None:
    host, _, _, _ = retired_host
    setup = Path(host.config.state_base) / "setup.json"
    original = json.loads(setup.read_text())
    for key in ("kubeconfig_path", "kubectl_path", "cluster_uid"):
        original["config"].pop(key, None)
    setup.write_text(json.dumps(original))
    with pytest.raises(StorageError):
        host.restore_mounts()
    host.setup()
    upgraded = json.loads(setup.read_text())
    assert upgraded["config"] == asdict(host.config)
    assert upgraded["base_device"] == original["base_device"]
    assert upgraded["created_at_ns"] == original["created_at_ns"]
    changed = StorageHost(
        replace(host.config, cluster_uid=str(uuid4())),
        host.ops,
        trusted_uid=os.getuid(),
        check_ancestors=False,
    )
    with pytest.raises(StorageError):
        changed.setup()


@pytest.mark.parametrize(
    "unsafe", ["http", "insecure", "exec", "token_file", "external_ca", "mode"]
)
def test_static_kube_configuration_rejects_unverified_transport_or_dynamic_auth(
    retired_host: RetiredHost, unsafe: str
) -> None:
    host, _, req, ready = retired_host
    assert host.config.kubeconfig_path is not None
    path = Path(host.config.kubeconfig_path)
    data = json.loads(path.read_text())
    cluster = data["clusters"][0]["cluster"]
    user = data["users"][0]["user"]
    if unsafe == "http":
        cluster["server"] = "http://127.0.0.1:6443"
    elif unsafe == "insecure":
        cluster["insecure-skip-tls-verify"] = True
    elif unsafe == "exec":
        user["exec"] = {"command": "whoami"}
    elif unsafe == "token_file":
        user["tokenFile"] = "/other/file"
    elif unsafe == "external_ca":
        cluster["certificate-authority"] = "/other/ca.pem"
    else:
        path.chmod(0o644)
    path.write_text(json.dumps(data))
    with pytest.raises(StorageError):
        host.retire(req)
    assert Path(ready["backing_path"]).exists()


def test_retire_protocol_has_no_caller_proof_or_paths(retired_host: RetiredHost) -> None:
    _, _, req, _ = retired_host
    action, decoded = decode_message(
        json.dumps({"version": 1, "action": "retire", "request": req.as_dict()}).encode()
    )
    assert action == "retire"
    assert decoded == req
    data = req.as_dict()
    data["no_attachments"] = True
    with pytest.raises(StorageError):
        decode_message(json.dumps({"version": 1, "action": "retire", "request": data}).encode())


@pytest.fixture
def namespace_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NamespaceAudit:
    from app.runtime_v2 import storage_host

    ops = storage_host.LinuxOps()
    own_ns = tmp_path / "host-namespace"
    own_ns.write_bytes(b"namespace")
    foreign_ns = tmp_path / "foreign-namespace"
    foreign_ns.write_bytes(b"namespace")
    original_stat = os.stat
    host_stat = original_stat(own_ns)

    def fake_stat(
        path: str | bytes | int | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        if str(path) in ("/proc/1/ns/mnt", "/proc/self/ns/mnt"):
            return host_stat
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)
    target = Path("/trusted/mount")
    line = "22 1 7:7 / /trusted/mount rw,nodev,nosuid - ext4 /dev/loop7 rw\n"
    tasks = []
    for index, namespace in enumerate((own_ns, foreign_ns)):
        task = tmp_path / f"process{index}" / "task" / f"thread{index}"
        (task / "ns").mkdir(parents=True)
        (task / "fd").mkdir()
        os.link(namespace, task / "ns/mnt")
        (task / "cwd").symlink_to(tmp_path, target_is_directory=True)
        (task / "root").symlink_to(tmp_path, target_is_directory=True)
        (task / "mountinfo").write_text(line if index == 0 else "")
        fields = ["S", "0", "0", "0", "0", "0", "0"] + ["0"] * 12 + [str(1000 + index)]
        (task / "stat").write_text(f"{100 + index} (live process) " + " ".join(fields) + "\n")
        tasks.append(task)
    monkeypatch.setattr(ops, "_task_inventory", lambda: tasks)
    monkeypatch.setattr(
        ops,
        "_mounts",
        lambda: [{"target": "/proc", "fstype": "proc", "options": ["rw"], "super_options": ["rw"]}],
    )
    backing_file = tmp_path / "image"
    backing_file.write_bytes(b"owned")
    st = backing_file.stat()
    return (
        ops,
        tasks,
        target,
        ops._parse_mounts(line)[0],
        {"device": st.st_dev, "inode": st.st_ino},
        backing_file,
    )


def test_namespace_audit_accepts_only_unique_expected_host_mount(
    namespace_audit: NamespaceAudit,
) -> None:
    ops, _, target, expected, backing, _ = namespace_audit
    proof = ops.mount_references("7:7", target, expected, backing, None)
    assert proof["namespace_count"] == 2
    assert proof["task_count"] == 2


@pytest.mark.parametrize(
    "unsafe",
    ["foreign_bind", "host_bind", "backing_fd", "unreadable", "process_churn", "hidden_submount"],
)
def test_namespace_audit_refuses_each_live_or_unknown_reference(
    namespace_audit: NamespaceAudit, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    ops, tasks, target, expected, backing, image = namespace_audit
    if unsafe == "foreign_bind":
        (tasks[1] / "mountinfo").write_text("44 1 7:7 /data /workspace rw - ext4 /dev/loop7 rw\n")
    elif unsafe == "host_bind":
        with (tasks[0] / "mountinfo").open("a") as stream:
            stream.write("44 1 7:7 /data /other rw - ext4 /dev/loop7 rw\n")
    elif unsafe == "hidden_submount":
        with (tasks[0] / "mountinfo").open("a") as stream:
            stream.write("44 22 8:8 / /trusted/mount/hidden rw - ext4 /dev/sdb rw\n")
    elif unsafe == "backing_fd":
        (tasks[1] / "fd/3").symlink_to(image)
    elif unsafe == "unreadable":
        (tasks[1] / "mountinfo").unlink()
    else:
        snapshots: Iterator[list[Path]] = iter((tasks, []))
        monkeypatch.setattr(ops, "_task_inventory", lambda: next(snapshots))
    with pytest.raises(StorageError):
        ops.mount_references("7:7", target, expected, backing, None)


def test_kubernetes_cli_output_is_bounded_and_failures_are_not_absence() -> None:
    import sys

    from app.runtime_v2.storage_host import LinuxOps

    for code in ("import sys;sys.exit(1)", "print('x' * (8*1024*1024+1))"):
        with pytest.raises(StorageError):
            LinuxOps()._execute_kube([sys.executable, "-c", code])


def test_kubernetes_cli_timeout_kills_the_query_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from app.runtime_v2 import storage_host

    monkeypatch.setattr(storage_host, "KUBE_TIMEOUT", 0.05)
    with pytest.raises(StorageError, match="timed out"):
        storage_host.LinuxOps()._execute_kube([sys.executable, "-c", "import time;time.sleep(30)"])


def test_unlink_to_tombstone_crash_resumes_only_from_durable_unlink_intent(
    retired_host: RetiredHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _, req, ready = retired_host
    original_unlink = os.unlink

    def crash(path: str | bytes | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        original_unlink(path, *args, **kwargs)
        if str(path) == Path(ready["backing_path"]).name:
            raise OSError("crash after unlink")

    monkeypatch.setattr(os, "unlink", crash)
    with pytest.raises(StorageError):
        host.retire(req)
    assert not Path(ready["backing_path"]).exists()
    monkeypatch.setattr(os, "unlink", original_unlink)
    assert host.retire(req)["phase"] == "retired"


def test_new_cluster_name_collision_after_unmount_keeps_backing_and_retry_is_fenced(
    retired_host: RetiredHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, kernel, req, ready = retired_host
    query = kernel._execute_kube

    def recreate_after_unmount(argv: list[str]) -> str:
        if not kernel.mounts:
            kernel.objects["pv"] = {"metadata": {"uid": str(uuid4())}}
        return query(argv)

    monkeypatch.setattr(kernel, "_execute_kube", recreate_after_unmount)
    with pytest.raises(StorageError):
        host.retire(req)
    assert Path(ready["backing_path"]).exists()
    assert kernel.loop_rows
    assert not any("--detach" in argv for argv in kernel.commands)
    with pytest.raises(StorageError):
        host.restore_mounts()


def test_no_retirement_bundle_preserves_ready_legacy_volume(retired_host: RetiredHost) -> None:
    host, _, req, ready = retired_host
    legacy = replace(host.config, kubeconfig_path=None, kubectl_path=None, cluster_uid=None)
    setup = Path(host.config.state_base) / "setup.json"
    evidence = json.loads(setup.read_text())
    evidence["config"] = asdict(legacy)
    setup.write_text(json.dumps(evidence))
    legacy_host = StorageHost(legacy, host.ops, trusted_uid=os.getuid(), check_ancestors=False)
    with pytest.raises(StorageError, match="not configured"):
        legacy_host.retire(req)
    assert legacy_host.inspect(req)["phase"] == "ready"
    assert Path(ready["backing_path"]).exists()


def test_util_linux238_padded_backing_major_minor_matches_numeric_device(
    retired_host: RetiredHost,
) -> None:
    host, kernel, _, _ = retired_host
    image = Path("/trusted/owned.img")
    kernel.loop_rows = {
        "/dev/loop0": {
            "name": "/dev/loop0",
            "back-file": str(image),
            "back-ino": 715087,
            "back-maj:min": "       8:17 ",
            "offset": 0,
            "sizelimit": 0,
            "ro": False,
            "autoclear": False,
        }
    }
    assert host._loop(image, {"device": 2065, "inode": 715087}) == "/dev/loop0"


@pytest.mark.parametrize("wrong", ["       8:18 ", "8:17 extra", "8 :17", "", None])
def test_padded_device_fix_preserves_strict_wrong_device_rejection(
    retired_host: RetiredHost, wrong: str | None
) -> None:
    host, kernel, _, _ = retired_host
    image = Path("/trusted/owned.img")
    kernel.loop_rows = {
        "/dev/loop0": {
            "name": "/dev/loop0",
            "back-file": str(image),
            "back-ino": 715087,
            "back-maj:min": wrong,
            "offset": 0,
            "sizelimit": 0,
            "ro": False,
            "autoclear": False,
        }
    }
    with pytest.raises(StorageError):
        host._loop(image, {"device": 2065, "inode": 715087})


def _nonuserspace_task(tasks: list[Path], state: str, flags: int) -> Path:
    task = tasks[1]
    (task / "ns/mnt").unlink()
    (task / "cwd").unlink()
    (task / "root").unlink()
    fields = [state, "0", "0", "0", "0", "0", str(flags)] + ["0"] * 12 + ["1234"]
    (task / "stat").write_text("2 (kernel worker) " + " ".join(fields) + "\n")
    return task


@pytest.mark.parametrize("state,flags", [("S", 0x00200000), ("Z", 0)])
def test_proven_kernel_thread_or_zombie_without_userspace_refs_is_not_unknown(
    namespace_audit: NamespaceAudit, state: str, flags: int
) -> None:
    ops, tasks, target, expected, backing, _ = namespace_audit
    _nonuserspace_task(tasks, state, flags)
    proof = ops.mount_references("7:7", target, expected, backing, None)
    assert proof["nonuserspace_tasks"] == 1


@pytest.mark.parametrize("unsafe", ["live", "open_fd", "cwd_present", "malformed_stat"])
def test_missing_namespace_is_never_a_blanket_process_exemption(
    namespace_audit: NamespaceAudit, unsafe: str
) -> None:
    ops, tasks, target, expected, backing, image = namespace_audit
    task = _nonuserspace_task(tasks, "S", 0 if unsafe == "live" else 0x00200000)
    if unsafe == "open_fd":
        (task / "fd/4").symlink_to(image)
    elif unsafe == "cwd_present":
        (task / "cwd").symlink_to(image.parent, target_is_directory=True)
    elif unsafe == "malformed_stat":
        (task / "stat").write_text("not proc stat")
    with pytest.raises(StorageError):
        ops.mount_references("7:7", target, expected, backing, None)


@pytest.mark.parametrize(
    "changed,when",
    [
        ("namespace", "mountinfo"),
        ("starttime", "mountinfo"),
        ("namespace", "end"),
        ("starttime", "end"),
    ],
)
def test_same_task_paths_never_hide_namespace_or_pid_identity_changes(
    namespace_audit: NamespaceAudit, monkeypatch: pytest.MonkeyPatch, changed: str, when: str
) -> None:
    ops, tasks, target, expected, backing, _ = namespace_audit
    task = tasks[1]
    replacement = task.parent / "replacement-namespace"
    replacement.write_bytes(b"new namespace")

    def change_identity() -> None:
        if changed == "namespace":
            (task / "ns/mnt").unlink()
            os.link(replacement, task / "ns/mnt")
        else:
            fields = ["S", "0", "0", "0", "0", "0", "0"] + ["0"] * 12 + ["987654"]
            (task / "stat").write_text("101 (new process) " + " ".join(fields) + "\n")

    if when == "mountinfo":
        read_mountinfo = ops._proc_mountinfo

        def transition_while_collecting(path: Path) -> list[dict[str, Any]]:
            if path == task / "mountinfo":
                change_identity()
            return read_mountinfo(path)

        monkeypatch.setattr(ops, "_proc_mountinfo", transition_while_collecting)
    else:
        calls = 0

        def stable_paths_with_changed_identity() -> list[Path]:
            nonlocal calls
            calls += 1
            if calls == 2:
                change_identity()
            return tasks

        monkeypatch.setattr(ops, "_task_inventory", stable_paths_with_changed_identity)
    with pytest.raises(StorageError, match=r"identity|namespace"):
        ops.mount_references("7:7", target, expected, backing, None)


@pytest.mark.parametrize("failure", ["snapshot", "fstat"])
def test_namespace_pin_is_closed_if_validation_after_open_raises(
    namespace_audit: NamespaceAudit, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import errno
    import fcntl
    from contextlib import suppress

    ops, _, target, expected, backing, _ = namespace_audit
    opened: list[int] = []
    real_open = os.open
    real_fstat = os.fstat
    read_snapshot = ops._task_snapshot

    def track_namespace_open(
        path: str | bytes | os.PathLike[str], flags: int, *args: Any, **kwargs: Any
    ) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        if str(path).endswith("/ns/mnt"):
            opened.append(fd)
        return fd

    def fail_snapshot_after_open(
        task: Path,
    ) -> tuple[tuple[int, bool, bool], tuple[int, int] | None]:
        if failure == "snapshot" and opened:
            raise FileNotFoundError("task exited after namespace open")
        return read_snapshot(task)

    def fail_namespace_fstat(fd: int) -> os.stat_result:
        if failure == "fstat" and fd in opened:
            raise OSError("namespace identity became unreadable")
        return real_fstat(fd)

    monkeypatch.setattr(os, "open", track_namespace_open)
    monkeypatch.setattr(os, "fstat", fail_namespace_fstat)
    monkeypatch.setattr(ops, "_task_snapshot", fail_snapshot_after_open)
    try:
        with pytest.raises(StorageError):
            ops.mount_references("7:7", target, expected, backing, None)
        assert opened
        for fd in opened:
            with pytest.raises(OSError) as closed:
                fcntl.fcntl(fd, fcntl.F_GETFD)
            assert closed.value.errno == errno.EBADF
    finally:
        # The regression must release the leaked baseline descriptors even while RED.
        for fd in opened:
            with suppress(OSError):
                os.close(fd)


def test_root_inventory_uses_raw_api_instead_of_kubectl_rebuilt_lists(
    retired_host: RetiredHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, kernel, req, _ = retired_host
    kernel.objects["pods"]["metadata"]["resourceVersion"] = "150510"
    kernel.objects["pods"]["items"] = [
        {"metadata": {"name": f"unrelated-{index}", "namespace": "system"}, "spec": {"volumes": []}}
        for index in range(11)
    ]
    kernel.objects["volumeattachments.storage.k8s.io"]["metadata"]["resourceVersion"] = "150510"
    query = kernel._execute_kube
    raw_calls: list[list[str]] = []
    paths = {
        "/api/v1/pods": "pods",
        "/apis/storage.k8s.io/v1/volumeattachments": "volumeattachments.storage.k8s.io",
    }

    def actual_cli_shape(argv: list[str]) -> str:
        command = argv[argv.index("get") + 1 :]
        if command[0] == "--raw":
            assert len(command) == 2
            raw_calls.append(command)
            return json.dumps(kernel.objects[paths[command[1]]])
        if command[0] in ("pods", "volumeattachments.storage.k8s.io"):
            # Observed kubectl1.35.10 rebuilds these lists and erases their API list RV.
            return json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "List",
                    "metadata": {"resourceVersion": ""},
                    "items": kernel.objects[command[0]]["items"],
                }
            )
        return query(argv)

    monkeypatch.setattr(kernel, "_execute_kube", actual_cli_shape)
    audit = kernel.kube_audit(host.config, req, os.getuid(), False)
    assert audit["pods_resource_version"] == "150510"
    assert audit["attachments_resource_version"] == "150510"
    assert raw_calls == [
        ["--raw", "/api/v1/pods"],
        ["--raw", "/apis/storage.k8s.io/v1/volumeattachments"],
    ]


@pytest.mark.parametrize("resource", ["pods", "volumeattachments.storage.k8s.io"])
@pytest.mark.parametrize("damage", ["generic_kind", "empty_rv", "non_string_rv", "continue"])
def test_raw_inventory_keeps_strict_kind_version_and_completeness_checks(
    retired_host: RetiredHost, resource: str, damage: str
) -> None:
    host, kernel, req, ready = retired_host
    inventory = kernel.objects[resource]
    if damage == "generic_kind":
        inventory["kind"] = "List"
    elif damage == "empty_rv":
        inventory["metadata"]["resourceVersion"] = ""
    elif damage == "non_string_rv":
        inventory["metadata"]["resourceVersion"] = 150510
    else:
        inventory["metadata"]["continue"] = "unread-page"
    with pytest.raises(StorageError, match="incomplete or malformed"):
        kernel.kube_audit(host.config, req, os.getuid(), False)
    raw_commands = [argv[argv.index("get") + 1 :] for argv in kernel.kube_calls if "--raw" in argv]
    assert raw_commands == [
        ["--raw", "/api/v1/pods"],
        ["--raw", "/apis/storage.k8s.io/v1/volumeattachments"],
    ]
    assert Path(ready["backing_path"]).exists()
