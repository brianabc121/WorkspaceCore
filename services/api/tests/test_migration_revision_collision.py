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


async def _legacy_bridge(url: str) -> tuple[str, str, str]:
    """旧桥分支0066的DDL；显式保留历史形状，不由待测迁移生成。"""
    conn = await asyncpg.connect(asyncpg_dsn(url))
    workspace, owner, bridge, instance = uuid4(), uuid4(), uuid4(), uuid4()
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
        await conn.execute(
            "INSERT INTO workspace.agent_instances(id,workspace_id,name,network_mode,"
            "im_user_id,im_nickname) VALUES($1,$2,'legacy','public',$3,'legacy')",
            instance,
            workspace,
            "oc_" + instance.hex,
        )
        await conn.execute(
            "INSERT INTO workspace.llm_bridge_grants(instance_id,workspace_id,enabled,"
            "can_write,can_account,can_takeover,platforms) "
            "VALUES($1,$2,true,true,false,true,'[\"codex.cloud\"]'::jsonb)",
            instance,
            workspace,
        )
        return str(bridge), "synthetic-ciphertext", str(instance)
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
                await conn.execute("SELECT setval('workspace.runner_public_port_seq', 20123)")
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
            public_port = await conn.fetchval("SELECT nextval('workspace.runner_public_port_seq')")
            assert public_port == (20124 if legacy in ("gateway", "both") else 20000)
            if bridge:
                assert (
                    await conn.fetchval(
                        "SELECT token_ref FROM workspace.llm_bridge_bridges WHERE id=$1::uuid",
                        bridge[0],
                    )
                    == bridge[1]
                )
                grant = await conn.fetchrow(
                    "SELECT enabled,can_write,can_account,can_takeover,platforms::text "
                    "FROM workspace.llm_bridge_grants WHERE instance_id=$1::uuid",
                    bridge[2],
                )
                assert tuple(grant) == (True, True, False, True, '["codex.cloud"]')
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


@pytest.mark.integration
@pytest.mark.parametrize(
    "damage",
    ["bridge_type", "bridge_fk", "gateway_default", "gateway_check", "gateway_sequence", "neither"],
)
def test_unrecognized_0066_shape_does_not_advance_revision(
    temp_database_url: str, damage: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    if damage.startswith("bridge"):
        asyncio.run(_legacy_bridge(temp_database_url))
    elif damage != "neither":
        command.upgrade(config, "0066")
    statements = {
        "bridge_type": (
            "ALTER TABLE workspace.llm_bridge_bridges ALTER COLUMN base_url TYPE varchar(255)"
        ),
        "bridge_fk": (
            "ALTER TABLE workspace.llm_bridge_grants "
            "DROP CONSTRAINT llm_bridge_grants_instance_id_fkey"
        ),
        "gateway_default": (
            "ALTER TABLE workspace.runner_ingress_rules ALTER COLUMN gateway_port DROP DEFAULT"
        ),
        "gateway_check": (
            "ALTER TABLE workspace.runner_ingress_rules "
            "DROP CONSTRAINT ck_runner_ingress_rules_gateway_port_range; "
            "ALTER TABLE workspace.runner_ingress_rules "
            "ADD CONSTRAINT ck_runner_ingress_rules_gateway_port_range "
            "CHECK(gateway_port > 0)"
        ),
        "gateway_sequence": "ALTER SEQUENCE workspace.runner_ingress_port_seq CYCLE",
        "neither": "UPDATE alembic_version SET version_num='0066'",
    }

    async def damage_and_check(*, check: bool) -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            if check:
                assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0066"
            else:
                await conn.execute(statements[damage])
        finally:
            await conn.close()

    asyncio.run(damage_and_check(check=False))
    with pytest.raises(RuntimeError, match="legacy"):
        command.upgrade(config, "head")
    asyncio.run(damage_and_check(check=True))


@pytest.mark.integration
def test_downgrade_refuses_to_delete_preexisting_bridge_data(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    bridge = asyncio.run(_legacy_bridge(temp_database_url))
    command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="bridge data"):
        command.downgrade(config, "0066")

    async def preserved() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0067"
            assert (
                await conn.fetchval(
                    "SELECT token_ref FROM workspace.llm_bridge_bridges WHERE id=$1::uuid",
                    bridge[0],
                )
                == bridge[1]
            )
        finally:
            await conn.close()

    asyncio.run(preserved())
