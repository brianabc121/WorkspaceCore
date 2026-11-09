"""迁移 0055（容器读数与上报时间，T-0185）：只加可为空的列，旧记录不回填；downgrade 只删新列。"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration

NEW = (
    "cpu_used_cores",
    "cpu_window_seconds",
    "cpu_reported_at",
    "mem_reported_at",
    "disk_reported_at",
)


async def _exec(url: str, sql: str, *args: Any) -> Any:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def run(url: str, sql: str, *args: Any) -> Any:
    return asyncio.run(_exec(url, sql, *args))


def _columns(url: str) -> set[str]:
    rows = run(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'workspace' AND table_name = 'runner_environments'",
    )
    return {r["column_name"] for r in rows}


def _seed(url: str) -> UUID:
    user, workspace, host, env = uuid4(), uuid4(), uuid4(), uuid4()
    run(
        url,
        "INSERT INTO workspace.users(id,email,password_hash,display_name) "
        "VALUES($1,'m@example.test','synthetic','M')",
        user,
    )
    run(
        url, "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'W',$2)", workspace, user
    )
    run(
        url,
        "INSERT INTO workspace.runner_hosts(id,workspace_id,name,token_hash,token_prefix) "
        "VALUES($1,$2,'h','x','mwr_x')",
        host,
        workspace,
    )
    run(
        url,
        "INSERT INTO workspace.runner_environments(id,workspace_id,host_id,purpose,lifetime,state,"
        "spec,container_name,volume_name,network_name,mem_bytes,disk_bytes) "
        "VALUES($1,$2,$3,'旧环境','session','running','{}'::jsonb,'c','v','n',100,200)",
        env,
        workspace,
        host,
    )
    return env


def test_upgrade_adds_nullable_columns_and_downgrade_keeps_old_readings(
    temp_database_url: str,
) -> None:
    url = temp_database_url
    config = alembic_config(url)
    command.upgrade(config, "0054")
    env = _seed(url)
    command.upgrade(config, "0055")
    assert set(NEW) <= _columns(url)
    row = run(url, "SELECT * FROM workspace.runner_environments WHERE id = $1", env)[0]
    assert all(row[name] is None for name in NEW)  # 旧记录不回填“刚刚”
    assert (row["mem_bytes"], row["disk_bytes"]) == (100, 200)
    # 约束：CPU 两项成对、非负、窗口 1–180 秒
    for values in (
        "cpu_used_cores = 1",
        "cpu_used_cores = -1, cpu_window_seconds = 60",
        "cpu_used_cores = 1, cpu_window_seconds = 0.5",
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            run(url, f"UPDATE workspace.runner_environments SET {values} WHERE id = $1", env)  # noqa: S608
    run(
        url,
        "UPDATE workspace.runner_environments SET cpu_used_cores = 0, cpu_window_seconds = 60, "
        "cpu_reported_at = now() WHERE id = $1",
        env,
    )
    command.downgrade(config, "0054")
    assert not set(NEW) & _columns(url)
    row = run(
        url, "SELECT mem_bytes, disk_bytes FROM workspace.runner_environments WHERE id = $1", env
    )[0]
    assert (row["mem_bytes"], row["disk_bytes"]) == (100, 200)
    command.upgrade(config, "head")
    assert set(NEW) <= _columns(url)
