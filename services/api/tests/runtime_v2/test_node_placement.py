"""Kubernetes placement requires validated network and a finite runtime profile."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.runner_placement import candidate_for
from app.models.runner import RunnerHost


def host() -> RunnerHost:
    return RunnerHost(
        id=uuid4(),
        backend="kubernetes",
        node_name="node-a",
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
            "physical_host_id": "a" * 64,
            "capacity_sampled_at": datetime.now(UTC).isoformat(),
            "mem_free_bytes": 6 << 30,
            "disk_free_bytes": 80 << 30,
            "load": 0.5,
        },
    )


def test_verified_node_supports_only_gvisor_profile() -> None:
    candidate = candidate_for(host(), [])
    assert candidate.enabled
    assert candidate.runtimes == ("gvisor",)


@pytest.mark.parametrize(
    "key,value",
    [
        ("network_ready", False),
        ("network_ready", "true"),
        ("network_validation_revision", 0),
        ("network_validation_revision", True),
        ("supported_profile", "arbitrary"),
    ],
)
def test_missing_or_untrusted_node_capability_remains_closed(key: str, value: object) -> None:
    node = host()
    node.runtime_metadata[key] = value
    assert not candidate_for(node, []).enabled


def test_untrusted_physical_node_and_unknown_runtime_remain_closed() -> None:
    node = host()
    node.report["physical_identity_verified"] = False
    assert not candidate_for(node, []).enabled
    node = host()
    node.runtimes = []
    assert not candidate_for(node, []).enabled


def test_docker_runtime_selection_is_preserved() -> None:
    node = host()
    node.backend = "docker"
    node.runtime_metadata = {}
    node.runtimes = ["runc", "runsc"]
    node.kvm = True
    candidate = candidate_for(node, [])
    assert candidate.enabled
    assert candidate.runtimes == ("runc", "gvisor", "kvm")
