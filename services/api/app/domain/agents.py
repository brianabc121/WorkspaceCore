"""OpenClaw 实例、接入凭证与密钥库（V2 OC-2）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节：
- 管理员操作只给 owner，全部限定在 X-Workspace-Id 指定的工作空间，写审计；
- 实例令牌只存 SHA-256；令牌绑定登记时的入口；外网入口还要同一实例的客户端证书（按指纹）；
- 认证失败一律 401 AGENT_UNAUTHORIZED，不区分原因；外网入口同一来源失败 10 次后封禁 15 分钟；
- 密钥只写不读：管理员接口从不返回明文；实例取用时未授权与不存在都是 NOT_FOUND，每次取用都留记录。

OpenIM 相关的动作（注册用户、加好友、强制下线）由 OC-3 接上，这里先保持实例的 im_ready = false。
"""

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent_bundle
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agent_ca import ClientCertificate, ensure_ca, issue_client_certificate
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import FailureLimiter, WindowLimiter
from app.domain.tasks import decode_cursor, encode_cursor
from app.models import (
    AgentCredential,
    AgentInstance,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    AgentSettings,
    WorkspaceMember,
)
from app.schemas.agents import (
    AgentCredentialOut,
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstancePage,
    AgentInstanceUpdate,
    AgentSecretCreate,
    AgentSecretOut,
    AgentSecretPage,
    AgentSecretRotate,
    AgentSecretUpdate,
    AgentSettingsOut,
    AgentSettingsUpdate,
    BundleRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    SecretAccessOut,
    SecretAccessPage,
    SecretGrantOut,
    SecretGrantRef,
    SecretGrantsReplace,
    SecretResolveError,
    SecretResolveResponse,
)

TOKEN_PREFIX = "mwa_"  # noqa: S105 -- 令牌格式的前缀，不是密钥
TOKEN_PREFIX_SHOWN = 12
ROTATION_GRACE = timedelta(hours=24)
MAX_ACTIVE_PER_KIND = 2
LAST_USED_RESOLUTION = timedelta(seconds=60)
# 管理员页面显示多久之内吊销的凭证
SHOW_REVOKED_FOR = timedelta(days=30)

AUTH_FAILURE_LIMIT = 10
AUTH_FAILURE_WINDOW_SECONDS = 15 * 60
RESOLVE_PER_MINUTE = 60
HEARTBEAT_PER_MINUTE = 60


def agent_secret_context(secret_id: uuid.UUID, value_version: int) -> str:
    return f"agent_secret:{secret_id}:{value_version}"


def im_user_id_for(instance_id: uuid.UUID) -> str:
    return f"oc_{instance_id.hex}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    """mwa_ 加 43 个 base64url 字符（32 字节随机数）。"""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").strip().lower()


def _now() -> datetime:
    return datetime.now(UTC)


def _valid(credential: AgentCredential, now: datetime) -> bool:
    """revoked_at 在将来表示处于轮换宽限期，仍然有效。"""
    if credential.revoked_at is not None and credential.revoked_at <= now:
        return False
    return credential.expires_at is None or credential.expires_at > now


def _not_found(what: str = "实例") -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what}不存在")


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _unauthorized() -> ApiError:
    return ApiError(
        401,
        "AGENT_UNAUTHORIZED",
        "实例认证失败",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "RATE_LIMITED",
        "请求过于频繁，请稍后再试",
        headers={"Retry-After": str(retry_after)},
    )


@dataclass(frozen=True)
class AgentPrincipal:
    """通过认证的实例。"""

    instance: AgentInstance
    credential: AgentCredential

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.instance.workspace_id


@dataclass(frozen=True)
class IssuedToken:
    credential: AgentCredential
    token: str


class AgentService:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)

    def _box(self) -> CredentialBox:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥") from error

    # ---------------------------------------------------------------- 总开关

    async def _settings_row(self, workspace_id: uuid.UUID) -> AgentSettings | None:
        return await self.db.get(AgentSettings, workspace_id)

    @staticmethod
    def _settings_out(row: AgentSettings | None) -> AgentSettingsOut:
        if row is None:
            return AgentSettingsOut(
                agents_enabled=True, version=1, updated_at=None, updated_by=None
            )
        return AgentSettingsOut(
            agents_enabled=row.agents_enabled,
            version=row.version,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )

    async def get_settings(self, access: WorkspaceAccess) -> AgentSettingsOut:
        return self._settings_out(await self._settings_row(access.workspace.id))

    async def update_settings(
        self, access: WorkspaceAccess, body: AgentSettingsUpdate, *, client_ip: str
    ) -> AgentSettingsOut:
        row = await self._settings_row(access.workspace.id)
        current = row.version if row else 1
        if body.version != current:
            raise _version_conflict(current)
        if row is None:
            row = AgentSettings(workspace_id=access.workspace.id, version=1)
            self.db.add(row)
        row.agents_enabled = body.agents_enabled
        row.version = current + 1
        row.updated_by = access.user_id
        await self.audit.record(
            "agents.settings.updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_settings",
            metadata={"agents_enabled": body.agents_enabled},
            client_ip=client_ip,
        )
        await self.db.commit()
        # TODO(OC-3)：关闭时强制下线全部实例的 IM（/auth/force_logout）
        return self._settings_out(row)

    # ---------------------------------------------------------------- 实例

    async def _instance(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentInstance:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id,
                AgentInstance.workspace_id == access.workspace.id,
            )
        )
        if instance is None:
            raise _not_found()
        return instance

    async def _instances_out(self, instances: Sequence[AgentInstance]) -> list[AgentInstanceOut]:
        ids = [i.id for i in instances]
        if not ids:
            return []
        since = _now() - SHOW_REVOKED_FOR
        credentials = (
            await self.db.scalars(
                select(AgentCredential)
                .where(
                    AgentCredential.instance_id.in_(ids),
                    (AgentCredential.revoked_at.is_(None)) | (AgentCredential.revoked_at > since),
                )
                .order_by(AgentCredential.created_at.desc())
            )
        ).all()
        grants = (
            await self.db.execute(
                select(AgentSecretGrant, AgentSecret.name)
                .join(AgentSecret, AgentSecret.id == AgentSecretGrant.secret_id)
                .where(AgentSecretGrant.instance_id.in_(ids))
                .order_by(AgentSecretGrant.ref_id)
            )
        ).all()
        by_instance: dict[uuid.UUID, list[AgentCredentialOut]] = {i: [] for i in ids}
        for credential in credentials:
            by_instance[credential.instance_id].append(
                AgentCredentialOut(
                    id=credential.id,
                    kind=credential.kind,
                    ingress=credential.ingress,
                    token_prefix=credential.token_prefix,
                    cert_fingerprint=credential.cert_fingerprint,
                    created_at=credential.created_at,
                    expires_at=credential.expires_at,
                    revoked_at=credential.revoked_at,
                    last_used_at=credential.last_used_at,
                    last_used_ip=credential.last_used_ip,
                )
            )
        grants_by: dict[uuid.UUID, list[SecretGrantOut]] = {i: [] for i in ids}
        for grant, secret_name in grants:
            grants_by[grant.instance_id].append(
                SecretGrantOut(
                    secret_id=grant.secret_id, secret_name=secret_name, ref_id=grant.ref_id
                )
            )
        return [
            AgentInstanceOut(
                id=i.id,
                name=i.name,
                description=i.description,
                network_mode=i.network_mode,
                status=i.status,
                im_ready=i.im_ready,
                im_user_id=i.im_user_id,
                im_nickname=i.im_nickname,
                scope=i.scope,
                max_role=i.max_role,
                acting_user_id=i.acting_user_id,
                reported_version=i.reported_version,
                plugin_version=i.plugin_version,
                last_seen_at=i.last_seen_at,
                bundle_issued_at=i.bundle_issued_at,
                disabled_at=i.disabled_at,
                credentials=by_instance[i.id],
                secret_grants=grants_by[i.id],
                version=i.version,
                created_at=i.created_at,
                updated_at=i.updated_at,
            )
            for i in instances
        ]

    async def _one_out(self, instance: AgentInstance) -> AgentInstanceOut:
        await self.db.refresh(instance)
        return (await self._instances_out([instance]))[0]

    async def list_instances(
        self, access: WorkspaceAccess, *, cursor: str | None, limit: int
    ) -> AgentInstancePage:
        offset = decode_cursor(cursor)
        rows = (
            await self.db.scalars(
                select(AgentInstance)
                .where(AgentInstance.workspace_id == access.workspace.id)
                .order_by(AgentInstance.created_at, AgentInstance.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        settings = await self._settings_row(access.workspace.id)
        return AgentInstancePage(
            items=await self._instances_out(rows[:limit]),
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
            agents_enabled=settings.agents_enabled if settings else True,
        )

    async def get_instance(
        self, access: WorkspaceAccess, instance_id: uuid.UUID
    ) -> AgentInstanceOut:
        return (await self._instances_out([await self._instance(access, instance_id)]))[0]

    async def create_instance(
        self, access: WorkspaceAccess, body: AgentInstanceCreate, *, client_ip: str
    ) -> AgentInstanceOut:
        instance_id = uuid.uuid4()
        instance = AgentInstance(
            id=instance_id,
            workspace_id=access.workspace.id,
            name=body.name,
            description=body.description,
            network_mode=body.network_mode,
            scope=body.scope,
            max_role=body.max_role,
            im_user_id=im_user_id_for(instance_id),
            im_nickname=body.im_nickname or body.name,
            acting_user_id=access.user_id,
        )
        self.db.add(instance)
        await self.audit.record(
            "agents.instance.created",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance_id,
            metadata={
                "name": body.name,
                "network_mode": body.network_mode,
                "scope": body.scope,
                "max_role": body.max_role,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        # TODO(OC-3)：注册 OpenIM 用户 oc_<id>，与 owner / admin 互加好友，成功后 im_ready = true
        return await self._one_out(instance)

    def _check_version(self, instance: AgentInstance, version: int) -> None:
        if instance.version != version:
            raise _version_conflict(instance.version)

    async def update_instance(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentInstanceUpdate,
        *,
        client_ip: str,
    ) -> AgentInstanceOut:
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        changes = body.model_dump(exclude_unset=True, exclude={"version"})
        for field, value in changes.items():
            setattr(instance, field, value)
        instance.version += 1
        await self.audit.record(
            "agents.instance.updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={"fields": sorted(changes)},
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self._one_out(instance)

    async def set_enabled(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        *,
        enabled: bool,
        version: int,
        client_ip: str,
    ) -> AgentInstanceOut:
        instance = await self._instance(access, instance_id)
        self._check_version(instance, version)
        if enabled and instance.status == "disabled":
            instance.status = "offline" if instance.last_seen_at else "pending"
            instance.disabled_at = None
        elif not enabled and instance.status != "disabled":
            instance.status = "disabled"
            instance.disabled_at = _now()
        instance.version += 1
        await self.audit.record(
            "agents.instance.enabled" if enabled else "agents.instance.disabled",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            client_ip=client_ip,
        )
        await self.db.commit()
        # TODO(OC-3)：停用时强制下线 IM（/auth/force_logout）
        return await self._one_out(instance)

    async def revoke_credential(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        credential_id: uuid.UUID,
        *,
        version: int,
        client_ip: str,
    ) -> AgentInstanceOut:
        instance = await self._instance(access, instance_id)
        self._check_version(instance, version)
        credential = await self.db.scalar(
            select(AgentCredential).where(
                AgentCredential.id == credential_id,
                AgentCredential.instance_id == instance.id,
            )
        )
        if credential is None:
            raise _not_found("凭证")
        now = _now()
        if credential.revoked_at is None or credential.revoked_at > now:
            credential.revoked_at = now
        instance.version += 1
        await self.audit.record(
            "agents.credential.revoked",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_credential",
            resource_id=credential.id,
            metadata={"instance_id": str(instance.id), "kind": credential.kind},
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self._one_out(instance)

    async def issue_token(
        self, instance: AgentInstance, *, keep_previous: bool, now: datetime | None = None
    ) -> IssuedToken:
        """签发新的实例令牌（接入包用）。在调用方的事务里写入，由调用方提交。"""
        now = now or _now()
        await self._retire(instance, "token", keep_previous=keep_previous, now=now)
        token = new_token()
        credential = AgentCredential(
            workspace_id=instance.workspace_id,
            instance_id=instance.id,
            kind="token",
            ingress=instance.network_mode,
            token_hash=hash_token(token),
            token_prefix=token[:TOKEN_PREFIX_SHOWN],
        )
        self.db.add(credential)
        await self.db.flush()
        return IssuedToken(credential=credential, token=token)

    async def add_client_cert(
        self,
        instance: AgentInstance,
        *,
        fingerprint: str,
        expires_at: datetime,
        keep_previous: bool,
        now: datetime | None = None,
    ) -> AgentCredential:
        """登记新签发的客户端证书（只存指纹）。在调用方的事务里写入。"""
        now = now or _now()
        await self._retire(instance, "client_cert", keep_previous=keep_previous, now=now)
        credential = AgentCredential(
            workspace_id=instance.workspace_id,
            instance_id=instance.id,
            kind="client_cert",
            ingress="public",
            cert_fingerprint=normalize_fingerprint(fingerprint),
            expires_at=expires_at,
        )
        self.db.add(credential)
        await self.db.flush()
        return credential

    async def _whitelist(self, workspace_id: uuid.UUID) -> list[str]:
        """接入包里的 inboundWhitelist：本工作空间的 owner / admin 与全部实例（契约第 3 节）。"""
        members = (
            await self.db.scalars(
                select(WorkspaceMember.user_id)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.role.in_(("owner", "admin")),
                )
                .order_by(WorkspaceMember.user_id)
            )
        ).all()
        instances = (
            await self.db.scalars(
                select(AgentInstance.im_user_id)
                .where(AgentInstance.workspace_id == workspace_id)
                .order_by(AgentInstance.im_user_id)
            )
        ).all()
        return [f"ws_{user_id.hex}" for user_id in members] + list(instances)

    async def issue_bundle(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: BundleRequest,
        *,
        client_ip: str,
    ) -> tuple[str, bytes]:
        """签发新凭证并生成接入包（zip）。凭证与私钥只出现在返回的字节里。"""
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        if instance.status == "disabled":
            raise ApiError(409, "AGENT_DISABLED", "实例已停用，启用后才能生成接入包")
        public = instance.network_mode == "public"
        configured = self.settings.agent_public_url if public else self.settings.agent_intranet_url
        if not configured:
            raise ApiError(
                409,
                "AGENT_INGRESS_NOT_CONFIGURED",
                "这个网络模式的接入入口还没有配置",
                details={"network_mode": instance.network_mode},
            )
        now = _now()
        keep = body.previous == "keep_24h"
        issued = await self.issue_token(instance, keep_previous=keep, now=now)
        cert: ClientCertificate | None = None
        if public:
            box = self._box()
            ca = await ensure_ca(self.db, box, now=now)
            cert = issue_client_certificate(ca, box, instance, now=now)
            await self.add_client_cert(
                instance,
                fingerprint=cert.fingerprint,
                expires_at=cert.not_after,
                keep_previous=keep,
                now=now,
            )
        tunnel_target: str | None = None
        base = configured
        if public:
            # 外网模式：公网域名在 OpenClaw 那边解析到本机隧道
            # （Caddy 开启客户端证书时要求 SNI 与 Host 一致，所以不能用 127.0.0.1）
            url = urlsplit(configured)
            host = url.hostname or ""
            tunnel_target = f"{host}:{url.port or 443}"
            base = f"http://{host}:{self.settings.agent_tunnel_port}"
        data = agent_bundle.build(
            agent_bundle.BundleInput(
                instance_id=str(instance.id),
                instance_name=instance.name,
                network_mode=instance.network_mode,
                token=issued.token,
                base_url=base,
                whitelist=await self._whitelist(instance.workspace_id),
                client_cert_pem=cert.cert_pem if cert else None,
                client_key_pem=cert.key_pem if cert else None,
                tunnel_target=tunnel_target,
                tunnel_port=self.settings.agent_tunnel_port,
            ),
            now=now,
        )
        instance.bundle_issued_at = now
        instance.version += 1
        await self.audit.record(
            "agents.bundle.issued",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "previous": body.previous,
                "token_prefix": issued.credential.token_prefix,
                "cert_fingerprint": cert.fingerprint if cert else None,
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        return agent_bundle.filename_for(str(instance.id)), data

    async def _retire(
        self, instance: AgentInstance, kind: str, *, keep_previous: bool, now: datetime
    ) -> None:
        """轮换：旧凭证再保留 24 小时（或立即吊销）；同一种凭证同时最多两份有效。"""
        active = [
            c
            for c in (
                await self.db.scalars(
                    select(AgentCredential)
                    .where(AgentCredential.instance_id == instance.id, AgentCredential.kind == kind)
                    .order_by(AgentCredential.created_at.desc())
                )
            ).all()
            if _valid(c, now)
        ]
        grace_end = now + ROTATION_GRACE
        for index, credential in enumerate(active):
            # 新凭证加上之后：最新的一份旧凭证可以保留宽限期，更早的立即吊销
            if keep_previous and index < MAX_ACTIVE_PER_KIND - 1:
                if credential.revoked_at is None or credential.revoked_at > grace_end:
                    credential.revoked_at = grace_end
            else:
                credential.revoked_at = now

    async def replace_grants(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: SecretGrantsReplace,
        *,
        client_ip: str,
    ) -> AgentInstanceOut:
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        secret_ids = {g.secret_id for g in body.grants}
        if secret_ids:
            found = set(
                (
                    await self.db.scalars(
                        select(AgentSecret.id).where(
                            AgentSecret.workspace_id == access.workspace.id,
                            AgentSecret.id.in_(secret_ids),
                        )
                    )
                ).all()
            )
            if found != secret_ids:
                raise _not_found("密钥")
        await self.db.execute(
            delete(AgentSecretGrant).where(AgentSecretGrant.instance_id == instance.id)
        )
        for grant in body.grants:
            self.db.add(
                AgentSecretGrant(
                    workspace_id=access.workspace.id,
                    instance_id=instance.id,
                    secret_id=grant.secret_id,
                    ref_id=grant.ref_id,
                )
            )
        instance.version += 1
        await self.audit.record(
            "agents.grants.replaced",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={"ref_ids": sorted(g.ref_id for g in body.grants)},
            client_ip=client_ip,
        )
        await self.db.commit()
        return await self._one_out(instance)

    # ---------------------------------------------------------------- 密钥库

    async def _secret(self, access: WorkspaceAccess, secret_id: uuid.UUID) -> AgentSecret:
        secret = await self.db.scalar(
            select(AgentSecret).where(
                AgentSecret.id == secret_id, AgentSecret.workspace_id == access.workspace.id
            )
        )
        if secret is None:
            raise _not_found("密钥")
        return secret

    async def _secrets_out(self, secrets_: Sequence[AgentSecret]) -> list[AgentSecretOut]:
        ids = [s.id for s in secrets_]
        refs: dict[uuid.UUID, list[SecretGrantRef]] = {i: [] for i in ids}
        if ids:
            rows = (
                await self.db.execute(
                    select(AgentSecretGrant, AgentInstance.name)
                    .join(AgentInstance, AgentInstance.id == AgentSecretGrant.instance_id)
                    .where(AgentSecretGrant.secret_id.in_(ids))
                    .order_by(AgentInstance.name)
                )
            ).all()
            for grant, instance_name in rows:
                refs[grant.secret_id].append(
                    SecretGrantRef(
                        instance_id=grant.instance_id,
                        instance_name=instance_name,
                        ref_id=grant.ref_id,
                    )
                )
        return [
            AgentSecretOut(
                id=s.id,
                name=s.name,
                kind=s.kind,
                description=s.description,
                value_version=s.current_version,
                last_rotated_at=s.last_rotated_at,
                grants=refs[s.id],
                version=s.version,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in secrets_
        ]

    async def list_secrets(
        self, access: WorkspaceAccess, *, cursor: str | None, limit: int
    ) -> AgentSecretPage:
        offset = decode_cursor(cursor)
        rows = (
            await self.db.scalars(
                select(AgentSecret)
                .where(AgentSecret.workspace_id == access.workspace.id)
                .order_by(AgentSecret.name, AgentSecret.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return AgentSecretPage(
            items=await self._secrets_out(rows[:limit]),
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    def _seal(self, secret_id: uuid.UUID, value_version: int, value: str) -> str:
        return self._box().seal(value, context=agent_secret_context(secret_id, value_version))

    async def create_secret(
        self, access: WorkspaceAccess, body: AgentSecretCreate, *, client_ip: str
    ) -> AgentSecretOut:
        existing = await self.db.scalar(
            select(AgentSecret.id).where(
                AgentSecret.workspace_id == access.workspace.id, AgentSecret.name == body.name
            )
        )
        if existing is not None:
            raise ApiError(
                409, "AGENT_SECRET_EXISTS", "已有同名的密钥", details={"existing_id": str(existing)}
            )
        secret_id = uuid.uuid4()
        ref = self._seal(secret_id, 1, body.value.get_secret_value())
        secret = AgentSecret(
            id=secret_id,
            workspace_id=access.workspace.id,
            name=body.name,
            kind=body.kind,
            description=body.description,
            current_version=1,
        )
        self.db.add(secret)
        await self.db.flush()
        self.db.add(
            AgentSecretVersion(
                workspace_id=access.workspace.id,
                secret_id=secret_id,
                value_version=1,
                ciphertext_ref=ref,
            )
        )
        await self.audit.record(
            "agents.secret.created",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_secret",
            resource_id=secret_id,
            metadata={"name": body.name, "kind": body.kind},
            client_ip=client_ip,
        )
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ApiError(409, "AGENT_SECRET_EXISTS", "已有同名的密钥") from None
        await self.db.refresh(secret)
        return (await self._secrets_out([secret]))[0]

    async def update_secret(
        self,
        access: WorkspaceAccess,
        secret_id: uuid.UUID,
        body: AgentSecretUpdate,
        *,
        client_ip: str,
    ) -> AgentSecretOut:
        secret = await self._secret(access, secret_id)
        if secret.version != body.version:
            raise _version_conflict(secret.version)
        changes = body.model_dump(exclude_unset=True, exclude={"version"})
        for field, value in changes.items():
            setattr(secret, field, value)
        secret.version += 1
        await self.audit.record(
            "agents.secret.updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_secret",
            resource_id=secret.id,
            metadata={"fields": sorted(changes)},
            client_ip=client_ip,
        )
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ApiError(409, "AGENT_SECRET_EXISTS", "已有同名的密钥") from None
        await self.db.refresh(secret)
        return (await self._secrets_out([secret]))[0]

    async def rotate_secret(
        self,
        access: WorkspaceAccess,
        secret_id: uuid.UUID,
        body: AgentSecretRotate,
        *,
        client_ip: str,
    ) -> AgentSecretOut:
        secret = await self._secret(access, secret_id)
        if secret.version != body.version:
            raise _version_conflict(secret.version)
        now = _now()
        new_version = secret.current_version + 1
        ref = self._seal(secret.id, new_version, body.value.get_secret_value())
        # 旧值不再下发，也不留密文
        await self.db.execute(
            update(AgentSecretVersion)
            .where(AgentSecretVersion.secret_id == secret.id)
            .values(ciphertext_ref=None, destroyed_at=now)
        )
        self.db.add(
            AgentSecretVersion(
                workspace_id=access.workspace.id,
                secret_id=secret.id,
                value_version=new_version,
                ciphertext_ref=ref,
            )
        )
        secret.current_version = new_version
        secret.last_rotated_at = now
        secret.version += 1
        await self.audit.record(
            "agents.secret.rotated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_secret",
            resource_id=secret.id,
            metadata={"value_version": new_version},
            client_ip=client_ip,
        )
        await self.db.commit()
        await self.db.refresh(secret)
        return (await self._secrets_out([secret]))[0]

    async def delete_secret(
        self, access: WorkspaceAccess, secret_id: uuid.UUID, *, version: int, client_ip: str
    ) -> None:
        secret = await self._secret(access, secret_id)
        if secret.version != version:
            raise _version_conflict(secret.version)
        await self.audit.record(
            "agents.secret.deleted",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_secret",
            resource_id=secret.id,
            metadata={"name": secret.name},
            client_ip=client_ip,
        )
        await self.db.delete(secret)
        await self.db.commit()

    async def list_accesses(
        self,
        access: WorkspaceAccess,
        *,
        instance_id: uuid.UUID | None,
        secret_id: uuid.UUID | None,
        result: str | None,
        cursor: str | None,
        limit: int,
    ) -> SecretAccessPage:
        offset = decode_cursor(cursor)
        stmt = select(AgentSecretAccess).where(
            AgentSecretAccess.workspace_id == access.workspace.id
        )
        if instance_id is not None:
            stmt = stmt.where(AgentSecretAccess.instance_id == instance_id)
        if secret_id is not None:
            stmt = stmt.where(AgentSecretAccess.secret_id == secret_id)
        if result is not None:
            stmt = stmt.where(AgentSecretAccess.result == result)
        rows = (
            await self.db.scalars(
                stmt.order_by(AgentSecretAccess.at.desc(), AgentSecretAccess.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return SecretAccessPage(
            items=[
                SecretAccessOut(
                    id=r.id,
                    instance_id=r.instance_id,
                    instance_name=r.instance_name,
                    ref_id=r.ref_id,
                    secret_id=r.secret_id,
                    value_version=r.value_version,
                    result=r.result,
                    source_ip=r.source_ip,
                    at=r.at,
                )
                for r in rows[:limit]
            ],
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    # ---------------------------------------------------------------- 实例认证

    async def authenticate(
        self,
        token: str | None,
        *,
        ingress: str | None,
        cert_fingerprint: str | None,
        client_ip: str,
    ) -> AgentPrincipal:
        """实例凭证 + 入口 + （外网）客户端证书。任何不符都是同一个 401。"""
        limiter = (
            FailureLimiter(self.redis, AUTH_FAILURE_WINDOW_SECONDS)
            if self.redis is not None and ingress == "public"
            else None
        )
        failure_key = f"agent_auth_fail:{client_ip}"
        if limiter is not None:
            retry = await limiter.retry_after({failure_key: AUTH_FAILURE_LIMIT})
            if retry is not None:
                raise _rate_limited(retry)
        principal = await self._check(token, ingress, cert_fingerprint, client_ip)
        if principal is None:
            if limiter is not None:
                await limiter.record_failure([failure_key])
            raise _unauthorized()
        return principal

    async def _check(
        self,
        token: str | None,
        ingress: str | None,
        cert_fingerprint: str | None,
        client_ip: str,
    ) -> AgentPrincipal | None:
        if not token or not token.startswith(TOKEN_PREFIX) or ingress not in ("intranet", "public"):
            return None
        now = _now()
        credential = await self.db.scalar(
            select(AgentCredential).where(
                AgentCredential.token_hash == hash_token(token), AgentCredential.kind == "token"
            )
        )
        if credential is None or not _valid(credential, now) or credential.ingress != ingress:
            return None
        instance = await self.db.get(AgentInstance, credential.instance_id)
        if instance is None or instance.status == "disabled":
            return None
        settings = await self._settings_row(instance.workspace_id)
        if settings is not None and not settings.agents_enabled:
            return None
        used = [credential]
        if ingress == "public":
            if not cert_fingerprint:
                return None
            cert = await self.db.scalar(
                select(AgentCredential).where(
                    AgentCredential.cert_fingerprint == normalize_fingerprint(cert_fingerprint),
                    AgentCredential.kind == "client_cert",
                    AgentCredential.instance_id == instance.id,
                )
            )
            if cert is None or not _valid(cert, now):
                return None
            used.append(cert)
        for item in used:
            if item.last_used_at is None or now - item.last_used_at >= LAST_USED_RESOLUTION:
                item.last_used_at = now
                item.last_used_ip = client_ip
        if instance.last_seen_at is None or now - instance.last_seen_at >= LAST_USED_RESOLUTION:
            instance.last_seen_at = now
        await self.db.commit()
        return AgentPrincipal(instance=instance, credential=credential)

    async def _limit(self, principal: AgentPrincipal, name: str, per_minute: int) -> int | None:
        if self.redis is None:
            return None
        return await WindowLimiter(self.redis, 60).hit(
            f"agent_rate:{name}:{principal.instance.id}", per_minute
        )

    # ---------------------------------------------------------------- 实例接口

    async def resolve_secrets(
        self, principal: AgentPrincipal, ids: Sequence[str], *, client_ip: str
    ) -> SecretResolveResponse:
        instance = principal.instance
        wanted = list(dict.fromkeys(ids))
        retry = await self._limit(principal, "resolve", RESOLVE_PER_MINUTE)
        if retry is not None:
            for ref_id in wanted:
                self._record_access(instance, ref_id, None, None, "rate_limited", client_ip)
            await self.db.commit()
            raise _rate_limited(retry)
        rows = (
            await self.db.execute(
                select(AgentSecretGrant.ref_id, AgentSecret, AgentSecretVersion.ciphertext_ref)
                .join(AgentSecret, AgentSecret.id == AgentSecretGrant.secret_id)
                .join(
                    AgentSecretVersion,
                    (AgentSecretVersion.secret_id == AgentSecret.id)
                    & (AgentSecretVersion.value_version == AgentSecret.current_version),
                )
                .where(
                    AgentSecretGrant.instance_id == instance.id,
                    AgentSecretGrant.ref_id.in_(wanted),
                )
            )
        ).all()
        found = {ref_id: (secret, ref) for ref_id, secret, ref in rows}
        values: dict[str, str] = {}
        errors: dict[str, SecretResolveError] = {}
        box = self._box() if found else None
        for ref_id in wanted:
            secret, ref = found.get(ref_id, (None, None))
            value: str | None = None
            if secret is not None and ref and box is not None:
                try:
                    value = box.resolve(
                        ref, context=agent_secret_context(secret.id, secret.current_version)
                    ).get_secret_value()
                except CredentialError:
                    # 密文损坏或缺少解密密钥：对实例同样是 NOT_FOUND，不透露原因
                    value = None
            if secret is None or value is None:
                errors[ref_id] = SecretResolveError(code="NOT_FOUND")
                self._record_access(instance, ref_id, None, None, "not_found", client_ip)
            else:
                values[ref_id] = value
                self._record_access(
                    instance, ref_id, secret.id, secret.current_version, "ok", client_ip
                )
        await self.db.commit()
        return SecretResolveResponse(values=values, errors=errors)

    def _record_access(
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
                ref_id=ref_id,
                secret_id=secret_id,
                value_version=value_version,
                result=result,
                source_ip=client_ip,
            )
        )

    async def heartbeat(
        self, principal: AgentPrincipal, body: HeartbeatRequest
    ) -> HeartbeatResponse:
        retry = await self._limit(principal, "heartbeat", HEARTBEAT_PER_MINUTE)
        if retry is not None:
            raise _rate_limited(retry)
        now = _now()
        await self.db.execute(
            update(AgentInstance)
            .where(AgentInstance.id == principal.instance.id)
            .values(
                reported_version=body.openclaw_version,
                plugin_version=body.plugin_version,
                last_seen_at=now,
            )
        )
        await self.db.commit()
        return HeartbeatResponse(server_time=now)
