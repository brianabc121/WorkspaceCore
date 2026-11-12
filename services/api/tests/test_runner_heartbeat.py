"""Unknown telemetry keeps command delivery alive without granting capacity."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.domain.runner import Runner
from app.domain.runner_hosts import RunnerHosts
from app.domain.runner_placement import candidate_for
from app.models import RunnerEnvironment
from app.schemas.runner import RunnerPoll
from tests.test_runner_multihost import host


@pytest.mark.parametrize("kind", ["create_env", "start_env", "stop_env", "delete_env"])
def test_enqueued_lifecycle_binds_host_and_overrides_untrusted_payload(kind):
    node = host(node_name="actual-node")
    environment = RunnerEnvironment(
        id=uuid.uuid4(),
        workspace_id=node.workspace_id,
        owner_instance_id=uuid.uuid4(),
        host_id=node.id,
        runtime_backend="kubernetes",
        runtime_metadata={"generation": 3},
    )
    command = Runner(MagicMock(), Settings()).enqueue(
        node,
        kind,
        {"host_id": str(uuid.uuid4()), "runtime_identity": {"node_name": "attacker"}},
        env=environment,
    )
    assert command.payload["host_id"] == str(node.id)
    assert command.payload["runtime_identity"]["node_name"] == node.node_name
    assert command.payload["runtime_identity"]["workspace_id"] == str(node.workspace_id)
    assert command.payload["runtime_identity"]["generation"] == 3


@pytest.mark.parametrize("missing", ["cpus", "mem_bytes", "disk_bytes", "disk_free_bytes"])
async def test_missing_heartbeat_metric_preserves_last_readings_but_invalidates_admission(missing):
    node = host()
    original = {
        key: getattr(node, key) for key in ("cpus", "mem_bytes", "disk_bytes", "disk_free_bytes")
    }
    body = {
        "agent_version": "0.2.0",
        "docker_version": None,
        "runtimes": [],
        "kvm": False,
        "cpus": 4,
        "mem_bytes": 8 << 30,
        "disk_bytes": 100 << 30,
        "disk_free_bytes": 50 << 30,
    }
    body[missing] = None
    db = AsyncMock()
    await RunnerHosts(db, Settings(), AsyncMock()).check_in(node, RunnerPoll(**body))
    assert node.status == "online"
    assert {key: getattr(node, key) for key in original} == original
    assert node.report["capacity_sampled_at"] is None
    assert node.report["capacity_error"] == "METRICS_UNKNOWN"
    assert candidate_for(node, []).observed_at is None
    db.refresh.assert_awaited_once_with(node, with_for_update=True)


async def test_unknown_heartbeat_does_not_become_fresh_when_poll_metrics_resume():
    node = host()
    hosts = RunnerHosts(AsyncMock(), Settings(), AsyncMock())
    unknown = RunnerPoll(agent_version="0.2.0", runtimes=[], kvm=False)
    await hosts.check_in(node, unknown)
    assert node.cpus == 8
    full = unknown.model_copy(
        update={
            "cpus": 4,
            "mem_bytes": 8 << 30,
            "disk_bytes": 100 << 30,
            "disk_free_bytes": 50 << 30,
        }
    )
    await hosts.check_in(node, full)
    assert node.cpus == 4
    assert candidate_for(node, []).observed_at is None


@pytest.mark.integration
async def test_unknown_heartbeat_delivers_pending_command_without_renewing_capacity(
    make_client, migrated_database_url
):
    from sqlalchemy import select

    from app.models import RunnerHost
    from tests.test_runner_api import SETTINGS, add_host, runner_headers
    from tests.workspace_support import app_of, open_env

    async with open_env(make_client, migrated_database_url, **SETTINGS) as env:
        token = await add_host(env)
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            node = await db.scalar(
                select(RunnerHost).where(RunnerHost.workspace_id == env.workspace_id)
            )
            assert node is not None
            assert candidate_for(node, []).observed_at is not None
            command = Runner(db, app.state.settings).enqueue(
                node, "stop_env", {"env_id": str(uuid.uuid4())}
            )
            command_id = command.id
            capacity = node.mem_bytes
            await db.commit()
        result = await env.client.post(
            "/runner-api/v1/poll",
            json={"agent_version": "0.2.0", "runtimes": [], "kvm": False, "wait_seconds": 0},
            headers=runner_headers(token),
        )
        assert result.status_code == 200, result.text
        assert result.json()["commands"][0]["id"] == str(command_id)
        async with app.state.sessionmaker() as db:
            node = await db.scalar(
                select(RunnerHost).where(RunnerHost.workspace_id == env.workspace_id)
            )
            assert node is not None
            assert node.status == "online"
            assert node.mem_bytes == capacity
            assert node.report["capacity_error"] == "METRICS_UNKNOWN"
            assert candidate_for(node, []).observed_at is None
