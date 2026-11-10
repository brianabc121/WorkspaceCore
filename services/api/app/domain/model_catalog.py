"""中转站管理：模型目录同步与连接检测（T-0202，MC-2）。

约定见 docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md
第 1 节（检测结果）、第 2 节（/models/sync、/checks）、第 4 节、第 5 节。

- 只给 owner；连接的 Key 只在内存里解密，交给协议适配（app/integrations/model_providers.py）
  放进 Authorization 请求头，不进响应、审计、日志或检测记录。
- 访问服务商期间不占数据库事务和行锁：读完连接与 Key 就结束事务，拿到结果后重新读取连接
  （SELECT … FOR UPDATE）再写回。
- 修订号竞态：开始时记下 config_revision；写回时连接的修订号已经变了（检测期间更换了 Key、
  改了地址或模型集合），检测记录照样保存，但记为 unknown / stale_revision、不关联到连接的
  last_check、不把模型标成 verified；同步结果直接丢弃（不覆盖新配置的快照）。
- 限流：同一连接同一时间只有一个同步或检测（Redis 锁，带过期，409 CHECK_IN_PROGRESS）；
  每个 owner 对每个连接每分钟最多 5 次同步与检测合计（429 RATE_LIMITED，带 Retry-After）。
- 停用的连接也可以同步和检测（管理员排查问题用）；停用只阻止后续取用 Key 与新绑定。
  非模型凭据、没填协议或地址、协议未核验开放的连接不能同步或检测（409）。

模型同步：
- 完整结果（不超过 1000 个、响应不超过 2 MiB、没有下一页）：保存排好序的快照
  （discovered_models）、sync_state=complete、models_synced_at。
  - 全部可用模型（all_available）：模型集合换成快照（新增的记为 discovered / listed）；
    服务商不再列出、但正被智能体使用（目标或已应用）的模型保留并在结果里列出（kept_in_use），
    不默默移除。
  - 指定模型（selected）：只更新快照与列出的已选模型的 last_seen_at，不改变选中的集合。
  - 可用的模型集合变了才增加 config_revision（并让旧检测结果失效）。
- 不完整（partial）、失败（failed）、不支持列模型（unsupported）：保留上一次的完整快照与模型集合
  （kept_previous=true）。连接上的 sync_state 表示“当前生效的快照”：已有完整快照时仍为 complete
  （全部可用模型的连接不会因为一次失败的同步变成未完成配置），否则记为这次的结果；
  这次同步的结果在返回值里。
- 列出模型不等于可以推理：同步和 metadata 检测都不会把模型标成 verified；
  只有推理检测通过才标 verified。
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agents import agent_secret_context
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialError
from app.domain.model_connections import ModelConnectionService, check_out, host_of_url
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import WindowLimiter
from app.integrations.http import ConnectorHttp
from app.integrations.model_providers import (
    ModelListing,
    ProbeResult,
    ProviderAdapter,
    adapter_for,
    provider_http,
)
from app.models import (
    AgentSecret,
    AgentSecretVersion,
    ModelConnection,
    ModelConnectionCheck,
    ModelConnectionModel,
)
from app.schemas.model_connections import (
    ConnectionCheckOut,
    ConnectionCheckRequest,
    ModelSyncRequest,
    ModelSyncResult,
    SyncState,
)

# 同一连接同一时间只有一个同步或检测；锁的过期时间要长于一次操作的上限
# （列模型最多两次尝试，每次 20 秒，见 model_providers）
BUSY_TTL_SECONDS = 90
PROBES_PER_MINUTE = 5

# 只删除自己持有的锁（比较并删除，原子执行；与 app/domain/sync_lock.py 相同）
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

AdapterFactory = Callable[[str, ConnectorHttp], ProviderAdapter]


def _now() -> datetime:
    return datetime.now(UTC)


def _busy_key(connection_id: uuid.UUID) -> str:
    return f"ws:model-connection-busy:{connection_id}"


def _attempt_state(listing: ModelListing) -> SyncState:
    if listing.result.passed:
        return "complete" if listing.complete else "partial"
    if listing.result.status == "unsupported":
        return "unsupported"
    return "failed"


class ModelCatalogService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: ConnectorRuntime,
        redis: Redis,
        *,
        adapters: AdapterFactory = adapter_for,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.redis = redis
        self.adapters = adapters
        self.connections = ModelConnectionService(db, settings)
        self.audit = self.connections.audit

    # ---------------------------------------------------------------- 内部

    def _adapter(self, protocol: str) -> ProviderAdapter:
        # 测试替换 runtime.transport（httpx.MockTransport）；生产为 None，用默认传输层（校验 TLS）
        return self.adapters(protocol, provider_http(self.settings, self.runtime.transport))

    def _ensure_checkable(self, connection: ModelConnection) -> tuple[str, str]:
        """返回（协议, 协议基地址）。不能访问服务商的连接直接 409。"""
        if not connection.protocol or not connection.base_url:
            raise ApiError(
                409,
                "MODEL_CONFIGURATION_INCOMPLETE",
                "连接还没有填写协议和地址，不能同步模型或检测",
            )
        if connection.protocol not in self.connections.protocols:
            raise ApiError(
                409,
                "MODEL_PROTOCOL_UNSUPPORTED",
                "这个协议还没有核验开放，不能同步模型或检测",
            )
        return connection.protocol, connection.base_url

    async def _api_key(self, connection: ModelConnection) -> SecretStr | None:
        """在内存里解密当前版本的 Key；不可读时返回 None（记为 credential_unreadable）。"""
        secret = await self.db.get(AgentSecret, connection.credential_secret_id)
        if secret is None:
            return None
        ref = await self.db.scalar(
            select(AgentSecretVersion.ciphertext_ref).where(
                AgentSecretVersion.secret_id == secret.id,
                AgentSecretVersion.value_version == secret.current_version,
            )
        )
        if not ref:
            return None
        box = self.connections._box()
        try:
            return box.resolve(ref, context=agent_secret_context(secret.id, secret.current_version))
        except CredentialError:
            return None

    async def _reload(self, access: WorkspaceAccess, connection_id: uuid.UUID) -> ModelConnection:
        """访问服务商之后重新读取并锁住连接（覆盖会话里的旧值）。"""
        row = await self.db.scalar(
            select(ModelConnection)
            .where(
                ModelConnection.id == connection_id,
                ModelConnection.workspace_id == access.workspace.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "连接不存在")
        return row

    @asynccontextmanager
    async def _slot(self, access: WorkspaceAccess, connection_id: uuid.UUID) -> AsyncIterator[None]:
        """同一连接一个在途操作 + 每个 owner 每个连接每分钟 5 次。"""
        holder = uuid.uuid4().hex
        key = _busy_key(connection_id)
        if not await self.redis.set(key, holder, nx=True, ex=BUSY_TTL_SECONDS):
            raise ApiError(409, "CHECK_IN_PROGRESS", "这个连接正在同步模型或检测，请等它完成后再试")
        try:
            retry = await WindowLimiter(self.redis, 60).hit(
                f"model_connection_probe:{access.user_id}:{connection_id}", PROBES_PER_MINUTE
            )
            if retry is not None:
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    f"每个连接每分钟最多同步或检测 {PROBES_PER_MINUTE} 次，请稍后再试",
                    headers={"Retry-After": str(retry)},
                )
            yield
        finally:
            await self.redis.eval(_RELEASE, 1, key, holder)  # type: ignore[misc]

    # ---------------------------------------------------------------- 模型同步

    async def sync_models(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        body: ModelSyncRequest,
        *,
        client_ip: str,
    ) -> ModelSyncResult:
        connection = await self.connections.connection(access, connection_id)
        self.connections.check_version(connection, body.version)
        protocol, base_url = self._ensure_checkable(connection)
        revision = connection.config_revision
        async with self._slot(access, connection.id):
            api_key = await self._api_key(connection)
            # 结束读事务：访问服务商期间不占数据库事务和行锁
            await self.db.commit()
            if api_key is None:
                listing = ModelListing(ProbeResult("failed", "credential_unreadable"))
            else:
                listing = await self._adapter(protocol).list_models(
                    base_url=base_url, api_key=api_key
                )
            del api_key
            connection = await self._reload(access, connection_id)
            stale = connection.config_revision != revision
            attempt: SyncState = "failed" if stale else _attempt_state(listing)
            added = removed = 0
            kept_in_use: list[str] = []
            # stale：期间更换了 Key、改了地址或模型集合，这次的结果属于旧配置，丢弃不写
            if not stale and attempt == "complete":
                added, removed, kept_in_use = await self._apply_snapshot(
                    connection, list(listing.model_ids)
                )
            elif not stale and connection.sync_state not in ("complete", attempt):
                # 已有完整快照时它仍然生效，连接上保持 complete；否则记下这次的结果
                connection.sync_state = attempt
                connection.version += 1
            await self.connections.refresh_state(connection)
            await self.audit.record(
                "model_connection.models_synced",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="model_connection",
                resource_id=connection.id,
                metadata={
                    "sync_state": attempt,
                    "status": listing.result.status,
                    "reason": "stale_revision" if stale else listing.result.reason,
                    "listed": len(listing.model_ids),
                    "added": added,
                    "removed": removed,
                    "kept_in_use": len(kept_in_use),
                    "host": host_of_url(base_url),
                },
                client_ip=client_ip,
            )
            await self.db.commit()
        out = await self.connections.out(connection)
        return ModelSyncResult(
            sync_state=attempt,
            added=added,
            removed=removed,
            total=out.model_count,
            kept_previous=attempt != "complete",
            reason="stale_revision" if stale else listing.result.reason,
            retry_at=None if stale else listing.result.retry_at,
            discovered=list(connection.discovered_models or []),
            kept_in_use=kept_in_use,
            connection=out,
        )

    async def _apply_snapshot(
        self, connection: ModelConnection, snapshot: list[str]
    ) -> tuple[int, int, list[str]]:
        """保存完整快照；全部可用模型范围下按快照替换模型集合（在用的不移除）。

        返回（新增数, 移除数, 因在用而保留的模型 ID）。"""
        now = _now()
        rows = {
            m.model_id: m
            for m in (
                await self.db.scalars(
                    select(ModelConnectionModel).where(
                        ModelConnectionModel.connection_id == connection.id
                    )
                )
            ).all()
        }
        listed = set(snapshot)
        added = removed = 0
        kept_in_use: list[str] = []
        if connection.model_scope == "all_available":
            used = await self.connections.models_in_use(connection.id)
            gone = set(rows) - listed
            kept_in_use = sorted(m for m in gone if m in used)
            drop = gone - set(kept_in_use)
            if drop:
                await self.db.execute(
                    delete(ModelConnectionModel).where(
                        ModelConnectionModel.connection_id == connection.id,
                        ModelConnectionModel.model_id.in_(drop),
                    )
                )
            removed = len(drop)
            for model_id in snapshot:
                row = rows.get(model_id)
                if row is None:
                    self.db.add(
                        ModelConnectionModel(
                            workspace_id=connection.workspace_id,
                            connection_id=connection.id,
                            model_id=model_id,
                            display_name="",
                            source="discovered",
                            availability="listed",
                            last_seen_at=now,
                            created_at=now,
                        )
                    )
                    added += 1
                else:
                    row.last_seen_at = now
        else:
            # 指定模型：只记录哪些已选模型还在服务商的列表里，不增删
            for model_id, row in rows.items():
                if model_id in listed:
                    row.last_seen_at = now
        connection.discovered_models = snapshot
        connection.models_synced_at = now
        connection.sync_state = "complete"
        if added or removed:
            connection.config_revision += 1
            connection.last_check_id = None
        connection.version += 1
        await self.db.flush()
        return added, removed, kept_in_use

    # ---------------------------------------------------------------- 检测

    async def check_connection(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        body: ConnectionCheckRequest,
        *,
        client_ip: str,
    ) -> ConnectionCheckOut:
        connection = await self.connections.connection(access, connection_id)
        self.connections.check_version(connection, body.version)
        protocol, base_url = self._ensure_checkable(connection)
        model_id = await self._checked_model(connection, body)
        revision = connection.config_revision
        async with self._slot(access, connection.id):
            api_key = await self._api_key(connection)
            # 结束读事务：访问服务商期间不占数据库事务和行锁
            await self.db.commit()
            if api_key is None:
                result = ProbeResult("failed", "credential_unreadable")
            elif model_id is None:
                adapter = self._adapter(protocol)
                result = (await adapter.list_models(base_url=base_url, api_key=api_key)).result
            else:
                adapter = self._adapter(protocol)
                result = await adapter.infer(base_url=base_url, api_key=api_key, model_id=model_id)
            del api_key
            connection = await self._reload(access, connection_id)
            stale = connection.config_revision != revision
            row = ModelConnectionCheck(
                id=uuid.uuid4(),
                workspace_id=connection.workspace_id,
                connection_id=connection.id,
                config_revision=revision,
                mode=body.mode,
                model_id=model_id,
                # 结果属于旧配置：照样留记录，但不当作当前配置的检测结论
                status="unknown" if stale else result.status,
                reason="stale_revision" if stale else result.reason,
                checked_at=_now(),
                latency_ms=result.latency_ms,
                retry_at=None if stale else result.retry_at,
                actor_id=access.user_id,
            )
            self.db.add(row)
            await self.db.flush()
            if not stale:
                # 检测结果不是用户配置：不增加连接的 version（页面上的编辑不会因此冲突）
                connection.last_check_id = row.id
                if model_id is not None and result.passed:
                    await self._mark_verified(connection.id, model_id)
            await self.audit.record(
                "model_connection.checked",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="model_connection",
                resource_id=connection.id,
                metadata={
                    "mode": body.mode,
                    "model_id": model_id,
                    "status": row.status,
                    "reason": row.reason,
                    "config_revision": revision,
                    "host": host_of_url(base_url),
                },
                client_ip=client_ip,
            )
            await self.db.commit()
        return check_out(row)

    async def _checked_model(
        self, connection: ModelConnection, body: ConnectionCheckRequest
    ) -> str | None:
        """推理检测必须选连接明确模型集合里的一个模型；metadata 检测不带模型。"""
        if body.mode == "metadata":
            if body.model_id is not None:
                raise ApiError(
                    422,
                    "VALIDATION_FAILED",
                    "元数据检测不针对具体模型，不要填 model_id",
                    details={
                        "errors": [
                            {
                                "loc": ["body", "model_id"],
                                "msg": "metadata 检测不接受 model_id",
                                "type": "value_error",
                            }
                        ]
                    },
                )
            return None
        if body.model_id is None:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "推理检测要选择一个模型",
                details={
                    "errors": [
                        {
                            "loc": ["body", "model_id"],
                            "msg": "inference 检测必须填 model_id",
                            "type": "value_error",
                        }
                    ]
                },
            )
        exists = await self.db.scalar(
            select(ModelConnectionModel.id).where(
                ModelConnectionModel.connection_id == connection.id,
                ModelConnectionModel.model_id == body.model_id,
            )
        )
        if exists is None:
            raise ApiError(
                422,
                "MODEL_NOT_ALLOWED",
                "这个模型不在连接的模型集合里；请先把它加入模型集合",
            )
        return body.model_id

    async def _mark_verified(self, connection_id: uuid.UUID, model_id: str) -> None:
        row = await self.db.scalar(
            select(ModelConnectionModel)
            .where(
                ModelConnectionModel.connection_id == connection_id,
                ModelConnectionModel.model_id == model_id,
            )
            .with_for_update()
        )
        if row is not None and row.availability != "verified":
            row.availability = "verified"
            row.version += 1
