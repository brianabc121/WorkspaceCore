"""一次点击新建并部署 OpenClaw 智能体，以及部署请求的收据（T-0180）。

依据 docs/v2/agent-servers-redesign/ 的接口与迁移约定第 5、7 节：

- `POST /agents/deployments`：request_id 同时是 Idempotency-Key。先用现有的幂等记录
  （`app.domain.idempotency.begin`）登记“处理中”并提交，让并发的同一个请求能看到它；
  然后**一个业务事务**里：锁服务器、检查前提（不满足时什么都不建）、登记实例
  （`AgentService.create_instance_record`）、设资源上限（`apply_limits`）、接纳部署
  （`AgentServerService.accept_deployment`：部署记录 + 任务 + 进程内登记），
  并把幂等记录改成“已接纳”、
  只在 context 里记 instance_id / job_id 等安全元数据——一处提交，提交之后才启动后台任务、
  注册 OpenIM（外部调用不在数据库原子性承诺里，失败由既有的重试机制补）。
- 提交前出错（进程崩溃、数据库错误）：业务行随事务回滚，没有半个实例；幂等记录停在“处理中”，
  超过 `STALE_PROCESSING` 后收据是 interrupted，同一个请求号重发会重新执行一次
  （之前确实什么都没建）。
- 提交之后响应丢失：收据（或同一个请求号重发）按 context 找回同一个实例与任务，
  不会再建、也不会重启任务。
- 前提不满足（版本过期、没密码、没初始化、同机有任务……）：幂等记录记为失败，重发得到同样的错误，
  收据是 failed；之后的任务失败不影响收据（accepted），实例保留，用现有的部署接口继续。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import agent_server_resources, idempotency
from app.domain.agent_model_bindings import AgentModelBindingService
from app.domain.agent_servers import AgentServerService
from app.domain.agent_ssh import _version_conflict
from app.domain.agents import AgentService
from app.domain.placement import PlacementError, ResourceRequest, rank_candidates
from app.domain.policy import WorkspaceAccess
from app.integrations.openim import OpenIMClient
from app.models import (
    AgentDeployment,
    AgentInstance,
    AgentServer,
    AgentSettings,
    IdempotencyRequest,
)
from app.repositories import integrations as integration_repo
from app.schemas.agents import (
    AgentDeploymentCreateIn,
    AgentDeploymentCreateOut,
    AgentDeploymentRequestOut,
    AgentInstanceCreate,
)

OP = "agents.deployment.create"


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found() -> ApiError:
    return ApiError(
        404,
        "NOT_FOUND",
        "找不到这个部署请求（或记录已过期）：请在实例列表里查询现有实例，不要换新的请求号重建",
    )


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


class AgentDeploymentService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None = None,
        im: OpenIMClient | None = None,
        runner: Any = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.agents = AgentService(db, settings, redis, im)
        self.servers = AgentServerService(db, settings, redis, runner)

    # ---------------------------------------------------------------- 新建并部署

    async def create(
        self,
        access: WorkspaceAccess,
        body: AgentDeploymentCreateIn,
        *,
        client_ip: str,
        path: str,
    ) -> tuple[AgentDeploymentCreateOut, bool]:
        """返回 (结果, 是否重放)。"""
        actor_id = access.user_id
        if actor_id is None:
            raise ApiError(403, "FORBIDDEN", "只有登录的 owner 可以部署")
        fingerprint = idempotency.fingerprint(
            access.workspace.id, "POST", path, body.model_dump(mode="json")
        )
        begun = await idempotency.begin(
            self.db,
            workspace_id=access.workspace.id,
            actor_id=actor_id,
            key=body.request_id,
            request_fingerprint=fingerprint,
            context={"op": OP, "server_id": str(body.server_id)},
        )
        if begun.state == "replay":
            record = begun.record
            if record.status == "failed" or record.context_json.get("op") != OP:
                idempotency.replay(record)  # 失败的记录：原样抛出当时的错误
            return await self._replayed(access, record), True
        # new：刚登记“处理中”；unknown：之前处理到一半退出了（业务事务没有提交），重新执行
        try:
            return await self._execute(access, body, client_ip=client_ip), False
        except ApiError as error:
            await self.db.rollback()
            await self._record_failure(actor_id, body.request_id, error)
            raise

    async def _execute(
        self, access: WorkspaceAccess, body: AgentDeploymentCreateIn, *, client_ip: str
    ) -> AgentDeploymentCreateOut:
        assert access.user_id is not None  # noqa: S101 - create 已检查
        # Fresh trusted probes are collected before taking admission row locks.
        # Replays return earlier in create() and never refresh or pick a new host.
        if body.placement.mode == "specified":
            assert body.placement.server_id is not None  # noqa: S101 - Placement validates specified
            preflight = await self.servers.server_of(access, body.placement.server_id)
            if body.server_version is not None and body.server_version != preflight.version:
                raise _version_conflict(preflight.version)
            await self.servers.deployable_target(preflight)
            server_ids = [preflight.id]
        else:
            server_ids = list(
                (
                    await self.db.scalars(
                        select(AgentServer.id)
                        .where(
                            AgentServer.workspace_id == access.workspace.id,
                            AgentServer.kind == "ssh",
                        )
                        .order_by(AgentServer.id)
                        .limit(64)
                    )
                ).all()
            )
        await self.servers.refresh_resource_snapshots(access, server_ids)
        # 锁住这条幂等记录：同一个请求号的并发重发（包括“处理中”超时后的重发）等这里结束
        record = await integration_repo.get_idempotency(
            self.db, actor_id=access.user_id, key=body.request_id, lock=True
        )
        if record is None or record.status in {"succeeded", "failed"}:
            raise ApiError(
                409,
                "IDEMPOTENCY_IN_PROGRESS",
                "同一个请求仍在处理，请稍后查询收据",
                headers={"Retry-After": "1"},
            )
        servers = self.servers
        servers.ready_to_run()
        server = await self._select_server(access, body, server_ids)
        await self._check_switch(access)
        if not self.settings.agent_public_url:
            raise ApiError(
                409,
                "AGENT_INGRESS_NOT_CONFIGURED",
                "外网接入入口还没有配置（AGENT_PUBLIC_URL）",
                details={"network_mode": "public"},
            )
        # 前提都在建实例之前检查：不满足时什么都不写
        await servers.deployable_target(server)
        instance = await self.agents.create_instance_record(
            access,
            AgentInstanceCreate(
                name=body.name,
                description=body.description,
                network_mode="public",
                scope="read",
                max_role="member",
                im_nickname=body.im_nickname,
            ),
            client_ip=client_ip,
        )
        await self.agents.apply_limits(
            access, instance, body.mem_limit_mb, body.cpus, client_ip=client_ip
        )
        # 主模型：授权连接给新实例并建绑定（同一事务；前提不满足时整体回滚、收据记为失败）
        await AgentModelBindingService(self.db, self.settings).bind_new_instance(
            access, instance, body.model
        )
        job, revision = await servers.accept_deployment(
            access, instance, server, client_ip=client_ip
        )
        try:
            deployment = await self.db.scalar(
                select(AgentDeployment).where(AgentDeployment.instance_id == instance.id)
            )
            assert deployment is not None  # noqa: S101 - accept_deployment creates reservation
            # Existing durable deployment row is the resource reservation: unknown,
            # failed and pending installs retain it until explicit confirmed removal.
            deployment.selection = {
                "requested_mode": body.placement.mode,
                "requested_server_id": str(body.placement.server_id)
                if body.placement.server_id
                else None,
                "actual_server_id": str(server.id),
                "reservation_id": str(deployment.id),
                "metrics_at": server.resource_snapshot["sampled_at"],
                "cpus": body.cpus,
                "memory_mb": body.mem_limit_mb,
                "disk_gb": self.settings.agent_placement_disk_gb,
                "status": "reserved",
                "reason_code": None,
            }
            ids = {
                "request_id": str(body.request_id),
                "instance_id": str(instance.id),
                "job_id": str(job.id),
            }
            idempotency.succeed(record, 202, ids)
            record.context_json = {**record.context_json, **ids, "server_id": str(server.id)}
            warning = servers.capacity_warning(server)
        except BaseException:
            servers.release_job(job)
            raise
        # 一处提交：实例、资源上限、部署记录、任务、审计、幂等记录（同机并发时部分唯一索引 409）
        await servers.commit_job(job)
        servers.launch(job, revision=revision, client_ip=client_ip)
        # 提交之后注册 OpenIM 账号；失败时 im_ready=false，由 Worker 重试（不影响部署）
        await self.agents.provision_im(instance)
        return await self._out(body.request_id, instance, warning=warning)

    async def _select_server(
        self,
        access: WorkspaceAccess,
        body: AgentDeploymentCreateIn,
        server_ids: list[uuid.UUID],
    ) -> AgentServer:
        """Serialize admission against durable commitments, across API processes.

        Every create holds host row locks through deployment/instance/job commit.
        The next transaction sees pending rows even when no runtime exists yet.
        No TTL deletes unknown allocations; runtime and pending count once each.
        """
        assert body.cpus is not None and body.mem_limit_mb is not None  # noqa: S101 - schema
        request = ResourceRequest(
            body.cpus, body.mem_limit_mb, self.settings.agent_placement_disk_gb
        )
        candidates = []
        servers = {}
        reasons = {}
        for server_id in sorted(server_ids, key=lambda value: value.int):
            server = await self.servers.server_of(access, server_id, lock=True)
            if body.server_version is not None and body.server_version != server.version:
                raise _version_conflict(server.version)
            try:
                await self.servers.deployable_target(server)
            except ApiError as error:
                if body.placement.mode == "specified":
                    raise
                reasons[str(server_id)] = error.code
                continue
            snap = server.resource_snapshot or {}
            if not agent_server_resources.is_fresh(
                snap, _now(), self.settings.agent_placement_metrics_max_age_seconds
            ):
                reasons[str(server_id)] = "METRICS_STALE"
                continue
            if not snap.get("runtime_ready") or not snap.get("network_ready"):
                reasons[str(server_id)] = "TARGET_UNAVAILABLE"
                continue
            try:
                candidates.append(
                    await self.servers.placement_candidate(
                        server, disk_gb=self.settings.agent_placement_disk_gb
                    )
                )
            except ApiError as error:
                reasons[str(server_id)] = error.code
                continue
            servers[server_id] = server
        if not candidates:
            code = "METRICS_STALE" if "METRICS_STALE" in reasons.values() else "NO_ELIGIBLE_SERVER"
            raise ApiError(
                409, code, "没有满足资源与健康要求的智能体服务器", details={"reasons": reasons}
            )
        try:
            ranked = rank_candidates(candidates, request, body.placement, _now())
        except PlacementError as error:
            raise ApiError(
                409, error.code, error.message, details={"reasons": error.reasons}
            ) from error
        return servers[ranked[0].server_id]

    async def _check_switch(self, access: WorkspaceAccess) -> None:
        settings = await self.db.get(AgentSettings, access.workspace.id)
        if settings is not None and not settings.agents_enabled:
            raise ApiError(
                409,
                "AGENT_DISABLED",
                "智能体总开关已关闭：打开后才能部署新的智能体",
                details={"reason": "agents_disabled"},
            )

    async def _record_failure(
        self, actor_id: uuid.UUID, request_id: uuid.UUID, error: ApiError
    ) -> None:
        """前提不满足：记为失败（重发得到同样的错误，收据是 failed）。业务写入已经回滚。"""
        record = await integration_repo.get_idempotency(
            self.db, actor_id=actor_id, key=request_id, lock=True
        )
        if record is None or record.status != "processing":
            await self.db.rollback()
            return
        idempotency.fail(record, error)
        await self.db.commit()

    async def _out(
        self, request_id: uuid.UUID, instance: AgentInstance, *, warning: str | None = None
    ) -> AgentDeploymentCreateOut:
        return AgentDeploymentCreateOut(
            request_id=request_id,
            instance=await self.agents.instance_out(instance),
            deployment=await self.servers.deployment_state_of(instance, warning=warning),
        )

    async def _instance(
        self, access: WorkspaceAccess, record: IdempotencyRequest
    ) -> AgentInstance | None:
        instance_id = _uuid(record.context_json.get("instance_id"))
        if instance_id is None:
            return None
        instance: AgentInstance | None = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id,
                AgentInstance.workspace_id == access.workspace.id,
            )
        )
        return instance

    async def _replayed(
        self, access: WorkspaceAccess, record: IdempotencyRequest
    ) -> AgentDeploymentCreateOut:
        """同一个请求号重发：返回那次接纳的实例与它现在的部署状态（不再建、不重启任务）。"""
        instance = await self._instance(access, record)
        if instance is None:
            raise ApiError(404, "NOT_FOUND", "这次部署创建的实例已被删除")
        return await self._out(record.idempotency_key, instance)

    # ---------------------------------------------------------------- 收据

    async def receipt(
        self, access: WorkspaceAccess, request_id: uuid.UUID
    ) -> AgentDeploymentRequestOut:
        """只给原来发出请求的人、在同一个工作空间里查；只认部署请求的记录。"""
        if access.user_id is None:
            raise _not_found()
        record = await integration_repo.get_idempotency(
            self.db, actor_id=access.user_id, key=request_id
        )
        now = _now()
        if (
            record is None
            or record.workspace_id != access.workspace.id
            or record.context_json.get("op") != OP
            or record.expires_at <= now
        ):
            raise _not_found()
        if record.status == "succeeded":
            instance = await self._instance(access, record)
            return AgentDeploymentRequestOut(
                request_id=request_id,
                state="accepted",
                instance=await self.agents.instance_out(instance) if instance else None,
                deployment=(await self.servers.deployment_state_of(instance) if instance else None),
                error_code=None,
            )
        if record.status == "failed":
            error = (record.response_json or {}).get("error") or {}
            code = error.get("code")
            return AgentDeploymentRequestOut(
                request_id=request_id,
                state="failed",
                instance=None,
                deployment=None,
                error_code=str(code) if code else None,
            )
        fresh = (
            record.status == "processing" and now - record.updated_at < idempotency.STALE_PROCESSING
        )
        return AgentDeploymentRequestOut(
            request_id=request_id,
            state="processing" if fresh else "interrupted",
            instance=None,
            deployment=None,
            error_code=None if fresh else "interrupted",
        )
