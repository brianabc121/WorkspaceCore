"""Numeric entry addresses retain certificate verification without a DNS name."""

import hashlib
import ipaddress
import runpy
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[4]
public_checks = runpy.run_path(str(ROOT / "infra/runtime-v2/acceptance_two_nodes.py"))[
    "public_checks"
]


@pytest.mark.parametrize("ip_san", [True, False])
def test_numeric_entry_requires_matching_ip_san(tmp_path: Path, ip_san: bool) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    san: x509.GeneralName = (
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")) if ip_san else x509.DNSName("localhost")
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=5))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "certificate.pem", tmp_path / "private.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server_errors: list[Exception] = []
    listener = socket.socket()
    for port in range(20000, 30000):
        try:
            listener.bind(("127.0.0.1", port))
            break
        except OSError:
            continue
    else:
        listener.close()
        pytest.fail("no free reviewed-range port for local TLS verification")
    listener.listen(1)
    listener.settimeout(5)

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                try:
                    with context.wrap_socket(connection, server_side=True) as secured:
                        secured.sendall(secured.recv(128))
                except ssl.SSLError:
                    if ip_san:
                        raise
        except Exception as error:
            server_errors.append(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    # The public-source identity here is synthetic. This test proves local IP
    # TLS behavior only; the harness still requires independent public evidence.
    plan: dict[str, Any] = {
        "host": "127.0.0.1",
        "server_name": "127.0.0.1",
        "port": port,
        "ca_file": str(cert_file),
        "external_source_expected": "1.1.1.1",
        "source_cidrs": ["1.1.1.1/32"],
        "reviewed_config_sha256": "a" * 64,
    }
    try:
        if ip_san:
            result = public_checks(plan, "allowed")
            assert result["public_source_probe"] == "connected"
            assert (
                result["tls_certificate_sha256"]
                == hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
            )
            assert result["combined_acceptance"].startswith("pending independent")
        else:
            with pytest.raises(ssl.SSLCertVerificationError, match="IP address mismatch"):
                public_checks(plan, "allowed")
    finally:
        thread.join(timeout=6)
        listener.close()
    assert not thread.is_alive()
    assert not server_errors
