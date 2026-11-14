#!/usr/bin/env python3
"""Opt-in destructive-within-owned-namespaces nonproduction network acceptance.

No cluster operation occurs without both explicit consent and exact context name.
The gateway suite needs existing DB-backed fixtures; unconfigured suites block a
complete PASS. Tests use ephemeral network-only Pods, never runtime PVCs.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import select
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
from app.runtime_v2.kubernetes import KubernetesRuntime, RuntimeIdentity  # noqa: E402
from app.runtime_v2.runner import Kubectl  # noqa: E402

LABEL = "workspace.myworkspace.io/acceptance-run"
NODE_LABEL = "workspace.myworkspace.io.node-restriction.kubernetes.io/runner-node"
SERVER = """import socket,threading

def tcp_client(c):
 try:
  while True:
   b=c.recv(4096)
   if not b: break
   c.sendall(b)
 finally: c.close()

def udp():
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(("0.0.0.0",7070))
 while True:
  b,a=s.recvfrom(4096);s.sendto(b,a)
threading.Thread(target=udp,daemon=True).start()
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("0.0.0.0",7070));s.listen(16)
while True:
 c,_=s.accept();threading.Thread(target=tcp_client,args=(c,),daemon=True).start()
"""
PROBE = """import socket,sys
kind,host,port,expected=sys.argv[1:]
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if kind=="udp" else socket.SOCK_STREAM)
s.settimeout(3)
try:
 s.connect((host,int(port)));s.sendall(b"acceptance-echo");ok=s.recv(128)==b"acceptance-echo"
except OSError: ok=False
finally: s.close()
sys.exit(0 if ok==(expected=="allow") else 1)
"""
STREAM = """import socket,sys,time
s=socket.create_connection((sys.argv[1],int(sys.argv[2])),3);s.settimeout(3)
s.sendall(b"acceptance-echo")
if s.recv(128)!=b"acceptance-echo": sys.exit(2)
print("OPEN",flush=True)
end=time.monotonic()+30
while time.monotonic()<end:
 try:
  s.sendall(b"acceptance-echo");b=s.recv(128)
  if not b: print("CLOSED",flush=True);sys.exit(0)
  if b!=b"acceptance-echo": sys.exit(3)
 except OSError: print("CLOSED",flush=True);sys.exit(0)
 time.sleep(.25)
sys.exit(4)
"""


def test_objects(
    image: str, node_a: str, node_b: str, run_id: UUID
) -> tuple[list[dict[str, Any]], dict[str, RuntimeIdentity]]:
    if node_a == node_b:
        raise ValueError("two distinct nodes required")
    workspace, instance1, instance2 = uuid4(), uuid4(), uuid4()
    identities = {
        name: RuntimeIdentity(workspace, instance, uuid4(), uuid4(), 1, node)
        for name, instance, node in (
            ("a1", instance1, node_a),
            ("b1", instance1, node_b),
            ("a2", instance2, node_a),
            ("b2", instance2, node_b),
        )
    }
    objects = []
    for identity in (identities["a1"], identities["a2"]):
        scope = {
            key: value
            for key, value in identity.labels.items()
            if key.endswith(("/workspace", "/instance"))
        }
        objects.extend(
            [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": identity.namespace,
                        "labels": {
                            **scope,
                            LABEL: str(run_id),
                            "pod-security.kubernetes.io/enforce": "restricted",
                            "pod-security.kubernetes.io/enforce-version": "v1.35",
                        },
                    },
                },
                {
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "NetworkPolicy",
                    "metadata": {
                        "name": "runner-default-deny",
                        "namespace": identity.namespace,
                    },
                    "spec": {
                        "podSelector": {},
                        "policyTypes": ["Ingress", "Egress"],
                        "ingress": [],
                        "egress": [],
                    },
                },
                KubernetesRuntime.network_policy(identity),
            ]
        )
    for identity in identities.values():
        pod = KubernetesRuntime.pod_manifest(identity, image, 100, 128)
        pod["metadata"]["labels"][LABEL] = str(run_id)
        pod["spec"]["runtimeClassName"] = "gvisor"
        pod["spec"]["securityContext"].update(runAsUser=1000, runAsGroup=1000)
        container = pod["spec"]["containers"][0]
        container["command"] = ["python", "-u", "-c", SERVER]
        container["readinessProbe"] = {
            "tcpSocket": {"port": 7070},
            "initialDelaySeconds": 1,
            "periodSeconds": 2,
        }
        objects.append(pod)
    return objects, identities


def argv(api: Kubectl, context: str, args: list[str]) -> list[str]:
    return [
        api.executable,
        "--kubeconfig",
        api.kubeconfig,
        "--context",
        context,
        "--request-timeout=20s",
        "--insecure-skip-tls-verify=false",
        *args,
    ]


def call(
    api: Kubectl,
    context: str,
    args: list[str],
    *,
    data: dict[str, Any] | None = None,
    timeout: int = 30,
) -> str:
    result = subprocess.run(  # noqa: S603 — fixed kubectl, structured operator arguments
        argv(api, context, args),
        input=json.dumps(data) if data else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("bounded Kubernetes acceptance command failed")
    return result.stdout


def exec_args(namespace: str, pod: str, code: str, *args: str) -> list[str]:
    return ["exec", "-n", namespace, pod, "--", "python", "-c", code, *args]


def gateway_checks(api: Kubectl, context: str, plan: dict[str, Any]) -> None:
    for key in (
        "source_namespace",
        "source_pod",
        "denied_namespace",
        "denied_pod",
        "gateway_host",
    ):
        if not isinstance(plan[key], str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]{0,252}", plan[key]
        ):
            raise ValueError("invalid gateway fixture identity")
    if type(plan["port"]) is not int or not 30000 <= plan["port"] <= 39999:
        raise ValueError("private gateway test requires a reviewed port")
    command = plan["revoke_argv"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)
        or not Path(command[0]).is_absolute()
    ):
        raise ValueError("explicit absolute bounded revoke argv required")
    source = plan["source_namespace"], plan["source_pod"]
    denied = plan["denied_namespace"], plan["denied_pod"]
    host, port = plan["gateway_host"], str(plan["port"])
    call(api, context, exec_args(*source, PROBE, "tcp", host, port, "allow"))
    call(api, context, exec_args(*denied, PROBE, "tcp", host, port, "deny"))
    process = subprocess.Popen(  # noqa: S603 — fixed kubectl, validated fixture names
        argv(api, context, exec_args(*source, STREAM, host, port)),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        if (
            process.stdout is None
            or not select.select([process.stdout], [], [], 10)[0]
            or process.stdout.readline().strip() != "OPEN"
        ):
            raise RuntimeError("gateway in-flight stream was not established")
        revoked = subprocess.run(  # noqa: S603 — explicitly supplied operator revoke argv, no shell
            command,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if revoked.returncode:
            raise RuntimeError("operator revocation command failed")
        if (
            not select.select([process.stdout], [], [], 8)[0]
            or process.stdout.readline().strip() != "CLOSED"
        ):
            raise RuntimeError("revocation did not close the established stream within 8 seconds")
        if process.wait(timeout=5) != 0:
            raise RuntimeError("gateway stream acceptance failed")
        call(api, context, exec_args(*source, PROBE, "tcp", host, port, "deny"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_test_cluster:
        raise ValueError("--allow-test-cluster is required before any cluster operation")
    api = Kubectl(str(args.kubeconfig.resolve()))
    current = api.call(["config", "current-context"]).strip()
    if not args.confirm_context or current != args.confirm_context:
        raise ValueError("--confirm-context must exactly match current-context")
    api.validate_connection()
    run_id = uuid4()
    args.run_id = str(run_id)
    objects, identities = test_objects(args.image, args.node_a, args.node_b, run_id)
    # Validate nodes and protected labels before creating any object.
    for name in (args.node_a, args.node_b):
        node = json.loads(call(api, current, ["get", "node", name, "-o", "json"]))
        if node.get("metadata", {}).get("labels", {}).get(NODE_LABEL) != name:
            raise ValueError("test Node protected label missing")
    created: list[tuple[str, str]] = []
    try:
        for obj in objects:
            if obj["kind"] == "Namespace":
                observed = json.loads(
                    call(api, current, ["create", "-f", "-", "-o", "json"], data=obj)
                )
                created.append((observed["metadata"]["name"], observed["metadata"]["uid"]))
            else:
                call(api, current, ["create", "-f", "-"], data=obj)
        addresses = {}
        for name, identity in identities.items():
            call(
                api,
                current,
                [
                    "wait",
                    "-n",
                    identity.namespace,
                    "--for=condition=Ready",
                    "pod/" + identity.pod_name,
                    "--timeout=120s",
                ],
                timeout=125,
            )
            pod = json.loads(
                call(
                    api,
                    current,
                    [
                        "get",
                        "pod",
                        identity.pod_name,
                        "-n",
                        identity.namespace,
                        "-o",
                        "json",
                    ],
                )
            )
            if pod["spec"]["nodeName"] != identity.node_name:
                raise RuntimeError("test Pod ran outside its expected node")
            addresses[name] = pod["status"]["podIP"]
        for source_name, target_name, allowed in (
            ("a1", "b1", True),
            ("b1", "a1", True),
            ("a2", "b2", True),
            ("b2", "a2", True),
            ("a1", "b2", False),
            ("b2", "a1", False),
            ("a2", "b1", False),
            ("b1", "a2", False),
        ):
            source = identities[source_name]
            for protocol in ("tcp", "udp"):
                call(
                    api,
                    current,
                    exec_args(
                        source.namespace,
                        source.pod_name,
                        PROBE,
                        protocol,
                        addresses[target_name],
                        "7070",
                        "allow" if allowed else "deny",
                    ),
                )
        for identity in identities.values():
            call(
                api,
                current,
                exec_args(
                    identity.namespace,
                    identity.pod_name,
                    "import socket;socket.gethostbyname('kubernetes.default.svc.cluster.local')",
                ),
            )
        result = {
            "run_id": str(run_id),
            "context": current,
            "private_tcp_udp_dns": "passed",
            "gateway_allowed_denied_revocation": "blocked: DB-backed gateway fixture plan missing",
            "public_tls_external_source": "blocked: external IPv4 client and real LB required",
        }
        if args.gateway_plan:
            gateway_checks(api, current, json.loads(args.gateway_plan.read_text()))
            result["gateway_allowed_denied_revocation"] = "passed"
        return result
    finally:
        # Delete only namespace names and UIDs this invocation received from create.
        for namespace, uid in reversed(created):
            observed = json.loads(call(api, current, ["get", "namespace", namespace, "-o", "json"]))
            meta = observed.get("metadata") or {}
            if meta.get("uid") != uid or meta.get("labels", {}).get(LABEL) != str(run_id):
                raise RuntimeError("cleanup identity changed; refusing namespace deletion")
            options = {
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": uid},
            }
            call(
                api,
                current,
                ["delete", "--raw", "/api/v1/namespaces/" + namespace, "-f", "-"],
                data=options,
            )


def public_checks(plan: dict[str, Any], role: str | None) -> dict[str, Any]:
    """One external origin is evidence for one role, never proof of both CIDRs."""
    if role not in ("allowed", "denied"):
        raise ValueError("explicit --external-role required")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", plan["host"]):
        raise ValueError("explicit external gateway IPv4 address or hostname required")
    if type(plan["port"]) is not int or not 20000 <= plan["port"] <= 29999:
        raise ValueError("reviewed public binding port required")
    expected_source = ipaddress.ip_address(plan["external_source_expected"])
    if not isinstance(expected_source, ipaddress.IPv4Address) or not expected_source.is_global:
        raise ValueError("operator-verified external IPv4 source required")
    allowed = [ipaddress.ip_network(value, strict=True) for value in plan["source_cidrs"]]
    if not allowed or any(not isinstance(value, ipaddress.IPv4Network) for value in allowed):
        raise ValueError("reviewed IPv4 source allowlist required")
    if any(expected_source in network for network in allowed) != (role == "allowed"):
        raise ValueError("external role does not match reviewed source CIDRs")
    if not re.fullmatch(r"[a-f0-9]{64}", plan["reviewed_config_sha256"]):
        raise ValueError("reviewed configuration fingerprint required")
    context = ssl.create_default_context(cafile=str(Path(plan["ca_file"]).resolve()))
    # Resolve explicitly as IPv4, preserving TLS verification against server_name.
    address = socket.getaddrinfo(plan["host"], plan["port"], socket.AF_INET, socket.SOCK_STREAM)[0][
        4
    ]
    result: dict[str, Any] = {
        "probe_completed": False,
        "external_role": role,
        "expected_external_source": str(expected_source),
        "source_provenance": "operator assertion; compare LB/gateway socket peer audit",
        "reviewed_config_sha256": plan["reviewed_config_sha256"],
        "public_source_probe": "unknown",
        "public_revocation": "blocked: no explicit revoke argv",
        "combined_acceptance": "pending independent allowed/denied-origin artifacts and peer audit",
    }
    stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stream.settimeout(5)
    try:
        stream.connect(address)
        result["socket_local_ipv4"] = stream.getsockname()[0]
        secured = context.wrap_socket(stream, server_hostname=plan["server_name"])
        stream = secured
        certificate = secured.getpeercert(binary_form=True)
        result["tls_certificate_sha256"] = hashlib.sha256(certificate or b"").hexdigest()
        secured.sendall(b"acceptance-echo")
        connected = secured.recv(128) == b"acceptance-echo"
        result["public_source_probe"] = "connected" if connected else "refused_after_verified_tls"
        if role == "denied" and connected:
            raise RuntimeError("denied external origin reached the target")
        if role == "allowed" and not connected:
            raise RuntimeError("allowed external origin could not reach echo target")
        command = plan.get("revoke_argv")
        if role == "allowed" and command:
            if (
                not isinstance(command, list)
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)
                or not Path(command[0]).is_absolute()
            ):
                raise ValueError("explicit absolute revoke argv required")
            revoked = subprocess.run(  # noqa: S603 — explicit operator revoke argv, no shell
                command,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if revoked.returncode:
                raise RuntimeError("operator revocation command failed")
            deadline = time.monotonic() + 8
            closed = False
            secured.settimeout(1)
            while time.monotonic() < deadline:
                try:
                    secured.sendall(b"acceptance-echo")
                    if not secured.recv(128):
                        closed = True
                        break
                except (ConnectionError, ssl.SSLError):
                    closed = True
                    break
                except TimeoutError:
                    continue
                time.sleep(0.25)
            if not closed:
                raise RuntimeError("public revocation did not close the in-flight stream")
            result["public_revocation"] = "existing connection closed within 8 seconds"
        result["probe_completed"] = True
        return result
    except ssl.SSLCertVerificationError:
        raise
    except OSError:
        if role != "denied":
            raise
        # LB allowlists may reject before TLS. This alone cannot distinguish an
        # allowlist denial from outage; preserve that uncertainty in the artifact.
        result["public_source_probe"] = "refused_before_echo; compare concurrent allowed baseline"
        result["probe_completed"] = True
        return result
    finally:
        stream.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-test-cluster", action="store_true")
    parser.add_argument("--confirm-context")
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--node-a")
    parser.add_argument("--node-b")
    parser.add_argument(
        "--image",
        help="immutable runtime image digest with Python stdlib",
    )
    parser.add_argument("--gateway-plan", type=Path)
    parser.add_argument("--public-plan", type=Path)
    parser.add_argument("--external-role", choices=("allowed", "denied"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.public_plan:
            result = public_checks(json.loads(args.public_plan.read_text()), args.external_role)
        else:
            if not all((args.kubeconfig, args.node_a, args.node_b, args.image)):
                raise ValueError("cluster acceptance arguments missing")
            result = run(args)
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        args.report.write_text(
            json.dumps(
                {
                    "status": "failed_or_blocked",
                    "run_id": getattr(args, "run_id", None),
                    "reason": "acceptance dependency unavailable or assertion failed",
                },
                indent=2,
            )
            + "\n"
        )
        print(
            "Acceptance failed or blocked; inspect the test cluster and owned namespaces.",
            file=sys.stderr,
        )
        return 2
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    if args.public_plan:
        return 0 if result.get("probe_completed") is True else 2
    return (
        0
        if all(
            value == "passed" for key, value in result.items() if key not in ("run_id", "context")
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
