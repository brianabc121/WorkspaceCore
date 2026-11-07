"""内部 CA：给外网模式的实例签发客户端证书（V2 OC-2，方案第 6.1 节）。

- 整个部署共用一套 CA（`agent_ca` 表里 retired_at 为 NULL 的那一行），第一次需要时生成；
- CA：EC P-256，有效期 5 年；私钥以 enc:v1 引用保存（用途绑定到这一行），
  明文只在签发时出现在内存里；
- 客户端证书：EC P-256，有效期 90 天，只用于 clientAuth；CN 与 URI SAN 标明实例；
- 吊销**以证书指纹为准**（agent_credentials.cert_fingerprint），不发布 CRL：
  API 每次请求都检查指纹。

Caddy 的外网入口要信任这张 CA 证书：`python -m app.commands.agent_ca export <路径>`
把证书（不含私钥）写出来。

外网入口的服务器证书也可以由这套 CA 签发（T-0088）：`deploy.sh connect` 调用
`python -m app.commands.agent_ca issue-server --host <入口主机名>`，接入包附带 CA 证书，
本机隧道用它校验入口（`AGENT_PUBLIC_INTERNAL_CA=true`）。
"""

import hashlib
import ipaddress
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.credentials import CredentialBox
from app.models import AgentCa, AgentInstance

CA_VALIDITY = timedelta(days=5 * 365)
CLIENT_VALIDITY = timedelta(days=90)
# 入口的服务器证书：2 年；deploy.sh connect 在剩不到 60 天时重签（T-0088）
SERVER_VALIDITY = timedelta(days=730)
SERVER_RENEW_BEFORE = timedelta(days=60)
# 时钟偏差：证书从一小时前开始生效
BACKDATE = timedelta(hours=1)
# 入口主机名：小写的 DNS 名字（每段 1 ~ 63 个字母、数字或连字符），或者 IP 地址
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*$")


def agent_ca_context(ca_id: uuid.UUID) -> str:
    return f"agent_ca:{ca_id}"


def fingerprint_of(cert: x509.Certificate) -> str:
    """SHA-256(DER) 的小写十六进制。

    与 Caddy 的 {http.request.tls.client.fingerprint} 对应（OC-2 第 4 部分核实）。
    """
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


@dataclass(frozen=True)
class ClientCertificate:
    cert_pem: str
    key_pem: str
    ca_pem: str
    fingerprint: str
    not_after: datetime


def _key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


async def active_ca(db: AsyncSession) -> AgentCa | None:
    return await db.scalar(select(AgentCa).where(AgentCa.retired_at.is_(None)))


async def ensure_ca(
    db: AsyncSession, box: CredentialBox, *, now: datetime | None = None
) -> AgentCa:
    """返回在用的 CA；没有时生成一套（在调用方的事务里写入）。"""
    existing = await active_ca(db)
    if existing is not None:
        return existing
    now = now or datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "My Workspace"),
            x509.NameAttribute(NameOID.COMMON_NAME, "My Workspace Agent CA"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(now + CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    ca_id = uuid.uuid4()
    ca = AgentCa(
        id=ca_id,
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_ref=box.seal(_key_pem(key), context=agent_ca_context(ca_id)),
        not_after=now + CA_VALIDITY,
    )
    db.add(ca)
    await db.flush()
    return ca


def issue_client_certificate(
    ca: AgentCa, box: CredentialBox, instance: AgentInstance, *, now: datetime | None = None
) -> ClientCertificate:
    return issue_certificate(
        ca,
        box,
        common_name=instance.im_user_id,
        uri=f"urn:myworkspace:agent-instance:{instance.id}",
        now=now,
    )


def issue_certificate(
    ca: AgentCa,
    box: CredentialBox,
    *,
    common_name: str,
    uri: str,
    now: datetime | None = None,
) -> ClientCertificate:
    """客户端证书：实例与运行服务器（T-0136）共用同一个内部 CA。"""
    now = now or datetime.now(UTC)
    ca_cert, ca_key = _ca_signer(ca, box)
    key = ec.generate_private_key(ec.SECP256R1())
    not_after = min(now + CLIENT_VALIDITY, ca.not_after)
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "My Workspace"),
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),  # type: ignore[arg-type]
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return ClientCertificate(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=_key_pem(key),
        ca_pem=ca.cert_pem,
        fingerprint=fingerprint_of(cert),
        not_after=not_after,
    )


@dataclass(frozen=True)
class ServerCertificate:
    cert_pem: str
    key_pem: str
    ca_pem: str
    not_after: datetime


def _san_for(host: str) -> x509.GeneralName:
    """证书的 SAN：IP 地址或 DNS 名字；都不是时 ValueError。"""
    host = host.strip().lower()
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        if not _HOSTNAME.match(host):
            raise ValueError(f"入口主机名无效：{host!r}") from None
        return x509.DNSName(host)


def _ca_signer(
    ca: AgentCa, box: CredentialBox
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    ca_cert = x509.load_pem_x509_certificate(ca.cert_pem.encode())
    ca_key = serialization.load_pem_private_key(
        box.resolve(ca.key_ref, context=agent_ca_context(ca.id)).get_secret_value().encode(),
        password=None,
    )
    if not isinstance(ca_key, ec.EllipticCurvePrivateKey):
        raise ValueError("内部 CA 的私钥类型不对")
    return ca_cert, ca_key


def issue_server_certificate(
    ca: AgentCa, box: CredentialBox, host: str, *, now: datetime | None = None
) -> ServerCertificate:
    """外网接入入口的服务器证书（T-0088）：EC P-256、serverAuth，SAN 是入口主机名（或 IP）。"""
    san = _san_for(host)
    host = host.strip().lower()
    now = now or datetime.now(UTC)
    ca_cert, ca_key = _ca_signer(ca, box)
    key = ec.generate_private_key(ec.SECP256R1())
    not_after = min(now + SERVER_VALIDITY, ca.not_after)
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "My Workspace"),
                    x509.NameAttribute(NameOID.COMMON_NAME, host[:64]),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),  # type: ignore[arg-type]
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return ServerCertificate(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=_key_pem(key),
        ca_pem=ca.cert_pem,
        not_after=not_after,
    )


def server_certificate_status(
    ca: AgentCa, cert_pem: str, host: str, *, now: datetime | None = None
) -> str:
    """已有的入口证书还能不能用：ok / renew（快到期、主机名不对、不是这套 CA 签发的或无法解析）。"""
    now = now or datetime.now(UTC)
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        ca_cert = x509.load_pem_x509_certificate(ca.cert_pem.encode())
        cert.verify_directly_issued_by(ca_cert)
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        wanted = _san_for(host)
    except Exception:  # 任何解析或校验失败都重签
        return "renew"
    if wanted not in list(sans):
        return "renew"
    if cert.not_valid_after_utc - now < SERVER_RENEW_BEFORE:
        return "renew"
    return "ok"
