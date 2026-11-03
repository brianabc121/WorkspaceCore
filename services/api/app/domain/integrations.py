"""第三方集成（GitHub）的配置、测试连接与首次同步（Sprint 4 契约第 6 节）。

Token 用凭证加密服务保存，从不返回、不写日志与审计。GitHub 的地址是固定的（api.github.com）。
T-0082 移除了 Vikunja 集成：项目与任务由工作台自己管理（app/domain/tasks.py）。
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import github_sync, sync_lock
from app.domain.audit import AuditTrail
from app.domain.auth import Actor
from app.domain.connectors import (
    ACTIVE_STATUSES,
    ConnectorRuntime,
    account_context,
    api_error,
    sync_state,
)
from app.domain.policy import WorkspaceAccess, not_found
from app.integrations.base import ConnectorError
from app.integrations.github import GitHubClient
from app.jobs.queue import SYNC_INTEGRATION, JobQueue
from app.models import Integration, IntegrationAccount
from app.repositories import integrations as repo
from app.repositories import workspaces as workspace_repo
from app.schemas.integrations import (
    GITHUB_API_URL,
    GITHUB_API_VERSION,
    GITHUB_WEB_URL,
    GitHubIntegrationCreate,
    GitHubRepositoriesRequest,
    GitHubRepositoryList,
    IntegrationCheck,
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


def _not_applicable(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", field], "msg": message, "type": "value_error"}]},
    )


# 每种集成的 Token 前缀（Sprint 4 契约第 6.2 节）；修改时的请求体不带 provider，在这里检查
_TOKEN_PREFIX = {"github": "github_pat_"}


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
        return await self._github_out(integration)

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

    async def create_github(
        self, context: Actor, access: WorkspaceAccess, body: GitHubIntegrationCreate
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
        context: Actor,
        access: WorkspaceAccess,
        integration_id: uuid.UUID,
        body: IntegrationUpdate,
    ) -> IntegrationOut:
        integration = await self._get(access, integration_id)
        if integration.version != body.version:
            raise _version_conflict(integration.version)
        fields = body.model_fields_set - {"version"}
        if body.repositories is not None:
            # 不需要重新测试；下一次同步按新列表进行，移出的仓库写 tombstone
            integration.settings_json = {
                **integration.settings_json,
                "repositories": list(body.repositories),
            }
        if body.api_token is not None and not body.api_token.get_secret_value().startswith(
            _TOKEN_PREFIX.get(integration.provider, "github_pat_")
        ):
            raise _not_applicable("api_token", "Token 与集成的类型不符")
        reset = False
        if "name" in fields and body.name is not None:
            integration.name = body.name
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

    async def github_repositories(
        self, access: WorkspaceAccess, body: GitHubRepositoriesRequest
    ) -> GitHubRepositoryList:
        """Token 能访问的仓库（`/user/repos`，只需 Metadata 只读），网页上让管理员勾选（T-0091）。

        新 Token 只用来调用这一次，不保存、不进日志与审计；不给 Token 时用已保存的那份。
        """
        if body.api_token is not None:
            client = GitHubClient(
                self.runtime.http("provision:github"),
                body.api_token.get_secret_value(),
                self.settings.github_api_url,
            )
        else:
            integration = next(
                (
                    i
                    for i in await repo.list_for_workspace(self.db, access.workspace.id)
                    if i.provider == "github"
                ),
                None,
            )
            account = await repo.get_account(self.db, integration.id) if integration else None
            if integration is None or account is None:
                raise ApiError(409, "INTEGRATION_UNAVAILABLE", "尚未保存 GitHub Token")
            client = self.runtime.github(integration, account)
        try:
            listing = await client.list_user_repos()
        except ConnectorError as error:
            raise api_error(error) from error
        names = sorted(
            {item["full_name"] for item in listing.items if isinstance(item.get("full_name"), str)}
        )
        return GitHubRepositoryList(repositories=names, complete=listing.complete)

    async def test(
        self, context: Actor, access: WorkspaceAccess, integration_id: uuid.UUID
    ) -> IntegrationTestResult:
        integration = await self._get(access, integration_id)
        account = await repo.get_account(self.db, integration.id)
        if account is None:
            raise ApiError(409, "INTEGRATION_UNAVAILABLE", "尚未保存 Token")
        return await self._test_github(
            context, access, integration, self.runtime.github(integration, account)
        )

    async def _test_github(
        self,
        context: Actor,
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

    # ---------------------------------------------------------------- 手动同步

    async def request_sync(
        self, context: Actor, access: WorkspaceAccess, integration_id: uuid.UUID
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
