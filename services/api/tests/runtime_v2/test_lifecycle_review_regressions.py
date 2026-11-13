"""Regressions for scoped review C1, I1 and I2."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.runner_agent.client import WorkspaceError
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import Kubectl
from tests.runtime_v2.test_lifecycle_recovery import (
    Api,
    Workspace,
    command,
    controller,
    identity,
)


def test_selected_insecure_kubeconfig_context_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {
        "clusters": [
            {"cluster": {"server": "https://cluster.invalid", "insecure-skip-tls-verify": True}}
        ]
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(config), ""),
    )
    with pytest.raises(ValueError, match="TLS verification"):
        Kubectl("/etc/kubeconfig").validate_connection()


@pytest.mark.parametrize("kind", ["start_env", "stop_env", "delete_env"])
@pytest.mark.parametrize("resource", ["deployment", "pvc"])
def test_new_command_after_restart_preserves_original_allocation_uids(
    tmp_path: Path, kind: str, resource: str
) -> None:
    ident, workspace = identity(), Workspace()
    api = Api(ident)
    api.timeout = False
    assert api.deployment is not None
    api.deployment["spec"]["replicas"] = 0
    first = command(ident)
    initial = controller(tmp_path, ident, api, workspace)
    initial.process(first)
    assert len(workspace.sent) == 1 and workspace.sent[0][1]["status"] == "done"
    original = initial.journal.recovery(first["id"])["provenance"]
    item = api.deployment if resource == "deployment" else api.claim
    assert item is not None
    item["metadata"]["uid"] = "replacement-" + resource
    second = command(ident, kind)
    restarted = controller(tmp_path, ident, api, workspace)
    restarted.process(second)
    assert len(api.mutations) == 1
    assert len(workspace.sent) == 1
    assert restarted.journal.recovery(first["id"])["provenance"] == original
    assert ("replacement-" + resource).encode() not in (tmp_path / "journal.db").read_bytes()


class ReadyApi(Api):
    def __init__(self, ident: RuntimeIdentity) -> None:
        super().__init__(ident)
        self.timeout = False
        assert self.deployment is not None
        self.deployment["status"]["readyReplicas"] = 1
        self.pods = [
            {
                "metadata": {
                    "name": "pod",
                    "namespace": ident.namespace,
                    "uid": "old-pod",
                    "labels": ident.labels,
                    "ownerReferences": [
                        {"kind": "ReplicaSet", "name": "rs", "uid": "rs-uid", "controller": True}
                    ],
                },
                "spec": {"nodeName": ident.node_name},
                "status": {
                    "phase": "Running",
                    "podIP": "10.0.0.1",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        ]
        self.rs: dict[str, Any] = {
            "metadata": {
                "name": "rs",
                "namespace": ident.namespace,
                "uid": "rs-uid",
                "labels": ident.labels,
                "ownerReferences": [
                    {
                        "kind": "Deployment",
                        "name": ident.pod_name,
                        "uid": "dep-original",
                        "controller": True,
                    }
                ],
            }
        }

    def get_optional(
        self, kind: str, name: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        return self.rs if kind == "replicaset" else super().get_optional(kind, name, namespace)


class LostCallbackWorkspace(Workspace):
    def __init__(self) -> None:
        super().__init__()
        self.lose_callback = True

    def post(self, path: str, body: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        if self.lose_callback:
            self.lose_callback = False
            raise WorkspaceError(0, "NETWORK", "response lost")
        return super().post(path, body, timeout=timeout)


@pytest.mark.parametrize(
    "uid,address", [("new-pod", "10.0.0.2"), ("old-pod", "10.0.0.2"), ("new-pod", "10.0.0.1")]
)
def test_lost_callback_never_acknowledges_changed_pod_binding(
    tmp_path: Path, uid: str, address: str
) -> None:
    ident, workspace = identity(), LostCallbackWorkspace()
    api = ReadyApi(ident)
    first = command(ident, "start_env")
    with pytest.raises(WorkspaceError):
        controller(tmp_path, ident, api, workspace).process(first)
    api.pods[0]["metadata"]["uid"] = uid
    api.pods[0]["status"]["podIP"] = address
    restarted = controller(tmp_path, ident, api, workspace)
    restarted.recover_pending()
    assert workspace.sent == []
    assert len(api.mutations) == 1
    receipt = restarted.journal.begin(first["id"])
    assert isinstance(receipt, dict)
    assert receipt["result"]["pod_uid"] == "old-pod"
    assert receipt["result"]["pod_ip"] == "10.0.0.1"


def test_pinned_kubectl_rejects_untrusted_local_tls(tmp_path: Path) -> None:
    """Optional real CLI test; local TLS endpoint, never a Kubernetes cluster."""
    import hashlib
    import ipaddress
    import os
    import secrets
    import ssl
    import threading
    from datetime import UTC, datetime, timedelta
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from app.runtime_v2.runner import KubernetesCommandError

    executable = os.environ.get("RUNTIME_V2_TEST_KUBECTL")
    if not executable:
        pytest.skip("set RUNTIME_V2_TEST_KUBECTL to checksum-verified official v1.35.10")
    assert (
        hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        == "c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327"
    )
    version = subprocess.run(  # noqa: S603 - executable SHA256 is verified above
        [executable, "version", "--client", "-o", "json"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(version.stdout)["clientVersion"]["gitVersion"] == "v1.35.10"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.crt", tmp_path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)

    class Handler(BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self) -> None:
            type(self).requests += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"gitVersion":"local-tls-harness"}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cluster = {
            "server": f"https://127.0.0.1:{server.server_port}",
            "insecure-skip-tls-verify": True,
        }
        config = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": "test",
            "clusters": [{"name": "local", "cluster": cluster}],
            "contexts": [{"name": "test", "context": {"cluster": "local", "user": "test"}}],
            "users": [{"name": "test", "user": {"token": secrets.token_hex(16)}}],
        }
        path = tmp_path / "kubeconfig.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        api = Kubectl(str(path), executable=executable, timeout=10)
        with pytest.raises(ValueError, match="TLS verification"):
            api.validate_connection()
        assert Handler.requests == 0
        cluster["insecure-skip-tls-verify"] = False
        path.write_text(json.dumps(config))
        api.validate_connection()
        with pytest.raises(KubernetesCommandError):
            api.call(["get", "--raw=/version"])
        assert Handler.requests == 0
        # A positive control proves the server/CLI path works when its CA is trusted.
        cluster["certificate-authority"] = str(cert_path)
        path.write_text(json.dumps(config))
        api.validate_connection()
        assert json.loads(api.call(["get", "--raw=/version"]))["gitVersion"] == "local-tls-harness"
        assert Handler.requests == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_new_generation_uses_its_own_uid_fence_in_same_process(tmp_path: Path) -> None:
    from dataclasses import replace

    ident, workspace = identity(), Workspace()
    api = Api(ident)
    api.timeout = False
    assert api.deployment is not None
    api.deployment["spec"]["replicas"] = 0
    current = controller(tmp_path, ident, api, workspace)
    current.process(command(ident))
    assert len(workspace.sent) == 1
    next_generation = replace(ident, generation=ident.generation + 1)
    assert api.deployment is not None
    api.deployment["metadata"].update({"labels": next_generation.labels, "uid": "next-deployment"})
    assert api.claim is not None
    api.claim["metadata"].update({"labels": next_generation.labels, "uid": "next-pvc"})
    current.process(command(next_generation))
    assert len(api.mutations) == 2
    assert len(workspace.sent) == 2
    assert workspace.sent[-1][1]["result"]["generation"] == next_generation.generation


def test_new_create_cannot_recreate_missing_canonical_resources(tmp_path: Path) -> None:
    from copy import deepcopy

    ident, workspace = identity(), Workspace()

    class RecreateApi(Api):
        def get_optional(
            self, kind: str, name: str, namespace: str | None = None
        ) -> dict[str, Any] | None:
            if kind == "namespace":
                return {"metadata": {"labels": ident.labels}}
            if kind == "networkpolicy":
                from app.runtime_v2.kubernetes import KubernetesRuntime

                return KubernetesRuntime.network_policy(ident)
            return super().get_optional(kind, name, namespace)

        def create(self, obj: dict[str, Any]) -> dict[str, Any]:
            self.mutations.append(["create", obj["kind"]])
            observed = deepcopy(obj)
            observed["metadata"]["uid"] = "replacement-after-absence"
            return observed

    api = RecreateApi(ident)
    api.timeout = False
    assert api.deployment is not None
    api.deployment["spec"]["replicas"] = 0
    controller(tmp_path, ident, api, workspace).process(command(ident))
    api.deployment = api.claim = None
    create = command(ident, "create_env")
    create["payload"].update(
        {"image": "image@sha256:" + "a" * 64, "cpus": 1, "memory_mb": 256, "disk_gb": 10}
    )
    controller(tmp_path, ident, api, workspace).process(create)
    assert len(api.mutations) == 1
    assert len(workspace.sent) == 1
