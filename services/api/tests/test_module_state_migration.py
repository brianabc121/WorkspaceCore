"""迁移 0058（应用中心 T-0210）：启用记录加 state_version，新建同步记录表；不改已有数据的含义。"""

import asyncio
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration


async def _run(url: str, *statements: tuple[Any, ...]) -> list[Any]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return [await conn.fetchval(sql, *args) for sql, *args in statements]
    finally:
        await conn.close()


def test_upgrade_preserves_enablement_and_downgrade_is_clean(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0057")
    user, first, second = uuid4(), uuid4(), uuid4()
    asyncio.run(
        _run(
            temp_database_url,
            (
                "INSERT INTO workspace.users(id,email,password_hash,display_name) "
                "VALUES($1,'mod@example.test','synthetic','Mod')",
                user,
            ),
            ("INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'A',$2)", first, user),
            ("INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'B',$2)", second, user),
            (
                "INSERT INTO workspace.workspace_modules(workspace_id,module_key,enabled) "
                "VALUES($1,'news',false)",
                first,
            ),
            (
                "INSERT INTO workspace.audit_logs(workspace_id,action,resource_type,resource_id) "
                "VALUES($1,'module.disabled','module','news')",
                first,
            ),
        )
    )

    command.upgrade(config, "0058")
    enabled, version, rows, audits, attempts = asyncio.run(
        _run(
            temp_database_url,
            ("SELECT enabled FROM workspace.workspace_modules WHERE workspace_id=$1", first),
            ("SELECT state_version FROM workspace.workspace_modules WHERE workspace_id=$1", first),
            ("SELECT count(*) FROM workspace.workspace_modules",),
            ("SELECT count(*) FROM workspace.audit_logs WHERE action LIKE 'module.%'",),
            ("SELECT count(*) FROM workspace.module_sync_attempts",),
        )
    )
    # 已有的启用记录原样保留、版本为 0；没有记录的工作空间仍按清单默认值（不补行）；不回填历史
    assert (enabled, version, rows, audits, attempts) == (False, 0, 1, 1, 0)

    insert = (
        "INSERT INTO workspace.module_sync_attempts"
        "(workspace_id,module_key,state_version,target_enabled,trigger,state,reason) "
        "VALUES($1,$2,$3,true,$4,$5,$6)"
    )
    asyncio.run(_run(temp_database_url, (insert, first, "news", 0, "retry", "failed", "busy")))
    for bad in (
        (first, "news", 0, "retry", "failed", "Traceback: secret"),  # 原因只能是固定类别
        (first, "news", 0, "retry", "done", None),
        (first, "news", 0, "cron", "failed", None),
        (first, "news", -1, "retry", "failed", None),
        (first, "Bad-Key", 0, "retry", "failed", None),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            asyncio.run(_run(temp_database_url, (insert, *bad)))
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(
            _run(temp_database_url, ("UPDATE workspace.workspace_modules SET state_version=-1",))
        )
    # 工作空间删除时同步记录一起删除
    remaining = asyncio.run(
        _run(
            temp_database_url,
            ("DELETE FROM workspace.workspaces WHERE id=$1", first),
            ("SELECT count(*) FROM workspace.module_sync_attempts",),
        )
    )
    assert remaining[1] == 0

    command.downgrade(config, "0057")
    table, column = asyncio.run(
        _run(
            temp_database_url,
            ("SELECT to_regclass('workspace.module_sync_attempts')::text",),
            (
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='workspace' "
                "AND table_name='workspace_modules' AND column_name='state_version'",
            ),
        )
    )
    assert (table, column) == (None, 0)
    command.upgrade(config, "head")
