"""SSH 客户端（T-0089）：检查目标地址、探测主机公钥。

锁定 asyncssh 2.24.1，用到的接口在锁定版本上核验过
（ADR 2026-11-03-openclaw-ssh-provisioning 第 4 节）：

- `get_server_host_key(host, port)` 只完成密钥交换、读取服务器出示的主机公钥，不登录；
- `SSHKey.get_fingerprint("sha256")` 的格式与 `ssh-keygen -lf` 一致（`SHA256:` 加 base64）；
- 之后的连接用 `known_hosts=([固定的公钥], [], [])` 只接受这把公钥（B2）。

不读运行环境里的 `~/.ssh/config`（`config=[]`），连接结果只取决于这里给出的参数。
目标地址：域名只解析一次，之后连接解析出的地址；拒绝回环、链路本地
（含云元数据地址 169.254.169.254）、组播、未指定与保留地址。
私网地址允许（同一机房或内网里的服务器）。
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass

import asyncssh

CONNECT_TIMEOUT = 10.0
DNS_TIMEOUT = 5.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class SshError(Exception):
    """连接 SSH 服务器失败；reason 是给接口与日志用的分类，message 是给人看的说明。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class HostKey:
    algorithm: str
    # OpenSSH 公钥行的前两段（类型与 base64），不含注释
    public_key: str
    fingerprint: str


def check_address(ip: IPAddress, *, allow_loopback: bool = False) -> None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback and not allow_loopback:
        raise SshError("not_allowed", "不能连接本机地址")
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise SshError("not_allowed", f"不能连接这个地址：{ip}")


async def resolve(host: str, port: int, *, allow_loopback: bool = False) -> str:
    """把主机名解析成一个地址（优先 IPv4），并检查它是否允许连接。"""
    try:
        ip: IPAddress = ipaddress.ip_address(host)
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(DNS_TIMEOUT):
                infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (TimeoutError, OSError, UnicodeError) as error:
            # UnicodeError：IDNA 编码失败（例如某一段超过 63 个字符）
            raise SshError("dns", f"解析不了主机名：{host}") from error
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not addresses:
            raise SshError("dns", f"解析不了主机名：{host}") from None
        ip = next((a for a in addresses if a.version == 4), addresses[0])
    check_address(ip, allow_loopback=allow_loopback)
    return str(ip)


def _classify(error: BaseException) -> SshError:
    if isinstance(error, TimeoutError):
        return SshError("timeout", "连接超时：检查地址、端口与防火墙")
    if isinstance(error, ConnectionRefusedError):
        return SshError("refused", "连接被拒绝：端口上没有 SSH 服务")
    if isinstance(error, asyncssh.Error):
        return SshError("handshake", f"SSH 握手失败：{error.reason}")
    return SshError("unreachable", "连接不上这台服务器")


async def probe_host_key(address: str, port: int) -> HostKey:
    """连到 SSH 服务器，只读取它出示的主机公钥（不登录）。"""
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            key = await asyncssh.get_server_host_key(address, port, config=[])
    except (TimeoutError, OSError, asyncssh.Error) as error:
        raise _classify(error) from error
    if key is None:
        raise SshError("handshake", "服务器没有出示主机公钥")
    line = key.export_public_key().decode().split()
    return HostKey(
        algorithm=key.get_algorithm(),
        public_key=" ".join(line[:2]),
        fingerprint=key.get_fingerprint("sha256"),
    )


# ---------------------------------------------------------------- 登录、执行命令、上传文件（B2）

COMMAND_TIMEOUT = 120.0


@dataclass(frozen=True)
class CommandResult:
    exit_status: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_status == 0


async def connect(
    address: str,
    port: int,
    username: str,
    host_key: str,
    *,
    password: str | None = None,
    client_key: asyncssh.SSHKey | None = None,
) -> asyncssh.SSHClientConnection:
    """按固定的主机公钥登录：密码或专用私钥，二选一。

    密码登录也回答只有一个 Password: 提示的 keyboard-interactive（asyncssh 的行为，ADR 第 4.1 节）。

    不用运行环境里的 ssh-agent、默认私钥与 ~/.ssh/config（agent_path=None、config=[]）。
    """
    if (password is None) == (client_key is None):
        raise ValueError("密码与专用私钥只能给一个")
    pinned = asyncssh.import_public_key(host_key)
    try:
        return await asyncssh.connect(
            address,
            port,
            username=username,
            known_hosts=([pinned], [], []),
            config=[],
            agent_path=None,
            client_keys=[client_key] if client_key is not None else None,
            password=password,
            preferred_auth=(
                "publickey" if client_key is not None else "keyboard-interactive,password"
            ),
            connect_timeout=CONNECT_TIMEOUT,
            login_timeout=CONNECT_TIMEOUT,
            keepalive_interval=15,
        )
    except asyncssh.HostKeyNotVerifiable as error:
        raise SshError(
            "host_key_changed", "服务器的主机公钥与确认过的不一致：可能被冒充，已停止连接"
        ) from error
    except asyncssh.PermissionDenied as error:
        raise SshError(
            "auth_failed",
            "登录被拒绝：检查账号与密码" if password is not None else "专用密钥登录被拒绝",
        ) from error
    except (TimeoutError, OSError, asyncssh.Error) as error:
        raise _classify(error) from error


async def connect_password_only(
    address: str, port: int, username: str, host_key: str, password: str
) -> asyncssh.SSHClientConnection:
    """OpenClaw 服务器的登录（T-0178）：只用密码，按固定的主机公钥。

    与 `connect` 不同，这里把其他认证方式都显式关掉（asyncssh 2.24.1 的参数，已在锁定版本的
    SSHClientConnectionOptions 上核对）：

    - `client_keys=None`、`public_key_auth=False`：不做公钥认证，也不去读 ~/.ssh 下的默认私钥；
    - `agent_path=None`、`agent_forwarding=False`：不用运行环境里的 ssh-agent，也不转发；
    - `host_based_auth=False`、`gss_host=None`、`gss_kex=False`、`gss_auth=False`：
      不做基于主机的认证与 GSS（Kerberos）；
    - `preferred_auth` 只有 keyboard-interactive 与 password；`disable_trivial_auth=True`：
      服务器不真正校验凭证（none 认证、没有提示的 keyboard-interactive）就当作失败；
    - `config=[]`：不读 ~/.ssh/config；`known_hosts` 只放确认过的那把主机公钥。
    """
    pinned = asyncssh.import_public_key(host_key)
    try:
        return await asyncssh.connect(
            address,
            port,
            username=username,
            known_hosts=([pinned], [], []),
            config=[],
            agent_path=None,
            agent_forwarding=False,
            client_keys=None,
            public_key_auth=False,
            host_based_auth=False,
            gss_host=None,
            gss_kex=False,
            gss_auth=False,
            kbdint_auth=True,
            password_auth=True,
            password=password,
            preferred_auth="keyboard-interactive,password",
            disable_trivial_auth=True,
            connect_timeout=CONNECT_TIMEOUT,
            login_timeout=CONNECT_TIMEOUT,
            keepalive_interval=15,
        )
    except asyncssh.HostKeyNotVerifiable as error:
        raise SshError(
            "host_key_changed", "服务器的主机公钥与确认过的不一致：可能被冒充，已停止连接"
        ) from error
    except asyncssh.PermissionDenied as error:
        raise SshError(
            "auth_failed", "登录被拒绝：检查账号与密码，以及服务器是否允许密码登录"
        ) from error
    except (TimeoutError, OSError, asyncssh.Error) as error:
        raise _classify(error) from error


async def run(
    conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    stdin: str | None = None,
    seconds: float = COMMAND_TIMEOUT,
) -> CommandResult:
    """执行一条命令（远端的登录 shell）；stdin 用来传密码等，不出现在命令行里。

    没有 stdin 时接到 DEVNULL（立即发送 EOF）：asyncssh 的 run() 只在有 input 时才发 EOF，
    否则远端命令的标准输入一直开着，读标准输入的命令会卡住。
    """
    try:
        if stdin is None:
            result = await conn.run(command, stdin=asyncssh.DEVNULL, check=False, timeout=seconds)
        else:
            result = await conn.run(command, input=stdin, check=False, timeout=seconds)
    except TimeoutError as error:
        raise SshError("command_timeout", "命令执行超时") from error
    except asyncssh.Error as error:
        raise SshError("connection_lost", f"连接中断：{error.reason}") from error
    status = result.exit_status if result.exit_status is not None else -1
    return CommandResult(status, _text(result.stdout), _text(result.stderr))


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


async def put_files(
    conn: asyncssh.SSHClientConnection, directory: str, files: dict[str, tuple[bytes, int]]
) -> None:
    """用 SFTP 把文件写到 directory 下（相对路径 → (内容, 权限)），需要时建子目录。"""
    try:
        async with conn.start_sftp_client() as sftp:
            for name, (content, mode) in files.items():
                path = f"{directory}/{name}"
                parent = path.rsplit("/", 1)[0]
                await sftp.makedirs(parent, exist_ok=True)
                async with sftp.open(path, "wb") as handle:
                    await handle.write(content)
                await sftp.chmod(path, mode)
    except (OSError, asyncssh.Error) as error:
        raise SshError("upload_failed", f"上传文件失败：{error}") from error
