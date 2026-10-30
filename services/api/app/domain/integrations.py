"""第三方集成的配置、测试连接与首次同步（Sprint 3 契约第 4 节；GitHub 见 Sprint 4 契约第 6 节）。

Token 与 Webhook 签名密钥用凭证加密服务保存，从不返回、不写日志与审计。
地址在保存时做 SSRF 检查（解析出的地址必须是公网，除非主机名在 INTEGRATION_ALLOWED_HOSTS 里），
连接时 ConnectorHttp 会再检查一次。
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import github_sync, projection, sync_lock
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.connectors import (
    ACTIVE_STATUSES,
    ConnectorRuntime,
    account_context,
    sync_state,
    webhook_context,
)
from app.domain.policy import WorkspaceAccess, not_found
from app.integrations.base import ConnectorError
from app.integrations.github import GitHubClient
from app.integrations.http import OutboundPolicy
from app.integrations.vikunja import VikunjaClient
from app.jobs.queue import REGISTER_WEBHOOKS, SYNC_INTEGRATION, JobQueue
from app.models import Integration, IntegrationAccount
from app.repositories import integrations as repo
from app.repositories import workspaces as workspace_repo
from app.schemas.integrations import (
    GITHUB_API_URL,
    GITHUB_API_VERSION,
    GITHUB_WEB_URL,
    VIKUNJA_API_VERSION,
    VIKUNJA_TESTED_VERSION,
    GitHubIntegrationCreate,
    IntegrationCheck,
    IntegrationCreate,
    IntegrationOut,
    IntegrationTestResult,
    IntegrationUpdate,
    RepositoryCheck,
    RepositoryPermissionCheck,
    SyncRequestOut,
    WebhookRegistration,
)

logger = logging.getLogger("app.integrations")

MAX_PER_PROVIDER = 1  # V1（契约第 4.3 节）
CheckName = Literal["reachable", "version", "token", "tasks_read", "webhooks"]


def _url_not_allowed(field: str) -> ApiError:
    # 与请求校验失败相同的格式；不回显地址
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [
                {
                    "loc": ["body", field],
                    "msg": "该地址不被允许：私有或内网地址需要在部署配置 "
                    "INTEGRATION_ALLOWED_HOSTS 中显式允许",
                    "type": "value_error",
                }
            ]
        },
    )


def _not_applicable(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", field], "msg": message, "type": "value_error"}]},
    )


# 每种集成的 Token 前缀（Sprint 4 契约第 6.2 节）；修改时的请求体不带 provider，在这里检查
_TOKEN_PREFIX = {"vikunja": "tk_", "github": "github_pat_"}


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


class IntegrationService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: ConnectorRuntime,
        *,
        redis: Redis | None = None,
        jobs: JobQueue | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.redis = redis
        self.jobs = jobs
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 读取

    async def out(self, integration: Integration) -> IntegrationOut:
        if integration.provider == "github":
            return await self._github_out(integration)
        account = await repo.get_account(self.db, integration.id)
        cursor = await repo.get_cursor(self.db, integration.id, "task")
        settings = integration.settings_json
        webhooks = settings.get("webhooks") or {}
        last_tested = settings.get("last_tested_at")
        return IntegrationOut(
            id=integration.id,
            provider="vikunja",
            name=integration.name,
            base_url=integration.base_url,
            public_url=integration.public_url,
            status=integration.status,
            token_set=account is not None,
            token_expires_at=account.expires_at if account else None,
            tested_version=str(settings.get("tested_version") or VIKUNJA_TESTED_VERSION),
            api_version=str(settings.get("api_version") or VIKUNJA_API_VERSION),
            instance_version=settings.get("instance_version"),
            last_tested_at=datetime.fromisoformat(last_tested) if last_tested else None,
            sync=sync_state(
                integration, cursor, stale_after_seconds=self.settings.sync_stale_after_seconds
            ),
            webhooks=WebhookRegistration(
                mode=webhooks.get("mode", "unavailable"),
                registered_projects=int(webhooks.get("registered_projects", 0)),
                reason=webhooks.get("reason", "not_tested"),
            ),
            version=integration.version,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
        )

    async def _github_out(self, integration: Integration) -> IntegrationOut:
        account = await repo.get_account(self.db, integration.id)
        cursor = await repo.get_cursor(self.db, integration.id, github_sync.CURSOR)
        last_tested = integration.settings_json.get("last_tested_at")
        return IntegrationOut(
            id=integration.id,
            provider="github",
            name=integration.name,
            base_url=GITHUB_API_URL,
            public_url=GITHUB_WEB_URL,
            status=integration.status,
            token_set=account is not None,
            token_expires_at=account.expires_at if account else None,
            tested_version=GITHUB_API_VERSION,
            api_version=GITHUB_API_VERSION,
            instance_version=None,
            last_tested_at=datetime.fromisoformat(last_tested) if last_tested else None,
            sync=sync_state(
                integration, cursor, stale_after_seconds=self.settings.sync_stale_after_seconds
            ),
            # V1 不启用 GitHub Webhook（核验 ADR 第 6 节）
            webhooks=WebhookRegistration(
                mode="unavailable", registered_projects=0, reason="not_supported"
            ),
            repositories=github_sync.repositories(integration),
            version=integration.version,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
        )

    async def list_integrations(self, access: WorkspaceAccess) -> list[IntegrationOut]:
        return [
            await self.out(i) for i in await repo.list_for_workspace(self.db, access.workspace.id)
        ]

    async def _get(self, access: WorkspaceAccess, integration_id: uuid.UUID) -> Integration:
        integration = await repo.get_for_workspace(
            self.db, integration_id=integration_id, workspace_id=access.workspace.id, lock=True
        )
        if integration is None:
            raise not_found()
        return integration

    # ---------------------------------------------------------------- 写入

    async def _check_url(self, url: str, field: str) -> None:
        try:
            await OutboundPolicy(self.settings.allowed_integration_hosts).check(url)
        except ConnectorError as error:
            if error.reason == "dns_failed":
                return  # 暂时解析不到不阻止保存；测试连接时会报告不可达
            raise _url_not_allowed(field) from None

    async def create(
        self, context: AuthContext, access: WorkspaceAccess, body: IntegrationCreate
    ) -> IntegrationOut:
        box = self.runtime.credentials()
        # public_url 只给浏览器打开，服务端不访问，不做地址检查（模型已限制为 http(s)）
        await self._check_url(body.base_url, "base_url")
        await workspace_repo.lock(self.db, access.workspace.id)
        if (
            await repo.count_provider(self.db, access.workspace.id, body.provider)
            >= MAX_PER_PROVIDER
        ):
            raise ApiError(409, "LIMIT_EXCEEDED", "每个工作空间最多配置 1 个 Vikunja 集成")
        integration_id = uuid.uuid4()
        integration = Integration(
            id=integration_id,
            workspace_id=access.workspace.id,
            provider=body.provider,
            name=body.name,
            base_url=body.base_url,
            public_url=body.public_url or body.base_url,
            status="pending",
            settings_json={
                "tested_version": VIKUNJA_TESTED_VERSION,
                "api_version": VIKUNJA_API_VERSION,
            },
            webhook_secret_ref=box.seal(
                secrets.token_hex(32), context=webhook_context(integration_id)
            ),
        )
        self.db.add(integration)
        await self.db.flush()
        account_id = uuid.uuid4()
        self.db.add(
            IntegrationAccount(
                id=account_id,
                integration_id=integration_id,
                user_id=context.user.id,
                auth_type="api_token",
                credential_ref=box.seal(
                    body.api_token.get_secret_value(), context=account_context(account_id)
                ),
                expires_at=body.token_expires_at,
            )
        )
        await self.audit.record(
            "integration.created",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="integration",
            resource_id=integration_id,
            metadata={"provider": body.provider},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(integration)
        return await self.out(integration)

    async def create_github(
        self, context: AuthContext, access: WorkspaceAccess, body: GitHubIntegrationCreate
    ) -> IntegrationOut:
        """地址固定为 api.github.com（契约第 6.1 节），不需要 SSRF 保存检查与 Webhook 密钥。"""
        box = self.runtime.credentials()
        await workspace_repo.lock(self.db, access.workspace.id)
        if await repo.count_provider(self.db, access.workspace.id, "github") >= MAX_PER_PROVIDER:
            raise ApiError(409, "LIMIT_EXCEEDED", "每个工作空间最多配置 1 个 GitHub 集成")
        integration = Integration(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            provider="github",
            name=body.name,
            base_url=GITHUB_API_URL,
            public_url=GITHUB_WEB_URL,
            status="pending",
            settings_json={
                "tested_version": GITHUB_API_VERSION,
                "api_version": GITHUB_API_VERSION,
                "repositories": list(body.repositories),
            },
        )
        self.db.add(integration)
        await self.db.flush()
        account_id = uuid.uuid4()
        self.db.add(
            IntegrationAccount(
                id=account_id,
                integration_id=integration.id,
                user_id=context.user.id,
                auth_type="api_token",
                credential_ref=box.seal(
                    body.api_token.get_secret_value(), context=account_context(account_id)
                ),
                expires_at=body.token_expires_at,
            )
        )
        await self.audit.record(
            "integration.created",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="integration",
            resource_id=integration.id,
            metadata={"provider": "github", "repositories": len(body.repositories)},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(integration)
        return await self.out(integration)

    async def update(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        integration_id: uuid.UUID,
        body: IntegrationUpdate,
    ) -> IntegrationOut:
        integration = await self._get(access, integration_id)
        if integration.version != body.version:
            raise _version_conflict(integration.version)
        fields = body.model_fields_set - {"version"}
        if "repositories" in fields and integration.provider != "github":
            raise _not_applicable("repositories", "只有 GitHub 集成可以设置仓库")
        if integration.provider == "github":
            for field in ("base_url", "public_url"):
                if field in fields:
                    raise _not_applicable(field, "GitHub 集成的地址是固定的")
            if body.repositories is not None:
                # 不需要重新测试；下一次同步按新列表进行，移出的仓库写 tombstone
                integration.settings_json = {
                    **integration.settings_json,
                    "repositories": list(body.repositories),
                }
        if body.api_token is not None and not body.api_token.get_secret_value().startswith(
            _TOKEN_PREFIX.get(integration.provider, "tk_")
        ):
            raise _not_applicable("api_token", "Token 与集成的类型不符")
        reset = False
        if "name" in fields and body.name is not None:
            integration.name = body.name
        if "base_url" in fields and body.base_url is not None:
            await self._check_url(body.base_url, "base_url")
            if body.base_url != integration.base_url:
                reset = True
            integration.base_url = body.base_url
        if "public_url" in fields:
            integration.public_url = body.public_url or integration.base_url
        account = await repo.get_account(self.db, integration.id)
        if "api_token" in fields and body.api_token is not None:
            box = self.runtime.credentials()
            if account is None:
                account = IntegrationAccount(
                    id=uuid.uuid4(),
                    integration_id=integration.id,
                    user_id=context.user.id,
                    auth_type="api_token",
                    credential_ref="",
                )
                self.db.add(account)
            account.credential_ref = box.seal(
                body.api_token.get_secret_value(), context=account_context(account.id)
            )
            account.user_id = context.user.id
            reset = True
        if "token_expires_at" in fields and account is not None:
            account.expires_at = body.token_expires_at
        if reset and integration.status != "disabled":
            integration.status = "pending"
        if "enabled" in fields and body.enabled is not None:
            if not body.enabled:
                integration.status = "disabled"
            elif integration.status == "disabled":
                integration.status = "pending"  # 重新启用：需要再测试一次
        integration.version += 1
        await self.audit.record(
            "integration.updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="integration",
            resource_id=integration.id,
            # Token 只记“已更换”：字段名本身不含内容
            metadata={"fields": sorted(fields)},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(integration)
        return await self.out(integration)

    # ---------------------------------------------------------------- 测试连接

    async def test(
        self, context: AuthContext, access: WorkspaceAccess, integration_id: uuid.UUID
    ) -> IntegrationTestResult:
        integration = await self._get(access, integration_id)
        account = await repo.get_account(self.db, integration.id)
        if account is None:
            raise ApiError(409, "INTEGRATION_UNAVAILABLE", "尚未保存 Token")
        if integration.provider == "github":
            return await self._test_github(
                context, access, integration, self.runtime.github(integration, account)
            )
        client = self.runtime.vikunja(integration, account)
        checks: list[IntegrationCheck] = []
        warnings: list[str] = []
        instance_version: str | None = None
        webhooks: dict[str, Any] = {"mode": "unavailable", "registered_projects": 0}
        failure: ConnectorError | None = None
        try:
            info = await client.info()
            checks.append(IntegrationCheck(name="reachable", ok=True))
            instance_version = info.version
            compatible = instance_version == VIKUNJA_TESTED_VERSION
            checks.append(
                IntegrationCheck(
                    name="version", ok=compatible, code=None if compatible else "untested_version"
                )
            )
            if not compatible:
                warnings.append(
                    f"实例版本 {instance_version or '未知'} 未经核验"
                    f"（已核验 {VIKUNJA_TESTED_VERSION}）"
                )
            step: CheckName = "token"
            projects = await client.first_project_page()
            checks.append(IntegrationCheck(name="token", ok=True))
            step = "tasks_read"
            await client.first_task_page()
            checks.append(IntegrationCheck(name="tasks_read", ok=True))
            webhooks = await self._webhook_permission(client, projects, checks)
        except ConnectorError as error:
            failure = error
            failed: CheckName = "reachable" if not checks else step
            checks.append(IntegrationCheck(name=failed, ok=False, code=error.reason or error.kind))
        ok = failure is None
        if integration.status != "disabled":
            integration.status = "active" if ok else _status_after_failure(failure)
        integration.settings_json = {
            **integration.settings_json,
            "instance_version": instance_version,
            "last_tested_at": datetime.now(UTC).isoformat(),
            "webhooks": webhooks,
        }
        integration.version += 1
        await self.audit.record(
            "integration.tested",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="integration",
            resource_id=integration.id,
            metadata={"ok": ok, "status": integration.status},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        if ok and integration.status == "active":
            await self._initial_sync(integration, client)
            if webhooks.get("mode") == "auto":
                await self._enqueue_registration(integration.id)
        return IntegrationTestResult(
            ok=ok,
            status=integration.status,
            instance_version=instance_version,
            api_version=VIKUNJA_API_VERSION if instance_version else None,
            tested_version=VIKUNJA_TESTED_VERSION,
            compatible=instance_version == VIKUNJA_TESTED_VERSION,
            checks=checks,
            warnings=warnings,
        )

    async def _test_github(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        integration: Integration,
        client: GitHubClient,
    ) -> IntegrationTestResult:
        """契约第 6.3 节：可达、API 版本、Token 有效，再逐个仓库检查四项读权限。"""
        names = github_sync.repositories(integration)
        checks: list[IntegrationCheck] = []
        warnings: list[str] = []
        repo_checks: list[RepositoryCheck] = []
        failure: ConnectorError | None = None
        try:
            first = await client.get_repo(names[0]) if names else None
            checks += [
                IntegrationCheck(name="reachable", ok=True),
                IntegrationCheck(name="version", ok=True),
                IntegrationCheck(name="token", ok=True),
            ]
            if first is not None and first.oauth_scopes:
                warnings.append("这是经典 Token，权限过大；请改用细粒度 Personal Access Token")
            for name in names:
                repo_checks.append(await _check_repository(client, name))
        except ConnectorError as error:
            failure = error
            checks += _failed_github_checks(error, already=len(checks))
        checks.append(IntegrationCheck(name="webhooks", ok=False, code="not_supported"))
        ok = failure is None and all(r.ok for r in repo_checks)
        if integration.status != "disabled":
            integration.status = "active" if failure is None else _status_after_failure(failure)
        integration.settings_json = {
            **integration.settings_json,
            "last_tested_at": datetime.now(UTC).isoformat(),
        }
        integration.version += 1
        await self.audit.record(
            "integration.tested",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="integration",
            resource_id=integration.id,
            metadata={"ok": ok, "status": integration.status},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        if failure is None and integration.status == "active":
            await self._initial_github_sync(integration, client)
        return IntegrationTestResult(
            ok=ok,
            status=integration.status,
            instance_version=None,
            api_version=GITHUB_API_VERSION,
            tested_version=GITHUB_API_VERSION,
            compatible=True,
            checks=checks,
            warnings=warnings,
            repositories=repo_checks,
        )

    async def _initial_github_sync(self, integration: Integration, client: GitHubClient) -> None:
        """测试通过后立即同步一次；失败（包括限流）只记录在同步状态里。"""
        try:
            await github_sync.sync(self.db, integration, client, full=True)
        except ConnectorError as error:
            logger.warning(
                "initial github sync failed",
                extra={"integration_id": str(integration.id), "error_type": error.kind},
            )
        await self.db.commit()

    async def _webhook_permission(
        self, client: VikunjaClient, projects: list[Any], checks: list[IntegrationCheck]
    ) -> dict[str, Any]:
        """可选检查：Token 能否注册 Webhook（没有时只靠周期对账，不影响 ok）。"""
        if not projects:
            checks.append(IntegrationCheck(name="webhooks", ok=False, code="no_projects"))
            return {"mode": "unavailable", "registered_projects": 0, "reason": "no_projects"}
        allowed = await client.can_manage_webhooks(projects[0].id)
        checks.append(
            IntegrationCheck(
                name="webhooks", ok=allowed, code=None if allowed else "missing_permission"
            )
        )
        return {
            "mode": "auto" if allowed else "unavailable",
            "registered_projects": 0,
            "reason": None if allowed else "missing_permission",
        }

    async def _enqueue_registration(self, integration_id: uuid.UUID) -> None:
        """让 Worker 为每个项目注册 Webhook（契约第 9 节）。

        投递失败不影响测试结果：Worker 每次全量对账都会检查并补注册。
        """
        if self.jobs is None:
            return
        try:
            await self.jobs.enqueue(REGISTER_WEBHOOKS, str(integration_id))
        except Exception as error:
            logger.warning(
                "webhook registration not queued",
                extra={"integration_id": str(integration_id), "error_type": type(error).__name__},
            )

    # ---------------------------------------------------------------- 手动同步

    async def request_sync(
        self, context: AuthContext, access: WorkspaceAccess, integration_id: uuid.UUID
    ) -> SyncRequestOut:
        """加锁并投递一次全量同步；锁由 Worker 的作业接手，结束时释放。

        锁的约定见 app/domain/sync_lock.py。
        """
        integration = await self._get(access, integration_id)
        account = await repo.get_account(self.db, integration.id)
        if integration.status not in ACTIVE_STATUSES or account is None:
            raise ApiError(409, "INTEGRATION_UNAVAILABLE", "集成尚未测试通过或已停用，请先测试连接")
        if self.redis is None or self.jobs is None:
            raise RuntimeError("后台任务队列未初始化")
        owner = sync_lock.new_owner("manual")
        if not await sync_lock.acquire(self.redis, integration.id, owner):
            raise ApiError(409, "SYNC_IN_PROGRESS", "同步正在进行，请稍后再试")
        try:
            await self.jobs.enqueue(SYNC_INTEGRATION, str(integration.id), owner)
        except Exception:
            await sync_lock.release(self.redis, integration.id, owner)
            raise
        queued_at = datetime.now(UTC)
        await self.audit.record(
            "integration.sync_requested",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="integration",
            resource_id=integration.id,
            metadata={"mode": "full"},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return SyncRequestOut(integration_id=integration.id, mode="full", queued_at=queued_at)

    async def _initial_sync(self, integration: Integration, client: VikunjaClient) -> None:
        """测试通过后立即做一次全量同步，项目与任务马上可用；失败只记录在同步状态里。"""
        try:
            await projection.full_sync(self.db, integration, client)
        except ConnectorError as error:
            logger.warning(
                "initial sync failed",
                extra={"integration_id": str(integration.id), "error_type": error.kind},
            )
        await self.db.commit()


def _status_after_failure(error: ConnectorError | None) -> str:
    if error is not None and error.kind == "AUTH_FAILED" and not error.resource_level:
        return "unauthorized"
    return "unavailable"


# GitHub 测试连接：每项权限对应的读取端点（核验 ADR 第 3 节）
_GITHUB_PROBES: tuple[
    tuple[Literal["metadata", "issues", "pull_requests", "actions"], str], ...
] = (
    ("metadata", ""),
    ("issues", "/issues"),
    ("pull_requests", "/pulls"),
    ("actions", "/actions/runs"),
)


async def _check_repository(client: GitHubClient, full_name: str) -> RepositoryCheck:
    """逐项读一条记录；缺少权限（403）或看不到（404）只记在这个仓库上。"""
    results: list[RepositoryPermissionCheck] = []
    for name, suffix in _GITHUB_PROBES:
        try:
            await client.probe(f"/repos/{full_name}{suffix}")
            results.append(RepositoryPermissionCheck(name=name, ok=True))
        except ConnectorError as error:
            if error.kind == "NOT_FOUND":
                code = "not_found"
            elif error.kind == "AUTH_FAILED" and error.resource_level:
                code = "missing_permission"
            else:
                raise
            results.append(RepositoryPermissionCheck(name=name, ok=False, code=code))
            if name == "metadata":
                break  # 仓库本身看不到，其余几项没有意义
    return RepositoryCheck(full_name=full_name, ok=all(r.ok for r in results), checks=results)


def _failed_github_checks(error: ConnectorError, *, already: int) -> list[IntegrationCheck]:
    """第一个请求失败时，按错误判断是哪一项没通过。"""
    if already:
        return []  # 前几项已通过，失败发生在逐仓库检查（例如限流），只体现在整体结果里
    code = error.reason or error.kind
    if error.not_sent or error.reason in {
        "connect_failed",
        "dns_failed",
        "destination_not_allowed",
    }:
        return [IntegrationCheck(name="reachable", ok=False, code=code)]
    reachable = IntegrationCheck(name="reachable", ok=True)
    if error.reason == "api_version_unsupported":
        return [reachable, IntegrationCheck(name="version", ok=False, code=code)]
    # Token 被拒、第一个仓库看不到或限流：可达与版本没问题，记在 token 这一项上
    version = IntegrationCheck(name="version", ok=True)
    return [reachable, version, IntegrationCheck(name="token", ok=False, code=code)]
