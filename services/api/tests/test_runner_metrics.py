"""容器的资源读数：口径与新鲜度（T-0185，容器服务器方案指标约定第 5 节）。纯函数，不需要数据库。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.runner_metrics import (
    apply_container_reading,
    apply_volume_reading,
    environment_metrics,
)
from app.models.runner import RunnerEnvironment, RunnerHost
from app.schemas.runner import RunnerReportContainer

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
MIB = 1024 * 1024


def _host(**fields: Any) -> RunnerHost:
    values: dict[str, Any] = {"id": uuid.uuid4(), "status": "online", "last_seen_at": NOW}
    values.update(fields)
    return RunnerHost(**values)


def _env(host: RunnerHost, **fields: Any) -> RunnerEnvironment:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "host_id": host.id,
        "state": "running",
        "spec": {"cpus": 2, "memory_mb": 2048},
        "cpu_used_cores": 0.6,
        "cpu_window_seconds": 60.0,
        "cpu_reported_at": NOW,
        "mem_bytes": 640 * MIB,
        "mem_reported_at": NOW,
        "disk_bytes": 3 * 1024 * MIB,
        "disk_reported_at": NOW,
    }
    values.update(fields)
    return RunnerEnvironment(**values)


def test_percentages_use_cores_and_mib() -> None:
    host = _host()
    m = environment_metrics(_env(host), host, now=NOW)
    assert m.cpu_status == m.memory_status == m.data_volume_status == "ok"
    assert m.cpu_used_cores == pytest.approx(0.6)
    assert m.cpu_percent_of_limit == pytest.approx(30)
    assert m.cpu_window_seconds == pytest.approx(60)
    assert m.memory_used_bytes == 640 * MIB
    assert m.memory_percent_of_limit == pytest.approx(31.25)
    assert m.data_volume_bytes == 3 * 1024 * MIB


def test_over_limit_is_kept_and_zero_limit_has_no_percent() -> None:
    host = _host()
    m = environment_metrics(_env(host, cpu_used_cores=3.0, mem_bytes=3072 * MIB), host, now=NOW)
    assert m.cpu_percent_of_limit == pytest.approx(150)  # 不截断
    assert m.memory_percent_of_limit == pytest.approx(150)
    m = environment_metrics(_env(host, spec={"cpus": 0, "memory_mb": 0}), host, now=NOW)
    assert m.cpu_percent_of_limit is None and m.memory_percent_of_limit is None
    assert m.cpu_status == "ok"


def test_real_zero_is_ok_not_unknown() -> None:
    host = _host()
    m = environment_metrics(_env(host, cpu_used_cores=0.0), host, now=NOW)
    assert m.cpu_status == "ok" and m.cpu_used_cores == 0.0 and m.cpu_percent_of_limit == 0


def test_freshness_boundary_is_180_seconds() -> None:
    host = _host()
    edge = NOW - timedelta(seconds=180)
    m = environment_metrics(
        _env(host, cpu_reported_at=edge, mem_reported_at=edge, disk_reported_at=edge),
        host,
        now=NOW,
    )
    assert (m.cpu_status, m.memory_status, m.data_volume_status) == ("ok", "ok", "ok")
    late = NOW - timedelta(seconds=181)
    m = environment_metrics(
        _env(host, cpu_reported_at=late, mem_reported_at=NOW, disk_reported_at=late),
        host,
        now=NOW,
    )
    assert (m.cpu_status, m.memory_status, m.data_volume_status) == ("stale", "ok", "stale")
    assert m.cpu_used_cores == pytest.approx(0.6)  # 过期的数字保留，带原来的时间
    assert m.cpu_reported_at == late


@pytest.mark.parametrize(
    "host_fields",
    [
        {"status": "offline"},
        {"status": "disabled"},
        {"status": "pending"},
        {"last_seen_at": NOW - timedelta(seconds=91)},  # 显示为在线，其实太久没联系
        {"last_seen_at": None},
    ],
)
def test_host_not_reporting_makes_readings_stale(host_fields: dict[str, Any]) -> None:
    host = _host(**host_fields)
    m = environment_metrics(_env(host), host, now=NOW)
    assert (m.cpu_status, m.memory_status, m.data_volume_status) == ("stale",) * 3
    assert m.cpu_used_cores == pytest.approx(0.6)


def test_no_host_or_other_host_is_stale() -> None:
    host = _host()
    m = environment_metrics(_env(host), None, now=NOW)
    assert m.cpu_status == "stale"
    m = environment_metrics(_env(host), _host(), now=NOW)  # 记录属于另一台主机
    assert m.cpu_status == "stale"


def test_old_value_without_time_is_stale_with_unknown_time() -> None:
    host = _host()
    m = environment_metrics(
        _env(
            host,
            cpu_used_cores=None,
            cpu_window_seconds=None,
            cpu_reported_at=None,
            mem_reported_at=None,
            disk_reported_at=None,
        ),
        host,
        now=NOW,
    )
    assert m.cpu_status == "unavailable" and m.cpu_used_cores is None
    assert m.memory_status == "stale" and m.memory_reported_at is None
    assert m.data_volume_status == "stale" and m.data_volume_reported_at is None


def test_missing_or_negative_values_are_unavailable() -> None:
    host = _host()
    m = environment_metrics(
        _env(host, mem_bytes=None, disk_bytes=-1, cpu_used_cores=None, cpu_window_seconds=None),
        host,
        now=NOW,
    )
    assert (m.cpu_status, m.memory_status, m.data_volume_status) == ("unavailable",) * 3
    assert m.memory_used_bytes is None and m.data_volume_bytes is None


@pytest.mark.parametrize("state", ["stopped", "creating", "failed", "pending_delete"])
def test_not_running_hides_live_cpu_and_memory_but_keeps_volume(state: str) -> None:
    host = _host()
    m = environment_metrics(_env(host, state=state), host, now=NOW)
    assert m.cpu_status == m.memory_status == "not_running"
    assert m.cpu_used_cores is None and m.memory_used_bytes is None
    assert m.cpu_percent_of_limit is None and m.memory_percent_of_limit is None
    assert m.data_volume_status == "ok" and m.data_volume_bytes == 3 * 1024 * MIB


@pytest.mark.parametrize("state", ["archived", "deleted"])
def test_archived_has_no_current_volume(state: str) -> None:
    host = _host()
    m = environment_metrics(_env(host, state=state), host, now=NOW)
    assert m.data_volume_status == "unavailable" and m.data_volume_bytes is None


def _reading(**fields: Any) -> RunnerReportContainer:
    return RunnerReportContainer.model_validate(
        {"env_id": str(uuid.uuid4()), "name": "c", "state": "running", **fields}
    )


def test_apply_reading_sets_and_clears_with_server_time() -> None:
    host = _host()
    env = _env(host, cpu_reported_at=None, mem_reported_at=None)
    apply_container_reading(
        env, _reading(mem_bytes=5, cpu_used_cores=0.25, cpu_window_seconds=60), NOW
    )
    assert (env.mem_bytes, env.mem_reported_at) == (5, NOW)
    assert (env.cpu_used_cores, env.cpu_window_seconds, env.cpu_reported_at) == (0.25, 60, NOW)
    # 旧 agent（不带 CPU）且这一轮内存没读到：两项都清空，不给旧数字盖新时间
    later = NOW + timedelta(minutes=1)
    apply_container_reading(env, _reading(), later)
    assert env.mem_bytes is None and env.mem_reported_at is None
    assert env.cpu_used_cores is None and env.cpu_reported_at is None
    # 容器不在报告里：同样清空
    apply_container_reading(env, _reading(mem_bytes=7), later)
    apply_container_reading(env, None, later)
    assert env.mem_bytes is None and env.mem_reported_at is None


def test_apply_volume_keeps_last_good_reading_when_unknown() -> None:
    host = _host()
    env = _env(host, disk_bytes=None, disk_reported_at=None)
    apply_volume_reading(env, 0, NOW)
    assert (env.disk_bytes, env.disk_reported_at) == (0, NOW)  # 真实的 0
    apply_volume_reading(env, 100, NOW)
    later = NOW + timedelta(minutes=5)
    apply_volume_reading(env, None, later)
    apply_volume_reading(env, -1, later)
    assert (env.disk_bytes, env.disk_reported_at) == (100, NOW)  # 保留上次的读数与时间
