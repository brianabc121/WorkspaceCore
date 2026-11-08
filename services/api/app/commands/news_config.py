"""按当前设置生成 TrendRadar 的配置文件（T-0152，docs/v2/NEWS_PLAN.md 第 4、10 节）。

    docker compose exec -T api python -m app.commands.news_config

make trendradar-up 第一次启动 TrendRadar 前用（容器要求配置文件已经存在）：没有工作空间启用早报时
写一份“不抓取、不推送”的配置。之后由保存设置与 Worker 维护。退出码：0 写入成功；1 失败。
只输出结果，不输出任何密钥。
"""

import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.connectors import ConnectorRuntime
from app.integrations.http import CircuitBreaker
from app.modules.news.service import NewsService


async def run() -> str:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            service = NewsService(db, settings, ConnectorRuntime(settings, CircuitBreaker()))
            error = await service.render_current(force=True)
            await db.commit()
            return error
    finally:
        await engine.dispose()


def main() -> int:
    error = asyncio.run(run())
    if error:
        print(f"TrendRadar 配置文件没有写成：{error}", file=sys.stderr)
        return 1
    print(f"已写入 TrendRadar 配置文件：{get_settings().news_config_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
