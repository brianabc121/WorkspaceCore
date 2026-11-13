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
| `bin/usage-report.mjs` | 755 | 每分钟上报 token 用量（宿主机 crontab 在容器里运行，T-0104） |
| `bin/host-report.mjs` | 755 | 每分钟上报服务器状态：CPU、内存、磁盘、网络的数字（T-0113） |
| `bin/channel-patch.mjs` | 755 | 给插件打补丁：自动回复被拒收时把原因放回会话（T-0120） |
| `bin/wakeup.mjs` | 755 | 每分钟领取到期的唤醒，在容器里跑一轮（T-0121） |
| `bin/stop-runs.mjs` | 755 | 每分钟领取终止令，中止正在运行的会话（T-0126） |
| `bin/model-config.mjs` | 755 | 每分钟取工作台设置的主模型，`config patch` 应用后回报（T-0205） |
| `compose.yaml` | 644 | OpenClaw 服务器上的 Compose（锁定镜像，不开放端口） |
| `CHECKLIST.md`、`README.md` | 644 | 部署检查清单与说明 |
| `setup-reflection.sh`、`reflection-prompt.md` | 755 / 644 | 每晚复盘的定时任务（T-0100） |
| `tls/client.crt`、`tls/client.key` | 644 / 600 | 外网模式：本机隧道出示的客户端证书 |
| `tls/hosts` | 644 | 外网模式：本机隧道自己的 hosts（只读挂到隧道容器的 /etc/hosts） |

一台服务器可以有多个实例（docs/v2/MULTI_OPENCLAW_PLAN.md 第 3 节）：每个实例的目录是
`~/myworkspace-openclaw/<实例短号>/`，Compose 项目名 `myworkspace-openclaw-<实例短号>`，
OpenClaw 与本机隧道 / 取图代理共用这个实例自己的网络命名空间（`net` 服务）。

模板文件放在 `app/agent_bundle/files/`：API 镜像只包含 services/api，
所以不放在仓库的 infra/openclaw。
"""

import hashlib
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
# 接入包的 channel-patch.mjs 打上的补丁：T-0120（回复被拒收时告诉智能体）
# + T-0221（各对话各用一个会话）。两处都打了的实例上报 "0.3.3+myworkspace.2"；
# 只有旧的第一处的上报 "0.3.3+myworkspace.1"（实例页提示重新签发）
PLUGIN_PATCH = "myworkspace.2"
PATCHED_PLUGIN_VERSION = f"{PLUGIN_VERSION}+{PLUGIN_PATCH}"
# 外网模式的本机隧道（OC-2 第 4 部分核验：client 模式的 --listen / --target / --cert / --key）
GHOSTUNNEL_IMAGE = (
    "ghostunnel/ghostunnel:v1.11.4"
    "@sha256:e5248233863aadd36383a11c92360f2981141881ef05e38360a6ada8630d36b1"
)
_FILES = resources.files("app.agent_bundle") / "files"
# bin/ 下的程序（权限 755）；usage-report.mjs 是每分钟的用量上报（T-0104），
# host-report.mjs 是每分钟的服务器状态上报（T-0113），channel-patch.mjs 给插件打补丁（T-0120）
BIN_SCRIPTS = (
    "myworkspace-secret-resolver",
    "myworkspace-openclaw-start",
    "usage-report.mjs",
    "host-report.mjs",
    "channel-patch.mjs",
    "wakeup.mjs",
    "stop-runs.mjs",
    "model-config.mjs",
)


# 实例短号：实例 ID 去掉连字符的前 12 位（目录名、Compose 项目名、定时任务标记都用它）
SHORT_ID_LENGTH = 12
PROJECT_PREFIX = "myworkspace-openclaw-"
# 内存上限至少 256 MiB、CPU 上限 0.1 ~ 256 核（与 agent_instances 的 CHECK 约束一致）
MEM_LIMIT_MIN_MB = 256
MEM_LIMIT_MAX_MB = 1 << 20
CPUS_MIN = 0.1
CPUS_MAX = 256.0


def short_id(instance_id: str) -> str:
    value = instance_id.replace("-", "").lower()
    if len(value) < SHORT_ID_LENGTH or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("实例 ID 不是 UUID")
    return value[:SHORT_ID_LENGTH]


def project_name(instance_id: str) -> str:
    return PROJECT_PREFIX + short_id(instance_id)


def host_salt(workspace_id: str) -> str:
    """服务器指纹（boot_id 的 HMAC）用的盐：每个工作空间一个，固定不变。

    boot_id 本身是 122 位随机数，哈希不能反推；按工作空间加盐，不同工作空间的指纹对不上。
    """
    return hashlib.sha256(f"myworkspace-host-salt\x00{workspace_id}".encode()).hexdigest()


@dataclass(frozen=True)
class BundleModel:
    """受管的主模型配置（T-0205）：models.providers.<provider_key> 与
    agents.defaults.model.primary。

    apiKey 是 exec SecretRef（取密钥程序把 mc_ 开头的 id 发给
    /agent-api/v1/model-credentials/resolve），配置里不出现 Key。
    provider_key 只用小写字母和数字（OpenClaw 运行时把 provider id 转成小写，
    含 / 的 id 能通过校验但运行时找不到，核验记录第 3 节）。"""

    provider_key: str
    api: str
    base_url: str
    model_id: str
    model_name: str
    secret_ref_id: str


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
    # 内网模式、且工作台也有外网入口时：图片地址用的是外网域名（OC-3 ADR 第 15 节），
    # 内网实例要把这个域名指向本机代理，代理（不带客户端证书）转发到内网入口
    image_proxy_host: str | None = None
    image_proxy_target: str | None = None
    # 外网入口用工作台内部 CA 签发的证书时（T-0088）：CA 证书写进 tls/ca.pem，隧道用它校验入口；
    # 入口主机名不在公网 DNS 里时，隧道容器把它映射到这个地址
    server_ca_pem: str | None = None
    tunnel_address: str | None = None
    # 每晚复盘的定时任务（T-0100）：setup-reflection.sh 用的时间与时区
    reflection_cron: str = "0 22 * * *"
    reflection_timezone: str = "Asia/Shanghai"
    # 服务器指纹的盐（host_salt）；空表示不报指纹
    host_salt: str = ""
    # OpenClaw 容器的资源上限（D2：默认不设）
    mem_limit_mb: int | None = None
    cpus: float | None = None
    # 主模型：有绑定时写入受管 provider 与主模型；没有绑定时不写模型段
    model: BundleModel | None = None


def _read(name: str) -> str:
    return (_FILES / name).read_text(encoding="utf-8")


def model_provider(model: BundleModel) -> dict[str, Any]:
    """受管 provider 的配置段（与 bin/model-config.mjs 写入的一致）。

    custom provider 必须有 baseUrl 与 models；models 的每项必须有 id 与 name（核验记录第 3 节）。"""
    return {
        "baseUrl": model.base_url,
        "api": model.api,
        "apiKey": {"source": "exec", "provider": "myworkspace", "id": model.secret_ref_id},
        "models": [{"id": model.model_id, "name": model.model_name}],
    }


def _openclaw_json(whitelist: list[str], model: BundleModel | None = None) -> str:
    # 不能加说明用的键（例如 _comment）：OpenClaw 会把整个配置判为无效
    # （T-0059 用真实 OpenClaw 验收发现）。说明写在 CHECKLIST.md / README.md 里
    config: dict[str, Any] = json.loads(_read("openclaw.json.template"))
    account = config["channels"]["openim"]["accounts"]["default"]
    account["inboundWhitelist"] = whitelist
    if model is not None:
        config["models"] = {"providers": {model.provider_key: model_provider(model)}}
        config.setdefault("agents", {}).setdefault("defaults", {})["model"] = {
            "primary": f"{model.provider_key}/{model.model_id}"
        }
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def _tunnel_host(data: BundleInput) -> str | None:
    return data.tunnel_target.rsplit(":", 1)[0] if data.tunnel_target else None


def mcp_url(data: BundleInput) -> str:
    """MCP 地址。外网模式用本机隧道的字面回环地址（T-0133）：OpenClaw 2026.9.36 的 SSRF 防护只放行
    主机名本身就是回环地址的 MCP 地址，拦下“解析到 127.0.0.1 的域名”（核验记录
    docs/decisions/2026-11-04-agent-messaging-verification.md 6.4）。外网入口接受这个 Host
    （infra/caddy/agent.Caddyfile 的 :9443），双向证书照常由隧道提供。其他请求仍用外网域名。"""
    if data.tunnel_target:
        return f"http://127.0.0.1:{data.tunnel_port}/mcp"
    return data.base_url.rstrip("/") + "/mcp"


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
        *(
            [
                "# MCP 用本机隧道的回环地址：OpenClaw 的 SSRF 防护拦下解析到 127.0.0.1 的域名",
            ]
            if data.tunnel_target
            else []
        ),
        f"MYWS_MCP_URL={mcp_url(data)}",
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
        *(
            [
                "# 服务器状态上报：识别同一台服务器上的实例（boot_id 的 HMAC，不报原值）",
                f"MYWS_HOST_SALT={data.host_salt}",
            ]
            if data.host_salt
            else []
        ),
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
        "1. 把整个目录放到 OpenClaw 服务器上的"
        f" `~/myworkspace-openclaw/{short_id(data.instance_id)}/`"
        "（一台服务器可以放多个实例，每个实例一个目录），按 `CHECKLIST.md` 设置属主与权限；",
        "2. 按 `CHECKLIST.md` 安装 `openclaw-channel` 插件并打上工作台的补丁"
        "（`bin/channel-patch.mjs`）；主模型在工作台的实例页设置（中转站管理），"
        "`bin/model-config.mjs` 每分钟自动应用，不用手改 `state/openclaw.json`；",
    ]
    if public:
        steps.append(
            "3. 启动本机隧道：把 `tls/client.crt`、`tls/client.key` 交给隧道程序，"
            "在 127.0.0.1 上转发到工作台的外网入口（隧道配置见工作台文档，OC-2 第 4 部分）；"
        )
    steps += [
        f"{4 if public else 3}. `docker compose up -d`"
        f"（Compose 项目名 `{project_name(data.instance_id)}`），"
        "冷启动要 2 ~ 4 分钟；",
        f"{5 if public else 4}. 在工作台的实例页确认状态变为“在线”，并在 IM 里私聊它。",
        "",
        "重新生成接入包会签发新凭证：旧凭证默认再保留 24 小时，到期前换上新的接入包并重启。",
        "",
    ]
    return "\n".join(steps)


def _image_proxy(data: BundleInput) -> tuple[str, str, str] | None:
    """内网实例的本机取图代理：在这个实例里把外网域名指向 127.0.0.1，转发到内网入口。

    返回（net 服务的 hosts、openclaw 服务的补充、image-proxy 服务）。
    """
    if not data.image_proxy_host or not data.image_proxy_target:
        return None
    net = (
        "    # 图片地址用的是工作台的外网域名：在这个实例里指向本机代理（转发到内网入口）\n"
        f'    extra_hosts: ["{data.image_proxy_host}:127.0.0.1"]\n'
    )
    openclaw = "    depends_on: [image-proxy]\n"
    service = f"""
  # 本机取图代理：在 127.0.0.1:{data.tunnel_port} 接收明文，用 TLS（不带客户端证书）转发到
  # 工作台内网入口。校验服务器证书用系统信任库；内网入口用私有 CA 时，加上 --cacert 指向那张 CA 证书
  image-proxy:
    image: {GHOSTUNNEL_IMAGE}
    restart: unless-stopped
    user: "1000:1000"
    network_mode: "service:net"
    command:
      - client
      - --listen=127.0.0.1:{data.tunnel_port}
      - --target={data.image_proxy_target}
      - --disable-authentication
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
"""
    return net, openclaw, service


def _tunnel_hosts(data: BundleInput) -> str:
    """本机隧道自己的 hosts：实例共用的那份把入口域名指向 127.0.0.1（给 OpenClaw 用），
    隧道要连真实的入口。入口主机名不在公网 DNS 里时写上工作台地址，否则照常查 DNS。"""
    lines = [
        "# 本机隧道自己的 hosts（My Workspace 接入包生成，只读挂到隧道容器的 /etc/hosts）",
        "127.0.0.1\tlocalhost",
        "::1\tlocalhost ip6-localhost ip6-loopback",
    ]
    host = _tunnel_host(data)
    if data.tunnel_address and host:
        lines.append(f"{data.tunnel_address}\t{host}")
    return "\n".join(lines) + "\n"


def _limits(data: BundleInput) -> str:
    lines: list[str] = []
    if data.mem_limit_mb is not None:
        if not MEM_LIMIT_MIN_MB <= data.mem_limit_mb <= MEM_LIMIT_MAX_MB:
            raise ValueError("内存上限超出范围")
        lines.append(f"    mem_limit: {int(data.mem_limit_mb)}m\n")
    if data.cpus is not None:
        if not CPUS_MIN <= data.cpus <= CPUS_MAX:
            raise ValueError("CPU 上限超出范围")
        lines.append(f'    cpus: "{round(data.cpus, 2):g}"\n')
    if not lines:
        return ""
    return "    # 资源上限（在工作台的实例设置里修改，重新部署这个实例后生效）\n" + "".join(lines)


def _compose(data: BundleInput) -> str:
    text = (
        _read("compose.yaml")
        .replace("@PROJECT@", project_name(data.instance_id))
        .replace("    # @LIMITS@\n", _limits(data))
    )
    host = _tunnel_host(data)
    proxy = _image_proxy(data)
    if proxy is not None:
        net, openclaw, service = proxy
    elif data.tunnel_target and host:
        net = (
            "    # 工作台外网入口的域名在这个实例里指向本机隧道（隧道自己挂另一份 hosts）\n"
            f'    extra_hosts: ["{host}:127.0.0.1"]\n'
        )
        openclaw = "    depends_on: [tunnel]\n"
        trust = (
            "入口证书由工作台内部 CA 签发：用 tls/ca.pem 校验（接入包自带）"
            if data.server_ca_pem
            else "校验服务器证书用系统信任库；外网入口用私有 CA 时，加上 --cacert 指向那张 CA 证书"
        )
        cacert = "      - --cacert=/tls/ca.pem\n" if data.server_ca_pem else ""
        service = f"""
  # 本机隧道：在 127.0.0.1:{data.tunnel_port} 接收明文，
  # 用 tls/ 里的客户端证书（双向证书）转发到工作台外网入口。
  # {trust}
  tunnel:
    image: {GHOSTUNNEL_IMAGE}
    restart: unless-stopped
    user: "1000:1000"
    network_mode: "service:net"
    command:
      - client
      - --listen=127.0.0.1:{data.tunnel_port}
      - --target={data.tunnel_target}
      - --cert=/tls/client.crt
      - --key=/tls/client.key
{cacert}    volumes:
      - ./tls:/tls:ro
      # 自己的 hosts：实例共用的那份把入口域名指向 127.0.0.1（给 OpenClaw 用）
      - ./tls/hosts:/etc/hosts:ro
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
"""
    else:
        net = openclaw = service = ""
    return (
        text.replace("    # @NET_HOSTS@\n", net)
        .replace("    # @PUBLIC_OPENCLAW@\n", openclaw)
        .replace("# @PUBLIC_SERVICES@\n", service)
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
        _add(
            zf,
            "state/openclaw.json",
            _openclaw_json(data.whitelist, data.model),
            0o600,
            now,
        )
        for script in BIN_SCRIPTS:
            _add(zf, f"bin/{script}", _read(f"bin/{script}"), 0o755, now)
        _add(zf, "compose.yaml", _compose(data), 0o644, now)
        _add(zf, "CHECKLIST.md", _read("CHECKLIST.md"), 0o644, now)
        _add(zf, "reflection-prompt.md", _read("reflection-prompt.md"), 0o644, now)
        script = (
            _read("setup-reflection.sh")
            .replace("@CRON@", data.reflection_cron)
            .replace("@TZ@", data.reflection_timezone)
        )
        _add(zf, "setup-reflection.sh", script, 0o755, now)
        _add(zf, "README.md", _readme(data, now), 0o644, now)
        if data.client_cert_pem and data.client_key_pem:
            _add(zf, "tls/client.crt", data.client_cert_pem, 0o644, now)
            _add(zf, "tls/client.key", data.client_key_pem, 0o600, now)
        if data.server_ca_pem and data.tunnel_target:
            _add(zf, "tls/ca.pem", data.server_ca_pem, 0o644, now)
        if data.tunnel_target and not data.image_proxy_host:
            _add(zf, "tls/hosts", _tunnel_hosts(data), 0o644, now)
    return buffer.getvalue()


def filename_for(instance_id: str) -> str:
    return f"myworkspace-agent-{instance_id[:8]}.zip"
