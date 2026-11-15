"""工作台访问部署在别的服务器上的 MCP 管理器：专用的 SSH 通道
（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 4 节）。

管理器只监听那台服务器的本机地址（127.0.0.1:<端口>）。部署时给它装一把只能转发到这一个端口的
密钥（不能执行命令、不能开终端）；工作台的每个进程在第一次访问时用这把密钥登录、把本进程里的
一个本机端口转发过去，之后复用，断了下次访问时重建。

通道只用部署记录里自己保存的服务器地址、账号、主机公钥和这把密钥：服务器页面上改密码、
吊销容器服务器的专用密钥、把服务器从工作台移除，都不影响它。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

import asyncssh

from app.core.config import Settings
from app.domain.credentials import CredentialBox, CredentialError
from app.integrations import ssh
from app.integrations.base import ConnectorError
from app.modules.llm_bridge import deploy_bundle as bundle
from app.modules.llm_bridge.models import LlmBridgeInstall

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 10.0


def key_context(install_id: uuid.UUID) -> str:
    """转发私钥的用途：绑定这一条部署记录（挪到别的记录上解不开）。"""
    return f"llm_bridge_tunnel:{install_id}"


def new_key(slot: str) -> tuple[str, str]:
    """生成一把转发密钥：返回（私钥文本，公钥那一行）。"""
    key = asyncssh.generate_private_key("ssh-ed25519", comment=bundle.tunnel_comment(slot))
    return key.export_private_key().decode(), key.export_public_key().decode().strip()


@dataclass(frozen=True)
class TunnelSpec:
    install_id: uuid.UUID
    address: str
    ssh_port: int
    username: str
    host_key: str
    key_ref: str
    port: int


def spec_of(install: LlmBridgeInstall) -> TunnelSpec | None:
    if install.mode != "tunnel" or install.tunnel_key_ref is None or install.port is None:
        return None
    return TunnelSpec(
        install_id=install.id,
        address=install.address,
        ssh_port=install.ssh_port,
        username=install.username,
        host_key=install.host_key,
        key_ref=install.tunnel_key_ref,
        port=install.port,
    )


def _unavailable(message: str, reason: str) -> ConnectorError:
    return ConnectorError("UNAVAILABLE", message, not_sent=True, reason=reason)


@dataclass
class _Tunnel:
    spec: TunnelSpec
    loop: asyncio.AbstractEventLoop
    conn: asyncssh.SSHClientConnection
    listener: asyncssh.SSHListener
    url: str

    def alive(self, spec: TunnelSpec, loop: asyncio.AbstractEventLoop) -> bool:
        return self.loop is loop and self.spec == spec and not self.conn.is_closed()

    def close(self) -> None:
        if self.loop.is_closed():
            return
        try:
            self.listener.close()
            self.conn.close()
        except (OSError, RuntimeError, asyncssh.Error):
            logger.debug("llm bridge tunnel close failed", exc_info=True)


class TunnelPool:
    """本进程里的通道：部署记录 → 本机端口。"""

    def __init__(self) -> None:
        self._tunnels: dict[uuid.UUID, _Tunnel] = {}
        self._locks: dict[tuple[int, uuid.UUID], asyncio.Lock] = {}

    def _lock(self, loop: asyncio.AbstractEventLoop, install_id: uuid.UUID) -> asyncio.Lock:
        return self._locks.setdefault((id(loop), install_id), asyncio.Lock())

    async def _open(self, settings: Settings, spec: TunnelSpec) -> _Tunnel:
        try:
            secret = CredentialBox.from_settings(settings).resolve(
                spec.key_ref, context=key_context(spec.install_id)
            )
            key = asyncssh.import_private_key(secret.get_secret_value())
        except (CredentialError, asyncssh.KeyImportError) as error:
            raise _unavailable(
                "这座 MCP 管理器的转发密钥读不出来", "tunnel_key_unreadable"
            ) from error
        try:
            conn = await ssh.connect(
                spec.address, spec.ssh_port, spec.username, spec.host_key, client_key=key
            )
        except ssh.SshError as error:
            raise _unavailable(
                f"经 SSH 通道连不上这台服务器：{error.message}", f"tunnel_{error.reason}"
            ) from error
        try:
            listener = await conn.forward_local_port("127.0.0.1", 0, "127.0.0.1", spec.port)
        except (OSError, asyncssh.Error) as error:
            conn.close()
            raise _unavailable("工作台本机开不了转发端口", "tunnel_listen_failed") from error
        return _Tunnel(
            spec,
            asyncio.get_running_loop(),
            conn,
            listener,
            f"http://127.0.0.1:{listener.get_port()}",
        )

    async def _tunnel(self, settings: Settings, spec: TunnelSpec) -> _Tunnel:
        loop = asyncio.get_running_loop()
        current = self._tunnels.get(spec.install_id)
        if current is not None and current.alive(spec, loop):
            return current
        async with self._lock(loop, spec.install_id):
            current = self._tunnels.get(spec.install_id)
            if current is not None and current.alive(spec, loop):
                return current
            if current is not None:
                current.close()
                self._tunnels.pop(spec.install_id, None)
            opened = await self._open(settings, spec)
            self._tunnels[spec.install_id] = opened
            return opened

    async def url(self, settings: Settings, spec: TunnelSpec) -> str:
        """这座管理器在本进程里的访问地址（需要时建立通道）。"""
        return (await self._tunnel(settings, spec)).url

    async def probe(self, settings: Settings, spec: TunnelSpec) -> str | None:
        """直接开一条转发试一下：通了返回 None，不通返回原因。

        `prohibited`：sshd 不允许这把密钥转发（AllowTcpForwarding 关着）；
        `refused`：转发是允许的，但那个端口上还没有东西在听（管理器还在启动）。
        """
        tunnel = await self._tunnel(settings, spec)
        try:
            _, writer = await asyncio.wait_for(
                tunnel.conn.open_connection("127.0.0.1", spec.port), PROBE_TIMEOUT
            )
        except asyncssh.ChannelOpenError as error:
            return (
                "prohibited"
                if error.code == asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED
                else "refused"
            )
        except (TimeoutError, OSError, asyncssh.Error):
            self.drop(spec.install_id)
            return "refused"
        writer.close()
        return None

    def drop(self, install_id: uuid.UUID) -> None:
        """关掉这条部署记录的通道（卸载、重新部署换了端口、请求失败后重建）。"""
        current = self._tunnels.pop(install_id, None)
        if current is not None:
            current.close()

    def close(self) -> None:
        for install_id in list(self._tunnels):
            self.drop(install_id)
        self._locks.clear()


POOL = TunnelPool()
