"""OpenClaw 接入包（V2 OC-2）。

依据方案第 6.2 节与契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3 节。

每次生成都签发新的实例凭证；令牌、客户端证书私钥只出现在这个 zip 里，工作台只存哈希与指纹。

zip 里的文件：

| 路径 | 权限 | 用途 |
|---|---|---|
| `myworkspace.env` | 600 | 实例凭证、各接入地址、Gateway 令牌（OpenClaw 从进程环境读取 ${VAR}） |
| `state/openclaw.json` | 600 | OpenClaw 配置：加固基线、exec 密钥源、MCP、openim 渠道 |
| `bin/myworkspace-secret-resolver` | 755 | exec SecretRef 的取密钥程序 |
| `bin/myworkspace-openclaw-start` | 755 | 启动包装：取 IM 令牌、上报版本，再启动 OpenClaw |
| `compose.yaml` | 644 | OpenClaw 服务器上的 Compose（锁定镜像，不开放端口） |
| `CHECKLIST.md`、`README.md` | 644 | 部署检查清单与说明 |
| `tls/client.crt`、`tls/client.key` | 644 / 600 | 外网模式：本机隧道出示的客户端证书 |

模板文件放在 `app/agent_bundle/files/`：API 镜像只包含 services/api，
所以不放在仓库的 infra/openclaw。
"""

import io
import json
import secrets
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from typing import Any

OPENCLAW_VERSION = "2026.9.36"
PLUGIN_VERSION = "0.3.3"
# 外网模式的本机隧道（OC-2 第 4 部分核验：client 模式的 --listen / --target / --cert / --key）
GHOSTUNNEL_IMAGE = (
    "ghostunnel/ghostunnel:v1.11.4"
    "@sha256:e5248233863aadd36383a11c92360f2981141881ef05e38360a6ada8630d36b1"
)
_FILES = resources.files("app.agent_bundle") / "files"


@dataclass(frozen=True)
class BundleInput:
    instance_id: str
    instance_name: str
    network_mode: str
    token: str
    base_url: str
    whitelist: list[str]
    client_cert_pem: str | None = None
    client_key_pem: str | None = None
    # 外网模式：本机隧道转发的目标（工作台外网入口的 主机:端口）与本机监听端口
    tunnel_target: str | None = None
    tunnel_port: int = 18443


def _read(name: str) -> str:
    return (_FILES / name).read_text(encoding="utf-8")


def _openclaw_json(whitelist: list[str]) -> str:
    config: dict[str, Any] = json.loads(_read("openclaw.json.template"))
    config["_comment"] = (
        "My Workspace 接入包生成。${VAR} 由 OpenClaw 从进程环境（myworkspace.env）读取；"
        "不要把明文写进本文件。模型服务商按实际使用的那一家修改，密钥 ID 与工作台密钥库的授权一致。"
    )
    account = config["channels"]["openim"]["accounts"]["default"]
    account["inboundWhitelist"] = whitelist
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def _tunnel_host(data: BundleInput) -> str | None:
    return data.tunnel_target.rsplit(":", 1)[0] if data.tunnel_target else None


def _env(data: BundleInput) -> str:
    base = data.base_url.rstrip("/")
    ws = (
        "wss://" + base[len("https://") :]
        if base.startswith("https://")
        else ("ws://" + base[len("http://") :])
    )
    lines = [
        f"# My Workspace 实例「{data.instance_name}」（{data.instance_id}）的接入配置。",
        "# 权限 600；里面有实例凭证，泄露后到工作台的实例页吊销并重新生成接入包。",
        "# 网络模式："
        + ("内网" if data.network_mode == "intranet" else "外网（经本机隧道，双向证书）"),
        f"MYWS_INSTANCE_ID={data.instance_id}",
        f"MYWS_INSTANCE_TOKEN={data.token}",
        f"MYWS_AGENT_API={base}/agent-api",
        f"MYWS_MCP_URL={base}/mcp",
        *(
            [
                "# 外网模式：这个域名在 OpenClaw 容器里解析到 127.0.0.1"
                "（compose.yaml 的 extra_hosts），",
                "# 请求经本机隧道（tunnel 服务，双向证书）转发；明文只走本机回环",
                f"MYWS_LOCAL_TUNNEL_HOST={_tunnel_host(data)}",
            ]
            if data.tunnel_target
            else []
        ),
        f"OPENIM_WS_ADDR={ws}/im/ws",
        f"OPENIM_API_ADDR={base}/im/api",
        "# OpenClaw Gateway 自己的令牌（只在本机使用）",
        f"OPENCLAW_GATEWAY_TOKEN={secrets.token_hex(32)}",
        "# 启动包装脚本上报给工作台的版本（与 compose.yaml 锁定的镜像、安装的插件一致）",
        f"MYWS_OPENCLAW_VERSION={OPENCLAW_VERSION}",
        f"MYWS_PLUGIN_VERSION={PLUGIN_VERSION}",
    ]
    return "\n".join(lines) + "\n"


def _readme(data: BundleInput, generated_at: datetime) -> str:
    public = data.network_mode == "public"
    steps = [
        f"# 接入包：{data.instance_name}",
        "",
        f"- 实例 ID：`{data.instance_id}`；生成时间：{generated_at.isoformat(timespec='seconds')}",
        f"- 网络模式：{'外网（本机隧道 + 双向证书）' if public else '内网'}",
        f"- 工作台接入地址：`{data.base_url}`",
        "",
        "## 部署",
        "",
        "1. 把整个目录放到 OpenClaw 服务器上，按 `CHECKLIST.md` 设置属主与权限；",
        "2. 按 `CHECKLIST.md` 安装 `openclaw-channel` 插件，"
        "并修改 `state/openclaw.json` 里的模型服务商；",
    ]
    if public:
        steps.append(
            "3. 启动本机隧道：把 `tls/client.crt`、`tls/client.key` 交给隧道程序，"
            "在 127.0.0.1 上转发到工作台的外网入口（隧道配置见工作台文档，OC-2 第 4 部分）；"
        )
    steps += [
        f"{4 if public else 3}. `docker compose up -d`，冷启动要 2 ~ 4 分钟；",
        f"{5 if public else 4}. 在工作台的实例页确认状态变为“在线”，并在 IM 里私聊它。",
        "",
        "重新生成接入包会签发新凭证：旧凭证默认再保留 24 小时，到期前换上新的接入包并重启。",
        "",
    ]
    return "\n".join(steps)


def _compose(data: BundleInput) -> str:
    text = _read("compose.yaml")
    host = _tunnel_host(data)
    if not data.tunnel_target or not host:
        return text.replace("    # @PUBLIC_OPENCLAW@\n", "").replace("# @PUBLIC_SERVICES@\n", "")
    openclaw = (
        "    # 工作台外网入口的域名在容器里指向本机隧道\n"
        f'    extra_hosts: ["{host}:127.0.0.1"]\n'
        "    depends_on: [tunnel]\n"
    )
    tunnel = f"""
  # 本机隧道：在 127.0.0.1:{data.tunnel_port} 接收明文，
  # 用 tls/ 里的客户端证书（双向证书）转发到工作台外网入口。
  # 校验服务器证书用系统信任库；外网入口用私有 CA 时，加上 --cacert 指向那张 CA 证书
  tunnel:
    image: {GHOSTUNNEL_IMAGE}
    restart: unless-stopped
    user: "1000:1000"
    network_mode: host
    command:
      - client
      - --listen=127.0.0.1:{data.tunnel_port}
      - --target={data.tunnel_target}
      - --cert=/tls/client.crt
      - --key=/tls/client.key
    volumes:
      - ./tls:/tls:ro
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
"""
    return text.replace("    # @PUBLIC_OPENCLAW@\n", openclaw).replace(
        "# @PUBLIC_SERVICES@\n", tunnel
    )


def _add(zf: zipfile.ZipFile, name: str, content: str, mode: int, when: datetime) -> None:
    info = zipfile.ZipInfo(name, date_time=when.timetuple()[:6])
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, content)


def build(data: BundleInput, *, now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _add(zf, "myworkspace.env", _env(data), 0o600, now)
        _add(zf, "state/openclaw.json", _openclaw_json(data.whitelist), 0o600, now)
        for script in ("myworkspace-secret-resolver", "myworkspace-openclaw-start"):
            _add(zf, f"bin/{script}", _read(f"bin/{script}"), 0o755, now)
        _add(zf, "compose.yaml", _compose(data), 0o644, now)
        _add(zf, "CHECKLIST.md", _read("CHECKLIST.md"), 0o644, now)
        _add(zf, "README.md", _readme(data, now), 0o644, now)
        if data.client_cert_pem and data.client_key_pem:
            _add(zf, "tls/client.crt", data.client_cert_pem, 0o644, now)
            _add(zf, "tls/client.key", data.client_key_pem, 0o600, now)
    return buffer.getvalue()


def filename_for(instance_id: str) -> str:
    return f"myworkspace-agent-{instance_id[:8]}.zip"
