"""Actual PostgreSQL constraints must survive the documented rollback path."""

import asyncio

import pytest
from alembic import command

from tests.helpers import alembic_config


@pytest.mark.integration
async def test_gateway_ports_upgrade_downgrade_upgrade(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.downgrade, config, "0066")
    await asyncio.to_thread(command.upgrade, config, "head")
