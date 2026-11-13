# Trusted node sampling and scheduling prerequisites

This is an operator template, not an assertion that any cluster has passed. No
cluster credentials or real nodes were available in the development environment.

Each controller binds one workspace, host registration and Node. Its verified
HTTPS kubeconfig must refer to one cluster; TLS bypass is rejected. The Node's
`metadata.name` and UID must be present, and its `status.nodeInfo.machineID` must
be a valid 32 digit machine identifier. The report hashes `machine-id:` plus the
normalized identifier. The operator must enable Node authorizer/NodeRestriction
and set the protected label
`workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node=<Node name>`.
The Node object is read again before a report to reject replacement during capture.
A `gvisor` RuntimeClass with `handler: runsc` must exist and be verified on each node.

`rbac.yaml` grants the collector read access. Replace its Node/subject values per
controller. `nodes/proxy` permission is powerful: Kubernetes RBAC cannot limit it
to the `/stats/summary` suffix. This credential therefore belongs only to the
trusted host controller, protected on disk; never give it to workload Pods or
tenants. Workload credentials and admission must prohibit edits to managed
namespace, Pod, PVC, deployment and identity labels. The controller's mutation
permissions are a separate deployment concern; this telemetry role grants none.
Cluster-wide list reads are necessary to discover node-local orphaned PVCs and
managed Deployments; no Secret permission is needed.

The supported storage profile uses `local` PVs on the same filesystem reported by
the Node's kubelet `node.fs` stats. Operators must verify that local PV paths are
on that filesystem, with enforced per-environment quotas; remote CSI storage and
separate unmeasured mounts are not supported by this collector. PV node affinity
must constrain `metadata.name` to exactly the configured node, and the bound PV's
claimRef must match the current PVC name, namespace and UID. Deployment identity,
namespace workspace/instance labels, current Pod UID/node/identity labels and
kubelet PodRef must agree. Workload stats are never associated by a reused name.
CPU/memory are allocatable resources, CPU usage is current `usageNanoCores`, and
memory/disk availability come from current kubelet summary. Stats timestamps older
than 60 seconds or over 5 seconds in the future, missing fields, negative numbers,
or unknown inventory reject the sample. CPU allocatable is rounded down because
the existing host heartbeat contract stores whole CPU counts.

The collector obtains `/report/start` before capture, then submits `/report` only
with a complete sample and physical host identity. It never turns an unmeasured
PVC into zero. Detached/stopped or orphaned node-local PVCs with no current kubelet
volume stats deliberately prevent eligibility refresh. They need a future trusted
storage usage source before stopped environments can coexist with fresh placement
metrics. Kubernetes reports must update capacity/metrics without the Docker
missing-container state/lost/stray reconciliation; lifecycle state and UID binding
come from the controller's fenced lifecycle receipts.

Scheduling additionally requires operator host metadata:

```json
{"network_validation_revision":1,"network_ready":true,"supported_profile":"gvisor-immutable-v1"}
```

Set these only after the real two-node acceptance harness passes, including
same-instance TCP/UDP, cross-instance deny, managed DNS, prohibited platform/node
addresses, gateway TCP authorization and already-open connection revocation. A
current physical report alone does not prove network enforcement. Kubernetes
candidates expose only gVisor. Admission must enforce the matching finite workload
profile: immutable digest image, nonroot user, no setup/files/secret injection,
public egress or unsupported runtime operations. Exec, file copy and snapshots
remain unsupported by the current lifecycle controller.

Verified upstream source on 2026-11-13:

- [kubelet v0.35.10 stats schema](https://raw.githubusercontent.com/kubernetes/kubelet/v0.35.10/pkg/apis/stats/v1alpha1/types.go), SHA256 `c3ed2fff06a1e59d5e4210dc3fda167013edfc090ac9fbe45ec19cca1e782c39`.
- [Kubernetes v1.35.10 kubelet server](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.35.10/pkg/kubelet/server/server.go), SHA256 `e89803fb66a02360753da9b7a5c9a3672016694f3a3f1ae9efc2b1b1b3ccd583`.
- [Kubernetes v1.35.10 stats handler](https://raw.githubusercontent.com/kubernetes/kubernetes/v1.35.10/pkg/kubelet/server/stats/handler.go) registers `GET /stats/summary`.

Source verification establishes response field names and the read endpoint;
it does not establish deployment compatibility, storage quotas or actual network
readiness. Run the real cluster checks before enabling the registration.
