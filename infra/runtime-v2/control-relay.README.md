# Private control relay (IP-only)

`render_control_relay.py` renders four objects offline: a ConfigMap, ClusterIP
Service, Deployment and NetworkPolicy in existing namespace `runner-system`.
It neither reserves addresses nor deploys resources. This path needs no domain,
DNS, public listener, tailnet ACL change or tenant-policy change. The operator
must first prove that the selected relay node can reach the existing HTTPS API.

## Reserve the address before issuing the certificate

In the approved nonproduction cluster, reserve the Service using its final name,
selector and port. Let Kubernetes allocate the actual free address; do not guess
one, and do not delete/recreate the Service after issuing its certificate.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: control-api-private
  namespace: runner-system
  labels:
    workspace.myworkspace.io/component: runtime-control-relay
spec:
  type: ClusterIP
  ipFamilyPolicy: SingleStack
  ipFamilies: [IPv4]
  selector:
    workspace.myworkspace.io/component: runtime-control-relay
  ports:
    - name: https
      protocol: TCP
      port: 8443
      targetPort: 8443
```

Read `spec.clusterIP` from that Service. Securely generate a new leaf certificate
with **IP SAN equal to this exact ClusterIP**, signed by the existing trusted
lab CA; an IP in a DNS SAN or common name is insufficient. The renderer cannot
inspect certificates, allocate an address or prove that an address is free.
The CA private key stays outside the cluster and outside repository artifacts.

Create an existing namespaced generic Secret with exactly the referenced files:

- `server.crt`: leaf plus required intermediate chain for the reserved Service IP.
- `server.key`: matching leaf private key.
- `ca.crt`: CA bundle that verifies the existing upstream HTTPS endpoint.

Use secure local files and the operator's secret channel. Do not embed Secret
objects, keys, tokens or certificates in committed options or manifests. The
Secret is mounted read-only with mode `0440`, made readable by pod fsGroup1000.
Clients must separately trust the CA that signed `server.crt`.

For host-origin traffic through Cilium, verify the source address actually seen by
the relay policy. It can be the node's **CiliumInternalIP**, rather than its
Tailscale/InternalIP. Read the approved nodes' `CiliumNode.spec.addresses` and
correlate a short relay-scoped drop trace before changing the options. Include
only those verified node addresses as exact `/32` entries in `node_cidrs`; do not
allow the whole Pod CIDR. Recheck after node or Cilium identity replacement. The
existing cluster's `policy-cidr-match-mode=nodes` is part of this path's tested
configuration, not an instruction to change a different cluster globally.

## Render reviewed non-secret options

The stable Python interface is `render(options: dict) -> Kubernetes List`.
The CLI reads a JSON file and writes that List to stdout:

```sh
python3 infra/runtime-v2/render_control_relay.py --options /secure/operator/relay-options.json
```

| Field | Required value |
| --- | --- |
| `service_ip` | Actual reserved IPv4 ClusterIP; bare canonical address |
| `upstream_ip` | Exact reachable IPv4 API address with matching certificate IP SAN |
| `upstream_port` | Optional integer 1–65535; default `8443` |
| `listen_port` | Optional; only integer `8443` accepted |
| `node_name` | Exact Kubernetes node name (max63 characters), with the protected label below |
| `node_cidrs` | Nonempty unique list of approved canonical node IPv4 `/32` CIDRs |
| `image` | Exact image below, including tag and digest |
| `tls_secret` | Existing Secret DNS-subdomain name in `runner-system` |

The only accepted image is:

```text
caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5
```

Unknown options, hostnames, URLs in IP fields, loopback/link-local/multicast/
reserved addresses, wildcard/subnet CIDRs, mutable or different images, boolean
ports and injected config/secret strings are rejected. The renderer contains no
credentials and does not inject authorization: clients supply their normal
runner API Authorization header, which Caddy forwards normally to the API.

The pod requires both `metadata.name=node_name` affinity and this protected node
label, consistent with the runtime's existing node trust mechanism:

```text
workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node=<node_name>
```

Apply the independently reviewed output while retaining the reserved Service.
After changing its ConfigMap or TLS Secret, explicitly restart this Deployment
and repeat TLS/path verification; Caddy does not watch these files for config
reload. The renderer creates no Namespace, RBAC, ServiceAccount or tenant policy.

## Security boundary and verification

The only HTTP routes proxied are `/runner-api/v1/*`, `/healthz` and `/readyz`.
The prefix stays intact; all other paths return404 locally. Static TLS files
serve port8443. Upstream TLS uses `tls_trust_pool file /relay/tls/ca.crt` plus
`tls_server_name <upstream_ip>` so both CA trust and IP SAN are checked. There
is no TLS verification bypass or trusted client-supplied proxy header setting.
Global admin, certificate automation and config persistence are disabled;
HTTP/3 is disabled, so there is only the TCP listener. Request logging is absent
and global logs are discarded to prevent upstream errors logging credentials.

The Deployment runs UID/GID1000, fsGroup1000, RuntimeDefault seccomp, read-only
rootfs, no privilege escalation, no capabilities and no host namespaces/ports.
Only `/data` (16Mi) and `/tmp` (64Mi) emptyDirs are writable. The pinned official
image's `/usr/bin/caddy` has `cap_net_bind_service=ep`; executing it directly
with all capabilities dropped fails EPERM on the tested Docker runtime. A fixed
nonroot shell command copies that trusted image binary to `/tmp/caddy` using
plain `cp`, stripping the file capability, then `exec`s it. The 51,953,826-byte
binary fits inside the 64Mi bound. No tenant files or operator shell fragments
are executed. No extra capability, root init container or image rebuild is used.

The exact relay-only NetworkPolicy permits ingress TCP8443 from the operator's
node `/32` list and pods labelled
`workspace.myworkspace.io/component=runtime-controller` **in the same namespace**.
Egress is only the upstream IPv4 `/32` and TCP port. There is no DNS or internet
allowance. Existing policies are additive in Kubernetes: the operator must check
that no broader existing policy also selects this relay. Node-origin traffic
and Cilium source identity/SNAT behavior require live verification; a manifest
cannot prove enforcement. Tenant namespaces cannot acquire this same-namespace
controller exception merely by setting a pod label.

Readiness/liveness are TCP socket checks; they do not verify the upstream or
replace end-to-end TLS acceptance. Before configuring clients, prove from NodeB:
verified HTTPS to `https://<reserved-ClusterIP>:8443/healthz`, normal authorized
runner API behavior, 404 for other paths, and rejection with a wrong CA or a
certificate without the matching IP SAN. Verify network rejection from an
unapproved pod separately. A ClusterIP is reachable through the cluster overlay;
it is not an externally published Service.

The focused pytest file conditionally runs the actual pinned Docker image with
`--network none`, UID1000, read-only rootfs, all capabilities dropped and no new
privileges. It adapts and validates the actual rendered Caddyfile using freshly
generated synthetic TLS material. It skips if Docker/the exact cached image is
unavailable, never pulls images, and never contacts the real nodes/API. A pass
establishes configuration and restricted-process compatibility, not live TLS or
Cilium acceptance.

Official syntax references (verified against the pinned executable):

- https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
- https://caddyserver.com/docs/caddyfile/options
- https://caddyserver.com/docs/caddyfile/directives/tls
