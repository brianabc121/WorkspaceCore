"""运行服务器的接入（T-0136，docs/v2/RUNNER_PLAN.md 第 9 节）。

- 添加 / 重新签发 / 移除运行服务器：凭证 `mwr_` 开头，工作台只存 SHA-256；
  外网另签客户端证书（实例的内部 CA）。
- runner-agent 的长轮询：更新容量，领走待执行的指令（`FOR UPDATE SKIP LOCKED`，标为 sent）；
  需要密钥的指令在**下发这一刻**才解密并放进响应（不写进 runner_commands）。
- 结果回报交给 `Runner.apply_result`；对账（每分钟）比对容器与数据卷：记录里有、实际没有 → 丢失；
  实际有、记录里没有 → 返回给 runner-agent 先停止（24 小时后它自己删除）。没有工作台标签的不碰。
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import notifications, runner_metrics
from app.domain import runner_specs as specs
from app.domain.agent_ca import ensure_ca, issue_certificate
from app.domain.agents import agent_secret_context, normalize_fingerprint
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.ratelimit import FailureLimiter
from app.domain.runner import ONLINE_SECONDS, Runner
from app.models import (
    AgentInstance,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    RunnerCommand,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerSnapshot,
    RunnerSshJob,
)
from app.schemas.runner import (
    RunnerCommandOut,
    RunnerCommandResult,
    RunnerHostCreate,
    RunnerHostOut,
    RunnerPoll,
    RunnerReport,
    RunnerReportResult,
    RunnerReportSample,
)

logger = logging.getLogger("app.runner")

TOKEN_PREFIX = "mwr_"  # noqa: S105 -- 令牌格式的前缀，不是密钥
TOKEN_PREFIX_SHOWN = 12
POLL_BATCH = 10
AUTH_FAILURE_LIMIT = 10
AUTH_FAILURE_WINDOW_SECONDS = 15 * 60
# 带密钥下发的指令（容器启动时写进 /run/secrets）
SECRET_KINDS = frozenset({"create_env", "start_env", "exec", "restore"})
# 记录里应当有容器的状态（creating 可能还没建好）
EXPECT_CONTAINER = ("running", "stopped")
EGRESS_KEEP = 50


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    """mwr_ 加 43 个 base64url 字符（32 字节随机数）。"""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def unauthorized() -> ApiError:
    return ApiError(
        401, "RUNNER_UNAUTHORIZED", "运行服务器认证失败", headers={"WWW-Authenticate": "Bearer"}
    )


def accept_report_sample(host: RunnerHost, body: RunnerReport, now: datetime) -> datetime | None:
    """Host-bound single-use epoch: receipt time cannot stand in for sample time."""
    if body.sample_id is None:
        return None
    metadata = dict(host.runtime_metadata or {})
    try:
        started = datetime.fromisoformat(metadata["report_sample_started_at"])
        if (
            str(body.sample_id) != metadata["report_sample_id"]
            or not 0 <= (now - started).total_seconds() <= 90
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ApiError(
            409, "REPORT_SAMPLE_STALE", "采样票据过期、已使用或不属于此服务器"
        ) from error
    metadata.pop("report_sample_id", None)
    metadata.pop("report_sample_started_at", None)
    host.runtime_metadata = metadata
    return started


def host_out(host: RunnerHost) -> RunnerHostOut:
    status = host.status
    if status == "online" and (
        host.last_seen_at is None
        or host.last_seen_at < specs.now() - timedelta(seconds=ONLINE_SECONDS)
    ):
        status = "offline"
    return RunnerHostOut(
        backend=host.backend or "docker",
        node_name=host.node_name,
        runtime_metadata=dict(host.runtime_metadata or {}),
        id=host.id,
        name=host.name,
        status=status,
        ingress=host.ingress,
        token_prefix=host.token_prefix,
        cpus=host.cpus,
        mem_bytes=host.mem_bytes,
        disk_bytes=host.disk_bytes,
        disk_free_bytes=host.disk_free_bytes,
        kvm=host.kvm,
        runtimes=list(host.runtimes or []),
        docker_version=host.docker_version,
        agent_version=host.agent_version,
        last_seen_at=host.last_seen_at,
        report=dict(host.report or {}),
        policy=specs.policy_for(host.policy),
        version=host.version,
        created_at=host.created_at,
    )


async def resolve_env_secrets(
    db: AsyncSession,
    settings: Settings,
    env: RunnerEnvironment,
    *,
    record: bool,
    client_ip: str | None = None,
) -> dict[str, str]:
    """环境要注入的密钥：按创建它的实例在密钥库里的授权解密；授权已撤销的不再给。

    record=True 时每个密钥写一条取用记录（与实例自己取密钥用同一张表，来源 IP 是运行服务器）。
    """
    names = [str(n) for n in env.spec.get("secrets") or []]
    if not names or env.owner_instance_id is None:
        return {}
    instance = await db.get(AgentInstance, env.owner_instance_id)
    if instance is None:
        return {}
    rows = (
        await db.execute(
            select(AgentSecretGrant.ref_id, AgentSecret, AgentSecretVersion.ciphertext_ref)
            .join(AgentSecret, AgentSecret.id == AgentSecretGrant.secret_id)
            .join(
                AgentSecretVersion,
                (AgentSecretVersion.secret_id == AgentSecret.id)
                & (AgentSecretVersion.value_version == AgentSecret.current_version),
            )
            .where(AgentSecretGrant.instance_id == instance.id, AgentSecretGrant.ref_id.in_(names))
        )
    ).all()
    found = {ref_id: (secret, ref) for ref_id, secret, ref in rows}
    try:
        box = CredentialBox.from_settings(settings) if found else None
    except CredentialError:
        box = None
    values: dict[str, str] = {}
    for name in names:
        secret, ref = found.get(name, (None, None))
        value: str | None = None
        if secret is not None and ref and box is not None:
            try:
                value = box.resolve(
                    ref, context=agent_secret_context(secret.id, secret.current_version)
                ).get_secret_value()
            except CredentialError:
                value = None
        if value is not None:
            values[name] = value
        if record:
            db.add(
                AgentSecretAccess(
                    workspace_id=instance.workspace_id,
                    instance_id=instance.id,
                    instance_name=instance.name,
                    ref_id=name,
                    secret_id=secret.id if value is not None and secret is not None else None,
                    value_version=secret.current_version
                    if value is not None and secret is not None
                    else None,
                    result="ok" if value is not None else "not_found",
                    source_ip=client_ip or "runner",
                )
            )
    return values


class RunnerHosts:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)
        self.runner = Runner(db, settings, redis)

    # ---------------------------------------------------------------- 管理员

    async def get(self, workspace_id: uuid.UUID) -> RunnerHost | None:
        return await self.runner.host(workspace_id)

    async def create(
        self, workspace_id: uuid.UUID, body: RunnerHostCreate, actor_id: uuid.UUID | None
    ) -> tuple[RunnerHost, str]:
        token = new_token()
        host = RunnerHost(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=body.name,
            ingress=body.ingress,
            backend=body.backend,
            node_name=body.node_name,
            runtime_metadata=body.runtime_metadata,
            token_hash=hash_token(token),
            token_prefix=token[:TOKEN_PREFIX_SHOWN],
            created_by=actor_id,
        )
        self.db.add(host)
        await self.db.flush()
        await self.audit.record(
            "runner.host_created",
            workspace_ids=[workspace_id],
            actor_id=actor_id,
            resource_type="runner_host",
            resource_id=host.id,
            metadata={"name": body.name, "ingress": body.ingress},
        )
        return host, token

    async def reissue(
        self, host: RunnerHost, actor_id: uuid.UUID | None
    ) -> tuple[str, tuple[str, str, str] | None]:
        """换新凭证（旧的立即失效）；外网入口再签一张客户端证书。返回 (令牌, (证书, 私钥, CA))。"""
        token = new_token()
        host.token_hash = hash_token(token)
        host.token_prefix = token[:TOKEN_PREFIX_SHOWN]
        cert: tuple[str, str, str] | None = None
        if host.ingress == "public":
            try:
                box = CredentialBox.from_settings(self.settings)
            except CredentialError as error:
                raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥") from error
            ca = await ensure_ca(self.db, box)
            issued = issue_certificate(
                ca,
                box,
                common_name=f"runner_{host.id.hex}",
                uri=f"urn:myworkspace:runner-host:{host.id}",
            )
            host.cert_fingerprint = issued.fingerprint
            cert = (issued.cert_pem, issued.key_pem, issued.ca_pem)
        host.version += 1
        await self.audit.record(
            "runner.host_reissued",
            workspace_ids=[host.workspace_id],
            actor_id=actor_id,
            resource_type="runner_host",
            resource_id=host.id,
            metadata={"token_prefix": host.token_prefix, "client_cert": cert is not None},
        )
        return token, cert

    async def server_ca(self) -> str | None:
        """接入入口用内部 CA 签发的证书时，运行服务器要信任这个 CA。"""
        try:
            box = CredentialBox.from_settings(self.settings)
        except CredentialError:
            return None
        ca = await ensure_ca(self.db, box)
        return ca.cert_pem

    async def remove(self, host: RunnerHost, actor_id: uuid.UUID | None, version: int) -> None:
        await self.db.refresh(host, with_for_update=True)
        if host.version != version:
            raise ApiError(409, "VERSION_CONFLICT", "容器服务器已被修改，请刷新后重试")
        # Job creation takes this same host lock before its target lock. Keep
        # the physical registration while SSH can still mutate the machine.
        running_job = await self.db.scalar(
            select(RunnerSshJob.id)
            .where(RunnerSshJob.host_id == host.id, RunnerSshJob.status == "running")
            .limit(1)
        )
        if running_job is not None:
            raise ApiError(
                409, "RUNNER_SSH_JOB_RUNNING", "服务器正在部署或卸载，请等待 SSH 任务结束后再移除"
            )
        live = await self.db.scalar(
            select(RunnerEnvironment.id)
            .where(
                RunnerEnvironment.host_id == host.id,
                RunnerEnvironment.state != "deleted",
            )
            .limit(1)
        )
        snapshot = await self.db.scalar(
            select(RunnerSnapshot.id)
            .join(RunnerEnvironment, RunnerSnapshot.environment_id == RunnerEnvironment.id)
            .where(RunnerEnvironment.host_id == host.id, RunnerSnapshot.status != "deleted")
            .limit(1)
        )
        if live is not None or snapshot is not None:
            raise ApiError(
                409,
                "RUNNER_HAS_ENVIRONMENTS",
                "还有保有或结果未知的环境、快照；完成确认清理后再移除容器服务器",
            )
        await self.audit.record(
            "runner.host_removed",
            workspace_ids=[host.workspace_id],
            actor_id=actor_id,
            resource_type="runner_host",
            resource_id=host.id,
            metadata={"name": host.name},
        )
        await self.db.delete(host)

    async def set_policy(
        self, host: RunnerHost, policy: dict[str, float], version: int, actor_id: uuid.UUID | None
    ) -> RunnerHost:
        if host.version != version:
            raise ApiError(409, "VERSION_CONFLICT", "设置已被修改，请刷新后重试")
        unknown = [k for k in policy if k not in specs.DEFAULT_POLICY]
        if unknown:
            raise ApiError(422, "VALIDATION_FAILED", f"不认识的设置：{', '.join(unknown[:5])}")
        for key, value in policy.items():
            if value < 0 or value > 100000:
                raise ApiError(422, "VALIDATION_FAILED", f"{key} 超出范围")
        host.policy = {k: float(v) for k, v in policy.items()}
        host.version += 1
        await self.audit.record(
            "runner.policy_updated",
            workspace_ids=[host.workspace_id],
            actor_id=actor_id,
            resource_type="runner_host",
            resource_id=host.id,
            metadata={"policy": host.policy},
        )
        return host

    # ---------------------------------------------------------------- runner-agent

    async def authenticate(
        self,
        token: str | None,
        *,
        ingress: str | None,
        cert_fingerprint: str | None,
        client_ip: str,
    ) -> RunnerHost:
        """凭证 + 入口一致 + （外网）客户端证书指纹一致。任何不符都是同一个 401。"""
        limiter = (
            FailureLimiter(self.redis, AUTH_FAILURE_WINDOW_SECONDS)
            if self.redis is not None and ingress == "public"
            else None
        )
        key = f"runner_auth_fail:{client_ip}"
        if limiter is not None:
            retry = await limiter.retry_after({key: AUTH_FAILURE_LIMIT})
            if retry is not None:
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "请求过于频繁，请稍后再试",
                    headers={"Retry-After": str(retry)},
                )
        host = await self._check(token, ingress, cert_fingerprint)
        if host is None:
            if limiter is not None:
                await limiter.record_failure([key])
            raise unauthorized()
        return host

    async def _check(
        self, token: str | None, ingress: str | None, cert_fingerprint: str | None
    ) -> RunnerHost | None:
        if not token or not token.startswith(TOKEN_PREFIX) or ingress not in ("intranet", "public"):
            return None
        host: RunnerHost | None = await self.db.scalar(
            select(RunnerHost).where(RunnerHost.token_hash == hash_token(token))
        )
        if host is None or host.status == "disabled" or host.ingress != ingress:
            return None
        if ingress == "public":
            if not cert_fingerprint or not host.cert_fingerprint:
                return None
            if normalize_fingerprint(cert_fingerprint) != host.cert_fingerprint:
                return None
        return host

    async def check_in(self, host: RunnerHost, body: RunnerPoll) -> None:
        # Serialize telemetry invalidation with report sampling and admission;
        # otherwise an older authenticated row could overwrite a newer report.
        await self.db.refresh(host, with_for_update={"key_share": True})
        was = host.status
        host.agent_version = body.agent_version
        host.docker_version = body.docker_version
        host.runtimes = list(body.runtimes)
        host.kvm = body.kvm
        if all(
            value is not None
            for value in (body.cpus, body.mem_bytes, body.disk_bytes, body.disk_free_bytes)
        ):
            host.cpus = body.cpus
            host.mem_bytes = body.mem_bytes
            host.disk_bytes = body.disk_bytes
            host.disk_free_bytes = body.disk_free_bytes
        else:
            # Keep prior readings for display, but never renew capacity from
            # unknown telemetry. A subsequent poll cannot restore freshness:
            # only another complete, identity-verified report may do that.
            host.report = {
                **(host.report or {}),
                "capacity_sampled_at": None,
                "capacity_error": "METRICS_UNKNOWN",
            }
        host.last_seen_at = specs.now()
        if host.status in ("pending", "offline"):
            host.status = "online"
        if was == "offline":
            await notifications.notify_admins(
                self.db,
                host.workspace_id,
                type_="runner.host_online",
                severity="info",
                title=f"容器服务器「{host.name}」恢复在线",
                body="已重新连上工作台",
                link="/runner",
                key=f"{host.id}:online:{specs.now():%Y%m%d%H%M}",
            )

    async def take(self, host: RunnerHost, *, client_ip: str) -> list[RunnerCommandOut]:
        """领走待执行的指令（最多 10 条），标为 sent；带密钥的指令这时才解密。"""
        rows = await self.db.scalars(
            select(RunnerCommand)
            .where(RunnerCommand.host_id == host.id, RunnerCommand.status == "pending")
            # created_at 相同（同一个事务写的）时按写入顺序：重建（create_env）一定排在随后的
            # exec / 读写文件之前，runner-agent 才会让后者等重建做完（T-0213）
            .order_by(RunnerCommand.created_at, RunnerCommand.seq)
            .limit(POLL_BATCH)
            .with_for_update(skip_locked=True)
        )
        out: list[RunnerCommandOut] = []
        now = specs.now()
        for command in rows:
            command.status = "sent"
            command.sent_at = now
            command.attempts += 1
            payload = dict(command.payload)
            if command.kind in SECRET_KINDS and command.environment_id is not None:
                env = await self.db.get(RunnerEnvironment, command.environment_id)
                if env is not None and env.spec.get("secrets"):
                    payload["secret_values"] = await resolve_env_secrets(
                        self.db, self.settings, env, record=True, client_ip=client_ip
                    )
            if command.kind == "exec" and command.run_id is not None:
                await self._mark_run_started(command.run_id)
            out.append(RunnerCommandOut(id=command.id, kind=command.kind, payload=payload))
        return out

    async def _mark_run_started(self, run_id: uuid.UUID) -> None:
        run = await self.db.get(RunnerRun, run_id)
        if run is not None and run.status == "queued":
            run.status = "running"
            run.started_at = specs.now()

    async def result(
        self, host: RunnerHost, command_id: uuid.UUID, body: RunnerCommandResult
    ) -> bool:
        """回报结果；同一条指令重复回报只认第一次。返回是否采用。"""
        host = await self._lock_report_host(host)
        command = await self.db.scalar(
            select(RunnerCommand)
            .where(RunnerCommand.id == command_id, RunnerCommand.host_id == host.id)
            .with_for_update()
        )
        if command is None:
            raise ApiError(404, "NOT_FOUND", "没有这条指令")
        if command.status not in ("pending", "sent", "unknown"):
            return False
        await self.runner.apply_result(command, body.status, body.result, body.error)
        host.last_seen_at = specs.now()
        return True

    async def _lock_report_host(self, host: RunnerHost) -> RunnerHost:
        # Non-key telemetry writes serialize with admission and deletion while
        # allowing foreign-key checks from lifecycle commands.
        current = await self.db.scalar(
            select(RunnerHost)
            .where(
                RunnerHost.id == host.id,
                RunnerHost.token_hash == host.token_hash,
                RunnerHost.status != "disabled",
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if current is None:
            raise unauthorized()
        return current

    async def begin_report(self, host: RunnerHost) -> RunnerReportSample:
        host = await self._lock_report_host(host)
        sample_id = uuid.uuid4()
        host.runtime_metadata = {
            **(host.runtime_metadata or {}),
            "report_sample_id": str(sample_id),
            "report_sample_started_at": specs.now().isoformat(),
        }
        return RunnerReportSample(sample_id=sample_id)

    async def report(self, host: RunnerHost, body: RunnerReport) -> RunnerReportResult:
        host = await self._lock_report_host(host)
        received = specs.now()  # 这份报告里各项读数的“平台收到时间”（不信 agent 的时钟）
        sampled = accept_report_sample(host, body, received)
        from app.domain.physical_hosts import claim_physical_host

        placement_error = None
        if sampled is None or body.physical_host_id is None:
            placement_error = "PHYSICAL_IDENTITY_REQUIRED"
        elif not await claim_physical_host(
            self.db,
            body.physical_host_id,
            workspace_id=host.workspace_id,
            role="runner",
            server_id=host.id,
        ):
            placement_error = "PHYSICAL_HOST_CONFLICT"
        if placement_error is not None:
            sampled = None
        host.last_seen_at = received
        host.report = {
            "containers": len(body.containers),
            "volumes": len(body.volumes),
            "images": len(body.images),
            "unlabeled_containers": body.unlabeled_containers,
            "mcp_manager_containers": body.mcp_manager_containers,
            "mem_bytes": body.mem_bytes,
            "mem_free_bytes": body.mem_free_bytes,
            "disk_bytes": body.disk_bytes,
            "disk_free_bytes": body.disk_free_bytes,
            "images_bytes": body.images_bytes,
            "volumes_bytes": body.volumes_bytes,
            "containers_bytes": body.containers_bytes,
            "load": body.load,
            "at": specs.now().isoformat(),
            "capacity_sampled_at": sampled.isoformat() if sampled else None,
            "physical_host_id": body.physical_host_id,
            "physical_identity_verified": placement_error is None,
            "placement_error": placement_error,
        }
        host.disk_free_bytes = body.disk_free_bytes or host.disk_free_bytes
        if placement_error is not None:
            # This can be a duplicate registration of another pool's machine.
            # Telemetry is useful, but never order its containers stopped as
            # "stray" or alter allocation state without a verified identity.
            return RunnerReportResult(lost=[], stray=[])
        if host.backend == "kubernetes":
            # Capacity inventory has no allocation/generation/Pod UID receipt.
            # Only the lifecycle controller can change Kubernetes runtime binding.
            return RunnerReportResult(lost=[], stray=[])
        envs = {
            e.id: e
            for e in await self.db.scalars(
                select(RunnerEnvironment).where(
                    RunnerEnvironment.host_id == host.id, RunnerEnvironment.state != "deleted"
                )
            )
        }
        containers = {c.env_id: c for c in body.containers}
        volumes = {v.env_id: v for v in body.volumes}
        # 在途的建、删指令：对账时先不判定
        busy = set(
            await self.db.scalars(
                select(RunnerCommand.environment_id).where(
                    RunnerCommand.host_id == host.id,
                    RunnerCommand.status.in_(("pending", "sent", "unknown")),
                    RunnerCommand.environment_id.is_not(None),
                )
            )
        )
        lost: list[uuid.UUID] = []
        for env in envs.values():
            container = containers.get(env.id)
            reserved = (env.runtime_metadata or {}).get("compute_reserved_at")
            can_reconcile = reserved is None or (
                sampled is not None and sampled.isoformat() > str(reserved)
            )
            # 资源读数（T-0185）：只写本主机的环境；与状态对账互不影响
            runner_metrics.apply_container_reading(env, container, received)
            if container is not None and can_reconcile:
                running = container.state == "running"
                stopped = container.state in ("exited", "created")
                if env.id not in busy and env.state in ("creating", "failed", "running", "stopped"):
                    if running:
                        env.state = "running"
                    elif stopped:
                        env.state = "stopped"
                        env.stopped_at = env.stopped_at or specs.now()
                    else:
                        env.state = "failed"
                    env.state_reason = (
                        "已通过服务器报告确认容器存在"
                        if running or stopped
                        else f"容器状态尚未确认停止（{container.state}）；保留计算预约"
                    )
                if env.id not in busy:
                    env.runtime_metadata = {
                        **(env.runtime_metadata or {}),
                        "compute_stopped": stopped,
                        "compute_observed_at": sampled.isoformat() if sampled else None,
                        "compute_memory_working_set_bytes": (
                            container.memory_working_set_bytes if sampled else None
                        ),
                    }
            if env.id in volumes:
                runner_metrics.apply_volume_reading(env, volumes[env.id].bytes, received)
            if not can_reconcile or env.id in busy or env.state not in EXPECT_CONTAINER:
                continue
            if container is None:
                lost.append(env.id)
                env.state = "failed"
                env.state_reason = "容器服务器上找不到这个容器（丢失）"
                await notifications.notify_admins(
                    self.db,
                    host.workspace_id,
                    type_="runner.environment_lost",
                    severity="warning",
                    title="容器服务器上的环境丢失",
                    body=f"{env.purpose}：记录里有，容器服务器上找不到容器",
                    link=f"/runner?environment_id={env.id}",
                    key=f"{env.id}:lost",
                )
        stray = [
            c.name
            for c in body.containers
            if c.env_id not in envs or envs[c.env_id].state == "deleted"
        ]
        if stray:
            await notifications.notify_admins(
                self.db,
                host.workspace_id,
                type_="runner.environment_stray",
                severity="warning",
                title="容器服务器上有记录外的容器",
                body=f"{len(stray)} 个带工作台标签、但记录里没有的容器：已停止，24 小时后删除",
                link="/runner",
                key=f"{host.id}:stray:{specs.now():%Y%m%d}",
            )
        for env_id, items in body.egress.items():
            try:
                target = envs.get(uuid.UUID(env_id))
            except ValueError:
                continue
            if target is not None:
                target.egress = _merge_egress(target.egress, items)
        return RunnerReportResult(lost=lost, stray=stray)


def _merge_egress(current: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    hosts: dict[str, dict[str, Any]] = dict((current or {}).get("hosts") or {})
    for item in items:
        key = f"{item.host}:{item.port}"
        entry = dict(hosts.get(key) or {"count": 0, "bytes": 0, "denied": 0})
        entry["count"] = int(entry.get("count", 0)) + item.count
        entry["bytes"] = int(entry.get("bytes", 0)) + item.bytes
        if not item.allowed:
            entry["denied"] = int(entry.get("denied", 0)) + item.count
        entry["last"] = specs.now().isoformat(timespec="seconds")
        hosts[key] = entry
    if len(hosts) > EGRESS_KEEP:
        keep = sorted(hosts.items(), key=lambda kv: str(kv[1].get("last", "")), reverse=True)
        hosts = dict(keep[:EGRESS_KEEP])
    return {"hosts": hosts}
