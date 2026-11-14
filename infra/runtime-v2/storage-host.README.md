# Dedicated local ext4 storage host

This opt-in operator-local profile creates one fully preallocated fixed ext4
filesystem per allocation/generation, on one static node. It is not a CSI driver,
cluster administrator, migration tool, or deletion implementation. Existing Docker
volumes, local PVs and other files are never adopted, formatted, detached or erased.
Deployment and real write-until-ENOSPC validation remain operator acceptance work.

## Operator installation

Use only the approved dedicated nonproduction nodes. Record the current node name,
Kubernetes Node UID and SHA-256 of ASCII `machine-id:` followed by the
stripped lowercase 32-hex `/etc/machine-id` (same identity as NodeTelemetry). Inspect
existing PV paths, backing filesystem, tool versions and free space first. The
controller must already have captured the exact new PVC UID before provision.
Choose new dedicated bases outside every old PV path; do not reuse sample paths
without checking the actual host inventory.

The file named `storage-host.env.example` is **strict JSON**, not a shell environment
file. Install its edited contents at `/etc/workspace/runtime-v2/storage-host.json`,
root-owned mode 0600, with root-owned ancestors lacking group/other write access.
Every key is required; unknown keys are rejected. Use the actual numeric local
controller UID. Bases must be disjoint absolute paths with existing trusted parents.
`setup` creates the backing/mount/state bases root-owned0700 and socket parent
root-owned0711. The socket itself is mode0600 and owned by the allowed controller
UID; Linux SO_PEERCRED still checks that UID for every accepted connection.

`emergency_bytes` and `scratch_bytes` must each be at least 1 GiB. Select meaningful
host emergency and image/snapshot scratch reservations from measured headroom.
Allocation checks their sum plus the entire requested image against fresh parent
filesystem available bytes while holding a per-node flock. Other host writers may
still consume space; fallocate failure retains an incomplete receipt and bytes.
Do not reinterpret free space as authorization to consume the host.

The module is stdlib-only and supports a standalone host Python 3.9+ profile;
copy the reviewed file root-owned0644 to `/opt/workspace/runtime-storage/storage_host.py`
under trusted root-owned ancestors. Do not upgrade system Python or install API
dependencies on storage nodes. Run with the host interpreter:

```sh
/usr/bin/python3 /opt/workspace/runtime-storage/storage_host.py setup --config /etc/workspace/runtime-v2/storage-host.json
/usr/bin/python3 /opt/workspace/runtime-storage/storage_host.py restore-mounts --config /etc/workspace/runtime-v2/storage-host.json
```

Setup refuses nonempty uninitialized bases and stores the immutable configuration,
node/machine identity, backing filesystem device/mount evidence and executable
versions. It requires trusted root-owned tools at `/usr/sbin/losetup`,
`/usr/sbin/mkfs.ext4`, `/usr/sbin/blkid`, `/usr/bin/mount`. A changed config or backing
filesystem fails closed. Do not delete setup state to bypass a refusal.

Verify the actual node's installed tool behavior before enablement. Official
[e2fsprogs v1.47.2 mke2fs manual](https://github.com/tytso/e2fsprogs/blob/v1.47.2/misc/mke2fs.8.in)
documents `nodiscard`, `lazy_itable_init=0`, `lazy_journal_init=0`, `-m 0` and `-U`.
[util-linux v2.41.3 losetup source](https://github.com/util-linux/util-linux/blob/v2.41.3/sys-utils/losetup.c)
confirms JSON number/boolean types and selected backing identity columns. These
references establish flag semantics, not that a particular node passed acceptance.
Parent read-only inventory reports A: Python3.11.2/mke2fs1.47.0/util-linux2.38.1;
B: Python3.9.2/mke2fs1.46.2/util-linux2.36.1. Verify flags on those installed
versions before mutation. The development machine has util-linux 2.41.5 and no
mkfs.ext4; unit tests simulate
that privileged boundary and do not prove real disk quotas.

Install `workspace-runtime-storage.service` into systemd, adjusting its installation
paths and `RequiresMountsFor` to the reviewed fixed operator config. **Enable** it
before starting the node service: `[Install] RequiredBy=` creates required dependency
links from k3s/k3s-agent/kubelet. `Before=` alone is insufficient. Verify the actual
node service has both a `Requires` dependency on this unit and an `After` dependency
on it, and that no alternate kubelet startup path bypasses them. Add the equivalent
explicit Requires/After drop-in to the actual distribution service if enablement
does not create those dependencies. Test failed restore blocking node startup on
dedicated resources before permitting workload admission. Do not reboot unrelated
workloads as part of this task.

The root storage unit must share the host mount namespace so kubelet sees the
mounts. Filesystem sandbox directives such as ProtectSystem/PrivateDevices imply a
private mount namespace and must not be copied from the unprivileged controller.
The controller unit itself keeps its current sandbox. Workload Pods receive only
the PVC, never privileged mode, loop devices, the hook socket, or host mount access.

## Fixed protocol and receipts

One newline-terminated UTF-8 JSON request per AF_UNIX stream connection, at most
65,536 bytes including framing; no HTTP/TCP, shell, paths, options or chosen device:

```json
{"version":1,"action":"provision","request":{"workspace_id":"11111111-1111-4111-8111-111111111111","instance_id":"22222222-2222-4222-8222-222222222222","environment_id":"33333333-3333-4333-8333-333333333333","allocation_id":"44444444-4444-4444-8444-444444444444","generation":1,"pvc_namespace":"workspace-runtime","pvc_name":"volume-test","pvc_uid":"55555555-5555-4555-8555-555555555555","node_name":"node-a","node_uid":"66666666-6666-4666-8666-666666666666","nominal_gib":1,"operation_id":"77777777-7777-4777-8777-777777777777"}}
```

Only `provision` and read-only `inspect` actions exist. No client-supplied cleanup or
attachment boolean is accepted. UUIDs are canonical lowercase strings, generation
is an integer >=1, and nominal_gib is an integer 1..1000 (booleans are rejected).
The controller is the trusted authorization boundary for workspace and cluster
ownership; the hook does not contact Kubernetes or trust tenant PV annotations.
The same strict request object works on stdin with the operator `provision` and
`inspect` CLI modes. `setup`, `restore-mounts`, and `serve` are root-only CLI modes.

Success: `{"version":1,"ok":true,"receipt":{...}}`. Refusal/unknown:
`{"version":1,"ok":false,"error":"storage_refused_or_unknown"}`. A timeout or
refusal never means resources were released. Retry an identical allocation and
inspect the receipt; never invent a new allocation to work around an unknown result.
Different operation IDs may inspect/retry the same immutable allocation identity.

The receipt has profile `workspace-loop-ext4-v1`, deterministic `volume_id` (UUIDv5
of allocation+generation), `identity` containing every request field except
operation_id, original `operation_id`, machine hash, generated `filesystem_uuid`,
`backing_path`, `mount_path`, **`data_path`**, phase and nanosecond timestamps.
`backing` records parent device/inode, fixed size and actually allocated host bytes.
`data` records the fixed child directory inode, current filesystem device and owner.
`live` records mountinfo identity/options, loop device, sample time,
`host_reserved_bytes`, measured `usable_bytes`, `available_bytes`, and `used_bytes`.
Loop minor/mount ID/data device may change after reboot; backing device/inode,
filesystem UUID and data-directory inode must still match. Root journals are mode
0600 with file and directory fsync and per-node serialization.

**Create the local PV with `local.path=receipt.data_path`, not mount_path.** The
filesystem root remains root-owned0700, including ext4 lost+found; only the new
`data` child is owned by UID/GID1000. The data child is opened fd-relative with
O_NOFOLLOW and must remain on the verified filesystem with its recorded inode.
The PV must separately bind the exact immutable PVC UID, node UID/affinity and
allocation identity, RWO/Filesystem/Retain, then be observed Bound before Pods start.
The hook cannot prove those Kubernetes conditions; controller integration owns them.

The nominal cap is backing length, not guaranteed user-usable bytes: ext4 metadata
reduces measured usable capacity. Fully allocated host blocks are separate from
inner filesystem usage. Stop retains mounted data and the full reservation; inspect
works without a running Pod and restore inventories all retained receipts even if
a PVC no longer exists. Do not count inner used bytes as backing space reclaimed.

## Failure and scope limits

Only a freshly O_EXCL/O_NOFOLLOW-created known inode is formatted. Formatting uses
explicit nodiscard and disables lazy initialization; allocated blocks are rechecked
and holes refilled only during that original owned provision attempt. A ready volume
that later becomes sparse is refused, never silently repaired. Mounts require ext4,
exact fsUUID/loop inode/device/offset, rw,nodev,nosuid, no discard, and fresh statvfs.
Do not run fstrim, hole punching, or discard remounts against these filesystems.

Incomplete phases, unknown files, malformed ownership evidence, missing backing,
symlinks/hardlinks, changed identity or missing mount preserve bytes and block ready.
Boot restoration refuses unknown/missing artifacts and any incomplete phase before
kubelet admission. It never reformats, repairs a filesystem, removes data, detaches a
loop or unmounts. A known stale Unix socket alone can be replaced only under an
exclusive server lock, after exact durable socket inode proof and ECONNREFUSED.
Unknown socket artifacts remain untouched. A bind-to-receipt crash gap requires
operator reconciliation; it fails closed.

This is an operator-local mount invariant. An administrator can unmount a volume
while kubelet runs; systemd ordering does not automatically fence that mount loss,
and existing Pod bind mounts may retain the old filesystem. Do not administer
mounts outside the hook while workloads are eligible. Independent automatic
NodePublish/mount-loss fencing needs a real volume plugin/CSI implementation.
A changed node/backing filesystem or lost receipt is an operator quarantine event.

Deletion is intentionally unsupported here. A later trusted cleanup stage must
prove current cluster identity, exact current PVC/PV UIDs and generation, and no
workload/helper/attachment references, then perform UID-preconditioned Kubernetes
cleanup and controlled mount/loop/inode removal plus observed reclaimed blocks.
PVC absence, Retain PV deletion, or an unchecked `no_attachments:true` request is
never authority to erase retained data. Snapshot/archive validation must precede
any destructive finalization. Until that integration exists, reservations remain.
