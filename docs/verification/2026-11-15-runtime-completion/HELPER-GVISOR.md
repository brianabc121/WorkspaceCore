# Task3G operator validation report

Verified 2026-11-16T03:48:22.095172+00:00. Required helper delivery, gVisor primitives and writable-path boundaries passed independently on both dedicated nonproduction nodes. Optional snapshot passed; optional restore failed after a partial destination write because this emptyDir profile leaves the workspace root owned by UID 0. This report does not accept the product controller, PVC lifecycle, large scratch reservations, or production runtime.

## Scope and durable identity

Only A `100.72.60.100` / `t0224-node-a` and B `100.121.210.36` / `t0224-node-b` were contacted through `/workspace/.cache/t0224-completion/ssh_probe.py`, which verifies the expected ED25519 host key on each connection. Kubernetes admin commands ran on A only with `/opt/workspace-t0224/bin/k3s kubectl --kubeconfig /etc/workspace-t0224/kubeconfig`; B received only local image/runtime inspection commands. No kubeconfig, credentials, token contents, secret values or archive bytes were printed.

Operation label: `273ff3c5-e3d9-4e64-8f79-b359654715f4`. All names and allocation UUIDs were saved before the first Kubernetes mutation. Local cache `/workspace/.cache/t0224-completion/task3g-dace8baf8c9d4ac6a25e15debdd190a0` is mode 0700 with mode 0600 files. It contains identity.json, original and final probe scripts, image commands, per-phase commands/output, runtime receipts, final receipts.json, and local-verification.json. Every helper call used a separate recorded operation UUID. Requests went through stdin with EOF; argv contained only identities. All archive bytes stayed inside remote Python subprocess byte buffers.

| Pod | Allocation | Generation |
| --- | --- | --- |
| `task3g-273ff3c5e3-a-source` | `9ac3eac4-a938-4216-90c0-9df46dd7358c` | 1 |
| `task3g-273ff3c5e3-a-target` | `1f47e5ca-5af1-4007-a2fd-fe68138bd81e` | 1 |
| `task3g-273ff3c5e3-b-source` | `5fc4e613-f4a2-4942-9ff7-f251318b9e57` | 1 |
| `task3g-273ff3c5e3-b-target` | `924805e4-91cd-471b-9769-c0b2275d1fef` | 1 |

## Image delivery and physical runtime

Local helper binary SHA256 `8f29c9b02225eeee57495b0d61687fd2a9068a17d790248503e5808edf6f369b` and Docker archive SHA256 `6c0bf8adeebc4e05693eb582732ed18692f10834cdfe581b4f2efb2b0a7adbc1` matched the brief before mutation. The same archive was sent as SSH stdin to B's task-owned `/opt/workspace-t0224/completion/task3g-dace8baf8c9d4ac6a25e15debdd190a0/helper.docker.tar`; its remote SHA256 matched. B import used the installed k3s ctr help, namespace `k8s.io`, platform `linux/amd64`, and label `io.cri-containerd.image=managed`.

Both nodes now expose the exact helper reference `docker.io/library/t0224-runtime-helper@sha256:8b67038b8f4e6be3ae118296d74a33a360eb8c524071afb518ba94d3cb3dbc38` in containerd and CRI. This is the actual OCI manifest digest. CRI's helper config/image ID is separately `sha256:75839c2651b758e791cdb768497bf9f32e63cc95f4c5241418fbcafdb13e4354`.

Both nodes already cached `docker.io/library/python@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`; CRI image ID `sha256:a33c10a97f11005523814c6eee013986119fe41f3122f444518117536675c8e1`. Nodes are amd64; actual helper exec reported x86_64. Both images used `imagePullPolicy: Never`, so no registry lookup or floating pull supplied this proof. Kubernetes init status on A reports a preexisting alternate repository name `docker.io/library/import-2026-11-14@sha256:8b67038b8f4e6be3ae118296d74a33a360eb8c524071afb518ba94d3cb3dbc38`; its manifest matches the requested pinned helper ref and installed binary hash.

All four exact Pod sandboxes were inspected through each node's local `/run/k3s/containerd/containerd.sock`: `runtimeHandler=runsc`, `runtimeType=io.containerd.runsc.v1`. The gvisor RuntimeClass handler was also `runsc`. Actual in-container kernel string was `4.19.0-gvisor` on A and B. Runtime and sandbox ID receipts are in `100.72.60.100-runtime.json` and `100.121.210.36-runtime.json`.

## Fixed probe profile

Four Pods total, two per node, in `runner-system`. Each had exact node-name affinity plus the existing protected `workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node` label. All ran with UID/GID/fsGroup1000, RuntimeDefault seccomp, all capabilities dropped, privilege escalation disabled, read-only init/main roots, no host namespaces, no service-account token, and no hostPath. Both containers requested/limited 250m CPU and 256Mi memory. `helper-install` copied `/helper` into its sole writable `/runtime` emptyDir; main mounted the helper volume read-only and held using `/runtime/helper hold`.

Private memory-backed volumes: workspace 16Mi, tmp 64Mi, secrets 32Mi and helper delivery 8Mi. Protected downward identity mounted read-only with Pod UID, allocation and generation. Task labels use `probe.workspace.invalid/*` with a distinct component/operation; no canonical workspace/instance/environment selectors. Each Pod had a deny-all ingress/egress NetworkPolicy matching its task operation and exact task Pod label. The policy configuration was installed; network reachability was not separately probed.

## Results on each node

| Probe | A | B |
| --- | --- | --- |
| Helper exec actual UID/GID/groups | 1000/1000/[1000] | 1000/1000/[1000] |
| Installed helper hash/mode | exact pinned binary /0555 | exact pinned binary /0555 |
| Put/get/list known tiny file | pass, 32 bytes | pass, 32 bytes |
| Incorrect protected UID/allocation/generation | each rejected, exit 1 | each rejected, exit 1 |
| Traversal `../escape` | rejected, exit 1 | rejected, exit 1 |
| Workspace unchanged after rejection probes | only expected tiny.txt | only expected tiny.txt |
| `/workspace` and `/tmp` writes | pass | pass |
| `/var/tmp`, `/runtime`, identity mount writes | errno 30 EROFS | errno 30 EROFS |
| `/tmp` bounded fill | errno 28 ENOSPC at 67,108,864bytes | errno 28 ENOSPC at 67,108,864bytes |
| Available `/tmp` after removing fill file | 67,108,864bytes | 67,108,864bytes |
| Main readiness/restarts before deletion | Running/Ready, 0 | Running/Ready, 0 |
| Tiny snapshot | pass | pass |
| Restore into distinct fresh target | fail after partial write | fail after partial write |

Known content SHA256 `0115fade18ba7545139cf16280baa25942ac29876c5aec29d4d336c83e01a5d9`, size 32. Snapshot tar size 2560, SHA256 `0540fdaaab5298cc414712deb6bcac95ab080532fb5793160f5a2c4858aa1437` on both nodes. No archive stream crossed SSH text decoding. Snapshot/restore requested max_bytes 1 MiB and max_files 16.

The ENOSPC test wrote at most 65 one-MiB chunks to a single private `/tmp` file, stopped on ENOSPC at exactly 64 MiB, and unlinked that file in a finally block. All Pods remained Running/Ready with zero restarts and empty lastState before deletion; this is a filesystem result, not an OOM inference. Workspace memory data was tiny and ephemeral; no PVC or existing volume was mounted.

## Optional restore obstacle

Each target was a distinct fresh Pod with an untouched workspace before its one restore attempt. Restore returned exit1 and the generic helper rejection. Read-only inspection then found the correct 32-byte `tiny.txt` in each target; the target had already been partially populated. The target root was UID 0/GID 1000, mode 03777. An explicit same-mode chmod by UID1000 returned errno 1 EPERM on both nodes. The actual helper implementation applies directory modes in postorder, including `.` at the end (`archive.go` restore); this observed root-ownership constraint explains why this profile cannot complete restore even though it can create the file. No success is claimed. Restores were never replayed and no existing data was involved. Root ownership/restore semantics require a separately reviewed delivery/profile or helper design change before a successful integration proof; no chown, extra privilege, source edit or host mutation was introduced here.

The first A batch stopped at that optional restore error before publishing its aggregated successes. A second batch deliberately repeated the bounded, task-owned positive/fence/filesystem checks under fresh helper operation UUIDs while persisting structured results, inspected the existing failed restore receipt and target, and did not invoke A restore again. B ran the full bounded probe once. There were 40 recorded helper calls in total. Initial crictl calls failed because the bundled CLI searched an absent default config; subsequent calls used `--config /dev/null` and explicit local endpoints. The initial task script named operator.py collided with Python's standard operator module before any Pod/policy mutation; `python3 -I` isolated imports for all executed phases. These harness corrections changed only private task artifacts.

## Cleanup and retained artifacts

Every delete used a freshly checked exact task operation label and recorded UID, then a Kubernetes DeleteOptions UID precondition with normal grace 10 seconds and Background propagation. No force delete, broad selector delete, image pruning or unrelated cleanup was used. All eight exact objects were observed absent; no ambiguous object was adopted or deleted.

| Kind | Name | UID | Final state |
| --- | --- | --- | --- |
| NetworkPolicy | `task3g-273ff3c5e3-a-source-deny` | `aee5161d-a245-4108-94e3-f021d7b85f05` | absent |
| Pod | `task3g-273ff3c5e3-a-source` | `220b55d3-d246-4f37-b90e-e7abaa37016a` | absent |
| NetworkPolicy | `task3g-273ff3c5e3-a-target-deny` | `fb5cd7f4-fbbd-44f4-b3a6-2e2a1243f83d` | absent |
| Pod | `task3g-273ff3c5e3-a-target` | `50b1954f-1907-449a-ba58-b7f6dfaf5db0` | absent |
| NetworkPolicy | `task3g-273ff3c5e3-b-source-deny` | `a2d7ec46-fac8-46c1-bd3d-88a44919caaf` | absent |
| Pod | `task3g-273ff3c5e3-b-source` | `14aad94c-4b23-4de0-95cc-59c1f7433c44` | absent |
| NetworkPolicy | `task3g-273ff3c5e3-b-target-deny` | `0fb18da8-2ccd-4ed1-81cd-65189d6548c7` | absent |
| Pod | `task3g-273ff3c5e3-b-target` | `045bc572-771c-4c96-9305-03eba7d7e353` | absent |

Task-owned image references and private operator artifacts remain for audit under `/opt/workspace-t0224/completion/task3g-dace8baf8c9d4ac6a25e15debdd190a0` on A and B; B retains its verified Docker archive. No existing workloads, PVs, data, ACLs, credentials, policies, node labels, networking or production flags were changed. No source modules were changed, staged or committed. Remote work has stopped and the parent was notified that storage's quiet sample may resume.

This establishes independent trusted helper delivery and these bounded primitives under actual gVisor on both nodes. It does not establish controller/API allocation binding, durable operation journals, at-most-once product recovery, admission enforcement against tenant control-plane writers, product PVC lifecycle, large restore staging, hard disk quota or production readiness. Dummy secret injection was optional and was not exercised.
