"""A physical machine cannot contribute independent capacity to two registrations."""

import asyncio
import uuid
from typing import Literal

import pytest
from sqlalchemy import func, select

from app.models import AgentServer, RunnerHost
from tests.helpers import ClientFactory
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


async def seed(env: Env) -> tuple[AgentServer, RunnerHost]:
    async with app_of(env.client).state.sessionmaker() as db:
        agent = AgentServer(workspace_id=uuid.UUID(env.workspace_id), name="agent", kind="ssh")
        runner = RunnerHost(
            workspace_id=agent.workspace_id,
            name="runner",
            status="online",
            token_hash=uuid.uuid4().hex,
            token_prefix=uuid.uuid4().hex[:8],
            cpus=8,
            mem_bytes=32 << 30,
            disk_bytes=100 << 30,
            runtimes=["runc"],
        )
        db.add_all([agent, runner])
        await db.commit()
        return agent, runner


@pytest.mark.parametrize("other_workspace", [False, True])
async def test_concurrent_cross_pool_claim_has_one_winner_and_stable_identity(
    other_workspace: bool,
    make_client: ClientFactory,
    migrated_database_url: str,
) -> None:
    from app.domain.physical_hosts import claim_physical_host
    from app.models.physical_host import PhysicalHostRegistration

    async with open_env(make_client, migrated_database_url) as env:
        agent, runner = await seed(env)
        sessions = app_of(env.client).state.sessionmaker
        if other_workspace:
            from app.models import Workspace

            async with sessions() as db:
                other = Workspace(name="other", owner_id=env.owner_id)
                db.add(other)
                await db.flush()
                runner = await db.get(RunnerHost, runner.id)
                runner.workspace_id = other.id
                await db.commit()

        async def claim(
            role: Literal["agent", "runner"], server_id: uuid.UUID, identity: str = "a" * 64
        ) -> bool:
            async with sessions() as db:
                accepted = await claim_physical_host(
                    db,
                    identity,
                    workspace_id=agent.workspace_id if role == "agent" else runner.workspace_id,
                    role=role,
                    server_id=server_id,
                )
                await db.commit()
                return accepted

        results = await asyncio.gather(claim("agent", agent.id), claim("runner", runner.id))
        assert sorted(results) == [False, True]
        winner: tuple[Literal["agent", "runner"], uuid.UUID] = (
            ("agent", agent.id) if results[0] else ("runner", runner.id)
        )
        assert await claim(*winner)
        assert not await claim(*winner, identity="b" * 64), "cannot silently move a registration"
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(PhysicalHostRegistration)) == 1


async def test_claim_rejects_a_server_from_another_workspace(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    from app.domain.physical_hosts import claim_physical_host
    from app.models.physical_host import PhysicalHostRegistration

    async with open_env(make_client, migrated_database_url) as env:
        agent, _runner = await seed(env)
        async with app_of(env.client).state.sessionmaker() as db:
            assert not await claim_physical_host(
                db,
                "c" * 64,
                workspace_id=uuid.uuid4(),
                role="agent",
                server_id=agent.id,
            )
            assert await db.scalar(select(func.count()).select_from(PhysicalHostRegistration)) == 0


async def test_signed_report_needs_unique_physical_proof_for_placement(
    make_client: ClientFactory,
    migrated_database_url: str,
) -> None:
    from app.domain.physical_hosts import claim_physical_host
    from app.domain.runner_hosts import RunnerHosts
    from app.domain.runner_placement import candidate_for
    from app.schemas.runner import RunnerReport

    async with open_env(make_client, migrated_database_url) as env:
        agent, runner = await seed(env)
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            assert await claim_physical_host(
                db,
                "d" * 64,
                workspace_id=agent.workspace_id,
                role="agent",
                server_id=agent.id,
            )
            await db.commit()
            node = await db.get(RunnerHost, runner.id)
            service = RunnerHosts(db, app.state.settings)
            for physical_id, expected in [
                (None, "PHYSICAL_IDENTITY_REQUIRED"),
                ("d" * 64, "PHYSICAL_HOST_CONFLICT"),
                ("e" * 64, None),
            ]:
                sample = await service.begin_report(node)
                await db.commit()
                await service.report(
                    node,
                    RunnerReport(
                        sample_id=sample.sample_id,
                        physical_host_id=physical_id,
                        mem_bytes=32 << 30,
                        mem_free_bytes=24 << 30,
                        disk_bytes=100 << 30,
                        disk_free_bytes=80 << 30,
                        load=0.1,
                    ),
                )
                await db.commit()
                assert node.report.get("placement_error") == expected
                assert (candidate_for(node, []).observed_at is None) == (expected is not None)


async def test_unverified_physical_report_never_orders_other_containers_stopped(
    make_client: ClientFactory,
    migrated_database_url: str,
) -> None:
    from app.domain.physical_hosts import claim_physical_host
    from app.domain.runner_hosts import RunnerHosts
    from app.schemas.runner import RunnerReport

    async with open_env(make_client, migrated_database_url) as env:
        agent, runner = await seed(env)
        app = app_of(env.client)
        async with app.state.sessionmaker() as db:
            assert await claim_physical_host(
                db,
                "d" * 64,
                workspace_id=agent.workspace_id,
                role="agent",
                server_id=agent.id,
            )
            node = await db.get(RunnerHost, runner.id)
            service = RunnerHosts(db, app.state.settings)
            for identity in (None, "d" * 64):
                sample = await service.begin_report(node)
                response = await service.report(
                    node,
                    RunnerReport(
                        sample_id=sample.sample_id,
                        physical_host_id=identity,
                        containers=[
                            {
                                "env_id": uuid.uuid4(),
                                "name": "another-hosts-container",
                                "state": "running",
                            }
                        ],
                    ),
                )
                assert response.stray == [], (
                    "identity denial must not trigger destructive stray cleanup"
                )
                assert response.lost == []
