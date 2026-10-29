"""首次安装：还没有任何用户时，用安装凭据创建 owner 与默认工作空间。"""

from fastapi import APIRouter, Depends, status

from app.api.deps import csrf_protected
from app.core.errors import not_implemented
from app.schemas.auth import SetupRequest, SetupStatus
from app.schemas.common import error_responses
from app.schemas.users import MeResponse

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status", summary="是否需要首次安装（公开）")
async def get_setup_status() -> SetupStatus:
    raise not_implemented("T-0010")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="首次安装：创建 owner 账户与默认工作空间，并直接登录",
    description="只能成功一次；之后返回 409 SETUP_COMPLETED。成功时设置会话与 CSRF Cookie。",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(403, 409, 422, 429, 501),
)
async def complete_setup(body: SetupRequest) -> MeResponse:
    raise not_implemented("T-0010")
