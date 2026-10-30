"""按当前数据重建跨资源搜索索引（S4-03）。

平时不需要：索引由数据库触发器随写入维护。只在怀疑索引与数据不一致、或修改了索引规则之后运行：

    docker compose exec -T api python -m app.commands.reindex_search
"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.search import rebuild


async def main() -> int:
    engine = create_engine(get_settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await rebuild(db)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(f"搜索索引已重建：{asyncio.run(main())} 条")
