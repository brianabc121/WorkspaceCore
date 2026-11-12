"""Trusted independent resource probes and deployment placement normalization."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.agents import AgentDeploymentCreateIn


def body(**extra):
    return {
        "request_id": str(uuid4()),
        "name": "auto",
        "model": {"connection_id": str(uuid4()), "model_id": "model"},
        **extra,
    }


def test_auto_defaults_are_finite():
    request = AgentDeploymentCreateIn.model_validate(body())
    assert request.placement.mode == "auto"
    assert request.cpus == 1 and request.mem_limit_mb == 2048


def test_legacy_target_normalizes_to_specified():
    server_id = uuid4()
    request = AgentDeploymentCreateIn.model_validate(
        body(server_id=str(server_id), server_version=2)
    )
    assert request.placement.mode == "specified"
    assert request.placement.server_id == server_id
    assert request.cpus == 1 and request.mem_limit_mb == 2048


def test_conflicting_target_and_auto_rejected():
    with pytest.raises(ValidationError):
        AgentDeploymentCreateIn.model_validate(
            body(server_id=str(uuid4()), placement={"mode": "auto"})
        )


def test_equivalent_explicit_inputs_have_same_normalized_fingerprint():
    data = body(server_id=str(uuid4()), server_version=3)
    old = AgentDeploymentCreateIn.model_validate(data)
    new = AgentDeploymentCreateIn.model_validate(
        {
            **{k: v for k, v in data.items() if k != "server_id"},
            "placement": {"mode": "specified", "server_id": data["server_id"]},
        }
    )
    assert old.model_dump(mode="json") == new.model_dump(mode="json")


def test_independent_probe_rejects_incomplete_or_nonfinite_output():
    from app.domain.agent_server_resources import parse_resources

    for raw in ("{}", '{"cores":NaN}', "password=should-never-appear"):
        with pytest.raises(ValueError):
            parse_resources(raw, received_at=datetime.now(UTC))


def snapshot():
    return {
        "generation": str(uuid4()),
        "physical_host_id": "a" * 64,
        "sequence": 12345,
        "cores": 4,
        "cpu_percent": 10,
        "load1": 0.2,
        "mem_total": 16 << 30,
        "mem_available": 12 << 30,
        "root_total": 100 << 30,
        "root_free": 80 << 30,
        "data_total": 100 << 30,
        "data_free": 80 << 30,
        "runtime_ready": True,
        "network_ready": True,
        "managed_identities": [],
        "managed_usage": [],
    }


def test_probe_snapshot_freshness_and_order():
    from app.domain.agent_server_resources import is_fresh, newer_snapshot, parse_resources

    now = datetime.now(UTC)
    first = parse_resources(json.dumps(snapshot()), received_at=now)
    assert is_fresh(first, now)
    assert not is_fresh(first, now + timedelta(seconds=61))
    delayed = {**first, "sequence": first["sequence"] - 1}
    assert not newer_snapshot(first, delayed)
    assert newer_snapshot(first, {**first, "sequence": first["sequence"] + 1})


async def test_trusted_password_channel_and_closed_connection(monkeypatch):
    from types import SimpleNamespace

    from app.domain import agent_server_resources as resources

    class Input:
        def write(self, script):
            assert script == resources.PROBE_SCRIPT

        def write_eof(self):
            pass

    class Output:
        async def read(self, count):
            assert count == resources.MAX_OUTPUT + 1
            return json.dumps(snapshot())

    class Process:
        stdin = Input()
        stdout = Output()
        exit_status = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def wait_closed(self):
            pass

    class Connection:
        closed = False

        def create_process(self, command, **kwargs):
            assert command == "sh -s"
            return Process()

        def close(self):
            self.closed = True

    connection = Connection()
    target = SimpleNamespace(credential_revision=3)
    box = object()

    async def connect(actual_target, actual_box, *, expected_revision):
        assert actual_target is target and actual_box is box and expected_revision == 3
        return connection

    monkeypatch.setattr(resources, "connect_agent_server", connect)
    got = await resources.probe_resources(target, box)
    assert got["source"] == "trusted_ssh"
    assert connection.closed


def test_failed_probe_marks_previous_snapshot_ineligible():
    from app.domain.agent_server_resources import is_fresh, parse_resources

    now = datetime.now(UTC)
    previous = parse_resources(json.dumps(snapshot()), received_at=now)
    assert not is_fresh({**previous, "probe_error": "unavailable"}, now)


def test_docker_memory_credit_rounds_down_and_never_invents_unknown_usage():
    from app.domain.agent_server_resources import docker_memory_credit

    assert docker_memory_credit("1.50MiB / 2GiB") == 1.49 * (1 << 20)
    assert docker_memory_credit("0B / 2GiB") == 0
    for raw in ("? / 2GiB", "-1MiB / 2GiB", "NaNMiB / 2GiB", "1XB / 2GiB"):
        assert docker_memory_credit(raw) is None


def test_delayed_previous_boot_cannot_replace_new_boot():
    from app.domain.agent_server_resources import newer_snapshot

    now = datetime.now(UTC)
    old = {
        "generation": str(uuid4()),
        "sequence": 100,
        "sampled_at": (now - timedelta(seconds=3)).isoformat(),
        "received_at": now.isoformat(),
    }
    current = {
        "generation": str(uuid4()),
        "sequence": 1,
        "sampled_at": (now - timedelta(seconds=1)).isoformat(),
        "received_at": (now - timedelta(seconds=1)).isoformat(),
    }
    assert not newer_snapshot(current, old)
