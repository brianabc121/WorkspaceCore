"""Kubernetes workload admission before placement and concrete command payloads."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.runner import Runner, RunnerError, Viewer
from app.domain.runner_specs import normalize
from app.models import AgentInstance, RunnerEnvironment, RunnerHost
from app.schemas.placement import Placement
from app.schemas.runner import EnvCreateInput, RunnerHostCandidatesOutput, RunnerSpecIn

IMAGE = "registry.invalid/example/app@sha256:" + "a" * 64


def spec(**updates: Any) -> dict[str, Any]:
    return {**normalize({"image": IMAGE}), **updates}


def environment(**updates: Any) -> RunnerEnvironment:
    return RunnerEnvironment(
        id=uuid4(),
        workspace_id=uuid4(),
        host_id=uuid4(),
        runtime_backend="kubernetes",
        spec=spec(**updates),
        setup=[],
        lifetime="session",
        container_name="container",
        volume_name="volume",
        network_name="network",
    )


def host(backend: str, *, load: float = 0) -> RunnerHost:
    return RunnerHost(
        id=uuid4(),
        workspace_id=uuid4(),
        name=backend,
        backend=backend,
        node_name="node-a" if backend == "kubernetes" else None,
        status="online",
        cpus=8,
        mem_bytes=8 << 30,
        disk_bytes=100 << 30,
        runtimes=["runsc"],
        kvm=False,
        runtime_metadata={
            "network_validation_revision": 1,
            "network_ready": True,
            "supported_profile": "gvisor-immutable-v1",
        },
        report={
            "physical_identity_verified": True,
            "physical_host_id": "b" * 64,
            "capacity_sampled_at": datetime.now(UTC).isoformat(),
            "mem_free_bytes": 7 << 30,
            "disk_free_bytes": 90 << 30,
            "load": load,
        },
    )


class PoolRunner(Runner):
    def __init__(self, hosts: list[RunnerHost]) -> None:
        session = AsyncSession()
        super().__init__(session, Settings(app_env="test"))
        self.pool = hosts

    async def _lock_pool_hosts(self, workspace_id: Any) -> list[RunnerHost]:
        return self.pool

    async def hosts(self, workspace_id: Any) -> list[RunnerHost]:
        return self.pool

    async def select_host(
        self,
        workspace_id: UUID,
        spec: dict[str, Any],
        placement: Any,
        **kwargs: Any,
    ) -> RunnerHost:
        with patch.object(self.db, "scalars", new=AsyncMock(return_value=[])):
            return await super().select_host(workspace_id, spec, placement, **kwargs)

    async def host_candidates(self, viewer: Viewer) -> RunnerHostCandidatesOutput:
        with patch.object(self.db, "scalars", new=AsyncMock(return_value=[])):
            return await super().host_candidates(viewer)


@pytest.mark.parametrize(
    "updates",
    [
        {"image": "example:latest"},
        {"runtime": "runc"},
        {"user": "root"},
        {"network": "all"},
        {"env": {"EXAMPLE": "value"}},
        {"secrets": ["secret-name"]},
        {"steps": ["RUN true"]},
        {"memory_mb": 256.5},
        {"disk_gb": 2.5},
    ],
)
def test_kubernetes_payload_rejects_unsupported_profile(updates: dict[str, Any]) -> None:
    runner = PoolRunner([])
    with pytest.raises(RunnerError) as error:
        runner.create_payload(environment(**updates))
    assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"


def test_payload_normalizes_integral_memory_disk_and_template_digest() -> None:
    env = environment(
        image="base:mutable",
        template_image="local/template:ready",
        image_digest="sha256:" + "b" * 64,
        memory_mb=256.0,
        disk_gb=2.0,
        start_command=["python", "server.py"],
    )
    payload = PoolRunner([]).create_payload(env)
    assert payload["image"] == "local/template:ready@sha256:" + "b" * 64
    assert type(payload["memory_mb"]) is int and payload["memory_mb"] == 256
    assert type(payload["disk_gb"]) is int and payload["disk_gb"] == 2
    assert payload["start_command"] == ["python", "server.py"]


@pytest.mark.parametrize(
    "extra",
    [
        {"files": [{"path": "file", "b64": "eA=="}]},
        {"snapshot": {"key": "snapshot"}},
        {"source": environment()},
    ],
)
def test_payload_refuses_unsupported_materialization(extra: dict[str, Any]) -> None:
    with pytest.raises(RunnerError, match="Kubernetes"):
        PoolRunner([]).create_payload(environment(), **extra)


def test_docker_payload_keeps_legacy_mutable_and_resource_semantics() -> None:
    env = environment(image="python:3.12", env={"VALUE": "example"}, disk_gb=2.5)
    env.runtime_backend = "docker"
    payload = PoolRunner([]).create_payload(env)
    assert payload["image"] == "python:3.12"
    assert payload["env"] == {"VALUE": "example"}
    assert payload["disk_gb"] == 2.5


@pytest.mark.asyncio
async def test_auto_placement_excludes_unsupported_kubernetes_without_changing_docker() -> None:
    kubernetes, docker = host("kubernetes"), host("docker", load=2)
    runner = PoolRunner([kubernetes, docker])
    selected = await runner.select_host(uuid4(), spec(image="python:3.12"), Placement())
    assert selected.id == docker.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"image": "python:3.12"},
        {"files": ["file"]},
        {"copy_from_volume": "volume"},
        {"command": ["true"]},
    ],
)
async def test_specified_kubernetes_reports_unsupported_reason_before_reservation(
    updates: dict[str, Any],
) -> None:
    kubernetes = host("kubernetes")
    runner = PoolRunner([kubernetes])
    with pytest.raises(RunnerError) as error:
        await runner.select_host(
            uuid4(), spec(**updates), Placement(mode="specified", server_id=kubernetes.id)
        )
    assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
    assert "Kubernetes" in error.value.message


@pytest.mark.asyncio
async def test_supported_profile_can_select_validated_kubernetes() -> None:
    kubernetes = host("kubernetes")
    runner = PoolRunner([kubernetes])
    runner.settings.runner_network_verified = True
    selected = await runner.select_host(uuid4(), spec(), Placement())
    assert selected.id == kubernetes.id


@pytest.mark.asyncio
async def test_candidate_summary_explains_unsupported_node_profile() -> None:
    kubernetes = host("kubernetes")
    kubernetes.runtime_metadata["supported_profile"] = "arbitrary"
    output = await PoolRunner([kubernetes]).host_candidates(Viewer(uuid4()))
    assert not output.items[0].eligible
    assert "gvisor-immutable-v1" in " ".join(output.items[0].reasons)


@pytest.mark.parametrize("field", ["memory_mb", "disk_gb"])
def test_api_schema_rejects_fractional_integral_resources(field: str) -> None:
    with pytest.raises(ValidationError):
        RunnerSpecIn.model_validate({"image": IMAGE, field: 64.5})


def test_pure_profile_rejects_stale_template_and_invalid_start_shape() -> None:
    from app.domain.runner_runtime_profile import UnsupportedRuntimeProfile, kubernetes_profile

    for updates in (
        {"template_image": "local/template:mutable"},
        {"start_command": "python server.py"},
        {"setup": ["echo unsupported"]},
        {"snapshot": {"key": "data"}},
        {"template_build": {"tag": "image"}},
    ):
        with pytest.raises(UnsupportedRuntimeProfile):
            kubernetes_profile(spec(**updates))


@pytest.mark.asyncio
async def test_create_files_rejected_before_any_environment_or_command_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.runner_capabilities import RunnerQuotaService

    node = host("kubernetes")
    runner = PoolRunner([node])
    instance = AgentInstance(id=uuid4(), workspace_id=node.workspace_id)
    monkeypatch.setattr(runner, "_lock_instance", AsyncMock())
    monkeypatch.setattr(runner, "_check_secrets", AsyncMock())
    monkeypatch.setattr(RunnerQuotaService, "check_count", AsyncMock())
    body = EnvCreateInput.model_validate(
        {
            "purpose": "profile",
            "lifetime": "session",
            "spec": {"image": IMAGE},
            "placement": {"mode": "specified", "server_id": str(node.id)},
            "files": [{"path": "file", "text": "content"}],
        }
    )
    with pytest.raises(RunnerError) as error:
        await runner.create(instance, body)
    assert error.value.code == "RUNTIME_PROFILE_UNSUPPORTED"
    assert "files" in error.value.message
    assert not runner.db.new  # no pending reservation or command, even before rollback


@pytest.mark.asyncio
async def test_managed_auto_placement_does_not_mix_new_containers_with_legacy_docker() -> None:
    kubernetes, docker = host("kubernetes", load=2), host("docker", load=0)
    runner = PoolRunner([kubernetes, docker])
    runner.settings.runner_network_verified = True
    assert (await runner.select_host(uuid4(), spec(), Placement())).id == kubernetes.id
    with pytest.raises(RunnerError) as error:
        await runner.select_host(uuid4(), spec(), Placement(mode="specified", server_id=docker.id))
    assert error.value.code == "RUNTIME_NETWORK_UNSUPPORTED"
    assert (
        await runner.select_host(
            uuid4(),
            spec(),
            Placement(mode="specified", server_id=docker.id),
            exclude_environment=uuid4(),
        )
    ).id == docker.id


@pytest.mark.asyncio
async def test_unverified_managed_backend_cannot_admit_even_when_node_claims_ready() -> None:
    kubernetes = host("kubernetes")
    runner = PoolRunner([kubernetes])
    assert not runner.settings.runner_network_verified
    with pytest.raises(RunnerError) as error:
        await runner.select_host(
            uuid4(), spec(), Placement(mode="specified", server_id=kubernetes.id)
        )
    assert error.value.code == "NETWORK_VALIDATION_REQUIRED"
