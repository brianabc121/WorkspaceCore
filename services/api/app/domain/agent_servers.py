"""OpenClaw 服务器：添加、核对指纹、保存密码、测试连接、初始化、部署实例、卸载、旧公钥退役、移除。

设计见 docs/v2/MULTI_OPENCLAW_PLAN.md 第 4、7、8 节（T-0145、T-0147）与
docs/v2/agent-servers-redesign/ 的接口与迁移约定（T-0178）。一台服务器上可以有多个实例：

- 服务器用 IP / 端口 / 账号 / 密码添加，登录密码加密保存、只写不读
  （app/domain/agent_server_credentials.py，ADR 2026-11-09-agent-server-password-auth）；
  主机指纹只核对一次，核对之前密码不会发给服务器；
- 部署实例只替换这个实例自己的目录与 Compose 项目，卸载也只碰自己（agent_deployments）；
- 同一台服务器同时只有一个 SSH 任务（部署也一个一个来），与这台服务器上的运维动作互斥；
  替换密码、更换地址也要等这台服务器上没有任务；
- 手动部署的实例按上报的服务器指纹自动归到“自动识别的服务器”（app/domain/host_metrics.py），
  管理员可以手动指定（server_manual）；
- 只有 owner 可以使用（与实例接口一致）；每一步写审计（不含密码）。探测主机公钥、测试连接、
  旧公钥退役会对外发起连接，按用户限流；
- 初始化、部署、卸载（agent_ssh_install 的后台任务）与运维（agent_ops）全程用保存的登录密码登录
  （T-0179）：接口只检查密码已保存、可以解密，任务登录时才按接纳时的凭证版本解密；
  不生成、不安装、不读取 SSH 客户端密钥。旧方案装在服务器上的专用公钥用 `cleanup_legacy_key`
  （密码登录、只删那一行）退役，旧的 `revoke` 返回 410；
- 一次点击新建并部署（app/domain/agent_deployments.py，T-0180）与旧的部署接口共用
  `accept_deployment`（检查前提、建部署记录与任务并登记，不提交、不启动）。
"""

import asyncio
import hashlib
import hmac
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any, NoReturn

from pydantic import BaseModel, SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent_provisioning as prov
from app.agent_bundle import project_name
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import agent_server_resources, agent_server_ssh, idempotency
from app.domain.agent_server_credentials import (
    ServerCredentialError,
    credential_required,
    credential_unreadable,
    password_readable,
    seal_server_password,
)
from app.domain.agent_ssh import (
    PROBES_PER_MINUTE,
    _client_fingerprint,
    _job_out,
    _job_running,
    _unreachable,
    _version_conflict,
)
from app.domain.agent_ssh_install import job_steps
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.host_metrics import STALE_AFTER, HostService, _percent_used, drop_if_empty_auto
from app.domain.placement import Candidate, PlacementError, ResourceRequest, rank_candidates
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
    IdempotencyRequest,
    Task,
    TaskRun,
)
from app.repositories import integrations as integration_repo
from app.schemas.agents import (
    AgentDeployIn,
    AgentDeploymentOut,
    AgentDeploymentState,
    AgentServerAddress,
    AgentServerAssign,
    AgentServerCreate,
    AgentServerCredentialsIn,
    AgentServerDetail,
    AgentServerLegacyCleanupIn,
    AgentServerList,
    AgentServerOut,
    AgentServerPrepare,
    AgentServerSummary,
    AgentServerTestIn,
    AgentServerUpdate,
    AgentSshConfirm,
    AgentUndeployIn,
    ConnectionStatus,
    DeployBlockReason,
    InitializationStatus,
    RunningTaskRef,
    ServerAgentRow,
    ServerAgentState,
    ServerSshOut,
    UnassignedInstance,
)
from app.schemas.host import HostRange
from app.schemas.placement import Placement

if TYPE_CHECKING:
    from app.domain.agent_ssh_install import SshJobRunner

# 部署前的容量提醒（D4：不设硬上限，只提醒）
MEMORY_WARN_BYTES = 2 << 30
DISK_WARN_PERCENT = 85.0
# 旧公钥退役（T-0179）：登录 + 改 authorized_keys 的总时限（期间持有这台服务器的行锁）
LEGACY_CLEANUP_TIMEOUT = 60.0


def _now() -> datetime:
    return datetime.now(UTC)


_CONNECTION_STATUSES: frozenset[str] = frozenset(
    {"connected", "auth_failed", "unreachable", "host_key_changed", "credential_unreadable"}
)


def _connection_status(target: AgentSshTarget, readable: bool) -> ConnectionStatus:
    """连接状态：没保存密码 / 密钥不对优先；其余按最近一次显式检测，没检测过是 unknown。"""
    if target.login_password_ref is None:
        return "credentials_required"
    if not readable:
        return "credential_unreadable"
    reason = target.last_connection_reason
    if reason in _CONNECTION_STATUSES:
        return reason  # type: ignore[return-value]
    return "unknown"


def _ssh_out(target: AgentSshTarget, readable: bool) -> ServerSshOut:
    return ServerSshOut(
        host=target.host,
        address=target.address,
        port=target.port,
        username=target.username,
        host_key_algorithm=target.host_key_algorithm,
        host_key_fingerprint=target.host_key_fingerprint,
        host_key_confirmed=target.host_key_confirmed_at is not None,
        host_key_confirmed_at=target.host_key_confirmed_at,
        password_set=target.login_password_ref is not None,
        credential_revision=target.credential_revision,
        connection_status=_connection_status(target, readable),
        last_connection_at=target.last_connection_at,
        legacy_key_cleanup_state=target.legacy_key_cleanup_state,
        # 迁移兼容（已弃用）：旧方案的专用公钥还在不在；不参与是否可以部署的判断
        client_key_installed=target.client_key_ref is not None,
        client_key_fingerprint=_client_fingerprint(target.client_public_key),
        prepared_at=target.prepared_at,
    )


def _initialization(target: AgentSshTarget, job: AgentSshJob | None) -> InitializationStatus:
    """初始化状态：由 prepared_at 与最近一次初始化任务推导（不另建一套状态机）。

    最近的任务进行中 → initializing；
    最近的任务失败 / 中断、且之后没有成功过 → failed / interrupted；
    有 prepared_at → ready；其余 not_initialized。
    """
    if job is not None and job.status == "running":
        return "initializing"
    if (
        job is not None
        and job.status in {"failed", "interrupted"}
        and (target.prepared_at is None or target.prepared_at < job.started_at)
    ):
        return "failed" if job.status == "failed" else "interrupted"
    return "ready" if target.prepared_at is not None else "not_initialized"


_INIT_BLOCKS: dict[str, DeployBlockReason] = {
    "not_initialized": "not_initialized",
    "initializing": "initializing",
    "failed": "initialization_failed",
    "interrupted": "initialization_interrupted",
}


@dataclass(frozen=True)
class _DeploymentStats:
    """一台服务器上部署记录的汇总（T-0178；口径见接口与迁移约定第 3 节）。"""

    managed: int = 0
    successful: int = 0
    unknown: int = 0
    mem_limit_mb: int = 0
    mem_unlimited: int = 0
    cpus: float = 0.0
    cpus_unlimited: int = 0


@dataclass(frozen=True)
class _ServerFacts:
    counts: tuple[int, int] = (0, 0)
    prepare_job: AgentSshJob | None = None
    busy: bool = False
    stats: _DeploymentStats = field(default_factory=_DeploymentStats)


def _block_reason(
    target: AgentSshTarget | None,
    initialization: InitializationStatus | None,
    busy: bool,
    readable: bool,
) -> DeployBlockReason | None:
    """不能部署的第一个原因（只是本地判断，GET 不连服务器；实际部署前仍会真实检查）。"""
    if target is None or initialization is None:
        return "not_ssh"
    if target.login_password_ref is None:
        return "credentials_required"
    if not readable or target.last_connection_reason == "credential_unreadable":
        return "credential_unreadable"
    if target.host_key_confirmed_at is None:
        return "host_key_unconfirmed"
    if target.last_connection_reason in {"host_key_changed", "auth_failed"}:
        return target.last_connection_reason  # type: ignore[return-value]
    if initialization != "ready":
        return _INIT_BLOCKS[initialization]
    if busy:
        return "busy"
    return None


def _server_out(
    server: AgentServer,
    target: AgentSshTarget | None,
    facts: _ServerFacts,
    now: datetime,
    box: CredentialBox | None,
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
    ssh_target = target if server.kind == "ssh" else None
    readable = ssh_target is not None and password_readable(box, ssh_target.login_password_ref)
    initialization = (
        _initialization(ssh_target, facts.prepare_job) if ssh_target is not None else None
    )
    block = _block_reason(ssh_target, initialization, facts.busy, readable)
    stats = facts.stats
    # 部署成功写 runtime_installed=true、卸载保留目录写 false（T-0180）；
    # 还有 installed_at 有值而运行状态未知（null）的旧记录时，成功数保守地给 null
    partial = stats.unknown > 0
    return AgentServerOut(
        id=server.id,
        name=server.name,
        kind="ssh" if server.kind == "ssh" else "auto",
        ssh=_ssh_out(target, readable) if target is not None else None,
        resource_snapshot=server.resource_snapshot or None,
        reported_at=server.reported_at,
        stale=server.reported_at is None or now - server.reported_at > STALE_AFTER,
        cores=latest.get("cores"),
        cpu_percent=latest.get("cpu_percent"),
        load15=latest.get("load15"),
        mem_total=latest.get("mem_total"),
        mem_used_percent=_percent_used(latest.get("mem_total"), latest.get("mem_available")),
        disk_used_percent=max(disks) if disks else None,
        instances_total=facts.counts[0],
        instances_online=facts.counts[1],
        alerts=sorted((server.alerts or {}).keys()),
        initialization_status=initialization,
        deployable=block is None,
        deploy_block_reason=block,
        managed_deployments=stats.managed,
        successful_deployments=None if partial else stats.successful,
        deployment_counts_partial=partial,
        allocated_mem_limit_mb=None if stats.mem_unlimited else stats.mem_limit_mb,
        allocated_cpus=None if stats.cpus_unlimited else round(stats.cpus, 2),
        has_unlimited_limits=bool(stats.mem_unlimited or stats.cpus_unlimited),
        version=server.version,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _summary(items: list[AgentServerOut], now: datetime) -> AgentServerSummary:
    ssh_items = [s for s in items if s.kind == "ssh" and s.ssh is not None]
    partial = any(s.deployment_counts_partial for s in items)
    return AgentServerSummary(
        server_count=len(items),
        ready_count=sum(1 for s in ssh_items if s.initialization_status == "ready"),
        successful_deployments=(
            None if partial else sum(s.successful_deployments or 0 for s in items)
        ),
        not_initialized_count=sum(
            1 for s in ssh_items if s.initialization_status == "not_initialized"
        ),
        needs_credentials_count=sum(1 for s in ssh_items if s.ssh and not s.ssh.password_set),
        deployment_counts_partial=partial,
        generated_at=now,
    )


def _agent_state(deployment: AgentDeployment | None, job: AgentSshJob | None) -> ServerAgentState:
    """服务器详情里一个智能体的部署状态（没有真实容器检测，不输出“已停止”之类的推断）。"""
    if deployment is None:
        return "associated"
    if job is not None and job.kind == "install":
        if job.status == "running":
            return "deploying"
        if job.status in {"failed", "interrupted"}:
            return "failed" if job.status == "failed" else "interrupted"
    if deployment.runtime_installed is False:
        return "retained"
    if deployment.installed_at is not None:
        return "deployed"
    if job is not None and job.status == "running":
        return "deploying"
    return "failed"


def _block_error(reason: DeployBlockReason) -> ApiError:
    """不能部署的原因 → 接口错误（实际部署前的检查；GET 只返回原因）。"""
    if reason == "not_ssh":
        return ApiError(
            409, "AGENT_SERVER_NOT_SSH", "这台服务器是按上报自动识别的，不能经 SSH 部署"
        )
    if reason == "credentials_required":
        return credential_required().api()
    if reason == "credential_unreadable":
        return credential_unreadable().api()
    if reason == "host_key_unconfirmed":
        return ApiError(409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹")
    if reason in {"host_key_changed", "auth_failed"}:
        return ApiError(
            409,
            "AGENT_SERVER_CONNECTION_FAILED",
            "最近一次连接检测失败（主机公钥与确认过的不一致）：先核对服务器后重新添加"
            if reason == "host_key_changed"
            else "最近一次连接检测登录被拒绝：先更新登录密码并重新测试连接",
            details={"reason": reason},
        )
    if reason == "busy":
        return _job_running()
    return ApiError(
        409,
        "AGENT_SERVER_NOT_INITIALIZED",
        {
            "not_initialized": "这台服务器还没有初始化：先在服务器页初始化",
            "initializing": "这台服务器正在初始化，结束后再部署",
            "initialization_failed": "最近一次初始化失败：处理后重新初始化",
            "initialization_interrupted": "最近一次初始化中断：重新初始化",
        }.get(reason, "这台服务器的环境还没有就绪"),
        details={"reason": reason},
    )


def _legacy_key_present(target: AgentSshTarget) -> bool:
    """旧方案（一次密码 + 专用密钥）的专用公钥 / 私钥引用还在。"""
    return target.client_key_ref is not None or bool(target.client_public_key)


def _has_resource_commitment(deployment: AgentDeployment) -> bool:
    values = deployment.selection or {}
    try:
        return all(
            isfinite(float(values[key])) and float(values[key]) > 0
            for key in ("cpus", "memory_mb", "disk_gb")
        )
    except (KeyError, TypeError, ValueError):
        return False


def _legacy_reconciliation() -> ApiError:
    return ApiError(
        409,
        "LEGACY_RESOURCE_RECONCILIATION_REQUIRED",
        "旧部署缺少可信的实际资源账本：请先通过受信 Docker 检查核对资源，"
        "或确认容器已卸载后重试；实例与数据会保留",
    )


def deployment_out(deployment: AgentDeployment) -> AgentDeploymentOut:
    return AgentDeploymentOut(
        selection=deployment.selection or None,
        server_id=deployment.server_id,
        install_dir=deployment.install_dir,
        installed_at=deployment.installed_at,
        runtime_installed=deployment.runtime_installed,
        version=deployment.version,
    )


def _connection_snapshot(target: AgentSshTarget) -> tuple[object, ...]:
    """决定“连的是哪台机器、用哪个密码”的字段；检测前后不一致时不写检测结果。"""
    return (
        target.address,
        target.port,
        target.username,
        target.host_key,
        target.host_key_confirmed_at is not None,
        target.credential_revision,
    )


def _default_name(host: str, port: int) -> str:
    """名称省略时用 host:port（IPv6 加方括号），超过 100 字符截断。"""
    name = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return name[:100]


@dataclass(frozen=True)
class _Receipt:
    """带 Idempotency-Key 的服务器写请求：成功时与业务写入一起记下（只存服务器 ID）。"""

    key: uuid.UUID
    fingerprint: str
    op: str


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
        self._receipt: _Receipt | None = None

    # ---------------------------------------------------------------- 加密与幂等

    def _box_or_none(self) -> CredentialBox | None:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError:
            return None

    def _require_box(self) -> CredentialBox:
        box = self._box_or_none()
        if box is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")
        return box

    def _require_password(self, target: AgentSshTarget) -> None:
        """要经 SSH 登录的操作：服务器已保存登录密码、当前配置能解开（不在这里解密）。

        真正解密在登录时（后台任务按接纳时的凭证版本）；密文损坏要到那时才发现。
        """
        if target.login_password_ref is None:
            raise credential_required().api()
        if not password_readable(self._box_or_none(), target.login_password_ref):
            raise credential_unreadable().api()

    def _fingerprint(self, access: WorkspaceAccess, method: str, path: str, body: BaseModel) -> str:
        """请求指纹：密码换成 HMAC（工作台密钥派生的子密钥），不把可撞库的明文摘要存进幂等记录。"""
        data = body.model_dump(mode="json")
        secrets = {name: value for name, value in body if isinstance(value, SecretStr)}
        if secrets:
            salt = self._require_box().derive_key("agent-server-idempotency")
            for name, value in secrets.items():
                digest = hmac.new(salt, value.get_secret_value().encode(), hashlib.sha256)
                data[name] = f"hmac:{digest.hexdigest()}"
        return idempotency.fingerprint(access.workspace.id, method, path, data)

    async def idempotent(
        self,
        access: WorkspaceAccess,
        key: uuid.UUID | None,
        *,
        op: str,
        method: str,
        path: str,
        body: BaseModel,
        run: Callable[[], Awaitable[AgentServerDetail]],
    ) -> tuple[AgentServerDetail, bool]:
        """服务器写接口的可选 Idempotency-Key（契约第 1 节）。返回 (详情, 是否重放)。

        记录只在成功时与业务写入一起提交，内容只有操作名与服务器 ID；
        重放返回这台服务器现在的详情（不缓存响应正文，也不再次连接服务器）。
        失败的请求不留记录，可以用同一个键重试。
        """
        actor_id = access.user_id
        if key is None or actor_id is None:
            return await run(), False
        fingerprint = self._fingerprint(access, method, path, body)
        record = await integration_repo.get_idempotency(self.db, actor_id=actor_id, key=key)
        if record is not None and record.expires_at <= _now():
            await integration_repo.delete_idempotency(self.db, record.id)
            await self.db.commit()
            record = None
        if record is not None:
            if record.request_fingerprint != fingerprint:
                await self.db.rollback()
                raise ApiError(
                    409, "IDEMPOTENCY_KEY_REUSED", "同一个 Idempotency-Key 已用于不同的请求"
                )
            server_id = record.context_json.get("server_id")
            if record.status != "succeeded" or not isinstance(server_id, str):
                await self.db.rollback()
                raise ApiError(
                    409,
                    "IDEMPOTENCY_IN_PROGRESS",
                    "同一个请求仍在处理，请稍后再查询",
                    headers={"Retry-After": "1"},
                )
            await self.db.commit()
            server = await self._server(access, uuid.UUID(server_id))
            return await self._detail(server), True
        self._receipt = _Receipt(key=key, fingerprint=fingerprint, op=op)
        try:
            return await run(), False
        finally:
            self._receipt = None

    def _stamp(self, access: WorkspaceAccess, server_id: uuid.UUID, status_code: int) -> None:
        """有 Idempotency-Key 时，把“成功、服务器 ID”与业务写入放进同一个事务。"""
        receipt = self._receipt
        if receipt is None:
            return
        now = _now()
        self.db.add(
            IdempotencyRequest(
                workspace_id=access.workspace.id,
                actor_id=access.user_id,
                idempotency_key=receipt.key,
                request_fingerprint=receipt.fingerprint,
                status="succeeded",
                response_code=status_code,
                response_json={"body": {"server_id": str(server_id)}},
                context_json={
                    "op": receipt.op,
                    "server_id": str(server_id),
                    "started_at": now.isoformat(),
                },
                expires_at=now + idempotency.RETENTION,
            )
        )

    async def _commit(self) -> None:
        try:
            await self.db.commit()
        except IntegrityError as error:
            await self.db.rollback()
            if self._receipt is None or "idempotency_requests" not in str(error.orig):
                raise
            # 并发的同一个请求（同一个键）先提交了：这一个的写入随事务回滚
            raise ApiError(
                409,
                "IDEMPOTENCY_IN_PROGRESS",
                "同一个请求仍在处理，请稍后再查询",
                headers={"Retry-After": "1"},
            ) from None

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

    async def _facts(
        self, server_ids: list[uuid.UUID], targets: dict[uuid.UUID, AgentSshTarget]
    ) -> dict[uuid.UUID, _ServerFacts]:
        """一批服务器的实例数、最近一次初始化任务、是否有进行中的任务、部署记录汇总（批量查询）。"""
        if not server_ids:
            return {}
        counts = await self._counts(server_ids)
        target_ids = [t.id for t in targets.values()]
        prepare_jobs: dict[uuid.UUID, AgentSshJob] = {}
        busy_targets: set[uuid.UUID] = set()
        if target_ids:
            prepare_jobs = {
                job.target_id: job
                for job in (
                    await self.db.scalars(
                        select(AgentSshJob)
                        .where(AgentSshJob.target_id.in_(target_ids), AgentSshJob.kind == "prepare")
                        .ext(distinct_on(AgentSshJob.target_id))
                        .order_by(AgentSshJob.target_id, AgentSshJob.started_at.desc())
                        .execution_options(populate_existing=True)
                    )
                ).all()
            }
            busy_targets = set(
                (
                    await self.db.scalars(
                        select(AgentSshJob.target_id).where(
                            AgentSshJob.target_id.in_(target_ids), AgentSshJob.status == "running"
                        )
                    )
                ).all()
            )
        busy_servers = set(
            (
                await self.db.scalars(
                    select(AgentOpsAction.server_id).where(
                        AgentOpsAction.server_id.in_(server_ids),
                        AgentOpsAction.status == "running",
                    )
                )
            ).all()
        )
        installed = AgentDeployment.installed_at.is_not(None)
        stats = {
            row[0]: _DeploymentStats(
                managed=int(row[1]),
                successful=int(row[2]),
                unknown=int(row[3]),
                mem_limit_mb=int(row[4] or 0),
                mem_unlimited=int(row[5]),
                cpus=float(row[6] or 0),
                cpus_unlimited=int(row[7]),
            )
            for row in (
                await self.db.execute(
                    select(
                        AgentDeployment.server_id,
                        func.count(),
                        func.count().filter(installed, AgentDeployment.runtime_installed.is_(True)),
                        func.count().filter(installed, AgentDeployment.runtime_installed.is_(None)),
                        func.sum(AgentInstance.mem_limit_mb),
                        func.count().filter(AgentInstance.mem_limit_mb.is_(None)),
                        func.sum(AgentInstance.cpus),
                        func.count().filter(AgentInstance.cpus.is_(None)),
                    )
                    .join(AgentInstance, AgentInstance.id == AgentDeployment.instance_id)
                    .where(AgentDeployment.server_id.in_(server_ids))
                    .group_by(AgentDeployment.server_id)
                )
            ).all()
        }
        facts = {}
        for server_id in server_ids:
            target = targets.get(server_id)
            facts[server_id] = _ServerFacts(
                counts=counts.get(server_id, (0, 0)),
                prepare_job=prepare_jobs.get(target.id) if target is not None else None,
                busy=server_id in busy_servers
                or (target is not None and target.id in busy_targets),
                stats=stats.get(server_id, _DeploymentStats()),
            )
        return facts

    async def _out(self, server: AgentServer) -> AgentServerOut:
        target = await self._target(server)
        facts = await self._facts([server.id], {server.id: target} if target else {})
        return _server_out(server, target, facts[server.id], _now(), self._box_or_none())

    async def servers_out(
        self, server_ids: list[uuid.UUID], now: datetime
    ) -> dict[uuid.UUID, AgentServerOut]:
        """一批服务器的输出（批量查询，总览用）。调用方保证这些服务器属于当前工作空间。"""
        if not server_ids:
            return {}
        servers = list(
            (await self.db.scalars(select(AgentServer).where(AgentServer.id.in_(server_ids)))).all()
        )
        targets = {
            t.server_id: t
            for t in (
                await self.db.scalars(
                    select(AgentSshTarget).where(AgentSshTarget.server_id.in_(server_ids))
                )
            ).all()
        }
        facts = await self._facts([s.id for s in servers], targets)
        box = self._box_or_none()
        return {s.id: _server_out(s, targets.get(s.id), facts[s.id], now, box) for s in servers}

    async def server_of(
        self, access: WorkspaceAccess, server_id: uuid.UUID, *, lock: bool = False
    ) -> AgentServer:
        """当前工作空间里的这台服务器（别的空间的 404）。"""
        return await self._server(access, server_id, lock=lock)

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
        facts = await self._facts(ids, targets)
        box = self._box_or_none()
        items = [_server_out(s, targets.get(s.id), facts[s.id], now, box) for s in servers]
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
            summary=_summary(items, now),
            items=items,
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
        jobs = {
            job.instance_id: job
            for job in (
                await self.db.scalars(
                    select(AgentSshJob)
                    .where(AgentSshJob.instance_id.in_(ids))
                    .ext(distinct_on(AgentSshJob.instance_id))
                    .order_by(AgentSshJob.instance_id, AgentSshJob.started_at.desc())
                    .execution_options(populate_existing=True)
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
            job = jobs.get(instance.id)
            rows.append(
                ServerAgentRow(
                    instance_id=instance.id,
                    name=instance.name,
                    status=instance.status,
                    deployed=deployment is not None,
                    deployment_state=_agent_state(deployment, job),
                    latest_job=_job_out(job) if job else None,
                    deployment_project=project_name(str(instance.id)) if deployment else None,
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
        return await self.deployment_state_of(instance)

    async def deployment_state_of(
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
            deployment=deployment_out(deployment) if deployment else None,
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
        """添加一台服务器：解析地址、读取主机公钥（不登录），加密保存登录密码。

        确认指纹是另外一步；确认之前密码不会发给服务器（这里只做密钥交换、读主机公钥）。
        """
        box = self._require_box()
        address, key = await self._probe(access, body.host, body.port)
        await self._duplicate(access, address, body.port, body.username, skip=uuid.uuid4())
        name = body.name or _default_name(body.host, body.port)
        server = AgentServer(workspace_id=access.workspace.id, name=name, kind="ssh")
        self.db.add(server)
        await self.db.flush()
        # 密文的用途绑定到这条记录：先定下 ID 再加密
        target_id = uuid.uuid4()
        target = AgentSshTarget(
            id=target_id,
            workspace_id=access.workspace.id,
            server_id=server.id,
            host=body.host,
            address=address,
            port=body.port,
            username=body.username,
            host_key_algorithm=key.algorithm,
            host_key=key.public_key,
            host_key_fingerprint=key.fingerprint,
            login_password_ref=seal_server_password(box, target_id, body.login_password),
            credential_revision=1,
            legacy_key_cleanup_state="not_needed",
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
                "name": name,
                "host": body.host,
                "address": address,
                "port": body.port,
                "username": body.username,
                "host_key_fingerprint": key.fingerprint,
                "login_saved": True,
                "auth_revision": 1,
            },
            client_ip=client_ip,
        )
        self._stamp(access, server.id, 201)
        await self._commit()
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
        """更换地址、端口或账号（连同登录密码）：重新读取主机公钥，要重新确认，环境要重新初始化。

        还有部署记录时拒绝：不能把原有实例挂到另一台机器上（不做隐式迁移）。
        旧方案的专用公钥还没退役时不拒绝（T-0179）：那一行留在原来的机器上，工作台再也连不上它，
        清掉工作台里的旧引用、记为 manual_required，并在 warning 里给出要手动删除的那一行。
        """
        box = self._require_box()
        server = await self._server(access, server_id)
        target = await self._ssh_target(server, lock=False)
        if body.version != server.version:
            raise _version_conflict(server.version)
        await self._check_replaceable(server, target)
        # 探测要几秒：探测完再锁行、重新检查（旧指纹的确认绝不能落到新公钥上）
        address, key = await self._probe(access, body.host, body.port)
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        await self._check_replaceable(server, target)
        if await self._busy(target):
            raise _job_running()
        await self._duplicate(access, address, body.port, body.username, skip=target.id)
        server.resource_snapshot = {}
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
        target.login_password_ref = seal_server_password(box, target.id, body.login_password)
        target.credential_revision += 1
        target.last_connection_at = None
        target.last_connection_reason = None
        left = target.client_public_key if _legacy_key_present(target) else None
        left_fingerprint = _client_fingerprint(left)
        if _legacy_key_present(target):
            target.client_key_ref = None
            target.client_public_key = None
            target.legacy_key_cleanup_state = "manual_required"
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
                "login_saved": True,
                "auth_revision": target.credential_revision,
                "legacy_key_left": left_fingerprint,
            },
            client_ip=client_ip,
        )
        self._stamp(access, server.id, 200)
        await self._commit()
        await self.db.refresh(server)
        warning = None
        if left:
            warning = (
                "原来那台机器上还留着旧方案装的工作台专用公钥，工作台已不再连接它："
                f"请在原机器的 ~/.ssh/authorized_keys 里手动删除这一行：{left}"
            )
        return await self._detail(server, warning=warning)

    async def _check_replaceable(self, server: AgentServer, target: AgentSshTarget) -> None:
        deployed = await self.db.scalar(
            select(func.count())
            .select_from(AgentDeployment)
            .where(AgentDeployment.server_id == server.id)
        )
        if deployed:
            raise ApiError(
                409,
                "AGENT_SERVER_HAS_DEPLOYMENTS",
                "这台服务器上还有部署的实例：先卸载它们，或者把新机器添加成另一台服务器",
                details={"deployments": int(deployed)},
            )

    async def set_credentials(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentServerCredentialsIn,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """替换保存的登录密码：凭证版本加 1、连接状态改回未知；保留初始化记录与实例。

        这台服务器上有进行中的任务或运维动作时拒绝（原密码不变），之后要重新测试连接。
        """
        box = self._require_box()
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if await self._busy(target):
            raise _job_running()
        replaced = target.login_password_ref is not None
        target.login_password_ref = seal_server_password(box, target.id, body.login_password)
        target.credential_revision += 1
        target.last_connection_at = None
        target.last_connection_reason = None
        target.version += 1
        server.version += 1
        await self.audit.record(
            "agents.server.credentials_set",
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={"replaced": replaced, "auth_revision": target.credential_revision},
            client_ip=client_ip,
        )
        self._stamp(access, server.id, 200)
        await self._commit()
        await self.db.refresh(server)
        return await self._detail(server)

    async def test_connection(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentServerTestIn,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """测试连接：按固定的主机公钥、用保存的密码登录，只读检查账号权限与环境。

        不安装 Docker、不建容器、不写 authorized_keys。登录不了（密码错误、连不上、主机公钥变化、
        密码解不开）记在连接状态里、返回 200；版本过期、没确认指纹、没保存密码仍是接口错误。
        """
        box = self._require_box()
        server = await self._server(access, server_id)
        target = await self._ssh_target(server, lock=False)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        if target.login_password_ref is None:
            raise credential_required().api()
        await self._check_probe_rate(access)
        seen = _connection_snapshot(target)
        revision = target.credential_revision
        # 连接与检查要几秒到几十秒：不占着数据库事务
        await self.db.commit()
        try:
            check = await agent_server_ssh.check_agent_server(
                target, box, expected_revision=revision
            )
        except ServerCredentialError as error:
            raise error.api() from error
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if _connection_snapshot(target) != seen:
            # 检测期间地址、账号、主机公钥或密码被改了：结果属于旧的连接信息，不写
            raise _version_conflict(server.version)
        target.last_connection_at = _now()
        target.last_connection_reason = check.reason
        await self.audit.record(
            "agents.server.connection_tested",
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata={"result": check.reason, "auth_revision": revision},
            client_ip=client_ip,
        )
        self._stamp(access, server.id, 200)
        await self._commit()
        await self.db.refresh(server)
        return await self._detail(server, warning=check.warning)

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

    def ready_to_run(self) -> None:
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
        """初始化服务器（T-0179）：后台任务用保存的登录密码登录，缺 Docker 才按锁定脚本安装，
        准备平台受控的部署根目录并做最终检查。不建实例、不发接入包、不装公钥；可以重复执行。
        """
        self.ready_to_run()
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        self._require_password(target)
        if await self._busy(target):
            raise _job_running()
        revision = target.credential_revision
        job = await self._new_job(
            access,
            target,
            kind="prepare",
            instance_id=None,
            metadata={"host": target.host, "port": target.port, "username": target.username},
            client_ip=client_ip,
        )
        await self.commit_job(job)
        self.launch(job, revision=revision, client_ip=client_ip)
        await self.db.refresh(server)
        return await self._detail(server)

    async def _new_job(
        self,
        access: WorkspaceAccess,
        target: AgentSshTarget,
        *,
        kind: str,
        instance_id: uuid.UUID | None,
        metadata: dict[str, object],
        client_ip: str,
    ) -> AgentSshJob:
        """新建任务（running）、写审计并在进程内登记；不提交、不启动（调用方一处提交后 `launch`）。

        调用方已锁行并检查过没有进行中的任务。提交失败时要 `release_job`（`commit_job` 会做）。
        任务里不放密码：登录时按 `credential_revision` 解密保存的密码。
        """
        assert self.runner is not None  # noqa: S101 - 调用方已检查
        job = AgentSshJob(
            id=uuid.uuid4(),
            workspace_id=target.workspace_id,
            instance_id=instance_id,
            target_id=target.id,
            kind=kind,
            status="running",
            steps=[{"key": key, "status": "pending", "detail": None} for key in job_steps(kind)],
            log="",
            started_by=access.user_id,
        )
        self.db.add(job)
        try:
            # 先写入任务：同一台服务器上已经有进行中的任务时（部分唯一索引）在这里就失败，
            # 整个事务回滚（一次点击部署时连同刚登记的实例），不会留下半个实例
            await self.db.flush()
        except IntegrityError as error:
            await self.db.rollback()
            raise _job_running() from error
        await self.audit.record(
            f"agents.ssh.{kind}_started",
            workspace_ids=[target.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance" if instance_id else "agent_server",
            resource_id=instance_id or target.server_id,
            metadata={
                **metadata,
                "server_id": str(target.server_id),
                "auth": "saved_password",
                "auth_revision": target.credential_revision,
            },
            client_ip=client_ip,
        )
        # 提交前先登记：提交之后、启动协程之前，别的请求不会把它当成失效的任务
        self.runner.reserve(job.id)
        return job

    def release_job(self, job: AgentSshJob) -> None:
        """接纳了、但没能提交的任务：取消进程内的登记。"""
        if self.runner is not None:
            self.runner.release(job.id)

    async def commit_job(self, job: AgentSshJob) -> None:
        """提交接纳的任务；同一台服务器上同时开始了另一个任务（部分唯一索引）时 409。"""
        try:
            await self.db.commit()
        except IntegrityError as error:
            self.release_job(job)
            await self.db.rollback()
            raise _job_running() from error
        except BaseException:
            self.release_job(job)
            raise

    def launch(
        self,
        job: AgentSshJob,
        *,
        revision: int,
        client_ip: str,
        remove_files: bool = False,
    ) -> None:
        """提交之后启动后台任务；登录时按 revision 解密保存的密码（被替换过就停止）。"""
        assert self.runner is not None  # noqa: S101 - 调用方已检查
        self.runner.start(
            job.id, client_ip=client_ip, remove_files=remove_files, expected_revision=revision
        )

    async def revoke(self) -> NoReturn:
        """旧 OpenClaw 专用密钥的吊销入口已退役（T-0179）：不能悄悄用旧私钥做最后一次登录。"""
        raise ApiError(
            410,
            "AGENT_SSH_KEY_AUTH_RETIRED",
            "工作台不再用 SSH 密钥登录 OpenClaw 服务器：补录登录密码后，"
            "用“旧公钥退役”（POST /agents/servers/{server_id}/legacy-key-cleanup）"
            "以密码登录、只删掉本工作台那一条公钥",
            details={"replacement": "legacy-key-cleanup"},
        )

    async def cleanup_legacy_key(
        self,
        access: WorkspaceAccess,
        server_id: uuid.UUID,
        body: AgentServerLegacyCleanupIn,
        *,
        client_ip: str,
    ) -> AgentServerDetail:
        """旧公钥定向退役（T-0179）：用保存的登录密码登录，只删掉 authorized_keys 里本记录那把公钥。

        - 前提：保存了密码且能解开、主机公钥已确认、这台服务器没有进行中的任务或运维动作；
        - 成功：清空旧私钥引用与公钥、记为 done 并审计；
        - 服务器上失败（连不上、登录被拒、主机公钥变化、写不进 authorized_keys）：保留旧引用，
          记为 manual_required，warning 里说明原因与要手动删除的那一行（可以稍后重试）；
          绝不拿旧私钥登录作回退。
        登录与改文件期间持有这台服务器与 SSH 记录的行锁（有总时限），同机的任务、运维、改密码、
        改地址都要等它结束（多个 API 进程之间同样有效）。
        """
        box = self._require_box()
        server = await self._server(access, server_id, lock=True)
        target = await self._ssh_target(server)
        if body.version != server.version:
            raise _version_conflict(server.version)
        public = target.client_public_key
        fingerprint = _client_fingerprint(public)
        if public is None or fingerprint is None:
            raise ApiError(
                409,
                "AGENT_SERVER_NO_LEGACY_KEY",
                "这台服务器没有需要退役的旧专用公钥"
                if not _legacy_key_present(target)
                else "工作台没有记下旧公钥本身，无法定向清理：请在服务器的 ~/.ssh/authorized_keys"
                " 里手动删除注释以 myworkspace-server- 开头的那一行",
            )
        if target.host_key_confirmed_at is None:
            raise ApiError(
                409, "AGENT_SSH_HOST_KEY_UNCONFIRMED", "先核对并确认服务器的主机公钥指纹"
            )
        self._require_password(target)
        if await self._busy(target):
            raise _job_running()
        await self._check_probe_rate(access)
        revision = target.credential_revision
        failure: ssh.SshError | None = None
        reached = False
        try:
            async with asyncio.timeout(LEGACY_CLEANUP_TIMEOUT):
                conn = await agent_server_ssh.connect_agent_server(
                    target, box, expected_revision=revision
                )
                reached = True
                try:
                    result = await ssh.run(conn, prov.revoke_key_command(public), seconds=30)
                finally:
                    conn.close()
            if not result.ok:
                failure = ssh.SshError(
                    "command_failed", "写不进 ~/.ssh/authorized_keys（或删除后仍能找到这把公钥）"
                )
        except ServerCredentialError as error:
            # 本地问题（密文解不开 / 刚被替换）：什么都不改
            await self.db.rollback()
            raise error.api() from error
        except TimeoutError:
            failure = ssh.SshError("timeout", "连接或执行超时")
        except ssh.SshError as error:
            failure = error
        target.last_connection_at = _now()
        target.last_connection_reason = (
            "connected"
            if failure is None or reached
            else agent_server_ssh.connection_reason(failure)
        )
        warning: str | None = None
        metadata: dict[str, object] = {
            "client_key_fingerprint": fingerprint,
            "auth_revision": revision,
        }
        if failure is None:
            target.client_key_ref = None
            target.client_public_key = None
            target.legacy_key_cleanup_state = "done"
            action = "agents.server.legacy_key_cleaned"
        else:
            target.legacy_key_cleanup_state = "manual_required"
            action = "agents.server.legacy_key_cleanup_failed"
            metadata["reason"] = failure.reason
            warning = (
                f"没能从服务器上删除旧公钥（{failure.message}）：可以稍后重试，"
                f"或在服务器的 ~/.ssh/authorized_keys 里手动删除这一行：{public}"
            )
        target.version += 1
        server.version += 1
        await self.audit.record(
            action,
            workspace_ids=[server.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server.id,
            metadata=metadata,
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(server)
        return await self._detail(server, warning=warning)

    async def remove(
        self, access: WorkspaceAccess, server_id: uuid.UUID, *, version: int, client_ip: str
    ) -> None:
        """移除服务器。SSH 服务器要先卸载上面的实例（没有部署记录）、没有进行中的任务；
        旧方案的专用公钥还没退役（pending）时先完成退役（退役失败、记为 manual_required 后可以移除，
        那一行由 owner 手动删除）。不删容器、不改 sshd。
        自动识别的服务器直接移除，上面的实例改为“未归属”（下一次上报会重新识别）。"""
        server = await self._server(access, server_id, lock=True)
        if version != server.version:
            raise _version_conflict(server.version)
        target = await self._target(server, lock=True)
        if target is not None and await self._busy(target):
            raise _job_running()
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
        if (
            target is not None
            and _legacy_key_present(target)
            and target.legacy_key_cleanup_state != "manual_required"
        ):
            raise ApiError(
                409,
                "AGENT_SERVER_LEGACY_KEY_PENDING",
                "这台服务器上还留着旧方案装的工作台专用公钥：先补录登录密码并完成“旧公钥退役”"
                "（服务器连不上时，退役会给出要手动删除的那一行，之后就可以移除）",
                details={"legacy_key_cleanup_state": target.legacy_key_cleanup_state},
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
            metadata={
                "name": server.name,
                "kind": server.kind,
                "legacy_key_left": _client_fingerprint(target.client_public_key)
                if target is not None
                else None,
            },
            client_ip=client_ip,
        )
        await self.db.delete(server)
        await self.db.commit()

    async def placement_candidate(
        self,
        server: AgentServer,
        *,
        disk_gb: float,
        exclude_instance_id: uuid.UUID | None = None,
    ) -> Candidate:
        """Caller holds server row lock through reservation commit."""
        snap = server.resource_snapshot or {}
        if not agent_server_resources.is_fresh(
            snap, _now(), self.settings.agent_placement_metrics_max_age_seconds
        ):
            raise ApiError(409, "METRICS_STALE", "服务器资源指标未知或过期：请刷新资源后重试")
        if not snap.get("runtime_ready") or not snap.get("network_ready"):
            raise ApiError(409, "TARGET_UNAVAILABLE", "服务器运行时或网络未就绪")
        all_rows = (
            await self.db.execute(
                select(AgentDeployment, AgentInstance)
                .select_from(AgentInstance)
                .outerjoin(AgentDeployment, AgentInstance.id == AgentDeployment.instance_id)
                .where(
                    or_(
                        AgentDeployment.server_id == server.id, AgentInstance.server_id == server.id
                    )
                )
            )
        ).all()
        rows = [row for row in all_rows if row[1].id != exclude_instance_id]
        if any(instance.cpus is None or instance.mem_limit_mb is None for _, instance in rows):
            raise ApiError(
                409,
                "UNACCOUNTED_LEGACY_RESOURCES",
                "服务器有旧的无限资源实例：先核对并设置有限资源，再部署",
            )
        for deployment, _ in rows:
            if deployment is None or (
                deployment.runtime_installed is not False
                and not _has_resource_commitment(deployment)
            ):
                raise _legacy_reconciliation()
        cpu = memory = disk = pending_memory = pending_disk = 0.0
        observed = datetime.fromisoformat(snap["sampled_at"])
        for deployment, instance in rows:
            receipt = (deployment.selection or {}) if deployment is not None else {}
            # Do not free a previously accepted promise because an owner
            # changed desired limits without redeploying its actual runtime.
            assert instance.cpus is not None and instance.mem_limit_mb is not None  # noqa: S101
            allocated_cpu = max(instance.cpus, float(receipt.get("cpus", 0)))
            allocated_memory = max(instance.mem_limit_mb, int(receipt.get("memory_mb", 0)))
            allocated_disk = float(receipt.get("disk_gb", disk_gb))
            cpu += allocated_cpu
            memory += allocated_memory
            disk += allocated_disk
            if (
                deployment is None
                or deployment.runtime_installed is not True
                or deployment.installed_at is None
                or deployment.installed_at > observed
            ):
                pending_memory += allocated_memory
                pending_disk += allocated_disk
        return Candidate(
            server_id=server.id,
            cpus=float(snap["cores"]),
            memory_mb=snap["mem_total"] / (1 << 20),
            disk_gb=min(snap["root_total"], snap["data_total"]) / (1 << 30),
            available_memory_mb=snap["mem_available"] / (1 << 20),
            disk_free_gb=min(snap["root_free"], snap["data_free"]) / (1 << 30),
            cpu_load=max(snap["cpu_percent"] / 100, snap["load1"] / snap["cores"]),
            committed_cpus=cpu,
            committed_memory_mb=memory,
            committed_disk_gb=disk,
            pending_memory_mb=pending_memory,
            pending_disk_gb=pending_disk,
            observed_at=observed,
        )

    async def refresh_resource_snapshots(
        self, access: WorkspaceAccess, server_ids: list[uuid.UUID], *, force: bool = False
    ) -> dict[uuid.UUID, str]:
        """Network reads happen before admission, outside its row-lock transaction.

        Read scoped targets, detach immutable credential/identity inputs, release
        the read transaction, probe at most four hosts concurrently, then apply
        newer results under short row locks. A failed probe clears no reservation
        and never installs an all-zero fallback.
        """
        pending: list[tuple[uuid.UUID, AgentSshTarget, tuple[object, ...], str]] = []
        errors: dict[uuid.UUID, str] = {}
        for server_id in sorted(server_ids, key=lambda value: value.int):
            server = await self._server(access, server_id, lock=True)
            if not force and agent_server_resources.is_fresh(
                server.resource_snapshot or {},
                _now(),
                self.settings.agent_placement_metrics_max_age_seconds,
            ):
                continue
            server.resource_snapshot = {
                **(server.resource_snapshot or {}),
                "probe_attempted_at": _now().isoformat(),
            }
            target = await self._target(server)
            if target is None:
                server.resource_snapshot = {
                    **server.resource_snapshot,
                    "probe_error": "unavailable",
                }
                errors[server_id] = "TARGET_UNAVAILABLE"
                continue
            try:
                self._require_password(target)
                if target.host_key_confirmed_at is None or target.prepared_at is None:
                    errors[server_id] = "TARGET_UNAVAILABLE"
                    continue
            except ApiError:
                errors[server_id] = "TARGET_UNAVAILABLE"
                continue
            identity = _connection_snapshot(target)
            self.db.expunge(target)
            pending.append(
                (server_id, target, identity, server.resource_snapshot["probe_attempted_at"])
            )
        # expire_on_commit=False preserves the request's WorkspaceAccess; rollback
        # would expire those ORM objects before the business transaction starts.
        await self.db.commit()
        if not pending:
            return errors
        box = self._require_box()
        semaphore = asyncio.Semaphore(4)

        async def sample(target: AgentSshTarget) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    return await agent_server_resources.probe_resources(target, box)
                except (ValueError, ServerCredentialError):
                    return None

        values = await asyncio.gather(*(sample(target) for _, target, _, _ in pending))
        for (server_id, _, identity, attempted_at), snapshot in sorted(
            zip(pending, values, strict=True), key=lambda item: item[0][0].int
        ):
            server = await self._server(access, server_id, lock=True)
            target = await self._target(server, lock=True)
            if snapshot is None or target is None or _connection_snapshot(target) != identity:
                if (server.resource_snapshot or {}).get("probe_attempted_at") != attempted_at:
                    continue
                server.resource_snapshot = {
                    **(server.resource_snapshot or {}),
                    "probe_error": "unavailable",
                }
                errors[server_id] = "METRICS_STALE"
                continue
            if agent_server_resources.newer_snapshot(server.resource_snapshot or {}, snapshot):
                server.resource_snapshot = {
                    **snapshot,
                    "probe_attempted_at": snapshot["sampled_at"],
                }
        await self.db.commit()
        return errors

    async def refresh_resources(
        self, access: WorkspaceAccess, server_id: uuid.UUID, *, client_ip: str
    ) -> AgentServerDetail:
        await self._check_probe_rate(access)
        errors = await self.refresh_resource_snapshots(access, [server_id], force=True)
        if errors:
            raise ApiError(503, "METRICS_STALE", "独立资源探测失败；未更新容量数据")
        await self.audit.record(
            "agents.server.resources_refreshed",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_server",
            resource_id=server_id,
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self.detail(access, server_id)

    # ---------------------------------------------------------------- 实例

    def capacity_warning(self, server: AgentServer) -> str | None:
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

    async def _latest_prepare(self, target: AgentSshTarget) -> AgentSshJob | None:
        return await self.db.scalar(
            select(AgentSshJob)
            .where(AgentSshJob.target_id == target.id, AgentSshJob.kind == "prepare")
            .order_by(AgentSshJob.started_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    async def deployable_target(self, server: AgentServer) -> AgentSshTarget:
        """部署前的检查（调用方已锁住服务器）：锁住 SSH 记录，不能部署就抛出第一个原因。

        与 GET 返回的 deploy_block_reason 同一套规则（`_block_reason`），只是这里会把本进程里已经
        不在运行的 running 任务标为中断（`_busy`）。连接状态 unknown 不阻止：任务里仍会真实检查。
        """
        target = await self._target(server, lock=True)
        if target is None:
            raise _block_error("not_ssh")
        busy = await self._busy(target)
        initialization = _initialization(target, await self._latest_prepare(target))
        readable = password_readable(self._box_or_none(), target.login_password_ref)
        block = _block_reason(target, initialization, busy, readable)
        if block is not None:
            raise _block_error(block)
        return target

    def check_ingress(self, instance: AgentInstance) -> None:
        """部署到服务器走外网模式（ADR 第 3.1 节），外网接入入口要已经配置。"""
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

    async def accept_deployment(
        self,
        access: WorkspaceAccess,
        instance: AgentInstance,
        server: AgentServer,
        *,
        client_ip: str,
    ) -> tuple[AgentSshJob, int]:
        """接纳一次部署：检查前提，建部署记录（没有时）与部署任务并在进程内登记。

        不提交、不启动：调用方（旧的部署接口、一次点击新建并部署）在同一个事务里一处提交
        （`commit_job`），提交之后再 `launch`。前提不满足时抛出接口错误，什么都不写。
        调用方已锁住实例（新建的实例不用锁）与服务器。返回任务与接纳时的凭证版本。
        """
        self.ready_to_run()
        if instance.status == "disabled":
            raise _disabled()
        self.check_ingress(instance)
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
        target = await self.deployable_target(server)
        if deployment is None:
            deployment = AgentDeployment(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                server_id=server.id,
                created_by=access.user_id,
            )
            self.db.add(deployment)
        revision = target.credential_revision
        job = await self._new_job(
            access,
            target,
            kind="install",
            instance_id=instance.id,
            metadata={
                "host": target.host,
                "port": target.port,
                "username": target.username,
                "host_key_fingerprint": target.host_key_fingerprint,
            },
            client_ip=client_ip,
        )
        return job, revision

    async def deploy(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentDeployIn,
        *,
        client_ip: str,
    ) -> AgentDeploymentState:
        """把已有的实例部署（或重新部署、失败后继续）到一台 SSH 服务器。

        只替换这个实例自己的目录与 Compose 项目；用服务器上保存的登录密码（body.password 已弃用、
        忽略、不回显）；服务器要已初始化、可以部署（与一次点击新建并部署同一套接纳逻辑）。
        """
        self.ready_to_run()
        instance = await self._instance(access, instance_id)
        if body.version != instance.version:
            raise _version_conflict(instance.version)
        current = await self.db.scalar(
            select(AgentDeployment).where(AgentDeployment.instance_id == instance_id)
        )
        if current is not None and current.server_id != body.server_id:
            raise ApiError(
                409,
                "AGENT_DEPLOYED_ELSEWHERE",
                "这个实例已经部署在另一台服务器上：先卸载",
                details={"server_id": str(current.server_id)},
            )
        if (
            current is not None
            and current.runtime_installed is not False
            and not _has_resource_commitment(current)
        ):
            raise _legacy_reconciliation()
        if instance.cpus is None or instance.mem_limit_mb is None:
            raise ApiError(
                409,
                "UNACCOUNTED_LEGACY_RESOURCES",
                "旧实例的资源不受限：请核对并设置有限 CPU/内存后重新部署；不会自动修改旧实例",
            )
        await self.refresh_resource_snapshots(access, [body.server_id])
        instance = await self._instance(access, instance_id, lock=True)
        if body.version != instance.version:
            raise _version_conflict(instance.version)
        server = await self._server(access, body.server_id, lock=True)
        await self.deployable_target(server)
        candidate = await self.placement_candidate(
            server, disk_gb=self.settings.agent_placement_disk_gb, exclude_instance_id=instance.id
        )
        assert instance.cpus is not None and instance.mem_limit_mb is not None  # noqa: S101
        try:
            rank_candidates(
                [candidate],
                ResourceRequest(
                    instance.cpus, instance.mem_limit_mb, self.settings.agent_placement_disk_gb
                ),
                Placement(mode="specified", server_id=server.id),
                _now(),
            )
        except PlacementError as error:
            raise ApiError(
                409, error.code, error.message, details={"reasons": error.reasons}
            ) from error
        job, revision = await self.accept_deployment(access, instance, server, client_ip=client_ip)
        deployment = await self.db.scalar(
            select(AgentDeployment).where(AgentDeployment.instance_id == instance_id)
        )
        assert deployment is not None  # noqa: S101 - accept_deployment creates it
        previous = deployment.selection or {}
        deployment.selection = {
            "requested_mode": "specified",
            "requested_server_id": str(server.id),
            **(deployment.selection or {}),
            "actual_server_id": str(server.id),
            "reservation_id": str(deployment.id),
            "metrics_at": server.resource_snapshot["sampled_at"],
            # Acceptance/failure never proves the previous runtime disappeared.
            # Keep the larger promise until authoritative runtime observation.
            "cpus": max(instance.cpus, float(previous.get("cpus", 0))),
            "memory_mb": max(instance.mem_limit_mb, int(previous.get("memory_mb", 0))),
            "disk_gb": max(
                self.settings.agent_placement_disk_gb, float(previous.get("disk_gb", 0))
            ),
            "desired_cpus": instance.cpus,
            "desired_memory_mb": instance.mem_limit_mb,
            "desired_disk_gb": self.settings.agent_placement_disk_gb,
            "status": "reserved",
            "reason_code": None,
        }
        warning = self.capacity_warning(server)
        await self.commit_job(job)
        self.launch(job, revision=revision, client_ip=client_ip)
        await self.db.refresh(instance)
        return await self.deployment_state_of(instance, warning=warning)

    async def undeploy(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentUndeployIn,
        *,
        client_ip: str,
    ) -> AgentDeploymentState:
        """卸载这个实例（T-0179 起用保存的登录密码）：停止它的容器、删掉它的定时任务，
        可选删除它的目录。同一台服务器上的其他实例不受影响。"""
        self.ready_to_run()
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
        self._require_password(target)
        if await self._busy(target):
            raise _job_running()
        revision = target.credential_revision
        job = await self._new_job(
            access,
            target,
            kind="uninstall",
            instance_id=instance.id,
            metadata={"host": target.host, "port": target.port, "remove_files": body.remove_files},
            client_ip=client_ip,
        )
        await self.commit_job(job)
        self.launch(job, revision=revision, client_ip=client_ip, remove_files=body.remove_files)
        await self.db.refresh(instance)
        return await self.deployment_state_of(instance)

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
        return await self.deployment_state_of(instance)
