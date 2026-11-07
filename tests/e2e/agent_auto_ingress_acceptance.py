#!/usr/bin/env python3
"""OpenClaw 接入入口自动配置的验收（T-0088）：用原样的接入包连回工作台。

前提：
- Compose 全栈已启动并迁移；`deploy.sh connect` 的“OpenClaw 接入入口”一步已经完成：
  入口证书由工作台内部 CA 签发，`.env` 里有 AGENT_PUBLIC_HOST / PORT / URL / ADDRESS 与
  AGENT_PUBLIC_INTERNAL_CA=true，api 已经读到这些变量，接入入口（agent-gateway）在运行；
- 以 root 运行：接入包目录要交给容器里的 uid 1000（与 CHECKLIST.md 的做法一致）。

检查：
1. 外网接入包附带内部 CA（tls/ca.pem，与 make agent-ca 导出的一致），隧道带 --cacert；
   入口主机名不在公网 DNS 里时，隧道容器把它映射到 AGENT_PUBLIC_ADDRESS；
2. 不改接入包，只启动其中的 tunnel 服务（ghostunnel，按主机名发 SNI、用 tls/ca.pem 校验入口；
   它与 net 服务共用这个实例自己的网络命名空间）：在这个命名空间里经本机隧道取到密钥、心跳成功
   ——OpenClaw 服务器上不需要任何手动配置；
3. 入口证书：内部 CA 签发、SAN 是入口主机名。

只依赖 Python 标准库与 docker；用法：
  sudo python3 tests/e2e/agent_auto_ingress_acceptance.py
"""

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from agent_ingress_acceptance import bundle, env_of
from e2e_support import ROOT, Client, Report, default_base_url, parse_base_url
from seed import create_user

PASSWORD = "e2e auto ingress passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
SECRET_VALUE = "sk-auto-ingress-" + uuid.uuid4().hex


def dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def server_certificate(address: str, host: str, port: int, cafile: str) -> dict:  # type: ignore[type-arg]
    """按主机名连入口、用内部 CA 校验；返回服务器证书（外网入口要求客户端证书，TLS 1.3 下握手先完成）。"""
    context = ssl.create_default_context(cafile=cafile)
    with socket.create_connection((address, port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            return tls.getpeercert() or {}


def netns_request(
    compose_cmd: list[str], url: str, method: str, *, token: str, body: object
) -> tuple[int, str]:
    """在这个实例自己的网络命名空间里发请求（经 net 服务里的 node，与 OpenClaw 连本机隧道的路径一样：
    入口域名在 net 的 hosts 里指向 127.0.0.1）。隧道不对宿主开放端口，宿主上连不到。"""
    script = (
        "const [u,m,t,b]=JSON.parse(process.argv[1]);"
        "fetch(u,{method:m,headers:{'Content-Type':'application/json',Authorization:'Bearer '+t},"
        "body:JSON.stringify(b)})"
        ".then(async r=>console.log(JSON.stringify([r.status,(await r.text()).slice(0,2000)])))"
        ".catch(e=>console.log(JSON.stringify([0,String(e)])));"
    )
    out = subprocess.run(
        [*compose_cmd, "exec", "-T", "net", "node", "-e", script, json.dumps([url, method, token, body])],
        capture_output=True, text=True, timeout=60,
    )
    lines = out.stdout.strip().splitlines()
    if not lines:
        return 0, out.stderr.strip()[-300:]
    status, text = json.loads(lines[-1])
    return int(status), str(text)


def main() -> int:
    if os.geteuid() != 0:
        sys.exit("要以 root 运行：接入包目录要交给容器里的 uid 1000")
    base = parse_base_url(default_base_url())
    report = Report()
    env = dotenv()
    host, port = env.get("AGENT_PUBLIC_HOST", ""), int(env.get("AGENT_PUBLIC_PORT") or 9443)
    address = env.get("AGENT_PUBLIC_ADDRESS", "")
    ca_file = ROOT / "infra/caddy/agent-ca/agent-ca.pem"
    report.check(
        "deploy.sh connect 写好了 .env：内部 CA、外网入口地址",
        env.get("AGENT_PUBLIC_INTERNAL_CA") == "true"
        and env.get("AGENT_PUBLIC_URL") == f"https://{host}:{port}"
        and env.get("AGENT_PUBLIC_BIND") == "0.0.0.0",  # noqa: S104 -- 外网入口对公网开放
        json.dumps({k: v for k, v in env.items() if k.startswith("AGENT_PUBLIC_")}, ensure_ascii=False),
    )

    # ---------------------------------------------------------------- 入口证书
    cert = server_certificate(address or "127.0.0.1", host, port, str(ca_file))
    sans = [value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"]
    report.check("入口证书由内部 CA 签发，SAN 是入口主机名", sans == [host], f"{sans}")

    # ---------------------------------------------------------------- 接入包
    email = f"auto-ingress-{uuid.uuid4().hex[:8]}@e2e.example.com"
    _, workspace, _ = create_user(email, "自动接入验收", PASSWORD)
    owner = Client(base)
    login = owner.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if login.status != 200:
        sys.exit(f"登录失败：{login.summary}")
    headers = {"X-Workspace-Id": workspace}
    secret = owner.request(
        "POST", "/api/v1/agents/secrets", {"name": "OpenAI", "value": SECRET_VALUE}, headers=headers
    ).json()
    instance = owner.request(
        "POST", "/api/v1/agents/instances", {"name": "自动接入", "network_mode": "public"}, headers=headers
    ).json()
    owner.request(
        "PUT",
        f"/api/v1/agents/instances/{instance['id']}/secret-grants",
        {"version": instance["version"], "grants": [{"secret_id": secret["id"], "ref_id": "openai_api_key"}]},
        headers=headers,
    )
    files = bundle(owner, workspace, instance)
    token = env_of(files, "MYWS_INSTANCE_TOKEN")
    compose = files["compose.yaml"]
    report.check(
        "接入包附带内部 CA（与 make agent-ca 导出的一致），隧道用它校验入口",
        files.get("tls/ca.pem") == ca_file.read_text(encoding="utf-8")
        and "      - --cacert=/tls/ca.pem\n" in compose
        and f"      - --target={host}:{port}\n" in compose,
    )
    if address:
        report.check(
            "入口主机名不在公网 DNS 里：隧道自己的 hosts 把它映射到 AGENT_PUBLIC_ADDRESS",
            f"{address}\t{host}\n" in files.get("tls/hosts", "")
            and "      - ./tls/hosts:/etc/hosts:ro\n" in compose,
            address,
        )

    # ---------------------------------------------------------------- 原样启动接入包里的隧道
    project = f"e2e-auto-{uuid.uuid4().hex[:6]}"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp, "bundle")
        for name, content in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (target / "tls/client.key").chmod(0o600)
        (target / "myworkspace.env").chmod(0o600)
        for path in [target, *target.rglob("*")]:
            shutil.chown(path, 1000, 1000)
        compose_cmd = ["docker", "compose", "-p", project, "-f", str(target / "compose.yaml")]
        up = subprocess.run([*compose_cmd, "up", "-d", "tunnel"], capture_output=True, text=True)
        report.check("docker compose up -d tunnel（接入包未修改）", up.returncode == 0, up.stderr.strip()[-300:])
        try:
            tunnel = f"http://{host}:{urlsplit(env_of(files, 'MYWS_AGENT_API')).port}"
            status, text = 0, ""
            for _ in range(30):
                status, text = netns_request(
                    compose_cmd, f"{tunnel}/agent-api/v1/secrets/resolve", "POST",
                    token=token, body={"ids": ["openai_api_key"]},
                )
                if status:
                    break
                time.sleep(1)
            report.check(
                "经本机隧道取到密钥（隧道用 tls/ca.pem 校验内部 CA 签发的入口证书）",
                status == 200 and json.loads(text).get("values", {}).get("openai_api_key") == SECRET_VALUE,
                f"{status} {text[:200] if status != 200 else ''}",
            )
            status, _ = netns_request(
                compose_cmd, f"{tunnel}/agent-api/v1/heartbeat", "POST", token=token,
                body={"openclaw_version": "2026.9.36", "plugin_version": "0.3.3"},
            )
            report.check("经本机隧道心跳成功", status == 200, f"{status}")
        finally:
            if report.failures:
                logs = subprocess.run([*compose_cmd, "logs", "--no-color", "tunnel"], capture_output=True, text=True)
                print(logs.stdout[-2000:], logs.stderr[-500:], file=sys.stderr)
            subprocess.run([*compose_cmd, "down", "-v"], capture_output=True, text=True)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
