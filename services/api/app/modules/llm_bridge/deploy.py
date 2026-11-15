"""页面上一键部署 MCP 管理器（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md；人类 2026-11-14 要求）。

部署到“智能体服务器”或“容器服务器”页面登记的服务器上，用那里已有的登录方式
（智能体服务器：保存的密码；容器服务器：专用私钥），按确认过的主机公钥。工作台自己不挂
Docker socket；在服务器上只执行写死的几条命令（`deploy_bundle.py`），不拼接用户输入的文字。

- 部署：检查服务器 → 上传构建文件 → 在服务器上构建镜像 → 启动 →（别的服务器）装转发密钥 →
  工作台按将来实际使用的那条路连上它、写入控制的平台 → 登记。
  管理令牌由工作台生成，只在上传的 bridge.env（权限 600）和加密后的登记里，不进任务记录和日志。
- 网络：和工作台同一台机器时加入工作台的内部网络；其他情况只监听那台服务器的本机地址，
  工作台经专用的 SSH 通道访问（`tunnel.py`）。
- 登录账号不能直接用 Docker（容器服务器的账号按设计不在 docker 组）：经 sudo；
  sudo 要密码时用请求里给的登录密码，只在这个任务的内存里，不保存、不进日志。
- 重新部署（升级）：同一个目录、同一枚令牌、同一个数据卷，重新上传、构建、启动。
- 重启：`docker compose restart`，再确认连得上。
- 卸载：停掉并删除容器、删除部署目录和转发密钥、去掉登记；数据卷默认保留。

这些任务不走两个服务器页面的准入规则，也不和它们的任务互相排队（第 2、8 节）：
只有“同一台服务器同时只跑一个 MCP 管理器的任务”这一条。

任务在 API 进程里运行（借用 `SshJobRunner.launch`）。进程重启后协程不在了：
读到这样的记录时标为中断。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import asyncssh
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agent_server_credentials import ServerCredentialError
from app.domain.agent_server_ssh import connect_agent_server
from app.domain.agent_servers import AgentServerService
from app.domain.agent_ssh_install import JobGone, JobRecorder, SshJobRunner
from app.domain.audit import AuditTrail
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.domain.runner_ssh import runner_ssh_context
from app.integrations import ssh
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker
from app.models import AgentSshTarget, RunnerHost, RunnerSshTarget
from app.modules.llm_bridge import deploy_bundle as bundle
from app.modules.llm_bridge import tunnel
from app.modules.llm_bridge.client import BridgeCallError, BridgeClient, bridge_client
from app.modules.llm_bridge.deploy_schemas import (
    LlmBridgeDeployIn,
    LlmBridgeDeployJobList,
    LlmBridgeDeployJobOut,
    LlmBridgeDeployTargetList,
    LlmBridgeDeployTargetOut,
    LlmBridgeRedeployIn,
    LlmBridgeRestartIn,
    LlmBridgeServerManagerList,
    LlmBridgeServerManagerOut,
    LlmBridgeUninstallIn,
)
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeDeployJob, LlmBridgeInstall
from app.modules.llm_bridge.service import (
    LlmBridgeService,
    install_out,
    remove_bridge,
    secret_context,
)

logger = logging.getLogger(__name__)

AGENT_SERVER, RUNNER_HOST = "agent_server", "runner_host"
STEPS: dict[str, tuple[str, ...]] = {
    "deploy": ("connect", "inspect", "upload", "build", "start", "tunnel", "verify", "register"),
    "redeploy": ("connect", "inspect", "upload", "build", "start", "verify"),
    "restart": ("connect", "restart", "verify"),
    "uninstall": ("connect", "stop", "remove", "unregister"),
}
INSPECT_TIMEOUT = 60.0
BUILD_TIMEOUT = 1800.0
START_TIMEOUT = 300.0
START_WAIT = 240
VERIFY_TIMEOUT = 90.0
VERIFY_POLL = 3.0
STATUS_TIMEOUT = 8.0
LIST_LIMIT = 30

# 不能部署的原因 → 给人看的说明。智能体服务器上“正在部署实例”（busy）不算：互不排队
_BLOCKS = {
    "not_ssh": "这台服务器不是经 SSH 登记的：工作台登录不上去",
    "credentials_required": "还没有保存这台服务器的登录密码",
    "credential_unreadable": "保存的登录密码读不出来：重新保存一次",
    "host_key_unconfirmed": "还没有确认这台服务器的主机公钥指纹",
    "host_key_changed": "服务器的主机公钥变了：先在服务器页面重新确认",
    "auth_failed": "保存的登录密码被服务器拒绝：先在服务器页面更新密码",
    "not_initialized": "这台服务器还没有初始化（安装 Docker）",
    "initializing": "这台服务器正在初始化，完成后再部署",
    "initialization_failed": "这台服务器的初始化失败了：先在服务器页面重新初始化",
    "initialization_interrupted": "这台服务器的初始化被中断了：先在服务器页面重新初始化",
    "key_missing": "这台服务器没有专用密钥：先在容器服务器页面用 SSH 部署一次",
}


class DeployFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def new_token() -> str:
    """管理器的管理令牌：64 个十六进制字符。"""
    return secrets.token_hex(32)


def workbench_hostname() -> str:
    """工作台后端容器的主机名：检查脚本用它判断服务器和工作台是不是同一台机器。"""
    return socket.gethostname()


def _now() -> datetime:
    return datetime.now(UTC)


def job_out(row: LlmBridgeDeployJob, *, with_log: bool = False) -> LlmBridgeDeployJobOut:
    return LlmBridgeDeployJobOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        target_kind=row.target_kind,
        target_id=row.server_id if row.target_kind == AGENT_SERVER else row.runner_host_id,
        server_name=row.server_name,
        bridge_id=row.bridge_id,
        name=row.name,
        platforms=list(row.platforms or []),
        mode=row.mode,
        steps=list(row.steps or []),
        log=row.log if with_log else None,
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@dataclass(frozen=True)
class _Target:
    """选中的服务器：哪一类、是哪台、用哪条 SSH 记录登录。"""

    kind: str
    id: uuid.UUID
    name: str
    ssh_target_id: uuid.UUID
    revision: int | None = None  # 智能体服务器：保存的密码的版本


@dataclass(frozen=True)
class _Facts:
    """任务开始时定下来的东西：协程里不再依赖请求的数据库会话。"""

    job_id: uuid.UUID
    kind: str
    workspace_id: uuid.UUID
    actor_id: uuid.UUID | None
    target: _Target
    install_id: uuid.UUID
    slot: str
    name: str
    platforms: tuple[str, ...]
    bridge_id: uuid.UUID | None = None
    # 重新部署 / 重启 / 卸载：上一次安装时定下的
    install_dir: str | None = None
    mode: str | None = None
    port: int | None = None
    tunnel_public_key: str | None = None
    remove_data: bool = False
    # 新部署时指定：即使和工作台同机也走 SSH 通道
    force_tunnel: bool = False


class LlmBridgeDeployService:
    def __init__(
        self,
        bridges: LlmBridgeService,
        servers: AgentServerService,
        runner: SshJobRunner | None,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
    ) -> None:
        self.bridges = bridges
        self.servers = servers
        self.db = bridges.db
        self.settings = bridges.settings
        self.runtime = bridges.runtime
        self.runner = runner
        self.sessionmaker = sessionmaker

    # ---- 读 ----
    async def _settle(self, rows: list[LlmBridgeDeployJob]) -> None:
        """记录还是 running、但本进程里没有它的协程（API 重启过）：标为中断，不让它一直挡着。"""
        changed = False
        for row in rows:
            if row.status != "running":
                continue
            if self.runner is not None and self.runner.running(row.id):
                continue
            row.status = "interrupted"
            row.error_code = "interrupted"
            row.error_message = "任务已不在运行（工作台重启过）；可以重新开始"
            row.finished_at = _now()
            changed = True
        if changed:
            await self.db.commit()

    async def jobs(self, access: WorkspaceAccess) -> LlmBridgeDeployJobList:
        rows = list(
            (
                await self.db.scalars(
                    select(LlmBridgeDeployJob)
                    .where(LlmBridgeDeployJob.workspace_id == access.workspace.id)
                    .order_by(LlmBridgeDeployJob.started_at.desc(), LlmBridgeDeployJob.id)
                    .limit(LIST_LIMIT)
                )
            ).all()
        )
        await self._settle(rows)
        return LlmBridgeDeployJobList(
            jobs=[job_out(row) for row in rows], available=bundle.source_dir() is not None
        )

    async def _job(self, access: WorkspaceAccess, job_id: uuid.UUID) -> LlmBridgeDeployJob:
        row = await self.db.scalar(
            select(LlmBridgeDeployJob).where(
                LlmBridgeDeployJob.id == job_id,
                LlmBridgeDeployJob.workspace_id == access.workspace.id,
            )
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "没有这个部署任务")
        return row

    async def get(self, access: WorkspaceAccess, job_id: uuid.UUID) -> LlmBridgeDeployJobOut:
        row = await self._job(access, job_id)
        await self._settle([row])
        return job_out(row, with_log=True)

    async def _manager_counts(self, workspace_id: uuid.UUID) -> dict[tuple[str, uuid.UUID], int]:
        counts: dict[tuple[str, uuid.UUID], int] = {}
        for kind, column in (
            (AGENT_SERVER, LlmBridgeInstall.server_id),
            (RUNNER_HOST, LlmBridgeInstall.runner_host_id),
        ):
            rows = await self.db.execute(
                select(column, func.count())
                .where(
                    LlmBridgeInstall.workspace_id == workspace_id,
                    LlmBridgeInstall.target_kind == kind,
                    column.is_not(None),
                )
                .group_by(column)
            )
            for target_id, count in rows:
                if target_id is not None:
                    counts[(kind, target_id)] = int(count)
        return counts

    async def targets(self, access: WorkspaceAccess) -> LlmBridgeDeployTargetList:
        """两类服务器里可以部署的服务器；不能部署的带上原因。只读本地记录，不连服务器。"""
        counts = await self._manager_counts(access.workspace.id)
        out: list[LlmBridgeDeployTargetOut] = []
        for server in (await self.servers.list_servers(access)).items:
            reason: str | None = server.deploy_block_reason if server.kind == "ssh" else "not_ssh"
            if reason == "busy":  # 那台服务器上正在部署实例：不影响部署 MCP 管理器
                reason = None
            out.append(
                LlmBridgeDeployTargetOut(
                    kind=AGENT_SERVER,
                    id=server.id,
                    name=server.name,
                    host=server.ssh.host if server.ssh is not None else None,
                    deployable=reason is None,
                    block_reason=reason,
                    block_message=_BLOCKS.get(reason, "这台服务器现在不能部署") if reason else None,
                    may_need_password=False,
                    managers=counts.get((AGENT_SERVER, server.id), 0),
                )
            )
        hosts = (
            await self.db.execute(
                select(RunnerHost, RunnerSshTarget)
                .outerjoin(
                    RunnerSshTarget,
                    (RunnerSshTarget.host_id == RunnerHost.id)
                    & (RunnerSshTarget.workspace_id == RunnerHost.workspace_id),
                )
                .where(RunnerHost.workspace_id == access.workspace.id)
                .order_by(RunnerHost.created_at, RunnerHost.id)
            )
        ).all()
        for host, target in hosts:
            reason = _runner_block(target)
            out.append(
                LlmBridgeDeployTargetOut(
                    kind=RUNNER_HOST,
                    id=host.id,
                    name=host.name,
                    host=target.host if target is not None else None,
                    deployable=reason is None,
                    block_reason=reason,
                    block_message=_BLOCKS[reason] if reason else None,
                    may_need_password=target is not None and target.username != "root",
                    managers=counts.get((RUNNER_HOST, host.id), 0),
                )
            )
        return LlmBridgeDeployTargetList(targets=out, available=bundle.source_dir() is not None)

    async def managers_on(
        self, access: WorkspaceAccess, target_kind: str, target_id: uuid.UUID
    ) -> LlmBridgeServerManagerList:
        """某台服务器上的 MCP 管理器（两个服务器页面的入口）：带现在连不连得上。"""
        column = (
            LlmBridgeInstall.server_id
            if target_kind == AGENT_SERVER
            else LlmBridgeInstall.runner_host_id
        )
        rows = (
            await self.db.execute(
                select(LlmBridge, LlmBridgeInstall)
                .join(LlmBridgeInstall, LlmBridgeInstall.bridge_id == LlmBridge.id)
                .where(
                    LlmBridge.workspace_id == access.workspace.id,
                    LlmBridgeInstall.target_kind == target_kind,
                    column == target_id,
                )
                .order_by(LlmBridgeInstall.deployed_at, LlmBridge.id)
            )
        ).all()
        # 先在数据库会话里把每座桥的客户端准备好，再同时探测（探测不碰数据库）
        prepared: list[tuple[LlmBridge, LlmBridgeInstall, BridgeClient | None]] = []
        for bridge, install in rows:
            client = await self.bridges.client(bridge) if bridge.enabled else None
            prepared.append((bridge, install, client))
        tag = bundle.current_tag()
        await self.db.commit()

        async def probe(client: BridgeClient | None) -> dict[str, Any] | None:
            if client is None:
                return None
            try:
                data = await asyncio.wait_for(client.admin("GET", "/status"), STATUS_TIMEOUT)
            except (TimeoutError, BridgeCallError, ConnectorError):
                return {}
            return data if isinstance(data, dict) else {}

        probed = await asyncio.gather(*(probe(client) for _, _, client in prepared))
        managers = []
        for (bridge, install, _), data in zip(prepared, probed, strict=True):
            accounts = (data or {}).get("accounts")
            platforms = (data or {}).get("platforms")
            managers.append(
                LlmBridgeServerManagerOut(
                    bridge_id=bridge.id,
                    name=bridge.name,
                    enabled=bridge.enabled,
                    reachable=None if data is None else bool(data),
                    version=str(data["version"]) if data and data.get("version") else None,
                    platforms=[str(p) for p in platforms]
                    if isinstance(platforms, list)
                    else list(install.platforms or []),
                    logged_in=sorted(
                        slot
                        for slot, account in accounts.items()
                        if isinstance(account, dict) and account.get("state") == "logged_in"
                    )
                    if isinstance(accounts, dict)
                    else [],
                    deployment=install_out(install, tag),
                )
            )
        return LlmBridgeServerManagerList(
            managers=managers, available=bundle.source_dir() is not None
        )

    # ---- 开始一个任务 ----
    def _ready(self) -> tuple[SshJobRunner, async_sessionmaker[AsyncSession]]:
        if bundle.source_dir() is None:
            raise ApiError(
                503,
                "DEPLOY_UNAVAILABLE",
                "这个工作台里没有带 MCP 管理器的构建文件，不能一键部署："
                "请按部署指南手动部署后再添加",
            )
        return self._runner()

    def _runner(self) -> tuple[SshJobRunner, async_sessionmaker[AsyncSession]]:
        if self.runner is None or self.sessionmaker is None:
            raise ApiError(503, "DEPLOY_UNAVAILABLE", "部署任务暂时不能运行，稍后重试")
        try:
            CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise ApiError(503, "DEPLOY_UNAVAILABLE", "密钥库未配置加密密钥") from error
        return self.runner, self.sessionmaker

    async def _existing(
        self, access: WorkspaceAccess, request_id: str
    ) -> LlmBridgeDeployJob | None:
        return await self.db.scalar(
            select(LlmBridgeDeployJob).where(
                LlmBridgeDeployJob.workspace_id == access.workspace.id,
                LlmBridgeDeployJob.request_id == request_id,
            )
        )

    async def _target(self, access: WorkspaceAccess, kind: str, target_id: uuid.UUID) -> _Target:
        """锁住服务器，确认工作台现在登录得上去，并且没有别的 MCP 管理器任务在这台服务器上跑。

        不看两个服务器页面自己的准入：智能体服务器上正在部署实例、资源吃紧，
        容器服务器的配额和放置，都不拦 MCP 管理器。
        """
        if kind == AGENT_SERVER:
            server = await self.servers._server(access, target_id, lock=True)
            try:
                ssh_target = await self.servers.deployable_target(server)
            except ApiError as error:
                if error.code != "AGENT_SSH_JOB_RUNNING":
                    raise
                found = await self.servers._target(server, lock=True)
                assert found is not None  # noqa: S101 - 有任务在跑说明是 SSH 服务器
                ssh_target = found
            target = _Target(
                kind, server.id, server.name, ssh_target.id, ssh_target.credential_revision
            )
            column = LlmBridgeDeployJob.server_id
        else:
            host = await self.db.scalar(
                select(RunnerHost)
                .where(RunnerHost.id == target_id, RunnerHost.workspace_id == access.workspace.id)
                .with_for_update()
            )
            if host is None:
                raise ApiError(404, "NOT_FOUND", "没有这台容器服务器")
            runner_target = await self.db.scalar(
                select(RunnerSshTarget).where(
                    RunnerSshTarget.host_id == host.id,
                    RunnerSshTarget.workspace_id == access.workspace.id,
                )
            )
            reason = _runner_block(runner_target)
            if reason is not None or runner_target is None:
                raise ApiError(
                    409,
                    "DEPLOY_BLOCKED",
                    _BLOCKS[reason or "not_ssh"],
                    details={"reason": reason or "not_ssh"},
                )
            target = _Target(kind, host.id, host.name, runner_target.id)
            column = LlmBridgeDeployJob.runner_host_id
        busy = list(
            (
                await self.db.scalars(
                    select(LlmBridgeDeployJob).where(
                        column == target.id, LlmBridgeDeployJob.status == "running"
                    )
                )
            ).all()
        )
        await self._settle(busy)
        if any(row.status == "running" for row in busy):
            raise ApiError(409, "DEPLOY_BUSY", "这台服务器上有一个 MCP 管理器的任务还在进行")
        return target

    def _new_job(
        self,
        access: WorkspaceAccess,
        kind: str,
        target: _Target,
        *,
        name: str,
        platforms: list[str],
        slot: str,
        request_id: str,
        install: LlmBridgeInstall | None = None,
    ) -> LlmBridgeDeployJob:
        return LlmBridgeDeployJob(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            kind=kind,
            status="running",
            target_kind=target.kind,
            server_id=target.id if target.kind == AGENT_SERVER else None,
            runner_host_id=target.id if target.kind == RUNNER_HOST else None,
            server_name=target.name,
            bridge_id=install.bridge_id if install else None,
            name=name,
            platforms=platforms,
            slot=slot,
            mode=install.mode if install else None,
            install_dir=install.install_dir if install else None,
            port=install.port if install else None,
            request_id=request_id,
            requested_by=access.user_id,
            steps=[{"key": key, "status": "pending", "detail": None} for key in STEPS[kind]],
            log="",
        )

    async def _launch(
        self,
        access: WorkspaceAccess,
        job: LlmBridgeDeployJob,
        facts: _Facts,
        token: str,
        password: SecretStr | None,
    ) -> LlmBridgeDeployJobOut:
        runner, sessionmaker = self._runner()
        self.db.add(job)
        try:
            await self.db.flush()
        except IntegrityError as error:
            await self.db.rollback()
            raise ApiError(
                409, "DEPLOY_BUSY", "这台服务器上有一个 MCP 管理器的任务还在进行"
            ) from error
        await self.bridges.audit(
            access,
            f"{job.kind}_started",
            job.bridge_id or job.id,
            {
                "job": str(job.id),
                "name": job.name,
                "server": job.server_name,
                "target_kind": job.target_kind,
                "with_password": password is not None,
            },
        )
        runner.reserve(job.id)
        try:
            await self.db.commit()
        except Exception:
            runner.release(job.id)
            raise
        out = job_out(job)
        runner.launch(
            job.id, _Job(sessionmaker, self.settings, self.runtime, facts, token, password).run()
        )
        return out

    async def deploy(
        self, access: WorkspaceAccess, body: LlmBridgeDeployIn
    ) -> LlmBridgeDeployJobOut:
        existing = await self._existing(access, body.request_id)
        if existing is not None:
            return job_out(existing)
        self._ready()
        name = body.name.strip()
        await self.bridges._name_free(access.workspace.id, name, skip=None)
        target = await self._target(access, body.target_kind, body.target_id)
        platforms: list[str] = list(dict.fromkeys(body.platforms))
        job = self._new_job(
            access,
            "deploy",
            target,
            name=name,
            platforms=platforms,
            slot=uuid.uuid4().hex[:12],
            request_id=body.request_id,
        )
        facts = _Facts(
            job_id=job.id,
            kind="deploy",
            workspace_id=access.workspace.id,
            actor_id=access.user_id,
            target=target,
            install_id=uuid.uuid4(),
            slot=job.slot,
            name=name,
            platforms=tuple(platforms),
            force_tunnel=body.network == "tunnel",
        )
        return await self._launch(access, job, facts, new_token(), body.sudo_password)

    async def _installed(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID
    ) -> tuple[LlmBridge, LlmBridgeInstall]:
        row = await self.bridges.bridge(access.workspace.id, bridge_id)
        install = await self.db.scalar(
            select(LlmBridgeInstall).where(LlmBridgeInstall.bridge_id == row.id)
        )
        if install is None:
            raise ApiError(
                409, "NOT_DEPLOYED_HERE", "这个 MCP 管理器不是工作台部署的：只能删除登记"
            )
        return row, install

    async def _operate(
        self,
        access: WorkspaceAccess,
        bridge_id: uuid.UUID,
        kind: str,
        body: LlmBridgeRedeployIn | LlmBridgeRestartIn | LlmBridgeUninstallIn,
    ) -> LlmBridgeDeployJobOut:
        existing = await self._existing(access, body.request_id)
        if existing is not None:
            return job_out(existing)
        if kind == "redeploy":
            self._ready()
        else:
            self._runner()
        row, install = await self._installed(access, bridge_id)
        target_id = (
            install.server_id if install.target_kind == AGENT_SERVER else install.runner_host_id
        )
        if target_id is None:
            raise ApiError(
                409,
                "SERVER_REMOVED",
                "部署它的那台服务器已经从工作台移除，工作台登录不上去了："
                "卸载时选“只删除登记”，容器要到服务器上手动处理",
            )
        if install.needs_sudo and body.sudo_password is None:
            raise ApiError(
                409,
                "SUDO_PASSWORD_REQUIRED",
                "这台服务器的登录账号用 Docker 要 sudo 密码：填写登录密码（只用这一次，不保存）",
            )
        token = (
            self.runtime.credentials()
            .resolve(row.token_ref, context=secret_context(row.workspace_id, row.id))
            .get_secret_value()
        )
        target = await self._target(access, install.target_kind, target_id)
        job = self._new_job(
            access,
            kind,
            target,
            name=row.name,
            platforms=list(install.platforms or []),
            slot=install.slot,
            request_id=body.request_id,
            install=install,
        )
        facts = _Facts(
            job_id=job.id,
            kind=kind,
            workspace_id=access.workspace.id,
            actor_id=access.user_id,
            target=target,
            install_id=install.id,
            slot=install.slot,
            name=row.name,
            platforms=tuple(install.platforms or []),
            bridge_id=row.id,
            install_dir=install.install_dir,
            mode=install.mode,
            port=install.port,
            tunnel_public_key=install.tunnel_public_key,
            remove_data=isinstance(body, LlmBridgeUninstallIn) and body.remove_data,
        )
        return await self._launch(access, job, facts, token, body.sudo_password)

    async def redeploy(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgeRedeployIn
    ) -> LlmBridgeDeployJobOut:
        return await self._operate(access, bridge_id, "redeploy", body)

    async def restart(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgeRestartIn
    ) -> LlmBridgeDeployJobOut:
        return await self._operate(access, bridge_id, "restart", body)

    async def uninstall(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgeUninstallIn
    ) -> LlmBridgeDeployJobOut:
        if body.force:
            return await self._forget(access, bridge_id, body)
        return await self._operate(access, bridge_id, "uninstall", body)

    async def _forget(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgeUninstallIn
    ) -> LlmBridgeDeployJobOut:
        """只删除登记：不连服务器（服务器连不上、或已经从工作台移除）。写明服务器上还留着什么。"""
        existing = await self._existing(access, body.request_id)
        if existing is not None:
            return job_out(existing)
        row, install = await self._installed(access, bridge_id)
        left = [
            f"容器和数据卷（Compose 项目 {bundle.project_name(install.slot)}）",
            f"目录 {install.install_dir}（里面有管理令牌）",
        ]
        if install.tunnel_public_key:
            left.append(
                f"{install.username} 的 authorized_keys 里备注是"
                f" {bundle.tunnel_comment(install.slot)} 的那一行"
            )
        detail = f"服务器「{install.server_name}」上还留着：" + "；".join(left)
        job = LlmBridgeDeployJob(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            kind="uninstall",
            status="succeeded",
            target_kind=install.target_kind,
            server_id=install.server_id,
            runner_host_id=install.runner_host_id,
            server_name=install.server_name,
            bridge_id=None,
            name=row.name,
            platforms=list(install.platforms or []),
            slot=install.slot,
            mode=install.mode,
            install_dir=install.install_dir,
            port=install.port,
            request_id=body.request_id,
            requested_by=access.user_id,
            steps=[{"key": "unregister", "status": "done", "detail": detail}],
            log=f"只删除了工作台里的登记，没有连服务器。\n{detail}\n",
            finished_at=_now(),
        )
        self.db.add(job)
        await self.bridges.audit(
            access,
            "bridge_deleted",
            row.id,
            {"name": row.name, "uninstalled": False, "forced": True, "server": install.server_name},
        )
        tunnel.POOL.drop(install.id)
        await remove_bridge(self.db, row)
        await self.db.commit()
        return job_out(job, with_log=True)


def _runner_block(target: RunnerSshTarget | None) -> str | None:
    if target is None:
        return "not_ssh"
    if target.host_key_confirmed_at is None:
        return "host_key_unconfirmed"
    if target.client_key_ref is None:
        return "key_missing"
    return None


@dataclass(frozen=True)
class _Ssh:
    """这次登录用的服务器信息：登记成功后写进部署记录，SSH 通道以后用它登录。"""

    address: str
    port: int
    username: str
    host_key: str


class _Job:
    """一个部署 / 重新部署 / 重启 / 卸载任务的协程。"""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        runtime: ConnectorRuntime,
        facts: _Facts,
        token: str,
        password: SecretStr | None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.runtime = runtime
        self.facts = facts
        self.token = token
        # sudo 的密码：只在这个任务的内存里，结束时清掉
        self._password = password.get_secret_value() if password is not None else None
        self.rec = JobRecorder(
            sessionmaker,
            facts.job_id,
            secrets=[value for value in (token, self._password) if value],
            steps=STEPS[facts.kind],
            model=LlmBridgeDeployJob,
        )
        self._ssh: _Ssh | None = None
        self._access = bundle.ACCESS_DIRECT
        # 新部署失败时清掉服务器上留下的东西：目录建过就删（里面有令牌文件），容器起过就先停掉，
        # 转发密钥装过就删
        self._prepared = False
        self._started = False
        self._directory: str | None = facts.install_dir
        self._tunnel_public: str | None = facts.tunnel_public_key
        self._tunnel_ref: str | None = None
        self._tunnel_installed = False
        self._source_tag: str | None = None

    # ---- 入口 ----
    async def run(self) -> None:
        status, code, message = "succeeded", None, None
        try:
            await self._run()
        except JobGone:
            return
        except asyncio.CancelledError:
            await self._finish("interrupted", "interrupted", "工作台正在停止，任务被中断")
            raise
        except DeployFailed as error:
            status, code, message = "failed", error.code, error.message
        except bundle.BundleError as error:
            status, code, message = "failed", error.code, error.message
        except ServerCredentialError as error:
            status, code, message = "failed", error.reason, error.message
        except ssh.SshError as error:
            status, code, message = "failed", error.reason, error.message
        except Exception:
            logger.exception(
                "llm bridge deploy job crashed", extra={"job_id": str(self.facts.job_id)}
            )
            status, code, message = "failed", "internal_error", "工作台内部出错，详见工作台的日志"
        finally:
            self._password = None
        await self._finish(status, code, message)

    async def _finish(self, status: str, code: str | None, message: str | None) -> None:
        current = self.rec.current
        if status != "succeeded" and current is not None:
            for step in self.rec.steps:
                if step["key"] == current:
                    step["status"] = "failed"
                    step["detail"] = self.rec.redact(message or "")
        if message:
            self.rec.write(f"结果：{message}")
        try:
            async with self.sessionmaker() as db:
                result = await db.execute(
                    update(LlmBridgeDeployJob)
                    .where(
                        LlmBridgeDeployJob.id == self.facts.job_id,
                        LlmBridgeDeployJob.status == "running",
                    )
                    .values(
                        status=status,
                        error_code=code,
                        error_message=self.rec.redact(message) if message else None,
                        steps=self.rec.steps,
                        log=self.rec.log,
                        finished_at=_now(),
                    )
                )
                if getattr(result, "rowcount", 0):
                    await AuditTrail(db, self.settings).record(
                        f"llm_bridge.{self.facts.kind}_{status}",
                        workspace_ids=[self.facts.workspace_id],
                        actor_id=self.facts.actor_id,
                        resource_type="llm_bridge",
                        resource_id=self.facts.bridge_id or self.facts.job_id,
                        metadata={
                            "job": str(self.facts.job_id),
                            "name": self.facts.name,
                            "server": self.facts.target.name,
                            "error": code,
                        },
                    )
                await db.commit()
        except (OSError, SQLAlchemyError) as error:
            logger.warning(
                "llm bridge deploy job result not saved",
                extra={"job_id": str(self.facts.job_id), "error": type(error).__name__},
            )

    # ---- 命令 ----
    async def _cmd(
        self,
        conn: asyncssh.SSHClientConnection,
        label: str,
        command: str,
        *,
        stdin: str | None = None,
        seconds: float = ssh.COMMAND_TIMEOUT,
    ) -> ssh.CommandResult:
        result = await ssh.run(conn, command, stdin=stdin, seconds=seconds)
        self.rec.output(label, result)
        return result

    async def _docker(
        self,
        conn: asyncssh.SSHClientConnection,
        label: str,
        command: str,
        *,
        seconds: float = ssh.COMMAND_TIMEOUT,
    ) -> ssh.CommandResult:
        """要用 Docker 的命令：登录账号不能直接用时经 sudo（要密码时密码从标准输入给）。"""
        stdin = (
            f"{self._password}\n"
            if self._access == bundle.ACCESS_SUDO_PASSWORD and self._password is not None
            else None
        )
        return await self._cmd(
            conn, label, bundle.privileged(command, self._access), stdin=stdin, seconds=seconds
        )

    # ---- 步骤 ----
    async def _connect(self) -> asyncssh.SSHClientConnection:
        await self.rec.step("connect", "running")
        target = self.facts.target
        try:
            box = CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise DeployFailed("credentials_unavailable", "密钥库未配置加密密钥") from error
        if target.kind == AGENT_SERVER:
            async with self.sessionmaker() as db:
                agent = await db.get(AgentSshTarget, target.ssh_target_id)
                if agent is None or agent.server_id != target.id:
                    raise DeployFailed("server_gone", "这台服务器已经从工作台移除")
                db.expunge(agent)
            self._ssh = _Ssh(agent.address, agent.port, agent.username, agent.host_key)
            conn = await connect_agent_server(agent, box, expected_revision=target.revision or 0)
            how = "保存的密码"
        else:
            async with self.sessionmaker() as db:
                runner = await db.get(RunnerSshTarget, target.ssh_target_id)
                if runner is None or runner.host_id != target.id:
                    raise DeployFailed("server_gone", "这台服务器已经从工作台移除")
                if runner.client_key_ref is None:
                    raise DeployFailed("key_missing", _BLOCKS["key_missing"])
                self._ssh = _Ssh(runner.address, runner.port, runner.username, runner.host_key)
                key_ref = runner.client_key_ref
            try:
                secret = box.resolve(key_ref, context=runner_ssh_context(target.ssh_target_id))
                key = asyncssh.import_private_key(secret.get_secret_value())
            except (CredentialError, asyncssh.KeyImportError) as error:
                raise DeployFailed("key_unreadable", "这台服务器的专用密钥读不出来") from error
            conn = await ssh.connect(
                self._ssh.address,
                self._ssh.port,
                self._ssh.username,
                self._ssh.host_key,
                client_key=key,
            )
            how = "专用密钥"
        await self.rec.step(
            "connect", "done", f"已登录 {self._ssh.username}@{self._ssh.address}（{how}）"
        )
        return conn

    async def _skip_ports(self) -> list[int]:
        """这台服务器上已经分给别的管理器的端口。"""
        target = self.facts.target
        column = (
            LlmBridgeInstall.server_id
            if target.kind == AGENT_SERVER
            else LlmBridgeInstall.runner_host_id
        )
        async with self.sessionmaker() as db:
            rows = await db.execute(
                select(LlmBridgeInstall.port, LlmBridgeInstall.id).where(
                    column == target.id, LlmBridgeInstall.port.is_not(None)
                )
            )
            return sorted(
                {
                    port
                    for port, install_id in rows
                    if port is not None and install_id != self.facts.install_id
                }
            )

    async def _privilege(self, conn: asyncssh.SSHClientConnection, info: bundle.Inspect) -> None:
        """登录账号怎么用 Docker：直接用、免密 sudo、还是要密码的 sudo。"""
        if info.access:
            self._access = bundle.ACCESS_DIRECT
            return
        if not info.docker:
            where = (
                "先到“智能体服务器”页初始化这台服务器"
                if self.facts.target.kind == AGENT_SERVER
                else "先在“容器服务器”页用 SSH 部署一次"
            )
            raise DeployFailed("docker_unavailable", f"这台服务器上没有 Docker：{where}")
        if not info.sudo:
            raise DeployFailed("docker_unavailable", "登录账号不能使用 Docker，服务器上也没有 sudo")
        if info.sudo_nopasswd:
            self._access = bundle.ACCESS_SUDO
            return
        if self._password is None:
            raise DeployFailed(
                "sudo_password_required",
                "这台服务器的登录账号用 Docker 要 sudo 密码：填写登录密码后重试"
                "（只用这一次，不保存）",
            )
        self._access = bundle.ACCESS_SUDO_PASSWORD
        check = await self._docker(conn, "检查 sudo", "true", seconds=30)
        if not check.ok:
            raise DeployFailed("sudo_denied", "这个账号不能使用 sudo（或密码不对）")

    async def _probe(self, conn: asyncssh.SSHClientConnection) -> bundle.Inspect:
        """只读检查。登录账号不能直接用 Docker 时再经 sudo 运行一遍，读 Docker 里的情况。"""
        skip = await self._skip_ports()
        result = await self._cmd(
            conn,
            "检查服务器",
            bundle.inspect_command(workbench_hostname(), skip),
            stdin=bundle.inspect_script(),
            seconds=INSPECT_TIMEOUT,
        )
        if not result.ok:
            raise DeployFailed("inspect_failed", "读取服务器信息失败，详见日志")
        info = bundle.parse_inspect(result.stdout)
        await self._privilege(conn, info)
        if self._access != bundle.ACCESS_DIRECT:
            again = await self._docker(
                conn,
                "检查服务器（sudo）",
                bundle.inspect_inline(workbench_hostname(), skip),
                seconds=INSPECT_TIMEOUT,
            )
            if not again.ok:
                raise DeployFailed("inspect_failed", "读取服务器信息失败，详见日志")
            root = bundle.parse_inspect(again.stdout)
            # 主目录还是登录账号的；Docker 里的情况以 sudo 读到的为准
            info = replace(root, home=info.home, sudo=info.sudo, sudo_nopasswd=info.sudo_nopasswd)
        if not info.access or not info.compose:
            raise DeployFailed(
                "docker_unavailable", "这台服务器上的 Docker（或 docker compose）现在不能用"
            )
        return info

    async def _inspect(self, conn: asyncssh.SSHClientConnection) -> bundle.Network:
        await self.rec.step("inspect", "running")
        info = await self._probe(conn)
        if self.facts.mode == "tunnel":
            # 重新部署：网络方式和端口沿用上一次的，转发密钥只认这个端口
            info = replace(info, network="", port=self.facts.port)
        elif self.facts.force_tunnel:
            info = replace(info, network="")
        elif self.facts.mode == "same_host" and not info.network:
            raise DeployFailed(
                "network_missing",
                "在这台服务器上找不到工作台的内部网络了（工作台搬走了？）：卸载后重新部署",
            )
        network = bundle.plan_network(info, slot=self.facts.slot)
        self._directory = self.facts.install_dir or bundle.install_dir(info.home, self.facts.slot)
        await self.rec.flush(mode=network.mode, install_dir=self._directory, port=network.port)
        how = {
            bundle.ACCESS_DIRECT: "",
            bundle.ACCESS_SUDO: "；Docker 经免密 sudo 使用",
            bundle.ACCESS_SUDO_PASSWORD: "；Docker 经 sudo 使用（密码只用这一次）",
        }[self._access]
        detail = (
            f"和工作台在同一台服务器上：加入内部网络 {info.network}，端口不对外开放"
            if network.mode == "same_host"
            else f"只监听这台服务器的本机地址 127.0.0.1:{network.port}，工作台经 SSH 通道访问"
        )
        await self.rec.step("inspect", "done", detail + how)
        return network

    async def _upload(self, conn: asyncssh.SSHClientConnection, network: bundle.Network) -> None:
        await self.rec.step("upload", "running")
        root = bundle.source_dir()
        if root is None:
            raise DeployFailed("source_missing", "工作台里没有 MCP 管理器的构建文件")
        source = bundle.source_files(root)
        self._source_tag = bundle.content_tag(source)
        files = bundle.bundle(source, self.facts.slot, network, self.token)
        directory = self._directory
        assert directory is not None  # noqa: S101 - _inspect 已经定下目录
        prepared = await self._cmd(conn, "准备目录", bundle.prepare_command(directory))
        if not prepared.ok:
            raise DeployFailed("upload_failed", f"无法创建目录 {directory}")
        self._prepared = True
        await ssh.put_files(conn, directory, files)
        await self.rec.step("upload", "done", f"{len(files)} 个文件已上传到 {directory}")

    async def _build_and_start(self, conn: asyncssh.SSHClientConnection) -> None:
        directory = self._directory
        assert directory is not None  # noqa: S101
        await self.rec.step("build", "running")
        built = await self._docker(
            conn, "构建镜像", bundle.compose_command(directory, "build"), seconds=BUILD_TIMEOUT
        )
        if not built.ok:
            raise DeployFailed("build_failed", "构建镜像失败，详见日志")
        await self.rec.step("build", "done")

        await self.rec.step("start", "running")
        self._started = True
        started = await self._docker(
            conn,
            "启动容器",
            bundle.compose_command(
                directory, "up", "-d", "--wait", "--wait-timeout", str(START_WAIT)
            ),
            seconds=START_TIMEOUT,
        )
        if not started.ok:
            await self._docker(
                conn,
                "容器日志",
                bundle.compose_command(directory, "logs", "--no-color", "--tail", "60"),
            )
            raise DeployFailed("start_failed", "容器没有启动起来，详见日志")
        await self.rec.step("start", "done")

    async def _install_tunnel(
        self, conn: asyncssh.SSHClientConnection, network: bundle.Network
    ) -> None:
        """别的服务器：在登录账号的 authorized_keys 里装一把只能转发到管理器那个端口的密钥。"""
        await self.rec.step("tunnel", "running")
        if network.mode != "tunnel" or network.port is None:
            await self.rec.step("tunnel", "done", "和工作台在同一台服务器上：不需要通道")
            return
        private, public = tunnel.new_key(self.facts.slot)
        try:
            self._tunnel_ref = CredentialBox.from_settings(self.settings).seal(
                private, context=tunnel.key_context(self.facts.install_id)
            )
        except CredentialError as error:
            raise DeployFailed("credentials_unavailable", "密钥库未配置加密密钥") from error
        del private
        self._tunnel_public = public
        self._tunnel_installed = True
        added = await self._cmd(
            conn,
            "安装转发密钥",
            prov.authorize_key_command(bundle.tunnel_line(public, network.port)),
            seconds=30,
        )
        if not added.ok:
            raise DeployFailed("tunnel_failed", "无法把转发密钥写进 authorized_keys")
        await self.rec.step(
            "tunnel",
            "done",
            f"已安装只能转发到 127.0.0.1:{network.port} 的专用密钥（不能执行命令）",
        )

    def _spec(self, network: bundle.Network) -> tunnel.TunnelSpec | None:
        if network.mode != "tunnel" or network.port is None:
            return None
        assert self._ssh is not None  # noqa: S101 - _connect 已经定下
        return tunnel.TunnelSpec(
            install_id=self.facts.install_id,
            address=self._ssh.address,
            ssh_port=self._ssh.port,
            username=self._ssh.username,
            host_key=self._ssh.host_key,
            key_ref=self._tunnel_ref or "",
            port=network.port,
        )

    async def _client(self, network: bundle.Network) -> BridgeClient:
        """按将来实际使用的那条路访问管理器。刚启动时连不上是正常的：用自己的熔断计数。"""
        breaker = CircuitBreaker(threshold=10**6)
        spec = self._spec(network)
        if spec is None:
            return bridge_client(self.runtime, network.base_url, self.token, breaker=breaker)
        if not spec.key_ref:
            # 重新部署 / 重启：转发密钥在部署记录里
            async with self.sessionmaker() as db:
                install = await db.get(LlmBridgeInstall, self.facts.install_id)
                saved = tunnel.spec_of(install) if install is not None else None
            if saved is None:
                raise DeployFailed("tunnel_failed", "部署记录里没有转发密钥：卸载后重新部署")
            spec = replace(spec, key_ref=saved.key_ref)
        tunnel.POOL.drop(spec.install_id)
        if await tunnel.POOL.probe(self.settings, spec) == "prohibited":
            raise DeployFailed(
                "forwarding_disabled",
                "这台服务器的 SSH 不允许端口转发：在 sshd 配置里打开 AllowTcpForwarding"
                "（可以只对这个登录账号打开）后重新部署",
            )
        settings = self.settings

        async def resolve() -> str:
            return await tunnel.POOL.url(settings, spec)

        return bridge_client(
            self.runtime,
            network.base_url,
            self.token,
            breaker=breaker,
            resolve=resolve,
            scope=f"llm_bridge:install:{spec.install_id}",
        )

    async def _verify(self, network: bundle.Network, *, apply: bool) -> list[str]:
        """工作台连上管理器（apply：把控制的平台写进去）。刚启动时可能还连不上：等一会儿再试。"""
        await self.rec.step("verify", "running")
        try:
            client = await self._client(network)
        except ConnectorError as error:
            raise DeployFailed("verify_failed", str(error)) from error
        deadline = asyncio.get_running_loop().time() + VERIFY_TIMEOUT
        while True:
            try:
                if apply:
                    saved = await client.admin(
                        "PUT", "/settings", {"platforms": list(self.facts.platforms)}
                    )
                else:
                    saved = await client.admin("GET", "/status")
                break
            except BridgeCallError as error:
                raise DeployFailed("verify_failed", f"管理器拒绝了设置：{error.message}") from error
            except ConnectorError as error:
                if error.kind == "AUTH_FAILED":
                    raise DeployFailed(
                        "verify_failed",
                        "管理器不接受工作台生成的令牌：这台服务器上可能留着同名的旧容器",
                    ) from error
                if asyncio.get_running_loop().time() >= deadline:
                    raise DeployFailed(
                        "verify_failed", f"容器启动了，但工作台连不上它：{error}"
                    ) from error
                await asyncio.sleep(VERIFY_POLL)
        chosen = saved.get("platforms") if isinstance(saved, dict) else None
        platforms = [str(p) for p in chosen] if isinstance(chosen, list) else []
        how = "经 SSH 通道" if network.mode == "tunnel" else f"经内部网络 {network.base_url}"
        await self.rec.step("verify", "done", f"工作台已连上（{how}）")
        return platforms

    async def _register(self, network: bundle.Network, platforms: list[str]) -> None:
        await self.rec.step("register", "running")
        bridge_id = uuid.uuid4()
        target = self.facts.target
        assert self._ssh is not None and self._directory is not None  # noqa: S101
        async with self.sessionmaker() as db:
            taken = await db.scalar(
                select(LlmBridge.id).where(
                    LlmBridge.workspace_id == self.facts.workspace_id,
                    LlmBridge.name == self.facts.name,
                )
            )
            if taken is not None:
                raise DeployFailed(
                    "name_taken", "部署期间有人添加了同名的 MCP 管理器，换一个名字重新部署"
                )
            db.add(
                LlmBridge(
                    id=bridge_id,
                    workspace_id=self.facts.workspace_id,
                    name=self.facts.name,
                    base_url=network.base_url,
                    token_ref=self.runtime.credentials().seal(
                        self.token, context=secret_context(self.facts.workspace_id, bridge_id)
                    ),
                )
            )
            await db.flush()
            db.add(
                LlmBridgeInstall(
                    id=self.facts.install_id,
                    workspace_id=self.facts.workspace_id,
                    bridge_id=bridge_id,
                    target_kind=target.kind,
                    server_id=target.id if target.kind == AGENT_SERVER else None,
                    runner_host_id=target.id if target.kind == RUNNER_HOST else None,
                    server_name=target.name,
                    address=self._ssh.address,
                    ssh_port=self._ssh.port,
                    username=self._ssh.username,
                    host_key=self._ssh.host_key,
                    slot=self.facts.slot,
                    install_dir=self._directory,
                    mode=network.mode,
                    port=network.port,
                    tunnel_key_ref=self._tunnel_ref,
                    tunnel_public_key=self._tunnel_public if self._tunnel_ref else None,
                    platforms=list(self.facts.platforms),
                    source_tag=self._source_tag,
                    needs_sudo=self._access == bundle.ACCESS_SUDO_PASSWORD,
                    deployed_by=self.facts.actor_id,
                )
            )
            await db.execute(
                update(LlmBridgeDeployJob)
                .where(LlmBridgeDeployJob.id == self.facts.job_id)
                .values(bridge_id=bridge_id)
            )
            await AuditTrail(db, self.settings).record(
                "llm_bridge.bridge_created",
                workspace_ids=[self.facts.workspace_id],
                actor_id=self.facts.actor_id,
                resource_type="llm_bridge",
                resource_id=bridge_id,
                metadata={
                    "name": self.facts.name,
                    "platforms": platforms,
                    "deployed": True,
                    "server": target.name,
                    "target_kind": target.kind,
                    "mode": network.mode,
                },
            )
            await db.commit()
        await self.rec.step("register", "done", f"已登记「{self.facts.name}」")

    async def _cleanup(self, conn: asyncssh.SSHClientConnection) -> None:
        """新部署失败：不留下一个没登记的管理器、带着令牌的目录和用不上的转发密钥。"""
        tunnel.POOL.drop(self.facts.install_id)
        directory = self._directory
        try:
            if self._tunnel_installed and self._tunnel_public:
                await self._cmd(
                    conn, "删除转发密钥", prov.revoke_key_command(self._tunnel_public), seconds=30
                )
            if not self._prepared or directory is None:
                return
            if self._started:
                await self._docker(
                    conn,
                    "清理没部署成功的容器",
                    bundle.compose_command(directory, "down", "-v", "--remove-orphans"),
                    seconds=START_TIMEOUT,
                )
            await self._cmd(conn, "删除部署目录", bundle.remove_dir_command(directory))
        except ssh.SshError as error:
            self.rec.write(f"清理失败：{error.message}")

    async def _run(self) -> None:
        conn = await self._connect()
        try:
            kind = self.facts.kind
            if kind == "uninstall":
                await self._uninstall(conn)
            elif kind == "restart":
                await self._restart(conn)
            else:
                await self._deploy(conn)
        finally:
            conn.close()

    async def _deploy(self, conn: asyncssh.SSHClientConnection) -> None:
        fresh = self.facts.kind == "deploy"
        network = await self._inspect(conn)
        try:
            await self._upload(conn, network)
            await self._build_and_start(conn)
            if fresh:
                await self._install_tunnel(conn, network)
            platforms = await self._verify(network, apply=True)
            if fresh:
                await self._register(network, platforms)
            else:
                await self._redeployed()
        except (DeployFailed, bundle.BundleError, ssh.SshError):
            if fresh:
                await self._cleanup(conn)
            raise

    async def _redeployed(self) -> None:
        """重新部署成功：记下新的构建文件版本，以及这次登录用的服务器信息（通道以后用它）。"""
        assert self._ssh is not None  # noqa: S101
        async with self.sessionmaker() as db:
            install = await db.get(LlmBridgeInstall, self.facts.install_id)
            if install is None:
                return
            install.source_tag = self._source_tag
            install.needs_sudo = self._access == bundle.ACCESS_SUDO_PASSWORD
            install.address = self._ssh.address
            install.ssh_port = self._ssh.port
            install.username = self._ssh.username
            install.host_key = self._ssh.host_key
            install.server_name = self.facts.target.name
            await db.commit()

    def _installed_network(self) -> bundle.Network:
        """已经部署的管理器的访问方式（重启后确认连得上用）。"""
        if self.facts.mode == "tunnel" and self.facts.port is not None:
            port = self.facts.port
            return bundle.Network("tunnel", f"http://127.0.0.1:{port}", "", "", port)
        return bundle.Network(
            "same_host", f"http://{bundle.alias(self.facts.slot)}:{bundle.BRIDGE_PORT}", "", ""
        )

    async def _access_only(self, conn: asyncssh.SSHClientConnection) -> None:
        """重启 / 卸载：只需要知道登录账号怎么用 Docker。"""
        result = await self._cmd(
            conn,
            "检查服务器",
            bundle.inspect_command(None, []),
            stdin=bundle.inspect_script(),
            seconds=INSPECT_TIMEOUT,
        )
        if not result.ok:
            raise DeployFailed("inspect_failed", "读取服务器信息失败，详见日志")
        await self._privilege(conn, bundle.parse_inspect(result.stdout))

    async def _restart(self, conn: asyncssh.SSHClientConnection) -> None:
        directory = self._directory
        assert directory is not None  # noqa: S101 - 开始前已经确认有部署目录
        await self.rec.step("restart", "running")
        await self._access_only(conn)
        restarted = await self._docker(
            conn, "重启容器", bundle.compose_command(directory, "restart"), seconds=START_TIMEOUT
        )
        if not restarted.ok:
            raise DeployFailed("restart_failed", "重启容器失败，详见日志")
        await self.rec.step("restart", "done")
        await self._verify(self._installed_network(), apply=False)

    async def _uninstall(self, conn: asyncssh.SSHClientConnection) -> None:
        directory = self._directory
        assert directory is not None  # noqa: S101 - 开始卸载前已经确认有部署目录
        await self.rec.step("stop", "running")
        await self._access_only(conn)
        args = ["down", "--remove-orphans"] + (["-v"] if self.facts.remove_data else [])
        stopped = await self._docker(
            conn, "停止并删除容器", bundle.compose_command(directory, *args), seconds=START_TIMEOUT
        )
        if not stopped.ok:
            raise DeployFailed("stop_failed", "停止容器失败，详见日志")
        await self.rec.step(
            "stop",
            "done",
            "容器已删除，数据卷也已删除" if self.facts.remove_data else "容器已删除，数据卷保留",
        )
        await self.rec.step("remove", "running")
        removed = await self._cmd(conn, "删除部署目录", bundle.remove_dir_command(directory))
        if not removed.ok:
            raise DeployFailed("remove_failed", f"无法删除目录 {directory}")
        detail = "部署目录已删除"
        if self._tunnel_public:
            revoked = await self._cmd(
                conn, "删除转发密钥", prov.revoke_key_command(self._tunnel_public), seconds=30
            )
            if not revoked.ok:
                raise DeployFailed("remove_failed", "无法从 authorized_keys 删除转发密钥")
            detail += "，转发密钥已从服务器上删除"
        await self.rec.step("remove", "done", detail)
        await self.rec.step("unregister", "running")
        tunnel.POOL.drop(self.facts.install_id)
        async with self.sessionmaker() as db:
            row = await db.get(LlmBridge, self.facts.bridge_id)
            if row is not None:
                await remove_bridge(db, row)
                await AuditTrail(db, self.settings).record(
                    "llm_bridge.bridge_deleted",
                    workspace_ids=[self.facts.workspace_id],
                    actor_id=self.facts.actor_id,
                    resource_type="llm_bridge",
                    resource_id=row.id,
                    metadata={"name": self.facts.name, "uninstalled": True},
                )
                await db.commit()
        await self.rec.step("unregister", "done", f"已从工作台去掉「{self.facts.name}」")
