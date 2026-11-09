"""容器 CPU 差分采样与 runner-agent 上报（T-0184，容器服务器方案指标约定第 2、3 节）。

不需要 Docker：采样器是纯计算；上报用假的 Docker 客户端。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.runner_agent import agent as agent_module
from app.runner_agent import containers as c
from app.runner_agent.agent import Agent, Config
from app.runner_agent.docker import DockerError
from app.runner_agent.metrics import (
    ContainerCPUSampler,
    cpu_total_ns,
    memory_bytes,
    nonnegative_int,
    started_generation,
)
from app.schemas.runner import RunnerReport, RunnerReportContainer

S = 1_000_000_000  # 1 秒（纳秒）


# ------------------------------------------------------------------ 采样器


def test_contract_numbers() -> None:
    sampler = ContainerCPUSampler()
    assert sampler.sample("a", "g1", 10 * S, 100 * S).used_cores is None  # 第一次：未知
    sample = sampler.sample("a", "g1", 11_200_000_000, 102 * S)
    assert sample.used_cores is not None
    assert sample.used_cores == pytest.approx(0.6)
    assert sample.window_seconds == pytest.approx(2.0)
    assert sample.used_cores / 2 * 100 == pytest.approx(30)  # 配额 2 核 → 30%

    multi = ContainerCPUSampler()
    multi.sample("b", "g1", 0, 0)
    sample = multi.sample("b", "g1", 6 * S, 2 * S)
    assert sample.used_cores is not None
    assert sample.used_cores == pytest.approx(3.0)  # 可以超过 1 核
    assert sample.used_cores / 4 * 100 == pytest.approx(75)


def test_zero_delta_is_zero_not_unknown() -> None:
    sampler = ContainerCPUSampler()
    sampler.sample("a", "g1", 5 * S, 0)
    sample = sampler.sample("a", "g1", 5 * S, 60 * S)
    assert sample.used_cores == 0.0
    assert sample.window_seconds == pytest.approx(60.0)


def test_restart_and_rebuild_start_over() -> None:
    sampler = ContainerCPUSampler()
    sampler.sample("a", "g1", 50 * S, 0)
    # 重启：同一个 ID、新的启动代次，计数器从 0 开始
    assert sampler.sample("a", "g2", 1 * S, 60 * S).used_cores is None
    assert sampler.sample("a", "g2", 2 * S, 120 * S).used_cores == pytest.approx(1 / 60)
    # 同名重建：另一个 ID，和旧 ID 的基线互不影响
    assert sampler.sample("a2", "g1", 51 * S, 180 * S).used_cores is None


def test_counter_going_back_resets() -> None:
    sampler = ContainerCPUSampler()
    sampler.sample("a", "g1", 50 * S, 0)
    assert sampler.sample("a", "g1", 40 * S, 60 * S).used_cores is None
    # 回退后的读数成了新基线
    assert sampler.sample("a", "g1", 41 * S, 120 * S).used_cores == pytest.approx(1 / 60)


def test_missing_or_negative_reading_is_unknown_and_keeps_baseline() -> None:
    sampler = ContainerCPUSampler()
    sampler.sample("a", "g1", 10 * S, 0)
    assert sampler.sample("a", "g1", None, 60 * S).used_cores is None
    assert sampler.sample("a", "g1", -1, 60 * S).used_cores is None
    assert sampler.sample("a", "", 20 * S, 60 * S).used_cores is None  # 没有可靠代次
    sample = sampler.sample("a", "g1", 22 * S, 120 * S)
    assert sample.used_cores == pytest.approx(12 / 120)
    assert sample.window_seconds == pytest.approx(120)


def test_window_bounds() -> None:
    sampler = ContainerCPUSampler()
    sampler.sample("a", "g1", 0, 0)
    # 不足 1 秒（含时钟没走、倒退）：不算，保留上一个基线
    assert sampler.sample("a", "g1", S // 2, S // 2).used_cores is None
    assert sampler.sample("a", "g1", S, 0).used_cores is None
    assert sampler.sample("a", "g1", S, -5 * S).used_cores is None
    sample = sampler.sample("a", "g1", S, 2 * S)
    assert sample.used_cores == pytest.approx(0.5)
    # 正好 180 秒仍有效
    sample = sampler.sample("a", "g1", 91 * S, 182 * S)
    assert sample.window_seconds == pytest.approx(180)
    # 超过 180 秒：当作第一次采样
    assert sampler.sample("a", "g1", 200 * S, 363 * S).used_cores is None
    assert sampler.sample("a", "g1", 201 * S, 364 * S).used_cores == pytest.approx(1.0)


def test_prune_drops_gone_containers() -> None:
    sampler = ContainerCPUSampler()
    for name in ("a", "b", "c"):
        sampler.sample(name, "g1", 0, 0)
    sampler.prune({"b"})
    assert len(sampler) == 1
    assert sampler.sample("a", "g1", S, 10 * S).used_cores is None  # a 的基线已删
    assert sampler.sample("b", "g1", S, 10 * S).used_cores == pytest.approx(0.1)


def test_reading_helpers() -> None:
    assert nonnegative_int(-1) is None
    assert nonnegative_int(None) is None
    assert nonnegative_int(True) is None
    assert nonnegative_int("5") is None
    assert nonnegative_int(float("nan")) is None
    assert nonnegative_int(0) == 0
    assert cpu_total_ns({"cpu_stats": {"cpu_usage": {"total_usage": 124}}}) == 124
    assert cpu_total_ns({"cpu_stats": {}}) is None
    assert memory_bytes({"memory_stats": {}}) is None  # 已停止的容器：未知，不是 0
    assert memory_bytes({"memory_stats": {"usage": 0}}) == 0
    assert started_generation({"State": {"StartedAt": "2026-11-09T09:34:14.016061437Z"}})
    assert started_generation({"State": {"StartedAt": "0001-01-01T00:00:00Z"}}) is None
    assert started_generation(None) is None


# ------------------------------------------------------------------ 上报协议


def _container(**extra: Any) -> dict[str, Any]:
    return {"env_id": str(uuid.uuid4()), "name": "myws-env-x", "state": "running", **extra}


def test_report_without_cpu_still_parses() -> None:
    """旧版 runner-agent 不带 CPU 字段：照常解析，两项都是空。"""
    item = RunnerReportContainer.model_validate(_container(mem_bytes=100, size_rw=5))
    assert item.cpu_used_cores is None
    assert item.cpu_window_seconds is None


@pytest.mark.parametrize(
    "fields",
    [
        {"cpu_used_cores": float("nan"), "cpu_window_seconds": 2},
        {"cpu_used_cores": float("inf"), "cpu_window_seconds": 2},
        {"cpu_used_cores": -0.1, "cpu_window_seconds": 2},
        {"cpu_used_cores": 0.5},
        {"cpu_window_seconds": 2},
        {"cpu_used_cores": 0.5, "cpu_window_seconds": 0.5},
        {"cpu_used_cores": 0.5, "cpu_window_seconds": 181},
    ],
)
def test_report_rejects_bad_cpu(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RunnerReportContainer.model_validate(_container(**fields))


def test_report_accepts_more_than_one_core_and_zero() -> None:
    assert RunnerReportContainer.model_validate(
        _container(cpu_used_cores=3.0, cpu_window_seconds=60)
    ).cpu_used_cores == pytest.approx(3.0)
    assert (
        RunnerReportContainer.model_validate(
            _container(cpu_used_cores=0, cpu_window_seconds=60)
        ).cpu_used_cores
        == 0
    )


def test_report_over_capacity_is_rejected_not_truncated() -> None:
    """超过 2000 个容器：整份报告被拒（不会只收前 2000 个、把其余的判成丢失）。"""
    with pytest.raises(ValidationError):
        RunnerReport.model_validate({"containers": [_container() for _ in range(2001)]})


# ------------------------------------------------------------------ runner-agent 的上报


class FakeDocker:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.stats_by_name: dict[str, dict[str, Any] | Exception] = {}
        self.started: dict[str, str] = {}
        self.volume_sizes: dict[str, int | None] = {}
        self.calls: list[str] = []

    def add(self, env_id: str, name: str, *, state: str = "running", cid: str = "") -> None:
        self.items.append(
            {
                "Id": cid or f"id-{name}",
                "Names": [f"/{name}"],
                "State": state,
                "SizeRw": 10,
                "Created": 0,
                "Labels": {c.LABEL_RUNNER: "1", c.LABEL_ROLE: "env", c.LABEL_ENV: env_id},
            }
        )

    def system_df(self) -> dict[str, Any]:
        volumes = []
        for name, size in self.volume_sizes.items():
            usage = {} if size is None else {"Size": size}
            volumes.append({"Name": name, "UsageData": usage})
        return {"Volumes": volumes, "Images": []}

    def containers(self, label: str) -> list[dict[str, Any]]:
        return list(self.items)

    def all_containers(self) -> list[dict[str, Any]]:
        return list(self.items)

    def volumes(self, label: str) -> list[dict[str, Any]]:
        return [
            {"Name": name, "Labels": {c.LABEL_ENV: name.removeprefix("vol-")}}
            for name in self.volume_sizes
        ]

    def stats(self, name: str) -> dict[str, Any]:
        self.calls.append(f"stats:{name}")
        value = self.stats_by_name[name]
        if isinstance(value, Exception):
            raise value
        return value

    def inspect(self, name: str) -> dict[str, Any] | None:
        self.calls.append(f"inspect:{name}")
        started = self.started.get(name)
        return {"State": {"StartedAt": started}} if started else None


class FakeWorkspace:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def post(self, path: str, body: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        assert path == "/report"
        self.bodies.append(body)
        return {"lost": [], "stray": []}


class FakeProxy:
    class _Stats:
        def drain(self) -> dict[str, Any]:
            return {}

    stats = _Stats()

    def envs(self) -> set[str]:
        return set()

    def close(self, env_id: str) -> None:
        pass


def _stats(cpu_ns: int | None, mem: int | None) -> dict[str, Any]:
    out: dict[str, Any] = {"cpu_stats": {"cpu_usage": {}}, "memory_stats": {}}
    if cpu_ns is not None:
        out["cpu_stats"]["cpu_usage"]["total_usage"] = cpu_ns
    if mem is not None:
        out["memory_stats"]["usage"] = mem
    return out


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    now = [100 * S]
    monkeypatch.setattr(time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(agent_module, "DISK_EMERGENCY", 1.01)  # 不让本机磁盘占用触发紧急停止
    return now


def _agent(docker: FakeDocker) -> tuple[Agent, FakeWorkspace]:
    workspace = FakeWorkspace()
    agent = Agent(
        config=Config(url="https://example.invalid", token="t", workers=1),  # noqa: S106
        docker=docker,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
        proxy=FakeProxy(),  # type: ignore[arg-type]
    )
    return agent, workspace


def _by_env(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["env_id"]: item for item in body["containers"]}


def test_agent_reports_cpu_after_two_samples(clock: list[int]) -> None:
    docker = FakeDocker()
    busy, idle = str(uuid.uuid4()), str(uuid.uuid4())
    docker.add(busy, "busy")
    docker.add(idle, "idle")
    docker.started = {"busy": "2026-11-09T09:00:00Z", "idle": "2026-11-09T09:00:00Z"}
    docker.stats_by_name = {"busy": _stats(10 * S, 640 << 20), "idle": _stats(3 * S, 1 << 20)}
    agent, workspace = _agent(docker)

    agent.report()
    first = _by_env(workspace.bodies[-1])
    assert first[busy]["cpu_used_cores"] is None  # 第一次采样：未知
    assert first[busy]["cpu_window_seconds"] is None
    assert first[busy]["mem_bytes"] == 640 << 20

    clock[0] += 60 * S
    docker.stats_by_name = {"busy": _stats(70 * S, 600 << 20), "idle": _stats(3 * S, 1 << 20)}
    agent.report()
    second = _by_env(workspace.bodies[-1])
    assert second[busy]["cpu_used_cores"] == pytest.approx(1.0)
    assert second[busy]["cpu_window_seconds"] == pytest.approx(60)
    assert second[idle]["cpu_used_cores"] == 0.0  # 真的没用 CPU：0，不是未知
    # 报告里的每一项都能通过服务端的校验
    RunnerReport.model_validate(workspace.bodies[-1])


def test_agent_stats_failure_only_affects_that_container(clock: list[int]) -> None:
    docker = FakeDocker()
    good, bad = str(uuid.uuid4()), str(uuid.uuid4())
    docker.add(good, "good")
    docker.add(bad, "bad")
    docker.started = {"good": "g", "bad": "g"}
    docker.stats_by_name = {"good": _stats(S, 5), "bad": DockerError(500, "boom")}
    agent, workspace = _agent(docker)
    agent.report()
    clock[0] += 60 * S
    docker.stats_by_name = {"good": _stats(31 * S, 5), "bad": OSError("timed out")}
    agent.report()
    body = _by_env(workspace.bodies[-1])
    assert set(body) == {good, bad}  # 读不到用量的容器照样上报（不会被判成丢失）
    assert body[bad]["state"] == "running"
    assert body[bad].get("mem_bytes") is None
    assert body[bad].get("cpu_used_cores") is None
    assert body[good]["cpu_used_cores"] == pytest.approx(0.5)


def test_agent_without_generation_reports_unknown_cpu_but_keeps_memory(clock: list[int]) -> None:
    docker = FakeDocker()
    env = str(uuid.uuid4())
    docker.add(env, "x")
    docker.stats_by_name = {"x": _stats(S, 123)}
    agent, workspace = _agent(docker)  # inspect 读不到 StartedAt
    agent.report()
    clock[0] += 60 * S
    agent.report()
    body = _by_env(workspace.bodies[-1])[env]
    assert body["cpu_used_cores"] is None
    assert body["mem_bytes"] == 123


def test_agent_restart_is_unknown_then_recovers(clock: list[int]) -> None:
    docker = FakeDocker()
    env = str(uuid.uuid4())
    docker.add(env, "x")
    docker.started = {"x": "start-1"}
    docker.stats_by_name = {"x": _stats(100 * S, 1)}
    agent, workspace = _agent(docker)
    agent.report()
    clock[0] += 60 * S
    docker.started = {"x": "start-2"}  # 重启过：计数器从头开始
    docker.stats_by_name = {"x": _stats(1 * S, 1)}
    agent.report()
    assert _by_env(workspace.bodies[-1])[env]["cpu_used_cores"] is None
    clock[0] += 60 * S
    docker.stats_by_name = {"x": _stats(31 * S, 1)}
    agent.report()
    assert _by_env(workspace.bodies[-1])[env]["cpu_used_cores"] == pytest.approx(0.5)


def test_agent_stopped_container_has_no_live_usage_and_baseline_is_pruned(
    clock: list[int],
) -> None:
    docker = FakeDocker()
    env = str(uuid.uuid4())
    docker.add(env, "x")
    docker.started = {"x": "s"}
    docker.stats_by_name = {"x": _stats(S, 1)}
    agent, workspace = _agent(docker)
    agent.report()
    assert len(agent.cpu) == 1
    docker.items[0]["State"] = "exited"
    docker.calls.clear()
    agent.report()
    body = _by_env(workspace.bodies[-1])[env]
    assert "mem_bytes" not in body and "cpu_used_cores" not in body
    assert docker.calls == []  # 不为停止的容器读 stats
    assert len(agent.cpu) == 0


def test_agent_volume_size_unknown_is_not_zero(clock: list[int]) -> None:
    docker = FakeDocker()
    a, b, z = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    docker.volume_sizes = {f"vol-{a}": -1, f"vol-{b}": None, f"vol-{z}": 0}
    agent, workspace = _agent(docker)
    agent.report()
    body = workspace.bodies[-1]
    sizes = {v["env_id"]: v["bytes"] for v in body["volumes"]}
    assert sizes == {a: None, b: None, z: 0}
    assert body["volumes_bytes"] == 0
