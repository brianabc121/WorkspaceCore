"""OpenClaw 实例的主模型绑定、应用任务与实例取配置 / 回报 / 取 Key（T-0204 / T-0205）。

设计：docs/v2/model-connections/ 契约第 3 节；OpenClaw 行为核验：
docs/decisions/2026-11-09-model-connections.md。

- 绑定是“目标”（connection_id / model_id / desired_revision），实例回报确认后才写“已应用”快照
  （applied_connection_id / applied_model_id / applied_revision）；失败、未知都不显示已应用。
- 每次目标变化 desired_revision 加 1，并为它建一个应用任务（同实例同修订号一个任务）。
  实例的 model-config.mjs 每分钟取一次配置：apply_key 变了才应用（OpenClaw `config patch`
  一次校验写入 + 热加载 + `secrets reload`），然后回报。迟到的旧修订号回报一律 409，不改状态。
- 实例取模型 Key 只认 mc_<连接 ID>：连接必须是当前目标或尚未退休的已应用连接、启用、
  仍授权给这个实例、绑定没有解除。切换确认后旧连接退休；
  解除绑定立即拒绝后续取用（已在进程里的 Key 不保证立即失效）。
- 应用任务超过 APPLY_TIMEOUT 没被领取或没有回报，按“未知”显示（不写库）；再次应用会重新排队。
"""

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import ops_metrics
from app.domain.agents import AgentPrincipal, agent_secret_context
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.model_connections import (
    OPENCLAW_API,
    ModelConnectionService,
    connection_id_from_ref,
    provider_key,
    secret_ref_id,
    supported_protocols,
)
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.models import (
    AgentInstance,
    AgentModelBinding,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretVersion,
    ModelApplyJob,
    ModelConnection,
    ModelConnectionGrant,
    ModelConnectionModel,
)
from app.schemas.model_connections import (
    AgentModelBindingOut,
    AgentModelConfigOut,
    ModelApplyJobOut,
    ModelAssignmentPage,
    ModelAssignmentRow,
    ModelBindingApply,
    ModelBindingPut,
    ModelBindingView,
    ModelConfigApplied,
    ModelConfigAppliedOut,
    ModelCredentialError,
    ModelCredentialResolveResponse,
    NewInstanceModelChoice,
    PickerConnectionOut,
    PickerConnectionPage,
)

# 受管 provider 键的前缀（provider_key 生成的键都以它开头，见 model_connections.provider_key）
MANAGED_PREFIX = "mwc"
# 应用任务多久没被领取 / 没有回报算“未知”
APPLY_TIMEOUT = timedelta(minutes=10)
# 实例接口的频率上限（每个实例每分钟）
MODEL_CONFIG_PER_MINUTE = 6
MODEL_REPORT_PER_MINUTE = 6
MODEL_RESOLVE_PER_MINUTE = 60
_OPEN_JOBS = ("queued", "running")
PAGE_MAX = 100


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "资源不存在")


def _version_conflict(current: int | None) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "绑定已被修改，请刷新后再试",
        details={"current_version": current},
    )


def _rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429, "RATE_LIMITED", "请求过于频繁，请稍后再试", headers={"Retry-After": str(retry_after)}
    )


def effective_job_state(job: ModelApplyJob, *, now: datetime | None = None) -> str:
    """排队或执行中太久没有结果的任务按“未知”显示（不写库；再次应用会重新排队）。"""
    now = now or _now()
    if job.state == "queued" and now - job.updated_at > APPLY_TIMEOUT:
        return "unknown"
    if job.state == "running" and now - (job.started_at or job.updated_at) > APPLY_TIMEOUT:
        return "unknown"
    return job.state


def job_out(job: ModelApplyJob) -> ModelApplyJobOut:
    state = effective_job_state(job)
    return ModelApplyJobOut(
        id=job.id,
        instance_id=job.instance_id,
        state=state,
        desired_revision=job.desired_revision,
        attempt=job.attempt,
        last_error_code=job.last_error_code
        if state == job.state
        else (job.last_error_code or "no_report"),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def binding_state(binding: AgentModelBinding, job: ModelApplyJob | None) -> str:
    """绑定的应用状态：当前目标的任务超时没有结果时显示 unknown。"""
    if (
        binding.apply_state in ("pending", "applying")
        and job is not None
        and job.desired_revision == binding.desired_revision
        and effective_job_state(job) == "unknown"
    ):
        return "unknown"
    return binding.apply_state


def binding_out(
    binding: AgentModelBinding, job: ModelApplyJob | None, names: dict[uuid.UUID, str]
) -> AgentModelBindingOut:
    state = binding_state(binding, job)
    return AgentModelBindingOut(
        instance_id=binding.instance_id,
        connection_id=binding.connection_id,
        model_id=binding.model_id,
        desired_revision=binding.desired_revision,
        applied_connection_id=binding.applied_connection_id,
        applied_model_id=binding.applied_model_id,
        applied_revision=binding.applied_revision,
        applied_at=binding.applied_at,
        revoked=binding.revoked,
        apply_state=state,
        last_error_code=binding.last_error_code
        if state == binding.apply_state
        else (binding.last_error_code or "no_report"),
        job_id=binding.job_id,
        version=binding.version,
        connection_name=names.get(binding.connection_id) if binding.connection_id else None,
        applied_connection_name=names.get(binding.applied_connection_id)
        if binding.applied_connection_id
        else None,
    )


async def _supersede_jobs(db: AsyncSession, instance_id: uuid.UUID, revision: int) -> None:
    """只应用最新目标：同实例更早修订号的未完成任务标为失败（superseded）。"""
    await db.execute(
        update(ModelApplyJob)
        .where(
            ModelApplyJob.instance_id == instance_id,
            ModelApplyJob.desired_revision < revision,
            ModelApplyJob.state.in_(_OPEN_JOBS),
        )
        .values(state="failed", last_error_code="superseded", finished_at=_now())
    )


async def queue_job(db: AsyncSession, binding: AgentModelBinding) -> ModelApplyJob:
    """为绑定当前的目标修订号建应用任务（已有就重新排队）。不提交。"""
    await _supersede_jobs(db, binding.instance_id, binding.desired_revision)
    job = await db.scalar(
        select(ModelApplyJob)
        .where(
            ModelApplyJob.instance_id == binding.instance_id,
            ModelApplyJob.desired_revision == binding.desired_revision,
        )
        .with_for_update()
    )
    now = _now()
    if job is None:
        job = ModelApplyJob(
            workspace_id=binding.workspace_id,
            instance_id=binding.instance_id,
            desired_revision=binding.desired_revision,
            state="queued",
            attempt=1,
        )
        db.add(job)
        await db.flush()
    else:
        job.state = "queued"
        job.attempt += 1
        job.last_error_code = None
        job.started_at = None
        job.finished_at = None
        job.updated_at = now
    binding.job_id = job.id
    return job


async def advance_bindings(db: AsyncSession, connection_id: uuid.UUID) -> int:
    """连接的配置或 Key 变了：以它为目标的绑定重新应用（修订号加 1、排任务）。不提交。

    Key 更换后同一个 SecretRef id 的值要实例执行 `secrets reload` 才会更新
    （核验记录第 4 节），重新应用会马上做这一步，而不是等每 10 分钟的定时任务。"""
    bindings = (
        await db.scalars(
            select(AgentModelBinding)
            .where(
                AgentModelBinding.connection_id == connection_id,
                AgentModelBinding.revoked.is_(False),
            )
            .order_by(AgentModelBinding.instance_id)
            .with_for_update()
        )
    ).all()
    for binding in bindings:
        binding.desired_revision += 1
        binding.apply_state = "pending"
        binding.last_error_code = None
        binding.version += 1
        await queue_job(db, binding)
    return len(bindings)


def managed_fingerprint(parts: dict[str, object]) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class AgentModelBindingService:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)
        self.connections = ModelConnectionService(db, settings)

    # ---------------------------------------------------------------- 内部

    async def _instance(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, *, lock: bool = False
    ) -> AgentInstance:
        query = select(AgentInstance).where(
            AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
        )
        if lock:
            query = query.with_for_update()
        instance = await self.db.scalar(query)
        if instance is None:
            raise _not_found()
        return instance

    async def _binding(
        self, instance_id: uuid.UUID, *, lock: bool = False
    ) -> AgentModelBinding | None:
        query = select(AgentModelBinding).where(AgentModelBinding.instance_id == instance_id)
        if lock:
            query = query.with_for_update()
        binding: AgentModelBinding | None = await self.db.scalar(query)
        return binding

    async def _job(self, binding: AgentModelBinding) -> ModelApplyJob | None:
        if binding.job_id is None:
            return None
        return await self.db.get(ModelApplyJob, binding.job_id)

    async def _names(self, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
        wanted = {i for i in ids if i is not None}
        if not wanted:
            return {}
        rows = await self.db.execute(
            select(ModelConnection.id, ModelConnection.name).where(ModelConnection.id.in_(wanted))
        )
        return {cid: name for cid, name in rows.all()}

    async def _out(self, binding: AgentModelBinding) -> AgentModelBindingOut:
        names = await self._names([binding.connection_id, binding.applied_connection_id])
        return binding_out(binding, await self._job(binding), names)

    async def _check_target(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        model_id: str,
        *,
        instance_id: uuid.UUID | None,
    ) -> ModelConnection:
        """绑定目标的前提：同工作空间、模型连接、启用、配置完整、协议已核验、模型在集合内、
        （已有实例）已授权、Key 可读。instance_id 为 None 表示新部署（选择即授权）。"""
        connection = await self.connections.connection(access, connection_id, lock=True)
        if not connection.enabled:
            raise ApiError(409, "MODEL_CONNECTION_DISABLED", "这条连接已停用")
        if connection.configuration_state != "ready":
            raise ApiError(
                409, "MODEL_CONNECTION_NOT_READY", "这条连接还没有配置完整（协议、地址、Key、模型）"
            )
        if connection.protocol not in supported_protocols(self.settings):
            raise ApiError(409, "MODEL_PROTOCOL_UNSUPPORTED", "这条连接的协议还没有开放")
        model = await self.db.scalar(
            select(ModelConnectionModel.id).where(
                ModelConnectionModel.connection_id == connection.id,
                ModelConnectionModel.model_id == model_id,
            )
        )
        if model is None:
            raise ApiError(
                422,
                "MODEL_NOT_ALLOWED",
                "这个模型不在连接的模型集合里",
                details={"field": "model_id", "reason": "not_in_connection"},
            )
        if instance_id is not None:
            granted = await self.db.scalar(
                select(ModelConnectionGrant.id).where(
                    ModelConnectionGrant.connection_id == connection.id,
                    ModelConnectionGrant.instance_id == instance_id,
                )
            )
            if granted is None:
                raise ApiError(
                    409,
                    "MODEL_NOT_ALLOWED",
                    "这条连接还没有授权给这个实例：先在中转站管理里授权",
                    details={"reason": "not_granted"},
                )
        if not await self.connections.credential_readable(connection):
            raise ApiError(409, "CREDENTIAL_UNREADABLE", "这条连接的 Key 无法解密，请重新填写")
        return connection

    @staticmethod
    def _set_target(
        binding: AgentModelBinding, connection_id: uuid.UUID | None, model_id: str | None
    ) -> None:
        binding.connection_id = connection_id
        binding.model_id = model_id
        binding.revoked = connection_id is None
        binding.desired_revision += 1
        binding.apply_state = "pending"
        binding.last_error_code = None
        binding.version += 1

    # ---------------------------------------------------------------- owner：读

    async def view(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> ModelBindingView:
        instance = await self._instance(access, instance_id)
        binding = await self._binding(instance.id)
        if binding is not None:
            out = await self._out(binding)
            return ModelBindingView(binding=out, apply_state=out.apply_state)
        return ModelBindingView(binding=None, apply_state=None)

    async def job(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, job_id: uuid.UUID
    ) -> ModelApplyJobOut:
        instance = await self._instance(access, instance_id)
        job = await self.db.scalar(
            select(ModelApplyJob).where(
                ModelApplyJob.id == job_id, ModelApplyJob.instance_id == instance.id
            )
        )
        if job is None:
            raise _not_found()
        return job_out(job)

    async def assignments(
        self,
        access: WorkspaceAccess,
        *,
        q: str | None = None,
        connection_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> ModelAssignmentPage:
        """“OpenClaw 连接分配”表：每个实例一行（目标、已应用与应用状态）。"""
        query = select(AgentInstance).where(AgentInstance.workspace_id == access.workspace.id)
        if q:
            query = query.where(AgentInstance.name.ilike(f"%{_escape(q)}%", escape="\\"))
        if connection_id is not None:
            query = query.where(
                AgentInstance.id.in_(
                    select(AgentModelBinding.instance_id).where(
                        or_(
                            AgentModelBinding.connection_id == connection_id,
                            AgentModelBinding.applied_connection_id == connection_id,
                        )
                    )
                )
            )
        total = int(await self.db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        instances = (
            await self.db.scalars(
                query.order_by(AgentInstance.name, AgentInstance.id).offset(offset).limit(limit)
            )
        ).all()
        ids = [i.id for i in instances]
        bindings = {
            b.instance_id: b
            for b in (
                await self.db.scalars(
                    select(AgentModelBinding).where(AgentModelBinding.instance_id.in_(ids))
                )
            ).all()
        }
        jobs = {
            j.id: j
            for j in (
                await self.db.scalars(
                    select(ModelApplyJob).where(
                        ModelApplyJob.id.in_([b.job_id for b in bindings.values() if b.job_id])
                    )
                )
            ).all()
        }
        names = await self._names(
            [b.connection_id for b in bindings.values()]
            + [b.applied_connection_id for b in bindings.values()]
        )
        rows = []
        for instance in instances:
            binding = bindings.get(instance.id)
            rows.append(
                ModelAssignmentRow(
                    instance_id=instance.id,
                    instance_name=instance.name,
                    instance_status=instance.status,
                    binding=binding_out(
                        binding, jobs.get(binding.job_id) if binding.job_id else None, names
                    )
                    if binding
                    else None,
                )
            )
        return ModelAssignmentPage(
            items=rows,
            next_cursor=str(offset + limit) if offset + limit < total else None,
            total=total,
        )

    async def picker(
        self, access: WorkspaceAccess, *, instance_id: uuid.UUID | None
    ) -> PickerConnectionPage:
        """可选的连接：启用、配置完整、协议已核验。已有实例时标出是否已授权；新建实例不按授权过滤。"""
        if instance_id is not None:
            await self._instance(access, instance_id)
        rows = (
            await self.db.scalars(
                select(ModelConnection)
                .where(
                    ModelConnection.workspace_id == access.workspace.id,
                    ModelConnection.enabled.is_(True),
                    ModelConnection.configuration_state == "ready",
                    ModelConnection.protocol.in_(supported_protocols(self.settings)),
                )
                .order_by(ModelConnection.name, ModelConnection.id)
                .limit(PAGE_MAX)
            )
        ).all()
        granted: set[uuid.UUID] = set()
        if instance_id is not None and rows:
            granted = set(
                (
                    await self.db.scalars(
                        select(ModelConnectionGrant.connection_id).where(
                            ModelConnectionGrant.instance_id == instance_id,
                            ModelConnectionGrant.connection_id.in_([r.id for r in rows]),
                        )
                    )
                ).all()
            )
        counts = dict(
            (
                await self.db.execute(
                    select(ModelConnectionModel.connection_id, func.count())
                    .where(ModelConnectionModel.connection_id.in_([r.id for r in rows]))
                    .group_by(ModelConnectionModel.connection_id)
                )
            ).all()
        )
        items = [
            PickerConnectionOut(
                id=row.id,
                name=row.name,
                connection_type=row.connection_type,
                protocol=row.protocol,
                base_url=row.base_url or "",
                model_count=int(counts.get(row.id, 0)),
                granted=row.id in granted,
            )
            for row in rows
        ]
        return PickerConnectionPage(items=items, next_cursor=None, total=len(items))

    # ---------------------------------------------------------------- owner：写

    async def put(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: ModelBindingPut,
        *,
        client_ip: str,
    ) -> AgentModelBindingOut:
        # 锁实例：同一实例的绑定写入串行（第一次绑定的并发也不会重复插入）
        instance = await self._instance(access, instance_id, lock=True)
        binding = await self._binding(instance.id, lock=True)
        if binding is None:
            if body.version is not None:
                raise _version_conflict(None)
        elif body.version != binding.version:
            raise _version_conflict(binding.version)
        connection = await self._check_target(
            access, body.connection_id, body.model_id, instance_id=instance.id
        )
        if binding is None:
            binding = AgentModelBinding(
                instance_id=instance.id,
                workspace_id=instance.workspace_id,
                connection_id=connection.id,
                model_id=body.model_id,
                desired_revision=1,
                revoked=False,
                apply_state="pending",
                version=1,
            )
            self.db.add(binding)
            await self.db.flush()
        elif (
            binding.connection_id == connection.id
            and binding.model_id == body.model_id
            and not binding.revoked
        ):
            return await self._out(binding)  # 目标没变：不加修订号、不重复排任务
        else:
            self._set_target(binding, connection.id, body.model_id)
        await queue_job(self.db, binding)
        await self.audit.record(
            "agent.model_binding.updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "connection_id": str(connection.id),
                "model_id": body.model_id,
                "desired_revision": binding.desired_revision,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self._out(binding)

    async def remove(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, *, version: int, client_ip: str
    ) -> AgentModelBindingOut:
        """解除绑定：目标置空、revoked=true，立即拒绝后续模型 Key 取用，排任务移除受管配置。
        保留已应用快照用于诊断；不删除连接或授权。"""
        instance = await self._instance(access, instance_id, lock=True)
        binding = await self._binding(instance.id, lock=True)
        if binding is None:
            raise _not_found()
        if binding.version != version:
            raise _version_conflict(binding.version)
        if binding.revoked:
            return await self._out(binding)
        previous = binding.connection_id
        self._set_target(binding, None, None)
        await queue_job(self.db, binding)
        await self.audit.record(
            "agent.model_binding.revoked",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "connection_id": str(previous) if previous else None,
                "desired_revision": binding.desired_revision,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self._out(binding)

    async def apply(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: ModelBindingApply,
        *,
        client_ip: str,
    ) -> ModelApplyJobOut:
        """（重新）应用当前目标：同一修订号复用同一个任务；失败、未知、待手动的重新排队。"""
        instance = await self._instance(access, instance_id, lock=True)
        binding = await self._binding(instance.id, lock=True)
        if binding is None:
            raise _not_found()
        if binding.version != body.version:
            raise _version_conflict(binding.version)
        job = await self._job(binding)
        if (
            job is not None
            and job.desired_revision == binding.desired_revision
            and effective_job_state(job) in ("queued", "running", "succeeded")
        ):
            return job_out(job)
        job = await queue_job(self.db, binding)
        binding.apply_state = "pending"
        binding.last_error_code = None
        binding.version += 1
        await self.audit.record(
            "agent.model_binding.apply_requested",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={"desired_revision": binding.desired_revision, "attempt": job.attempt},
            client_ip=client_ip,
        )
        await self.db.commit()
        return job_out(job)

    async def bind_new_instance(
        self, access: WorkspaceAccess, instance: AgentInstance, choice: NewInstanceModelChoice
    ) -> AgentModelBinding:
        """新部署：在调用方的事务里授权连接给新实例并建绑定与应用任务（不提交）。"""
        connection = await self._check_target(
            access, choice.connection_id, choice.model_id, instance_id=None
        )
        self.db.add(
            ModelConnectionGrant(
                workspace_id=instance.workspace_id,
                connection_id=connection.id,
                instance_id=instance.id,
            )
        )
        binding = AgentModelBinding(
            instance_id=instance.id,
            workspace_id=instance.workspace_id,
            connection_id=connection.id,
            model_id=choice.model_id,
            desired_revision=1,
            revoked=False,
            apply_state="pending",
            version=1,
        )
        self.db.add(binding)
        await self.db.flush()
        await queue_job(self.db, binding)
        connection.version += 1
        return binding

    # ---------------------------------------------------------------- 接入包

    async def bundle_model(self, instance: AgentInstance) -> AgentModelConfigOut | None:
        """生成接入包时写进 openclaw.json 的受管模型配置（没有有效目标时为 None）。"""
        binding = await self._binding(instance.id)
        if binding is None or binding.revoked:
            return None
        try:
            return await self._config(binding)
        except ApiError:
            return None

    # ---------------------------------------------------------------- 实例接口

    async def _limit(self, principal: AgentPrincipal, name: str, per_minute: int) -> None:
        if self.redis is None:
            return
        retry = await WindowLimiter(self.redis, 60).hit(
            f"agent_rate:{name}:{principal.instance.id}", per_minute
        )
        if retry is not None:
            raise _rate_limited(retry)

    async def _config(
        self, binding: AgentModelBinding, job: ModelApplyJob | None = None
    ) -> AgentModelConfigOut:
        attempt = job.attempt if job and job.desired_revision == binding.desired_revision else 0
        job_state = (
            effective_job_state(job)
            if job and job.desired_revision == binding.desired_revision
            else None
        )
        common = {
            "binding_version": binding.version,
            "desired_revision": binding.desired_revision,
            "apply_key": f"{binding.desired_revision}.{attempt}",
            "job_state": job_state,
            "managed_prefix": MANAGED_PREFIX,
        }
        if binding.revoked or binding.connection_id is None or binding.model_id is None:
            return AgentModelConfigOut(
                state="revoked",
                connection_id=None,
                provider_key=None,
                api=None,
                protocol=None,
                base_url=None,
                model_id=None,
                model_name=None,
                primary=None,
                secret_ref_id=None,
                config_revision=None,
                fingerprint=managed_fingerprint({"state": "revoked", **common}),
                **common,
            )
        connection = await self.db.get(ModelConnection, binding.connection_id)
        if (
            connection is None
            or not connection.enabled
            or connection.configuration_state != "ready"
            or connection.protocol not in supported_protocols(self.settings)
            or not connection.base_url
        ):
            raise ApiError(
                409,
                "MODEL_CONFIGURATION_INCOMPLETE",
                "当前目标连接不可用",
                details={"reason": "connection_unavailable"},
            )
        model = await self.db.scalar(
            select(ModelConnectionModel).where(
                ModelConnectionModel.connection_id == connection.id,
                ModelConnectionModel.model_id == binding.model_id,
            )
        )
        name = (model.display_name if model else "") or binding.model_id
        key = provider_key(connection.id)
        parts = {
            "provider_key": key,
            "api": OPENCLAW_API[connection.protocol],
            "base_url": connection.base_url,
            "model_id": binding.model_id,
            "model_name": name,
            "secret_ref_id": secret_ref_id(connection.id),
            "config_revision": connection.config_revision,
        }
        return AgentModelConfigOut(
            state="active",
            connection_id=connection.id,
            protocol=connection.protocol,
            primary=f"{key}/{binding.model_id}",
            fingerprint=managed_fingerprint({"state": "active", **parts, **common}),
            **parts,
            **common,
        )

    async def model_config(self, principal: AgentPrincipal) -> AgentModelConfigOut:
        """实例取自己的配置。当前目标的任务在排队时标为执行中（实例开始应用）。"""
        await self._limit(principal, "model_config", MODEL_CONFIG_PER_MINUTE)
        binding = await self._binding(principal.instance.id, lock=True)
        if binding is None:
            await self.db.rollback()
            raise ApiError(
                409,
                "MODEL_CONFIGURATION_INCOMPLETE",
                "这个实例还没有绑定模型连接",
                details={"reason": "no_binding"},
            )
        job = await self._job(binding)
        try:
            out = await self._config(binding, job)
        except ApiError:
            await self.db.rollback()
            raise
        if (
            job is not None
            and job.desired_revision == binding.desired_revision
            and effective_job_state(job) == "queued"
        ):
            job.state = "running"
            job.started_at = _now()
            if binding.apply_state == "pending":
                binding.apply_state = "applying"
            out.job_state = "running"
        await self.db.commit()
        return out

    async def report_applied(
        self, principal: AgentPrincipal, body: ModelConfigApplied
    ) -> ModelConfigAppliedOut:
        """实例回报应用结果。只认当前目标的修订号：迟到的旧回报 409，不改任何状态。"""
        await self._limit(principal, "model_report", MODEL_REPORT_PER_MINUTE)
        instance = principal.instance
        binding = await self._binding(instance.id, lock=True)
        if binding is None or body.desired_revision != binding.desired_revision:
            current = binding.desired_revision if binding else None
            await self.db.rollback()
            raise ApiError(
                409,
                "MODEL_BINDING_CONFLICT",
                "回报的不是当前目标（已过期），状态未改变",
                details={"desired_revision": current},
            )
        job = await self._job(binding)
        now = _now()
        if body.result == "applied":
            binding.applied_connection_id = binding.connection_id
            binding.applied_model_id = binding.model_id
            binding.applied_revision = binding.desired_revision
            binding.applied_at = now
            binding.apply_state = "applied"
            binding.last_error_code = None
            job_state = "succeeded"
        else:
            binding.apply_state = body.result
            binding.last_error_code = body.reason or (
                "apply_failed" if body.result == "failed" else "manual_required"
            )
            job_state = body.result
        if job is not None and job.desired_revision == binding.desired_revision:
            job.state = job_state
            job.last_error_code = binding.last_error_code
            job.started_at = job.started_at or now
            job.finished_at = now
        await self.audit.record(
            "agent.model_binding.applied"
            if body.result == "applied"
            else "agent.model_binding.apply_failed",
            workspace_ids=[instance.workspace_id],
            actor_id=None,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "desired_revision": binding.desired_revision,
                "result": body.result,
                "reason": binding.last_error_code,
            },
        )
        await self.db.commit()
        return ModelConfigAppliedOut(
            apply_state=binding.apply_state,
            desired_revision=binding.desired_revision,
            applied_revision=binding.applied_revision,
        )

    async def resolve_credentials(
        self, principal: AgentPrincipal, ids: Sequence[str], *, client_ip: str
    ) -> ModelCredentialResolveResponse:
        """实例取模型 Key（mc_<连接 ID>）。只给当前目标或尚未退休的已应用连接，
        且连接启用、仍授权给这个实例、绑定没有解除。每次取用都写记录；失败一律 NOT_FOUND。"""
        instance = principal.instance
        wanted = list(dict.fromkeys(ids))
        try:
            await self._limit(principal, "model_resolve", MODEL_RESOLVE_PER_MINUTE)
        except ApiError:
            for ref in wanted:
                self._record(instance, ref, None, None, "rate_limited", client_ip)
            await self.db.commit()
            raise
        binding = await self._binding(instance.id)
        candidates: set[uuid.UUID] = set()
        if binding is not None and not binding.revoked:
            candidates = {
                cid for cid in (binding.connection_id, binding.applied_connection_id) if cid
            }
        wanted_ids = {
            cid
            for cid in (connection_id_from_ref(ref) for ref in wanted)
            if cid is not None and cid in candidates
        }
        rows = (
            (
                await self.db.execute(
                    select(ModelConnection, AgentSecret, AgentSecretVersion.ciphertext_ref)
                    .join(AgentSecret, AgentSecret.id == ModelConnection.credential_secret_id)
                    .join(
                        AgentSecretVersion,
                        (AgentSecretVersion.secret_id == AgentSecret.id)
                        & (AgentSecretVersion.value_version == AgentSecret.current_version),
                    )
                    .join(
                        ModelConnectionGrant,
                        (ModelConnectionGrant.connection_id == ModelConnection.id)
                        & (ModelConnectionGrant.instance_id == instance.id),
                    )
                    .where(
                        ModelConnection.id.in_(wanted_ids),
                        ModelConnection.workspace_id == instance.workspace_id,
                        ModelConnection.enabled.is_(True),
                    )
                )
            ).all()
            if wanted_ids
            else []
        )
        found: dict[uuid.UUID, tuple[AgentSecret, str | None]] = {
            connection.id: (secret, ref) for connection, secret, ref in rows
        }
        box: CredentialBox | None = None
        if found:
            try:
                box = CredentialBox.from_settings(self.settings)
            except CredentialError:
                box = None
        values: dict[str, str] = {}
        errors: dict[str, ModelCredentialError] = {}
        for ref_id in wanted:
            cid = connection_id_from_ref(ref_id)
            hit = found.get(cid) if cid else None
            secret = hit[0] if hit else None
            sealed = hit[1] if hit else None
            value: str | None = None
            if secret is not None and sealed and box is not None:
                try:
                    value = box.resolve(
                        sealed, context=agent_secret_context(secret.id, secret.current_version)
                    ).get_secret_value()
                except CredentialError:
                    value = None
            if secret is None or value is None:
                errors[ref_id] = ModelCredentialError(code="NOT_FOUND")
                self._record(instance, ref_id, None, None, "not_found", client_ip)
            else:
                values[ref_id] = value
                self._record(instance, ref_id, secret.id, secret.current_version, "ok", client_ip)
        await self.db.commit()
        if self.redis is not None:
            await ops_metrics.bump(self.redis, "agent_secret_resolves", "ok", amount=len(values))
            await ops_metrics.bump(
                self.redis, "agent_secret_resolves", "not_found", amount=len(errors)
            )
        return ModelCredentialResolveResponse(values=values, errors=errors)

    def _record(
        self,
        instance: AgentInstance,
        ref_id: str,
        secret_id: uuid.UUID | None,
        value_version: int | None,
        result: str,
        client_ip: str,
    ) -> None:
        self.db.add(
            AgentSecretAccess(
                workspace_id=instance.workspace_id,
                instance_id=instance.id,
                instance_name=instance.name,
                ref_id=ref_id[:64],
                secret_id=secret_id,
                value_version=value_version,
                result=result,
                source_ip=client_ip,
            )
        )


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
