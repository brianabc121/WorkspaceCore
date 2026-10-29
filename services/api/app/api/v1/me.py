"""当前用户与个人资料。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Security

from app.api.deps import authenticated, csrf_protected, get_auth_service
from app.core.errors import not_implemented
from app.domain.auth import AuthContext, AuthService
from app.schemas.common import error_responses
from app.schemas.users import MeResponse, MeUpdate, UserOut

router = APIRouter(tags=["me"])


@router.get(
    "/me",
    summary="当前用户及其所属工作空间（含角色）",
    responses=error_responses(401, 501),
)
async def get_me(
    context: Annotated[AuthContext, Security(authenticated)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> MeResponse:
    return await service.me(context.user)


@router.patch(
    "/me",
    summary="修改个人资料（显示名）",
    dependencies=[Security(authenticated), Depends(csrf_protected)],
    responses=error_responses(401, 403, 422, 501),
)
async def update_me(body: MeUpdate) -> UserOut:
    raise not_implemented("T-0011")
