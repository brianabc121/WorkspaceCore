"""Actual PostgreSQL locks: host admission must precede environment mutations."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

from app.domain.runner import MAX_ATTEMPTS, Runner, Viewer
from app.domain.runner_hosts import RunnerHosts
from app.models import (
    AgentInstance,
    RunnerCommand,
    RunnerEnvironment,
    RunnerHost,
    RunnerSnapshot,
    RunnerTemplate,
)
from app.schemas.runner import EnvCreateInput, RunnerCommandResult
from tests.helpers import ClientFactory
from tests.test_runner_api import SETTINGS, add_host, agent, create_env
from tests.workspace_support import app_of, open_env

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "operation",
    ["restart", "result", "reuse_stopped", "reuse_archived", "restore_deleted", "expire", "idle"],
)
async def test_lifecycle_waits_for_host_before_locking_environment(
    operation: str, make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        token = await add_host(env)
        instance_id, instance_token = await agent(env)
        record = (await create_env(env, instance_token, token, reuse_key="lock-order"))["result"][
            "environment"
        ]
        env_id = uuid.UUID(record["id"])
        app = app_of(env.client)
        sessions = app.state.sessionmaker
        async with sessions() as setup:
            allocation = await setup.get(RunnerEnvironment, env_id)
            host_id = allocation.host_id
            if operation != "result":
                allocation.state = "archived" if operation == "reuse_archived" else "stopped"
                allocation.runtime_metadata = {"compute_stopped": True}
                command_id = None
                if operation == "restore_deleted":
                    from app.domain import runner_specs as specs

                    allocation.state = "deleted"
                    allocation.lifetime = "persistent"
                    allocation.deleted_at = specs.now()
                    setup.add(
                        RunnerSnapshot(
                            workspace_id=allocation.workspace_id,
                            environment_id=allocation.id,
                            status="ready",
                            key_ref="sealed",
                            store="local",
                        )
                    )
            else:
                runner = Runner(setup, app.state.settings)
                command = runner.enqueue(
                    await setup.get(RunnerHost, host_id), "stop_env", {}, env=allocation
                )
                await setup.flush()
                command_id = command.id
            if operation == "expire":
                from datetime import timedelta

                from app.domain import runner_specs as specs

                allocation.state = "creating"
                command = Runner(setup, app.state.settings).enqueue(
                    await setup.get(RunnerHost, host_id), "create_env", {}, env=allocation
                )
                command.deadline_at = specs.now() - timedelta(seconds=1)
                command.attempts = MAX_ATTEMPTS
                host = await setup.get(RunnerHost, host_id)
                host.last_seen_at = specs.now() - timedelta(seconds=100)
            elif operation == "idle":
                from datetime import timedelta

                from app.domain import runner_specs as specs

                allocation.state = "running"
                allocation.last_used_at = specs.now() - timedelta(hours=2)
            await setup.commit()

        pid_ready = asyncio.Event()
        pid = None

        async def lifecycle() -> None:
            nonlocal pid
            async with sessions() as db:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                pid_ready.set()
                allocation = await db.get(RunnerEnvironment, env_id)
                runner = Runner(db, app.state.settings)
                if operation == "restart":
                    await runner.start(allocation)
                elif operation.startswith("reuse_"):
                    instance = await db.get(AgentInstance, uuid.UUID(instance_id))
                    reused, found, _, run = await runner.create(
                        instance,
                        EnvCreateInput(
                            purpose="reuse",
                            lifetime="session",
                            reuse_key="lock-order",
                            spec={"image": "python:3.12-slim"},
                            command="true" if operation == "reuse_stopped" else None,
                        ),
                    )
                    assert found and reused.id == env_id
                    assert (run is not None) == (operation == "reuse_stopped")
                elif operation == "restore_deleted":
                    from app.domain.runner_admin import RunnerAdmin

                    await RunnerAdmin(db, app.state.settings)._restore(allocation)
                elif operation in ("expire", "idle"):
                    from app.domain import runner_reclaim

                    await runner_reclaim.tick(db, app.state.settings)
                else:
                    assert command_id is not None
                    await RunnerHosts(db, app.state.settings).result(
                        await db.get(RunnerHost, host_id),
                        command_id,
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

        async with sessions() as verify:
            allocation = await verify.get(RunnerEnvironment, env_id)
            assert allocation is not None
            if operation.startswith("reuse_"):
                assert allocation.use_count == record["use_count"] + 1
                assert allocation.state == "creating"
                commands = list(
                    await verify.scalars(
                        select(RunnerCommand.kind)
                        .where(
                            RunnerCommand.environment_id == env_id,
                            RunnerCommand.status == "pending",
                        )
                        .order_by(RunnerCommand.seq)
                    )
                )
                assert commands == (
                    ["start_env", "exec"] if operation == "reuse_stopped" else ["create_env"]
                )
            elif operation == "restore_deleted":
                assert allocation.state == "creating"
                assert allocation.deleted_at is None
            elif operation == "expire":
                assert allocation.state == "failed"
            elif operation == "idle":
                assert allocation.state == "running"
                assert "自动停止" in (allocation.state_reason or "")
                pending = await verify.scalar(
                    select(RunnerCommand.kind).where(
                        RunnerCommand.environment_id == env_id,
                        RunnerCommand.status == "pending",
                    )
                )
                assert pending == "stop_env"


async def test_template_expiry_waits_for_host_before_locking_template(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    from datetime import timedelta

    from app.domain import runner_reclaim
    from app.domain import runner_specs as specs

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        await add_host(env)
        app = app_of(env.client)
        sessions = app.state.sessionmaker
        async with sessions() as setup:
            runner = Runner(setup, app.state.settings)
            viewer = Viewer(uuid.UUID(env.workspace_id), user_id=env.owner_id)
            template, _ = await runner.save_template(
                viewer, name="expiry-lock", description="", spec={"image": "python:3.12-slim"}
            )
            await setup.commit()
            template, _ = await runner.save_template(
                viewer, name="expiry-lock", description="", spec={"image": "python:3.12-slim"}
            )
            await setup.flush()
            commands = list(
                await setup.scalars(
                    select(RunnerCommand)
                    .where(RunnerCommand.template_id == template.id)
                    .order_by(RunnerCommand.seq)
                )
            )
            old, current = commands
            await runner.apply_result(current, "done", {"image_digest": "sha256:current"}, None)
            old.deadline_at = specs.now() - timedelta(seconds=1)
            old.attempts = MAX_ATTEMPTS
            host = await setup.get(RunnerHost, old.host_id)
            host.last_seen_at = specs.now() - timedelta(seconds=100)
            template_id, host_id = template.id, host.id
            await setup.commit()

        pid_ready = asyncio.Event()
        pid = None

        async def expire() -> None:
            nonlocal pid
            async with sessions() as db:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                pid_ready.set()
                await runner_reclaim.tick(db, app.state.settings)

        async with sessions() as holder:
            await holder.get(RunnerHost, host_id, with_for_update=True)
            work = asyncio.create_task(expire())
            try:
                await asyncio.wait_for(pid_ready.wait(), 5)
                async with sessions() as observer:
                    until = asyncio.get_running_loop().time() + 5
                    while True:
                        waiting = await observer.scalar(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                            {"pid": pid},
                        )
                        await observer.rollback()
                        if waiting == "Lock":
                            break
                        assert asyncio.get_running_loop().time() < until, "expiry never waited"
                        await asyncio.sleep(0.01)
                    await observer.scalar(
                        select(RunnerTemplate)
                        .where(RunnerTemplate.id == template_id)
                        .with_for_update(nowait=True)
                    )
                    await observer.rollback()
            finally:
                await holder.rollback()
                await asyncio.wait_for(work, 10)

        async with sessions() as verify:
            template = await verify.get(RunnerTemplate, template_id)
            assert template is not None
            assert template.build_status == "ready"
            assert template.image_digest == "sha256:current"


@pytest.mark.parametrize("lock", ["admission", "quota"])
async def test_run_result_wakeup_can_reference_instance_during_admission(
    lock: str, make_client: ClientFactory, migrated_database_url: str
) -> None:
    from app.domain.runner_capabilities import RunnerQuotaService
    from app.models import AgentWakeup

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        token = await add_host(env)
        instance_id, instance_token = await agent(env)
        record = (await create_env(env, instance_token, token))["result"]["environment"]
        app = app_of(env.client)
        sessions = app.state.sessionmaker
        async with sessions() as setup:
            instance = await setup.get(AgentInstance, uuid.UUID(instance_id))
            allocation = await setup.get(RunnerEnvironment, uuid.UUID(record["id"]))
            run = await Runner(setup, app.state.settings).start_run(
                instance, allocation, ["true"], 30
            )
            run.wake = True
            command = await setup.scalar(
                select(RunnerCommand).where(RunnerCommand.run_id == run.id)
            )
            command_id, host_id = command.id, command.host_id
            await setup.commit()

        async def finish() -> None:
            async with sessions() as db:
                assert await RunnerHosts(db, app.state.settings).result(
                    await db.get(RunnerHost, host_id),
                    command_id,
                    RunnerCommandResult(status="done", result={"exit_code": 0}),
                )
                await db.commit()

        async with sessions() as holder:
            instance = await holder.get(AgentInstance, uuid.UUID(instance_id))
            if lock == "admission":
                await Runner(holder, app.state.settings)._lock_instance(instance)
            else:
                await RunnerQuotaService(holder, app.state.settings).check_count(instance)
            work = asyncio.create_task(finish())
            try:
                # Real wakeup insertion takes the instance FK's KEY SHARE lock.
                # It must remain compatible with the admission serialization lock.
                await asyncio.wait_for(asyncio.shield(work), 5)
            finally:
                await holder.rollback()
                await asyncio.wait_for(work, 10)

        async with sessions() as verify:
            wakeup = await verify.scalar(
                select(AgentWakeup).where(AgentWakeup.instance_id == uuid.UUID(instance_id))
            )
            assert wakeup is not None


@pytest.mark.parametrize("lock", ["report", "poll", "admission"])
async def test_lifecycle_command_fk_is_compatible_with_host_serialization(
    lock: str, make_client: ClientFactory, migrated_database_url: str
) -> None:
    from app.schemas.runner import RunnerPoll
    from tests.test_runner_api import POLL

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        token = await add_host(env)
        _instance_id, instance_token = await agent(env)
        record = (await create_env(env, instance_token, token))["result"]["environment"]
        app = app_of(env.client)
        sessions = app.state.sessionmaker
        env_id = uuid.UUID(record["id"])
        async with sessions() as setup:
            allocation = await setup.get(RunnerEnvironment, env_id)
            host_id = allocation.host_id

        async def mutate(operation: str) -> None:
            async with sessions() as db:
                runner = Runner(db, app.state.settings)
                allocation = await db.get(RunnerEnvironment, env_id)
                if operation == "stop":
                    await runner.stop(Viewer(allocation.workspace_id), allocation, "stop")
                elif operation == "delete":
                    await runner.delete(allocation, "delete")
                else:
                    assert await runner.archive(allocation, "archive")
                await db.commit()

        for operation in ("stop", "delete", "archive"):
            async with sessions() as holder:
                host = await holder.get(RunnerHost, host_id)
                service = RunnerHosts(holder, app.state.settings)
                if lock == "report":
                    await service._lock_report_host(host)
                elif lock == "poll":
                    await service.check_in(host, RunnerPoll(**POLL))
                else:
                    await Runner(holder, app.state.settings)._lock_pool_hosts(host.workspace_id)
                work = asyncio.create_task(mutate(operation))
                try:
                    await asyncio.wait_for(asyncio.shield(work), 5)
                finally:
                    await holder.rollback()
                    await asyncio.wait_for(work, 10)
        async with sessions() as verify:
            kinds = list(
                await verify.scalars(
                    select(RunnerCommand.kind)
                    .where(
                        RunnerCommand.environment_id == env_id, RunnerCommand.status == "pending"
                    )
                    .order_by(RunnerCommand.seq)
                )
            )
            assert kinds == ["stop_env", "delete_env", "archive_env"]
