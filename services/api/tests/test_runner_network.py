"""Fail-closed network declarations and scoped DB/API lifecycle."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tests.helpers import ClientFactory
from tests.workspace_support import Env


def test_service_rejects_client_identity_and_raw_destination() -> None:
    from app.schemas.runner_network import RunnerServiceCreate

    for extra in ({"host": "127.0.0.1"}, {"owner_instance_id": str(uuid.uuid4())}):
        with pytest.raises(ValidationError):
            RunnerServiceCreate(name="db", port=5432, **extra)
    assert RunnerServiceCreate(name="db", port=5432).protocol == "TCP"
    for name, port in (("../x", 80), ("db", 0), ("db", 65536)):
        with pytest.raises(ValidationError):
            RunnerServiceCreate(name=name, port=port)


def test_public_sources_require_finite_explicit_ipv4_scope() -> None:
    from app.schemas.runner_network import RunnerPublishInput

    future = datetime.now(UTC) + timedelta(hours=1)
    for source in ([], ["0.0.0.0/0"], ["::/0"], ["127.0.0.1/32"], ["10.0.0.0/8"]):
        with pytest.raises(ValidationError):
            RunnerPublishInput(source_cidrs=source, expires_at=future)
    body = RunnerPublishInput(source_cidrs=["203.0.113.4/32"], expires_at=future)
    assert body.source_cidrs == ["203.0.113.4/32"]
    with pytest.raises(ValidationError):
        RunnerPublishInput(source_cidrs=["203.0.113.4/32"], expires_at=None)


def test_revision_ack_rejects_stale_host_generation_and_unsupported_gateway() -> None:
    from app.domain.runner_network import ack_allowed

    host, other = uuid.uuid4(), uuid.uuid4()
    assert ack_allowed(host, host, 2, 2, 3, 3, high_risk=False)
    assert not ack_allowed(other, host, 2, 2, 3, 3, high_risk=False)
    assert not ack_allowed(host, host, 1, 2, 3, 3, high_risk=False)
    assert not ack_allowed(host, host, 2, 2, 2, 3, high_risk=False)
    assert not ack_allowed(host, host, 2, 2, 3, 3, high_risk=True)


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    from tests.workspace_support import open_env

    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def seed(env: Env) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    from app.models import RunnerEnvironment, RunnerHost
    from tests.test_runner_capabilities import enabled_instance
    from tests.workspace_support import app_of

    owner = await enabled_instance(env)
    other = await enabled_instance(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        host = RunnerHost(
            workspace_id=uuid.UUID(env.workspace_id),
            name="net",
            backend="kubernetes",
            token_hash=uuid.uuid4().hex,
            token_prefix="mwr_test",  # noqa: S106 -- public test prefix
        )
        db.add(host)
        await db.flush()
        obj = RunnerEnvironment(
            workspace_id=host.workspace_id,
            host_id=host.id,
            owner_instance_id=owner,
            purpose="network",
            lifetime="session",
            container_name=uuid.uuid4().hex,
            volume_name=uuid.uuid4().hex,
            network_name=uuid.uuid4().hex,
            runtime_backend="kubernetes",
            runtime_metadata={"generation": 3},
        )
        db.add(obj)
        await db.commit()
        return obj.id, host.id, owner, other


async def post(env: Env, path: str, body: dict[str, Any]) -> httpx.Response:
    return await env.client.post("/api/v1" + path, json=body, headers=env.headers(write=True))


@pytest.mark.integration
async def test_private_declaration_identity_and_trusted_ack_lifecycle(env: Env) -> None:
    from app.domain.runner_network import RunnerNetworkService
    from tests.workspace_support import app_of

    env_id, host_id, owner, _ = await seed(env)
    path = f"/runner/environments/{env_id}"
    result = await post(env, path + "/services", {"name": "db", "port": 5432})
    assert result.status_code == 201, result.text
    service = result.json()
    assert service["state"] == "approved_pending_apply"
    assert service["owner_instance_id"] == str(owner)
    assert service["connect_host"] is None
    network = await env.get("/api/v1" + path + "/network")
    assert network["runtime_status"] == "pending_apply"
    assert network["published_endpoints"] == []
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        manager = RunnerNetworkService(db, app.state.settings)
        desired = await manager.desired_policy(host_id)
        assert desired["services"][0]["environment_id"] == str(env_id)
        assert not await manager.acknowledge(
            host_id, uuid.UUID(service["id"]), 1, generation=2, success=True
        )
        assert await manager.acknowledge(
            host_id, uuid.UUID(service["id"]), 1, generation=3, success=True
        )
        await db.commit()
    network = await env.get("/api/v1" + path + "/network")
    assert network["services"][0]["state"] == "active"
    assert network["services"][0]["connect_host"] == service["desired_dns"]
    assert (await post(env, path + "/services", {"name": "db", "port": 80})).status_code == 409
    assert (
        await post(
            env,
            path + "/services",
            {"name": "bad", "port": 80, "environment_id": str(uuid.uuid4())},
        )
    ).status_code == 422


@pytest.mark.integration
async def test_owner_consent_source_grant_and_public_pending_revoke(env: Env) -> None:
    from app.domain.runner_network import RunnerNetworkService
    from app.models.runner_network import RunnerPublishedEndpoint
    from tests.helpers import OWNER
    from tests.workspace_support import app_of

    env_id, host_id, _, source = await seed(env)
    path = f"/runner/environments/{env_id}"
    created = await post(env, path + "/services", {"name": "web", "port": 8080})
    sid = created.json()["id"]
    base = path + f"/services/{sid}"
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    body = {"source_instance_id": str(source), "expires_at": future, "password": OWNER["password"]}
    denied = await post(env, base + "/ingress", body)
    assert (
        denied.status_code == 403 and denied.json()["error"]["code"] == "SOURCE_CAPABILITY_REQUIRED"
    ), denied.text
    public = {"source_cidrs": ["203.0.113.4/32"], "expires_at": future}
    assert (await post(env, base + "/publish", public)).status_code == 403
    public["password"] = OWNER["password"]
    response = await post(env, base + "/publish", public)
    assert response.status_code == 201, response.text
    endpoint = response.json()
    assert endpoint["state"] == "approved_pending_apply"
    assert endpoint["apply_error"] == "NETWORK_ENFORCEMENT_UNAVAILABLE"
    assert endpoint["connect_host"] is None
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        manager = RunnerNetworkService(db, app.state.settings)
        assert not await manager.acknowledge(
            host_id, uuid.UUID(endpoint["id"]), 1, generation=3, success=True
        )
        row = await db.get(RunnerPublishedEndpoint, uuid.UUID(endpoint["id"]))
        assert row.state == "approved_pending_apply"
    revoked = await post(
        env, path + f"/published-endpoints/{endpoint['id']}/revoke", {"password": OWNER["password"]}
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["state"] == "revoked" and revoked.json()["desired_revision"] == 2
    assert revoked.json()["applied_revision"] == 0
    assert not revoked.json()["accepting_new_connections"]


@pytest.mark.integration
async def test_network_workspace_role_csrf_and_missing_runtime(env: Env) -> None:
    from tests.helpers import csrf

    env_id, _, _, _ = await seed(env)
    path = f"/api/v1/runner/environments/{env_id}"
    response = await env.client.post(
        path + "/services", json={"name": "x", "port": 1}, headers=env.headers()
    )
    assert response.status_code == 403
    async with env.member("network-viewer@example.com", "viewer") as viewer:
        response = await viewer.get(path + "/network", headers=env.headers())
        assert response.status_code == 403
    async with env.member("network-admin@example.com", "admin") as admin:
        assert (await admin.get(path + "/network", headers=env.headers())).status_code == 200
        response = await admin.post(
            path + "/services",
            json={"name": "x", "port": 1},
            headers={"X-Workspace-Id": env.workspace_id, **csrf(admin)},
        )
        assert response.status_code == 403
    result = await env.get(path + "/network")
    assert result["runtime_status"] == "unknown"
    assert result["runtime_error"] == "NETWORK_RUNTIME_UNCONFIRMED"
    response = await env.client.get(
        path + "/network", headers={"X-Workspace-Id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_network_host_transport_is_authenticated_and_requires_dataplane_validation(
    env: Env,
) -> None:
    from app.domain.runner_hosts import hash_token, new_token
    from app.models import RunnerHost
    from tests.workspace_support import app_of

    env_id, host_id, _, _ = await seed(env)
    app = app_of(env.client)
    app.state.settings.agent_ingress_trusted_proxies = "127.0.0.1/32"
    token = new_token()
    async with app.state.sessionmaker() as db:
        host = await db.get(RunnerHost, host_id)
        host.token_hash = hash_token(token)
        host.runtime_metadata = {}
        await db.commit()
    path = "/runner-api/v1/network"
    denied = await env.client.post(path + "/desired", json={})
    assert denied.status_code == 401
    headers = {"Authorization": "Bearer " + token, "X-MyWS-Ingress": "intranet"}
    declared = await post(
        env, f"/runner/environments/{env_id}/services", {"name": "db", "port": 5432}
    )
    assert declared.status_code == 201, declared.text
    service = declared.json()
    policy = await env.client.post(path + "/desired", headers=headers, json={})
    assert policy.status_code == 200, policy.text
    assert [s["id"] for s in policy.json()["services"]] == [service["id"]]
    assert "token" not in policy.text.lower()
    ack = await env.client.post(
        path + "/ack",
        headers=headers,
        json={
            "record_id": service["id"],
            "revision": service["desired_revision"],
            "generation": 3,
            "success": True,
            "receipt_uid": "api-object-uid",
            "resource_version": "42",
        },
    )
    assert ack.status_code == 409 and ack.json()["error"]["code"] == "NETWORK_VALIDATION_REQUIRED"
    state = await env.get(f"/api/v1/runner/environments/{env_id}/network")
    assert state["services"][0]["state"] == "approved_pending_apply"
