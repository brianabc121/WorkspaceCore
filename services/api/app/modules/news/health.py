"""运维页的 TrendRadar 检查（T-0152）：MCP 能否连通（只读的 get_system_status）。"""

from collections.abc import Awaitable, Callable

from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.modules.news.service import news_client


def probe(
    settings: Settings, runtime: ConnectorRuntime
) -> tuple[Callable[[], Awaitable[None]], float] | None:
    if not settings.trendradar_mcp_url:
        return None
    client = news_client(settings, runtime)

    async def check() -> None:
        await client.call("get_system_status", {})

    return check, settings.news_timeout_seconds + 1
