"""容器服务器的资源读数：上报时怎么存、读的时候怎么判断新鲜度（T-0185）。

依据 docs/v2/container-server-redesign/ 的指标与接口约定第 4、5 节与
docs/decisions/2026-11-09-runner-container-metrics.md。

- `apply_container_reading` / `apply_volume_reading` 在 RunnerHosts.report 的事务里调用：
  读数与“平台收到的时间”（服务端 UTC）一起写，读不到的项清空，不给旧数字盖新时间；
- `environment_metrics` 是纯函数：同一份记录、同一个 now 总是得到同样的结果；
  列表排序（`cpu_sort_key` 等）用同一套判断，保证“按 CPU 排序”与页面上标的“实时 / 过期”一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, case, literal, null

from app.domain.runner import ONLINE_SECONDS
from app.models.runner import RunnerEnvironment, RunnerHost
from app.schemas.runner import MetricStatus, RunnerEnvMetrics, RunnerReportContainer

FRESH_SECONDS = 180  # 报告默认 60 秒一次；超过 3 个周期没收到新读数就算过期
MIB = 1024 * 1024
# 归档 / 删除后数据卷已经删掉（常驻的数据在快照里），不再把旧的卷大小当成现在的占用
NO_VOLUME_STATES = frozenset({"archived", "deleted"})


# ---------------------------------------------------------------- 上报时写入


def apply_container_reading(
    env: RunnerEnvironment, container: RunnerReportContainer | None, now: datetime
) -> None:
    """一份完整报告里这个环境的容器读数；容器不在报告里（或不在运行）时 CPU / 内存都清空。"""
    mem = container.mem_bytes if container is not None else None
    if mem is not None and mem >= 0:
        env.mem_bytes = mem
        env.mem_reported_at = now
    else:
        env.mem_bytes = None
        env.mem_reported_at = None
    cores = container.cpu_used_cores if container is not None else None
    window = container.cpu_window_seconds if container is not None else None
    if cores is not None and window is not None:
        env.cpu_used_cores = cores
        env.cpu_window_seconds = window
        env.cpu_reported_at = now
    else:
        clear_live_readings(env, memory=False)


def apply_volume_reading(env: RunnerEnvironment, size: int | None, now: datetime) -> None:
    """数据卷占用：读数有效才更新数值与时间。

    读不到（Docker 的 -1、报告里没有这个卷）时保留上一次的合法读数与它原来的时间：
    页面按时间标成过期；回收规则（常驻环境的数据超过快照上限只停止、不归档）
    仍按上一次的大小判断，不因为一次没读到就放宽。
    """
    if size is not None and size >= 0:
        env.disk_bytes = size
        env.disk_reported_at = now


def clear_live_readings(env: RunnerEnvironment, *, memory: bool = True) -> None:
    env.cpu_used_cores = None
    env.cpu_window_seconds = None
    env.cpu_reported_at = None
    if memory:
        env.mem_bytes = None
        env.mem_reported_at = None


# ---------------------------------------------------------------- 读的时候判断


def host_reporting(host: RunnerHost | None, now: datetime) -> bool:
    """主机在线且没停用：与页面上的主机状态同一个判断（runner_hosts.host_out），
    在线但超过 ONLINE_SECONDS 没联系也算离线。"""
    return (
        host is not None
        and host.status == "online"
        and host.last_seen_at is not None
        and host.last_seen_at >= now - timedelta(seconds=ONLINE_SECONDS)
    )


def _fresh(reported_at: datetime | None, now: datetime, host_ok: bool) -> bool:
    return (
        host_ok
        and reported_at is not None
        and reported_at >= now - timedelta(seconds=FRESH_SECONDS)
    )


def _percent(used: float | None, limit: float) -> float | None:
    if used is None or limit <= 0:
        return None
    return used / limit * 100


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def environment_metrics(
    env: RunnerEnvironment, host: RunnerHost | None, *, now: datetime
) -> RunnerEnvMetrics:
    host_ok = host_reporting(host, now) and host is not None and env.host_id == host.id
    running = env.state == "running"
    spec = env.spec or {}

    cpu_status: MetricStatus
    cores = window = cpu_at = None
    if not running:
        cpu_status = "not_running"
    elif env.cpu_used_cores is None or env.cpu_used_cores < 0:
        cpu_status = "unavailable"
    else:
        cores, window, cpu_at = env.cpu_used_cores, env.cpu_window_seconds, env.cpu_reported_at
        cpu_status = "ok" if _fresh(cpu_at, now, host_ok) else "stale"

    mem_status: MetricStatus
    mem = mem_at = None
    if not running:
        mem_status = "not_running"
    elif env.mem_bytes is None or env.mem_bytes < 0:
        mem_status = "unavailable"
    else:
        mem, mem_at = env.mem_bytes, env.mem_reported_at
        mem_status = "ok" if _fresh(mem_at, now, host_ok) else "stale"

    disk_status: MetricStatus
    disk = disk_at = None
    if env.state in NO_VOLUME_STATES or env.disk_bytes is None or env.disk_bytes < 0:
        disk_status = "unavailable"
    else:
        disk, disk_at = env.disk_bytes, env.disk_reported_at
        disk_status = "ok" if _fresh(disk_at, now, host_ok) else "stale"

    return RunnerEnvMetrics(
        cpu_used_cores=cores,
        cpu_percent_of_limit=_percent(cores, _number(spec.get("cpus"))),
        cpu_window_seconds=window,
        cpu_reported_at=cpu_at,
        cpu_status=cpu_status,
        memory_used_bytes=mem,
        memory_percent_of_limit=_percent(mem, _number(spec.get("memory_mb")) * MIB),
        memory_reported_at=mem_at,
        memory_status=mem_status,
        data_volume_bytes=disk,
        data_volume_reported_at=disk_at,
        data_volume_status=disk_status,
    )


# ---------------------------------------------------------------- 列表排序（与上面的判断一致）


def _fresh_sql(
    value: Any, reported_at: Any, host: RunnerHost | None, now: datetime
) -> ColumnElement[bool]:
    if not host_reporting(host, now) or host is None:
        return literal(False)
    return and_(
        RunnerEnvironment.state == "running",
        RunnerEnvironment.host_id == host.id,
        value.is_not(None),
        value >= 0,
        reported_at.is_not(None),
        reported_at >= now - timedelta(seconds=FRESH_SECONDS),
    )


def cpu_sort_key(host: RunnerHost | None, now: datetime) -> ColumnElement[Any]:
    """实时（ok）的 CPU 读数；过期、未知、未运行为 NULL（排在最后）。"""
    value = RunnerEnvironment.cpu_used_cores
    return case(
        (_fresh_sql(value, RunnerEnvironment.cpu_reported_at, host, now), value), else_=null()
    )


def memory_sort_key(host: RunnerHost | None, now: datetime) -> ColumnElement[Any]:
    value = RunnerEnvironment.mem_bytes
    return case(
        (_fresh_sql(value, RunnerEnvironment.mem_reported_at, host, now), value), else_=null()
    )


__all__ = [
    "FRESH_SECONDS",
    "apply_container_reading",
    "apply_volume_reading",
    "clear_live_readings",
    "cpu_sort_key",
    "environment_metrics",
    "host_reporting",
    "memory_sort_key",
]
