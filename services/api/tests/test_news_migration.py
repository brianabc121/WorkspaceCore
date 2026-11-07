"""迁移 0048（新闻早报，T-0153）：建三张表与约束；唤醒来源可以是 news；downgrade 删干净。"""

import asyncio
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration
TABLES = ("news_settings", "news_briefings", "news_feedback")


async def _run(url: str, *statements: tuple[Any, ...]) -> list[Any]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        out = []
        for sql, *args in statements:
            out.append(await conn.fetchval(sql, *args))
        return out
    finally:
        await conn.close()


def _tables(url: str) -> set[str]:
    rows = asyncio.run(
        _run(
            url,
            (
                "SELECT string_agg(table_name, ',') FROM information_schema.tables "
                "WHERE table_schema = 'workspace' AND table_name LIKE 'news_%'",
            ),
        )
    )
    return set((rows[0] or "").split(",")) - {""}


def test_news_tables_constraints_and_downgrade(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0048")
    assert _tables(temp_database_url) == set(TABLES)
    user, first, second, instance = uuid4(), uuid4(), uuid4(), uuid4()
    asyncio.run(
        _run(
            temp_database_url,
            (
                "INSERT INTO workspace.users(id,email,password_hash,display_name) "
                "VALUES($1,'news@example.test','synthetic','News')",
                user,
            ),
            ("INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'A',$2)", first, user),
            ("INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'B',$2)", second, user),
            ("INSERT INTO workspace.news_settings(workspace_id,enabled) VALUES($1,true)", first),
            (
                "INSERT INTO workspace.agent_instances"
                "(id,workspace_id,name,network_mode,im_user_id,im_nickname) "
                "VALUES($1,$2,'甲','intranet','agent_news','甲')",
                instance,
                first,
            ),
            (
                "INSERT INTO workspace.agent_wakeups"
                "(workspace_id,instance_id,source,session_key,prompt,run_at) "
                "VALUES($1,$2,'news','k','写早报',now())",
                first,
                instance,
            ),
        )
    )
    # 同一时刻只能有一个工作空间启用
    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(
            _run(
                temp_database_url,
                (
                    "INSERT INTO workspace.news_settings(workspace_id,enabled) VALUES($1,true)",
                    second,
                ),
            )
        )
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(
            _run(
                temp_database_url,
                (
                    "INSERT INTO workspace.news_briefings(workspace_id,local_date,source) "
                    "VALUES($1,current_date,'other')",
                    first,
                ),
            )
        )
    command.downgrade(config, "0047")
    assert _tables(temp_database_url) == set()
    left = asyncio.run(_run(temp_database_url, ("SELECT count(*) FROM workspace.agent_wakeups",)))
    assert left == [0]
    command.upgrade(config, "head")
    assert _tables(temp_database_url) == set(TABLES)
