"""Pure resource-aware placement. Pool adapters own scope, locks and reservation persistence."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from uuid import UUID

from app.schemas.placement import Placement


@dataclass(frozen=True)
class ResourceRequest:
    cpus: float
    memory_mb: float
    disk_gb: float
    runtime: str | None = None


@dataclass(frozen=True)
class Candidate:
    server_id: UUID
    cpus: float | None
    memory_mb: float | None
    disk_gb: float | None
    available_memory_mb: float | None
    disk_free_gb: float | None
    cpu_load: float | None
    committed_cpus: float
    committed_memory_mb: float
    committed_disk_gb: float
    observed_at: datetime | None
    enabled: bool = True
    runtimes: tuple[str, ...] = ()
    pending_memory_mb: float = 0
    pending_disk_gb: float = 0


class PlacementError(ValueError):
    def __init__(self, code: str, message: str, reasons: dict[str, str]) -> None:
        self.code = code
        self.message = message
        self.reasons = reasons
        super().__init__(message)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and isfinite(value)


def _metric(value: float | None) -> float:
    if value is None:
        raise ValueError("missing metric")
    return value


def _reason(candidate: Candidate, request: ResourceRequest, now: datetime) -> str | None:
    if not candidate.enabled:
        return "TARGET_UNAVAILABLE"
    observed = candidate.observed_at
    if observed is None or observed.tzinfo is None or observed.utcoffset() is None:
        return "METRICS_UNKNOWN"
    age = (now - observed).total_seconds()
    if age < 0:
        return "METRICS_UNKNOWN"
    if age > 60:
        return "METRICS_STALE"
    totals = (candidate.cpus, candidate.memory_mb, candidate.disk_gb)
    nonnegative = (
        candidate.available_memory_mb,
        candidate.disk_free_gb,
        candidate.cpu_load,
        candidate.committed_cpus,
        candidate.committed_memory_mb,
        candidate.committed_disk_gb,
        candidate.pending_memory_mb,
        candidate.pending_disk_gb,
    )
    if any(not _number(v) or _metric(v) <= 0 for v in totals):
        return "METRICS_UNKNOWN"
    if any(not _number(v) or _metric(v) < 0 for v in nonnegative):
        return "METRICS_UNKNOWN"
    if (
        _metric(candidate.available_memory_mb) > _metric(candidate.memory_mb)
        or _metric(candidate.disk_free_gb) > _metric(candidate.disk_gb)
        or _metric(candidate.cpu_load) > 1
    ):
        return "METRICS_UNKNOWN"
    if request.runtime is not None and request.runtime not in candidate.runtimes:
        return "RUNTIME_UNAVAILABLE"
    if (
        candidate.committed_cpus + request.cpus > _metric(candidate.cpus) * 0.9
        or candidate.committed_memory_mb + request.memory_mb > _metric(candidate.memory_mb) * 0.9
        or candidate.committed_disk_gb + request.disk_gb > _metric(candidate.disk_gb) * 0.9
        or request.memory_mb + candidate.pending_memory_mb > _metric(candidate.available_memory_mb)
        or request.disk_gb + candidate.pending_disk_gb > _metric(candidate.disk_free_gb)
    ):
        return "INSUFFICIENT_CAPACITY"
    if _metric(candidate.cpu_load) >= 0.95:
        return "RESOURCE_PRESSURE"
    return None


def rank_candidates(
    candidates: Iterable[Candidate], request: ResourceRequest, placement: Placement, now: datetime
) -> list[Candidate]:
    """Return eligible hosts sorted by predicted utilization; specified is a hard constraint."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    if any(not _number(v) or v <= 0 for v in (request.cpus, request.memory_mb, request.disk_gb)):
        raise ValueError("resource requests must be finite and positive")
    reasons: dict[str, str] = {}
    eligible: list[Candidate] = []
    for candidate in candidates:
        if placement.mode == "specified" and candidate.server_id != placement.server_id:
            continue
        reason = _reason(candidate, request, now)
        if reason:
            reasons[str(candidate.server_id)] = reason
        else:
            eligible.append(candidate)
    if not eligible:
        if placement.mode == "specified":
            reasons.setdefault(str(placement.server_id), "TARGET_UNAVAILABLE")
            raise PlacementError(
                "TARGET_UNAVAILABLE", "指定服务器当前无法部署，请处理原因或重新选择", reasons
            )
        raise PlacementError("NO_ELIGIBLE_SERVER", "没有满足资源、健康与指标要求的服务器", reasons)

    def score(c: Candidate) -> tuple[float, str]:
        utilization = max(
            (c.committed_cpus + request.cpus) / _metric(c.cpus),
            (c.committed_memory_mb + request.memory_mb) / _metric(c.memory_mb),
            (c.committed_disk_gb + request.disk_gb) / _metric(c.disk_gb),
        )
        return utilization + _metric(c.cpu_load) * 0.2, str(c.server_id)

    return sorted(eligible, key=score)
