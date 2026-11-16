"""OpenClaw 服务器的 SSH 登录策略：只用保存的密码（T-0178）。

依据 docs/decisions/2026-11-09-agent-server-password-auth.md：

- `connect_agent_server`：主机公钥必须已经确认；按 credential_revision 解密保存的密码，
  用 `ssh.connect_password_only` 登录（不用 ssh-agent、默认私钥、~/.ssh/config，不尝试公钥）。
  旧方案留下的专用私钥（client_key_ref）从不读取；
- `check_agent_server`：“测试连接”——登录后只读检查发行版、架构、账号、Docker / Compose / sudo；
  需要 sudo 时用同一个密码（经标准输入）验证一次。不安装 Docker、不建容器、不写 authorized_keys。
  登录成功时顺带读出这条连接的延迟（T-0271，`connection_latency_ms`：
  内核 TCP_INFO 里的平滑往返时间，不另外建连接、不发额外的包）。

初始化、部署、卸载与运维改用 `connect_agent_server` 在 T-0179（计划 Task 2）完成；
运行服务器（Runner）的 SSH 流程不使用本模块。
"""

import socket
import struct
from dataclasses import dataclass, replace

import asyncssh

from app import agent_provisioning as prov
from app.domain.agent_server_credentials import ServerCredentialError, resolve_server_password
from app.domain.agent_ssh_install import SUPPORTED_OS_LABEL, HostInfo, InstallFailed, parse_inspect
from app.domain.credentials import CredentialBox
from app.integrations import ssh
from app.models import AgentSshTarget

SUDO_CHECK_TIMEOUT = 30.0
INSPECT_TIMEOUT = 60.0
# Linux struct tcp_info：tcpi_rtt（微秒）在第 68 字节（8 个 u8 之后的第 16 个 u32），
# 布局见内核 include/uapi/linux/tcp.h；读 104 字节，兼容老内核较短的结构
_TCP_INFO_SIZE = 104
_TCP_INFO_RTT_OFFSET = 68
MAX_LATENCY_MS = 60_000.0


async def connect_agent_server(
    target: AgentSshTarget, box: CredentialBox, *, expected_revision: int
) -> asyncssh.SSHClientConnection:
    """用保存的密码登录这台服务器。

    抛 ServerCredentialError（没保存 / 解不开 / 密码已被替换）或 ssh.SshError
    （host_key_unconfirmed / host_key_changed / auth_failed / 连不上）。
    """
    if target.host_key_confirmed_at is None:
        raise ssh.SshError("host_key_unconfirmed", "服务器的主机公钥还没有确认")
    password = resolve_server_password(box, target, expected_revision=expected_revision)
    return await ssh.connect_password_only(
        target.address, target.port, target.username, target.host_key, password.get_secret_value()
    )


def connection_latency_ms(conn: asyncssh.SSHClientConnection) -> float | None:
    """这条 SSH 连接的往返延迟（毫秒，精确到微秒）；读不到返回 None。

    读的是内核对这条 TCP 连接的平滑往返时间（TCP_INFO.tcpi_rtt）：登录过程已经来回了好几次，
    不需要再发探测包，也不会再连一次 sshd。只在 Linux 上有；其他系统、拿不到套接字、
    值不合理时返回 None（界面显示“延迟未知”），不影响连接结果。
    """
    option = getattr(socket, "TCP_INFO", None)
    sock = conn.get_extra_info("socket")
    if option is None or sock is None:
        return None
    try:
        raw = sock.getsockopt(socket.IPPROTO_TCP, option, _TCP_INFO_SIZE)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(raw, bytes) or len(raw) < _TCP_INFO_RTT_OFFSET + 4:
        return None
    (rtt_us,) = struct.unpack_from("=I", raw, _TCP_INFO_RTT_OFFSET)
    # 0 表示内核还没有测量值
    return rtt_us / 1000 if 0 < rtt_us < MAX_LATENCY_MS * 1000 else None


def connection_reason(error: ssh.SshError) -> str:
    """连接失败的类别（写进 last_connection_reason 的白名单值）。"""
    if error.reason in {"host_key_changed", "auth_failed"}:
        return error.reason
    return "unreachable"


@dataclass(frozen=True)
class ConnectionCheck:
    """一次连接检测的结果：reason 是白名单类别；warning 是需要 owner 处理的环境问题。"""

    reason: str
    warning: str | None = None
    info: HostInfo | None = None
    # 登录成功时这条连接的往返延迟（毫秒）；登录失败或读不到为 None
    latency_ms: float | None = None


async def check_agent_server(
    target: AgentSshTarget, box: CredentialBox, *, expected_revision: int
) -> ConnectionCheck:
    """测试连接：登录、只读检查环境，需要时验证 sudo。

    密码没保存 / 已被替换仍抛 ServerCredentialError（由接口返回错误）；
    解不开、连不上、密码错误、主机公钥变化都作为检测结果返回。
    """
    try:
        conn = await connect_agent_server(target, box, expected_revision=expected_revision)
    except ServerCredentialError as error:
        if error.reason == "credential_unreadable":
            return ConnectionCheck("credential_unreadable", error.message)
        raise
    except ssh.SshError as error:
        return ConnectionCheck(connection_reason(error), error.message)
    latency = connection_latency_ms(conn)
    try:
        check = await _inspect(conn, target, box, expected_revision)
    except ssh.SshError as error:
        check = ConnectionCheck("connected", f"登录成功，但读取服务器信息失败：{error.message}")
    finally:
        conn.close()
    return replace(check, latency_ms=latency)


async def _inspect(
    conn: asyncssh.SSHClientConnection,
    target: AgentSshTarget,
    box: CredentialBox,
    expected_revision: int,
) -> ConnectionCheck:
    result = await ssh.run(conn, "sh -s", stdin=prov.script("inspect.sh"), seconds=INSPECT_TIMEOUT)
    if not result.ok:
        return ConnectionCheck("connected", "登录成功，但读取服务器信息失败")
    try:
        info = parse_inspect(result.stdout)
    except InstallFailed as error:
        # 远端输出不合规：只报告，不当成连接失败
        return ConnectionCheck("connected", f"登录成功，但{error.message}")
    notes: list[str] = []
    if not info.docker_ready:
        if (not info.docker or not info.compose) and not info.supported:
            notes.append(
                f"系统 {info.os_id} {info.os_version} {info.arch} 不在自动安装 Docker 的支持范围"
                f"（{SUPPORTED_OS_LABEL}，amd64 / arm64）："
                "请先手动安装 Docker 与 Compose 插件"
            )
        if not info.root:
            if not info.sudo:
                notes.append("服务器上没有 sudo：初始化无法以 root 安装或配置 Docker")
            elif not await _sudo_ok(conn, target, box, expected_revision):
                notes.append(
                    "这个账号不能用登录密码执行 sudo（或 sudo 需要另外的密码 / 交互式验证）："
                    "初始化无法安装或配置 Docker"
                )
    if not info.crontab:
        notes.append("服务器上没有 crontab：部署时无法安装取密钥与每周重启的定时任务")
    return ConnectionCheck("connected", "；".join(notes) or None, info)


async def _sudo_ok(
    conn: asyncssh.SSHClientConnection,
    target: AgentSshTarget,
    box: CredentialBox,
    expected_revision: int,
) -> bool:
    """用同一个登录密码验证 sudo（密码只经标准输入，不出现在命令行里）。"""
    password = resolve_server_password(box, target, expected_revision=expected_revision)
    result = await ssh.run(
        conn,
        "sudo -S -p '' -k true",
        stdin=password.get_secret_value() + "\n",
        seconds=SUDO_CHECK_TIMEOUT,
    )
    return result.ok
