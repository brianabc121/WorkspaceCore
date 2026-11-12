"""0061 is additive and leaves existing unlimited deployment records intact."""

import asyncio

import pytest
from alembic import command

from tests.helpers import alembic_config, run_sql
from tests.test_agent_server_password_migration import _seed

pytestmark = pytest.mark.integration


def test_placement_migration_roundtrip_preserves_legacy_rows(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0060")
    ids = _seed(temp_database_url)
    command.upgrade(config, "0061")
    snapshot = asyncio.run(
        run_sql(
            temp_database_url,
            "SELECT resource_snapshot::text FROM workspace.agent_servers WHERE id=$1",
            ids["legacy"],
        )
    )
    assert snapshot == "{}"
    assert (
        asyncio.run(
            run_sql(
                temp_database_url,
                "SELECT count(*) FROM workspace.agent_instances "
                "WHERE cpus IS NULL AND mem_limit_mb IS NULL",
            )
        )
        > 0
    )
    command.downgrade(config, "0060")
    assert (
        asyncio.run(run_sql(temp_database_url, "SELECT count(*) FROM workspace.agent_deployments"))
        > 0
    )
    command.upgrade(config, "0061")
    assert (
        asyncio.run(
            run_sql(
                temp_database_url,
                "SELECT count(*) FROM workspace.agent_instances "
                "WHERE cpus IS NULL AND mem_limit_mb IS NULL",
            )
        )
        > 0
    )
