# Dedicated local ext4 storage host

This opt-in operator-local profile creates one fully preallocated fixed ext4
filesystem per allocation/generation, on one static node. It is not a CSI driver,
cluster administrator or migration tool. Retirement only follows independent current
cluster and host reference proof. Existing Docker
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
Every core key is required; unknown keys are rejected. The optional
`kubeconfig_path`, `kubectl_path`, `cluster_uid` retirement keys default to null;
retirement requires all three together. Legacy config/setup/ready receipts remain
compatible. Run operator `setup` explicitly to bind this new bundle to an existing
setup; core node/base/filesystem identity must remain unchanged. An already bound
retirement bundle cannot be silently replaced or removed. Use the actual numeric local
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
`/usr/sbin/mkfs.ext4`, `/usr/sbin/blkid`, `/usr/bin/mount`, `/usr/bin/umount`. A changed config or backing
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

The fixed actions are `provision`, read-only `inspect`, and `retire`. No
client-supplied cleanup path, attachment boolean, executable or option is accepted. UUIDs are canonical lowercase strings, generation
is an integer >=1, and nominal_gib is an integer 1..1000 (booleans are rejected).
The controller is the trusted authorization boundary for workspace and cluster
ownership; the hook independently queries Kubernetes read-only for retirement and
does not trust tenant PV annotations.
The same strict request object works on stdin with the operator `provision`,
`inspect`, and `retire` CLI modes. `setup`, `restore-mounts`, and `serve` are root-only CLI modes.

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
Unknown socket artifacts remain untouched. Older util-linux may pad the JSON
BACK-MAJ:MIN string; the hook trims ASCII whitespace and compares strictly parsed
major/minor integers, while wrong devices and malformed strings still refuse.
A bind-to-receipt crash gap requires
operator reconciliation; it fails closed.

This is an operator-local mount invariant. An administrator can unmount a volume
while kubelet runs; systemd ordering does not automatically fence that mount loss,
and existing Pod bind mounts may retain the old filesystem. Do not administer
mounts outside the hook while workloads are eligible. Independent automatic
NodePublish/mount-loss fencing needs a real volume plugin/CSI implementation.
A changed node/backing filesystem or lost receipt is an operator quarantine event.

## Independently verified retirement

The controller must first verify its immutable binding and absence of workload and
helper references, delete only its exact current PVC/PV using UID/resourceVersion
preconditions, and observe their absence. It then sends the **same original request
identity**, with `operation_id` set to its current durable delete/archive operation,
using `action:"retire"`. Root derives the sole expected PV name as
`pv-<volume_id>`. Neither a client success flag nor PVC absence alone authorizes erase.
Snapshot/archive validation must already have succeeded before this destructive
stage; the hook does not create or validate encrypted archives.

Set all three retirement configuration values in the root-owned config:

- `kubeconfig_path`: fixed root-owned0600 **flattened JSON** kubeconfig. Exactly one
  HTTPS cluster with embedded CA data, one selected context and one user with an
  embedded certificate/key or static bearer token. No insecure-skip-TLS, proxy,
  external CA/key/token files, exec/auth-provider plugins, impersonation or dynamic
  options are accepted. The CA must parse as a real PEM trust anchor. Obtain the
  JSON with the already verified node kubectl `config view --raw --flatten -o json`
  and install it without printing credential contents into logs.
- `kubectl_path`: trusted fixed absolute root-owned executable/wrapper under trusted
  ancestors. A wrapper may `exec` the verified installed `k3s kubectl` with no dynamic
  options of its own. No caller chooses this executable. Requests pass only fixed
  read-only `get` operations and generated/validated names, explicit kubeconfig,
  request timeout and root-private cache directory.
- `cluster_uid`: expected **kube-system namespace UID** from this cluster. This binds
  a cluster audit separately from node name, Node UID and normalized machine hash.

The Kubernetes credential should have only get on the configured Node, kube-system
namespace, named PVC/PV, plus cluster-wide list on Pods and VolumeAttachments. The
hook performs no Kubernetes mutations. Kubernetes CLI stdout is bounded to8MiB,
stderr16KiB, request timeout10s and subprocess timeout20s; timeout, partial output,
failed authorization, malformed JSON or incomplete/paginated inventories refuse
retirement. TLS and static credential structure are revalidated on each audit.

Before destructive phases, root independently verifies cluster UID, current Node
UID/machineID, exact PVC and `pv-<volume_id>` name absence (replacement UID/name
collision blocks), all-namespace Pod/helper PVC references, overlapping hostPath
volumes and VolumeAttachment references. Named absence and cluster/node identity
are re-read after inventory collection. The controller must fence concurrent
lifecycle work and prevent untrusted users from creating arbitrary PVs/hostPaths;
these sequential Kubernetes reads cannot make administrator-driven resource
recreation atomic with a host unmount. The hook rechecks them at each boundary.

Root also inventories **all process threads' mount namespaces**, pins each unique
namespace during the sample, reads mountinfo, and verifies that only the exact owned
mount appears in the host namespace. Any other namespace copy, host bind, nested
mount, process cwd/root, open filesystem/device descriptor or another open backing
inode descriptor refuses. It reads host `/proc` directly, uses no setns, lazy/force
unmount or unchecked caller reference inventory. Kernel threads or zombies without a userspace namespace are exempted only after
Linux PF_KTHREAD/state-Z proof, empty descriptor inventory, absent cwd/root, and a
stable start-time/type recheck. A missing live userspace namespace is never an
exemption. Hidden/unreadable namespaces, missing `/proc` visibility, excessive
inventory, or task churn fail closed. A host
service with a private mount namespace is rejected. The unit adds CAP_SYS_PTRACE
for read-only proc evidence across UIDs and AF_INET/AF_INET6 for the configured TLS
API queries; the local request server remains AF_UNIX only. The existing lifecycle
controller's sandbox remains unchanged. Operator-owned processes holding copied
mount namespaces may need to be stopped before retirement can prove no references.

The serialized durable phases are `retire_verified`, `unmounting`, `unmounted`,
`detaching`, `detached`, `unlinking`, `retired`. Only normal `umount -- <owned target>`
is used. A successful detach command alone is insufficient: root must observe that
the loop no longer references the exact backing inode. Original inode, linkcount,
owner, fixed size, allocated blocks and ext4 UUID are checked before removal. The
image is held open and unlinked fd-relative only after independent absence and
reference proof; no other inode or path is removed. Root then proves absence,
closes its pinned inode and observes parent free space. It leaves the empty
root-owned0700 mount directory and a permanent0600 retired tombstone. Original
backing/data evidence stays historical; `retirement.released_backing_bytes` records
release and `observed_reclaimed_bytes` reports the measured nonnegative parent-free
delta, which may be reduced by concurrent host writers. Inner statvfs usage is not
reclaimed host reservation.

A successful receipt preserves the original provision `operation_id` and all
immutable identity fields, with `phase:"retired"`, `retirement_operation_id` matching
the delete request, `retired_at_ns`, and `retirement` containing fresh cluster/ref
proofs, loop/mount identity and free-space observations. Same-operation retries
return the exact tombstone after a new cluster/name-absence check. Another delete
operation ID is refused for an incomplete or completed retirement. Persist the
operation ID across retries. A retired allocation/generation can never be
reformatted or provisioned again; restoration needs a new allocation/generation.

Interrupted phases retain all still-existing bytes and block new allocations and
boot admission. Never mark deletion successful merely because PVC/PV disappeared.
A repeated `retire` for the exact immutable identity and operation can recover
pre/post-unmount, detach, and unlink crash gaps only after fresh independent proof.
`restore-mounts` skips validated completed tombstones but fails on unfinished
retirement; it does not remount an uncovered retiring directory as usable data.
If the daemon cannot start because of that boot fence, use the trusted operator
CLI `retire` to finish reconciliation while the verified cluster API is reachable,
then re-run restore and start the service. A single control-plane node whose API
cannot start behind the same fence needs a separately reviewed operator recovery
plan or independently reachable control-plane endpoint. Do not bypass the fence
or start kubelet onto uncovered local-PV paths to obtain that API.
