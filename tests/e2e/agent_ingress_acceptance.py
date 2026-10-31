#!/usr/bin/env python3
"""OpenClaw 接入入口验收（T-0054 OC-2 第 4 部分）：Caddy 接入入口 + 本机隧道 + 双向证书，全栈。

前提：
- Compose 全栈已启动并迁移，`docker compose --profile agents up -d agent-gateway` 已启动接入入口；
- api 配置了 AGENT_INTRANET_URL / AGENT_PUBLIC_URL（与下面的 --intranet / --public 一致）；
- agent-gateway 的 /certs 里有 intranet.crt/key、public.crt/key，由 --server-ca 这张 CA 签发；
- infra/caddy/agent-ca/agent-ca.pem 是 `make agent-ca` 导出的内部 CA 证书。

检查（方案第 4.2 节、第 8 节场景 6、7）：
1. 内网入口：实例令牌取到密钥；不在白名单的路径 404；客户端伪造的入口请求头被覆盖；
   外网令牌在内网入口无效；
2. 外网入口：不带客户端证书、带别的 CA 签发的证书，TLS 握手失败；
3. 外网入口经本机隧道（ghostunnel 客户端 + 接入包里的客户端证书）：取到密钥、心跳成功，
   API 按 Caddy 传来的指纹认出证书（证明 Caddy 的指纹格式与工作台一致）；
   /mcp 经隧道到达 API；内网令牌经外网入口无效；吊销证书后立即 401；
4. 同一网络里的其他容器直接连 API、伪造入口请求头：401。

只依赖 Python 标准库与 docker；用法：
  python3 tests/e2e/agent_ingress_acceptance.py --server-ca <CA 证书> \\
      [--intranet intranet.example.internal:8443] [--public agent.example.com:9443]
"""

import argparse
import http.client
import io
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from e2e_support import ROOT, Client, Report, default_base_url, parse_base_url
from seed import create_user

PASSWORD = "e2e agent ingress passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
GHOSTUNNEL = (
    "ghostunnel/ghostunnel:v1.11.4"
    "@sha256:e5248233863aadd36383a11c92360f2981141881ef05e38360a6ada8630d36b1"
)
TUNNEL_PORT = 18443
SECRET_VALUE = "sk-ingress-" + uuid.uuid4().hex


def https_request(
    connect: str,
    hostport: str,
    method: str,
    path: str,
    *,
    cafile: str,
    token: str | None = None,
    body: object = None,
    headers: dict[str, str] | None = None,
    cert: tuple[str, str] | None = None,
) -> tuple[int, str]:
    """连到 connect（发布端口的地址），SNI 与 Host 用 hostport 里的域名。"""
    host, port = hostport.rsplit(":", 1)
    context = ssl.create_default_context(cafile=cafile)
    if cert:
        context.load_cert_chain(*cert)
    sock = socket.create_connection((connect, int(port)), timeout=10)
    tls = context.wrap_socket(sock, server_hostname=host)
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.sock = tls
    return _send(conn, hostport, method, path, token, body, headers)


def plain_request(
    hostport: str, method: str, path: str, *, token: str | None, body: object = None
) -> tuple[int, str]:
    """经本机隧道：连 127.0.0.1，Host 是外网入口的域名（Caddy 要求 SNI 与 Host 一致）。"""
    host, port = hostport.rsplit(":", 1)
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
    return _send(conn, hostport, method, path, token, body, None)


def _send(
    conn: http.client.HTTPConnection,
    hostport: str,
    method: str,
    path: str,
    token: str | None,
    body: object,
    headers: dict[str, str] | None,
) -> tuple[int, str]:
    all_headers = {"Host": hostport, **(headers or {})}
    if token:
        all_headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, data, all_headers)
        response = conn.getresponse()
        return response.status, response.read().decode(errors="replace")
    finally:
        conn.close()


def handshake_fails(connect: str, hostport: str, cafile: str, cert: tuple[str, str] | None) -> bool:
    """外网入口要求客户端证书：没有或不是内部 CA 签发的，握手（或第一个请求）失败。"""
    try:
        status, _ = https_request(connect, hostport, "GET", "/agent-api/v1/heartbeat", cafile=cafile, cert=cert)
    except (ssl.SSLError, ConnectionError, OSError):
        return True
    return status == 0


def bundle(client: Client, workspace: str, instance: dict) -> dict[str, str]:
    req_headers = {"X-Workspace-Id": workspace}
    current = client.request("GET", f"/api/v1/agents/instances/{instance['id']}", headers=req_headers)
    response_raw = client.opener.open(  # 接入包是 zip，不走 JSON 的 request()
        _zip_request(client, workspace, instance["id"], current.json()["version"])
    )
    with zipfile.ZipFile(io.BytesIO(response_raw.read())) as zf:
        return {name: zf.read(name).decode() for name in zf.namelist()}


def _zip_request(client: Client, workspace: str, instance_id: str, version: int):  # type: ignore[no-untyped-def]
    import urllib.request

    return urllib.request.Request(  # noqa: S310 -- base_url 已限定为 http(s)
        f"{client.base_url}/api/v1/agents/instances/{instance_id}/bundle",
        data=json.dumps({"version": version, "previous": "keep_24h"}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Workspace-Id": workspace,
            "Origin": client.origin,
            "X-CSRF-Token": client.cookie("ws_csrf") or "",
        },
    )


def env_of(files: dict[str, str], name: str) -> str:
    for line in files["myworkspace.env"].splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise KeyError(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=default_base_url())
    parser.add_argument("--intranet", default="intranet.example.internal:8443")
    parser.add_argument("--public", default="agent.example.com:9443")
    parser.add_argument("--connect", default="127.0.0.1", help="接入入口发布端口所在的地址")
    parser.add_argument("--server-ca", required=True, help="签发接入入口服务器证书的 CA")
    args = parser.parse_args()
    base = parse_base_url(args.base_url)
    report = Report()
    cafile = str(Path(args.server_ca).resolve())

    email = f"ingress-{uuid.uuid4().hex[:8]}@e2e.example.com"
    _, workspace, _ = create_user(email, "接入验收", PASSWORD)
    owner = Client(base)
    login = owner.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if login.status != 200:
        sys.exit(f"登录失败：{login.summary}")
    headers = {"X-Workspace-Id": workspace}

    secret = owner.request(
        "POST", "/api/v1/agents/secrets", {"name": "OpenAI", "value": SECRET_VALUE}, headers=headers
    ).json()
    instances = {}
    for mode in ("intranet", "public"):
        created = owner.request(
            "POST",
            "/api/v1/agents/instances",
            {"name": f"验收 {mode}", "network_mode": mode},
            headers=headers,
        ).json()
        owner.request(
            "PUT",
            f"/api/v1/agents/instances/{created['id']}/secret-grants",
            {"version": created["version"], "grants": [{"secret_id": secret["id"], "ref_id": "openai_api_key"}]},
            headers=headers,
        )
        instances[mode] = created
    intranet = bundle(owner, workspace, instances["intranet"])
    public = bundle(owner, workspace, instances["public"])
    intranet_token = env_of(intranet, "MYWS_INSTANCE_TOKEN")
    public_token = env_of(public, "MYWS_INSTANCE_TOKEN")
    report.check(
        "接入包地址：内网直连入口，外网经本机隧道（公网域名）",
        env_of(intranet, "MYWS_AGENT_API") == f"https://{args.intranet}/agent-api"
        and env_of(public, "MYWS_AGENT_API")
        == f"http://{args.public.rsplit(':', 1)[0]}:{TUNNEL_PORT}/agent-api",
    )

    # ---------------------------------------------------------------- 1. 内网入口
    resolve = {"ids": ["openai_api_key"]}
    status, text = https_request(
        args.connect, args.intranet, "POST", "/agent-api/v1/secrets/resolve", cafile=cafile, token=intranet_token, body=resolve
    )
    report.check(
        "内网入口：实例令牌取到密钥",
        status == 200 and json.loads(text).get("values", {}).get("openai_api_key") == SECRET_VALUE,
        f"{status}",
    )
    status, text = https_request(args.connect, args.intranet, "GET", "/api/v1/me", cafile=cafile)
    report.check("内网入口：不在白名单的路径 404（不转发管理员接口）", status == 404 and text == "", f"{status}")
    status, _ = https_request(
        args.connect,
        args.intranet,
        "POST",
        "/agent-api/v1/secrets/resolve",
        cafile=cafile,
        token=public_token,
        body=resolve,
        headers={"X-MyWS-Ingress": "public", "X-MyWS-Client-Cert-SHA256": "00" * 32},
    )
    report.check("内网入口：外网令牌 + 伪造的入口与指纹请求头，仍然 401", status == 401, f"{status}")

    # ---------------------------------------------------------------- 2. 外网入口的双向证书
    report.check(
        "外网入口：不带客户端证书，握手失败",
        handshake_fails(args.connect, args.public, cafile, None),
    )
    with tempfile.TemporaryDirectory() as tmp:
        foreign_key, foreign_crt = Path(tmp, "f.key"), Path(tmp, "f.crt")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-keyout", str(foreign_key), "-out", str(foreign_crt), "-days", "1",
             "-subj", "/CN=foreign"],
            check=True, capture_output=True,
        )
        report.check(
            "外网入口：别的 CA 签发的客户端证书，握手失败",
            handshake_fails(args.connect, args.public, cafile, (str(foreign_crt), str(foreign_key))),
        )

    # ---------------------------------------------------------------- 3. 外网入口经本机隧道
    host = args.public.rsplit(":", 1)[0]
    tunnel_hostport = f"{host}:{TUNNEL_PORT}"
    with tempfile.TemporaryDirectory() as tls_dir:
        Path(tls_dir, "client.crt").write_text(public["tls/client.crt"])
        Path(tls_dir, "client.key").write_text(public["tls/client.key"])
        Path(tls_dir, "server-ca.pem").write_text(Path(cafile).read_text())
        name = f"e2e-tunnel-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name, "--network", "host",
             "--add-host", f"{host}:{args.connect}", "-v", f"{tls_dir}:/tls:ro", GHOSTUNNEL,
             "client", f"--listen=127.0.0.1:{TUNNEL_PORT}", f"--target={args.public}",
             "--cert=/tls/client.crt", "--key=/tls/client.key", "--cacert=/tls/server-ca.pem"],
            check=True, capture_output=True,
        )
        try:
            for _ in range(30):
                try:
                    socket.create_connection(("127.0.0.1", TUNNEL_PORT), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.5)
            status, text = plain_request(tunnel_hostport, "POST", "/agent-api/v1/secrets/resolve", token=public_token, body=resolve)
            report.check(
                "外网入口经隧道：取到密钥（双向证书 + 实例令牌）",
                status == 200 and json.loads(text).get("values", {}).get("openai_api_key") == SECRET_VALUE,
                f"{status}",  # 不打印响应：里面是密钥
            )
            status, _ = plain_request(
                tunnel_hostport, "POST", "/agent-api/v1/heartbeat", token=public_token,
                body={"openclaw_version": "2026.9.36", "plugin_version": "0.3.3"},
            )
            report.check("外网入口经隧道：心跳", status == 200, f"{status}")
            status, text = plain_request(tunnel_hostport, "POST", "/mcp", token=public_token, body={})
            report.check(
                "外网入口经隧道：/mcp 到达 API（MCP 服务器在 OC-5 挂载，现在是 API 的 404）",
                status == 404 and '"error"' in text,
                f"{status}",
            )
            status, _ = plain_request(tunnel_hostport, "POST", "/agent-api/v1/secrets/resolve", token=intranet_token, body=resolve)
            report.check("外网入口经隧道：内网令牌无效", status == 401, f"{status}")

            detail = owner.request("GET", f"/api/v1/agents/instances/{instances['public']['id']}", headers=headers).json()
            cert = next(c for c in detail["credentials"] if c["kind"] == "client_cert")
            report.check(
                "API 按 Caddy 传来的指纹认出了客户端证书（{http.request.tls.client.fingerprint} 与工作台格式一致）",
                cert["last_used_at"] is not None,
            )
            owner.request(
                "POST",
                f"/api/v1/agents/instances/{detail['id']}/credentials/{cert['id']}/revoke",
                {"version": detail["version"]},
                headers=headers,
            )
            status, _ = plain_request(tunnel_hostport, "POST", "/agent-api/v1/secrets/resolve", token=public_token, body=resolve)
            report.check("吊销客户端证书后立即 401（证书本身仍有效，按指纹拒绝）", status == 401, f"{status}")
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    # ---------------------------------------------------------------- 4. 绕过接入入口、伪造请求头
    image = subprocess.run(
        ["docker", "compose", "images", "api", "--format", "json"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    repo = json.loads(image)[0]
    forged = {
        "Authorization": f"Bearer {intranet_token}",
        "Content-Type": "application/json",
        "X-MyWS-Ingress": "intranet",
    }
    probe = (
        "import json,sys,urllib.request,urllib.error\n"
        "r=urllib.request.Request('http://api:8000/agent-api/v1/secrets/resolve',"
        "data=json.dumps({'ids':['openai_api_key']}).encode(),method='POST',"
        "headers=json.loads(sys.argv[1]))\n"
        "try:\n print(urllib.request.urlopen(r,timeout=5).status)\n"
        "except urllib.error.HTTPError as e:\n print(e.code)\n"
    )
    api_id = subprocess.run(
        ["docker", "compose", "ps", "-q", "api"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    networks = subprocess.run(
        ["docker", "inspect", api_id, "--format", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    network = next(n for n in networks if n.endswith("_frontend"))
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", network, "--entrypoint", "python",
         f"{repo['Repository']}:{repo['Tag']}", "-c", probe, json.dumps(forged)],
        capture_output=True, text=True,
    )
    report.check(
        "同一网络里的其他容器直接连 API、伪造入口请求头：401",
        out.stdout.strip() == "401",
        out.stdout.strip() or out.stderr.strip()[-200:],
    )
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
