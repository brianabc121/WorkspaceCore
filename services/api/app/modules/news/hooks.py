"""新闻早报模块的启用 / 停用钩子（应用中心 T-0210：返回结构化结果）。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.connectors import ConnectorRuntime
from app.modules.base import HookOutcome
from app.modules.news.service import NEWS_CONFIG_BUSY, NewsService


async def on_toggle(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime,
    workspace_id: uuid.UUID,
    enabled: bool,
) -> HookOutcome:
    """打开了早报推送的工作空间启用 / 停用模块后，重新生成 TrendRadar 配置文件。

    停用时生成“不抓取、不推送”的配置（enabled_row() 把停用模块的工作空间当作没打开），
    TrendRadar 不会再往手机推早报；再启用时恢复原来的设置。按数据库里的最新状态生成，
    不看调用参数 enabled（它只是这次操作的目标），可以重复执行。

    - 没部署 TrendRadar：not_required(not_deployed)，不写文件；
    - 这个工作空间没打开早报推送：not_required(application_not_active)，不写文件；
    - 配置文件写失败或凭证解密失败：failed(config_render_failed)，错误说明留在设置行里；
    - 等不到配置文件写锁：failed(busy)。
    """
    service = NewsService(db, settings, runtime)
    if not service.deployed:
        return HookOutcome.not_required("not_deployed")
    try:
        async with service.render_lock():
            row = await service.row(workspace_id)
            if row is None or not row.enabled:
                return HookOutcome.not_required("application_not_active")
            # 停用时也记在这个工作空间的设置行上：写失败的原因留在 render_error 里
            error = await service.render_current(touch=row)
            await db.commit()
    except ApiError as busy:
        if busy.code != NEWS_CONFIG_BUSY:
            raise
        return HookOutcome.failed("busy")
    if error:
        return HookOutcome.failed("config_render_failed")
    return HookOutcome.applied()
