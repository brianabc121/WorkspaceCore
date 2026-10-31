"""路由共用的依赖：数据库会话、会话 Cookie、CSRF 校验、工作空间权限。

约定见 docs/decisions/2026-10-30-sprint-1-auth-contract.md。参数签名决定 OpenAPI 的样子，
修改时注意保持 openapi.json 不变（CI 会检查）。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request, Response, Security
from fastapi.security import APIKeyCookie
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, origin_of
from app.core.errors import ApiError
from app.domain import policy
from app.domain.agents import AgentService
from app.domain.auth import AuthContext, AuthService
from app.domain.automations import AutomationService
from app.domain.calendar import CalendarService
from app.domain.connectors import ConnectorRuntime
from app.domain.dashboards import DashboardService
from app.domain.developer import DeveloperService
from app.domain.integrations import IntegrationService
from app.domain.knowledge import KnowledgeService
from app.domain.notifications import NotificationService
from app.domain.operations import DeploymentHealth, OperationsService
from app.domain.search import SearchService
from app.domain.security import csrf_token_for, csrf_token_matches
from app.domain.tasks import TaskService
from app.domain.workspaces import WorkspaceService
from app.jobs.queue import JobQueue
from app.schemas.workspaces import Role

SESSION_COOKIE = "ws_session"
CSRF_COOKIE = "ws_csrf"
CSRF_HEADER = "X-CSRF-Token"
WORKSPACE_HEADER = "X-Workspace-Id"

session_cookie = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="SessionCookie",
    description="登录后由服务端设置的 HttpOnly 会话 Cookie；浏览器同源请求会自动携带",
    auto_error=False,
)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_auth_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthService:
    return AuthService(db, redis, settings)


def get_dashboard_service(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DashboardService:
    return DashboardService(
        db, settings, request.app.state.module_providers, request.app.state.overview_gate
    )


def get_workspace_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> WorkspaceService:
    return WorkspaceService(db, settings)


def get_jobs(request: Request) -> JobQueue:
    jobs: JobQueue = request.app.state.jobs
    return jobs


def get_connectors(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connectors
    return runtime


def get_integration_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    connectors: Annotated[ConnectorRuntime, Depends(get_connectors)],
    redis: Annotated[Redis, Depends(get_redis)],
    jobs: Annotated[JobQueue, Depends(get_jobs)],
) -> IntegrationService:
    return IntegrationService(db, settings, connectors, redis=redis, jobs=jobs)


def get_knowledge_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> KnowledgeService:
    return KnowledgeService(db, settings)


def get_calendar_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CalendarService:
    return CalendarService(db, settings)


def get_developer_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DeveloperService:
    return DeveloperService(db, settings)


def get_search_service(db: Annotated[AsyncSession, Depends(get_db)]) -> SearchService:
    return SearchService(db)


def deployment_health(app: FastAPI) -> DeploymentHealth:
    """部署级检查；readiness_checks 与 redis 在 lifespan 里才创建，所以每次现取。"""
    return DeploymentHealth(
        app.state.redis,
        app.state.settings,
        app.state.connectors,
        app.state.readiness_checks,
        app.state.service_cache,
    )


def get_agent_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AgentService:
    return AgentService(db, settings, redis)


def get_automation_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    connectors: Annotated[ConnectorRuntime, Depends(get_connectors)],
) -> AutomationService:
    return AutomationService(db, settings, connectors)


def get_notification_service(db: Annotated[AsyncSession, Depends(get_db)]) -> NotificationService:
    return NotificationService(db)


def get_operations_service(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> OperationsService:
    return OperationsService(db, deployment_health(request.app))


def get_task_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    connectors: Annotated[ConnectorRuntime, Depends(get_connectors)],
) -> TaskService:
    return TaskService(db, settings, connectors)


async def authenticated(
    request: Request,
    token: Annotated[str | None, Security(session_cookie)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthContext:
    """要求有效会话，否则 401 UNAUTHORIZED。"""
    return await service.authenticate(token, client_ip=client_ip(request, service.settings))


def _csrf_failed() -> ApiError:
    return ApiError(403, "CSRF_FAILED", "请求来源或 CSRF 校验失败，请刷新页面后重试")


# 两个依赖声明完全相同的请求头参数，OpenAPI 中的写法保持一致
CsrfHeader = Annotated[
    str | None,
    Header(
        alias=CSRF_HEADER,
        description=(
            f"改变状态的请求必须携带，值为 {CSRF_COOKIE} Cookie 的内容；"
            "缺失或不匹配返回 403 CSRF_FAILED"
        ),
    ),
]


def _check_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is None and (referer := request.headers.get("referer")) is not None:
        origin = referer
    if origin is not None and origin_of(origin) not in settings.trusted_origins:
        raise _csrf_failed()


async def csrf_protected(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    csrf_token: CsrfHeader = None,
) -> None:
    """校验请求来源；带会话 Cookie 的请求还必须携带与会话匹配的 CSRF Token。"""
    _check_origin(request, settings)
    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token and not (
        csrf_token and csrf_token_matches(settings.session_key, session_token, csrf_token)
    ):
        raise _csrf_failed()


async def origin_checked(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    csrf_token: CsrfHeader = None,
) -> None:
    """登录与首次安装：此时还没有可用的会话，只校验请求来源。

    不校验 CSRF Token：浏览器里残留的过期会话 Cookie，或 SESSION_SECRET 轮换后失效的 Token，
    都不应该让用户无法重新登录。跨站伪造由来源校验与 SameSite=Lax 防护。
    """
    _check_origin(request, settings)


WorkspaceHeader = Annotated[
    UUID,
    Header(alias=WORKSPACE_HEADER, description="当前工作空间；不是成员时返回 404"),
]


def workspace_role(minimum: Role) -> Callable[..., Awaitable[policy.WorkspaceAccess]]:
    """工作空间范围的接口：用请求头指定工作空间，并要求至少 minimum 角色（404 / 403 见 policy）。"""

    async def dependency(
        workspace_id: WorkspaceHeader,
        context: Annotated[AuthContext, Security(authenticated)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> policy.WorkspaceAccess:
        return await policy.require_role(
            db, user_id=context.user.id, workspace_id=workspace_id, minimum=minimum
        )

    return dependency


workspace_viewer = workspace_role(Role.viewer)
workspace_member = workspace_role(Role.member)
workspace_admin = workspace_role(Role.admin)
workspace_owner = workspace_role(Role.owner)


def client_ip(request: Request, settings: Settings) -> str:
    """限流用的客户端地址；只有在可信反向代理之后才采用 X-Forwarded-For 最右侧的值。"""
    forwarded = request.headers.get("x-forwarded-for")
    if settings.trust_forwarded_for and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def set_session_cookies(response: Response, token: str, settings: Settings) -> None:
    cookies = (
        (SESSION_COOKIE, token, True),
        (CSRF_COOKIE, csrf_token_for(settings.session_key, token), False),
    )
    for name, value, httponly in cookies:
        response.set_cookie(
            name,
            value,
            max_age=settings.session_ttl_days * 24 * 3600,
            httponly=httponly,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=httponly, samesite="lax"
        )
