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
        except (TimeoutError, OSError) as error:
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
