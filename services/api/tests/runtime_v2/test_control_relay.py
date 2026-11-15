"""Private relay exposes only its reviewed IP/TLS/path and network boundaries."""

import copy
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
RENDERER = ROOT / "infra/runtime-v2/render_control_relay.py"
IMAGE = (
    "caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5"
)
LABELS = {"workspace.myworkspace.io/component": "runtime-control-relay"}


def options() -> dict[str, Any]:
    return {
        "service_ip": "10.43.17.29",
        "upstream_ip": "100.72.60.100",
        "node_name": "node-a",
        "node_cidrs": ["100.72.60.100/32", "100.121.210.36/32"],
        "image": IMAGE,
        "tls_secret": "control-api-private-tls",
    }


def render(value: Any) -> dict[str, Any]:
    assert RENDERER.is_file(), "IP-only control relay renderer is missing"
    result: dict[str, Any] = runpy.run_path(str(RENDERER))["render"](value)
    return result


def objects() -> dict[str, Any]:
    result = render(options())
    assert result["apiVersion"] == "v1"
    assert result["kind"] == "List"
    assert len(result["items"]) == 4
    return {item["kind"]: item for item in result["items"]}


def test_service_exposes_only_reserved_cluster_ip_and_tls_port() -> None:
    items = objects()
    assert set(items) == {"ConfigMap", "Service", "Deployment", "NetworkPolicy"}
    for obj in items.values():
        assert obj["metadata"] == {
            "name": "control-api-private",
            "namespace": "runner-system",
            "labels": LABELS,
        }
    assert items["Service"]["spec"] == {
        "type": "ClusterIP",
        "clusterIP": "10.43.17.29",
        "ipFamilyPolicy": "SingleStack",
        "ipFamilies": ["IPv4"],
        "selector": LABELS,
        "ports": [{"name": "https", "port": 8443, "targetPort": 8443, "protocol": "TCP"}],
    }


def test_nonroot_readonly_pod_has_only_secret_refs_and_bounded_writable_mounts() -> None:
    deployment = objects()["Deployment"]["spec"]
    assert deployment["replicas"] == 1
    assert deployment["selector"] == {"matchLabels": LABELS}
    pod = deployment["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert all(pod[key] is False for key in ("hostNetwork", "hostPID", "hostIPC"))
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 1000,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert "initContainers" not in pod
    assert len(pod["containers"]) == 1
    container = pod["containers"][0]
    assert container["image"] == IMAGE
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "privileged": False,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["ports"] == [{"name": "https", "containerPort": 8443, "protocol": "TCP"}]
    assert container["resources"] == {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "500m", "memory": "128Mi"},
    }
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["tcpSocket"] == {"port": 8443}
        assert "httpGet" not in container[probe]
    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert set(mounts) == set(volumes) == {"caddyfile", "tls", "data", "tmp"}
    assert mounts["caddyfile"]["readOnly"] is True
    assert mounts["tls"]["readOnly"] is True
    assert mounts["data"]["mountPath"] == "/data"
    assert mounts["tmp"]["mountPath"] == "/tmp"  # noqa: S108 — isolated pod mount
    assert volumes["data"] == {"name": "data", "emptyDir": {"sizeLimit": "16Mi"}}
    assert volumes["tmp"] == {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}
    assert container["command"] == [
        "/bin/sh",
        "-ec",
        'cp /usr/bin/caddy /tmp/caddy; exec /tmp/caddy "$@"',
        "caddy",
    ]
    assert container["args"] == [
        "run",
        "--config",
        "/relay/config/Caddyfile",
        "--adapter",
        "caddyfile",
    ]
    assert volumes["tls"]["secret"] == {
        "secretName": "control-api-private-tls",
        "defaultMode": 0o440,
        "items": [{"key": key, "path": key} for key in ("server.crt", "server.key", "ca.crt")],
    }
    assert volumes["caddyfile"]["configMap"]["name"] == "control-api-private"
    for forbidden in ("hostPath", "hostPort", "envFrom"):
        assert forbidden not in json.dumps(pod)


def test_affinity_requires_exact_node_name_and_protected_node_label() -> None:
    pod = objects()["Deployment"]["spec"]["template"]["spec"]
    assert pod["affinity"] == {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {
                        "matchFields": [
                            {"key": "metadata.name", "operator": "In", "values": ["node-a"]}
                        ],
                        "matchExpressions": [
                            {
                                "key": (
                                    "workspace.myworkspace.io.node-restriction.kubernetes.io/"
                                    "runner-node"
                                ),
                                "operator": "In",
                                "values": ["node-a"],
                            }
                        ],
                    }
                ],
            }
        },
    }
    assert "nodeName" not in pod


def test_policy_allows_only_node_ips_controller_pods_and_exact_upstream() -> None:
    assert objects()["NetworkPolicy"]["spec"] == {
        "podSelector": {"matchLabels": LABELS},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [
            {
                "from": [{"ipBlock": {"cidr": cidr}} for cidr in options()["node_cidrs"]]
                + [
                    {
                        "podSelector": {
                            "matchLabels": {
                                "workspace.myworkspace.io/component": "runtime-controller",
                            }
                        }
                    }
                ],
                "ports": [{"protocol": "TCP", "port": 8443}],
            }
        ],
        "egress": [
            {
                "to": [{"ipBlock": {"cidr": "100.72.60.100/32"}}],
                "ports": [{"protocol": "TCP", "port": 8443}],
            }
        ],
    }


def test_caddy_has_verified_tls_ip_and_allowlisted_paths_without_auth_or_header_injection() -> None:
    config = objects()["ConfigMap"]["data"]["Caddyfile"]
    for required in (
        "admin off",
        "auto_https off",
        "persist_config off",
        "protocols h1 h2",
        "https://:8443",
        "tls /relay/tls/server.crt /relay/tls/server.key",
        "path /runner-api/v1/* /healthz /readyz",
        "handle @control",
        "reverse_proxy https://100.72.60.100:8443",
        "tls_trust_pool file /relay/tls/ca.crt",
        "tls_server_name 100.72.60.100",
        "respond 404",
        "output discard",
    ):
        assert required in config
    for forbidden in (
        "tls_insecure_skip_verify",
        "trusted_proxies",
        "header_up",
        "Authorization",
        "debug",
        "file_server",
        "acme",
        "dns",
        "handle_path",
    ):
        assert forbidden not in config
    assert config.count("reverse_proxy") == 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("service_ip", "http://10.43.17.29"),
        ("service_ip", "https://10.43.17.29"),
        ("service_ip", "control.example"),
        ("service_ip", "::1"),
        ("service_ip", "0.0.0.0"),  # noqa: S104 — rejection fixture
        ("service_ip", "127.0.0.1"),
        ("service_ip", "224.1.2.3"),
        ("service_ip", 123),
        ("upstream_ip", "https://100.72.60.100:8443"),
        ("upstream_ip", "control.example"),
        ("upstream_ip", "100.72.60.100\nrespond 200"),
        ("upstream_ip", "::1"),
        ("upstream_ip", "127.0.0.1"),
        ("upstream_ip", "255.255.255.255"),
        ("upstream_port", 0),
        ("upstream_port", 65536),
        ("upstream_port", True),
        ("upstream_port", "8443"),
        ("listen_port", 443),
        ("listen_port", True),
        ("image", "caddy:latest"),
        ("image", "caddy:2.11.7-alpine"),
        ("image", "other@sha256:" + "a" * 64),
        ("image", IMAGE + "\n"),
        ("tls_secret", "cert\nPRIVATE KEY"),
        ("tls_secret", "../tls"),
        ("tls_secret", "Bad_Name"),
        ("tls_secret", "a..b"),
        ("tls_secret", ""),
        ("node_name", "node-a\nmalicious"),
        ("node_name", "a..b"),
        ("node_name", "a" * 32 + "." + "b" * 32),
        ("node_name", ""),
        ("node_cidrs", []),
        ("node_cidrs", ["0.0.0.0/0"]),
        ("node_cidrs", ["100.72.60.0/24"]),
        ("node_cidrs", ["::/0"]),
        ("node_cidrs", ["100.72.60.100"]),
        ("node_cidrs", ["127.0.0.1/32"]),
        ("node_cidrs", ["100.72.60.100/32", "100.72.60.100/32"]),
        ("node_cidrs", "100.72.60.100/32"),
        ("node_cidrs", [1]),
        ("tls_skip_verify", True),
        ("control_url", "http://control.example"),
        ("token", "must-never-be-rendered"),
        ("tls_key", "PRIVATE KEY"),
    ],
)
def test_rejects_unreviewed_or_injectable_options(key: str, value: Any) -> None:
    data = options()
    data[key] = value
    with pytest.raises(ValueError):
        render(data)


@pytest.mark.parametrize("key", list(options()))
def test_rejects_missing_required_options(key: str) -> None:
    data = options()
    del data[key]
    with pytest.raises(ValueError):
        render(data)


@pytest.mark.parametrize("value", [None, [], "options"])
def test_rejects_nonmapping_input(value: Any) -> None:
    with pytest.raises(ValueError):
        render(value)


def test_render_is_pure_and_upstream_port_is_independently_scoped() -> None:
    data = options()
    data.update(upstream_port=9443, listen_port=8443)
    original = copy.deepcopy(data)
    rendered = render(data)
    assert data == original
    assert rendered == render(data)
    items = {item["kind"]: item for item in rendered["items"]}
    assert "https://100.72.60.100:9443" in items["ConfigMap"]["data"]["Caddyfile"]
    assert items["NetworkPolicy"]["spec"]["egress"][0]["ports"][0]["port"] == 9443
    assert items["Service"]["spec"]["ports"][0]["port"] == 8443


def test_readme_documents_service_ip_reservation_and_ip_san_trust() -> None:
    readme = ROOT / "infra/runtime-v2/control-relay.README.md"
    assert readme.is_file(), "operator IP SAN and reservation instructions are missing"
    for required in ("IP SAN", "reserve", "server.crt", "server.key", "ca.crt", "no domain"):
        assert required in readme.read_text()


def test_cli_outputs_manifest_or_generic_error_without_secret_echo(tmp_path: Path) -> None:
    import subprocess
    import sys

    source = tmp_path / "options.json"
    source.write_text(json.dumps(options()))
    command = [sys.executable, str(RENDERER), "--options", str(source)]
    result = subprocess.run(  # noqa: S603 — fixed local renderer
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == render(options())
    source.write_text(json.dumps({**options(), "token": "must-never-echo-this-value"}))
    result = subprocess.run(  # noqa: S603 — fixed local renderer
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "invalid control relay options\n"


def test_pinned_caddy_adapts_and_validates_actual_rendered_config() -> None:
    import datetime
    import ipaddress
    import shutil
    import subprocess
    import tempfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip(
            "Docker unavailable; pinned Caddy verification remains an operator prerequisite"
        )
    inspect = subprocess.run(  # noqa: S603 — fixed pinned local image
        [docker, "image", "inspect", IMAGE],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if inspect.returncode:
        pytest.skip("exact pinned Caddy image not cached; test never pulls an image")
    with tempfile.TemporaryDirectory(prefix="control-relay-fixture-") as directory:
        fixture = Path(directory)
        fixture.chmod(0o755)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-control-relay")])
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.IPAddress(ipaddress.IPv4Address(options()["service_ip"])),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        for filename in ("server.crt", "ca.crt"):
            (fixture / filename).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (fixture / "server.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (fixture / "Caddyfile").write_text(objects()["ConfigMap"]["data"]["Caddyfile"])
        for path in fixture.iterdir():
            path.chmod(0o444)  # ephemeral synthetic fixture key only; never live material
        base = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,exec,size=64m,mode=1777",  # noqa: S108 — isolated container tmpfs
            "--tmpfs",
            "/data:rw,size=16m,uid=1000,gid=1000",
            "--env",
            "XDG_CONFIG_HOME=/tmp/config",
            "--env",
            "XDG_DATA_HOME=/data",
            "--volume",
            f"{fixture}:/relay/tls:ro",
            IMAGE,
        ]
        command = objects()["Deployment"]["spec"]["template"]["spec"]["containers"][0]["command"]
        version = subprocess.run(  # noqa: S603 — pinned image, network none
            [*base, *command, "version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout.startswith("v2.11.7 ")
        adapted = subprocess.run(  # noqa: S603 — pinned image, network none
            [
                *base,
                *command,
                "adapt",
                "--config",
                "/relay/tls/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert adapted.returncode == 0, adapted.stderr
        config = json.loads(adapted.stdout)
        assert config["admin"]["disabled"] is True
        assert config["admin"]["config"]["persist"] is False
        server = config["apps"]["http"]["servers"]["srv0"]
        assert server["listen"] == [":8443"]
        assert server["protocols"] == ["h1", "h2"]
        assert server["automatic_https"]["disable"] is True
        rendered = json.dumps(config)
        for expected in (
            '"provider": "file"',
            '"pem_files": ["/relay/tls/ca.crt"]',
            '"server_name": "100.72.60.100"',
            '"/runner-api/v1/*"',
            '"status_code": 404',
        ):
            assert expected in rendered
        assert '"insecure_skip_verify"' not in rendered
        validated = subprocess.run(  # noqa: S603 — provisions real TLS fixture offline
            [
                *base,
                *command,
                "validate",
                "--config",
                "/relay/tls/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert validated.returncode == 0, validated.stderr
        assert "Valid configuration" in validated.stdout
