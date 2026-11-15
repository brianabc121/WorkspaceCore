"""运行服务器的 SSH 自动部署（T-0136，docs/v2/RUNNER_PLAN.md 第 9 节）。

与 OpenClaw 实例的 SSH 自动部署（T-0089，`agent_ssh` / `agent_ssh_install`）同一套做法：
先读取主机公钥、owner 在服务器上核对指纹并确认；登录密码只用这一次，换成工作台的专用密钥
（ed25519，私钥加密保存，用途绑定 runner_ssh:<目标 ID>）；
任务在 API 进程内运行，步骤与脱敏后的日志写回任务记录。

运行服务器的部署：上传接入包到 ~/myworkspace-runner，以 root 运行其中的 install.sh
（装锁定版本的 Docker 与 gVisor、启动 runner-agent），再等 runner-agent 用新凭证连上工作台。
install.sh 要 root：root 账号直接运行；其他账号用 sudo，所以每次部署都要填登录密码（只用这一次）。
启用 gVisor 时脚本先让 Docker 重新加载配置；非重启 Docker 不可、而服务器上有运行中的 MCP 管理器时，
脚本以固定的退出码停下，任务的错误码是 gvisor_needs_docker_restart：owner 在页面上确认后
带 allow_docker_restart 重新部署（T-0234，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 8 节）。
卸载：停止 runner-agent（同样要 root），可选删除接入包目录；环境的容器与数据卷由工作台先删掉。
"""

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncssh
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agent_ssh import (
    PROBES_PER_MINUTE,
    _client_fingerprint,
    _job_out,
    _job_running,
    _key_missing,
    _out,
    _unreachable,
    _version_conflict,
)
from app.domain.agent_ssh_install import (
    DOCKER_TIMEOUT,
    HostInfo,
    Installer,
    InstallFailed,
    JobGone,
    JobRecorder,
    OneTimePassword,
    SshJobRunner,
    Target,
    _finish,
    parse_inspect,
    unzip_bundle,
)
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.domain.runner_admin import RunnerAdmin
from app.integrations import ssh
from app.models import RunnerEnvironment, RunnerHost, RunnerSshJob, RunnerSshTarget
from app.schemas.agents import (
    AgentSshConfirm,
    AgentSshRevoke,
    AgentSshState,
    AgentSshTargetSet,
)
from app.schemas.runner import RunnerSshInstall, RunnerSshUninstall

INSTALL_DIR_NAME = "myworkspace-runner"
STEPS = ("connect", "inspect", "authorize_key", "upload", "install", "online")
UNINSTALL_STEPS = ("connect", "stop", "remove_files")
INSTALL_TIMEOUT = DOCKER_TIMEOUT + 900.0  # Docker 与 gVisor 的下载
ONLINE_TIMEOUT = 5 * 60.0
ONLINE_POLL = 3.0
AUDIT = ("runner.ssh", "runner_host")
# install-gvisor.sh：启用 gVisor 要重启 Docker，而服务器上有运行中的 MCP 管理器、又没有得到允许时，
# 向 stderr 输出 gvisor_needs_docker_restart <数量> 并以这个退出码结束（install.sh 原样传出来）
GVISOR_RESTART_EXIT = 6
GVISOR_RESTART_CODE = "gvisor_needs_docker_restart"
_GVISOR_RESTART = re.compile(rf"^{GVISOR_RESTART_CODE} (\d{{1,6}})\s*$", re.M)
# 允许安装脚本重启 Docker（owner 在页面上确认过）
RESTART_ENV = "MW_ALLOW_DOCKER_RESTART"

logger = logging.getLogger("app.runner")


def runner_ssh_context(target_id: uuid.UUID) -> str:
    """专用私钥的用途：绑定这一个目标（换成别的记录解不开）。"""
    return f"runner_ssh:{target_id}"


def _now() -> datetime:
    return datetime.now(UTC)


def _sudo(command: str, *, root: bool) -> str:
    return command if root else f"sudo -S -p '' -k {command}"


def install_command(directory: str, *, root: bool, allow_docker_restart: bool = False) -> str:
    """以 root 运行接入包里的 install.sh。

    允许重启 Docker 时用 env 把变量交给脚本：sudo 默认不保留调用者的环境变量，
    写成 `sudo … env 变量=1 bash …` 才真的传得进去（root 账号也用同一种写法）。
    """
    command = f"bash {prov.q(directory + '/install.sh')}"
    if allow_docker_restart:
        command = f"env {RESTART_ENV}=1 {command}"
    return _sudo(command, root=root)


def gvisor_restart_needed(result: ssh.CommandResult) -> int | None:
    """安装脚本是不是因为“要重启 Docker、但有 MCP 管理器在运行”停下的；是就返回管理器的数量。"""
    if result.exit_status != GVISOR_RESTART_EXIT:
        return None
    found = _GVISOR_RESTART.search(result.stderr)
    return int(found.group(1)) if found else None


# ---------------------------------------------------------------- 接口


class RunnerSshService:
    """owner 在运行服务器页面的“设置”里填写服务器、核对指纹、部署、卸载、吊销专用密钥。"""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None = None,
        jobs: SshJobRunner | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.jobs = jobs
        self.audit = AuditTrail(db, settings)

    def for_host(self, host_id: uuid.UUID | None) -> "RunnerSshService":
        self.host_id = host_id
        return self

    async def _host(self, access: WorkspaceAccess) -> RunnerHost:
        from app.domain.runner import Runner, RunnerError

        try:
            host = await Runner(self.db, self.settings, self.redis).host(
                access.workspace.id, getattr(self, "host_id", None)
            )
        except RunnerError as error:
            raise ApiError(409, error.code, error.message) from None
        if host is None:
            raise ApiError(404, "NOT_FOUND", "还没有添加运行服务器")
        return host

    async def _target(self, host: RunnerHost) -> RunnerSshTarget | None:
        target: RunnerSshTarget | None = await self.db.scalar(
            select(RunnerSshTarget).where(RunnerSshTarget.host_id == host.id)
        )
        return target

    async def _existing(self, access: WorkspaceAccess) -> RunnerSshTarget:
        """先锁服务器再锁目标，与移除和后台身份登记保持同一锁顺序。"""
        host = await self._host(access)
        locked_host = await self.db.scalar(
            select(RunnerHost)
            .where(RunnerHost.id == host.id, RunnerHost.workspace_id == access.workspace.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_host is None:
            raise ApiError(404, "NOT_FOUND", "运行服务器已被移除")
        target: RunnerSshTarget | None = await self.db.scalar(
            select(RunnerSshTarget)
            .where(RunnerSshTarget.host_id == host.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is None:
            raise ApiError(404, "NOT_FOUND", "还没有填写运行服务器的 SSH 地址")
        return target

    async def _running(self, target: RunnerSshTarget) -> bool:
        """有没有进行中的任务；记录是 running、本进程里却没有它的协程，就标为中断。"""
        job: RunnerSshJob | None = await self.db.scalar(
            select(RunnerSshJob).where(
                RunnerSshJob.target_id == target.id, RunnerSshJob.status == "running"
            )
        )
        if job is None:
            return False
        if self.jobs is None or self.jobs.running(job.id):
            return True
        job.status = "interrupted"
        job.error_code = "interrupted"
        job.error_message = "任务已不在运行；可以重新开始"
        job.finished_at = _now()
        await self.db.flush()
        return False

    async def _state(self, target: RunnerSshTarget | None) -> AgentSshState:
        if target is None:
            return AgentSshState(target=None, job=None)
        job: RunnerSshJob | None = await self.db.scalar(
            select(RunnerSshJob)
            .where(RunnerSshJob.target_id == target.id)
            .order_by(RunnerSshJob.started_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return AgentSshState(target=_out(target), job=_job_out(job) if job else None)

    async def state(self, access: WorkspaceAccess) -> AgentSshState:
        # 还没有添加运行服务器时也是“没有目标服务器”（读接口不返回 404，写接口仍然要先添加）
        try:
            host = await self._host(access)
        except ApiError as error:
            if error.status_code != 404:
                raise
            host = None
        return await self._state(await self._target(host) if host else None)

    async def _record(
        self,
        action: str,
        access: WorkspaceAccess,
        host_id: uuid.UUID,
        metadata: dict[str, Any],
        client_ip: str,
    ) -> None:
        await self.audit.record(
            f"runner.ssh.{action}",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="runner_host",
            resource_id=host_id,
            metadata=metadata,
            client_ip=client_ip,
        )

    async def set_target(
        self, access: WorkspaceAccess, body: AgentSshTargetSet, *, client_ip: str
    ) -> AgentSshState:
        """填写或更换服务器：解析地址、读取主机公钥（不登录）。确认是另一步。"""
        host = await self._host(access)
        target = await self._target(host)
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
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 60).hit(
                f"runner-ssh-probe:{access.user_id}", PROBES_PER_MINUTE
            )
            if retry is not None:
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "探测太频繁，请稍后再试",
                    headers={"Retry-After": str(retry)},
                )
        try:
            address = await ssh.resolve(
                body.host, body.port, allow_loopback=self.settings.agent_ssh_allow_loopback
            )
            key = await ssh.probe_host_key(address, body.port)
        except ssh.SshError as error:
            raise _unreachable(error) from error
        if target is not None:
            # 探测期间可能有别的请求改了这条记录：锁行后再查一次
            target = await self._existing(access)
            if target.version != body.version or target.client_key_ref is not None:
                raise _version_conflict(target.version)
            if await self._running(target):
                raise _job_running()
        else:
            target = RunnerSshTarget(
                workspace_id=host.workspace_id,
                host_id=host.id,
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
        await self._record(
            "target_set",
            access,
            host.id,
            {
                "host": body.host,
                "address": address,
                "port": body.port,
                "username": body.username,
                "host_key_fingerprint": key.fingerprint,
            },
            client_ip,
        )
        try:
            await self.db.commit()
        except IntegrityError as error:
            await self.db.rollback()
            raise _version_conflict(1) from error
        await self.db.refresh(target)
        return await self._state(target)

    async def confirm(
        self, access: WorkspaceAccess, body: AgentSshConfirm, *, client_ip: str
    ) -> AgentSshState:
        target = await self._existing(access)
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
        target.host_key_confirmed_at = _now()
        target.host_key_confirmed_by = access.user_id
        target.version += 1
        await self._record(
            "host_key_confirmed",
            access,
            target.host_id,
            {"host_key_fingerprint": target.host_key_fingerprint},
            client_ip,
        )
        await self.db.commit()
        await self.db.refresh(target)
        return await self._state(target)

    async def remove(self, access: WorkspaceAccess, *, version: int, client_ip: str) -> None:
        target = await self._existing(access)
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
        await self._record(
            "target_removed",
            access,
            target.host_id,
            {"host": target.host, "port": target.port},
            client_ip,
        )
        await self.db.delete(target)
        await self.db.commit()

    def _ready(self, target: RunnerSshTarget) -> None:
        if self.jobs is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "部署任务暂时不可用，请稍后重试")
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if self.settings.credential_encryption_key is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")

    async def install(
        self, access: WorkspaceAccess, body: RunnerSshInstall, *, client_ip: str
    ) -> AgentSshState:
        """开始部署。密码只交给后台协程，不写进任务记录、审计或日志。"""
        target = await self._existing(access)
        if body.version != target.version:
            raise _version_conflict(target.version)
        self._ready(target)
        host = await self._host(access)
        url = (
            self.settings.agent_public_url
            if host.ingress == "public"
            else self.settings.agent_intranet_url
        )
        if not url:
            raise ApiError(409, "AGENT_INGRESS_NOT_CONFIGURED", "接入入口还没有配置")
        if body.password is None and target.client_key_ref is None:
            raise ApiError(422, "VALIDATION_FAILED", "第一次部署需要填写登录密码")
        if await self._running(target):
            raise _job_running()
        return await self._start(
            access,
            target,
            kind="install",
            password=body.password,
            allow_docker_restart=body.allow_docker_restart,
            metadata={
                "host": target.host,
                "port": target.port,
                "username": target.username,
                "host_key_fingerprint": target.host_key_fingerprint,
                "with_password": body.password is not None,
                "allow_docker_restart": body.allow_docker_restart,
            },
            client_ip=client_ip,
        )

    async def uninstall(
        self, access: WorkspaceAccess, body: RunnerSshUninstall, *, client_ip: str
    ) -> AgentSshState:
        """停止 runner-agent，可选删除接入包目录。还有没删除的环境时不能卸载。"""
        target = await self._existing(access)
        if body.version != target.version:
            raise _version_conflict(target.version)
        self._ready(target)
        if target.client_key_ref is None:
            raise _key_missing()
        live = await self.db.scalar(
            select(RunnerEnvironment.id)
            .where(
                RunnerEnvironment.host_id == target.host_id,
                RunnerEnvironment.state != "deleted",
            )
            .limit(1)
        )
        if live is not None:
            raise ApiError(
                409,
                "RUNNER_HAS_ENVIRONMENTS",
                "还有没删除的环境：先在“环境”里删除（容器与数据卷由 runner-agent 删），再卸载",
            )
        if await self._running(target):
            raise _job_running()
        return await self._start(
            access,
            target,
            kind="uninstall",
            password=body.password,
            remove_files=body.remove_files,
            metadata={
                "host": target.host,
                "port": target.port,
                "remove_files": body.remove_files,
                "with_password": body.password is not None,
            },
            client_ip=client_ip,
        )

    async def _start(
        self,
        access: WorkspaceAccess,
        target: RunnerSshTarget,
        *,
        kind: str,
        metadata: dict[str, Any],
        client_ip: str,
        password: SecretStr | None = None,
        remove_files: bool = False,
        allow_docker_restart: bool = False,
    ) -> AgentSshState:
        assert self.jobs is not None  # noqa: S101 - _ready 已检查
        steps = STEPS if kind == "install" else UNINSTALL_STEPS
        job = RunnerSshJob(
            id=uuid.uuid4(),
            workspace_id=target.workspace_id,
            host_id=target.host_id,
            target_id=target.id,
            kind=kind,
            status="running",
            steps=[{"key": key, "status": "pending", "detail": None} for key in steps],
            log="",
            started_by=access.user_id,
        )
        self.db.add(job)
        await self._record(f"{kind}_started", access, target.host_id, metadata, client_ip)
        self.jobs.reserve(job.id)
        try:
            await self.db.commit()
        except IntegrityError as error:
            self.jobs.release(job.id)
            await self.db.rollback()
            raise _job_running() from error
        except BaseException:
            self.jobs.release(job.id)
            raise
        self.jobs.launch(
            job.id,
            run_job(
                self.jobs.sessionmaker,
                self.settings,
                job.id,
                OneTimePassword(password),
                client_ip=client_ip,
                remove_files=remove_files,
                allow_docker_restart=allow_docker_restart,
            ),
        )
        await self.db.refresh(target)
        return await self._state(target)

    async def revoke(
        self, access: WorkspaceAccess, body: AgentSshRevoke, *, client_ip: str
    ) -> AgentSshState:
        """吊销专用密钥：用它登录删掉 authorized_keys 里的这一行，再删除工作台保存的私钥。"""
        target = await self._existing(access)
        if body.version != target.version:
            raise _version_conflict(target.version)
        if target.client_key_ref is None or not target.client_public_key:
            raise _key_missing()
        if await self._running(target):
            raise _job_running()
        public = target.client_public_key
        reason: str | None = None
        try:
            box = CredentialBox.from_settings(self.settings)
            secret = box.resolve(target.client_key_ref, context=runner_ssh_context(target.id))
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
        await self._record(
            "key_revoked",
            access,
            target.host_id,
            {
                "client_key_fingerprint": _client_fingerprint(public),
                "remote_removed": reason is None,
                "reason": reason,
            },
            client_ip,
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


# ---------------------------------------------------------------- 后台任务


class RunnerInstaller(Installer):
    """部署与卸载运行服务器。`self.target.instance_id` 在这里是运行服务器的 ID。"""

    def key_comment(self) -> str:
        return f"myworkspace-runner-{str(self.target.instance_id)[:8]}"

    async def _claim_physical(self, conn: asyncssh.SSHClientConnection) -> None:
        from app.domain.physical_hosts import claim_physical_host

        identity = await self._physical_identity(conn)
        async with self.sessionmaker() as db:
            host = await db.get(RunnerHost, self.target.instance_id, with_for_update=True)
            if host is None:
                raise JobGone
            await self._locked(db)
            if not await claim_physical_host(
                db,
                identity,
                workspace_id=host.workspace_id,
                role="runner",
                server_id=host.id,
            ):
                raise InstallFailed(
                    "physical_host_conflict", "服务器已在其他位置登记；尚未更改远程部署"
                )
            await db.commit()

    def _existing_key(self) -> asyncssh.SSHKey | None:
        ref = self.target.client_key_ref
        if ref is None:
            return None
        try:
            secret = self._box().resolve(ref, context=runner_ssh_context(self.target.target_id))
            return asyncssh.import_private_key(secret.get_secret_value())
        except (CredentialError, asyncssh.KeyImportError) as error:
            raise InstallFailed(
                "client_key_invalid", "专用私钥无法解密，请吊销后重新部署"
            ) from error

    async def _locked(self, db: AsyncSession) -> RunnerSshTarget:
        target: RunnerSshTarget | None = await db.get(
            RunnerSshTarget, self.target.target_id, with_for_update=True
        )
        if target is None:
            raise JobGone
        if not self._unchanged(target):  # type: ignore[arg-type]
            raise InstallFailed("target_changed", "目标服务器在部署期间被更换，已停止")
        return target

    async def _save_key(self, key: asyncssh.SSHKey, public: str) -> None:
        sealed = self._box().seal(
            key.export_private_key().decode(), context=runner_ssh_context(self.target.target_id)
        )
        async with self.sessionmaker() as db:
            target = await self._locked(db)
            target.client_key_ref = sealed
            target.client_public_key = public
            target.version += 1
            await AuditTrail(db, self.settings).record(
                "runner.ssh.key_authorized",
                workspace_ids=[target.workspace_id],
                actor_id=self.target.started_by,
                resource_type="runner_host",
                resource_id=target.host_id,
                metadata={"client_key_fingerprint": key.get_fingerprint("sha256")},
                client_ip=self.client_ip,
            )
            await db.commit()

    async def _root(
        self, conn: asyncssh.SSHClientConnection, root: bool, sudo: bool, password: SecretStr | None
    ) -> str | None:
        """以 root 运行要用的 stdin（sudo 的密码）；root 账号返回 None。"""
        if root:
            return None
        if password is None:
            raise InstallFailed(
                "password_required",
                "这个账号不是 root：安装脚本要用 sudo 运行，请填写登录密码（只用这一次）",
            )
        if not sudo:
            raise InstallFailed("sudo_unavailable", "服务器上没有 sudo，无法以 root 运行安装脚本")
        secret = password.get_secret_value() + "\n"
        check = await self._run(
            conn, "检查 sudo", "sudo -S -p '' -k true", stdin=secret, seconds=30
        )
        if not check.ok:
            raise InstallFailed("sudo_denied", "这个账号不能使用 sudo（或密码不对）")
        return secret

    async def _issue(self) -> dict[str, tuple[bytes, int]]:
        async with self.sessionmaker() as db:
            host = await db.get(RunnerHost, self.target.instance_id)
            if host is None:
                raise JobGone
            try:
                _, data = await RunnerAdmin(db, self.settings).bundle_for(
                    host, self.target.started_by
                )
            except ApiError as error:
                raise InstallFailed(error.code.lower(), error.message) from error
        # zip 里的文件都在 myworkspace-runner/ 下（手动下载解压就是这个目录）：上传时去掉这一层
        prefix = f"{INSTALL_DIR_NAME}/"
        return {
            name.removeprefix(prefix): value
            for name, value in unzip_bundle(data).items()
            if name.startswith(prefix) and name != prefix
        }

    async def deploy(self, secret: OneTimePassword, *, allow_docker_restart: bool = False) -> None:
        if not self.target.host_key_confirmed:
            raise InstallFailed("host_key_unconfirmed", "服务器的主机公钥还没有确认")
        key = self._existing_key()
        password = secret.get()
        if password is None and key is None:
            raise InstallFailed("password_required", "第一次部署需要登录密码")
        conn: asyncssh.SSHClientConnection | None = None
        try:
            await self.rec.step("connect", "running")
            conn = await (
                self._connect(password=password) if password is not None else self._connect(key=key)
            )
            await self.rec.step(
                "connect", "done", "密码登录（只用这一次）" if password else "专用密钥登录"
            )
            info = await self._inspect(conn)
            await self._claim_physical(conn)
            if password is not None:
                await self._authorize(conn, key)
            else:
                await self.rec.step("authorize_key", "skipped", "专用公钥已经装好")
            sudo = await self._root(conn, info.root, info.sudo, password)
            directory = f"{info.home}/{INSTALL_DIR_NAME}"

            await self.rec.step("upload", "running")
            since = _now()
            files = await self._issue()
            for name in ("tls/client.key",):
                if name in files:
                    self.rec.add_secret(files[name][0].decode("utf-8", "replace").strip())
            env = files.get("runner.env", (b"", 0))[0].decode("utf-8", "replace")
            for line in env.splitlines():
                name, sep, value = line.partition("=")
                if sep and name.strip() == "MYWS_RUNNER_TOKEN":
                    self.rec.add_secret(value.strip())
            prepare = await self._run(
                conn, "准备目录", f"mkdir -p {prov.q(directory)} && chmod 700 {prov.q(directory)}"
            )
            if not prepare.ok:
                raise InstallFailed("upload_failed", f"无法创建目录 {directory}")
            await ssh.put_files(conn, directory, files)
            await self.rec.step("upload", "done", f"接入包已上传到 {directory}")

            await self.rec.step(
                "install", "running", "安装 Docker 与 gVisor、启动 runner-agent（几分钟）"
            )
            result = await self._run(
                conn,
                "运行 install.sh",
                install_command(
                    directory, root=info.root, allow_docker_restart=allow_docker_restart
                ),
                stdin=sudo,
                seconds=INSTALL_TIMEOUT,
            )
            # 密码阶段结束：之后不再需要
            if password is not None:
                self.rec.forget(password.get_secret_value())
            secret.clear()
            password = None
            sudo = None
            managers = gvisor_restart_needed(result)
            if managers is not None:
                raise InstallFailed(
                    GVISOR_RESTART_CODE,
                    f"启用 gVisor 需要重启 Docker，这台服务器上有 {managers} 个 MCP 管理器在运行"
                    "（重启会让它们短暂中断，之后自己恢复）：确认后重新部署并勾选“允许重启 Docker”",
                )
            if not result.ok or "runner_agent_started" not in result.stdout:
                raise InstallFailed("install_failed", "安装脚本没有成功，详见日志")
            await self.rec.step("install", "done", "Docker、gVisor 与 runner-agent 已就绪")
            await self._online(since)
            async with self.sessionmaker() as db:
                target = await self._locked(db)
                target.install_dir = directory
                target.installed_at = _now()
                target.version += 1
                await db.commit()
        finally:
            if conn is not None:
                conn.close()

    async def _online(self, since: datetime) -> None:
        """新凭证签发后 runner-agent 长轮询过一次，就说明它连上了工作台。"""
        await self.rec.step("online", "running", "等待 runner-agent 连上工作台")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ONLINE_TIMEOUT
        while True:
            async with self.sessionmaker() as db:
                seen = await db.scalar(
                    select(RunnerHost.last_seen_at).where(RunnerHost.id == self.target.instance_id)
                )
            if seen is not None and seen >= since:
                break
            if loop.time() >= deadline:
                raise InstallFailed(
                    "not_online",
                    "已经启动，但 5 分钟内没有连上工作台；在服务器的接入包目录里运行"
                    " docker compose logs runner-agent 查看原因",
                )
            await self.rec.flush()
            await asyncio.sleep(ONLINE_POLL)
        await self.rec.step("online", "done", "运行服务器已连上工作台")

    async def remove(self, secret: OneTimePassword, *, remove_files: bool) -> None:
        if not self.target.host_key_confirmed:
            raise InstallFailed("host_key_unconfirmed", "服务器的主机公钥还没有确认")
        key = self._existing_key()
        if key is None:
            raise InstallFailed("client_key_missing", "专用密钥已经吊销，不能卸载")
        password = secret.get()
        conn: asyncssh.SSHClientConnection | None = None
        try:
            await self.rec.step("connect", "running")
            conn = await self._connect(key=key)
            await self.rec.step("connect", "done", "专用密钥登录")
            info = await self._inspect_quiet(conn)
            sudo = await self._root(conn, info.root, info.sudo, password)
            directory = f"{info.home}/{INSTALL_DIR_NAME}"
            await self.rec.step("stop", "running")
            compose = f"docker compose -f {prov.q(directory + '/compose.yaml')} down"
            result = await self._run(
                conn,
                "停止 runner-agent",
                f"if [ -f {prov.q(directory + '/compose.yaml')} ]; then "
                f"{_sudo(compose, root=info.root)}; fi",
                stdin=sudo,
                seconds=300,
            )
            if not result.ok:
                raise InstallFailed("stop_failed", "停止 runner-agent 失败，详见日志")
            await self.rec.step("stop", "done")
            if remove_files:
                await self.rec.step("remove_files", "running")
                removed = await self._run(
                    conn,
                    "删除接入包目录",
                    _sudo(f"rm -rf {prov.q(directory)}", root=info.root),
                    stdin=sudo,
                    seconds=120,
                )
                if not removed.ok:
                    raise InstallFailed("remove_failed", f"删除 {directory} 失败")
                await self.rec.step("remove_files", "done", f"已删除 {directory}")
            else:
                await self.rec.step("remove_files", "skipped", f"保留 {directory}")
            if password is not None:
                self.rec.forget(password.get_secret_value())
            secret.clear()
            async with self.sessionmaker() as db:
                target = await self._locked(db)
                target.installed_at = None
                if remove_files:
                    target.install_dir = None
                target.version += 1
                await db.commit()
        finally:
            if conn is not None:
                conn.close()

    async def _inspect_quiet(self, conn: asyncssh.SSHClientConnection) -> HostInfo:
        """卸载时读服务器信息（root、sudo、家目录），不单独记一步。"""
        result = await self._run(conn, "检查服务器", "sh -s", stdin=prov.script("inspect.sh"))
        if not result.ok:
            raise InstallFailed("inspect_failed", "读取服务器信息失败")
        return parse_inspect(result.stdout)


async def _load(sessionmaker: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> Target:
    async with sessionmaker() as db:
        row = (
            await db.execute(
                select(RunnerSshJob, RunnerSshTarget)
                .join(RunnerSshTarget, RunnerSshTarget.id == RunnerSshJob.target_id)
                .where(RunnerSshJob.id == job_id, RunnerSshJob.status == "running")
            )
        ).first()
    if row is None:
        raise JobGone
    job, target = row
    return Target(
        job_id=job.id,
        kind=job.kind,
        target_id=target.id,
        instance_id=target.host_id,
        workspace_id=target.workspace_id,
        started_by=job.started_by,
        address=target.address,
        port=target.port,
        username=target.username,
        host_key=target.host_key,
        client_key_ref=target.client_key_ref,
        host_key_confirmed=target.host_key_confirmed_at is not None,
    )


async def run_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    job_id: uuid.UUID,
    secret: OneTimePassword,
    *,
    client_ip: str | None = None,
    remove_files: bool = False,
    allow_docker_restart: bool = False,
) -> None:
    """后台协程：部署或卸载运行服务器（按任务记录的 kind）。"""
    try:
        target = await _load(sessionmaker, job_id)
    except JobGone:
        return
    password = secret.get()
    recorder = JobRecorder(
        sessionmaker,
        job_id,
        [password.get_secret_value()] if password is not None else [],
        steps=STEPS if target.kind == "install" else UNINSTALL_STEPS,
        model=RunnerSshJob,
    )
    del password
    label = "部署" if target.kind == "install" else "卸载"

    async def finish(status: str, code: str | None = None, message: str | None = None) -> None:
        await _finish(
            sessionmaker,
            settings,
            recorder,
            target,
            status=status,
            code=code,
            message=message,
            client_ip=client_ip,
            audit=AUDIT,
        )

    installer = RunnerInstaller(sessionmaker, settings, recorder, target, client_ip=client_ip)
    try:
        if target.kind == "install":
            await installer.deploy(secret, allow_docker_restart=allow_docker_restart)
        else:
            await installer.remove(secret, remove_files=remove_files)
    except JobGone:
        return
    except asyncio.CancelledError:
        await asyncio.shield(
            finish("interrupted", "interrupted", f"工作台停止，{label}中断；可以重新开始")
        )
        raise
    except InstallFailed as error:
        await finish("failed", error.code, error.message)
    except ssh.SshError as error:
        await finish("failed", error.reason, error.message)
    except Exception:
        logger.exception("runner ssh job failed", extra={"job_id": str(job_id)})
        await finish("failed", "internal_error", "工作台内部错误，详见服务日志")
    else:
        await finish("succeeded")


__all__ = ["RunnerInstaller", "RunnerSshService", "run_job", "runner_ssh_context"]
