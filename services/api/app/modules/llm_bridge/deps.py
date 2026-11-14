"""大模型客户端模块接口共用的路由器和依赖。

接口分在 api.py（管理）和 chat_api.py（聊天页面）两个文件里，都登记在这里的 router 上：
模块的路由要直接是 /llm_bridge 下的路径（tests/test_modules.py），不能再套一层 include_router。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    get_app_settings,
    get_connectors,
    get_db,
    workspace_admin,
    workspace_owner,
)
from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess
from app.modules.llm_bridge.service import LlmBridgeService

router = APIRouter(tags=["llm_bridge"], dependencies=[Security(authenticated)])


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    runtime: Annotated[ConnectorRuntime, Depends(get_connectors)],
) -> LlmBridgeService:
    return LlmBridgeService(db, settings, runtime)


Service = Annotated[LlmBridgeService, Depends(get_service)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
