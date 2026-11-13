"""运行服务器池：实时指标、持久环境预约与共享筛选契约。"""

from datetime import datetime
from math import isfinite

from app.domain.placement import Candidate
from app.models.runner import RunnerEnvironment, RunnerHost

COMPUTE_STATES = frozenset({"creating", "running", "failed", "pending_delete"})
RETAINED_STATES = frozenset({"creating", "running", "stopped", "failed", "pending_delete"})


def compute_reserved(env: RunnerEnvironment) -> bool:
    """状态名称不能替代停止回执；撤销删除与快照失败也保留未确认的预约。"""
    if env.state in ("archived", "deleted"):
        return False
    stopped = (env.runtime_metadata or {}).get("compute_stopped")
    if stopped is not None:
        return not bool(stopped)
    return env.state in COMPUTE_STATES


def _pending_compute(env: RunnerEnvironment) -> bool:
    metadata = env.runtime_metadata or {}
    reserved = metadata.get("compute_reserved_at")
    observed = metadata.get("compute_observed_at")
    if reserved is not None:
        return observed is None or str(observed) <= str(reserved)
    return env.state in ("creating", "failed", "pending_delete")


def candidate_for(host: RunnerHost, environments: list[RunnerEnvironment]) -> Candidate:
    report = host.report or {}
    observed: datetime | None
    try:
        observed = datetime.fromisoformat(str(report["capacity_sampled_at"]))
        if observed.tzinfo is None or report.get("physical_identity_verified") is not True:
            observed = None
    except (KeyError, TypeError, ValueError):
        observed = None
    backend = host.backend or "docker"
    metadata = host.runtime_metadata or {}
    kubernetes_ready = (
        backend == "kubernetes"
        and observed is not None
        and type(metadata.get("network_validation_revision")) is int
        and metadata.get("network_validation_revision") == 1
        and metadata.get("network_ready") is True
        and metadata.get("supported_profile") == "gvisor-immutable-v1"
        and isinstance(host.node_name, str)
        and bool(host.node_name)
        and "runsc" in (host.runtimes or [])
    )
    compute = [env.spec for env in environments if compute_reserved(env)]
    retained = [env.spec for env in environments if env.state in RETAINED_STATES]
    pending = [env.spec for env in environments if compute_reserved(env) and _pending_compute(env)]
    memory_credit = 0.0
    memory_credit_known = True
    for env in environments:
        if not compute_reserved(env) or _pending_compute(env):
            continue
        value = (env.runtime_metadata or {}).get("compute_memory_working_set_bytes")
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not isfinite(value)
            or value < 0
        ):
            memory_credit_known = False
            break
        memory_credit += min(float(env.spec["memory_mb"]), value / 1024**2)
    runtimes: tuple[str, ...] = ()
    if backend == "kubernetes" and kubernetes_ready:
        runtimes = ("gvisor",)
    elif backend == "docker":
        runtimes = tuple(
            ["runc"]
            + (["gvisor"] if "runsc" in (host.runtimes or []) else [])
            + (["kvm"] if host.kvm else [])
        )
    return Candidate(
        server_id=host.id,
        cpus=host.cpus,
        memory_mb=host.mem_bytes / 1024**2 if host.mem_bytes is not None else None,
        disk_gb=host.disk_bytes / 1024**3 if host.disk_bytes is not None else None,
        available_memory_mb=(
            report["mem_free_bytes"] / 1024**2 if report.get("mem_free_bytes") is not None else None
        ),
        disk_free_gb=(
            report["disk_free_bytes"] / 1024**3
            if report.get("disk_free_bytes") is not None
            else None
        ),
        cpu_load=(
            report["load"] / host.cpus if report.get("load") is not None and host.cpus else None
        ),
        committed_cpus=sum(float(spec.get("cpus", 0)) for spec in compute),
        committed_memory_mb=sum(float(spec.get("memory_mb", 0)) for spec in compute),
        committed_disk_gb=sum(float(spec.get("disk_gb", 0)) for spec in retained),
        pending_memory_mb=sum(float(spec.get("memory_mb", 0)) for spec in pending),
        pending_disk_gb=sum(float(spec.get("disk_gb", 0)) for spec in pending),
        observed_committed_memory_mb=memory_credit if memory_credit_known else None,
        observed_at=observed,
        enabled=host.status == "online" and (backend == "docker" or kubernetes_ready),
        runtimes=runtimes,
    )
