"""Placement rejects stale metrics and preserves an explicit target."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 11, 11, 5, 0, tzinfo=UTC)
A = UUID("00000000-0000-0000-0000-000000000001")
B = UUID("00000000-0000-0000-0000-000000000002")


def candidate(server_id=A, **overrides):
    from app.domain.placement import Candidate

    values = dict(
        server_id=server_id,
        cpus=8,
        memory_mb=16384,
        disk_gb=100,
        available_memory_mb=12000,
        disk_free_gb=80,
        cpu_load=0.1,
        committed_cpus=1,
        committed_memory_mb=2048,
        observed_committed_memory_mb=0,
        committed_disk_gb=10,
        observed_at=NOW,
        runtimes=("gvisor", "runc"),
    )
    values.update(overrides)
    return Candidate(**values)


def test_default_contract_and_explicit_validation():
    from app.schemas.placement import Placement

    assert Placement().mode == "auto"
    for values in [
        dict(mode="auto", server_id=A),
        dict(mode="specified"),
        dict(mode="specified", server_id="not-a-uuid"),
        dict(mode="typo"),
        dict(mode="auto", url="http://private"),
    ]:
        with pytest.raises(ValidationError):
            Placement(**values)
    assert Placement(mode="specified", server_id=A).server_id == A


def test_selects_lower_loaded_server_after_pending_capacity():
    from app.domain.placement import ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    result = rank_candidates(
        [candidate(A, cpu_load=0.85), candidate(B, cpu_load=0.1)],
        ResourceRequest(2, 2048, 5),
        Placement(),
        NOW,
    )
    assert [c.server_id for c in result] == [B, A]


def test_explicit_target_never_falls_back():
    from app.domain.placement import PlacementError, ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    with pytest.raises(PlacementError) as exc:
        rank_candidates(
            [candidate(A, available_memory_mb=256), candidate(B)],
            ResourceRequest(2, 2048, 5),
            Placement(mode="specified", server_id=A),
            NOW,
        )
    assert exc.value.code == "TARGET_UNAVAILABLE"
    assert exc.value.reasons[str(A)] == "INSUFFICIENT_CAPACITY"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"observed_at": None}, "METRICS_UNKNOWN"),
        ({"observed_at": NOW - timedelta(seconds=61)}, "METRICS_STALE"),
        ({"observed_at": NOW + timedelta(seconds=1)}, "METRICS_UNKNOWN"),
        ({"cpus": float("nan")}, "METRICS_UNKNOWN"),
        ({"cpus": None}, "METRICS_UNKNOWN"),
        ({"available_memory_mb": None}, "METRICS_UNKNOWN"),
        ({"memory_mb": True}, "METRICS_UNKNOWN"),
        ({"available_memory_mb": -1}, "METRICS_UNKNOWN"),
        ({"cpu_load": float("inf")}, "METRICS_UNKNOWN"),
        ({"enabled": False}, "TARGET_UNAVAILABLE"),
        ({"runtimes": ("runc",)}, "RUNTIME_UNAVAILABLE"),
        ({"committed_cpus": 6}, "INSUFFICIENT_CAPACITY"),
        ({"committed_memory_mb": 13000}, "INSUFFICIENT_CAPACITY"),
        ({"committed_disk_gb": 89}, "INSUFFICIENT_CAPACITY"),
        ({"pending_memory_mb": 11000}, "INSUFFICIENT_CAPACITY"),
        ({"pending_disk_gb": 79}, "INSUFFICIENT_CAPACITY"),
        ({"cpu_load": 0.96}, "RESOURCE_PRESSURE"),
        ({"cpu_load": 1.5}, "RESOURCE_PRESSURE"),
    ],
)
def test_ineligible_metrics_or_reservations_are_excluded(changes, reason):
    from app.domain.placement import PlacementError, ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    with pytest.raises(PlacementError) as exc:
        rank_candidates(
            [candidate(**changes)], ResourceRequest(2, 2048, 5, "gvisor"), Placement(), NOW
        )
    assert exc.value.reasons[str(A)] == reason


def test_running_usage_is_not_double_counted_against_free_memory():
    from app.domain.placement import ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    chosen = candidate(
        committed_memory_mb=8000, observed_committed_memory_mb=8000, available_memory_mb=4096
    )
    result = rank_candidates([chosen], ResourceRequest(1, 2048, 5), Placement(), NOW)
    assert result[0].server_id == A


def test_external_memory_usage_preserves_unused_existing_commitments():
    from app.domain.placement import PlacementError, ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    node = candidate(
        memory_mb=10240,
        available_memory_mb=1945,
        committed_memory_mb=1536,
        observed_committed_memory_mb=100,
    )
    with pytest.raises(PlacementError) as error:
        rank_candidates([node], ResourceRequest(1, 1024, 1), Placement(), NOW)
    assert error.value.reasons[str(A)] == "INSUFFICIENT_CAPACITY"


@pytest.mark.parametrize("credit", [None, -1, float("nan"), 2049])
def test_existing_commitments_require_valid_bounded_usage_credit(credit):
    from app.domain.placement import PlacementError, ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    with pytest.raises(PlacementError) as error:
        rank_candidates(
            [candidate(observed_committed_memory_mb=credit)],
            ResourceRequest(1, 1024, 1),
            Placement(),
            NOW,
        )
    assert error.value.reasons[str(A)] == "METRICS_UNKNOWN"


def test_deterministic_ties_and_nonpositive_requests():
    from app.domain.placement import ResourceRequest, rank_candidates
    from app.schemas.placement import Placement

    result = rank_candidates(
        [candidate(B), candidate(A)], ResourceRequest(1, 2048, 5), Placement(), NOW
    )
    assert [c.server_id for c in result] == [A, B]
    for req in [
        ResourceRequest(0, 100, 1),
        ResourceRequest(1, -1, 1),
        ResourceRequest(float("nan"), 100, 1),
    ]:
        with pytest.raises(ValueError):
            rank_candidates([candidate()], req, Placement(), NOW)
