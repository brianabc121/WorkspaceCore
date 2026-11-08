"""模块接口的启用检查：宿主给每个模块路由自动加上（app/api/v1/__init__.py）。"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, workspace_viewer
from app.domain.policy import WorkspaceAccess
from app.modules.service import disabled_error, is_enabled


def require_module(key: str) -> Callable[..., Awaitable[None]]:
    """模块在请求头指定的工作空间里停用时，返回 404 MODULE_DISABLED。

    先做成员检查（不是成员时 404 NOT_FOUND，和其他接口一致），再查启用状态。
    """

    async def module_enabled(
        access: Annotated[WorkspaceAccess, Depends(workspace_viewer)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        if not await is_enabled(db, access.workspace.id, key):
            raise disabled_error()

    module_enabled.module_key = key  # type: ignore[attr-defined]  # 一致性检查用
    return module_enabled
