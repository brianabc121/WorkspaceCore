"""合并两条旧0066的真实数据库状态，升级不得丢桥记录或重置网关序列。"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.script import ScriptDirectory

from tests.helpers import alembic_config, asyncpg_dsn


def test_migration_graph_has_one_unambiguous_upgrade_target() -> None:
    script = ScriptDirectory.from_config(alembic_config("unused"))
    assert len(script.get_heads()) == 1
    assert script.get_current_head() is not None


async def _legacy_bridge(url: str) -> tuple[str, str]:
    """旧桥分支0066的DDL；显式保留历史形状，不由待测迁移生成。"""
    conn = await asyncpg.connect(asyncpg_dsn(url))
    workspace, owner, bridge = uuid4(), uuid4(), uuid4()
    try:
        await conn.execute(
            """
            CREATE TABLE workspace.llm_bridge_bridges (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              workspace_id uuid NOT NULL REFERENCES workspace.workspaces(id) ON DELETE CASCADE,
              name text NOT NULL, base_url text NOT NULL, token_ref text NOT NULL DEFAULT '',
              enabled boolean NOT NULL DEFAULT true,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE (workspace_id, name));
            CREATE TABLE workspace.llm_bridge_grants (
              instance_id uuid PRIMARY KEY
                REFERENCES workspace.agent_instances(id) ON DELETE CASCADE,
              workspace_id uuid NOT NULL REFERENCES workspace.workspaces(id) ON DELETE CASCADE,
              enabled boolean NOT NULL DEFAULT false, can_write boolean NOT NULL DEFAULT false,
              can_account boolean NOT NULL DEFAULT false,
              can_takeover boolean NOT NULL DEFAULT false,
              platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
              updated_at timestamptz NOT NULL DEFAULT now());
            UPDATE alembic_version SET version_num='0066';
            """
        )
        await conn.execute(
            "INSERT INTO workspace.users(id,email,password_hash,display_name) "
            "VALUES($1,'migration@example.test','synthetic','Migration')",
            owner,
        )
        await conn.execute(
            "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'test',$2)",
            workspace,
            owner,
        )
        await conn.execute(
            "INSERT INTO workspace.llm_bridge_bridges(id,workspace_id,name,base_url,token_ref) "
            "VALUES($1,$2,'legacy','https://bridge.invalid','synthetic-ciphertext')",
            bridge,
            workspace,
        )
        return str(bridge), "synthetic-ciphertext"
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.parametrize("legacy", ["base", "gateway", "bridge", "both"])
def test_upgrade_from_each_legacy_branch_preserves_data_and_allocator(
    temp_database_url: str, legacy: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    if legacy in ("gateway", "both"):
        command.upgrade(config, "0066")
    bridge = (
        asyncio.run(_legacy_bridge(temp_database_url)) if legacy in ("bridge", "both") else None
    )
    if legacy in ("gateway", "both"):

        async def advance() -> None:
            conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
            try:
                await conn.execute("SELECT setval('workspace.runner_ingress_port_seq', 30123)")
                await conn.execute(
                    "SELECT setval('workspace.runner_network_policy_revision_seq', 81)"
                )
            finally:
                await conn.close()

        asyncio.run(advance())
    command.upgrade(config, "head")

    async def verify() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            assert await conn.fetchval("SELECT to_regclass('workspace.llm_bridge_bridges')")
            assert await conn.fetchval("SELECT to_regclass('workspace.llm_bridge_grants')")
            port = await conn.fetchval("SELECT nextval('workspace.runner_ingress_port_seq')")
            policy = await conn.fetchval(
                "SELECT nextval('workspace.runner_network_policy_revision_seq')"
            )
            assert port == (30124 if legacy in ("gateway", "both") else 30000)
            assert policy == (82 if legacy in ("gateway", "both") else 1)
            if bridge:
                assert (
                    await conn.fetchval(
                        "SELECT token_ref FROM workspace.llm_bridge_bridges WHERE id=$1::uuid",
                        bridge[0],
                    )
                    == bridge[1]
                )
        finally:
            await conn.close()

    asyncio.run(verify())
    # 二次升级不重放，保留序列与数据；完整回退/升级只在专用空测试库执行。
    command.upgrade(config, "head")
    if legacy == "base":
        command.downgrade(config, "base")
        command.upgrade(config, "head")


@pytest.mark.integration
def test_partial_legacy_bridge_is_rejected_without_creating_gateway_objects(
    temp_database_url: str,
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    asyncio.run(_legacy_bridge(temp_database_url))

    async def sql(statement: str) -> object:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            return await conn.fetchval(statement)
        finally:
            await conn.close()

    asyncio.run(sql("DROP TABLE workspace.llm_bridge_grants"))
    with pytest.raises(RuntimeError, match="partial legacy"):
        command.upgrade(config, "head")
    assert asyncio.run(sql("SELECT to_regclass('workspace.runner_ingress_port_seq')")) is None
    assert asyncio.run(sql("SELECT version_num FROM alembic_version")) == "0066"
