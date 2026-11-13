"""Historical commitments must come from finite limits of the exact managed container."""

import json
from typing import TypedDict

import pytest


class RuntimeInspect(TypedDict):
    project: str
    directory: str
    service: str
    id: str
    memory: int
    nano_cpus: int
    cpu_quota: int
    cpu_period: int


def test_reconciliation_accepts_only_matching_finite_container() -> None:
    from app.domain.agent_runtime_resources import parse_runtime_limits

    row: RuntimeInspect = {
        "project": "myworkspace-openclaw-123456789abc",
        "directory": "/home/deploy/app",
        "service": "openclaw",
        "id": "a" * 64,
        "memory": 2147483648,
        "nano_cpus": 1500000000,
        "cpu_quota": 0,
        "cpu_period": 100000,
    }
    value = parse_runtime_limits(json.dumps(row), row["project"], row["directory"])
    assert value["cpus"] == 1.5 and value["memory_mb"] == 2048
    for change in (
        {"project": "foreign"},
        {"directory": "/other"},
        {"memory": 0},
        {"nano_cpus": 0},
        {"id": "untrusted"},
        {"service": "net"},
    ):
        with pytest.raises(ValueError):
            parse_runtime_limits(json.dumps(row | change), row["project"], row["directory"])


def test_reconciliation_understands_finite_cpu_quota_and_rounds_memory_up() -> None:
    from app.domain.agent_runtime_resources import parse_runtime_limits

    row: RuntimeInspect = {
        "project": "myworkspace-openclaw-123456789abc",
        "directory": "/app",
        "service": "openclaw",
        "id": "b" * 64,
        "memory": 2147483649,
        "nano_cpus": 0,
        "cpu_quota": 50000,
        "cpu_period": 100000,
    }
    value = parse_runtime_limits(json.dumps(row), row["project"], row["directory"])
    assert value["cpus"] == 0.5 and value["memory_mb"] == 2049
