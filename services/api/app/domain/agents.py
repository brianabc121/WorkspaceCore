"""OpenClaw 实例、接入凭证与密钥库（V2 OC-2）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4、7 节：
- 管理员操作只给 owner，全部限定在 X-Workspace-Id 指定的工作空间，写审计；
- 实例令牌只存 SHA-256；令牌绑定登记时的入口；外网入口还要同一实例的客户端证书（按指纹）；
- 认证失败一律 401 AGENT_UNAUTHORIZED，不区分原因；外网入口同一来源失败 10 次后封禁 15 分钟；
- 密钥只写不读：管理员接口从不返回明文；实例取用时未授权与不存在都是 NOT_FOUND，每次取用都留记录。

OpenIM 相关的动作（OC-3，app/domain/im.py）：登记实例后注册 IM 账号并与 owner / admin 互加好友，
成功后 im_ready = true（失败时由 Worker 重试）；停用实例、关闭总开关时强制下线；
实例启动时取 IM 令牌（每小时最多 10 次）。没有部署 OpenIM 时这些动作都跳过。

中转站管理（T-0200 ~ T-0209）：连接的 Key 也存在 agent_secrets 里，但那是连接自己的内部凭据，
在这里（列表、详情、改名、轮换、删除、授权）一律当作不存在；模型服务的 Key 只能在中转站管理里保存，
这里新建 model_api_key 返回 422。
"""

import base64
import binascii
import hashlib
import json
import logging
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent_bundle
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import mail_agents, mail_format, notifications, ops_metrics, task_runs
from app.domain.agent_ca import ClientCertificate, ensure_ca, issue_client_certificate
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.im import ImAccounts, im_unavailable
from app.domain.policy import WorkspaceAccess
from app.domain.ratelimit import FailureLimiter, WindowLimiter
from app.domain.tasks import decode_cursor, encode_cursor
from app.integrations.base import ConnectorError
from app.integrations.openim import PLATFORM_WEB, OpenIMClient
from app.models import (
    AgentCredential,
    AgentInstance,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    AgentSettings,
    MailAccount,
    ModelConnection,
    WorkspaceMember,
)
from app.schemas.agents import (
    AgentCredentialOut,
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstancePage,
    AgentInstanceUpdate,
    AgentLimits,
    AgentLimitsPut,
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
    ImTokenResponse,
    ProjectAdminOut,
    ProjectAdminPut,
    SecretAccessOut,
    SecretAccessPage,
    SecretGrantOut,
    SecretGrantRef,
    SecretGrantsReplace,
    SecretKind,
    SecretResolveError,
    SecretResolveResponse,
)
from app.schemas.mail import AgentMailAccountRef, AgentMailOut, AgentMailPut

logger = logging.getLogger("app.agents")


def mail_out(instance: AgentInstance, accounts: list[AgentMailAccountRef]) -> AgentMailOut:
    return AgentMailOut(
        accounts=accounts,
        read_enabled=instance.mail_read_enabled,
        send_enabled=instance.mail_send_enabled,
        send_scope=instance.mail_send_scope,
        recipients=list(instance.mail_send_recipients or []),
        send_by=instance.mail_send_by,
        send_at=instance.mail_send_at,
        version=instance.version,
    )


def clean_mail_recipients(values: Sequence[str]) -> list[str]:
    """收件人范围：完整地址（a@b.com）或域名（@b.com），小写、去重。"""
    out: list[str] = []
    for value in values:
        entry = value.strip().lower()
        if entry.startswith("@"):
            ok = mail_format.valid_address("x" + entry)
        else:
            ok = mail_format.valid_address(entry)
        if not ok:
            raise ApiError(422, "VALIDATION_FAILED", f"不是有效的地址或域名：{value[:80]}")
        if entry not in out:
            out.append(entry)
    return out


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
# 签发 IM 令牌会踢掉旧连接（OC-0 F1），只在实例启动时调用（契约第 7 节）
IM_TOKEN_PER_HOUR = 10


def agent_secret_context(secret_id: uuid.UUID, value_version: int) -> str:
    return f"agent_secret:{secret_id}:{value_version}"


def _connection_secret() -> ColumnElement[bool]:
    """中转站管理连接自己的内部凭据（不在密钥库里出现）。"""
    return (
        select(ModelConnection.id)
        .where(ModelConnection.credential_secret_id == AgentSecret.id)
        .exists()
    )


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


def _bad_cursor() -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": ["query", "cursor"], "msg": "游标无效", "type": "value_error"}]
        },
    )


def _secret_list_key(workspace_id: uuid.UUID, q: str | None, kind: str | None) -> str:
    """密钥列表游标绑定的范围：工作空间与 q / kind；换了任何一项，旧游标都不能接着用。"""
    raw = json.dumps(["agent_secrets", str(workspace_id), q, kind])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _encode_secret_cursor(offset: int, key: str) -> str:
    raw = json.dumps({"o": offset, "k": key}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_secret_cursor(cursor: str | None, key: str, *, filtered: bool) -> int:
    """取出偏移量。旧版游标只有偏移量、没有筛选摘要：只在不带 q / kind 时继续可用。"""
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise _bad_cursor() from None
    if not isinstance(data, dict):
        raise _bad_cursor()
    bound = data.get("k")
    if bound != key and not (bound is None and not filtered):
        raise _bad_cursor()
    offset = data.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1_000_000:
        raise _bad_cursor()
    return offset


def _like_pattern(text: str) -> str:
    """字面包含的 ILIKE 模式：% _ 和反斜杠（转义符）本身都按普通字符匹配。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None = None,
        im: OpenIMClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.im = im
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
        if not body.agents_enabled and self.im is not None:
            # 关闭总开关：强制下线本工作空间全部实例的 IM。尽力而为：回调与接入网关还会按
            # 总开关拒收消息、拒绝连接
            im_user_ids = (
                await self.db.scalars(
                    select(AgentInstance.im_user_id).where(
                        AgentInstance.workspace_id == access.workspace.id,
                        AgentInstance.im_ready.is_(True),
                        AgentInstance.status != "disabled",
                    )
                )
            ).all()
            await ImAccounts(self.db, self.im).force_logout(im_user_ids)
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
        mailboxes = await mail_agents.accounts_by_instance(self.db, ids)
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
                im_token_expires_at=i.im_token_expires_at,
                disabled_at=i.disabled_at,
                project_admin=ProjectAdminOut(
                    enabled=i.project_admin_enabled,
                    granted_by=i.project_admin_by,
                    granted_at=i.project_admin_at,
                ),
                im_media=ProjectAdminOut(
                    enabled=i.im_media_enabled,
                    granted_by=i.im_media_by,
                    granted_at=i.im_media_at,
                ),
                stop_others=ProjectAdminOut(
                    enabled=i.stop_others_enabled,
                    granted_by=i.stop_others_by,
                    granted_at=i.stop_others_at,
                ),
                mail=mail_out(i, mailboxes[i.id]),
                bundle_outdated=i.plugin_version is not None
                and i.plugin_version != agent_bundle.PATCHED_PLUGIN_VERSION,
                credentials=by_instance[i.id],
                secret_grants=grants_by[i.id],
                server_id=i.server_id,
                server_manual=i.server_manual,
                limits=AgentLimits(mem_limit_mb=i.mem_limit_mb, cpus=i.cpus),
                version=i.version,
                created_at=i.created_at,
                updated_at=i.updated_at,
            )
            for i in instances
        ]

    async def _one_out(self, instance: AgentInstance) -> AgentInstanceOut:
        await self.db.refresh(instance)
        return (await self._instances_out([instance]))[0]

    async def instance_out(self, instance: AgentInstance) -> AgentInstanceOut:
        """一个实例的输出（重新读取后批量组装）：一次点击部署与总览共用。"""
        return await self._one_out(instance)

    async def instances_out(self, instances: Sequence[AgentInstance]) -> list[AgentInstanceOut]:
        """一批实例的输出：凭证、密钥授权、邮箱都是按批查询（不随行数增加查询次数）。"""
        return await self._instances_out(instances)

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
        instance = await self.create_instance_record(access, body, client_ip=client_ip)
        await self.db.commit()
        await self._provision_im(instance)
        return await self._one_out(instance)

    async def create_instance_record(
        self, access: WorkspaceAccess, body: AgentInstanceCreate, *, client_ip: str
    ) -> AgentInstance:
        """登记实例的建模与审计（T-0180 提取）：只写进当前事务（flush），不提交、不调 OpenIM。

        `create_instance` 与一次点击新建并部署（app/domain/agent_deployments.py）共用；
        提交与之后的 OpenIM 注册由调用方负责。
        """
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
        await self.db.flush()
        return instance

    async def provision_im(self, instance: AgentInstance) -> bool:
        """提交之后注册实例的 IM 账号（失败时 im_ready=false，Worker 重试）。"""
        return await self._provision_im(instance)

    async def _provision_im(self, instance: AgentInstance) -> bool:
        """注册实例的 IM 账号并互加好友；失败时 im_ready 保持 false，由 Worker 重试。"""
        if self.im is None:
            return False
        try:
            await ImAccounts(self.db, self.im).provision_instance(instance)
        except ConnectorError as error:
            await self.db.rollback()
            logger.warning(
                "openim provisioning failed",
                extra={"instance_id": str(instance.id), "error_kind": error.kind},
            )
            return False
        await self.db.commit()
        return True

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

    async def set_project_admin(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: ProjectAdminPut,
        *,
        client_ip: str,
    ) -> ProjectAdminOut:
        """打开或关闭实例的项目管理权限（T-0112，owner / admin）。

        打开时记下是谁打开的：这个人做它建的项目群的群主。关闭后已经建的项目与成员保留。
        """
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        if instance.project_admin_enabled != body.enabled:
            instance.project_admin_enabled = body.enabled
            instance.project_admin_by = access.user_id if body.enabled else None
            instance.project_admin_at = datetime.now(UTC) if body.enabled else None
            instance.version += 1
            await self.audit.record(
                "agents.instance.project_admin_enabled"
                if body.enabled
                else "agents.instance.project_admin_disabled",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="agent_instance",
                resource_id=instance.id,
                client_ip=client_ip,
            )
        await self.db.commit()
        await self.db.refresh(instance)
        return ProjectAdminOut(
            enabled=instance.project_admin_enabled,
            granted_by=instance.project_admin_by,
            granted_at=instance.project_admin_at,
        )

    async def set_im_media(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: ProjectAdminPut,
        *,
        client_ip: str,
    ) -> ProjectAdminOut:
        """打开或关闭实例的附件权限（T-0119，owner / admin，方案 D3）。每次都写审计。"""
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        if instance.im_media_enabled != body.enabled:
            instance.im_media_enabled = body.enabled
            instance.im_media_by = access.user_id if body.enabled else None
            instance.im_media_at = datetime.now(UTC) if body.enabled else None
            instance.version += 1
            await self.audit.record(
                "agents.instance.im_media_enabled"
                if body.enabled
                else "agents.instance.im_media_disabled",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="agent_instance",
                resource_id=instance.id,
                client_ip=client_ip,
            )
        await self.db.commit()
        await self.db.refresh(instance)
        return ProjectAdminOut(
            enabled=instance.im_media_enabled,
            granted_by=instance.im_media_by,
            granted_at=instance.im_media_at,
        )

    async def set_limits(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentLimitsPut,
        *,
        client_ip: str,
    ) -> AgentLimits:
        """设置 OpenClaw 容器的内存、CPU 上限（T-0145，D2）。写审计；重新部署后生效。"""
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        await self.apply_limits(access, instance, body.mem_limit_mb, body.cpus, client_ip=client_ip)
        await self.db.commit()
        await self.db.refresh(instance)
        return AgentLimits(mem_limit_mb=instance.mem_limit_mb, cpus=instance.cpus)

    async def apply_limits(
        self,
        access: WorkspaceAccess,
        instance: AgentInstance,
        mem_limit_mb: int | None,
        cpus: float | None,
        *,
        client_ip: str,
    ) -> bool:
        """改资源上限并写审计（不提交）；值没变时什么都不做。返回是否改了。

        范围由 schema 校验（内存 256 MiB ~ 1 TiB、CPU 0.1 ~ 256 核），null 表示不限制（D2 默认）。
        """
        rounded = round(cpus, 2) if cpus is not None else None
        if (instance.mem_limit_mb, instance.cpus) == (mem_limit_mb, rounded):
            return False
        await self.audit.record(
            "agents.instance.limits_updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "mem_limit_mb": mem_limit_mb,
                "cpus": rounded,
                "previous": {"mem_limit_mb": instance.mem_limit_mb, "cpus": instance.cpus},
            },
            client_ip=client_ip,
        )
        instance.mem_limit_mb = mem_limit_mb
        instance.cpus = rounded
        instance.version += 1
        return True

    async def set_stop_others(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: ProjectAdminPut,
        *,
        client_ip: str,
    ) -> ProjectAdminOut:
        """打开或关闭“终止其他智能体任务”的权限（T-0126，owner / admin，方案 D20）。写审计。"""
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        if instance.stop_others_enabled != body.enabled:
            instance.stop_others_enabled = body.enabled
            instance.stop_others_by = access.user_id if body.enabled else None
            instance.stop_others_at = datetime.now(UTC) if body.enabled else None
            instance.version += 1
            await self.audit.record(
                "agents.instance.stop_others_enabled"
                if body.enabled
                else "agents.instance.stop_others_disabled",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="agent_instance",
                resource_id=instance.id,
                client_ip=client_ip,
            )
        await self.db.commit()
        await self.db.refresh(instance)
        return ProjectAdminOut(
            enabled=instance.stop_others_enabled,
            granted_by=instance.stop_others_by,
            granted_at=instance.stop_others_at,
        )

    async def set_mail(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentMailPut,
        *,
        client_ip: str,
    ) -> AgentMailOut:
        """邮件权限（T-0129，docs/v2/MAIL_PLAN.md 第 5 节，owner / admin）：写审计；
        打开发送时必须选收件人范围，并通知其他管理员。"""
        instance = await self._instance(access, instance_id)
        self._check_version(instance, body.version)
        # 关掉发送时保留选过的收件人范围，下次打开时页面可以直接沿用
        scope = body.send_scope
        recipients = clean_mail_recipients(body.recipients) if scope == "listed" else []
        if body.send_enabled:
            if scope is None:
                raise ApiError(422, "VALIDATION_FAILED", "打开发送邮件要选收件人范围")
            if scope == "listed" and not recipients:
                raise ApiError(
                    422, "VALIDATION_FAILED", "选了“只限下列地址”就至少要填一个地址或域名"
                )
            if instance.scope != "read_write":
                raise ApiError(
                    422, "VALIDATION_FAILED", "这个实例的权限范围是只读，打开发送邮件也用不了"
                )
        newly_sending = body.send_enabled and not instance.mail_send_enabled
        added: list[MailAccount] = []
        removed: list[MailAccount] = []
        if body.account_ids is not None:
            added, removed = await mail_agents.set_accounts_for_instance(
                self.db, instance, body.account_ids, user_id=access.user_id
            )
        changed = (
            instance.mail_read_enabled != body.read_enabled
            or instance.mail_send_enabled != body.send_enabled
            or instance.mail_send_scope != scope
            or list(instance.mail_send_recipients or []) != recipients
            or bool(added or removed)
        )
        if changed:
            instance.mail_read_enabled = body.read_enabled
            instance.mail_send_enabled = body.send_enabled
            instance.mail_send_scope = scope
            instance.mail_send_recipients = recipients
            if newly_sending:
                instance.mail_send_by = access.user_id
                instance.mail_send_at = datetime.now(UTC)
            elif not body.send_enabled:
                instance.mail_send_by = None
                instance.mail_send_at = None
            instance.version += 1
            await self.audit.record(
                "agents.instance.mail_updated",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="agent_instance",
                resource_id=instance.id,
                metadata={
                    "read_enabled": body.read_enabled,
                    "send_enabled": body.send_enabled,
                    "send_scope": scope,
                    "recipients": len(recipients),
                    "accounts_added": [a.email for a in added],
                    "accounts_removed": [a.email for a in removed],
                },
                client_ip=client_ip,
            )
            if newly_sending:
                await notifications.notify_admins(
                    self.db,
                    access.workspace.id,
                    type_="mail.agent_send_enabled",
                    severity="warning",
                    title=f"智能体「{instance.name}」获得了发送邮件的权限",
                    body="收件人范围："
                    + ("任何地址" if scope == "any" else "、".join(recipients[:10])),
                    link="/agents",
                    key=f"{instance.id}:{instance.version}",
                )
        await self.db.commit()
        await self.db.refresh(instance)
        mailboxes = await mail_agents.accounts_by_instance(self.db, [instance.id])
        return mail_out(instance, mailboxes[instance.id])

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
            # 时间轴（T-0104）：它正在执行的段记为中断
            await task_runs.interrupt_instance_runs(self.db, instance.id, at=instance.disabled_at)
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
        if not enabled and instance.im_ready and self.im is not None:
            # 尽力而为：回调与接入网关还会按停用状态拒收消息、拒绝连接
            await ImAccounts(self.db, self.im).force_logout([instance.im_user_id])
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
        return await self.build_bundle(
            instance, previous=body.previous, actor_id=access.user_id, client_ip=client_ip
        )

    async def build_bundle(
        self,
        instance: AgentInstance,
        *,
        previous: str,
        actor_id: uuid.UUID | None,
        client_ip: str | None,
    ) -> tuple[str, bytes]:
        """签发新凭证并生成接入包，写审计并提交。网页下载与 SSH 自动部署（T-0089）共用。"""
        if instance.status == "disabled":
            raise ApiError(409, "AGENT_DISABLED", "实例已停用，启用后才能生成接入包")
        public = instance.network_mode == "public"
        settings = self.settings
        configured = settings.agent_public_url if public else settings.agent_intranet_url
        if not configured:
            raise ApiError(
                409,
                "AGENT_INGRESS_NOT_CONFIGURED",
                "这个网络模式的接入入口还没有配置",
                details={"network_mode": instance.network_mode},
            )
        now = _now()
        keep = previous == "keep_24h"
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
        image_proxy_host: str | None = None
        image_proxy_target: str | None = None
        base = configured
        if not public and self.settings.agent_public_url:
            # 图片地址用外网域名（OC-3 ADR 第 15 节）：内网实例经本机代理转发到内网入口
            intranet = urlsplit(configured)
            image_proxy_host = urlsplit(self.settings.agent_public_url).hostname
            image_proxy_target = f"{intranet.hostname}:{intranet.port or 443}"
        if public:
            # 外网模式：公网域名在 OpenClaw 那边解析到本机隧道
            # （Caddy 开启客户端证书时要求 SNI 与 Host 一致，所以不能用 127.0.0.1）
            url = urlsplit(configured)
            host = url.hostname or ""
            tunnel_target = f"{host}:{url.port or 443}"
            base = f"http://{host}:{self.settings.agent_tunnel_port}"
        whitelist = await self._whitelist(instance.workspace_id)
        # 主模型（T-0205）：有绑定时写入受管 provider 与主模型；没有绑定时不写模型段
        # （在实例页设置主模型后由 model-config.mjs 应用）
        from app.domain.agent_model_bindings import AgentModelBindingService

        managed = await AgentModelBindingService(self.db, self.settings).bundle_model(instance)
        model = (
            agent_bundle.BundleModel(
                provider_key=managed.provider_key,
                api=managed.api,
                base_url=managed.base_url,
                model_id=managed.model_id,
                model_name=managed.model_name or managed.model_id,
                secret_ref_id=managed.secret_ref_id,
            )
            if managed is not None
            and managed.provider_key
            and managed.api
            and managed.base_url
            and managed.model_id
            and managed.secret_ref_id
            else None
        )
        data = agent_bundle.build(
            agent_bundle.BundleInput(
                instance_id=str(instance.id),
                instance_name=instance.name,
                network_mode=instance.network_mode,
                token=issued.token,
                base_url=base,
                whitelist=whitelist,
                client_cert_pem=cert.cert_pem if cert else None,
                client_key_pem=cert.key_pem if cert else None,
                tunnel_target=tunnel_target,
                tunnel_port=self.settings.agent_tunnel_port,
                image_proxy_host=image_proxy_host,
                image_proxy_target=image_proxy_target,
                # 外网入口用内部 CA 签发的证书时附带 CA；
                # 没有公网 DNS 的入口主机名映射到工作台地址（T-0088）
                server_ca_pem=cert.ca_pem if cert and settings.agent_public_internal_ca else None,
                tunnel_address=(settings.agent_public_address or None) if tunnel_target else None,
                reflection_cron=settings.agent_reflection_cron,
                reflection_timezone=settings.agent_reflection_timezone,
                # 一台服务器多个实例（T-0145 / T-0146）：服务器指纹的盐与资源上限
                host_salt=agent_bundle.host_salt(str(instance.workspace_id)),
                mem_limit_mb=instance.mem_limit_mb,
                cpus=instance.cpus,
                model=model,
            ),
            now=now,
        )
        instance.bundle_issued_at = now
        instance.bundle_whitelist = whitelist
        instance.version += 1
        await self.audit.record(
            "agents.bundle.issued",
            workspace_ids=[instance.workspace_id],
            actor_id=actor_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={
                "previous": previous,
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
                            ~_connection_secret(),
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
        """密钥库里的一条密钥；连接自己的内部凭据与不存在一样 404。"""
        secret = await self.db.scalar(
            select(AgentSecret).where(
                AgentSecret.id == secret_id,
                AgentSecret.workspace_id == access.workspace.id,
                ~_connection_secret(),
            )
        )
        if secret is None:
            raise _not_found("密钥")
        return secret

    async def _secrets_out(self, secrets_: Sequence[AgentSecret]) -> list[AgentSecretOut]:
        """转换成接口输出：一条查询批量读取这些密钥的全部授权（不逐个密钥查询）。"""
        ids = [s.id for s in secrets_]
        refs: dict[uuid.UUID, list[SecretGrantRef]] = {i: [] for i in ids}
        if ids:
            rows = (
                await self.db.execute(
                    select(AgentSecretGrant, AgentInstance.name)
                    .join(AgentInstance, AgentInstance.id == AgentSecretGrant.instance_id)
                    .where(AgentSecretGrant.secret_id.in_(ids))
                    .order_by(AgentInstance.name, AgentSecretGrant.ref_id, AgentInstance.id)
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
        self,
        access: WorkspaceAccess,
        *,
        cursor: str | None,
        limit: int,
        q: str | None = None,
        kind: SecretKind | None = None,
    ) -> AgentSecretPage:
        """按名称排序的密钥列表（只有元数据）。

        q 是名称或说明的字面包含（不区分大小写），kind 是类型；两者在本工作空间内、分页之前生效，
        total 是同样条件下的全量匹配数（SQL 计数）。游标绑定工作空间与条件，换条件要从第一页开始。
        """
        q = q or None
        key = _secret_list_key(access.workspace.id, q, kind)
        offset = _decode_secret_cursor(cursor, key, filtered=q is not None or kind is not None)
        conditions: list[ColumnElement[bool]] = [
            AgentSecret.workspace_id == access.workspace.id,
            ~_connection_secret(),
        ]
        if q is not None:
            pattern = _like_pattern(q)
            conditions.append(
                or_(
                    AgentSecret.name.ilike(pattern, escape="\\"),
                    AgentSecret.description.ilike(pattern, escape="\\"),
                )
            )
        if kind is not None:
            conditions.append(AgentSecret.kind == kind)
        total = await self.db.scalar(
            select(func.count()).select_from(AgentSecret).where(*conditions)
        )
        rows = (
            await self.db.scalars(
                select(AgentSecret)
                .where(*conditions)
                .order_by(AgentSecret.name, AgentSecret.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        matched = int(total or 0)
        return AgentSecretPage(
            items=await self._secrets_out(rows[:limit]),
            next_cursor=_encode_secret_cursor(offset + limit, key) if len(rows) > limit else None,
            total_estimate=matched,
            total=matched,
        )

    async def get_secret_metadata(
        self, access: WorkspaceAccess, secret_id: uuid.UUID
    ) -> AgentSecretOut:
        """单个密钥的元数据：名称、类型、说明、值版本、轮换时间与授权；从不含值、密文或历史值。"""
        secret = await self._secret(access, secret_id)
        return (await self._secrets_out([secret]))[0]

    def _seal(self, secret_id: uuid.UUID, value_version: int, value: str) -> str:
        return self._box().seal(value, context=agent_secret_context(secret_id, value_version))

    async def create_secret(
        self, access: WorkspaceAccess, body: AgentSecretCreate, *, client_ip: str
    ) -> AgentSecretOut:
        if body.kind == "model_api_key":
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "模型服务的 API Key 请在中转站管理里添加连接保存",
                details={"field": "kind"},
            )
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
        # 中转站管理连接的 Key 的取用（mc_ 引用）记在连接的活动记录里，不出现在密钥库
        stmt = select(AgentSecretAccess).where(
            AgentSecretAccess.workspace_id == access.workspace.id,
            ~AgentSecretAccess.ref_id.startswith("mc_", autoescape=True),
            ~select(ModelConnection.id)
            .where(ModelConnection.credential_secret_id == AgentSecretAccess.secret_id)
            .exists(),
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
            label = ingress if ingress in ("intranet", "public") else "none"
            await ops_metrics.bump(self.redis, "agent_auth_failures", label)
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
            await ops_metrics.bump(
                self.redis, "agent_secret_resolves", "rate_limited", amount=len(wanted)
            )
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
        await ops_metrics.bump(self.redis, "agent_secret_resolves", "ok", amount=len(values))
        await ops_metrics.bump(self.redis, "agent_secret_resolves", "not_found", amount=len(errors))
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

    async def issue_im_token(self, principal: AgentPrincipal) -> ImTokenResponse:
        """实例启动时取 IM 令牌（契约第 7 节）。IM 账号还没就绪时先补一次。"""
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 3600).hit(
                f"agent_rate:im_token:{principal.instance.id}", IM_TOKEN_PER_HOUR
            )
            if retry is not None:
                raise _rate_limited(retry)
        if self.im is None:
            raise im_unavailable()
        instance = principal.instance
        if not instance.im_ready and not await self._provision_im(instance):
            raise im_unavailable()
        try:
            issued = await self.im.user_token(instance.im_user_id, PLATFORM_WEB)
        except ConnectorError as error:
            logger.warning(
                "openim user token failed",
                extra={"instance_id": str(instance.id), "error_kind": error.kind},
            )
            raise im_unavailable() from error
        logger.info("openim user token issued", extra={"instance_id": str(instance.id)})
        expires_at = (
            _now() + timedelta(seconds=issued.expires_in) if issued.expires_in > 0 else None
        )
        # 记下到期时间：Worker 在到期前 7 天提醒管理员（方案第 4.3 节）
        instance.im_token_expires_at = expires_at
        await self.db.commit()
        return ImTokenResponse(
            token=issued.token,
            im_user_id=instance.im_user_id,
            platform_id=PLATFORM_WEB,
            expires_at=expires_at,
            ws_addr=None,
            api_addr=None,
        )
