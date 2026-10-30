"""首次安装：还没有任何用户时，用安装凭据创建 owner 与默认工作空间。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    client_ip,
    get_app_settings,
    get_auth_service,
    origin_checked,
    set_session_cookies,
)
from app.core.config import Settings
from app.domain.auth import AuthService
from app.schemas.auth import SetupRequest, SetupStatus
from app.schemas.common import error_responses
from app.schemas.users import MeResponse

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status", summary="是否需要首次安装（公开）")
async def get_setup_status(
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> SetupStatus:
    return SetupStatus(setup_required=await service.setup_required())


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="首次安装：创建 owner 账户与默认工作空间，并直接登录",
    description="只能成功一次；之后返回 409 SETUP_COMPLETED。成功时设置会话与 CSRF Cookie。",
    dependencies=[Depends(origin_checked)],
    responses=error_responses(403, 409, 422, 429),
)
async def complete_setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> MeResponse:
    user, token = await service.complete_setup(
        setup_token=body.setup_token.get_secret_value(),
        email=str(body.email),
        password=body.password.get_secret_value(),
        display_name=body.display_name,
        workspace_name=body.workspace_name,
        client_ip=client_ip(request, settings),
    )
    set_session_cookies(response, token, settings)
    return await service.me(user)
