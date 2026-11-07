"""OpenClaw 服务器：添加、核对指纹、准备、部署实例、卸载、吊销、移除（T-0145、T-0147）。

设计见 docs/v2/MULTI_OPENCLAW_PLAN.md 第 4、7、8 节。一台服务器上可以有多个实例：

- 服务器只核对一次主机指纹、只装一把工作台的专用密钥（D7）；
  登录密码只在“准备服务器”时用一次，不保存；
- 部署实例只替换这个实例自己的目录与 Compose 项目，卸载也只碰自己（agent_deployments）；
- 同一台服务器同时只有一个 SSH 任务（部署也一个一个来），与这台服务器上的运维动作互斥；
- 手动部署的实例按上报的服务器指纹自动归到“自动识别的服务器”（app/domain/host_metrics.py），
  管理员可以手动指定（server_manual）；
- 只有 owner 可以使用（与实例接口一致）；每一步写审计。探测主机公钥会对外发起连接，按用户限流。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import asyncssh
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agent_ssh import (
    PROBES_PER_MINUTE,
    _client_fingerprint,
    _job_out,
    _job_running,
    _key_missing,
    _unreachable,
    _version_conflict,
    agent_ssh_context,
)
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.host_metrics import STALE_AFTER, HostService, _percent_used, drop_if_empty_auto
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.integrations import ssh
from app.models import (
    AgentDeployment,
    AgentHostState,
    AgentInstance,
    AgentOpsAction,
    AgentServer,
    AgentSshJob,
    AgentSshTarget,
    AgentUsageMinute,
    Task,
    TaskRun,
)
from app.schemas.agents import (
    AgentDeployIn,
    AgentDeploymentOut,
    AgentDeploymentState,
    AgentServerAddress,
    AgentServerAssign,
    AgentServerCreate,
    AgentServerDetail,
    AgentServerList,
    AgentServerOut,
    AgentServerPrepare,
    AgentServerUpdate,
    AgentSshConfirm,
    AgentSshRevoke,
    AgentUndeployIn,
    RunningTaskRef,
    ServerAgentRow,
    ServerSshOut,
    UnassignedInstance,
)
from app.schemas.host import HostRange

if TYPE_CHECKING:
    from app.domain.agent_ssh_install import SshJobRunner

# 部署前的容量提醒（D4：不设硬上限，只提醒）
MEMORY_WARN_BYTES = 2 << 30
DISK_WARN_PERCENT = 85.0


def _now() -> datetime:
    return datetime.now(UTC)


def _ssh_out(target: AgentSshTarget) -> ServerSshOut:
    return ServerSshOut(
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
        prepared_at=target.prepared_at,
    )


def _server_out(
    server: AgentServer,
    target: AgentSshTarget | None,
    counts: tuple[int, int],
    now: datetime,
) -> AgentServerOut:
    latest = server.latest or {}
    disks = [
        used
        for used in (
            _percent_used(latest.get("data_total"), latest.get("data_free")),
            _percent_used(latest.get("root_total"), latest.get("root_free")),
        )
        if used is not None
    ]
    return AgentServerOut(
        id=server.id,
        name=server.name,
        kind="ssh" if server.kind == "ssh" else "auto",
        ssh=_ssh_out(target) if target is not None else None,
        reported_at=server.reported_at,
        stale=server.reported_at is None or now - server.reported_at > STALE_AFTER,
        cores=latest.get("cores"),
        cpu_percent=latest.get("cpu_percent"),
        load15=latest.get("load15"),
        mem_total=latest.get("mem_total"),
        mem_used_percent=_percent_used(latest.get("mem_total"), latest.get("mem_available")),
        disk_used_percent=max(disks) if disks else None,
        instances_total=counts[0],
        instances_online=counts[1],
        alerts=sorted((server.alerts or {}).keys()),
        version=server.version,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _disabled() -> ApiError:
    return ApiError(409, "AGENT_DISABLED", "实例已停用，启用后才能部署")


class AgentServerService:
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

    # ---------------------------------------------------------------- 读取

    async def _server(
        self, access: WorkspaceAccess, server_id: uuid.UUID, *, lock: bool = False
    ) -> AgentServer:
        query = select(AgentServer).where(
            AgentServer.id == server_id, AgentServer.workspace_id == access.workspace.id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        server = await self.db.scalar(query)
        if server is None:
            raise ApiError(404, "NOT_FOUND", "服务器不存在")
        return server

    async def _target(self, server: AgentServer, *, lock: bool = False) -> AgentSshTarget | None:
        query = select(AgentSshTarget).where(AgentSshTarget.server_id == server.id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(query)

    async def _ssh_target(self, server: AgentServer, *, lock: bool = True) -> AgentSshTarget:
        target = await self._target(server, lock=lock)
        if target is None:
            raise ApiError(
                409, "AGENT_SERVER_NOT_SSH", "这台服务器是按上报自动识别的，不能经 SSH 操作"
            )
        return target

    async def _instance(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, *, lock: bool = False
    ) -> AgentInstance:
        query = select(AgentInstance).where(
            AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        instance = await self.db.scalar(query)
        if instance is None:
            raise ApiError(404, "NOT_FOUND", "实例不存在")
        return instance

    async def _counts(self, server_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
        if not server_ids:
            return {}
        rows = await self.db.execute(
            select(
                AgentInstance.server_id,
                func.count(),
                func.count().filter(AgentInstance.status == "online"),
            )
            .where(AgentInstance.server_id.in_(server_ids))
            .group_by(AgentInstance.server_id)
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in rows.all() if row[0] is not None}

    async def _out(self, server: AgentServer) -> AgentServerOut:
        counts = await self._counts([server.id])
        return _server_out(
            server, await self._target(server), counts.get(server.id, (0, 0)), _now()
        )

    async def list_servers(self, access: WorkspaceAccess) -> AgentServerList:
        now = _now()
        servers = list(
            (
                await self.db.scalars(
                    select(AgentServer)
                    .where(AgentServer.workspace_id == access.workspace.id)
                    .order_by(AgentServer.name, AgentServer.id)
                )
            ).all()
        )
        ids = [s.id for s in servers]
        targets = (
            {
                t.server_id: t
                for t in (
                    await self.db.scalars(
                        select(AgentSshTarget).where(AgentSshTarget.server_id.in_(ids))
                    )
                ).all()
            }
            if ids
            else {}
        )
        counts = await self._counts(ids)
        unassigned = (
            await self.db.execute(
                select(AgentInstance.id, AgentInstance.name, AgentInstance.status)
                .where(
                    AgentInstance.workspace_id == access.workspace.id,
                    AgentInstance.server_id.is_(None),
                )
                .order_by(AgentInstance.name, AgentInstance.id)
            )
        ).all()
        return AgentServerList(
            items=[
                _server_out(s, targets.get(s.id), counts.get(s.id, (0, 0)), now) for s in servers
            ],
            unassigned=[
                UnassignedInstance(id=row[0], name=row[1], status=row[2]) for row in unassigned
            ],
        )

    async def _latest_job(
        self, target: AgentSshTarget | None, *, instance_id: uuid.UUID | None = None
    ) -> AgentSshJob | None:
        if target is None and instance_id is None:
            return None
        query = select(AgentSshJob)
        if instance_id is not None:
            query = query.where(AgentSshJob.instance_id == instance_id)
        else:
            assert target is not None  # noqa: S101 - 上面已检查
            query = query.where(AgentSshJob.target_id == target.id)
        return await self.db.scalar(
            query.order_by(AgentSshJob.started_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    async def _agent_rows(self, server: AgentServer) -> list[ServerAgentRow]:
        now = _now()
        instances = list(
            (
                await self.db.scalars(
                    select(AgentInstance)
                    .where(
                        AgentInstance.workspace_id == server.workspace_id,
                        AgentInstance.server_id == server.id,
                    )
                    .order_by(AgentInstance.name, AgentInstance.id)
                )
            ).all()
        )
        ids = [i.id for i in instances]
        if not ids:
            return []
        states = {
            s.instance_id: s
            for s in (
                await self.db.scalars(
                    select(AgentHostState).where(AgentHostState.instance_id.in_(ids))
                )
            ).all()
        }
        deployments = {
            d.instance_id: d
            for d in (
                await self.db.scalars(
                    select(AgentDeployment).where(
                        AgentDeployment.instance_id.in_(ids),
                        AgentDeployment.server_id == server.id,
                    )
                )
            ).all()
        }
        tokens = {
            row[0]: int(row[1] or 0)
            for row in (
                await self.db.execute(
                    select(
                        AgentUsageMinute.instance_id,
                        func.sum(AgentUsageMinute.input + AgentUsageMinute.output),
                    )
                    .where(
                        AgentUsageMinute.instance_id.in_(ids),
                        AgentUsageMinute.minute >= now - timedelta(hours=1),
                    )
                    .group_by(AgentUsageMinute.instance_id)
                )
            ).all()
        }
        running: dict[uuid.UUID, RunningTaskRef] = {}
        for instance_id, task_id, title in (
            await self.db.execute(
                select(TaskRun.instance_id, Task.id, Task.title)
                .join(Task, Task.id == TaskRun.task_id)
                .where(TaskRun.instance_id.in_(ids), TaskRun.ended_at.is_(None))
                .order_by(TaskRun.started_at.desc())
            )
        ).all():
            if instance_id is not None:
                running.setdefault(instance_id, RunningTaskRef(id=task_id, title=title))
        rows = []
        for instance in instances:
            state = states.get(instance.id)
            deployment = deployments.get(instance.id)
            rows.append(
                ServerAgentRow(
                    instance_id=instance.id,
                    name=instance.name,
                    status=instance.status,
                    deployed=deployment is not None,
                    install_dir=deployment.install_dir if deployment else None,
                    mem_limit_mb=instance.mem_limit_mb,
                    cpus=instance.cpus,
                    load=HostService.agent_load(state, now) if state is not None else None,
                    alerts=sorted((state.alerts or {}).keys()) if state else [],
                    tokens_1h=tokens.get(instance.id, 0),
                    running_task=running.get(instance.id),
                )
            )
        return rows

    async def detail(
        self, access: WorkspaceAccess, server_id: uuid.UUID, span: HostRange = "24h"
    ) -> AgentServerDetail:
        server = await self._server(access, server_id)
        return await self._detail(server, span)

    async def _detail(
        self, server: AgentServer, span: HostRange = "24h", *, warning: str | None = None
    ) -> AgentServerDetail:
        from app.domain.host_metrics import RANGES

        now = _now()
        hosts = HostService(self.db, self.redis)
        target = await self._target(server)
        job = await self._latest_job(target)
        return AgentServerDetail(
            server=await self._out(server),
            current=await hosts.server_current(server, now),
            range=span,
            points=await hosts.server_points(server.id, now - RANGES[span]),
            agents=await self._agent_rows(server),
            job=_job_out(job) if job else None,
            warning=warning,
        )

    async def deployment_state(
        self, access: WorkspaceAccess, instance_id: uuid.UUID
    ) -> AgentDeploymentState:
        instance = await self._instance(access, instance_id)
        return await self._deployment_state(instance)

    async def _deployment_state(
        self, instance: AgentInstance, *, warning: str | None = None
    ) -> AgentDeploymentState:
        deployment = await self.db.scalar(
            select(AgentDeployment)
            .where(AgentDeployment.instance_id == instance.id)
            .execution_options(populate_existing=True)
        )
        server = (
            await self.db.get(AgentServer, instance.server_id, populate_existing=True)
            if instance.server_id
            else None
        )
        job = await self._latest_job(None, instance_id=instance.id)
        return AgentDeploymentState(
            deployment=(
                AgentDeploymentOut(
                    server_id=deployment.server_id,
                    install_dir=deployment.install_dir,
                    installed_at=deployment.installed_at,
                    version=deployment.version,
                )
                if deployment
                else None
            ),
            server=await self._out(server) if server is not None else None,
            job=_job_out(job) if job else None,
            capacity_warning=warning,
        )

    # ---------------------------------------------------------------- 进行中的任务

    async def _busy(self, target: AgentSshTarget) -> bool:
        """这台服务器有没有进行中的 SSH 任务或运维动作。

        记录是 running、但本进程里没有它的协程（协程意外退出），就把它标为中断，
        不让它一直挡住后续操作（与启动时的处理相同）。
        """
        action = await self.db.scalar(
            select(AgentOpsAction.id).where(
                AgentOpsAction.server_id == target.server_id, AgentOpsAction.status == "running"
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
        job.error_message = "任务已不在运行；可以重新开始"
        job.finished_at = _now()
        await self.db.flush()
        return False

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

    async def _probe(
        self, access: WorkspaceAccess, host: str, port: int
    ) -> tuple[str, ssh.HostKey]:
        await self._check_probe_rate(access)
        try:
            address = await ssh.resolve(
                host, port, allow_loopback=self.settings.agent_ssh_allow_loopback
            )
            return address, await ssh.probe_host_key(address, port)
        except ssh.SshError as error:
            raise _unreachable(error) from error

    async def _duplicate(
        self, access: WorkspaceAccess, address: str, port: int, username: str, *, skip: uuid.UUID
    ) -> None:
        exists = await self.db.scalar(
            select(AgentSshTarget.id).where(
                AgentSshTarget.workspace_id == access.workspace.id,
                AgentSshTarget.address == address,
                AgentSshTarget.port == port,
                AgentSshTarget.username == username,
                AgentSshTarget.id != skip,
            )
        )
        if exists is not None:
            raise ApiError(
                409,
                "AGENT_SERVER_EXISTS",
                "已经添加过这台服务器（同一个地址、端口和账号）：在它上面部署更多实例即可",
            )

    # ---------------------------------------------------------------- 服务器

    async def create(
        self, access: WorkspaceAccess, body: AgentServerCreate, *, client_ip: str
    ) -> AgentServerDetail:
        """添加一台服务器：解析地址、读取主机公钥（不登录）。确认要另外一步。"""
        address, key = await self._probe(access, body.host, body.port)
        await self._duplicate(access, address, body.port, body.username, skip=uuid.uuid4())
        server = AgentServer(workspace_id=access.workspace.id, name=body.name, kind="ssh")
        self.db.add(server)
        await self.db.flush()
        target = AgentSshTarget(
            workspace_id=access.workspace.id,
            server_id=server.id,
            host=body.host,
            address=address,
            port=body.port,
            username=body.username,
            host_key_algorithm=key.algorithm,
            host_key=key.public_key,
            host_key_fingerprint=key.fingerprint,
            created_by=access.user_id,
            version=1,
        )
        self.db.add(target)
        await self.audit.record(
            "agents.server.created",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={
                "name": body.name,
                "host": body.host,
                "address": address,
                "port": body.port,
                "username": body.username,
                "host_key_fingerprint": key.fingerprint,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(server)
        return await self._detail(server)

    async def rename(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentServerUpdate,
        *,
        client_ip: str,
    ) -> AgentServerOut:
        server = await self._server(access, server_id, lock=True)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if server.name != body.name:
            await self.audit.record(
                "agents.server.renamed",
                workspace_ids=[server.workspace_id],
                actor_id=access.user_id,
                resource_type="agent_server",
                resource_id=server.id,
                metadata={"name": body.name, "previous": server.name},
                client_ip=client_ip,
            )
            server.name = body.name
            server.version += 1
        await self.db.commit()
        await self.db.refresh(server)
        return await self._out(server)

    async def set_address(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentServerAddress,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """更换地址或账号：重新读取主机公钥，要重新确认。已经装了专用公钥时先吊销。"""
        server = await self._server(access, server_id)
        target = await self._ssh_target(server, lock=False)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if target.client_key_ref is not None:
            raise ApiError(
                409,
                "AGENT_SSH_KEY_INSTALLED",
                "这台服务器上已经装了工作台的专用公钥：先吊销，再更换地址",
            )
        # 探测要几秒：探测完再锁行、重新检查（旧指纹的确认绝不能落到新公钥上）
        address, key = await self._probe(access, body.host, body.port)
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if target.client_key_ref is not None:
            raise ApiError(
                409,
                "AGENT_SSH_KEY_INSTALLED",
                "这台服务器上已经装了工作台的专用公钥：先吊销，再更换地址",
            )
        if await self._busy(target):
            raise _job_running()
        await self._duplicate(access, address, body.port, body.username, skip=target.id)
        target.host = body.host
        target.address = address
        target.port = body.port
        target.username = body.username
        target.host_key_algorithm = key.algorithm
        target.host_key = key.public_key
        target.host_key_fingerprint = key.fingerprint
        target.host_key_confirmed_at = None
        target.host_key_confirmed_by = None
        target.prepared_at = None
        target.version += 1
        server.version += 1
        await self.audit.record(
            "agents.server.address_set",
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={
                "host": body.host,
                "address": address,
                "port": body.port,
                "username": body.username,
                "host_key_fingerprint": key.fingerprint,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(server)
        return await self._detail(server)

    async def confirm(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentSshConfirm,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """owner 核对过指纹：固定这把主机公钥。指纹必须与探测时记下的一致。"""
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if await self._busy(target):
            raise _job_running()
        if body.fingerprint != target.host_key_fingerprint:
            raise ApiError(
                409,
                "AGENT_SSH_FINGERPRINT_MISMATCH",
                "指纹与工作台读到的主机公钥不一致；请重新填写地址并核对",
            )
        target.host_key_confirmed_at = _now()
        target.host_key_confirmed_by = access.user_id
        target.version += 1
        server.version += 1
        await self.audit.record(
            "agents.server.host_key_confirmed",
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={"host_key_fingerprint": target.host_key_fingerprint},
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(server)
        return await self._detail(server)

    def _ready_to_run(self) -> None:
        if self.runner is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "部署任务暂时不可用，请稍后重试")
        if self.settings.credential_encryption_key is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")

    async def prepare(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentServerPrepare,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """准备服务器：用一次登录密码装上专用公钥、需要时用 sudo 安装 Docker（只跑前五步）。"""
        self._ready_to_run()
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if await self._busy(target):
            raise _job_running()
        await self._start_job(
            access,
            target,
            kind="prepare",
            instance_id=None,
            password=body.password,
            metadata={"host": target.host, "port": target.port, "username": target.username},
            client_ip=client_ip,
        )
        await self.db.refresh(server)
        return await self._detail(server)

    async def _start_job(
        self,
        access: WorkspaceAccess,
        target: AgentSshTarget,
        *,
        kind: str,
        instance_id: uuid.UUID | None,
        metadata: dict[str, object],
        client_ip: str,
        password: SecretStr | None = None,
        remove_files: bool = False,
    ) -> AgentSshJob:
        """新建任务（running）并交给进程内的后台协程；调用方已锁行并检查过没有进行中的任务。

        密码只交给协程（`SshJobRunner.start`），不写进任务记录、审计或日志。
        """
        assert self.runner is not None  # noqa: S101 - 调用方已检查
        steps = {"prepare": prov.PREPARE_STEPS, "install": prov.STEPS}.get(
            kind, prov.UNINSTALL_STEPS
        )
        job = AgentSshJob(
            id=uuid.uuid4(),
            workspace_id=target.workspace_id,
            instance_id=instance_id,
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
            resource_type="agent_instance" if instance_id else "agent_server",
            resource_id=instance_id or target.server_id,
            metadata={
                **metadata,
                "server_id": str(target.server_id),
                "with_password": password is not None,
            },
            client_ip=client_ip,
        )
        # 提交前先登记：提交之后、启动协程之前，别的请求不会把它当成失效的任务
        self.runner.reserve(job.id)
        try:
            await self.db.commit()
        except IntegrityError as error:
            # 部分唯一索引：另一个请求同时在这台服务器上开始了任务
            self.runner.release(job.id)
            await self.db.rollback()
            raise _job_running() from error
        except BaseException:
            self.runner.release(job.id)
            raise
        self.runner.start(job.id, password, client_ip=client_ip, remove_files=remove_files)
        return job

    async def revoke(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentSshRevoke,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """吊销专用密钥：用它登录，从 authorized_keys 删掉这一行，再删除工作台保存的私钥。

        服务器连不上时，force=true 只删除工作台保存的私钥，并提示 owner 手动删除那一行。
        吊销后这台服务器上的实例不能再经工作台运维或卸载，要重新准备服务器。
        """
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if target.client_key_ref is None or not target.client_public_key:
            raise _key_missing()
        if await self._busy(target):
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
        target.prepared_at = None
        target.version += 1
        server.version += 1
        await self.audit.record(
            "agents.server.key_revoked",
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={
                "client_key_fingerprint": _client_fingerprint(public),
                "remote_removed": reason is None,
                "reason": reason,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(server)
        warning = None
        if reason is not None:
            warning = (
                f"没能连上服务器（{reason}），只删除了工作台保存的私钥；"
                f"请在服务器的 ~/.ssh/authorized_keys 里手动删除这一行：{public}"
            )
        return await self._detail(server, warning=warning)

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

    async def remove(
        self, access: WorkspaceAccess, server_id: uuid.UUID, *, version: int, client_ip: str
    ) -> None:
        """移除服务器。SSH 服务器要先卸载上面的实例、吊销专用密钥；
        自动识别的服务器直接移除，上面的实例改为“未归属”（下一次上报会重新识别）。"""
        server = await self._server(access, server_id, lock=True)
        if version != server.version:
            raise _version_conflict(server.version)
        target = await self._target(server, lock=True)
        if target is not None:
            if await self._busy(target):
                raise _job_running()
            if target.client_key_ref is not None:
                raise ApiError(
                    409,
                    "AGENT_SSH_KEY_INSTALLED",
                    "这台服务器上已经装了工作台的专用公钥：先吊销，再移除",
                )
        deployed = await self.db.scalar(
            select(func.count())
            .select_from(AgentDeployment)
            .where(AgentDeployment.server_id == server.id)
        )
        if deployed:
            raise ApiError(
                409,
                "AGENT_SERVER_HAS_DEPLOYMENTS",
                "这台服务器上还有部署的实例：先卸载它们",
                details={"deployments": int(deployed)},
            )
        await self.db.execute(
            update(AgentInstance)
            .where(AgentInstance.server_id == server.id)
            .values(server_id=None, server_manual=False)
        )
        await self.audit.record(
            "agents.server.removed",
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={"name": server.name, "kind": server.kind},
            client_ip=client_ip,
        )
        await self.db.delete(server)
        await self.db.commit()

    # ---------------------------------------------------------------- 实例

    def _capacity_warning(self, server: AgentServer) -> str | None:
        """D4：不设每台的实例上限；剩余内存或磁盘不多时提醒（不拦）。"""
        latest = server.latest or {}
        notes = []
        available = latest.get("mem_available")
        if available is not None and available < MEMORY_WARN_BYTES:
            notes.append(f"剩余内存约 {available // (1 << 20)} MiB")
        for prefix, label in (("data", "数据分区"), ("root", "Docker 分区")):
            used = _percent_used(latest.get(f"{prefix}_total"), latest.get(f"{prefix}_free"))
            if used is not None and used > DISK_WARN_PERCENT:
                notes.append(f"{label}已用 {used:.0f}%")
        if not notes:
            return None
        return (
            "这台服务器"
            + "、".join(notes)
            + "：一个空闲的 OpenClaw 约占 1 GiB 内存，部署前确认够用"
        )

    async def deploy(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentDeployIn,
        *,
        client_ip: str,
    ) -> AgentDeploymentState:
        """把实例部署到一台 SSH 服务器：只替换这个实例自己的目录与 Compose 项目。"""
        self._ready_to_run()
        instance = await self._instance(access, instance_id, lock=True)
        if body.version != instance.version:
            raise _version_conflict(instance.version)
        if instance.status == "disabled":
            raise _disabled()
        if instance.network_mode != "public":
            # 部署到任意一台服务器走外网模式（ADR 第 3.1 节）：服务器通常不在工作台的私网里
            raise ApiError(
                409,
                "AGENT_SSH_PUBLIC_ONLY",
                "部署到服务器只支持外网模式的实例",
                details={"network_mode": instance.network_mode},
            )
        if not self.settings.agent_public_url:
            raise ApiError(
                409,
                "AGENT_INGRESS_NOT_CONFIGURED",
                "外网接入入口还没有配置（AGENT_PUBLIC_URL）",
                details={"network_mode": instance.network_mode},
            )
        server = await self._server(access, body.server_id, lock=True)
        deployment = await self.db.scalar(
            select(AgentDeployment)
            .where(AgentDeployment.instance_id == instance.id)
            .with_for_update()
        )
        if deployment is not None and deployment.server_id != server.id:
            raise ApiError(
                409,
                "AGENT_DEPLOYED_ELSEWHERE",
                "这个实例已经部署在另一台服务器上：先在那台服务器上卸载",
                details={"server_id": str(deployment.server_id)},
            )
        target = await self._ssh_target(server)
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if body.password is None and target.client_key_ref is None:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "这台服务器还没准备：先准备服务器，或填写登录密码",
            )
        if await self._busy(target):
            raise _job_running()
        if deployment is None:
            deployment = AgentDeployment(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                server_id=server.id,
                created_by=access.user_id,
            )
            self.db.add(deployment)
        warning = self._capacity_warning(server)
        await self._start_job(
            access,
            target,
            kind="install",
            instance_id=instance.id,
            password=body.password,
            metadata={
                "host": target.host,
                "port": target.port,
                "username": target.username,
                "host_key_fingerprint": target.host_key_fingerprint,
            },
            client_ip=client_ip,
        )
        await self.db.refresh(instance)
        return await self._deployment_state(instance, warning=warning)

    async def undeploy(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentUndeployIn,
        *,
        client_ip: str,
    ) -> AgentDeploymentState:
        """卸载这个实例：只用专用密钥。停止它的容器、删掉它的定时任务，可选删除它的目录。"""
        self._ready_to_run()
        instance = await self._instance(access, instance_id, lock=True)
        if body.version != instance.version:
            raise _version_conflict(instance.version)
        deployment = await self.db.scalar(
            select(AgentDeployment).where(AgentDeployment.instance_id == instance.id)
        )
        if deployment is None:
            raise ApiError(404, "NOT_FOUND", "这个实例没有部署到服务器上")
        server = await self.db.get(AgentServer, deployment.server_id)
        if server is None:
            raise ApiError(404, "NOT_FOUND", "服务器不存在")
        target = await self._ssh_target(server)
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if target.client_key_ref is None:
            raise _key_missing()
        if await self._busy(target):
            raise _job_running()
        await self._start_job(
            access,
            target,
            kind="uninstall",
            instance_id=instance.id,
            remove_files=body.remove_files,
            metadata={"host": target.host, "port": target.port, "remove_files": body.remove_files},
            client_ip=client_ip,
        )
        await self.db.refresh(instance)
        return await self._deployment_state(instance)

    async def assign(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentServerAssign,
        *,
        client_ip: str,
    ) -> AgentDeploymentState:
        """手动指定实例所在的服务器（D3：手动指定的以手动为准）；null 改回按指纹自动识别。"""
        instance = await self._instance(access, instance_id, lock=True)
        if body.version != instance.version:
            raise _version_conflict(instance.version)
        deployment = await self.db.scalar(
            select(AgentDeployment).where(AgentDeployment.instance_id == instance.id)
        )
        if deployment is not None:
            raise ApiError(
                409,
                "AGENT_DEPLOYED",
                "这个实例是经 SSH 部署的，所在服务器以部署为准；要换服务器请先卸载",
            )
        server: AgentServer | None = None
        if body.server_id is not None:
            server = await self._server(access, body.server_id)
        previous = instance.server_id
        instance.server_id = server.id if server is not None else None
        instance.server_manual = server is not None
        instance.version += 1
        await self.db.flush()
        if previous != instance.server_id:
            await drop_if_empty_auto(self.db, previous)
        await self.audit.record(
            "agents.instance.server_assigned",
            workspace_ids=[instance.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "server_id": str(server.id) if server is not None else None,
                "previous": str(previous) if previous else None,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(instance)
        return await self._deployment_state(instance)
