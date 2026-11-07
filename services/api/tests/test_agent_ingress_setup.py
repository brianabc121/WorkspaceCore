"""OpenClaw 接入入口自动配置（T-0088）：内部 CA 签发入口证书、接入包附带 CA 与地址映射。

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md 第 3 节。
这里不需要数据库：CA 用假的会话生成（ensure_ca 只用 scalar / add / flush）。
"""

import asyncio
import io
import ipaddress
import json
import secrets
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from app import agent_bundle
from app.commands import agent_ca as command
from app.core.config import Settings
from app.domain.agent_ca import (
    SERVER_VALIDITY,
    ensure_ca,
    issue_server_certificate,
    server_certificate_status,
)
from app.domain.credentials import CredentialBox
from app.models import AgentCa

INTERNAL = "agent-gateway.myworkspace.internal"


class _FakeDb:
    """ensure_ca 用到的最少接口：没有在用的 CA，新建的记下来。"""

    def __init__(self) -> None:
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> None:
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None


def _new_ca() -> tuple[AgentCa, CredentialBox]:
    box = CredentialBox(secrets.token_bytes(32))
    ca = asyncio.run(ensure_ca(_FakeDb(), box))  # type: ignore[arg-type]
    return ca, box


@pytest.fixture(scope="module")
def ca_box() -> tuple[AgentCa, CredentialBox]:
    return _new_ca()


# ---------------------------------------------------------------- 签发与检查入口证书


@pytest.mark.parametrize(
    ("host", "san"),
    [
        (INTERNAL, x509.DNSName(INTERNAL)),
        ("WS.Example.COM", x509.DNSName("ws.example.com")),
        ("203.0.113.7", x509.IPAddress(ipaddress.ip_address("203.0.113.7"))),
    ],
)
def test_server_certificate_is_issued_by_the_internal_ca(
    ca_box: tuple[AgentCa, CredentialBox], host: str, san: x509.GeneralName
) -> None:
    ca, box = ca_box
    issued = issue_server_certificate(ca, box, host)
    cert = x509.load_pem_x509_certificate(issued.cert_pem.encode())
    ca_cert = x509.load_pem_x509_certificate(ca.cert_pem.encode())
    cert.verify_directly_issued_by(ca_cert)
    assert issued.ca_pem == ca.cert_pem and "PRIVATE KEY" not in issued.ca_pem
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert list(sans) == [san]
    usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(usage) == [ExtendedKeyUsageOID.SERVER_AUTH]
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert cert.not_valid_after_utc - cert.not_valid_before_utc <= SERVER_VALIDITY + timedelta(
        hours=1
    )
    assert cert.not_valid_after_utc == issued.not_after.replace(microsecond=0)
    # 私钥与证书是一对，EC P-256
    key = serialization.load_pem_private_key(issued.key_pem.encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256r1"
    assert key.public_key().public_numbers() == cert.public_key().public_numbers()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "host", ["", " ", "bad host", "-x.example.com", "a" * 64 + ".com", "x_y.com"]
)
def test_invalid_hosts_are_refused(ca_box: tuple[AgentCa, CredentialBox], host: str) -> None:
    ca, box = ca_box
    with pytest.raises(ValueError, match="入口主机名无效"):
        issue_server_certificate(ca, box, host)


def test_certificate_lifetime_never_outlives_the_ca(ca_box: tuple[AgentCa, CredentialBox]) -> None:
    ca, box = ca_box
    late = ca.not_after - timedelta(days=30)
    issued = issue_server_certificate(ca, box, INTERNAL, now=late)
    assert issued.not_after == ca.not_after


def test_status_says_when_to_reissue(ca_box: tuple[AgentCa, CredentialBox]) -> None:
    ca, box = ca_box
    now = datetime.now(UTC)
    issued = issue_server_certificate(ca, box, INTERNAL, now=now)
    assert server_certificate_status(ca, issued.cert_pem, INTERNAL, now=now) == "ok"
    assert server_certificate_status(ca, issued.cert_pem, INTERNAL.upper(), now=now) == "ok"
    # 主机名变了
    assert server_certificate_status(ca, issued.cert_pem, "ws.example.com", now=now) == "renew"
    # 剩不到 60 天
    soon = issued.not_after - timedelta(days=59)
    assert server_certificate_status(ca, issued.cert_pem, INTERNAL, now=soon) == "renew"
    assert (
        server_certificate_status(ca, issued.cert_pem, INTERNAL, now=soon - timedelta(days=2))
        == "ok"
    )
    # 不是这套 CA 签的（例如手动放进去的证书，或者 CA 换过）
    other, other_box = _new_ca()
    foreign = issue_server_certificate(other, other_box, INTERNAL)
    assert server_certificate_status(ca, foreign.cert_pem, INTERNAL, now=now) == "renew"
    # 无法解析、空文件、主机名无效
    assert server_certificate_status(ca, "", INTERNAL, now=now) == "renew"
    assert server_certificate_status(ca, "not a cert", INTERNAL, now=now) == "renew"
    assert server_certificate_status(ca, issued.cert_pem, "bad host", now=now) == "renew"


# ---------------------------------------------------------------- 命令行


@pytest.fixture
def cli_ca(monkeypatch: pytest.MonkeyPatch) -> AgentCa:
    ca, box = _new_ca()

    async def load() -> tuple[AgentCa, CredentialBox]:
        return ca, box

    monkeypatch.setattr(command, "_load_ca", load)
    return ca


def test_cli_issue_server_prints_json(cli_ca: AgentCa, capsys: pytest.CaptureFixture[str]) -> None:
    assert command.main(["issue-server", "--host", INTERNAL]) == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert set(data) == {"cert", "key", "ca", "not_after"}
    assert data["ca"] == cli_ca.cert_pem and "PRIVATE KEY" in data["key"]
    assert datetime.fromisoformat(data["not_after"]) > datetime.now(UTC) + timedelta(days=700)
    assert err == ""


def test_cli_rejects_bad_host_without_a_traceback(
    cli_ca: AgentCa, capsys: pytest.CaptureFixture[str]
) -> None:
    assert command.main(["issue-server", "--host", "bad host"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "入口主机名无效" in err and "Traceback" not in err


def test_cli_check_server_reads_stdin(
    cli_ca: AgentCa, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert command.main(["issue-server", "--host", INTERNAL]) == 0
    cert = json.loads(capsys.readouterr().out)["cert"]
    for host, expected in ((INTERNAL, "ok"), ("ws.example.com", "renew")):
        monkeypatch.setattr("sys.stdin", io.StringIO(cert))
        assert command.main(["check-server", "--host", host]) == 0
        assert capsys.readouterr().out.strip() == expected


# ---------------------------------------------------------------- 配置


def test_public_address_must_be_an_ip() -> None:
    assert Settings(agent_public_address=" 203.0.113.7 ").agent_public_address == "203.0.113.7"
    assert Settings(agent_public_address="2001:DB8::1").agent_public_address == "2001:db8::1"
    assert Settings(agent_public_address="").agent_public_address == ""
    with pytest.raises(ValueError, match="必须是 IP 地址"):
        Settings(agent_public_address="ws.example.com")
    assert Settings().agent_public_internal_ca is False


# ---------------------------------------------------------------- 接入包


def _bundle(**extra: Any) -> dict[str, tuple[str, int]]:
    data = agent_bundle.BundleInput(
        instance_id="00000000-0000-0000-0000-000000000001",
        instance_name="自动部署",
        network_mode="public",
        token="mwa_test",  # noqa: S106 - 测试值
        base_url=f"http://{INTERNAL}:18443",
        whitelist=["ws_1"],
        client_cert_pem="CLIENT CERT",
        client_key_pem="CLIENT KEY",
        tunnel_target=f"{INTERNAL}:9443",
        **extra,
    )
    with zipfile.ZipFile(io.BytesIO(agent_bundle.build(data))) as zf:
        return {
            info.filename: (zf.read(info).decode(), (info.external_attr >> 16) & 0o777)
            for info in zf.infolist()
        }


def _service(compose: str, name: str) -> str:
    """compose.yaml 里一个服务的文本（到下一个顶格两空格的服务为止）。"""
    block = compose.split(f"\n  {name}:\n", 1)[1]
    lines = []
    for line in block.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            break
        lines.append(line)
    return "\n".join(lines)


def test_bundle_carries_the_internal_ca_and_address_mapping() -> None:
    files = _bundle(server_ca_pem="CA PEM", tunnel_address="203.0.113.7")
    assert files["tls/ca.pem"] == ("CA PEM", 0o644)
    compose = files["compose.yaml"][0]
    tunnel = _service(compose, "tunnel")
    # 每个实例自己的网络命名空间（net 服务），隧道与 OpenClaw 都加入它，不再用宿主网络
    assert '    network_mode: "service:net"' in tunnel
    assert '    network_mode: "service:net"' in _service(compose, "openclaw")
    assert "network_mode: host" not in compose
    # 隧道按主机名发 SNI 并校验证书；主机名不在公网 DNS 里，写在隧道自己的 hosts 里指向工作台服务器
    assert files["tls/hosts"] == (
        "# 本机隧道自己的 hosts（My Workspace 接入包生成，只读挂到隧道容器的 /etc/hosts）\n"
        "127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n"
        f"203.0.113.7\t{INTERNAL}\n",
        0o644,
    )
    assert "      - ./tls/hosts:/etc/hosts:ro" in tunnel and "extra_hosts" not in tunnel
    assert f"      - --target={INTERNAL}:9443\n" in tunnel
    assert "      - --cacert=/tls/ca.pem\n" in tunnel
    assert "      - ./tls:/tls:ro" in tunnel
    # 实例共用的 hosts（net 服务）把入口主机名解析到本机隧道，OpenClaw 用它
    assert f'    extra_hosts: ["{INTERNAL}:127.0.0.1"]' in _service(compose, "net")
    assert "tls/ca.pem 校验" in compose


def test_bundle_without_internal_ca_keeps_the_system_trust_store() -> None:
    files = _bundle()
    assert "tls/ca.pem" not in files
    tunnel = _service(files["compose.yaml"][0], "tunnel")
    assert "extra_hosts" not in tunnel and "--cacert" not in tunnel
    # 有公网 DNS：隧道自己的 hosts 只有 localhost，照常查 DNS
    assert INTERNAL not in files["tls/hosts"][0]


def test_intranet_bundle_never_gets_the_ca() -> None:
    data = agent_bundle.BundleInput(
        instance_id="00000000-0000-0000-0000-000000000002",
        instance_name="内网",
        network_mode="intranet",
        token="mwa_test",  # noqa: S106 - 测试值
        base_url="https://10.0.0.5",
        whitelist=["ws_1"],
        server_ca_pem="CA PEM",
    )
    with zipfile.ZipFile(io.BytesIO(agent_bundle.build(data))) as zf:
        assert "tls/ca.pem" not in zf.namelist()
        assert "--cacert" not in zf.read("compose.yaml").decode()
