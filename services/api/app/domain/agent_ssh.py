"""通过 SSH 自动部署 OpenClaw 实例（T-0089）：目标服务器、主机公钥的探测与确认。

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md
（人类的选择：先显示指纹、确认后才安装；密码只用一次，换成专用密钥）。本模块（B1）只做：

1. 填写目标服务器（主机、端口、账号）：解析地址并检查，读取主机公钥（不登录），记下 SHA256 指纹；
2. owner 核对指纹后确认：之后的连接只接受这把公钥（B2 用 `known_hosts` 固定）；
3. 删除目标服务器。

只有 owner 可以使用（与实例接口一致）；每个实例最多一台目标服务器；每一步写审计。
探测会对外发起连接，按用户限流（每分钟 10 次），避免被用来扫描端口。
"""

import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.integrations import ssh
from app.models import AgentInstance, AgentSshTarget
from app.schemas.agents import (
    AgentSshConfirm,
    AgentSshState,
    AgentSshTargetOut,
    AgentSshTargetSet,
)

PROBES_PER_MINUTE = 10


def agent_ssh_context(target_id: uuid.UUID) -> str:
    """专用私钥的加密用途（B2）：绑定到目标服务器这条记录。"""
    return f"agent_ssh:{target_id}"


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _unreachable(error: ssh.SshError) -> ApiError:
    if error.reason == "not_allowed":
        return ApiError(422, "AGENT_SSH_ADDRESS_NOT_ALLOWED", error.message)
    return ApiError(503, "AGENT_SSH_UNREACHABLE", error.message, details={"reason": error.reason})


def _out(target: AgentSshTarget) -> AgentSshTargetOut:
    return AgentSshTargetOut(
        host=target.host,
        address=target.address,
        port=target.port,
        username=target.username,
        host_key_algorithm=target.host_key_algorithm,
        host_key_fingerprint=target.host_key_fingerprint,
        host_key_confirmed=target.host_key_confirmed_at is not None,
        host_key_confirmed_at=target.host_key_confirmed_at,
        client_key_installed=target.client_key_ref is not None,
        version=target.version,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


class AgentSshService:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)

    async def _instance(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentInstance:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id,
                AgentInstance.workspace_id == access.workspace.id,
            )
        )
        if instance is None:
            raise ApiError(404, "NOT_FOUND", "实例不存在")
        return instance

    async def _target(self, instance: AgentInstance) -> AgentSshTarget | None:
        return await self.db.scalar(
            select(AgentSshTarget).where(
                AgentSshTarget.instance_id == instance.id,
                AgentSshTarget.workspace_id == instance.workspace_id,
            )
        )

    async def _existing(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentSshTarget:
        target = await self._target(await self._instance(access, instance_id))
        if target is None:
            raise ApiError(404, "NOT_FOUND", "这个实例还没有填写目标服务器")
        return target

    async def state(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentSshState:
        target = await self._target(await self._instance(access, instance_id))
        return AgentSshState(target=_out(target) if target else None)

    async def _check_probe_rate(self, access: WorkspaceAccess) -> None:
        if self.redis is None:
            return
        retry = await WindowLimiter(self.redis, 60).hit(
            f"agent-ssh-probe:{access.user_id}", PROBES_PER_MINUTE
        )
        if retry is not None:
            raise ApiError(
                429,
                "RATE_LIMITED",
                "探测太频繁，请稍后再试",
                headers={"Retry-After": str(retry)},
            )

    async def set_target(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentSshTargetSet,
        *,
        client_ip: str,
    ) -> AgentSshState:
        """填写或更换目标服务器：解析地址、读取主机公钥（不登录）。确认要另外一步。"""
        instance = await self._instance(access, instance_id)
        if instance.status == "disabled":
            raise ApiError(409, "AGENT_DISABLED", "实例已停用，启用后才能部署")
        if instance.network_mode != "public":
            # 自动部署的服务器走外网模式（ADR 第 3.1 节）：任意一台服务器通常不在工作台的私网里
            raise ApiError(
                409,
                "AGENT_SSH_PUBLIC_ONLY",
                "SSH 自动部署只支持外网模式的实例",
                details={"network_mode": instance.network_mode},
            )
        target = await self._target(instance)
        if target is not None:
            if body.version is None or body.version != target.version:
                raise _version_conflict(target.version)
            if target.client_key_ref is not None:
                raise ApiError(
                    409,
                    "AGENT_SSH_KEY_INSTALLED",
                    "这台服务器上已经装了工作台的专用公钥：先吊销，再更换服务器",
                )
        await self._check_probe_rate(access)
        try:
            address = await ssh.resolve(
                body.host, body.port, allow_loopback=self.settings.agent_ssh_allow_loopback
            )
            key = await ssh.probe_host_key(address, body.port)
        except ssh.SshError as error:
            raise _unreachable(error) from error
        if target is None:
            target = AgentSshTarget(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                created_by=access.user_id,
                version=0,
            )
            self.db.add(target)
        target.host = body.host
        target.address = address
        target.port = body.port
        target.username = body.username
        target.host_key_algorithm = key.algorithm
        target.host_key = key.public_key
        target.host_key_fingerprint = key.fingerprint
        target.host_key_confirmed_at = None
        target.host_key_confirmed_by = None
        target.version += 1
        await self.audit.record(
            "agents.ssh.target_set",
            workspace_ids=[instance.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "host": body.host,
                "address": address,
                "port": body.port,
                "username": body.username,
                "host_key_fingerprint": key.fingerprint,
            },
            client_ip=client_ip,
        )
        try:
            await self.db.commit()
        except IntegrityError as error:
            # 另一个请求同时给这个实例填写了目标服务器
            await self.db.rollback()
            raise _version_conflict(1) from error
        await self.db.refresh(target)
        return AgentSshState(target=_out(target))

    async def confirm(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentSshConfirm,
        *,
        client_ip: str,
    ) -> AgentSshState:
        """owner 核对过指纹：固定这把主机公钥。指纹必须与探测时记下的一致。"""
        target = await self._existing(access, instance_id)
        if body.version != target.version:
            raise _version_conflict(target.version)
        if body.fingerprint != target.host_key_fingerprint:
            raise ApiError(
                409,
                "AGENT_SSH_FINGERPRINT_MISMATCH",
                "指纹与工作台读到的主机公钥不一致；请重新填写服务器并核对",
            )
        target.host_key_confirmed_at = datetime.now(UTC)
        target.host_key_confirmed_by = access.user_id
        target.version += 1
        await self.audit.record(
            "agents.ssh.host_key_confirmed",
            workspace_ids=[target.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=target.instance_id,
            metadata={"host_key_fingerprint": target.host_key_fingerprint},
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(target)
        return AgentSshState(target=_out(target))

    async def remove(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, *, version: int, client_ip: str
    ) -> None:
        target = await self._existing(access, instance_id)
        if version != target.version:
            raise _version_conflict(target.version)
        if target.client_key_ref is not None:
            raise ApiError(
                409,
                "AGENT_SSH_KEY_INSTALLED",
                "这台服务器上已经装了工作台的专用公钥：先吊销，再删除",
            )
        await self.audit.record(
            "agents.ssh.target_removed",
            workspace_ids=[target.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=target.instance_id,
            metadata={"host": target.host, "port": target.port},
            client_ip=client_ip,
        )
        await self.db.delete(target)
        await self.db.commit()
