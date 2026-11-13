"""Real database policy changes govern gateway leases, never caller destinations."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from tests.helpers import ClientFactory
from tests.test_runner_network import post, seed
from tests.workspace_support import Env, app_of


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    from tests.workspace_support import open_env

    async with open_env(make_client, migrated_database_url) as value:
        yield value


def test_service_updates_require_revision_and_reject_destination_injection() -> None:
    from app.schemas import runner_network as schema

    update = getattr(schema, "RunnerServiceUpdate", None)
    assert update is not None, "service edits need a revision-fenced schema"
    with pytest.raises(ValidationError):
        update(port=8080)
    with pytest.raises(ValidationError):
        update(expected_revision=1, port=8080, host="127.0.0.1")


async def running_service(env: Env) -> tuple[dict[str, Any], uuid.UUID]:
    from app.domain.runner_network import RunnerNetworkService
    from app.models import RunnerEnvironment, RunnerHost

    environment_id, host_id, _, _ = await seed(env)
    app = app_of(env.client)
    app.state.settings.runner_network_verified = True
    app.state.settings.runner_network_gateway_token = SecretStr("test-network-gateway-token")
    app.state.settings.runner_network_public_host = "runner.example.com"
    app.state.settings.runner_network_denied_cidrs = "10.42.0.0/16,192.0.2.0/24"
    async with app.state.sessionmaker() as db:
        host = await db.get(RunnerHost, host_id)
        assert host is not None
        host.node_name, host.status = "node-a", "online"
        host.runtime_metadata = {"network_validation_revision": 1}
        obj = await db.get(RunnerEnvironment, environment_id)
        assert obj is not None
        obj.state = "running"
        obj.runtime_metadata = {
            "generation": 3,
            "allocation_id": str(obj.id),
            "pod_uid": "target-pod-uid",
            "pod_ip": "10.42.0.5",
            "node": "node-a",
        }
        await db.commit()
    response = await post(
        env, f"/runner/environments/{environment_id}/services", {"name": "db", "port": 5432}
    )
    assert response.status_code == 201, response.text
    service: dict[str, Any] = response.json()
    async with app.state.sessionmaker() as db:
        assert await RunnerNetworkService(db, app.state.settings).acknowledge(
            host_id, uuid.UUID(service["id"]), 1, generation=3, success=True
        )
        await db.commit()
    return service, host_id


@pytest.mark.integration
async def test_service_edit_fences_old_ack_and_requires_owner_stepup(env: Env) -> None:
    from app.domain.runner_network import RunnerNetworkService
    from tests.helpers import OWNER

    service, host_id = await running_service(env)
    path = f"/api/v1/runner/environments/{service['environment_id']}/services/{service['id']}"
    body = {"expected_revision": 1, "name": "db", "port": 5433, "protocol": "TCP"}
    denied = await env.client.patch(path, json=body, headers=env.headers(write=True))
    assert denied.status_code == 403, denied.text
    body["password"] = OWNER["password"]
    changed = await env.client.patch(path, json=body, headers=env.headers(write=True))
    assert changed.status_code == 200, changed.text
    assert changed.json()["desired_revision"] == 2
    assert changed.json()["connect_host"] is None
    assert (
        await env.client.patch(path, json=body, headers=env.headers(write=True))
    ).status_code == 409
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        assert not await RunnerNetworkService(db, app.state.settings).acknowledge(
            host_id, uuid.UUID(service["id"]), 1, generation=3, success=True
        )


@pytest.mark.integration
async def test_gateway_auth_public_lease_and_revoke_confirmation(env: Env) -> None:
    from tests.helpers import OWNER

    service, _ = await running_service(env)
    prefix = "/api/v1/internal/runner-network/gateway"
    assert (await env.client.post(prefix + "/config", json={})).status_code == 401
    assert (
        await env.client.post(prefix + "/config", json={}, headers=env.headers())
    ).status_code == 401
    headers = {"Authorization": "Bearer test-network-gateway-token"}
    target = f"/runner/environments/{service['environment_id']}"
    published = await post(
        env,
        target + f"/services/{service['id']}/publish",
        {
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "source_cidrs": ["203.0.113.4/32"],
            "password": OWNER["password"],
        },
    )
    assert published.status_code == 201, published.text
    endpoint = published.json()
    waiting = await env.client.post(prefix + "/config", json={}, headers=headers)
    assert (
        next(b for b in waiting.json()["bindings"] if b["record_id"] == endpoint["id"])["target"]
        is None
    )
    await confirm_private_policy(env, service)
    snapshot = await env.client.post(prefix + "/config", json={}, headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    binding = next(b for b in snapshot.json()["bindings"] if b["record_id"] == endpoint["id"])
    assert 20000 <= binding["port"] <= 29999
    assert binding["target"]["pod_uid"] == "target-pod-uid"
    ack_body = {
        "record_id": endpoint["id"],
        "kind": "published",
        "revision": 1,
        "generation": 3,
        "success": True,
    }
    ack = await env.client.post(prefix + "/ack", json=ack_body, headers=headers)
    assert ack.status_code == 200 and ack.json()["accepted"], ack.text
    lease = {"record_id": endpoint["id"], "kind": "published", "peer_ip": "203.0.113.4"}
    allowed = await env.client.post(prefix + "/lease", json=lease, headers=headers)
    assert allowed.status_code == 200 and allowed.json()["allowed"], allowed.text
    assert 0 < allowed.json()["lease_seconds"] <= 60
    assert allowed.json()["target"]["port"] == 5432
    for peer in ("203.0.113.5", "10.42.0.5", "::1", "192.0.2.5"):
        result = await env.client.post(
            prefix + "/lease", json={**lease, "peer_ip": peer}, headers=headers
        )
        assert result.status_code == 200 and not result.json()["allowed"], result.text
        assert result.json().get("target") is None
    revoked = await post(
        env,
        target + f"/published-endpoints/{endpoint['id']}/revoke",
        {"password": OWNER["password"]},
    )
    assert revoked.status_code == 200 and not revoked.json()["revocation_confirmed"]
    assert not (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()[
        "allowed"
    ]
    assert not (await env.client.post(prefix + "/ack", json=ack_body, headers=headers)).json()[
        "accepted"
    ]
    ack_body["revision"] = 2
    assert (await env.client.post(prefix + "/ack", json=ack_body, headers=headers)).json()[
        "accepted"
    ]
    listing = await env.get("/api/v1" + target + "/network")
    assert listing["published_endpoints"][0]["revocation_confirmed"]


@pytest.mark.integration
async def test_second_gateway_child_keeps_live_sibling_and_service(env: Env) -> None:
    # Only the first gateway child changes the node's ingress policy. A later one
    # must not put the service back to pending: that would withdraw every live
    # sibling binding and cancel its established sessions until the node re-ACKs.
    from tests.helpers import OWNER

    service, _ = await running_service(env)
    prefix = "/api/v1/internal/runner-network/gateway"
    headers = {"Authorization": "Bearer test-network-gateway-token"}
    target = f"/runner/environments/{service['environment_id']}"
    body = {
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "source_cidrs": ["203.0.113.4/32"],
        "password": OWNER["password"],
    }
    first = await post(env, target + f"/services/{service['id']}/publish", body)
    assert first.status_code == 201, first.text
    await confirm_private_policy(env, service)
    ack = {
        "record_id": first.json()["id"],
        "kind": "published",
        "revision": 1,
        "generation": 3,
        "success": True,
    }
    acked = await env.client.post(prefix + "/ack", json=ack, headers=headers)
    assert acked.status_code == 200 and acked.json()["accepted"], acked.text
    before = (await env.get("/api/v1" + target + "/network"))["services"][0]
    assert before["state"] == "active"

    second = await post(
        env,
        target + f"/services/{service['id']}/publish",
        {**body, "source_cidrs": ["198.51.100.7/32"]},
    )
    assert second.status_code == 201, second.text
    after = (await env.get("/api/v1" + target + "/network"))["services"][0]
    assert after["state"] == "active"
    assert after["desired_revision"] == before["desired_revision"] == after["applied_revision"]
    snapshot = await env.client.post(prefix + "/config", json={}, headers=headers)
    bindings = {b["record_id"]: b for b in snapshot.json()["bindings"]}
    assert bindings[first.json()["id"]]["active"]
    assert bindings[second.json()["id"]]["target"] is not None
    lease = {"record_id": first.json()["id"], "kind": "published", "peer_ip": "203.0.113.4"}
    allowed = await env.client.post(prefix + "/lease", json=lease, headers=headers)
    assert allowed.status_code == 200 and allowed.json()["allowed"], allowed.text


@pytest.mark.integration
async def test_public_udp_not_supported_and_managed_sources_never_use_public_route(
    env: Env,
) -> None:
    from tests.helpers import OWNER

    service, _ = await running_service(env)
    base = f"/runner/environments/{service['environment_id']}"
    response = await post(env, base + "/services", {"name": "dns", "port": 5353, "protocol": "UDP"})
    assert response.status_code == 201
    published = await post(
        env,
        base + f"/services/{response.json()['id']}/publish",
        {
            "source_cidrs": ["203.0.113.4/32"],
            "password": OWNER["password"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert published.status_code == 409, published.text
    assert published.json()["error"]["code"] == "NETWORK_PROTOCOL_UNSUPPORTED"


@pytest.mark.integration
async def test_cross_instance_lease_needs_current_workload_grant_and_target_consent(
    env: Env,
) -> None:
    from sqlalchemy import select

    from app.models import AgentInstance, RunnerEnvironment
    from app.models.runner_capability import RunnerCapabilityGrant
    from tests.helpers import OWNER
    from tests.test_ai_tools_api import decide
    from tests.test_runner_capabilities import enabled_instance, request

    service, host_id = await running_service(env)
    source_id = await enabled_instance(env)
    pending = await request(env, source_id, "runner.cross_agent_communication")
    assert (
        await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    ).status_code == 200
    app = app_of(env.client)
    prefix = "/api/v1/internal/runner-network/gateway"
    headers = {"Authorization": "Bearer test-network-gateway-token"}
    ready = await env.client.post(prefix + "/ready", json={}, headers=headers)
    assert ready.status_code == 200 and ready.json()["accepted"], ready.text
    async with app.state.sessionmaker() as db:
        source = RunnerEnvironment(
            workspace_id=uuid.UUID(env.workspace_id),
            host_id=host_id,
            owner_instance_id=source_id,
            purpose="source",
            lifetime="session",
            state="running",
            runtime_backend="kubernetes",
            container_name=uuid.uuid4().hex,
            volume_name=uuid.uuid4().hex,
            network_name=uuid.uuid4().hex,
            runtime_metadata={
                "generation": 2,
                "pod_uid": "source-uid",
                "pod_ip": "10.42.1.8",
                "node": "node-a",
            },
        )
        db.add(source)
        await db.commit()
        source_body = {
            "workspace_id": env.workspace_id,
            "instance_id": str(source_id),
            "environment_id": str(source.id),
            "allocation_id": str(source.id),
            "generation": 2,
            "pod_uid": "source-uid",
        }
    base = f"/runner/environments/{service['environment_id']}"
    declared = await post(
        env,
        base + f"/services/{service['id']}/ingress",
        {
            "source_instance_id": str(source_id),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "password": OWNER["password"],
        },
    )
    assert declared.status_code == 201, declared.text
    rule = declared.json()
    await confirm_private_policy(env, service)
    lease = {
        "record_id": rule["id"],
        "kind": "ingress",
        "peer_ip": "10.42.1.8",
        "source": source_body,
    }
    assert not (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()[
        "allowed"
    ]

    ack = {
        "record_id": rule["id"],
        "kind": "ingress",
        "revision": 1,
        "generation": 3,
        "success": True,
    }
    assert (await env.client.post(prefix + "/ack", json=ack, headers=headers)).json()["accepted"]
    assert (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()["allowed"]
    for field, value in (
        ("workspace_id", str(uuid.uuid4())),
        ("pod_uid", "recycled-uid"),
        ("generation", 1),
    ):
        denied = await env.client.post(
            prefix + "/lease",
            json={**lease, "source": {**source_body, field: value}},
            headers=headers,
        )
        assert not denied.json()["allowed"] and denied.json().get("target") is None
    async with app.state.sessionmaker() as db:
        source_instance = await db.get(AgentInstance, source_id)
        assert source_instance is not None
        source_instance.runner_enabled = False
        await db.commit()
    assert not (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()[
        "allowed"
    ]
    async with app.state.sessionmaker() as db:
        source_instance = await db.get(AgentInstance, source_id)
        assert source_instance is not None
        source_instance.runner_enabled = True
        await db.commit()
    async with app.state.sessionmaker() as db:
        target_instance = await db.get(AgentInstance, uuid.UUID(service["owner_instance_id"]))
        assert target_instance is not None
        target_instance.runner_enabled = False
        await db.commit()
    assert not (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()[
        "allowed"
    ]
    async with app.state.sessionmaker() as db:
        target_instance = await db.get(AgentInstance, uuid.UUID(service["owner_instance_id"]))
        assert target_instance is not None
        target_instance.runner_enabled = True
        await db.commit()
    async with app.state.sessionmaker() as db:
        grant = await db.scalar(
            select(RunnerCapabilityGrant).where(
                RunnerCapabilityGrant.approval_id == pending.action_id
            )
        )
        assert grant is not None
        grant.state = "revoked"
        await db.commit()
    assert not (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()[
        "allowed"
    ]
    async with app.state.sessionmaker() as db:
        instance = await db.get(AgentInstance, source_id)
        assert instance is not None
        instance.status = "disabled"
        await db.commit()
    assert not (await env.client.post(prefix + "/lease", json=lease, headers=headers)).json()[
        "allowed"
    ]


@pytest.mark.integration
async def test_kubernetes_capacity_report_cannot_erase_uid_fenced_lifecycle(env: Env) -> None:
    from app.domain.runner_hosts import RunnerHosts
    from app.models import RunnerEnvironment, RunnerHost
    from app.schemas.runner import RunnerReport

    service, host_id = await running_service(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        host = await db.get(RunnerHost, host_id)
        assert host is not None
        manager = RunnerHosts(db, app.state.settings)
        ticket = await manager.begin_report(host)
        report = RunnerReport(
            sample_id=ticket.sample_id,
            physical_host_id="a" * 64,
            mem_bytes=8 * 1024**3,
            mem_free_bytes=6 * 1024**3,
            disk_bytes=100 * 1024**3,
            disk_free_bytes=80 * 1024**3,
        )
        result = await manager.report(host, report)
        obj = await db.get(RunnerEnvironment, uuid.UUID(service["environment_id"]))
        assert result.lost == [] and result.stray == []
        assert obj is not None and obj.state == "running"
        assert obj.runtime_metadata["pod_uid"] == "target-pod-uid"


@pytest.mark.integration
async def test_runtime_binding_only_accepts_current_host_allocation_generation(env: Env) -> None:
    from app.domain.runner import Runner, RunnerError
    from app.models import RunnerEnvironment, RunnerHost

    service, host_id = await running_service(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        host = await db.get(RunnerHost, host_id)
        obj = await db.get(RunnerEnvironment, uuid.UUID(service["environment_id"]))
        assert host is not None and obj is not None
        manager = Runner(db, app.state.settings)
        command = manager.enqueue(host, "start_env", {}, env=obj)
        await db.flush()
        command.payload = {
            **command.payload,
            "runtime_identity": {**command.payload["runtime_identity"], "generation": 2},
        }
        with pytest.raises(RunnerError):
            await manager.apply_result(
                command,
                "done",
                {
                    "ready": True,
                    "generation": 2,
                    "allocation_id": str(obj.id),
                    "pod_uid": "stale-uid",
                    "pod_ip": "10.42.0.9",
                    "node": "node-a",
                },
                None,
            )
        assert obj.runtime_metadata["pod_uid"] == "target-pod-uid"
        command.payload = {
            **command.payload,
            "runtime_identity": {**command.payload["runtime_identity"], "generation": 3},
        }
        await manager.apply_result(
            command,
            "done",
            {
                "ready": True,
                "generation": 3,
                "allocation_id": str(obj.id),
                "pod_uid": "replacement-uid",
                "deployment_uid": "deployment-uid",
                "pvc_uid": "pvc-uid",
                "pod_ip": "10.42.0.9",
                "node": "node-a",
            },
            None,
        )
        assert obj.runtime_metadata["pod_uid"] == "replacement-uid"
        assert obj.runtime_metadata["pod_ip"] == "10.42.0.9"
        assert obj.runtime_metadata["deployment_uid"] == "deployment-uid"


@pytest.mark.integration
async def test_periodic_binding_preserves_original_deployment_and_stopped_state(env: Env) -> None:
    from app.domain.runner_hosts import hash_token, new_token
    from app.models import RunnerEnvironment, RunnerHost

    service, host_id = await running_service(env)
    app = app_of(env.client)
    app.state.settings.agent_ingress_trusted_proxies = "127.0.0.1/32"
    token = new_token()
    async with app.state.sessionmaker() as db:
        host = await db.get(RunnerHost, host_id)
        obj = await db.get(RunnerEnvironment, uuid.UUID(service["environment_id"]))
        assert host is not None and obj is not None
        host.token_hash = hash_token(token)
        obj.runtime_metadata = {
            **obj.runtime_metadata,
            "deployment_uid": "deployment-uid",
            "pvc_uid": "pvc-uid",
        }
        await db.commit()
    body = {
        "workspace_id": env.workspace_id,
        "instance_id": service["owner_instance_id"],
        "environment_id": service["environment_id"],
        "allocation_id": service["environment_id"],
        "generation": 3,
        "deployment_uid": "deployment-uid",
        "pvc_uid": "pvc-uid",
        "pod_uid": "new-pod-uid",
        "pod_ip": "10.42.0.9",
        "node_name": "node-a",
    }
    headers = {"Authorization": "Bearer " + token, "X-MyWS-Ingress": "intranet"}
    path = "/runner-api/v1/network/binding"
    accepted = await env.client.post(path, headers=headers, json=body)
    assert accepted.status_code == 200 and accepted.json()["accepted"], accepted.text
    for key, value in (
        ("deployment_uid", "replacement-deployment"),
        ("pvc_uid", "other-pvc"),
        ("generation", 2),
        ("workspace_id", str(uuid.uuid4())),
    ):
        result = await env.client.post(path, headers=headers, json={**body, key: value})
        assert result.status_code == 200 and not result.json()["accepted"], result.text
    async with app.state.sessionmaker() as db:
        obj = await db.get(RunnerEnvironment, uuid.UUID(service["environment_id"]))
        assert obj is not None
        assert obj.runtime_metadata["pod_uid"] == "new-pod-uid"
        obj.state = "stopped"
        await db.commit()
    assert not (await env.client.post(path, headers=headers, json=body)).json()["accepted"]


async def confirm_private_policy(env: Env, service: dict[str, Any]) -> None:
    from app.domain.runner_network import RunnerNetworkService
    from app.models.runner_network import RunnerService

    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        row = await db.get(RunnerService, uuid.UUID(service["id"]))
        assert row is not None
        assert await RunnerNetworkService(db, app.state.settings).acknowledge(
            row.host_id,
            row.id,
            row.desired_revision,
            generation=row.desired_generation,
            success=True,
        )
        await db.commit()


@pytest.mark.integration
async def test_expiry_needs_withdrawal_ack_and_stopped_target_hides_address(env: Env) -> None:
    from app.domain.runner_network import RunnerNetworkService
    from app.models import RunnerEnvironment
    from app.models.runner_network import RunnerPublishedEndpoint

    service, _ = await running_service(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        obj = await db.get(RunnerEnvironment, uuid.UUID(service["environment_id"]))
        assert obj is not None
        row = RunnerPublishedEndpoint(
            workspace_id=obj.workspace_id,
            environment_id=obj.id,
            owner_instance_id=obj.owner_instance_id,
            host_id=obj.host_id,
            created_by=env.owner_id,
            service_id=uuid.UUID(service["id"]),
            desired_generation=3,
            applied_generation=3,
            desired_revision=1,
            applied_revision=1,
            state="active",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            source_cidrs=["203.0.113.4/32"],
        )
        db.add(row)
        await db.flush()
        output = RunnerNetworkService(db, app.state.settings).output(row, obj)
        assert output.state == "expired" and not output.revocation_confirmed
        row.expires_at = datetime.now(UTC) + timedelta(hours=1)
        obj.state = "stopped"
        output = RunnerNetworkService(db, app.state.settings).output(row, obj)
        assert (
            not output.accepting_new_connections
            and output.connect_host is None
            and output.connect_port is None
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "remaining"),
    [("approved_pending_apply", 3600), ("revoked", 3600), ("expired", -60)],
)
async def test_gateway_capacity_and_forbidden_sources_are_rejected_at_admission(
    env: Env, state: str, remaining: int
) -> None:
    from sqlalchemy import update

    from app.models.runner_network import RunnerPublishedEndpoint
    from tests.helpers import OWNER

    service, host_id = await running_service(env)
    app = app_of(env.client)
    base = f"/runner/environments/{service['environment_id']}/services/{service['id']}/publish"
    body = {
        "source_cidrs": ["192.0.2.4/32"],
        "password": OWNER["password"],
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    denied = await post(env, base, body)
    assert (
        denied.status_code == 409 and denied.json()["error"]["code"] == "NETWORK_SOURCE_FORBIDDEN"
    ), denied.text
    async with app.state.sessionmaker() as db:
        db.add_all(
            [
                RunnerPublishedEndpoint(
                    workspace_id=uuid.UUID(env.workspace_id),
                    environment_id=uuid.UUID(service["environment_id"]),
                    host_id=host_id,
                    owner_instance_id=uuid.UUID(service["owner_instance_id"]),
                    created_by=env.owner_id,
                    service_id=uuid.UUID(service["id"]),
                    desired_generation=3,
                    state=state,
                    source_cidrs=["203.0.113.4/32"],
                    expires_at=datetime.now(UTC) + timedelta(seconds=remaining),
                )
                for _ in range(100)
            ]
        )
        await db.commit()
    body["source_cidrs"] = ["203.0.113.4/32"]
    full = await post(env, base, body)
    assert full.status_code == 409 and full.json()["error"]["code"] == "NETWORK_GATEWAY_CAPACITY", (
        full.text
    )
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerPublishedEndpoint).values(
                state="revoked", applied_revision=1, applied_generation=3, apply_error=None
            )
        )
        await db.commit()
    released = await post(env, base, body)
    assert released.status_code == 201, released.text


@pytest.mark.integration
async def test_invalid_legacy_binding_does_not_poison_other_routes(env: Env) -> None:
    from app.models.runner_network import RunnerPublishedEndpoint

    service, host_id = await running_service(env)
    app = app_of(env.client)
    ids: dict[str, uuid.UUID] = {}
    async with app.state.sessionmaker() as db:
        for label, source in (("forbidden", "192.0.2.4/32"), ("allowed", "203.0.113.4/32")):
            row = RunnerPublishedEndpoint(
                workspace_id=uuid.UUID(env.workspace_id),
                environment_id=uuid.UUID(service["environment_id"]),
                host_id=host_id,
                owner_instance_id=uuid.UUID(service["owner_instance_id"]),
                created_by=env.owner_id,
                service_id=uuid.UUID(service["id"]),
                desired_generation=3,
                source_cidrs=[source],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            db.add(row)
            await db.flush()
            ids[label] = row.id
        await db.commit()
    snapshot = await env.client.post(
        "/api/v1/internal/runner-network/gateway/config",
        json={},
        headers={"Authorization": "Bearer test-network-gateway-token"},
    )
    assert snapshot.status_code == 200, snapshot.text
    states = {row["record_id"]: row["active"] for row in snapshot.json()["bindings"]}
    assert not states[str(ids["forbidden"])] and states[str(ids["allowed"])]


@pytest.mark.integration
async def test_withdrawn_live_binding_cannot_be_acknowledged_as_active(env: Env) -> None:
    from tests.helpers import OWNER

    service, _ = await running_service(env)
    base = f"/runner/environments/{service['environment_id']}"
    published = await post(
        env,
        base + f"/services/{service['id']}/publish",
        {
            "source_cidrs": ["203.0.113.4/32"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "password": OWNER["password"],
        },
    )
    assert published.status_code == 201, published.text
    await confirm_private_policy(env, service)
    result = await env.client.post(
        "/api/v1/internal/runner-network/gateway/ack",
        headers={"Authorization": "Bearer test-network-gateway-token"},
        json={
            "record_id": published.json()["id"],
            "kind": "published",
            "revision": 1,
            "generation": 3,
            "success": True,
            "active": False,
        },
    )
    assert result.status_code == 200 and result.json()["accepted"], result.text
    network = await env.get("/api/v1" + base + "/network")
    endpoint = network["published_endpoints"][0]
    assert endpoint["state"] == "approved_pending_apply"
    assert not endpoint["accepting_new_connections"] and endpoint["connect_host"] is None
