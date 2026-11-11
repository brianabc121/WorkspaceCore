"""Actual PostgreSQL locks: host admission must precede environment mutations."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

from app.domain.runner import Runner
from app.domain.runner_hosts import RunnerHosts
from app.models import RunnerEnvironment, RunnerHost
from app.schemas.runner import RunnerCommandResult
from tests.test_runner_api import SETTINGS, add_host, agent, create_env
from tests.workspace_support import app_of, open_env

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["restart", "result"])
async def test_lifecycle_waits_for_host_before_locking_environment(
    operation, make_client, migrated_database_url
):
    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        token = await add_host(env)
        _instance, instance_token = await agent(env)
        record = (await create_env(env, instance_token, token))["result"]["environment"]
        env_id = uuid.UUID(record["id"])
        app = app_of(env.client)
        sessions = app.state.sessionmaker
        async with sessions() as setup:
            allocation = await setup.get(RunnerEnvironment, env_id)
            host_id = allocation.host_id
            if operation == "restart":
                allocation.state = "stopped"
                allocation.runtime_metadata = {"compute_stopped": True}
                command_id = None
            else:
                runner = Runner(setup, app.state.settings)
                command = runner.enqueue(
                    await setup.get(RunnerHost, host_id), "stop_env", {}, env=allocation
                )
                await setup.flush()
                command_id = command.id
            await setup.commit()

        pid_ready = asyncio.Event()
        pid = None

        async def lifecycle():
            nonlocal pid
            async with sessions() as db:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                pid_ready.set()
                allocation = await db.get(RunnerEnvironment, env_id)
                if operation == "restart":
                    await Runner(db, app.state.settings).start(allocation)
                else:
                    await RunnerHosts(db, app.state.settings).result(
                        await db.get(RunnerHost, host_id), command_id,
                        RunnerCommandResult(status="done"),
                    )
                await db.commit()

        async with sessions() as holder:
            await holder.get(RunnerHost, host_id, with_for_update=True)
            work = asyncio.create_task(lifecycle())
            try:
                await asyncio.wait_for(pid_ready.wait(), 5)
                async with sessions() as observer:
                    until = asyncio.get_running_loop().time() + 5
                    while True:
                        waiting = await observer.scalar(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                            {"pid": pid},
                        )
                        await observer.rollback()  # pg_stat_activity snapshot must refresh
                        if waiting == "Lock":
                            break
                        assert asyncio.get_running_loop().time() < until, "lifecycle never waited"
                        await asyncio.sleep(0.01)
                    # If lifecycle already holds this row, reporter host→env and
                    # lifecycle env→host can deadlock. NOWAIT exposes that order.
                    await observer.scalar(
                        select(RunnerEnvironment)
                        .where(RunnerEnvironment.id == env_id)
                        .with_for_update(nowait=True)
                    )
                    await observer.rollback()
            finally:
                await holder.rollback()
                await asyncio.wait_for(work, 10)
