"""Real PostgreSQL admission; SSH/Docker resources are simulated by FakeHost."""

import asyncio
import json
import uuid
from typing import Any

import pytest

from app.domain import agent_server_resources
from app.domain.agent_servers import AgentServerService
from app.domain.agent_ssh_install import SshJobRunner
from tests import test_agent_deployment_requests as base
from tests.helpers import run_sql
from tests.test_agent_deployment_requests import (
    _CHOICE,
    _call,
    _count,
    _deploy,
    _ready_server,
    _receipt,
)
from tests.test_agent_ssh_install import FakeHost, Owner, ssh_host

pytestmark = pytest.mark.integration
owner = base.owner
fast_online = base.fast_online


def _auto(**extra: Any) -> dict[str, Any]:
    return {"request_id": str(uuid.uuid4()), "name": "auto", "model": dict(_CHOICE), **extra}


def _park(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SshJobRunner, "start", lambda *a, **kw: None)
    monkeypatch.setattr(SshJobRunner, "running", lambda *a, **kw: True)


async def _snapshot(owner: Owner, server_id: str, **changes: Any) -> None:
    from datetime import UTC, datetime

    from tests.test_agent_placement import snapshot

    value = agent_server_resources.parse_resources(
        json.dumps({**snapshot(), **changes}), received_at=datetime.now(UTC)
    )
    value["physical_identity_verified"] = True  # controlled trusted-snapshot fixture
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_servers SET resource_snapshot=$1::jsonb WHERE id=$2",
        json.dumps(value),
        uuid.UUID(server_id),
    )


async def test_auto_probes_empty_host_and_persists_actual_selection(owner, tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        sid = await _ready_server(owner, port)
        await run_sql(
            owner.database_url, "UPDATE workspace.agent_servers SET resource_snapshot='{}'::jsonb"
        )
        _park(monkeypatch)
        request = _auto()
        result = await _deploy(owner, request)
        assert result.status_code == 202, result.text
        selection = result.json()["deployment"]["deployment"]["selection"]
        assert selection["requested_mode"] == "auto" and selection["actual_server_id"] == sid
        assert selection["reservation_id"] and selection["metrics_at"]
        count = host.logins.count("password")
        replay = await _deploy(owner, request)
        assert replay.status_code == 202, replay.text
        assert replay.json()["deployment"]["deployment"]["selection"] == selection
        assert host.logins.count("password") == count
        assert (await _receipt(owner, request["request_id"])).json()["state"] == "accepted"


async def test_stale_empty_host_failure_never_uses_instance_report(owner, tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        await _ready_server(owner, port)
    _park(monkeypatch)
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_servers SET resource_snapshot='{}'::jsonb, "
        'latest=\'{"cores":9999,"mem_available":99999999999999}\'::jsonb',
    )
    result = await _deploy(owner, _auto())
    assert result.status_code == 409 and result.json()["error"]["code"] == "METRICS_STALE", (
        result.text
    )
    assert await _count(owner, "agent_instances") == 0


async def test_two_agent_registrations_cannot_double_count_one_physical_host(
    owner,
    tmp_path,
    monkeypatch,
):
    first = FakeHost(tmp_path / "one")
    second = FakeHost(tmp_path / "two")
    second.resources["physical_host_id"] = first.resources["physical_host_id"]
    async with ssh_host(first) as (one, _), ssh_host(second) as (two, _):
        await _ready_server(owner, one)
        from tests.test_agent_servers_api import _confirmed_server

        duplicate = await _confirmed_server(owner, two)
        refused = await owner.initialize(duplicate["id"])
        assert refused["status"] == "failed", refused
        assert refused["error_code"] == "physical_host_conflict"
        assert not (second.home / "myworkspace-openclaw").exists()
        # Also cover an old already-prepared registration from before this
        # upgrade: sampling must still reject the conflicting physical host.
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_targets SET prepared_at=now() WHERE server_id=$1",
            uuid.UUID(duplicate["id"]),
        )
        rejected = await _call(
            owner,
            "POST",
            f"/api/v1/agents/servers/{duplicate['id']}/resources/refresh",
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "PHYSICAL_HOST_CONFLICT"
        _park(monkeypatch)
        result = await _deploy(owner, _auto())
        assert result.status_code == 202, result.text
        assert await _count(owner, "physical_host_registrations") == 1
        assert (
            await _count(
                owner,
                "agent_servers",
                "resource_snapshot->>'physical_identity_verified' = 'true'",
            )
            == 1
        )
        assert (
            await _count(
                owner,
                "agent_servers",
                "resource_snapshot->>'probe_error' = 'PHYSICAL_HOST_CONFLICT'",
            )
            == 1
        )


async def test_specified_never_falls_back_to_healthy_host(owner, tmp_path, monkeypatch):
    host = FakeHost(tmp_path / "small")
    healthy = FakeHost(tmp_path / "healthy")
    async with ssh_host(host) as (port, _), ssh_host(healthy) as (healthy_port, _):
        sid = await _ready_server(owner, port)
        await _ready_server(owner, healthy_port)
        _park(monkeypatch)
        await _snapshot(owner, sid, cores=1)
        result = await _deploy(owner, _auto(placement={"mode": "specified", "server_id": sid}))
        assert result.status_code == 409, result.text
        assert await _count(owner, "agent_instances") == 0


async def test_concurrent_admission_counts_pending_failed_jobs(owner, tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        sid = await _ready_server(owner, port)
        await _snapshot(owner, sid, cores=2)
        _park(monkeypatch)
        # Let a second request reach capacity admission without relying on the
        # separate one-job-per-host unique index. Even failed installs retain
        # their durable resource reservation.
        original = AgentServerService._new_job

        async def failed_job(self, *a, **kw):
            job = await original(self, *a, **kw)
            job.status = "failed"
            await self.db.flush()
            return job

        async def idle(*a, **kw):
            return False

        monkeypatch.setattr(AgentServerService, "_new_job", failed_job)
        monkeypatch.setattr(AgentServerService, "_busy", idle)
        results = await asyncio.gather(
            _deploy(owner, _auto()), _deploy(owner, _auto(name="second"))
        )
        assert sorted(r.status_code for r in results) == [202, 409], [r.text for r in results]
        assert await _count(owner, "agent_deployments") == 1
        assert await _count(owner, "agent_instances") == 1
        refused = next(r for r in results if r.status_code == 409)
        assert "INSUFFICIENT_CAPACITY" in refused.text


async def test_existing_deploy_requires_capacity_without_rewriting_legacy(
    owner, tmp_path, monkeypatch
):
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        sid = await _ready_server(owner, port)
        _park(monkeypatch)
        instance_id = await owner.instance()
        result = await _call(
            owner,
            "POST",
            f"/api/v1/agents/instances/{instance_id}/deploy",
            json={"server_id": sid, "version": await owner.instance_version(instance_id)},
        )
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "UNACCOUNTED_LEGACY_RESOURCES"
        limits = await run_sql(
            owner.database_url,
            "SELECT mem_limit_mb FROM workspace.agent_instances WHERE id=$1",
            uuid.UUID(instance_id),
        )
        assert limits is None


async def test_failed_redeploy_keeps_previous_larger_resource_promise(owner, tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        await _ready_server(owner, port)
        _park(monkeypatch)
        original = AgentServerService._new_job

        async def failed_job(self, *args, **kwargs):
            job = await original(self, *args, **kwargs)
            job.status = "failed"
            await self.db.flush()
            return job

        async def idle(*args, **kwargs):
            return False

        monkeypatch.setattr(AgentServerService, "_new_job", failed_job)
        monkeypatch.setattr(AgentServerService, "_busy", idle)
        first = await _deploy(owner, _auto(mem_limit_mb=8192))
        assert first.status_code == 202, first.text
        instance = first.json()["instance"]
        server_id = first.json()["deployment"]["deployment"]["server_id"]
        limited = await _call(
            owner,
            "PUT",
            f"/api/v1/agents/instances/{instance['id']}/limits",
            json={"cpus": 1, "mem_limit_mb": 2048, "version": instance["version"]},
        )
        assert limited.status_code == 200, limited.text
        retry = await _call(
            owner,
            "POST",
            f"/api/v1/agents/instances/{instance['id']}/deploy",
            json={"server_id": server_id, "version": await owner.instance_version(instance["id"])},
        )
        assert retry.status_code == 202, retry.text
        assert retry.json()["deployment"]["selection"]["memory_mb"] == 8192


async def test_unmanaged_unlimited_instance_excludes_host_from_auto(owner, tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id = await _ready_server(owner, port)
        instance_id = await owner.instance()
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_instances SET server_id=$1 WHERE id=$2",
            uuid.UUID(server_id),
            uuid.UUID(instance_id),
        )
        _park(monkeypatch)
        result = await _deploy(owner, _auto())
        assert result.status_code == 409 and "UNACCOUNTED_LEGACY_RESOURCES" in result.text
        assert await _count(owner, "agent_instances") == 1


async def test_reconcile_legacy_limits_uses_trusted_runtime_and_preserves_desired_settings(
    owner, tmp_path, monkeypatch
):
    from datetime import UTC, datetime

    import app.domain.agent_runtime_resources as runtime_resources

    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        await _ready_server(owner, port)
        _park(monkeypatch)
        result = await _deploy(owner, _auto())
        assert result.status_code == 202, result.text
        instance_id = result.json()["instance"]["id"]
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_deployments SET selection='{}'::jsonb, "
            "runtime_installed=true, installed_at=now(), install_dir='/srv/managed/app' "
            "WHERE instance_id=$1",
            uuid.UUID(instance_id),
        )
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_jobs SET status='succeeded' WHERE instance_id=$1",
            uuid.UUID(instance_id),
        )

        async def inspect(target, box, *, project, directory):
            assert project.endswith(instance_id.replace("-", "")[:12])
            assert directory == "/srv/managed/app"
            return {
                "cpus": 2.0,
                "memory_mb": 4096,
                "container_id": "a" * 64,
                "source": "trusted_docker_inspect",
                "observed_at": datetime.now(UTC).isoformat(),
            }

        monkeypatch.setattr(runtime_resources, "inspect_runtime_limits", inspect)
        current = await owner.instance_version(instance_id)
        response = await _call(
            owner,
            "POST",
            f"/api/v1/agents/instances/{instance_id}/resources/reconcile",
            json={"version": current},
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["deployment"]["selection"]
        assert receipt["cpus"] == 2.0 and receipt["memory_mb"] == 4096
        details = await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}")
        assert details.json()["limits"] == {"cpus": 1.0, "mem_limit_mb": 2048}
