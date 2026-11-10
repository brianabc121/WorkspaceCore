"""中转站管理：模型连接的增删改查、汇总、Key 更换、模型集合、授权、活动与迁移分类
（T-0201 / T-0203）。

设计与约定：docs/v2/model-connections/、docs/decisions/2026-11-09-model-connections.md。

- 连接的 API Key 存在 agent_secrets / agent_secret_versions
  （加密用途绑定 secret_id + value_version），
  新建连接时建一条内部凭据；从旧密钥库导入的连接直接引用原密钥（不复制密文、不换 ID）；
- 任何输出都不含 Key 明文、密文、credential_ref 或内部 secret_id；
- 已配置完整、且有授权或绑定的连接不能改 URL / 协议（复制为新连接再切换）；
  旧导入项第一次从空补齐例外；
- 停用会影响实例时先返回影响摘要，确认后才生效；停用后旧取密钥接口、新取密钥接口与 Runner 注入
  都拒绝这条凭据（见 blocked_secret_ids）；
- 撤销某实例的授权时，同一事务里撤掉这条凭据对它的旧授权（AgentSecretGrant），分类、迁移不会恢复。
"""

import base64
import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import idempotency
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.models import (
    AgentInstance,
    AgentModelBinding,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    AuditLog,
    ModelApplyJob,
    ModelConnection,
    ModelConnectionCheck,
    ModelConnectionGrant,
    ModelConnectionModel,
)
from app.schemas.model_connections import (
    CHECK_FRESH_SECONDS,
    MAX_MODELS_PER_CONNECTION,
    ConnectionActivityOut,
    ConnectionActivityPage,
    ConnectionCheckOut,
    ConnectionGrantPage,
    ConnectionModelOut,
    ConnectionModelPage,
    GrantInstanceOut,
    GrantsReplace,
    ImpactOut,
    MigrationClassify,
    MigrationEntryOut,
    MigrationEntryPage,
    ModelConnectionCapabilities,
    ModelConnectionCreate,
    ModelConnectionKeyRotate,
    ModelConnectionOut,
    ModelConnectionPage,
    ModelConnectionSummary,
    ModelConnectionUpdate,
    ModelInput,
    ModelsReplace,
    OfficialPreset,
)

# ---------------------------------------------------------------- 协议与官方预设

# 已核验、开放给页面选择的协议
# （MC-0 核验记录：docs/decisions/2026-11-09-model-connections.md 第 2 节）。
# 未核验的协议不出现在 capabilities 里，创建 / 修改时选它返回 422 MODEL_PROTOCOL_UNSUPPORTED。
SUPPORTED_PROTOCOLS: tuple[str, ...] = ("openai_compatible",)

# OpenClaw models.providers.<key>.api 的取值（核验记录第 2 节）
OPENCLAW_API: dict[str, str] = {
    "openai_compatible": "openai-completions",
    "anthropic_native": "anthropic-messages",
    "gemini_native": "google-generative-ai",
}

# 官方预设只填经核验的协议与 URL，不代填 Key（核验记录第 3 节，附官方文档地址）
OFFICIAL_PRESETS: tuple[OfficialPreset, ...] = (
    OfficialPreset(
        provider="openai",
        label="OpenAI 官方",
        protocol="openai_compatible",
        base_url="https://api.openai.com/v1",
        source="https://platform.openai.com/docs/api-reference/introduction",
    ),
)

# 单次请求地址的路径结尾：base URL 不能带这些
# （否则 OpenClaw 会拼出 /chat/completions/chat/completions）
_REQUEST_SUFFIXES = (
    "/chat/completions",
    "/completions",
    "/responses",
    "/messages",
    "/models",
    ":generatecontent",
    ":streamgeneratecontent",
)

PAGE_DEFAULT = 50
PAGE_MAX = 100
CHECK_FRESH = timedelta(seconds=CHECK_FRESH_SECONDS)


def provider_key(connection_id: uuid.UUID) -> str:
    """OpenClaw 配置里 models.providers 的键：由连接 ID 生成，改名不影响运行时。"""
    return f"mwc{connection_id.hex[:20]}"


def secret_ref_id(connection_id: uuid.UUID) -> str:
    """apiKey 的 exec SecretRef id：mc_<连接 ID>。

    与旧密钥库的 ref_id 不会冲突：旧 ref_id 不能以 mc_ 开头。"""
    return f"mc_{connection_id.hex}"


SECRET_REF_PREFIX = "mc_"  # noqa: S105 - 引用名前缀，不是密钥


def connection_id_from_ref(ref: str) -> uuid.UUID | None:
    if not ref.startswith(SECRET_REF_PREFIX) or len(ref) != len(SECRET_REF_PREFIX) + 32:
        return None
    try:
        return uuid.UUID(hex=ref[len(SECRET_REF_PREFIX) :])
    except ValueError:
        return None


def supported_protocols(settings: Settings) -> tuple[str, ...]:
    extra = tuple(
        p.strip()
        for p in getattr(settings, "model_connection_extra_protocols", "").split(",")
        if p.strip() in OPENCLAW_API
    )
    return tuple(dict.fromkeys(SUPPORTED_PROTOCOLS + extra))


def _invalid(field: str, message: str, code: str = "VALIDATION_FAILED") -> ApiError:
    return ApiError(
        422,
        code,
        message,
        details={"errors": [{"loc": ["body", field], "msg": message, "type": "value_error"}]},
    )


def normalize_base_url(raw: str, *, allowed_hosts: frozenset[str]) -> str:
    """规范化并校验 base URL：HTTPS（部署允许的测试主机可以用 http）、不含用户名密码 / 查询 / 片段 /
    控制字符，去掉尾斜杠，主机名小写。"""
    text = raw.strip()
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in text):
        raise _invalid("base_url", "地址里不能有空白或控制字符", "MODEL_BASE_URL_INVALID")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise _invalid("base_url", "地址格式不正确", "MODEL_BASE_URL_INVALID") from error
    host = (parts.hostname or "").lower()
    scheme = parts.scheme.lower()
    if not host or scheme not in ("https", "http"):
        raise _invalid("base_url", "地址要以 https:// 开头", "MODEL_BASE_URL_INVALID")
    if scheme == "http" and host not in allowed_hosts:
        raise _invalid("base_url", "只支持 HTTPS 地址", "MODEL_BASE_URL_INVALID")
    if parts.username or parts.password or "@" in parts.netloc:
        raise _invalid("base_url", "地址里不能带用户名或密码", "MODEL_BASE_URL_INVALID")
    if parts.query or parts.fragment or "?" in text or "#" in text:
        raise _invalid(
            "base_url",
            "地址里不能带查询参数或 # 片段（Key 不能放在地址里）",
            "MODEL_BASE_URL_INVALID",
        )
    path = parts.path.rstrip("/")
    if path.lower().endswith(_REQUEST_SUFFIXES):
        raise _invalid(
            "base_url",
            "请填写协议基地址（例如 https://api.openai.com/v1），"
            "不要带 /chat/completions 等请求路径",
            "MODEL_BASE_URL_INVALID",
        )
    netloc = host if port is None else f"{host}:{port}"
    if ":" in host:  # IPv6 字面量
        netloc = f"[{host}]" if port is None else f"[{host}]:{port}"
    return urlunsplit((scheme, netloc, path, "", ""))


def host_of_url(url: str | None) -> str | None:
    if not url:
        return None
    return (urlsplit(url).hostname or "").lower() or None


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "连接不存在")


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "数据已被修改，请刷新后再试",
        details={"current_version": current},
    )


def _cursor_key(*parts: object) -> str:
    return hashlib.sha256(json.dumps([str(p) for p in parts]).encode()).hexdigest()[:16]


def _encode_cursor(offset: int, key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset, "k": key}).encode()).decode()


def _decode_cursor(cursor: str | None, key: str) -> int:
    if cursor is None:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        offset, cursor_key = int(data["o"]), str(data["k"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise _invalid("cursor", "无效的游标，请从第一页重新加载") from error
    if cursor_key != key or offset < 0:
        raise _invalid("cursor", "筛选条件已变化，请从第一页重新加载")
    return offset


def _like(text: str) -> str:
    if "\x00" in text:
        raise _invalid("q", "关键词里不能有 NUL 字符")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass
class _Stats:
    models: dict[uuid.UUID, int]
    grants: dict[uuid.UUID, int]
    bindings: dict[uuid.UUID, int]
    credential: dict[uuid.UUID, tuple[bool, int]]
    checks: dict[uuid.UUID, ModelConnectionCheck]


def check_out(row: ModelConnectionCheck) -> ConnectionCheckOut:
    return ConnectionCheckOut(
        id=row.id,
        connection_id=row.connection_id,
        config_revision=row.config_revision,
        mode=row.mode,
        model_id=row.model_id,
        status=row.status,
        reason=row.reason,
        checked_at=row.checked_at,
        latency_ms=row.latency_ms,
        retry_at=row.retry_at,
    )


def compute_state(
    connection: ModelConnection,
    *,
    model_count: int,
    credential_ok: bool,
    protocols: Iterable[str],
) -> str:
    """配置完整度：needs_review（Key 不可读）/ ready / draft。"""
    if not credential_ok:
        return "needs_review"
    scope_ok = model_count > 0 and (
        connection.model_scope == "selected" or connection.sync_state == "complete"
    )
    if (
        connection.connection_type is not None
        and connection.protocol in tuple(protocols)
        and connection.base_url
        and scope_ok
        and connection.migration_state != "non_model"
    ):
        return "ready"
    return "draft"


async def blocked_secret_ids(db: AsyncSession, secret_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """被连接托管、且连接已被管理员停用的凭据。旧取密钥接口与 Runner 注入都要拒绝它们。"""
    ids = list(set(secret_ids))
    if not ids:
        return set()
    rows = await db.scalars(
        select(ModelConnection.credential_secret_id).where(
            ModelConnection.credential_secret_id.in_(ids), ModelConnection.enabled.is_(False)
        )
    )
    return set(rows.all())


class ModelConnectionService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 内部

    def _box(self) -> CredentialBox:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥") from error

    @property
    def protocols(self) -> tuple[str, ...]:
        return supported_protocols(self.settings)

    def _allowed_hosts(self) -> frozenset[str]:
        return self.settings.allowed_integration_hosts

    async def connection(
        self, access: WorkspaceAccess, connection_id: uuid.UUID, *, lock: bool = False
    ) -> ModelConnection:
        query = select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.workspace_id == access.workspace.id,
        )
        if lock:
            query = query.with_for_update()
        row = await self.db.scalar(query)
        if row is None:
            raise _not_found()
        return row

    @staticmethod
    def check_version(connection: ModelConnection, version: int) -> None:
        if connection.version != version:
            raise _version_conflict(connection.version)

    async def _stats(self, connections: Sequence[ModelConnection]) -> _Stats:
        ids = [c.id for c in connections]
        if not ids:
            return _Stats({}, {}, {}, {}, {})
        models = dict(
            (
                await self.db.execute(
                    select(ModelConnectionModel.connection_id, func.count())
                    .where(ModelConnectionModel.connection_id.in_(ids))
                    .group_by(ModelConnectionModel.connection_id)
                )
            ).all()
        )
        grants = dict(
            (
                await self.db.execute(
                    select(ModelConnectionGrant.connection_id, func.count())
                    .where(ModelConnectionGrant.connection_id.in_(ids))
                    .group_by(ModelConnectionGrant.connection_id)
                )
            ).all()
        )
        bindings: dict[uuid.UUID, set[uuid.UUID]] = {}
        for instance_id, target, applied in (
            await self.db.execute(
                select(
                    AgentModelBinding.instance_id,
                    AgentModelBinding.connection_id,
                    AgentModelBinding.applied_connection_id,
                ).where(
                    or_(
                        AgentModelBinding.connection_id.in_(ids),
                        AgentModelBinding.applied_connection_id.in_(ids),
                    )
                )
            )
        ).all():
            for cid in (target, applied):
                if cid in ids:
                    bindings.setdefault(cid, set()).add(instance_id)
        secret_ids = {c.credential_secret_id: c.id for c in connections}
        credential: dict[uuid.UUID, tuple[bool, int]] = {}
        for secret_id, current_version, ciphertext in (
            await self.db.execute(
                select(
                    AgentSecret.id, AgentSecret.current_version, AgentSecretVersion.ciphertext_ref
                )
                .outerjoin(
                    AgentSecretVersion,
                    (AgentSecretVersion.secret_id == AgentSecret.id)
                    & (AgentSecretVersion.value_version == AgentSecret.current_version),
                )
                .where(AgentSecret.id.in_(secret_ids))
            )
        ).all():
            credential[secret_ids[secret_id]] = (bool(ciphertext), current_version)
        check_ids = [c.last_check_id for c in connections if c.last_check_id]
        checks: dict[uuid.UUID, ModelConnectionCheck] = {}
        if check_ids:
            for row in (
                await self.db.scalars(
                    select(ModelConnectionCheck).where(ModelConnectionCheck.id.in_(check_ids))
                )
            ).all():
                checks[row.connection_id] = row
        return _Stats(
            models=models,
            grants=grants,
            bindings={k: len(v) for k, v in bindings.items()},
            credential=credential,
            checks=checks,
        )

    def _out(self, connection: ModelConnection, stats: _Stats) -> ModelConnectionOut:
        configured, credential_version = stats.credential.get(connection.id, (False, 0))
        check = stats.checks.get(connection.id)
        if check is not None and check.config_revision != connection.config_revision:
            check = None
        return ModelConnectionOut(
            id=connection.id,
            workspace_id=connection.workspace_id,
            name=connection.name,
            description=connection.description,
            connection_type=connection.connection_type,
            official_provider=connection.official_provider,
            protocol=connection.protocol,
            base_url=connection.base_url,
            credential_configured=configured and connection.configuration_state != "needs_review",
            credential_version=credential_version,
            model_scope=connection.model_scope,
            configuration_state=connection.configuration_state,
            enabled=connection.enabled,
            migration_state=connection.migration_state,
            legacy_source_id=connection.legacy_source_id,
            model_count=stats.models.get(connection.id, 0),
            grant_count=stats.grants.get(connection.id, 0),
            binding_count=stats.bindings.get(connection.id, 0),
            config_revision=connection.config_revision,
            version=connection.version,
            sync_state=connection.sync_state,
            models_synced_at=connection.models_synced_at,
            last_check=check_out(check) if check is not None else None,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )

    async def outs(self, connections: Sequence[ModelConnection]) -> list[ModelConnectionOut]:
        stats = await self._stats(connections)
        return [self._out(c, stats) for c in connections]

    async def out(self, connection: ModelConnection) -> ModelConnectionOut:
        await self.db.refresh(connection)
        return (await self.outs([connection]))[0]

    async def model_count(self, connection_id: uuid.UUID) -> int:
        return int(
            await self.db.scalar(
                select(func.count()).where(ModelConnectionModel.connection_id == connection_id)
            )
            or 0
        )

    async def credential_readable(self, connection: ModelConnection) -> bool:
        secret = await self.db.get(AgentSecret, connection.credential_secret_id)
        if secret is None:
            return False
        ref = await self.db.scalar(
            select(AgentSecretVersion.ciphertext_ref).where(
                AgentSecretVersion.secret_id == secret.id,
                AgentSecretVersion.value_version == secret.current_version,
            )
        )
        if not ref:
            return False
        try:
            self._box().resolve(ref, context=_context(secret.id, secret.current_version))
        except (CredentialError, ApiError):
            return False
        return True

    async def refresh_state(
        self, connection: ModelConnection, *, credential_ok: bool | None = None
    ) -> None:
        """重新计算配置完整度；变成 ready 的导入项转为 managed。"""
        if credential_ok is None:
            credential_ok = connection.configuration_state != "needs_review"
        connection.configuration_state = compute_state(
            connection,
            model_count=await self.model_count(connection.id),
            credential_ok=credential_ok,
            protocols=self.protocols,
        )
        if (
            connection.configuration_state == "ready"
            and connection.migration_state == "needs_configuration"
        ):
            connection.migration_state = "managed"

    async def impact(self, connection: ModelConnection) -> ImpactOut:
        grants = await self._grant_rows(connection.id, offset=0, limit=PAGE_MAX)
        bound = await self._bound_instances(connection.id)
        jobs = int(
            await self.db.scalar(
                select(func.count())
                .select_from(ModelApplyJob)
                .join(AgentModelBinding, AgentModelBinding.instance_id == ModelApplyJob.instance_id)
                .where(
                    ModelApplyJob.state.in_(("queued", "running")),
                    or_(
                        AgentModelBinding.connection_id == connection.id,
                        AgentModelBinding.applied_connection_id == connection.id,
                    ),
                )
            )
            or 0
        )
        return ImpactOut(instances=grants, bound_instances=len(bound), pending_jobs=jobs)

    async def _bound_instances(self, connection_id: uuid.UUID) -> set[uuid.UUID]:
        rows = await self.db.scalars(
            select(AgentModelBinding.instance_id).where(
                or_(
                    AgentModelBinding.connection_id == connection_id,
                    AgentModelBinding.applied_connection_id == connection_id,
                )
            )
        )
        return set(rows.all())

    async def _grant_rows(
        self, connection_id: uuid.UUID, *, offset: int, limit: int
    ) -> list[GrantInstanceOut]:
        bound = await self._bound_instances(connection_id)
        rows = (
            await self.db.execute(
                select(ModelConnectionGrant.created_at, AgentInstance)
                .join(AgentInstance, AgentInstance.id == ModelConnectionGrant.instance_id)
                .where(ModelConnectionGrant.connection_id == connection_id)
                .order_by(AgentInstance.name, AgentInstance.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [
            GrantInstanceOut(
                instance_id=instance.id,
                instance_name=instance.name,
                instance_status=instance.status,
                created_at=created_at,
                bound=instance.id in bound,
            )
            for created_at, instance in rows
        ]

    # ---------------------------------------------------------------- 读

    def capabilities(self) -> ModelConnectionCapabilities:
        protocols = self.protocols
        return ModelConnectionCapabilities(
            enabled=True,
            supported_protocols=list(protocols),
            official_presets=[p for p in OFFICIAL_PRESETS if p.protocol in protocols],
            limits={
                "max_models": MAX_MODELS_PER_CONNECTION,
                "check_per_minute": 5,
                "page_max": PAGE_MAX,
                "check_fresh_seconds": CHECK_FRESH_SECONDS,
            },
        )

    def _filtered(
        self,
        access: WorkspaceAccess,
        *,
        q: str | None,
        connection_type: str | None,
        configuration_state: str | None,
        enabled: bool | None,
        check_status: str | None,
    ) -> Any:
        conditions: list[Any] = [
            ModelConnection.workspace_id == access.workspace.id,
            ModelConnection.migration_state != "non_model",
        ]
        if q:
            pattern = _like(q.strip())
            conditions.append(
                or_(
                    ModelConnection.name.ilike(pattern, escape="\\"),
                    ModelConnection.base_url.ilike(pattern, escape="\\"),
                    select(ModelConnectionModel.id)
                    .where(
                        ModelConnectionModel.connection_id == ModelConnection.id,
                        ModelConnectionModel.model_id.ilike(pattern, escape="\\"),
                    )
                    .exists(),
                )
            )
        if connection_type:
            conditions.append(ModelConnection.connection_type == connection_type)
        if configuration_state:
            conditions.append(ModelConnection.configuration_state == configuration_state)
        if enabled is not None:
            conditions.append(ModelConnection.enabled.is_(enabled))
        if check_status:
            last = (
                select(ModelConnectionCheck.status)
                .where(
                    ModelConnectionCheck.id == ModelConnection.last_check_id,
                    ModelConnectionCheck.config_revision == ModelConnection.config_revision,
                )
                .scalar_subquery()
            )
            if check_status == "never":
                conditions.append(last.is_(None))
            else:
                conditions.append(last == check_status)
        return and_(*conditions)

    async def list_connections(
        self,
        access: WorkspaceAccess,
        *,
        q: str | None,
        connection_type: str | None,
        configuration_state: str | None,
        enabled: bool | None,
        check_status: str | None,
        cursor: str | None,
        limit: int,
    ) -> ModelConnectionPage:
        key = _cursor_key(
            access.workspace.id, q, connection_type, configuration_state, enabled, check_status
        )
        offset = _decode_cursor(cursor, key)
        where = self._filtered(
            access,
            q=q,
            connection_type=connection_type,
            configuration_state=configuration_state,
            enabled=enabled,
            check_status=check_status,
        )
        total = int(
            await self.db.scalar(select(func.count()).select_from(ModelConnection).where(where))
            or 0
        )
        rows = (
            await self.db.scalars(
                select(ModelConnection)
                .where(where)
                .order_by(ModelConnection.updated_at.desc(), ModelConnection.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        page = list(rows[:limit])
        return ModelConnectionPage(
            items=await self.outs(page),
            next_cursor=_encode_cursor(offset + limit, key) if len(rows) > limit else None,
            total=total,
        )

    async def summary(self, access: WorkspaceAccess) -> ModelConnectionSummary:
        connections = (
            await self.db.scalars(
                select(ModelConnection).where(ModelConnection.workspace_id == access.workspace.id)
            )
        ).all()
        stats = await self._stats(connections)
        now = _now()
        total = passed = passed_inference = attention = non_model = 0
        for c in connections:
            if c.migration_state == "non_model":
                non_model += 1
                continue
            total += 1
            check = stats.checks.get(c.id)
            current = check is not None and check.config_revision == c.config_revision
            fresh_pass = (
                current
                and check is not None
                and check.status == "passed"
                and now - check.checked_at <= CHECK_FRESH
            )
            if c.configuration_state == "ready" and c.enabled and fresh_pass:
                passed += 1
                if check is not None and check.mode == "inference":
                    passed_inference += 1
            if (
                c.configuration_state != "ready"
                or c.migration_state in ("needs_configuration", "needs_classification")
                or (current and check is not None and check.status in ("failed", "rate_limited"))
            ):
                attention += 1
        granted = int(
            await self.db.scalar(
                select(func.count(func.distinct(ModelConnectionGrant.instance_id)))
                .join(ModelConnection, ModelConnection.id == ModelConnectionGrant.connection_id)
                .where(
                    ModelConnectionGrant.workspace_id == access.workspace.id,
                    ModelConnection.migration_state != "non_model",
                )
            )
            or 0
        )
        return ModelConnectionSummary(
            total=total,
            check_passed=passed,
            check_passed_inference=passed_inference,
            needs_attention=attention,
            unique_granted_instances=granted,
            non_model=non_model,
        )

    async def get(self, access: WorkspaceAccess, connection_id: uuid.UUID) -> ModelConnectionOut:
        return (await self.outs([await self.connection(access, connection_id)]))[0]

    # ---------------------------------------------------------------- 校验

    def _check_endpoint(
        self,
        *,
        connection_type: str | None,
        official_provider: str | None,
        protocol: str | None,
        base_url: str | None,
    ) -> str | None:
        """校验类型 / 官方标识 / 协议 / URL 的组合，返回规范化后的 URL。"""
        if protocol is not None and protocol not in self.protocols:
            raise _invalid("protocol", "这个协议还没有核验开放", "MODEL_PROTOCOL_UNSUPPORTED")
        url = (
            normalize_base_url(base_url, allowed_hosts=self._allowed_hosts()) if base_url else None
        )
        if official_provider is not None:
            if connection_type != "official":
                raise _invalid("official_provider", "官方标识只能用于“官方直连”")
            preset = next((p for p in OFFICIAL_PRESETS if p.provider == official_provider), None)
            if preset is None or preset.protocol not in self.protocols:
                raise _invalid(
                    "official_provider", "这个官方预设还没有核验开放", "MODEL_PROTOCOL_UNSUPPORTED"
                )
            if protocol is not None and protocol != preset.protocol:
                raise _invalid("protocol", f"{preset.label}的协议是 {preset.protocol}")
            if url is not None and host_of_url(url) != host_of_url(preset.base_url):
                raise _invalid(
                    "base_url",
                    f"地址不是{preset.label}的主机；第三方地址请把类型改为“第三方中转”",
                    "MODEL_OFFICIAL_HOST_MISMATCH",
                )
        return url

    def _clean_models(self, models: Sequence[ModelInput]) -> list[ModelInput]:
        seen: set[str] = set()
        out: list[ModelInput] = []
        for m in models:
            if m.model_id in seen:
                raise _invalid("models", f"模型 ID 重复：{m.model_id}")
            seen.add(m.model_id)
            out.append(m)
        return out

    # ---------------------------------------------------------------- 写

    async def create(
        self,
        access: WorkspaceAccess,
        body: ModelConnectionCreate,
        *,
        idempotency_key: uuid.UUID | None,
        path: str,
        client_ip: str,
    ) -> tuple[ModelConnectionOut, bool]:
        """新建连接：元数据与加密凭据在同一个事务里创建。带 Idempotency-Key 时重发不重复创建。"""
        record = None
        if idempotency_key is not None and access.user_id is not None:
            fingerprint = idempotency.fingerprint(
                access.workspace.id,
                "POST",
                path,
                {
                    **body.model_dump(mode="json", exclude={"api_key"}),
                    "api_key_sha256": hashlib.sha256(
                        body.api_key.get_secret_value().encode()
                    ).hexdigest(),
                },
            )
            begun = await idempotency.begin(
                self.db,
                workspace_id=access.workspace.id,
                actor_id=access.user_id,
                key=idempotency_key,
                request_fingerprint=fingerprint,
                context={"op": "model_connection.create"},
            )
            if begun.state == "replay":
                existing_id = begun.record.context_json.get("connection_id")
                if begun.record.status == "failed" or not existing_id:
                    idempotency.replay(begun.record)
                return await self.get(access, uuid.UUID(existing_id)), True
            record = begun.record
        try:
            out = await self._create(access, body, client_ip=client_ip, record=record)
        except ApiError as error:
            await self.db.rollback()
            if record is not None:
                await self._record_failure(access, idempotency_key, error)
            raise
        return out, False

    async def _record_failure(
        self, access: WorkspaceAccess, key: uuid.UUID | None, error: ApiError
    ) -> None:
        if key is None or access.user_id is None:
            return
        from app.repositories import integrations as integration_repo

        record = await integration_repo.get_idempotency(
            self.db, actor_id=access.user_id, key=key, lock=True
        )
        if record is not None:
            idempotency.fail(record, error)
            await self.db.commit()

    async def _create(
        self,
        access: WorkspaceAccess,
        body: ModelConnectionCreate,
        *,
        client_ip: str,
        record: Any,
    ) -> ModelConnectionOut:
        url = self._check_endpoint(
            connection_type=body.connection_type,
            official_provider=body.official_provider,
            protocol=body.protocol,
            base_url=body.base_url,
        )
        models = self._clean_models(body.models)
        if body.model_scope == "all_available" and models:
            raise _invalid("models", "“服务商全部可用模型”要先同步模型列表；新建时请用“指定模型”")
        if await self.db.scalar(
            select(ModelConnection.id).where(
                ModelConnection.workspace_id == access.workspace.id,
                ModelConnection.name == body.name,
            )
        ):
            raise ApiError(409, "MODEL_CONNECTION_EXISTS", "已有同名的连接")
        secret_id = uuid.uuid4()
        connection_id = uuid.uuid4()
        sealed = self._box().seal(body.api_key.get_secret_value(), context=_context(secret_id, 1))
        # 内部凭据：名字固定由连接 ID 生成，不在旧密钥库里重名
        self.db.add(
            AgentSecret(
                id=secret_id,
                workspace_id=access.workspace.id,
                name=f"model-connection-{connection_id.hex[:12]}",
                kind="model_api_key",
                description="中转站管理的连接凭据（由连接托管）",
                current_version=1,
            )
        )
        await self.db.flush()
        self.db.add(
            AgentSecretVersion(
                workspace_id=access.workspace.id,
                secret_id=secret_id,
                value_version=1,
                ciphertext_ref=sealed,
            )
        )
        connection = ModelConnection(
            id=connection_id,
            workspace_id=access.workspace.id,
            name=body.name,
            description=body.description,
            connection_type=body.connection_type,
            official_provider=body.official_provider,
            protocol=body.protocol,
            base_url=url,
            credential_secret_id=secret_id,
            model_scope=body.model_scope,
            configuration_state="draft",
            enabled=False,
            migration_state="native",
        )
        self.db.add(connection)
        await self.db.flush()
        now = _now()
        for m in models:
            self.db.add(
                ModelConnectionModel(
                    workspace_id=access.workspace.id,
                    connection_id=connection_id,
                    model_id=m.model_id,
                    display_name=m.display_name or "",
                    source="manual",
                    availability="listed",
                    last_seen_at=None,
                    created_at=now,
                )
            )
        await self.db.flush()
        await self.refresh_state(connection, credential_ok=True)
        if not body.save_as_draft:
            if connection.configuration_state != "ready":
                raise ApiError(
                    422,
                    "MODEL_CONNECTION_NOT_READY",
                    "连接还没有配置完整：需要类型、协议、URL 与至少一个模型；也可以保存为草稿",
                )
            connection.enabled = True
        await self.audit.record(
            "model_connection.created",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection_id,
            metadata={
                "name": body.name,
                "connection_type": body.connection_type,
                "protocol": body.protocol,
                "host": host_of_url(url),
                "model_count": len(models),
                "draft": body.save_as_draft,
            },
            client_ip=client_ip,
        )
        if record is not None:
            record.context_json = {**record.context_json, "connection_id": str(connection_id)}
            idempotency.succeed(record, 201, {"connection_id": str(connection_id)})
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ApiError(409, "MODEL_CONNECTION_EXISTS", "已有同名的连接") from None
        return await self.out(connection)

    async def update(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        body: ModelConnectionUpdate,
        *,
        client_ip: str,
    ) -> ModelConnectionOut:
        connection = await self.connection(access, connection_id, lock=True)
        self.check_version(connection, body.version)
        changes = body.model_dump(exclude_unset=True, exclude={"version", "confirm_impact"})
        endpoint_fields = {"connection_type", "official_provider", "protocol", "base_url"}
        touched_endpoint = endpoint_fields & changes.keys()
        new = {f: changes.get(f, getattr(connection, f)) for f in endpoint_fields}
        url = self._check_endpoint(
            connection_type=new["connection_type"],
            official_provider=new["official_provider"],
            protocol=new["protocol"],
            base_url=new["base_url"],
        )
        new["base_url"] = url
        target_changed = (new["base_url"] != connection.base_url) or (
            new["protocol"] != connection.protocol
        )
        if target_changed:
            await self._guard_endpoint_change(connection)
        if "name" in changes and changes["name"] is None:
            raise _invalid("name", "名称不能为空")
        if "description" in changes and changes["description"] is None:
            changes["description"] = ""
        audit_fields: list[str] = []
        for field in ("name", "description"):
            if field in changes and changes[field] != getattr(connection, field):
                setattr(connection, field, changes[field])
                audit_fields.append(field)
        if touched_endpoint:
            for field in endpoint_fields:
                if new[field] != getattr(connection, field):
                    setattr(connection, field, new[field])
                    audit_fields.append(field)
            if target_changed:
                connection.config_revision += 1
                connection.last_check_id = None
                # 地址或协议变了，旧的模型目录不再可信，需要重新同步
                if connection.model_scope == "all_available":
                    connection.sync_state = None
        enable_change: bool | None = changes.get("enabled")
        if enable_change is not None and enable_change != connection.enabled:
            if enable_change:
                await self.refresh_state(connection)
                if (
                    connection.configuration_state != "ready"
                    and connection.legacy_source_id is None
                ):
                    raise ApiError(
                        409, "MODEL_CONNECTION_NOT_READY", "连接还没有配置完整，不能启用"
                    )
            else:
                impact = await self.impact(connection)
                if (impact.instances or impact.bound_instances) and not body.confirm_impact:
                    raise ApiError(
                        409,
                        "CONNECTION_IN_USE",
                        "停用会影响已授权或已绑定的智能体，请确认影响范围后再提交",
                        details={"impact": impact.model_dump(mode="json")},
                    )
            connection.enabled = enable_change
            audit_fields.append("enabled")
        await self.refresh_state(connection)
        if not audit_fields:
            return await self.out(connection)
        connection.version += 1
        await self.audit.record(
            "model_connection.updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection.id,
            metadata={
                "fields": sorted(set(audit_fields)),
                **({"enabled": connection.enabled} if "enabled" in audit_fields else {}),
                **({"host": host_of_url(connection.base_url)} if target_changed else {}),
            },
            client_ip=client_ip,
        )
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ApiError(409, "MODEL_CONNECTION_EXISTS", "已有同名的连接") from None
        return await self.out(connection)

    async def _guard_endpoint_change(self, connection: ModelConnection) -> None:
        """已配置完整、且有授权或绑定的连接不能改 URL / 协议。

        例外：旧导入项第一次从空补齐 URL / 协议，且没有原生已应用的绑定
        （只存元数据，不改旧运行时）。"""
        complete = connection.base_url is not None and connection.protocol is not None
        grants = int(
            await self.db.scalar(
                select(func.count()).where(ModelConnectionGrant.connection_id == connection.id)
            )
            or 0
        )
        bound = await self._bound_instances(connection.id)
        if not complete:
            if bound:
                raise ApiError(
                    409,
                    "CONNECTION_ENDPOINT_IN_USE",
                    "这个连接已被智能体绑定，不能修改地址或协议；请复制为新连接再切换",
                )
            return
        if grants or bound:
            raise ApiError(
                409,
                "CONNECTION_ENDPOINT_IN_USE",
                "这个连接已授权给智能体，修改地址或协议会把 Key 发到新的目标；请复制为新连接再切换",
                details={"grant_count": grants, "bound_instances": len(bound)},
            )

    async def rotate_key(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        body: ModelConnectionKeyRotate,
        *,
        client_ip: str,
    ) -> ModelConnectionOut:
        """更换 Key：沿用原有加密与轮换规则（旧值不再下发、不留密文）。不会去服务商撤销旧 Key。"""
        connection = await self.connection(access, connection_id, lock=True)
        self.check_version(connection, body.version)
        if connection.migration_state == "non_model":
            raise ApiError(
                409, "MODEL_CONNECTION_NON_MODEL", "非模型凭据请在“迁移与兼容凭据”里更换"
            )
        secret = await self.db.get(
            AgentSecret, connection.credential_secret_id, with_for_update=True
        )
        if secret is None:
            raise _not_found()
        new_version = await rotate_secret_value(
            self.db, self._box(), secret, body.api_key.get_secret_value()
        )
        connection.config_revision += 1
        connection.last_check_id = None
        connection.version += 1
        await self.refresh_state(connection, credential_ok=True)
        # 绑定到它的实例马上重新应用（实例执行 secrets reload 换上新 Key，不等定时任务）
        from app.domain.agent_model_bindings import advance_bindings

        await advance_bindings(self.db, connection.id)
        await self.audit.record(
            "model_connection.key_rotated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection.id,
            metadata={"value_version": new_version},
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self.out(connection)

    async def delete(
        self, access: WorkspaceAccess, connection_id: uuid.UUID, *, version: int, client_ip: str
    ) -> None:
        connection = await self.connection(access, connection_id, lock=True)
        self.check_version(connection, version)
        impact = await self.impact(connection)
        if impact.instances or impact.bound_instances or impact.pending_jobs:
            raise ApiError(
                409,
                "CONNECTION_IN_USE",
                "连接仍有授权、绑定或进行中的应用任务，请先解除",
                details={"impact": impact.model_dump(mode="json")},
            )
        legacy_refs = int(
            await self.db.scalar(
                select(func.count()).where(
                    AgentSecretGrant.secret_id == connection.credential_secret_id
                )
            )
            or 0
        )
        if legacy_refs:
            raise ApiError(
                409,
                "CONNECTION_IN_USE",
                "这条凭据仍被旧配置或运行环境引用；请先撤销旧授权，或归类为非模型凭据保留",
                details={"legacy_grants": legacy_refs},
            )
        await self.audit.record(
            "model_connection.deleted",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection.id,
            metadata={"name": connection.name, "imported": connection.legacy_source_id is not None},
            client_ip=client_ip,
        )
        secret_id = connection.credential_secret_id
        await self.db.delete(connection)
        await self.db.flush()
        await self.db.execute(delete(AgentSecret).where(AgentSecret.id == secret_id))
        await self.db.commit()

    # ---------------------------------------------------------------- 模型集合

    async def list_models(
        self, access: WorkspaceAccess, connection_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> ConnectionModelPage:
        connection = await self.connection(access, connection_id)
        key = _cursor_key(connection.id, "models")
        offset = _decode_cursor(cursor, key)
        total = await self.model_count(connection.id)
        rows = (
            await self.db.scalars(
                select(ModelConnectionModel)
                .where(ModelConnectionModel.connection_id == connection.id)
                .order_by(ModelConnectionModel.model_id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return ConnectionModelPage(
            items=[model_out(m) for m in rows[:limit]],
            next_cursor=_encode_cursor(offset + limit, key) if len(rows) > limit else None,
            total=total,
            synced_at=connection.models_synced_at,
            sync_state=connection.sync_state,
            discovered=list(connection.discovered_models or []),
        )

    async def models_in_use(self, connection_id: uuid.UUID) -> dict[str, list[uuid.UUID]]:
        """被绑定（目标或已应用）的模型 ID → 实例。"""
        used: dict[str, list[uuid.UUID]] = {}
        for instance_id, cid, mid, acid, amid in (
            await self.db.execute(
                select(
                    AgentModelBinding.instance_id,
                    AgentModelBinding.connection_id,
                    AgentModelBinding.model_id,
                    AgentModelBinding.applied_connection_id,
                    AgentModelBinding.applied_model_id,
                ).where(
                    or_(
                        AgentModelBinding.connection_id == connection_id,
                        AgentModelBinding.applied_connection_id == connection_id,
                    )
                )
            )
        ).all():
            if cid == connection_id and mid:
                used.setdefault(mid, []).append(instance_id)
            if acid == connection_id and amid and amid != mid:
                used.setdefault(amid, []).append(instance_id)
        return used

    async def replace_models(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        body: ModelsReplace,
        *,
        client_ip: str,
    ) -> ModelConnectionOut:
        connection = await self.connection(access, connection_id, lock=True)
        self.check_version(connection, body.version)
        existing = {
            m.model_id: m
            for m in (
                await self.db.scalars(
                    select(ModelConnectionModel).where(
                        ModelConnectionModel.connection_id == connection.id
                    )
                )
            ).all()
        }
        if body.model_scope == "all_available":
            if body.models:
                raise _invalid("models", "“服务商全部可用模型”使用同步到的模型列表，不接受手工清单")
            if not connection.discovered_models:
                raise ApiError(
                    409,
                    "MODEL_SYNC_REQUIRED",
                    "还没有一次完整的模型同步；模型列表不可用时请改用“指定模型”手工填写",
                )
            wanted = set(connection.discovered_models)
            names = {mid: existing[mid].display_name if mid in existing else "" for mid in wanted}
        else:
            models = self._clean_models(body.models)
            if not models:
                raise _invalid("models", "至少需要一个模型 ID")
            wanted = {m.model_id for m in models}
            names = {m.model_id: (m.display_name or "") for m in models}
        removed = set(existing) - wanted
        used = await self.models_in_use(connection.id)
        blocked = {mid: ids for mid, ids in used.items() if mid in removed}
        if blocked:
            raise ApiError(
                409,
                "MODEL_IN_USE",
                "要移除的模型正被智能体使用，请先更换它们的主模型",
                details={
                    "models": {mid: [str(i) for i in ids] for mid, ids in sorted(blocked.items())}
                },
            )
        now = _now()
        if removed:
            await self.db.execute(
                delete(ModelConnectionModel).where(
                    ModelConnectionModel.connection_id == connection.id,
                    ModelConnectionModel.model_id.in_(removed),
                )
            )
        for mid in wanted:
            row = existing.get(mid)
            if row is None:
                self.db.add(
                    ModelConnectionModel(
                        workspace_id=connection.workspace_id,
                        connection_id=connection.id,
                        model_id=mid,
                        display_name=names.get(mid, ""),
                        source=(
                            "discovered"
                            if mid in set(connection.discovered_models or [])
                            else "manual"
                        ),
                        availability="listed",
                        created_at=now,
                    )
                )
            elif (
                body.model_scope == "selected" and names.get(mid) and row.display_name != names[mid]
            ):
                row.display_name = names[mid]
                row.version += 1
        changed = (
            bool(removed)
            or bool(wanted - set(existing))
            or (connection.model_scope != body.model_scope)
        )
        connection.model_scope = body.model_scope
        await self.db.flush()
        if changed:
            connection.config_revision += 1
            connection.last_check_id = None
        connection.version += 1
        await self.refresh_state(connection)
        await self.audit.record(
            "model_connection.models_replaced",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection.id,
            metadata={
                "model_scope": body.model_scope,
                "count": len(wanted),
                "added": len(wanted - set(existing)),
                "removed": len(removed),
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self.out(connection)

    # ---------------------------------------------------------------- 授权

    async def list_grants(
        self, access: WorkspaceAccess, connection_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> ConnectionGrantPage:
        connection = await self.connection(access, connection_id)
        key = _cursor_key(connection.id, "grants")
        offset = _decode_cursor(cursor, key)
        total = int(
            await self.db.scalar(
                select(func.count()).where(ModelConnectionGrant.connection_id == connection.id)
            )
            or 0
        )
        rows = await self._grant_rows(connection.id, offset=offset, limit=limit + 1)
        return ConnectionGrantPage(
            items=rows[:limit],
            next_cursor=_encode_cursor(offset + limit, key) if len(rows) > limit else None,
            total=total,
        )

    async def replace_grants(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        body: GrantsReplace,
        *,
        client_ip: str,
    ) -> ModelConnectionOut:
        connection = await self.connection(access, connection_id, lock=True)
        self.check_version(connection, body.version)
        if connection.migration_state == "non_model":
            raise ApiError(
                409, "MODEL_CONNECTION_NON_MODEL", "非模型凭据的授权请在“迁移与兼容凭据”里管理"
            )
        wanted = set(body.instance_ids)
        if wanted:
            found = set(
                (
                    await self.db.scalars(
                        select(AgentInstance.id).where(
                            AgentInstance.workspace_id == access.workspace.id,
                            AgentInstance.id.in_(wanted),
                        )
                    )
                ).all()
            )
            if found != wanted:
                raise ApiError(404, "NOT_FOUND", "实例不存在")
        current = set(
            (
                await self.db.scalars(
                    select(ModelConnectionGrant.instance_id).where(
                        ModelConnectionGrant.connection_id == connection.id
                    )
                )
            ).all()
        )
        removed = current - wanted
        added = wanted - current
        if removed:
            bound = await self._bound_instances(connection.id)
            in_use = sorted(str(i) for i in removed & bound)
            if in_use:
                raise ApiError(
                    409,
                    "GRANT_IN_USE",
                    "这些智能体正以这个连接为主模型，请先更换或解除它们的主模型",
                    details={"instance_ids": in_use},
                )
            await revoke_instance_access(self.db, connection=connection, instance_ids=removed)
        for instance_id in added:
            self.db.add(
                ModelConnectionGrant(
                    workspace_id=access.workspace.id,
                    connection_id=connection.id,
                    instance_id=instance_id,
                )
            )
        if not removed and not added:
            return await self.out(connection)
        connection.version += 1
        await self.audit.record(
            "model_connection.grants_replaced",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection.id,
            metadata={
                "added": sorted(str(i) for i in added),
                "removed": sorted(str(i) for i in removed),
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self.out(connection)

    # ---------------------------------------------------------------- 活动

    async def activity(
        self,
        access: WorkspaceAccess,
        connection_id: uuid.UUID,
        *,
        cursor: str | None,
        limit: int,
        instance_id: uuid.UUID | None,
        result: str | None,
    ) -> ConnectionActivityPage:
        """审计事件与取用记录按时间倒序合并（脱敏投影，不含 IP 与 metadata 原文）。"""
        connection = await self.connection(access, connection_id)
        key = _cursor_key(connection.id, "activity", instance_id, result)
        before: tuple[datetime, uuid.UUID] | None = None
        if cursor is not None:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if data["k"] != key:
                    raise ValueError
                before = (datetime.fromisoformat(data["at"]), uuid.UUID(data["id"]))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise _invalid("cursor", "无效的游标，请从第一页重新加载") from error
        items: list[ConnectionActivityOut] = []
        if result is None:
            audit_q = select(AuditLog).where(
                AuditLog.workspace_id == access.workspace.id,
                AuditLog.resource_type == "model_connection",
                AuditLog.resource_id == str(connection.id),
            )
            if before is not None:
                audit_q = audit_q.where(
                    or_(
                        AuditLog.created_at < before[0],
                        and_(AuditLog.created_at == before[0], AuditLog.id < before[1]),
                    )
                )
            if instance_id is None:
                for row in (
                    await self.db.scalars(
                        audit_q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all():
                    items.append(
                        ConnectionActivityOut(
                            id=row.id,
                            kind="audit",
                            action=row.action,
                            at=row.created_at,
                            actor_id=row.actor_id,
                            instance_id=None,
                            instance_name=None,
                            result=None,
                            value_version=None,
                        )
                    )
        access_q = select(AgentSecretAccess).where(
            AgentSecretAccess.workspace_id == access.workspace.id,
            AgentSecretAccess.secret_id == connection.credential_secret_id,
        )
        if instance_id is not None:
            access_q = access_q.where(AgentSecretAccess.instance_id == instance_id)
        if result is not None:
            access_q = access_q.where(AgentSecretAccess.result == result)
        if before is not None:
            access_q = access_q.where(
                or_(
                    AgentSecretAccess.at < before[0],
                    and_(AgentSecretAccess.at == before[0], AgentSecretAccess.id < before[1]),
                )
            )
        for hit in (
            await self.db.scalars(
                access_q.order_by(AgentSecretAccess.at.desc(), AgentSecretAccess.id.desc()).limit(
                    limit + 1
                )
            )
        ).all():
            items.append(
                ConnectionActivityOut(
                    id=hit.id,
                    kind="access",
                    action="credential.resolved",
                    at=hit.at,
                    actor_id=None,
                    instance_id=hit.instance_id,
                    instance_name=hit.instance_name,
                    result=hit.result,
                    value_version=hit.value_version,
                )
            )
        items.sort(key=lambda i: (i.at, i.id), reverse=True)
        page = items[:limit]
        next_cursor = None
        if len(items) > limit:
            last = page[-1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps({"k": key, "at": last.at.isoformat(), "id": str(last.id)}).encode()
            ).decode()
        return ConnectionActivityPage(items=page, next_cursor=next_cursor)

    # ---------------------------------------------------------------- 迁移项与分类

    async def migration_entries(
        self, access: WorkspaceAccess, *, state: str | None, cursor: str | None, limit: int
    ) -> MigrationEntryPage:
        key = _cursor_key(access.workspace.id, "migration", state)
        offset = _decode_cursor(cursor, key)
        base = [
            ModelConnection.workspace_id == access.workspace.id,
            ModelConnection.legacy_source_id.is_not(None),
        ]
        conditions = list(base)
        if state == "needs_review":
            conditions.append(ModelConnection.configuration_state == "needs_review")
        elif state:
            conditions.append(ModelConnection.migration_state == state)
        total = int(
            await self.db.scalar(
                select(func.count()).select_from(ModelConnection).where(*conditions)
            )
            or 0
        )
        rows = (
            await self.db.scalars(
                select(ModelConnection)
                .where(*conditions)
                .order_by(ModelConnection.name, ModelConnection.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        page = list(rows[:limit])
        outs = await self.outs(page)
        secrets_ = {
            s.id: s
            for s in (
                await self.db.scalars(
                    select(AgentSecret).where(
                        AgentSecret.id.in_([c.credential_secret_id for c in page])
                    )
                )
            ).all()
        }
        refs: dict[uuid.UUID, list[str]] = {}
        for secret_id, ref_id in (
            await self.db.execute(
                select(AgentSecretGrant.secret_id, AgentSecretGrant.ref_id).where(
                    AgentSecretGrant.secret_id.in_(secrets_)
                )
            )
        ).all():
            refs.setdefault(secret_id, []).append(ref_id)
        counts: dict[str, int] = {}
        for migration_state, n in (
            await self.db.execute(
                select(ModelConnection.migration_state, func.count())
                .where(*base)
                .group_by(ModelConnection.migration_state)
            )
        ).all():
            counts[migration_state] = n
        counts["needs_review"] = int(
            await self.db.scalar(
                select(func.count()).where(
                    *base, ModelConnection.configuration_state == "needs_review"
                )
            )
            or 0
        )
        counts["unmapped"] = int(
            await self.db.scalar(
                select(func.count())
                .select_from(AgentSecret)
                .where(
                    AgentSecret.workspace_id == access.workspace.id,
                    ~select(ModelConnection.id)
                    .where(ModelConnection.credential_secret_id == AgentSecret.id)
                    .exists(),
                )
            )
            or 0
        )
        items = []
        for connection, out in zip(page, outs, strict=True):
            secret = secrets_.get(connection.credential_secret_id)
            items.append(
                MigrationEntryOut(
                    entry_id=connection.id,
                    connection=out,
                    legacy_name=secret.name if secret else connection.name,
                    legacy_kind=(secret.kind if secret else "generic"),
                    legacy_grant_refs=sorted(refs.get(connection.credential_secret_id, [])),
                )
            )
        return MigrationEntryPage(
            items=items,
            next_cursor=_encode_cursor(offset + limit, key) if len(rows) > limit else None,
            total=total,
            counts=counts,
        )

    async def classify(
        self,
        access: WorkspaceAccess,
        entry_id: uuid.UUID,
        body: MigrationClassify,
        *,
        client_ip: str,
    ) -> MigrationEntryOut:
        """model ↔ non_model：保留同一个映射、同一条凭据与原授权；不新建、不恢复已撤销的访问。"""
        connection = await self.connection(access, entry_id, lock=True)
        if connection.legacy_source_id is None:
            raise ApiError(404, "NOT_FOUND", "导入项不存在")
        self.check_version(connection, body.version)
        if body.purpose == "non_model":
            if await self._bound_instances(connection.id):
                raise ApiError(
                    409,
                    "MODEL_BINDING_CONFLICT",
                    "这个连接已有智能体的主模型绑定，请先切换或解除后再归类为非模型凭据",
                )
            busy = int(
                await self.db.scalar(
                    select(func.count())
                    .select_from(ModelApplyJob)
                    .join(
                        AgentModelBinding,
                        AgentModelBinding.instance_id == ModelApplyJob.instance_id,
                    )
                    .where(
                        ModelApplyJob.state.in_(("queued", "running")),
                        AgentModelBinding.connection_id == connection.id,
                    )
                )
                or 0
            )
            if busy:
                raise ApiError(409, "MODEL_BINDING_CONFLICT", "这个连接还有进行中的应用任务")
            connection.migration_state = "non_model"
            connection.configuration_state = (
                "needs_review" if connection.configuration_state == "needs_review" else "draft"
            )
        else:
            connection.migration_state = "needs_configuration"
            await self.refresh_state(connection)
        connection.version += 1
        secret = await self.db.get(AgentSecret, connection.credential_secret_id)
        if secret is not None:
            # 旧密钥接口用 secret.version 做并发检查：一起加 1，界面上的冲突能被察觉
            secret.version += 1
        await self.audit.record(
            "model_connection.classified",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="model_connection",
            resource_id=connection.id,
            metadata={"purpose": body.purpose},
            client_ip=client_ip,
        )
        await self.db.commit()
        page = await self.migration_entries(access, state=None, cursor=None, limit=PAGE_MAX)
        found = next((e for e in page.items if e.entry_id == connection.id), None)
        if found is not None:
            return found
        out = await self.out(connection)
        return MigrationEntryOut(
            entry_id=connection.id,
            connection=out,
            legacy_name=secret.name if secret else connection.name,
            legacy_kind=(secret.kind if secret else "generic"),
            legacy_grant_refs=[],
        )


def model_out(row: ModelConnectionModel) -> ConnectionModelOut:
    return ConnectionModelOut(
        id=row.id,
        model_id=row.model_id,
        display_name=row.display_name,
        source=row.source,
        availability=row.availability,
        last_seen_at=row.last_seen_at,
        version=row.version,
    )


def _context(secret_id: uuid.UUID, value_version: int) -> str:
    from app.domain.agents import agent_secret_context

    return agent_secret_context(secret_id, value_version)


async def rotate_secret_value(
    db: AsyncSession, box: CredentialBox, secret: AgentSecret, value: str
) -> int:
    """与旧密钥库的轮换同一套规则：新版本加 1，旧版本的密文立即清除（不能恢复）。"""
    now = _now()
    new_version = secret.current_version + 1
    sealed = box.seal(value, context=_context(secret.id, new_version))
    await db.execute(
        update(AgentSecretVersion)
        .where(AgentSecretVersion.secret_id == secret.id)
        .values(ciphertext_ref=None, destroyed_at=now)
    )
    db.add(
        AgentSecretVersion(
            workspace_id=secret.workspace_id,
            secret_id=secret.id,
            value_version=new_version,
            ciphertext_ref=sealed,
        )
    )
    secret.current_version = new_version
    secret.last_rotated_at = now
    secret.version += 1
    return new_version


async def revoke_instance_access(
    db: AsyncSession, *, connection: ModelConnection, instance_ids: Iterable[uuid.UUID]
) -> None:
    """撤销实例对这个连接的授权：连接授权与这条凭据对它的旧授权（含 Runner 的引用）一起撤掉。"""
    ids = list(instance_ids)
    if not ids:
        return
    await db.execute(
        delete(ModelConnectionGrant).where(
            ModelConnectionGrant.connection_id == connection.id,
            ModelConnectionGrant.instance_id.in_(ids),
        )
    )
    await db.execute(
        delete(AgentSecretGrant).where(
            AgentSecretGrant.secret_id == connection.credential_secret_id,
            AgentSecretGrant.instance_id.in_(ids),
        )
    )
