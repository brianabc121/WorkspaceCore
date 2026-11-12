"""工作空间的图片存储设置（T-0223）：网页“设置 → 图片存储”。owner 修改，admin 只读。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_db,
    workspace_admin,
    workspace_owner,
)
from app.core.config import Settings
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.domain.storage_settings import StorageSettingsService
from app.schemas.common import error_responses
from app.schemas.storage import StorageSettingsIn, StorageSettingsOut

router = APIRouter(tags=["storage"])

Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Context = Annotated[AuthContext, Security(authenticated)]


def get_service(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> StorageSettingsService:
    # storage_transport 只在测试里设置（模拟 R2 / OSS）；生产环境直连对象存储
    return StorageSettingsService(
        db,
        settings,
        redis=request.app.state.redis,
        transport=getattr(request.app.state, "storage_transport", None),
    )


Service = Annotated[StorageSettingsService, Depends(get_service)]


@router.get(
    "/settings/storage",
    summary="图片存储设置（owner / admin）",
    description="主存储、副本与 R2 / OSS 的连接信息。不返回密钥，只说是否已设置；"
    "in_use 是还存着本工作空间图片或快照的存储。",
    responses=error_responses(401, 403, 404),
)
async def get_storage_settings(
    access: Admin, service: Service, response: Response
) -> StorageSettingsOut:
    response.headers["Cache-Control"] = "no-store"
    return await service.get(access.workspace.id)


@router.put(
    "/settings/storage",
    summary="保存图片存储设置（owner，乐观并发，先自检再保存）",
    description=(
        "带上读取时的 version。密钥不填（null）保持不变，空字符串清除。"
        "保存前对主存储与副本各写、读、删一个探测对象：不通过返回 422 STORAGE_CHECK_FAILED"
        "（details.checks 是各自的结果），设置不变。还存着文件的存储不能清空连接信息"
        "（409 STORAGE_IN_USE）。"
    ),
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def put_storage_settings(
    request: Request,
    access: Owner,
    context: Context,
    service: Service,
    body: StorageSettingsIn,
    response: Response,
) -> StorageSettingsOut:
    response.headers["Cache-Control"] = "no-store"
    out = await service.update(
        access.workspace.id,
        body,
        actor_id=context.user.id,
        client_ip=client_ip(request, service.settings),
    )
    await service.db.commit()
    return out
