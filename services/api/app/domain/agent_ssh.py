"""通过 SSH 自动部署 OpenClaw 实例（T-0089）：目标服务器、主机公钥的探测与确认。

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md
（人类的选择：先显示指纹、确认后才安装；密码只用一次，换成专用密钥）。本模块（B1）只做：

1. 填写目标服务器（主机、端口、账号）：解析地址并检查，读取主机公钥（不登录），记下 SHA256 指纹；
2. owner 核对指纹后确认：之后的连接只接受这把公钥（B2 用 `known_hosts` 固定）；
3. 删除目标服务器；
4. 开始自动安装（B2）：新建一条安装任务，交给进程内的 `SshJobRunner`
   （app/domain/agent_ssh_install.py）。

只有 owner 可以使用（与实例接口一致）；每个实例最多一台目标服务器；每一步写审计。
探测会对外发起连接，按用户限流（每分钟 10 次），避免被用来扫描端口。
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import asyncssh
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.integrations import ssh
from app.models import (
    AgentInstance,
    AgentOpsAction,
    AgentSshJob,
    AgentSshTarget,
    RunnerSshJob,
    RunnerSshTarget,
)
from app.schemas.agents import (
    AgentSshConfirm,
    AgentSshInstall,
    AgentSshJobOut,
    AgentSshJobStep,
    AgentSshRevoke,
    AgentSshState,
    AgentSshTargetOut,
    AgentSshTargetSet,
    AgentSshUninstall,
)

if TYPE_CHECKING:
    from app.domain.agent_ssh_install import SshJobRunner

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


def _key_missing() -> ApiError:
    return ApiError(409, "AGENT_SSH_KEY_MISSING", "这台服务器上还没有装工作台的专用公钥")


def _job_running() -> ApiError:
    return ApiError(409, "AGENT_SSH_JOB_RUNNING", "这台服务器正在安装，结束后再操作")


def _client_fingerprint(public_key: str | None) -> str | None:
    if not public_key:
        return None
    try:
        return asyncssh.import_public_key(public_key).get_fingerprint("sha256")
    except (asyncssh.KeyImportError, ValueError):
        return None


def _job_out(job: AgentSshJob | RunnerSshJob) -> AgentSshJobOut:
    return AgentSshJobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        steps=[AgentSshJobStep.model_validate(step) for step in job.steps],
        log=job.log,
        error_code=job.error_code,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )


def _out(target: AgentSshTarget | RunnerSshTarget) -> AgentSshTargetOut:
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
        client_key_fingerprint=_client_fingerprint(target.client_public_key),
        install_dir=target.install_dir,
        installed_at=target.installed_at,
        version=target.version,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


class AgentSshService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None = None,
        runner: "SshJobRunner | None" = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.runner = runner
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
        """锁住这一行再读（SELECT ... FOR UPDATE）：版本检查与写入在同一个事务里，
        不会与并发的更换交错。"""
        instance = await self._instance(access, instance_id)
        target = await self.db.scalar(
            select(AgentSshTarget)
            .where(
                AgentSshTarget.instance_id == instance.id,
                AgentSshTarget.workspace_id == instance.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is None:
            raise ApiError(404, "NOT_FOUND", "这个实例还没有填写目标服务器")
        return target

    async def _latest_job(self, target: AgentSshTarget) -> AgentSshJob | None:
        return await self.db.scalar(
            select(AgentSshJob)
            .where(AgentSshJob.target_id == target.id)
            .order_by(AgentSshJob.started_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    async def _running(self, target: AgentSshTarget) -> bool:
        """有没有进行中的任务。

        API 只有一个进程：记录是 running、但本进程里没有它的协程（协程意外退出），
        就把它标为中断，不让它一直挡住后续操作（与启动时的处理相同）。
        """
        # 运维动作（T-0114）进行中时也不能开始部署、卸载、吊销（同一条 SSH 通道）
        action = await self.db.scalar(
            select(AgentOpsAction.id).where(
                AgentOpsAction.instance_id == target.instance_id,
                AgentOpsAction.status == "running",
            )
        )
        if action is not None and self.runner is not None and self.runner.running(action):
            return True
        job = await self.db.scalar(
            select(AgentSshJob).where(
                AgentSshJob.target_id == target.id, AgentSshJob.status == "running"
            )
        )
        if job is None:
            return False
        if self.runner is None or self.runner.running(job.id):
            return True
        job.status = "interrupted"
        job.error_code = "interrupted"
        job.error_message = "安装任务已不在运行；可以重新开始"
        job.finished_at = datetime.now(UTC)
        await self.db.flush()
        return False

    async def _state(self, target: AgentSshTarget | None) -> AgentSshState:
        if target is None:
            return AgentSshState(target=None, job=None)
        job = await self._latest_job(target)
        return AgentSshState(target=_out(target), job=_job_out(job) if job else None)

    async def state(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentSshState:
        target = await self._target(await self._instance(access, instance_id))
        return await self._state(target)

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
            if await self._running(target):
                raise _job_running()
        await self._check_probe_rate(access)
        # 探测要几秒：前面只是普通读取（READ COMMITTED，不加锁）；探测完再锁行、重新检查
        try:
            address = await ssh.resolve(
                body.host, body.port, allow_loopback=self.settings.agent_ssh_allow_loopback
            )
            key = await ssh.probe_host_key(address, body.port)
        except ssh.SshError as error:
            raise _unreachable(error) from error
        if target is not None:
            # 探测期间可能有别的请求确认了旧指纹或改了这条记录：版本变了就拒绝，
            # 旧指纹的确认绝不能落到新公钥上
            target = await self._existing(access, instance_id)
            if target.version != body.version:
                raise _version_conflict(target.version)
            if target.client_key_ref is not None:
                raise ApiError(
                    409,
                    "AGENT_SSH_KEY_INSTALLED",
                    "这台服务器上已经装了工作台的专用公钥：先吊销，再更换服务器",
                )
            # 开始安装不改版本号：探测期间开始的安装任务也要在锁内再查一次，
            # 否则任务会连到更换后的服务器（并把原服务器的登录密码发过去）
            if await self._running(target):
                raise _job_running()
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
        return await self._state(target)

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
        if await self._running(target):
            raise _job_running()
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
        return await self._state(target)

    async def remove(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, *, version: int, client_ip: str
    ) -> None:
        target = await self._existing(access, instance_id)
        if version != target.version:
            raise _version_conflict(target.version)
        if await self._running(target):
            raise _job_running()
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

    async def install(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentSshInstall,
        *,
        client_ip: str,
    ) -> AgentSshState:
        """开始自动安装：检查前提，新建任务（running），交给进程内的后台协程。

        密码只交给协程（`SshJobRunner.start`），这里不写进任务记录、审计或日志。
        """
        if self.runner is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "安装任务暂时不可用，请稍后重试")
        target = await self._existing(access, instance_id)
        if body.version != target.version:
            raise _version_conflict(target.version)
        instance = await self._instance(access, instance_id)
        if instance.status == "disabled":
            raise ApiError(409, "AGENT_DISABLED", "实例已停用，启用后才能部署")
        if instance.network_mode != "public":
            raise ApiError(
                409,
                "AGENT_SSH_PUBLIC_ONLY",
                "SSH 自动部署只支持外网模式的实例",
                details={"network_mode": instance.network_mode},
            )
        if not self.settings.agent_public_url:
            raise ApiError(
                409,
                "AGENT_INGRESS_NOT_CONFIGURED",
                "外网接入入口还没有配置（AGENT_PUBLIC_URL）",
                details={"network_mode": instance.network_mode},
            )
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if body.password is None and target.client_key_ref is None:
            raise ApiError(422, "VALIDATION_FAILED", "第一次部署需要填写登录密码")
        if self.settings.credential_encryption_key is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")
        if await self._running(target):
            raise _job_running()
        return await self._start_job(
            access,
            target,
            kind="install",
            password=body.password,
            metadata={
                "host": target.host,
                "port": target.port,
                "username": target.username,
                "host_key_fingerprint": target.host_key_fingerprint,
                "with_password": body.password is not None,
            },
            client_ip=client_ip,
        )

    async def _start_job(
        self,
        access: WorkspaceAccess,
        target: AgentSshTarget,
        *,
        kind: str,
        metadata: dict[str, object],
        client_ip: str,
        password: SecretStr | None = None,
        remove_files: bool = False,
    ) -> AgentSshState:
        """新建任务（running）并交给进程内的后台协程；调用方已锁行并检查过没有进行中的任务。"""
        assert self.runner is not None  # noqa: S101 - 调用方已检查
        steps = prov.STEPS if kind == "install" else prov.UNINSTALL_STEPS
        job = AgentSshJob(
            id=uuid.uuid4(),
            workspace_id=target.workspace_id,
            instance_id=target.instance_id,
            target_id=target.id,
            kind=kind,
            status="running",
            steps=[{"key": key, "status": "pending", "detail": None} for key in steps],
            log="",
            started_by=access.user_id,
        )
        self.db.add(job)
        await self.audit.record(
            f"agents.ssh.{kind}_started",
            workspace_ids=[target.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=target.instance_id,
            metadata=metadata,
            client_ip=client_ip,
        )
        # 提交前先登记：提交之后、启动协程之前，别的请求不会把它当成失效的任务
        self.runner.reserve(job.id)
        try:
            await self.db.commit()
        except IntegrityError as error:
            # 部分唯一索引：另一个请求同时开始了任务
            self.runner.release(job.id)
            await self.db.rollback()
            raise _job_running() from error
        except BaseException:
            self.runner.release(job.id)
            raise
        self.runner.start(job.id, password, client_ip=client_ip, remove_files=remove_files)
        await self.db.refresh(target)
        return await self._state(target)

    async def uninstall(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentSshUninstall,
        *,
        client_ip: str,
    ) -> AgentSshState:
        """开始卸载（B3）：只用专用密钥；停止容器、删掉定时任务，可选删除接入包目录。"""
        if self.runner is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "卸载任务暂时不可用，请稍后重试")
        target = await self._existing(access, instance_id)
        if body.version != target.version:
            raise _version_conflict(target.version)
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if target.client_key_ref is None:
            raise _key_missing()
        if self.settings.credential_encryption_key is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")
        if await self._running(target):
            raise _job_running()
        return await self._start_job(
            access,
            target,
            kind="uninstall",
            remove_files=body.remove_files,
            metadata={
                "host": target.host,
                "port": target.port,
                "remove_files": body.remove_files,
            },
            client_ip=client_ip,
        )

    async def revoke(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentSshRevoke,
        *,
        client_ip: str,
    ) -> AgentSshState:
        """吊销专用密钥（B3）：用它登录，从 authorized_keys 删掉这一行，再删除工作台保存的私钥。

        锁住目标服务器这一行再连（最多几十秒）：期间不能开始安装或卸载。服务器连不上时，
        force=true 只删除工作台保存的私钥，并提示 owner 手动删除服务器上的那一行。
        """
        target = await self._existing(access, instance_id)
        if body.version != target.version:
            raise _version_conflict(target.version)
        if target.client_key_ref is None or not target.client_public_key:
            raise _key_missing()
        if await self._running(target):
            raise _job_running()
        public = target.client_public_key
        reason: str | None = None
        try:
            await self._remove_remote_key(target, public)
        except ssh.SshError as error:
            if not body.force:
                raise _unreachable(error) from error
            reason = error.reason
        except (CredentialError, asyncssh.KeyImportError) as error:
            if not body.force:
                raise ApiError(
                    409, "AGENT_SSH_KEY_INVALID", "专用私钥无法解密；可以强制删除工作台保存的私钥"
                ) from error
            reason = "client_key_invalid"
        target.client_key_ref = None
        target.client_public_key = None
        target.version += 1
        await self.audit.record(
            "agents.ssh.key_revoked",
            workspace_ids=[target.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=target.instance_id,
            metadata={
                "client_key_fingerprint": _client_fingerprint(public),
                "remote_removed": reason is None,
                "reason": reason,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(target)
        state = await self._state(target)
        if reason is not None:
            state.warning = (
                f"没能连上服务器（{reason}），只删除了工作台保存的私钥；"
                f"请在服务器的 ~/.ssh/authorized_keys 里手动删除这一行：{public}"
            )
        return state

    async def _remove_remote_key(self, target: AgentSshTarget, public: str) -> None:
        box = CredentialBox.from_settings(self.settings)
        secret = box.resolve(target.client_key_ref or "", context=agent_ssh_context(target.id))
        key = asyncssh.import_private_key(secret.get_secret_value())
        conn = await ssh.connect(
            target.address, target.port, target.username, target.host_key, client_key=key
        )
        try:
            result = await ssh.run(conn, prov.revoke_key_command(public), seconds=30)
        finally:
            conn.close()
        if not result.ok:
            raise ssh.SshError("command_failed", "从 authorized_keys 删除专用公钥失败")
