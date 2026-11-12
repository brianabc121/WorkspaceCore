"""多服务器选择、实际主机路由与持久预约。"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain import runner_specs as specs
from app.domain.runner import MAX_ATTEMPTS, Runner, RunnerError
from app.models import RunnerEnvironment, RunnerHost
from app.schemas.runner import EnvCreateInput
from tests.helpers import ClientFactory


def host(**changes: object) -> RunnerHost:
    values = dict(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="node",
        status="online",
        last_seen_at=specs.now(),
        cpus=8,
        mem_bytes=32 * 1024**3,
        disk_bytes=500 * 1024**3,
        disk_free_bytes=400 * 1024**3,
        runtimes=["runc", "runsc"],
        kvm=False,
        report={
            "at": specs.now().isoformat(),
            "capacity_sampled_at": specs.now().isoformat(),
            "physical_identity_verified": True,
            "load": 1.0,
            "mem_free_bytes": 24 * 1024**3,
            "disk_free_bytes": 400 * 1024**3,
        },
    )
    return RunnerHost(**(values | changes))


def test_create_accepts_explicit_placement_and_legacy_host_id() -> None:
    target = str(uuid.uuid4())
    body = EnvCreateInput(
        purpose="test", lifetime="session", placement={"mode": "specified", "server_id": target}
    )
    assert str(body.placement.server_id) == target
    legacy = EnvCreateInput(purpose="test", lifetime="session", server_id=target)
    assert legacy.placement.mode == "specified"
    assert str(legacy.placement.server_id) == target
    with pytest.raises(ValidationError):
        EnvCreateInput(
            purpose="test",
            lifetime="session",
            server_id=target,
            placement={"mode": "specified", "server_id": str(uuid.uuid4())},
        )


async def test_legacy_host_refuses_ambiguous_pool() -> None:
    db = AsyncMock()
    db.scalars.return_value = [host(), host()]
    with pytest.raises(RunnerError, match="服务器") as error:
        await Runner(db, Settings()).host(uuid.uuid4())
    assert error.value.code == "RUNNER_HOST_AMBIGUOUS"


async def test_online_host_checks_requested_host_identity() -> None:
    node = host()
    db = AsyncMock()
    db.scalar.return_value = node
    result = await Runner(db, Settings()).online_host(node.workspace_id, node.id)
    assert result.id == node.id
    query = str(db.scalar.call_args.args[0])
    assert "runner_hosts.id =" in query
    assert "runner_hosts.workspace_id =" in query


def test_capacity_samples_are_single_use_bounded_and_bound_to_the_host() -> None:
    from datetime import timedelta

    from app.core.errors import ApiError
    from app.domain.runner_hosts import accept_report_sample
    from app.schemas.runner import RunnerReport

    now = specs.now()
    ticket = uuid.uuid4()
    node = host(
        runtime_metadata={
            "report_sample_id": str(ticket),
            "report_sample_started_at": now.isoformat(),
        }
    )
    assert accept_report_sample(node, RunnerReport(sample_id=ticket), now) == now
    with pytest.raises(ApiError) as error:
        accept_report_sample(node, RunnerReport(sample_id=ticket), now)
    assert error.value.code == "REPORT_SAMPLE_STALE"
    node.runtime_metadata = {
        "report_sample_id": str(ticket),
        "report_sample_started_at": (now - timedelta(seconds=91)).isoformat(),
    }
    with pytest.raises(ApiError):
        accept_report_sample(node, RunnerReport(sample_id=ticket), now)
    with pytest.raises(ApiError):
        accept_report_sample(host(), RunnerReport(sample_id=ticket), now)
    assert accept_report_sample(node, RunnerReport(), now) is None


def test_candidate_requires_report_not_recent_poll() -> None:
    from app.domain.runner_placement import candidate_for

    candidate = candidate_for(host(report={}), [])
    assert candidate.observed_at is None
    assert candidate.cpu_load is None


def test_stopped_releases_compute_but_retains_disk_and_unknown_reserves_compute() -> None:
    from app.domain.runner_placement import candidate_for

    allocations = [
        RunnerEnvironment(state=state, spec={"cpus": 2, "memory_mb": 1000, "disk_gb": 10})
        for state in ("running", "stopped", "failed", "pending_delete", "archived", "deleted")
    ]
    candidate = candidate_for(host(), allocations)
    assert candidate.committed_cpus == 6
    assert candidate.committed_memory_mb == 3000
    assert candidate.committed_disk_gb == 40


def test_candidate_credits_only_measured_nonpending_allocation_memory() -> None:
    from app.domain.runner_placement import candidate_for

    env = RunnerEnvironment(
        state="running",
        spec={"cpus": 1, "memory_mb": 2048, "disk_gb": 1},
        runtime_metadata={"compute_memory_working_set_bytes": 128 << 20},
    )
    assert candidate_for(host(), [env]).observed_committed_memory_mb == 128
    env.runtime_metadata = {}
    assert candidate_for(host(), [env]).observed_committed_memory_mb is None
    env.state = "creating"
    assert candidate_for(host(), [env]).observed_committed_memory_mb == 0


async def test_restart_reserves_before_dispatch_and_routes_actual_host() -> None:
    node = host()
    env = RunnerEnvironment(
        id=uuid.uuid4(),
        workspace_id=node.workspace_id,
        host_id=node.id,
        state="stopped",
        spec=specs.normalize(
            {"image": "python:3.12-slim", "cpus": 1, "memory_mb": 128, "disk_gb": 1}
        ),
        container_name="container",
        network_name="network",
        volume_name="volume",
    )
    runner = Runner(AsyncMock(), Settings())
    with (
        patch.object(runner, "admit_restart", new=AsyncMock(return_value=node)) as admit_restart,
        patch.object(runner, "enqueue", new=MagicMock()) as enqueue,
    ):
        await runner.start(env)
        admit_restart.assert_awaited_once_with(env)
        assert env.state == "creating"
        assert enqueue.call_args.args[0].id == env.host_id


@pytest.mark.integration
async def test_register_multiple_hosts_routes_and_legacy_ambiguity(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    from tests.test_runner_api import SETTINGS
    from tests.workspace_support import open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        nodes = []
        for name in ("first", "second"):
            response = await env.client.post(
                "/api/v1/runner/hosts", json={"name": name}, headers=env.headers(write=True)
            )
            assert response.status_code == 201, response.text
            nodes.append(response.json())
        response = await env.client.get("/api/v1/runner/hosts", headers=env.headers())
        assert {item["id"] for item in response.json()["items"]} == {node["id"] for node in nodes}
        response = await env.client.get("/api/v1/runner/overview", headers=env.headers())
        assert response.json()["host"] is None
        assert len(response.json()["hosts"]) == 2
        response = await env.client.post(
            "/api/v1/runner/host/bundle", headers=env.headers(write=True)
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RUNNER_HOST_AMBIGUOUS"
        response = await env.client.post(
            f"/api/v1/runner/hosts/{nodes[0]['id']}/bundle", headers=env.headers(write=True)
        )
        assert response.status_code == 200, response.text
        response = await env.client.get(
            f"/api/v1/runner/hosts/{uuid.uuid4()}", headers=env.headers()
        )
        assert response.status_code == 404


async def test_shared_environment_requires_copy_before_mutation() -> None:
    viewer_instance = MagicMock(id=uuid.uuid4(), workspace_id=uuid.uuid4())
    environment = RunnerEnvironment(
        id=uuid.uuid4(),
        workspace_id=viewer_instance.workspace_id,
        owner_instance_id=uuid.uuid4(),
        share="project",
        project_id=uuid.uuid4(),
    )
    db = AsyncMock()
    db.get.return_value = environment
    runner = Runner(db, Settings())
    from app.domain.runner import Viewer

    with patch.object(runner, "_project_ids", new=AsyncMock(return_value={environment.project_id})):
        with pytest.raises(RunnerError) as error:
            await runner.env(
                Viewer(viewer_instance.workspace_id, instance=viewer_instance), environment.id
            )
        assert error.value.code == "ENV_COPY_REQUIRED"
        assert (
            await runner.env(
                Viewer(viewer_instance.workspace_id, instance=viewer_instance),
                environment.id,
                allow_shared=True,
            )
            is environment
        )


def test_keep_without_stop_receipt_keeps_compute_reserved() -> None:
    from app.domain.runner_placement import candidate_for

    env = RunnerEnvironment(
        state="stopped",
        spec={"cpus": 2, "memory_mb": 2048, "disk_gb": 1},
        runtime_metadata={"compute_stopped": False},
    )
    assert candidate_for(host(), [env]).committed_memory_mb == 2048


def test_restart_reservation_outlives_original_created_at_and_stale_report() -> None:
    from datetime import timedelta

    from app.domain.runner_placement import candidate_for

    reserved = specs.now()
    env = RunnerEnvironment(
        state="running",
        created_at=reserved - timedelta(days=2),
        spec={"cpus": 2, "memory_mb": 2048, "disk_gb": 1},
        runtime_metadata={"compute_stopped": False, "compute_reserved_at": reserved.isoformat()},
    )
    assert candidate_for(host(), [env]).pending_memory_mb == 2048


@pytest.mark.integration
async def test_delayed_report_does_not_release_restart_and_failed_host_cannot_be_removed(
    make_client: ClientFactory,
    migrated_database_url: str,
) -> None:
    from sqlalchemy import select

    from app.core.errors import ApiError
    from app.domain.runner_hosts import RunnerHosts
    from app.models import AgentInstance, RunnerCommand
    from app.schemas.runner import RunnerReport
    from tests.test_runner_api import SETTINGS, add_host, agent
    from tests.workspace_support import app_of, open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        await add_host(env)
        instance_id, _ = await agent(env)
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            instance = await db.get(AgentInstance, uuid.UUID(instance_id))
            runner = Runner(db, app.state.settings)
            allocation, _, _, _ = await runner.create(
                instance,
                EnvCreateInput(
                    purpose="admission",
                    lifetime="session",
                    reuse_key="one",
                    spec={"image": "python:3.12-slim", "cpus": 1, "memory_mb": 2048, "disk_gb": 1},
                ),
            )
            command = await db.scalar(
                select(RunnerCommand).where(RunnerCommand.environment_id == allocation.id)
            )
            await runner.apply_result(command, "done", {}, None)
            stop = runner.enqueue(
                await db.get(RunnerHost, allocation.host_id), "stop_env", {}, env=allocation
            )
            await db.flush()
            await runner.apply_result(stop, "done", {}, None)
            await db.commit()
            # 6GiB free minus physical headroom: two 2GiB starts cannot share it.
            node = await db.get(RunnerHost, allocation.host_id)
            node.report = {
                **node.report,
                "mem_free_bytes": 6 * 1024**3,
                "at": specs.now().isoformat(),
            }
            await db.commit()
            service = RunnerHosts(db, app.state.settings)
            old_sample = await service.begin_report(node)
            await db.commit()
            await runner.start(allocation)
            await db.commit()
            restart = await db.scalar(
                select(RunnerCommand).where(
                    RunnerCommand.environment_id == allocation.id,
                    RunnerCommand.kind == "start_env",
                )
            )
            await runner.apply_result(restart, "done", {}, None)
            await db.commit()
            await service.report(
                node,
                RunnerReport(
                    sample_id=old_sample.sample_id,
                    physical_host_id=node.report.get("physical_host_id"),
                    containers=[
                        {
                            "env_id": allocation.id,
                            "name": allocation.container_name,
                            "state": "exited",
                        }
                    ],
                    mem_bytes=node.mem_bytes,
                    mem_free_bytes=6 * 1024**3,
                    disk_bytes=node.disk_bytes,
                    disk_free_bytes=node.disk_free_bytes,
                    load=0.1,
                ),
            )
            await db.commit()
            assert allocation.state == "running"
            assert allocation.runtime_metadata["compute_stopped"] is False
            from app.domain.runner_placement import candidate_for

            candidate = candidate_for(node, [allocation])
            assert candidate.committed_memory_mb == 2048
            assert candidate.pending_memory_mb == 2048
            with pytest.raises(RunnerError) as error:
                await runner.create(
                    instance,
                    EnvCreateInput(
                        purpose="second",
                        lifetime="session",
                        spec={
                            "image": "python:3.12-slim",
                            "cpus": 1,
                            "memory_mb": 2048,
                            "disk_gb": 1,
                        },
                    ),
                )
            assert error.value.code == "NO_ELIGIBLE_SERVER"
            await db.rollback()
            await db.refresh(allocation)
            await db.refresh(node)
            allocation.state = "failed"
            await db.commit()
            with pytest.raises(ApiError) as removal_error:
                await service.remove(node, None, node.version)
            assert removal_error.value.code == "RUNNER_HAS_ENVIRONMENTS"
            assert await db.get(RunnerEnvironment, allocation.id) is not None


@pytest.mark.integration
async def test_concurrent_instances_cannot_share_unobserved_free_memory(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    import asyncio

    from app.models import AgentInstance
    from tests.test_runner_api import SETTINGS, add_host, agent
    from tests.workspace_support import app_of, open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        await add_host(env)
        one, _ = await agent(env)
        two, _ = await agent(env)
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            node = (await Runner(db, app.state.settings).hosts(uuid.UUID(env.workspace_id)))[0]
            node.report = {**node.report, "mem_free_bytes": 6 * 1024**3}
            await db.commit()

        async def allocate(instance_id: str) -> uuid.UUID | str:
            async with app.state.sessionmaker() as db:
                instance = await db.get(AgentInstance, uuid.UUID(instance_id))
                try:
                    allocation, _, _, _ = await Runner(db, app.state.settings).create(
                        instance,
                        EnvCreateInput(
                            purpose="concurrent",
                            lifetime="session",
                            spec={
                                "image": "python:3.12-slim",
                                "cpus": 1,
                                "memory_mb": 2048,
                                "disk_gb": 1,
                            },
                        ),
                    )
                    return allocation.id
                except RunnerError as error:
                    await db.rollback()
                    return error.code

        results = await asyncio.gather(allocate(one), allocate(two))
        assert sum(isinstance(value, uuid.UUID) for value in results) == 1
        assert results.count("NO_ELIGIBLE_SERVER") == 1


@pytest.mark.integration
async def test_auto_specified_reuse_conflict_and_operations_use_actual_host(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    from sqlalchemy import select

    from app.domain.runner_hosts import RunnerHosts
    from app.models import AgentInstance, RunnerCommand
    from app.schemas.placement import Placement
    from app.schemas.runner import EnvExecInput, RunnerHostCreate
    from tests.test_runner_api import SETTINGS, add_host, agent
    from tests.workspace_support import app_of, open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        await add_host(env)
        instance_id, _ = await agent(env)
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            runner = Runner(db, app.state.settings)
            instance = await db.get(AgentInstance, uuid.UUID(instance_id))
            first = (await runner.hosts(instance.workspace_id))[0]
            second, _ = await RunnerHosts(db, app.state.settings).create(
                instance.workspace_id, RunnerHostCreate(name="lower-load"), None
            )
            second.status, second.last_seen_at = "online", specs.now()
            second.cpus, second.mem_bytes, second.disk_bytes = (
                first.cpus,
                first.mem_bytes,
                first.disk_bytes,
            )
            second.disk_free_bytes, second.runtimes = first.disk_free_bytes, first.runtimes
            second.report = {**first.report, "load": 0.0}
            await db.commit()
            body = EnvCreateInput(
                purpose="auto",
                lifetime="session",
                reuse_key="identity",
                spec={"image": "python:3.12-slim", "cpus": 1, "memory_mb": 128, "disk_gb": 1},
            )
            allocation, _, _, _ = await runner.create(instance, body)
            assert allocation.host_id == second.id
            create = await db.scalar(
                select(RunnerCommand).where(RunnerCommand.environment_id == allocation.id)
            )
            assert create.host_id == second.id
            await runner.apply_result(create, "done", {}, None)
            await db.commit()
            with pytest.raises(RunnerError) as error:
                await runner.create(
                    instance,
                    body.model_copy(
                        update={"placement": Placement(mode="specified", server_id=first.id)}
                    ),
                )
            assert error.value.code == "PLACEMENT_CONFLICT"
            await db.rollback()
            await db.refresh(instance)
            await db.refresh(allocation)
            run = await runner.exec(
                instance, EnvExecInput(environment_id=allocation.id, command="true")
            )
            execute = await db.scalar(select(RunnerCommand).where(RunnerCommand.run_id == run.id))
            assert execute.host_id == allocation.host_id
            assert execute.payload["runtime_identity"]["allocation_id"] == str(allocation.id)


@pytest.mark.integration
@pytest.mark.parametrize("retry", ["resave_same", "resave_other", "rebuild"])
@pytest.mark.parametrize("old_result", ["done", "failed", "expired"])
async def test_only_current_template_build_can_change_template(
    retry: str,
    old_result: str,
    make_client: ClientFactory,
    migrated_database_url: str,
) -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from app.domain import runner_reclaim
    from app.domain.runner_hosts import RunnerHosts
    from app.models import RunnerCommand, RunnerTemplate
    from app.schemas.runner import RunnerCommandResult, RunnerHostCreate
    from tests.test_runner_api import SETTINGS, add_host
    from tests.workspace_support import app_of, open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        await add_host(env)
        body = {"name": "attempt-fence", "spec": {"image": "python:3.12-slim"}}
        response = await env.client.put(
            "/api/v1/runner/templates", json=body, headers=env.headers(write=True)
        )
        assert response.status_code == 200, response.text
        saved = response.json()
        template_id = uuid.UUID(saved["id"])
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            old = await db.scalar(
                select(RunnerCommand).where(RunnerCommand.template_id == template_id)
            )
            assert old is not None
            old_id, old_host_id = old.id, old.host_id
            old.status = "unknown" if old_result == "failed" else "sent"
            if retry == "resave_other":
                first = await db.get(RunnerHost, old_host_id)
                second, _ = await RunnerHosts(db, app.state.settings).create(
                    first.workspace_id, RunnerHostCreate(name="new-build-host"), None
                )
                second.status, second.last_seen_at = "online", specs.now()
                second.cpus, second.mem_bytes, second.disk_bytes = (
                    first.cpus,
                    first.mem_bytes,
                    first.disk_bytes,
                )
                second.disk_free_bytes, second.runtimes = first.disk_free_bytes, first.runtimes
                second.report = {**first.report, "load": 0.0}
            await db.commit()

        if retry == "rebuild":
            response = await env.client.post(
                f"/api/v1/runner/templates/{template_id}/rebuild", headers=env.headers(write=True)
            )
        else:
            response = await env.client.put(
                "/api/v1/runner/templates",
                json={**body, "version": saved["version"]},
                headers=env.headers(write=True),
            )
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == saved["revision"]

        async with app.state.sessionmaker() as db:
            commands = list(
                await db.scalars(
                    select(RunnerCommand)
                    .where(RunnerCommand.template_id == template_id)
                    .order_by(RunnerCommand.seq)
                )
            )
            assert len(commands) == 2
            old, current = commands
            assert old.id == old_id
            assert (current.host_id != old_host_id) == (retry == "resave_other")
            current_id, current_host_id = current.id, current.host_id
            if old_result == "expired":
                old.deadline_at = specs.now() - timedelta(seconds=1)
                old.attempts = MAX_ATTEMPTS
                await db.commit()
                await runner_reclaim._expire_commands(
                    Runner(db, app.state.settings), runner_reclaim.TickResult()
                )
            else:
                assert await RunnerHosts(db, app.state.settings).result(
                    await db.get(RunnerHost, old_host_id),
                    old_id,
                    RunnerCommandResult(
                        status=old_result,
                        result={"image_digest": "sha256:old", "log": "old build"},
                        error="old failure" if old_result == "failed" else None,
                    ),
                )
            await db.commit()

        async with app.state.sessionmaker() as db:
            template = await db.get(RunnerTemplate, template_id)
            assert template is not None
            assert template.build_host_id == current_host_id
            assert template.build_status == "building"
            assert template.build_error is None
            assert template.image_digest is None
            assert not template.build_log
            assert await RunnerHosts(db, app.state.settings).result(
                await db.get(RunnerHost, current_host_id),
                current_id,
                RunnerCommandResult(
                    status="done",
                    result={"image_digest": "sha256:current", "log": "current build"},
                ),
            )
            await db.commit()
            await db.refresh(template)
            assert template.build_status == "ready"
            assert template.image_digest == "sha256:current"
            assert template.build_log == "current build"


@pytest.mark.integration
@pytest.mark.parametrize("late_status", ["done", "failed"])
async def test_template_attempt_fence_survives_command_retention(
    late_status: str, make_client: ClientFactory, migrated_database_url: str
) -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from app.domain import runner_reclaim
    from app.domain.runner_hosts import RunnerHosts
    from app.models import RunnerCommand, RunnerTemplate
    from app.schemas.runner import RunnerCommandResult
    from tests.test_runner_api import SETTINGS, add_host
    from tests.workspace_support import app_of, open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        await add_host(env)
        body = {"name": "retained-attempt", "spec": {"image": "python:3.12-slim"}}
        first = await env.client.put(
            "/api/v1/runner/templates", json=body, headers=env.headers(write=True)
        )
        assert first.status_code == 200, first.text
        template_id = uuid.UUID(first.json()["id"])
        second = await env.client.put(
            "/api/v1/runner/templates",
            json={**body, "version": first.json()["version"]},
            headers=env.headers(write=True),
        )
        assert second.status_code == 200, second.text
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            commands = list(
                await db.scalars(
                    select(RunnerCommand)
                    .where(RunnerCommand.template_id == template_id)
                    .order_by(RunnerCommand.seq)
                )
            )
            old, current = commands
            old.status = "unknown"
            service = RunnerHosts(db, app.state.settings)
            host = await db.get(RunnerHost, current.host_id)
            assert await service.result(
                host,
                current.id,
                RunnerCommandResult(status="done", result={"image_digest": "sha256:current"}),
            )
            current.finished_at = specs.now() - timedelta(days=8)
            await db.commit()
            await runner_reclaim._purge(Runner(db, app.state.settings), runner_reclaim.TickResult())
            await db.commit()
            assert await service.result(
                host,
                old.id,
                RunnerCommandResult(
                    status=late_status,
                    result={"image_digest": "sha256:stale"},
                    error="stale failure" if late_status == "failed" else None,
                ),
            )
            await db.commit()
            template = await db.get(RunnerTemplate, template_id)
            assert template is not None
            assert template.build_status == "ready"
            assert template.image_digest == "sha256:current"
            assert template.build_error is None
