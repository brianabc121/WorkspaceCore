"""Unsupported Kubernetes operations must not leave commands or partial state."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import runner_reclaim
from app.domain import runner_specs as specs
from app.domain.runner import Runner, RunnerError, Viewer
from app.domain.runner_snapshots import SnapshotStore
from app.models import (
    AgentInstance,
    RunnerCapabilityRequest,
    RunnerCommand,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerTemplate,
)
from app.models.runner import RunnerSnapshot
from app.schemas.placement import Placement
from app.schemas.runner import EnvFilesInput
from tests.runtime_v2.test_runtime_profile import environment, host
from tests.test_mcp_api import new_instance
from tests.test_runner_api import SETTINGS
from tests.workspace_support import Env, app_of, open_env

UNSUPPORTED = ("exec", "get_files", "put_files", "list_files", "snapshot", "archive_env", "restore")


@pytest.mark.parametrize("kind", UNSUPPORTED)
def test_enqueue_rejects_unsupported_kubernetes_without_creating_command(kind: str) -> None:
    db = AsyncSession()
    runner = Runner(db, Settings(app_env="test"))
    with pytest.raises(RunnerError) as error:
        runner.enqueue(host("kubernetes"), kind, {}, env=environment())
    assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
    assert not db.new


@pytest.mark.parametrize("kind", ["prune_images", "build_template"])
def test_host_commands_cannot_bypass_kubernetes_admission(kind: str) -> None:
    db = AsyncSession()
    with pytest.raises(RunnerError) as error:
        Runner(db, Settings(app_env="test")).enqueue(host("kubernetes"), kind, {})
    assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
    assert not db.new


@pytest.mark.parametrize(
    "kind", (*UNSUPPORTED, "create_env", "start_env", "stop_env", "delete_env")
)
def test_docker_and_supported_kubernetes_commands_remain_available(kind: str) -> None:
    db = AsyncSession()
    runner = Runner(db, Settings(app_env="test"))
    target = environment()
    target.runtime_backend = "docker"
    command = runner.enqueue(host("docker"), kind, {}, env=target)
    assert command.kind == kind and command.environment_id == target.id
    if kind not in UNSUPPORTED:
        target.runtime_backend = "kubernetes"
        command = runner.enqueue(host("kubernetes"), kind, {}, env=target)
        assert command.payload["backend"] == "kubernetes"


@pytest.fixture
async def lab(make_client: Any, migrated_database_url: str) -> AsyncIterator[tuple[Env, UUID]]:
    async with open_env(
        make_client, migrated_database_url, **SETTINGS, runner_network_verified=True
    ) as value:
        instance_id = UUID(await new_instance(value))
        app = app_of(value.client)
        async with app.state.sessionmaker() as db:
            instance = await db.get(AgentInstance, instance_id)
            assert instance is not None
            node = host("kubernetes")
            node.workspace_id, node.name = instance.workspace_id, "managed-test"
            node.token_hash, node.token_prefix = uuid4().hex, uuid4().hex[:8]
            node.last_seen_at = specs.now()
            db.add(node)
            await db.flush()
            target = environment()
            target.workspace_id, target.host_id = instance.workspace_id, node.id
            target.owner_instance_id = instance.id
            target.purpose, target.state = "admission test", "running"
            target.runtime_metadata = {
                "generation": 1,
                "compute_stopped": False,
                "compute_memory_working_set_bytes": 128 << 20,
            }
            target.lifetime, target.state_reason = "persistent", "original reason"
            db.add(target)
            await db.commit()
            target_id = target.id
        yield value, target_id


async def assert_no_work(db: AsyncSession, environment_id: UUID) -> None:
    # Commit after rejection: rollback must not be the reason partial work vanished.
    await db.commit()
    for model in (RunnerCommand, RunnerRun, RunnerSnapshot):
        count = await db.scalar(
            select(func.count()).select_from(model).where(model.environment_id == environment_id)
        )
        assert count == 0, model.__name__


@pytest.mark.integration
@pytest.mark.parametrize("state", ["running", "stopped", "archived"])
async def test_exec_rejects_before_restart_rebuild_or_run_record(
    lab: tuple[Env, UUID], state: str
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        target.state = state
        await db.commit()
        before = dict(target.runtime_metadata)
        with pytest.raises(RunnerError) as error:
            await Runner(db, app.state.settings).start_run(None, target, ["true"], 60)
        assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
        assert target.state == state and target.runtime_metadata == before
        await assert_no_work(db, target_id)


@pytest.mark.integration
@pytest.mark.parametrize("action", ["read", "write", "list"])
async def test_files_reject_before_queue_or_touch(
    lab: tuple[Env, UUID], action: Literal["read", "write", "list"], monkeypatch: pytest.MonkeyPatch
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    monkeypatch.setattr(specs, "SYNC_WAIT_SECONDS", 0)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None and target.owner_instance_id is not None
        instance = await db.get(AgentInstance, target.owner_instance_id)
        assert instance is not None
        before = target.last_used_at, target.use_count
        with pytest.raises(RunnerError) as error:
            await Runner(db, app.state.settings).files(
                instance,
                EnvFilesInput(environment_id=target_id, action=action, path="data", text="x"),
            )
        assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
        assert (target.last_used_at, target.use_count) == before
        await assert_no_work(db, target_id)


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["snapshot", "archive", "delete", "setup"])
async def test_reject_before_stop_snapshot_record_or_configuration_write(
    lab: tuple[Env, UUID], operation: str
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None and target.owner_instance_id is not None
        node = await db.get(RunnerHost, target.host_id)
        instance = await db.get(AgentInstance, target.owner_instance_id)
        assert node is not None and instance is not None
        runner = Runner(db, app.state.settings)
        before = (target.state, target.state_reason, target.version, list(target.setup))
        with pytest.raises(RunnerError) as error:
            if operation == "snapshot":
                await runner.start_snapshot(node, target, then="delete")
            elif operation == "archive":
                await runner.archive(target, "archive request")
            elif operation == "delete":
                await runner.delete(target, "delete request")
            else:
                await runner.setup(instance, target.id, ["echo changed"])
        assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
        assert (target.state, target.state_reason, target.version, target.setup) == before
        await assert_no_work(db, target_id)


@pytest.mark.integration
@pytest.mark.parametrize("state", ["pending_delete", "stopped"])
async def test_reclaimer_preserves_unsupported_data_and_continues_other_environments(
    lab: tuple[Env, UUID],
    state: str,
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        target.state, target.delete_after = state, specs.now() - timedelta(seconds=1)
        target.last_used_at = target.stopped_at = specs.now() - timedelta(days=60)
        docker = RunnerHost(
            id=uuid4(),
            workspace_id=target.workspace_id,
            name="docker-test",
            backend="docker",
            token_hash=uuid4().hex,
            token_prefix=uuid4().hex[:8],
            status="online",
            last_seen_at=specs.now(),
        )
        db.add(docker)
        await db.flush()
        other = environment()
        other.workspace_id, other.host_id = target.workspace_id, docker.id
        other.runtime_backend, other.owner_instance_id = "docker", target.owner_instance_id
        other.purpose, other.state = "other environment", "stopped"
        other.stopped_at = other.last_used_at = specs.now() - timedelta(days=3)
        db.add(other)
        await db.commit()
        # Real tick must commit both the diagnostic and unrelated lifecycle work.
        out = await runner_reclaim.tick(db, app.state.settings)
        assert target.state == state and target.state_reason is not None
        assert "Kubernetes" in target.state_reason
        assert out.deleted == 0 and out.archived == 1
        kinds = list(
            await db.scalars(
                select(RunnerCommand.kind).where(RunnerCommand.environment_id == other.id)
            )
        )
        assert kinds == ["archive_env"]
        await assert_no_work(db, target_id)


@pytest.mark.integration
async def test_kubernetes_image_cleanup_does_not_queue_docker_command(
    lab: tuple[Env, UUID],
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        await runner_reclaim._prune_images(Runner(db, app.state.settings), node)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(RunnerCommand)) == 0


@pytest.mark.integration
@pytest.mark.parametrize("existing", [False, True])
async def test_template_build_rejects_before_persisting_configuration(
    lab: tuple[Env, UUID], existing: bool
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        template = RunnerTemplate(
            workspace_id=target.workspace_id,
            name="template",
            spec=target.spec,
            description="original",
            build_host_id=target.host_id,
            build_status="ready",
        )
        if existing:
            db.add(template)
            await db.commit()
        runner = Runner(db, app.state.settings)
        # The selected host really is eligible; rejection must happen in operation admission.
        node = await runner.select_host(target.workspace_id, target.spec, Placement())
        assert node.id == target.host_id
        with pytest.raises(RunnerError) as error:
            await runner.save_template(
                Viewer(target.workspace_id),
                name="template",
                description="changed",
                spec=target.spec,
            )
        assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
        await db.commit()
        rows = list(await db.scalars(select(RunnerTemplate)))
        assert len(rows) == (1 if existing else 0)
        if existing:
            assert (template.description, template.build_status, template.version) == (
                "original",
                "ready",
                1,
            )
        await assert_no_work(db, target_id)


@pytest.mark.integration
async def test_admin_template_rebuild_returns_unsupported_without_state_write(
    lab: tuple[Env, UUID],
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        template = RunnerTemplate(
            workspace_id=target.workspace_id,
            name="template",
            spec=target.spec,
            build_host_id=target.host_id,
            build_status="ready",
            build_error="original error",
        )
        db.add(template)
        await db.commit()
        template_id = template.id
    response = await value.client.post(
        f"/api/v1/runner/templates/{template_id}/rebuild", headers=value.headers(write=True)
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "RUNTIME_PROFILE_UNSUPPORTED"
    async with app.state.sessionmaker() as db:
        stored = await db.get(RunnerTemplate, template_id)
        assert stored is not None
        assert (stored.build_status, stored.build_error, stored.version) == (
            "ready",
            "original error",
            1,
        )
        assert await db.scalar(select(func.count()).select_from(RunnerCommand)) == 0


@pytest.mark.integration
@pytest.mark.parametrize("retained", [0, 5])
async def test_restore_rejects_snapshot_before_generation_or_state_write(
    lab: tuple[Env, UUID],
    retained: int,
) -> None:
    value, target_id = lab
    app = app_of(value.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        target.state, target.runtime_metadata = (
            "archived",
            {"generation": 1, "compute_stopped": True},
        )
        owner = await db.get(AgentInstance, target.owner_instance_id)
        assert owner is not None
        owner.runner_enabled = True
        snapshot = await SnapshotStore(db, app.state.settings).begin(target)
        snapshot.status = "ready"
        for _ in range(retained):
            other = environment()
            other.workspace_id, other.host_id = target.workspace_id, target.host_id
            other.owner_instance_id = target.owner_instance_id
            other.purpose, other.state = "retained environment", "stopped"
            db.add(other)
        await db.commit()
        before = target.state, target.state_reason, dict(target.runtime_metadata)
        with pytest.raises(RunnerError) as error:
            await Runner(db, app.state.settings).rebuild(target, "restore request")
        assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
        assert (target.state, target.state_reason, target.runtime_metadata) == before
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(RunnerCommand)) == 0
        assert await db.scalar(select(func.count()).select_from(RunnerSnapshot)) == 1
        assert await db.scalar(select(func.count()).select_from(RunnerCapabilityRequest)) == 0
