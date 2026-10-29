"""登录、登出、会话管理与修改密码。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, Security, status

from app.api.deps import (
    authenticated,
    clear_session_cookies,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_auth_service,
    origin_checked,
    set_session_cookies,
)
from app.core.config import Settings
from app.domain.auth import AuthContext, AuthService
from app.schemas.auth import LoginRequest, PasswordChangeRequest, SessionOut
from app.schemas.common import Page, error_responses
from app.schemas.users import MeResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_signed_in = [Security(authenticated)]
_signed_in_write = [Security(authenticated), Depends(csrf_protected)]


@router.post(
    "/login",
    summary="邮箱密码登录",
    description="成功时设置会话与 CSRF Cookie。"
    "账户不存在与密码错误返回相同的 401 INVALID_CREDENTIALS。",
    dependencies=[Depends(origin_checked)],
    responses=error_responses(401, 403, 422, 429, 501),
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> MeResponse:
    user, token = await service.login(
        email=str(body.email),
        password=body.password.get_secret_value(),
        client_ip=client_ip(request, settings),
    )
    set_session_cookies(response, token, settings)
    return await service.me(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="登出：撤销当前会话并清除 Cookie",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 501),
)
async def logout(
    response: Response,
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    await service.logout(context)
    clear_session_cookies(response, settings)


@router.get(
    "/sessions",
    summary="我的会话列表",
    dependencies=_signed_in,
    responses=error_responses(401, 501),
)
async def list_sessions(
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Page[SessionOut]:
    items = await service.list_sessions(context)
    return Page(items=items, next_cursor=None, total_estimate=len(items))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="撤销我的某个会话",
    description="只能撤销自己的会话；别人的会话按不存在处理（404）。",
    dependencies=_signed_in_write,
    responses=error_responses(401, 403, 404, 501),
)
async def revoke_session(
    session_id: UUID,
    response: Response,
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if await service.revoke_session(context, session_id):
        # 撤销的是当前会话：与登出相同，同时清除 Cookie
        clear_session_cookies(response, settings)


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="修改密码",
    description="成功后撤销该用户的全部会话，并为当前客户端签发新的会话与 CSRF Cookie。"
    "当前密码错误返回 400 INVALID_CURRENT_PASSWORD。",
    dependencies=_signed_in_write,
    responses=error_responses(400, 401, 403, 422, 429, 501),
)
async def change_password(
    body: PasswordChangeRequest,
    response: Response,
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    token = await service.change_password(
        context,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    set_session_cookies(response, token, settings)
