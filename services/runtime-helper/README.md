# Trusted runtime helper protocol v1

This Linux amd64, static, stdlib-only executable supplies target-side execution,
file, secret and archive primitives. It is an independent foundation: it does
not establish that the controller, runtime profile, image distribution, storage
quota, or live gVisor deployment is supported or accepted.

## Delivery and trust boundary

Build with an operator-verified local compiler; no download or builder image is
used by the build:

```sh
./build.sh /absolute/path/to/verified/go
file .build/helper
readelf -l .build/helper
# Build context: services/runtime-helper
# Build, distribute and pin the final image by its actual registry/CRI digest.
docker build -t operator-runtime-helper:review .
```

The required compiler is `go version go1.27.4 linux/amd64`. The official
`go1.27.4.linux-amd64.tar.gz` archive was verified against the downloaded
`https://go.dev/dl/?mode=json` metadata and has SHA-256
`7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840`.
The script sets `CGO_ENABLED=0`, Linux/amd64, disables network/toolchain downloads,
clears build-affecting Go flags, and builds with `-trimpath`, `-buildvcs=false`,
`-ldflags='-s -w -buildid='`. Only ignored `.build/helper` is produced. Do not
commit the binary, use a floating image tag in deployment, or confuse the binary
hash with the image manifest digest. Image build/distribution and real gVisor
acceptance are separate operator work.

The scratch image runs as UID/GID `1000:1000`. Its default command is
`/helper install /runtime/helper`. Mount a writable, platform-owned emptyDir at
`/runtime` in the init container, with fsGroup 1000. Installation copies the
running executable via `/proc/self/exe` to an exclusive temporary file, fsyncs,
and atomically installs `/runtime/helper` with mode 0555. Every other destination
is rejected. Mount that emptyDir **read-only** at a reserved path such as
`/runtime` in the environment container. Invoke that exact absolute helper path;
never discover it through tenant PATH or `/workspace`. The running workload
must never have a writable mount of the helper executable.

For volume-only helper Pods, set `command: ["/helper", "hold"]`. This mode waits
for SIGTERM/SIGINT, then exits 0; it performs no file, stdin, network, or operation
processing. Once the controller has verified this Pod's actual UID and bindings,
it can exec `/helper run ...` with the fence below. `hold` accepts no arguments.

Runtime prerequisites:

- A platform-owned, read-only Downward API mount at
  `/run/workspace-identity` containing `uid`, `allocation`, `generation`.
  Kubelet's normal projected-file symlinks are supported. Tenants must not control
  or update this mount or its identity labels. Keep allocation/generation fixed
  throughout a Pod lifetime; create a new Pod for a new generation.
- The verified managed volume at `/workspace`. The controller must independently
  bind workspace/host/environment/allocation/generation and PVC/PV/Pod UIDs,
  node affinity and operation journal. The helper sees only its local mount.
- Linux procfs at `/proc`. FD-pinned regular files and execution cwd use
  `/proc/self/fd`; no tenant executable/interpreter is used to implement helper
  operations. User commands can use the environment's own binaries.
- A writable private `/run/secrets` mount owned by UID 1000 for injection.
  File modes are 0400. Mount read-only for consuming containers where lifecycle
  permits. No Kubernetes API credentials or service-account token are needed.
- Writable staging at fixed `/tmp` for restore. This holds an **unlinked disk
  spool up to the full declared stream size**. Provision and account for staging
  storage plus destination storage using an independently bounded, reserved scratch
  volume. An unbounded emptyDir is not a hard disk quota. A small memory-backed tmpfs is unsuitable
  for large restores. No production path is configurable by request or env.

No network, hostPath, privileged mode, capabilities, host network, Docker socket,
root user, or external Go dependencies are needed. Existing user `"user"`,
`"1000"`, `"1000:1000"`, or omitted user means the admitted nonroot profile;
`"root"` and every other user are rejected. The controller must actually run the
container with UID 1000; the helper does not switch identities or sandbox exec.

## Invocation and framing

```text
/runtime/helper run --uid POD_UUID --allocation ALLOCATION_UUID \
  --generation POSITIVE_INTEGER --operation COMMAND_UUID
```

The argv contains only nonsecret identity values. Pod UID and operation must be
canonical UUID-shaped strings. Allocation accepts canonical hyphenated UUID or
32 hex digits, comparing case-insensitively after normalization, so an allocation
Downward API label containing `UUID.hex` matches a controller's canonical UUID.
Generation is a positive canonical decimal uint64 (no sign/leading zero).

All three projected identities must match before any request input is consumed.
The helper rechecks after non-stream request input and after restore validation
before target mutation. Cross-Pod/identity replacement requires the controller's
immutable Pod labels and UID-bound lifecycle; projected fields cannot provide an
atomic transaction against arbitrary concurrent control-plane label changes.
The operation UUID is a binding value, **not a local idempotency receipt**. There
is no tenant-writable completion journal and no automatic retry/recovery/replay.
The controller owns durable started/completed/unknown state and at-most-once
execution. Never infer completion from files authored under `/workspace`.

Every request starts with one UTF-8 JSON object terminated by newline. Header
including newline is at most 32 MiB. Unknown fields, duplicate keys, nesting over
64, invalid base64, and trailing JSON/data are rejected. Close stdin after the
header for every operation except restore. Restore appends exactly its declared
binary tar byte count and then EOF. No command, environment, file data, or secret
belongs in argv or diagnostic logs.

Success: exit 0, stdout is exactly one direct result JSON object plus newline;
snapshot instead emits one raw tar stream. Rejection/failure: exit 1 and generic
stderr `runtime helper: operation rejected`, without values or paths. There is
no promised JSON error body. Failed snapshot stdout may be partial: discard it.
An interrupted/lost transport is **unknown**, never proof of no side effects.

## Operations and compatible results

All examples below are one JSON header line. Optional user is as above.

| Kind | Request fields | Success stdout |
| --- | --- | --- |
| `exec` | `cmd: [str]`, `timeout: seconds` (default 600), `workdir: relative` (default root), `exec_env: {key: value}` | `{exit_code,output_b64,truncated,timed_out,seconds,started_at}` |
| `setup` | Same as exec; **one step** per invocation; default/max timeout 1800 | Same exec result; 64 KiB output cap |
| `put_files` | `files: [{path,b64}]` | `{size}` aggregate decoded bytes |
| `get_files` | `path`, optional positive `limit` at most 20 MiB | `{b64,size}` |
| `list_files` | `path` (default root) | `{entries:[{path,kind:"file"\|"dir",size}]}` |
| `inject_secrets` | `secrets: {validated_name: value}` | `{}` |
| `snapshot` | `paths: [relative]` (default `["."]`), `max_bytes`, `max_files` | Raw tar; no JSON |
| `restore` | `stream_bytes`, `max_bytes`, `max_files`, followed by raw tar | `{}` |

Execution merges stdout/stderr and drains beyond the cap without retaining it.
Outputs are base64 encoded; `started_at` is UTC RFC3339, `seconds` elapsed real
time. Nonzero user command exits are successful helper protocol responses with
that exit code. Startup/protocol failures reject. Max timeout is 7200 seconds;
normal output cap is 1 MiB. Commands have at most 1000 argv strings / 100000 total
bytes, no NUL. At most 50 explicit environment entries, keys at most 128 bytes
and values at most 4096 bytes. Env merges with the admitted container environment;
requested PATH controls user command lookup. Relative cwd uses a pinned directory
FD, rejecting traversal/symlinks. Setup orchestration and step order belong to
the controller; a lost step response must never rerun setup.

Each command runs in a new process group. On timeout the helper SIGKILLs the
group, waits/reaps the leader, and kills remaining group members. It also cleans
remaining group members after normal leader exit and bounds pipe draining.
**User code can call setsid and escape a process group.** A timeout is not proof
that the entire environment is stopped. To preserve Docker timeout semantics,
the controller must stop the whole workload and verify its exact Pod UID is gone
before marking stopped or releasing compute. If termination or transport is
uncertain, retain unknown and quota; never replay exec/setup.

Files are bounded to 20 MiB aggregate decoded put data / single get data. Put
accepts at most 1000 files and rejects duplicate normalized paths. List walks at
most depth 3, returning at most 1000 entries, without first collecting an
unbounded directory. Returned paths are relative to workspace root. Links and
special files reject instead of reporting/following unsafe kinds.

All tenant paths reject absolute paths, `..` components, NUL, empty components,
and symlinks in any traversed component. File inodes are opened with Linux
`O_PATH|O_NOFOLLOW`, checked regular, and reopened through their pinned proc FD;
FIFOs/devices are never opened for I/O. Directories use `openat` with
`O_DIRECTORY|O_NOFOLLOW`; writes use exclusive same-directory temporaries and
`renameat`, never following a concurrently replaced target symlink. Writes are
atomic **per file**, with mode 0600. There is no operation-wide rollback; disk
errors, interference, or interrupted multi-file operations can leave partial
results and require journal reconciliation. No file-delete operation is invented.

Secret names contain only ASCII letters/digits/`_-.`, at most 128 bytes, excluding
`.`/`..`; at most 1000 secrets / 20 MiB total values. Secret values travel only in
stdin, are atomically written exclusively under `/run/secrets` with mode 0400,
and are never returned/logged or placed in a receipt. Secret response is `{}`.
Actual exec output can contain user-chosen data; the controller must redact
currently authorized secrets before persistence/publication as with Docker.

## Snapshot and restore constraints

`max_bytes` defaults to 5 GiB, minimum 1024, hard maximum 1 TiB. It bounds both
physical tar stream bytes and expanded regular-file bytes. The controller must
supply a value within the actual reserved user disk bound; this is not a disk
quota implementation. `max_files` defaults/hard-limits to 100000 logical nodes,
including the workspace root and implicit parent directories. Total archive
member path metadata is at most 16 MiB; each normalized path at most 4096 bytes.
Stream/tar padding must be aligned to 512 bytes.

Snapshot emits one workspace-rooted tar, preserving requested relative paths,
with a `.` directory header. Nested/duplicate selections are coalesced. Missing
selected paths are omitted; it never follows links/special files. File contents
are streamed and can exceed the file API's 20 MiB bound. Ordinary 0777 permission bits are preserved; setuid/setgid/sticky and foreign
ownership are stripped. Snapshot is consistent only
when the controller has already proven that all workload writers are stopped;
it does not freeze a live filesystem. The parent SnapshotStore manifest uses
one entry with `path="."` for this single stream and handles authenticated
16 MiB parts, immutable manifests, digests and upload finalization itself.

Restore requires a **fresh, empty destination workspace**, verified after stream
validation; a nonempty destination is rejected without overwrite. It first
spools exactly `stream_bytes` to an exclusive file unlinked immediately, checks
EOF, and validates the entire tar before opening/mutating the destination.
Validation rejects links, devices, FIFO, sparse/vendor extensions, traversal,
absolute paths, duplicate normalized members, file/directory hierarchy conflicts,
nonzero trailing material/second archives, truncated streams, oversize expansion,
and excess logical nodes/path metadata. Only portable regular files/directories
are extracted. Ordinary 0777 permissions, including executable bits, are restored;
setuid/setgid/sticky and foreign ownership are stripped. Restrictive directory
permissions are applied in postorder after all children are written. Implicit
parent directories default to 0700. Files are streamed from the validated spool with FD-safe atomic
writes. No memory buffer holds an entire snapshot or regular archive file.

A rejected archive leaves destination data untouched; an I/O failure while
applying a validated archive can leave a partially populated fresh destination.
The controller must retain the old environment/snapshot and clean/reconcile the
new target without re-running exec/setup or blindly replaying writes. Lifecycle
serialization, stable mounts, fresh PVC identity and stopped-workload proof are
controller prerequisites. Tenant same-UID processes must not run during restore;
this is not a protection boundary against ptrace or `/proc` access by a concurrent
process with the same Unix UID.

## Verification

```sh
/path/to/go test ./...
/path/to/go test -race ./...
/path/to/go vet ./...
./build.sh /path/to/verified/go
file .build/helper
readelf -l .build/helper  # no INTERP segment
ldd .build/helper        # expected: not a dynamic executable, exit 1
```

Tests use actual subprocesses, process-group timeout, filesystem links/races,
FIFO, executable copying, bounded outputs/requests, secret permissions, malicious
tars, staging/fresh-target preservation, and a streamed 24 MiB snapshot/restore.
They do not prove live Kubernetes/gVisor, image import/distribution, detached
volume accounting, hard storage quotas, or controller/database at-most-once
behavior. Those remain separate integration/real-machine acceptance work.
