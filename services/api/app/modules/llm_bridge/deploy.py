"""页面上一键部署 MCP 管理器（docs/v2/LLM_BRIDGE_PLAN.md 第 20 节；人类 2026-11-14 要求）。

部署到“智能体服务器”页登记并初始化过的服务器上，走那里已有的 SSH 通道（保存的密码、确认过的
主机公钥；`domain/agent_server_ssh.py`）。工作台自己不挂 Docker socket；在服务器上只执行
写死的几条命令（`deploy_bundle.py`），不拼接用户输入的文字。

- 部署：检查服务器 → 上传构建文件 → 在服务器上构建镜像 → 启动 → 工作台连上它、
  写入控制的平台 → 登记。
  管理令牌由工作台生成，只在上传的 bridge.env（权限 600）和加密后的登记里，不进任务记录和日志。
- 网络：和工作台同一台机器时加入工作台的内部网络；不同机器时端口只开在登记这台服务器用的
  私网地址上，公网地址不部署。
- 重新部署（升级）：同一个目录、同一枚令牌、同一个数据卷，重新上传、构建、启动。
- 卸载：停掉并删除容器、删除部署目录、去掉登记；数据卷默认保留。

任务在 API 进程里运行（借用 `SshJobRunner.launch`）。进程重启后协程不在了：
读到这样的记录时标为中断。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncssh
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from app.integrations import ssh
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker
from app.models import AgentServer, AgentSshTarget
from app.modules.llm_bridge import deploy_bundle as bundle
from app.modules.llm_bridge.client import BridgeCallError, bridge_client
from app.modules.llm_bridge.deploy_schemas import (
    LlmBridgeDeployIn,
    LlmBridgeDeployJobList,
    LlmBridgeDeployJobOut,
    LlmBridgeRedeployIn,
    LlmBridgeUninstallIn,
)
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeDeployJob
from app.modules.llm_bridge.service import LlmBridgeService, remove_bridge, secret_context

logger = logging.getLogger(__name__)

STEPS: dict[str, tuple[str, ...]] = {
    "deploy": ("connect", "inspect", "upload", "build", "start", "verify", "register"),
    "redeploy": ("connect", "inspect", "upload", "build", "start", "verify"),
    "uninstall": ("connect", "stop", "remove", "unregister"),
}
INSPECT_TIMEOUT = 60.0
BUILD_TIMEOUT = 1800.0
START_TIMEOUT = 300.0
START_WAIT = 240
VERIFY_TIMEOUT = 90.0
VERIFY_POLL = 3.0
LIST_LIMIT = 30


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
        server_id=row.server_id,
        server_name=row.server_name,
        bridge_id=row.bridge_id,
        name=row.name,
        platforms=list(row.platforms or []),
        mode=row.mode,
        base_url=row.base_url,
        steps=list(row.steps or []),
        log=row.log if with_log else None,
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@dataclass(frozen=True)
class _Facts:
    """任务开始时定下来的东西：协程里不再依赖请求的数据库会话。"""

    job_id: uuid.UUID
    kind: str
    workspace_id: uuid.UUID
    actor_id: uuid.UUID | None
    server_id: uuid.UUID
    target_id: uuid.UUID
    revision: int
    slot: str
    name: str
    platforms: tuple[str, ...]
    bridge_id: uuid.UUID | None = None
    # 重新部署 / 卸载：上一次安装时定下的
    install_dir: str | None = None
    port: int | None = None
    remove_data: bool = False


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

    async def _install(self, bridge_id: uuid.UUID) -> LlmBridgeDeployJob | None:
        """这座桥是不是工作台部署的：最近一次成功的部署 / 重新部署。"""
        return await self.db.scalar(
            select(LlmBridgeDeployJob)
            .where(
                LlmBridgeDeployJob.bridge_id == bridge_id,
                LlmBridgeDeployJob.kind.in_(("deploy", "redeploy")),
                LlmBridgeDeployJob.status == "succeeded",
            )
            .order_by(LlmBridgeDeployJob.started_at.desc())
            .limit(1)
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

    async def _server_target(
        self, access: WorkspaceAccess, server_id: uuid.UUID
    ) -> tuple[AgentServer, AgentSshTarget]:
        """锁住服务器，确认它现在可以部署（确认过主机公钥、保存了密码、初始化过、没有别的任务在跑）。"""
        server = await self.servers._server(access, server_id, lock=True)
        target = await self.servers.deployable_target(server)
        busy = list(
            (
                await self.db.scalars(
                    select(LlmBridgeDeployJob).where(
                        LlmBridgeDeployJob.server_id == server.id,
                        LlmBridgeDeployJob.status == "running",
                    )
                )
            ).all()
        )
        await self._settle(busy)
        if any(row.status == "running" for row in busy):
            raise ApiError(409, "DEPLOY_BUSY", "这台服务器上有一个 MCP 管理器的任务还在进行")
        return server, target

    async def _launch(
        self, access: WorkspaceAccess, job: LlmBridgeDeployJob, facts: _Facts, token: str
    ) -> LlmBridgeDeployJobOut:
        runner, sessionmaker = self._ready()
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
            {"job": str(job.id), "name": job.name, "server": job.server_name},
        )
        runner.reserve(job.id)
        try:
            await self.db.commit()
        except Exception:
            runner.release(job.id)
            raise
        out = job_out(job)
        runner.launch(job.id, _Job(sessionmaker, self.settings, self.runtime, facts, token).run())
        return out

    def _new_job(
        self,
        access: WorkspaceAccess,
        kind: str,
        *,
        server: AgentServer,
        name: str,
        platforms: list[str],
        slot: str,
        request_id: str,
        bridge_id: uuid.UUID | None = None,
        install: LlmBridgeDeployJob | None = None,
    ) -> LlmBridgeDeployJob:
        return LlmBridgeDeployJob(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            kind=kind,
            status="running",
            server_id=server.id,
            server_name=server.name,
            bridge_id=bridge_id,
            name=name,
            platforms=platforms,
            slot=slot,
            mode=install.mode if install else None,
            base_url=install.base_url if install else None,
            install_dir=install.install_dir if install else None,
            port=install.port if install else None,
            request_id=request_id,
            requested_by=access.user_id,
            steps=[{"key": key, "status": "pending", "detail": None} for key in STEPS[kind]],
            log="",
        )

    async def deploy(
        self, access: WorkspaceAccess, body: LlmBridgeDeployIn
    ) -> LlmBridgeDeployJobOut:
        existing = await self._existing(access, body.request_id)
        if existing is not None:
            return job_out(existing)
        self._ready()
        name = body.name.strip()
        await self.bridges._name_free(access.workspace.id, name, skip=None)
        server, target = await self._server_target(access, body.server_id)
        platforms: list[str] = list(dict.fromkeys(body.platforms))
        job = self._new_job(
            access,
            "deploy",
            server=server,
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
            server_id=server.id,
            target_id=target.id,
            revision=target.credential_revision,
            slot=job.slot,
            name=name,
            platforms=tuple(platforms),
        )
        return await self._launch(access, job, facts, new_token())

    async def _installed(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID
    ) -> tuple[LlmBridge, LlmBridgeDeployJob]:
        row = await self.bridges.bridge(access.workspace.id, bridge_id)
        install = await self._install(row.id)
        if install is None or install.install_dir is None:
            raise ApiError(
                409, "NOT_DEPLOYED_HERE", "这个 MCP 管理器不是工作台部署的：只能删除登记"
            )
        if install.server_id is None:
            raise ApiError(
                409,
                "NOT_DEPLOYED_HERE",
                "部署它的那台服务器已经从工作台移除：只能删除登记，容器要到服务器上手动处理",
            )
        return row, install

    async def redeploy(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgeRedeployIn
    ) -> LlmBridgeDeployJobOut:
        existing = await self._existing(access, body.request_id)
        if existing is not None:
            return job_out(existing)
        self._ready()
        row, install = await self._installed(access, bridge_id)
        assert install.server_id is not None  # noqa: S101 - _installed 已经检查过
        token = self.bridges.runtime.credentials().resolve(
            row.token_ref, context=secret_context(row.workspace_id, row.id)
        )
        server, target = await self._server_target(access, install.server_id)
        job = self._new_job(
            access,
            "redeploy",
            server=server,
            name=row.name,
            platforms=list(install.platforms or []),
            slot=install.slot,
            request_id=body.request_id,
            bridge_id=row.id,
            install=install,
        )
        facts = _Facts(
            job_id=job.id,
            kind="redeploy",
            workspace_id=access.workspace.id,
            actor_id=access.user_id,
            server_id=server.id,
            target_id=target.id,
            revision=target.credential_revision,
            slot=install.slot,
            name=row.name,
            platforms=tuple(install.platforms or []),
            bridge_id=row.id,
            install_dir=install.install_dir,
            port=install.port,
        )
        return await self._launch(access, job, facts, token.get_secret_value())

    async def uninstall(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgeUninstallIn
    ) -> LlmBridgeDeployJobOut:
        existing = await self._existing(access, body.request_id)
        if existing is not None:
            return job_out(existing)
        self._ready()
        row, install = await self._installed(access, bridge_id)
        assert install.server_id is not None  # noqa: S101 - _installed 已经检查过
        server, target = await self._server_target(access, install.server_id)
        job = self._new_job(
            access,
            "uninstall",
            server=server,
            name=row.name,
            platforms=list(install.platforms or []),
            slot=install.slot,
            request_id=body.request_id,
            bridge_id=row.id,
            install=install,
        )
        facts = _Facts(
            job_id=job.id,
            kind="uninstall",
            workspace_id=access.workspace.id,
            actor_id=access.user_id,
            server_id=server.id,
            target_id=target.id,
            revision=target.credential_revision,
            slot=install.slot,
            name=row.name,
            platforms=tuple(install.platforms or []),
            bridge_id=row.id,
            install_dir=install.install_dir,
            port=install.port,
            remove_data=body.remove_data,
        )
        return await self._launch(access, job, facts, "")


class _Job:
    """一个部署 / 重新部署 / 卸载任务的协程。"""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        runtime: ConnectorRuntime,
        facts: _Facts,
        token: str,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.runtime = runtime
        self.facts = facts
        self.token = token
        self.rec = JobRecorder(
            sessionmaker,
            facts.job_id,
            secrets=[token],
            steps=STEPS[facts.kind],
            model=LlmBridgeDeployJob,
        )
        # 新部署失败时清掉服务器上留下的东西：目录建过就删（里面有令牌文件），容器起过就先停掉
        self._prepared = False
        self._started = False
        self._directory: str | None = facts.install_dir

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
                            "error": code,
                        },
                    )
                await db.commit()
        except (OSError, SQLAlchemyError) as error:
            logger.warning(
                "llm bridge deploy job result not saved",
                extra={"job_id": str(self.facts.job_id), "error": type(error).__name__},
            )

    # ---- 步骤 ----
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

    async def _connect(self) -> asyncssh.SSHClientConnection:
        await self.rec.step("connect", "running")
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.facts.target_id)
            if target is None or target.server_id != self.facts.server_id:
                raise DeployFailed("server_gone", "这台服务器已经从工作台移除")
            db.expunge(target)
        try:
            box = CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise DeployFailed("credentials_unavailable", "密钥库未配置加密密钥") from error
        conn = await connect_agent_server(target, box, expected_revision=self.facts.revision)
        self._address = target.address
        await self.rec.step("connect", "done", f"已登录 {target.username}@{target.host}")
        return conn

    async def _skip_ports(self) -> list[int]:
        """这台服务器上已经分给别的管理器的端口。"""
        async with self.sessionmaker() as db:
            rows = await db.execute(
                select(LlmBridgeDeployJob.port, LlmBridgeDeployJob.slot).where(
                    LlmBridgeDeployJob.server_id == self.facts.server_id,
                    LlmBridgeDeployJob.port.is_not(None),
                    LlmBridgeDeployJob.bridge_id.is_not(None),
                    LlmBridgeDeployJob.status == "succeeded",
                )
            )
            return sorted(
                {port for port, slot in rows if port is not None and slot != self.facts.slot}
            )

    async def _inspect(self, conn: asyncssh.SSHClientConnection) -> bundle.Network:
        await self.rec.step("inspect", "running")
        result = await self._cmd(
            conn,
            "检查服务器",
            bundle.inspect_command(workbench_hostname(), await self._skip_ports()),
            stdin=bundle.inspect_script(),
            seconds=INSPECT_TIMEOUT,
        )
        if not result.ok:
            raise DeployFailed("inspect_failed", "读取服务器信息失败，详见日志")
        info = bundle.parse_inspect(result.stdout)
        if not info.docker_ready:
            raise DeployFailed(
                "docker_unavailable",
                "这台服务器上的 Docker 现在不能用：先到“智能体服务器”页初始化这台服务器",
            )
        if self.facts.port is not None:
            # 重新部署：端口沿用上一次的，登记的地址不变
            info = bundle.Inspect(info.home, info.docker_ready, info.network, self.facts.port)
        network = bundle.plan_network(
            info,
            address=self._address,
            slot=self.facts.slot,
            allow_loopback=self.settings.agent_ssh_allow_loopback,
        )
        self._directory = self.facts.install_dir or bundle.install_dir(info.home, self.facts.slot)
        await self.rec.flush(
            mode=network.mode,
            base_url=network.base_url,
            install_dir=self._directory,
            port=network.port,
        )
        detail = (
            f"和工作台在同一台机器上：加入内部网络 {info.network}，端口不对外开放"
            if network.mode == "same_host"
            else f"和工作台不在同一台机器上：端口开在私网地址 {self._address}:{network.port}"
        )
        await self.rec.step("inspect", "done", detail)
        return network

    async def _upload(self, conn: asyncssh.SSHClientConnection, network: bundle.Network) -> None:
        await self.rec.step("upload", "running")
        root = bundle.source_dir()
        if root is None:
            raise DeployFailed("source_missing", "工作台里没有 MCP 管理器的构建文件")
        files = bundle.bundle(bundle.source_files(root), self.facts.slot, network, self.token)
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
        built = await self._cmd(
            conn, "构建镜像", bundle.compose_command(directory, "build"), seconds=BUILD_TIMEOUT
        )
        if not built.ok:
            raise DeployFailed("build_failed", "构建镜像失败，详见日志")
        await self.rec.step("build", "done")

        await self.rec.step("start", "running")
        self._started = True
        started = await self._cmd(
            conn,
            "启动容器",
            bundle.compose_command(
                directory, "up", "-d", "--wait", "--wait-timeout", str(START_WAIT)
            ),
            seconds=START_TIMEOUT,
        )
        if not started.ok:
            await self._cmd(
                conn,
                "容器日志",
                bundle.compose_command(directory, "logs", "--no-color", "--tail", "60"),
            )
            raise DeployFailed("start_failed", "容器没有启动起来，详见日志")
        await self.rec.step("start", "done")

    async def _verify(self, network: bundle.Network) -> list[str]:
        """工作台连上管理器，把控制的平台写进去。刚启动时可能还连不上：等一会儿再试。"""
        await self.rec.step("verify", "running")
        # 刚启动时连不上是正常的：用自己的熔断计数，不让连续几次失败把后面的重试挡掉
        client = bridge_client(
            self.runtime, network.base_url, self.token, breaker=CircuitBreaker(threshold=10**6)
        )
        deadline = asyncio.get_running_loop().time() + VERIFY_TIMEOUT
        while True:
            try:
                saved = await client.admin(
                    "PUT", "/settings", {"platforms": list(self.facts.platforms)}
                )
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
                        "verify_failed",
                        f"容器启动了，但工作台连不上它（{network.base_url}）："
                        "检查两台机器之间的防火墙是否放行这个端口",
                    ) from error
                await asyncio.sleep(VERIFY_POLL)
        chosen = saved.get("platforms") if isinstance(saved, dict) else None
        platforms = [str(p) for p in chosen] if isinstance(chosen, list) else []
        await self.rec.step("verify", "done", f"工作台已连上 {network.base_url}")
        return platforms

    async def _register(self, network: bundle.Network, platforms: list[str]) -> None:
        await self.rec.step("register", "running")
        bridge_id = uuid.uuid4()
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
                    "base_url": network.base_url,
                    "platforms": platforms,
                    "deployed": True,
                },
            )
            await db.commit()
        await self.rec.step("register", "done", f"已登记「{self.facts.name}」")

    async def _cleanup(self, conn: asyncssh.SSHClientConnection) -> None:
        """新部署失败：不留下一个没登记的管理器，也不留下带着令牌的目录。"""
        directory = self._directory
        if not self._prepared or directory is None:
            return
        try:
            if self._started:
                await self._cmd(
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
            if self.facts.kind == "uninstall":
                await self._uninstall(conn)
                return
            network = await self._inspect(conn)
            try:
                await self._upload(conn, network)
                await self._build_and_start(conn)
                platforms = await self._verify(network)
                if self.facts.kind == "deploy":
                    await self._register(network, platforms)
                else:
                    await self._moved(network)
            except (DeployFailed, bundle.BundleError, ssh.SshError):
                if self.facts.kind == "deploy":
                    await self._cleanup(conn)
                raise
        finally:
            conn.close()

    async def _moved(self, network: bundle.Network) -> None:
        """重新部署后地址变了（例如服务器换了登记的地址）：登记跟着改。"""
        async with self.sessionmaker() as db:
            row = await db.get(LlmBridge, self.facts.bridge_id)
            if row is not None and row.base_url != network.base_url:
                row.base_url = network.base_url
                await db.commit()

    async def _uninstall(self, conn: asyncssh.SSHClientConnection) -> None:
        directory = self._directory
        assert directory is not None  # noqa: S101 - 开始卸载前已经确认有部署目录
        await self.rec.step("stop", "running")
        args = ["down", "--remove-orphans"] + (["-v"] if self.facts.remove_data else [])
        stopped = await self._cmd(
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
        await self.rec.step("remove", "done")
        await self.rec.step("unregister", "running")
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
