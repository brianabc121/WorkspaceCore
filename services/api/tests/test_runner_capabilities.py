"""Runtime capability security and quota contracts, with real PostgreSQL / Redis."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import AgentInstance, AiToolExecution
from app.schemas.ai_tools import PendingApproval
from tests.helpers import OWNER, ClientFactory
from tests.test_ai_tools_api import decide, new_instance
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(
        make_client, migrated_database_url, agent_ingress_trusted_proxies="127.0.0.1/32"
    ) as value:
        yield value


async def enabled_instance(env: Env) -> uuid.UUID:
    instance = await new_instance(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, uuid.UUID(instance))
        obj.runner_enabled = True
        await db.commit()
    return uuid.UUID(instance)


async def request(
    env: Env,
    instance_id: uuid.UUID,
    capability: str = "runner.unlimited_container_count",
    *,
    expires_at: datetime | None = None,
) -> PendingApproval:
    from app.domain.runner_capabilities import RunnerCapabilityService

    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        instance = await db.get(AgentInstance, instance_id)
        pending = await RunnerCapabilityService(db, app.state.settings, app.state.redis).request(
            instance, capability=capability, reason="需要更多独立环境", expires_at=expires_at
        )
        await db.commit()
        return pending


async def test_capability_schema_is_registered() -> None:
    from app.schemas.ai_tools import TOOLS

    assert "runner_request_capability" in TOOLS
    assert "runner_hosts" in TOOLS
    assert TOOLS["runner_request_capability"].risk == "critical"
    assert TOOLS["runner_request_capability"].min_role.value == "member"


async def test_concurrent_requests_deduplicate_per_instance_capability(env: Env) -> None:
    instance = await enabled_instance(env)
    first, second = await asyncio.gather(request(env, instance), request(env, instance))
    assert first.action_id == second.action_id
    assert first.deduplicated != second.deduplicated
    # A caller rollback after the committed result must not delete the approval.
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        row = await db.get(AiToolExecution, first.action_id)
        assert row.status == "pending_approval" and row.risk_level == "critical"


async def test_owner_approval_persists_independent_grant_and_requires_apply(env: Env) -> None:
    from app.domain.runner_capabilities import RunnerCapabilityService, has_capability
    from app.models.runner_capability import RunnerCapabilityGrant

    instance = await enabled_instance(env)
    pending = await request(env, instance, "runner.cross_agent_communication")
    missing = await decide(env, pending.action_id, "approve", 1)
    assert missing.status_code == 403
    approved = await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "succeeded", approved.text
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        grant = await db.scalar(
            select(RunnerCapabilityGrant).where(
                RunnerCapabilityGrant.approval_id == pending.action_id
            )
        )
        assert grant.approved_by == env.owner_id
        assert grant.state == "approved_pending_apply"
        assert not await has_capability(db, grant.workspace_id, instance, grant.capability)
        service = RunnerCapabilityService(db, app.state.settings, app.state.redis)
        await service.mark_applied(grant.id, grant.desired_revision)
        await db.commit()
        assert await has_capability(db, grant.workspace_id, instance, grant.capability)
        execution = await db.get(AiToolExecution, pending.action_id)
        assert execution.arguments is None
        assert grant.reason == "需要更多独立环境"
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
        assert not await has_capability(db, grant.workspace_id, instance, grant.capability)


async def test_disabled_runner_cannot_request(env: Env) -> None:
    from app.domain.ai_tools import ToolCallError

    instance = uuid.UUID(await new_instance(env))
    with pytest.raises(ToolCallError) as error:
        await request(env, instance)
    assert error.value.code == "FORBIDDEN_CAPABILITY"


async def test_pending_state_identifies_each_capability(env: Env) -> None:
    from app.domain.ai_tools import AiToolService
    from app.domain.runner_capabilities import RunnerCapabilityService

    instance = await enabled_instance(env)
    count = await request(env, instance)
    network = await request(env, instance, "runner.cross_agent_communication")
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        who = await AiToolService(db, app.state.settings, app.state.connectors).requester(
            await db.get(AgentInstance, instance)
        )
        output = await RunnerCapabilityService(db, app.state.settings).list(who.access(), instance)
        assert {p.action_id: p.capability for p in output.pending_requests} == {
            count.action_id: "runner.unlimited_container_count",
            network.action_id: "runner.cross_agent_communication",
        }


async def add_held(env: Env, instance_id: uuid.UUID, states: Sequence[str]) -> None:
    from app.models import RunnerEnvironment, RunnerHost

    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        host = RunnerHost(
            workspace_id=uuid.UUID(env.workspace_id),
            name="one",
            token_hash=uuid.uuid4().hex,
            token_prefix="mwr_test",  # noqa: S106 -- test-only public token prefix
        )
        db.add(host)
        await db.flush()
        for state in states:
            env_id = uuid.uuid4()
            db.add(
                RunnerEnvironment(
                    id=env_id,
                    workspace_id=host.workspace_id,
                    host_id=host.id,
                    owner_instance_id=instance_id,
                    purpose="quota test",
                    lifetime="session",
                    state=state,
                    container_name=str(env_id),
                    volume_name=str(env_id),
                    network_name=str(env_id),
                )
            )
        await db.commit()


async def test_held_quota_counts_stopped_pending_delete_failed_across_hosts(env: Env) -> None:
    from app.domain.runner_capabilities import ContainerApprovalRequired, RunnerQuotaService

    instance = await enabled_instance(env)
    await add_held(env, instance, ["creating", "running", "stopped", "pending_delete"])
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, instance)
        await RunnerQuotaService(db, app.state.settings).check_count(obj)
    await add_held(env, instance, ["failed", "deleted", "archived"])
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, instance)
        with pytest.raises(ContainerApprovalRequired) as needed:
            await RunnerQuotaService(db, app.state.settings).check_count(obj)
        await db.commit()
        approval_id = needed.value.approval_id
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, instance)
        with pytest.raises(ContainerApprovalRequired) as again:
            await RunnerQuotaService(db, app.state.settings).check_count(obj)
        assert again.value.approval_id == approval_id
        await db.commit()


async def test_sixth_tool_branch_commits_approval(env: Env) -> None:
    from app.domain import runner_tools
    from app.domain.ai_tools import AiToolService
    from app.schemas.ai_tools import TOOLS, ContainerApprovalOutput

    instance = await enabled_instance(env)
    await add_held(env, instance, ["stopped"] * 5)
    app = app_of(env.client)

    async with app.state.sessionmaker() as db:
        service = AiToolService(db, app.state.settings, app.state.connectors)
        who = await service.requester(await db.get(AgentInstance, instance))
        output = await runner_tools.run(
            service,
            who,
            TOOLS["env_create"],
            {"purpose": "sixth", "lifetime": "session", "spec": {"image": "alpine:3.20"}},
            request_id=None,
        )
        assert isinstance(output, ContainerApprovalOutput)
        assert output.code == "APPROVAL_REQUIRED"
        await db.rollback()
    async with app.state.sessionmaker() as db:
        row = await db.get(AiToolExecution, output.approval_id)
        assert row.status == "pending_approval"


async def test_revoke_retains_grant_and_stale_apply_cannot_reactivate(env: Env) -> None:
    from app.domain.auth import AuthContext
    from app.domain.policy import WorkspaceAccess
    from app.domain.runner_capabilities import RunnerCapabilityService, has_capability
    from app.models import AuthSession, User, Workspace
    from app.models.runner_capability import RunnerCapabilityGrant
    from app.schemas.runner_capabilities import RunnerCapabilityRevokeInput
    from app.schemas.workspaces import Role

    instance = await enabled_instance(env)
    pending = await request(env, instance, "runner.cross_agent_communication")
    response = await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert response.json()["status"] == "succeeded", response.text
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        grant = await db.scalar(
            select(RunnerCapabilityGrant).where(
                RunnerCapabilityGrant.approval_id == pending.action_id
            )
        )
        service = RunnerCapabilityService(db, app.state.settings, app.state.redis)
        assert grant.scope["target_inbound_required"] is True
        await service.mark_applied(grant.id, 1)
        await db.commit()
        assert await has_capability(db, grant.workspace_id, instance, grant.capability)
        user = await db.get(User, env.owner_id)
        access = WorkspaceAccess(
            await db.get(Workspace, grant.workspace_id), Role.owner, env.owner_id
        )
        context = AuthContext(
            user=user,
            session=AuthSession(user_id=env.owner_id),
            token="",
            client_ip="127.0.0.1",
        )
        output = await service.revoke(
            context,
            access,
            instance,
            grant.id,
            RunnerCapabilityRevokeInput(reason="撤销", password=OWNER["password"]),
        )
        assert output.state == "revoked" and output.applied_revision < output.desired_revision
        assert not await has_capability(db, grant.workspace_id, instance, grant.capability)
        assert await service.mark_applied(grant.id, 1) is False
        await db.commit()
        assert await db.get(RunnerCapabilityGrant, grant.id) is not None


async def test_admin_cannot_decide_capability_and_member_registrant_is_not_grantor(
    env: Env,
) -> None:
    from tests.helpers import csrf

    instance = await enabled_instance(env)
    async with env.member("registrant@example.com", "member") as registrant:
        response = await registrant.get("/api/v1/me")
        registrant_id = uuid.UUID(response.json()["user"]["id"])
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, instance)
        obj.acting_user_id = registrant_id
        await db.commit()
    pending = await request(env, instance)
    async with env.member("admin@example.com", "admin") as admin:
        headers = {"X-Workspace-Id": env.workspace_id, **csrf(admin), "If-Match": '"1"'}
        response = await admin.post(
            f"/api/v1/ai/tool-executions/{pending.action_id}/approve",
            json={"password": "member password 123456"},
            headers=headers,
        )
        assert response.status_code == 403, response.text
    approved = await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert approved.json()["status"] == "succeeded", approved.text
    from app.models.runner_capability import RunnerCapabilityGrant

    async with app.state.sessionmaker() as db:
        grant = await db.scalar(
            select(RunnerCapabilityGrant).where(
                RunnerCapabilityGrant.approval_id == pending.action_id
            )
        )
        assert grant.requested_by == registrant_id
        assert grant.approved_by == env.owner_id


async def test_legacy_count_policy_is_effective_after_owner_approval(env: Env) -> None:
    from app.domain.runner_capabilities import has_capability

    instance = await enabled_instance(env)
    pending = await request(env, instance)
    approved = await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert approved.json()["status"] == "succeeded"
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        assert await has_capability(
            db, uuid.UUID(env.workspace_id), instance, "runner.unlimited_container_count"
        )


async def test_admin_cannot_reject_dedicated_owner_capability(env: Env) -> None:
    from tests.helpers import csrf

    instance = await enabled_instance(env)
    pending = await request(env, instance)
    async with env.member("rejector@example.com", "admin") as admin:
        response = await admin.post(
            f"/api/v1/ai/tool-executions/{pending.action_id}/reject",
            json={"reason": "admin cannot decide owner capabilities"},
            headers={"X-Workspace-Id": env.workspace_id, **csrf(admin), "If-Match": '"1"'},
        )
        assert response.status_code == 403, response.text


async def test_mcp_hosts_scoped_safe_and_capability_request_still_requires_runner(env: Env) -> None:
    from app.models import RunnerHost, Workspace
    from tests.helpers import tool_error
    from tests.test_mcp_api import call, issue_token

    instance = await enabled_instance(env)
    await add_held(env, instance, [])
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        other = Workspace(name="other", owner_id=env.owner_id)
        db.add(other)
        await db.flush()
        host = RunnerHost(
            workspace_id=other.id,
            name="hidden",
            token_hash=uuid.uuid4().hex,
            token_prefix="mwr_hidden",  # noqa: S106 -- public test prefix, not a credential
        )
        db.add(host)
        await db.commit()
    token = await issue_token(env, str(instance))
    hosts = await call(env, token, "runner_hosts", {})
    assert hosts.get("isError") is not True, hosts
    data = hosts["structuredContent"]
    assert [h["untrusted"]["name"] for h in data["items"]] == ["one"]
    assert all("name" not in h and "node_name" not in h for h in data["items"])
    assert "token" not in str(data).lower() and "runtime_metadata" not in str(data)
    pending = await call(
        env,
        token,
        "runner_request_capability",
        {"capability": "runner.cross_agent_communication", "reason": "MCP"},
    )
    assert pending["structuredContent"]["status"] == "pending_approval"
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, instance)
        obj.runner_enabled = False
        await db.commit()
    refused = await call(
        env,
        token,
        "runner_request_capability",
        {"capability": "runner.unlimited_container_count", "reason": "denied"},
    )
    assert tool_error(refused)["code"] == "FORBIDDEN_CAPABILITY"


async def test_explicit_lower_container_limit_remains_effective(env: Env) -> None:
    from app.domain.runner import RunnerError
    from app.domain.runner_capabilities import RunnerQuotaService

    instance = await enabled_instance(env)
    await add_held(env, instance, ["stopped"])
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        obj = await db.get(AgentInstance, instance)
        obj.runner_limits = {"envs": 1}
        await db.commit()
        with pytest.raises(RunnerError) as error:
            await RunnerQuotaService(db, app.state.settings).check_count(obj)
        assert error.value.code == "QUOTA_EXCEEDED"


async def test_management_request_disabled_runner_returns_forbidden(env: Env) -> None:
    instance = await new_instance(env)
    response = await env.client.post(
        f"/api/v1/agents/instances/{instance}/runner/capabilities/request",
        json={"capability": "runner.unlimited_container_count", "reason": "disabled"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_CAPABILITY"
