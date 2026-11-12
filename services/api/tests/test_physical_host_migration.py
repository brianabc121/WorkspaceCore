"""The identity ledger survives safe application rollback; no silent protection drop."""

import asyncio

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn
from tests.test_runner_metrics_migration import _seed, run

pytestmark = pytest.mark.integration


def test_physical_registration_migration_keeps_resources_and_refuses_active_downgrade(
    temp_database_url,
):
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0064")
    env_id = _seed(temp_database_url)
    before = run(
        temp_database_url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env_id
    )[0]
    command.upgrade(config, "0065")
    run(
        temp_database_url,
        "INSERT INTO workspace.physical_host_registrations"
        "(physical_id,workspace_id,runner_host_id) VALUES($1,$2,$3)",
        "f" * 64,
        before["workspace_id"],
        before["host_id"],
    )
    with pytest.raises(RuntimeError, match="物理服务器身份"):
        command.downgrade(config, "0064")
    assert (
        run(temp_database_url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env_id)[0]
        == before
    )
    run(temp_database_url, "DELETE FROM workspace.physical_host_registrations")
    command.downgrade(config, "0064")
    assert (
        run(temp_database_url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env_id)[0]
        == before
    )
    command.upgrade(config, "0065")


async def test_downgrade_waits_for_inflight_claim_before_checking_if_ledger_is_empty(
    temp_database_url,
):
    config = alembic_config(temp_database_url)
    await asyncio.to_thread(command.upgrade, config, "0064")
    env_id = await asyncio.to_thread(_seed, temp_database_url)
    await asyncio.to_thread(command.upgrade, config, "0065")
    connection = await asyncpg.connect(asyncpg_dsn(temp_database_url))
    row = await connection.fetchrow(
        "SELECT * FROM workspace.runner_environments WHERE id=$1", env_id
    )
    observer = await asyncpg.connect(asyncpg_dsn(temp_database_url))
    transaction = connection.transaction()
    await transaction.start()
    work = None
    committed = False
    try:
        await connection.execute(
            "INSERT INTO workspace.physical_host_registrations"
            "(physical_id,workspace_id,runner_host_id) VALUES($1,$2,$3)",
            "e" * 64,
            row["workspace_id"],
            row["host_id"],
        )
        work = asyncio.create_task(asyncio.to_thread(command.downgrade, config, "0064"))
        until = asyncio.get_running_loop().time() + 10
        while not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
            "AND wait_event_type='Lock' AND query LIKE '%physical_host_registrations%')"
        ):
            assert asyncio.get_running_loop().time() < until, "downgrade did not wait on the claim"
            await asyncio.sleep(0.01)
        await transaction.commit()
        committed = True
        with pytest.raises(RuntimeError, match="物理服务器身份"):
            await asyncio.wait_for(work, 10)
        assert (
            await observer.fetchval("SELECT count(*) FROM workspace.physical_host_registrations")
            == 1
        )
    finally:
        if not committed:
            await transaction.rollback()
        if work is not None and not work.done():
            await asyncio.wait_for(work, 10)
        await connection.close()
        await observer.close()
