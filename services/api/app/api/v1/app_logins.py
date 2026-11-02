"""Vikunja、n8n 管理界面的登录账号（T-0080，docs/decisions/2026-11-02-app-logins.md）。

只有 admin 以上能读写（用 X-Workspace-Id 指定工作空间）；member、viewer 一律 403。
列表不含密码；查看密码用 POST …/reveal（要 CSRF，写审计），响应带 Cache-Control: no-store。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, Security, status

from app.api.deps import authenticated, csrf_protected, get_app_login_service, workspace_admin
from app.domain.app_logins import AppLoginService
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.schemas.app_logins import App, AppLoginList, AppLoginOut, AppLoginSecret, AppLoginUpsert
from app.schemas.common import error_responses

router = APIRouter(tags=["app-logins"], dependencies=[Security(authenticated)])

Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[AppLoginService, Depends(get_app_login_service)]
_write = [Depends(csrf_protected)]
NO_STORE = "no-store"


@router.get(
    "/app-logins",
    summary="Vikunja、n8n 的登录账号（admin）",
    description="固定返回 vikunja、n8n 两项；没有保存时 configured 为 false。不含密码。",
    responses=error_responses(401, 403, 404, 422),
)
async def list_app_logins(access: Admin, service: Service, response: Response) -> AppLoginList:
    response.headers["Cache-Control"] = NO_STORE
    return await service.list(access)


@router.put(
    "/app-logins/{app}",
    summary="保存或修改登录账号（admin，乐观并发）",
    description="新建时必须给 username；修改时必须带 version。password 不填表示不改密码。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def put_app_login(
    app: App,
    access: Admin,
    context: Context,
    service: Service,
    body: AppLoginUpsert,
    response: Response,
) -> AppLoginOut:
    response.headers["Cache-Control"] = NO_STORE
    return await service.put(context, access, app, body)


@router.post(
    "/app-logins/{app}/reveal",
    summary="查看保存的密码（admin，写审计）",
    description="每次调用都写审计 app_login.revealed；响应不缓存。还没有保存密码时返回 404。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422, 503),
)
async def reveal_app_login(
    app: App, access: Admin, context: Context, service: Service, response: Response
) -> AppLoginSecret:
    response.headers["Cache-Control"] = NO_STORE
    return await service.reveal(context, access, app)


@router.delete(
    "/app-logins/{app}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除保存的账号（admin，乐观并发；不影响 Vikunja、n8n 里的账号）",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def delete_app_login(
    app: App,
    access: Admin,
    context: Context,
    service: Service,
    version: Annotated[int, Query(ge=1, description="读取时的版本")],
) -> None:
    await service.delete(context, access, app, version)
