"""新闻早报模块的启用 / 停用钩子。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.modules.news.service import NewsService


async def on_toggle(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime,
    workspace_id: uuid.UUID,
    enabled: bool,
) -> None:
    """打开了早报推送的工作空间启用 / 停用模块后，重新生成 TrendRadar 配置文件。

    停用时生成“不抓取、不推送”的配置（enabled_row() 把停用模块的工作空间当作没打开），
    TrendRadar 不会再往手机推早报；再启用时恢复原来的设置。
    """
    service = NewsService(db, settings, runtime)
    row = await service.row(workspace_id)
    if row is None or not row.enabled:
        return
    await service.render_current(touch=row if enabled else None)
    await db.commit()
