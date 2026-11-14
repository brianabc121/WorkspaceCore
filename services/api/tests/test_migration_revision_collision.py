"""合并两条旧0066的真实数据库状态，升级不得丢桥记录或重置网关序列。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from tests.helpers import alembic_config, asyncpg_dsn


def test_migration_graph_has_one_unambiguous_upgrade_target() -> None:
    script = ScriptDirectory.from_config(alembic_config("unused"))
    assert len(script.get_heads()) == 1
    assert script.get_current_head() == "0069"


async def _legacy_bridge(
    url: str, *, revision: str = "0066", create_tables: bool = True
) -> tuple[str, str, str]:
    """旧桥分支0066的DDL；显式保留历史形状，不由待测迁移生成。"""
    conn = await asyncpg.connect(asyncpg_dsn(url))
    workspace, owner, bridge, instance = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        if create_tables:
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
                """
            )
        await conn.execute("UPDATE alembic_version SET version_num=$1", revision)
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


async def _legacy_gateway(url: str) -> None:
    """不可变的历史网关0066 DDL，不调用待测迁移来制造历史状态。"""
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        await conn.execute("CREATE SEQUENCE workspace.runner_network_policy_revision_seq AS BIGINT")
        for table, sequence, low, high in (
            ("runner_ingress_rules", "runner_ingress_port_seq", 30000, 39999),
            ("runner_published_endpoints", "runner_public_port_seq", 20000, 29999),
        ):
            await conn.execute(
                f"CREATE SEQUENCE workspace.{sequence} START {low} "
                f"MINVALUE {low} MAXVALUE {high} NO CYCLE; "
                f"ALTER TABLE workspace.{table} ADD COLUMN gateway_port integer NOT NULL "
                f"DEFAULT nextval('workspace.{sequence}'), "
                f"ADD CONSTRAINT uq_{table}_gateway_port UNIQUE (gateway_port), "
                f"ADD CONSTRAINT ck_{table}_gateway_port_range "
                f"CHECK(gateway_port BETWEEN {low} AND {high})"
            )
        await conn.execute("UPDATE alembic_version SET version_num='0066'")
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    "legacy", ["base", "gateway", "bridge", "both", "bridge0067", "gateway0067"]
)
def test_upgrade_from_each_legacy_branch_preserves_data_and_allocator(
    temp_database_url: str, legacy: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    if legacy == "gateway0067":
        command.upgrade(config, "0067")
    elif legacy in ("gateway", "both", "bridge0067"):
        asyncio.run(_legacy_gateway(temp_database_url))
    bridge = (
        asyncio.run(
            _legacy_bridge(
                temp_database_url,
                revision="0067" if legacy.endswith("0067") else "0066",
                create_tables=legacy != "gateway0067",
            )
        )
        if legacy in ("bridge", "both", "bridge0067", "gateway0067")
        else None
    )
    if legacy in ("gateway", "both", "bridge0067", "gateway0067"):

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
            assert port == (
                30124 if legacy in ("gateway", "both", "bridge0067", "gateway0067") else 30000
            )
            assert policy == (
                82 if legacy in ("gateway", "both", "bridge0067", "gateway0067") else 1
            )
            public_port = await conn.fetchval("SELECT nextval('workspace.runner_public_port_seq')")
            assert public_port == (
                20124 if legacy in ("gateway", "both", "bridge0067", "gateway0067") else 20000
            )
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
@pytest.mark.parametrize("revision", ["0066", "0067"])
@pytest.mark.parametrize(
    "damage",
    [
        "bridge_type",
        "bridge_fk",
        "gateway_default",
        "gateway_check",
        "gateway_sequence",
        "neither",
        "gateway_smallint",
        "gateway_bigint",
        "gateway_unvalidated_check",
        "bridge_unvalidated_fk",
    ],
)
def test_unrecognized_0066_shape_does_not_advance_revision(
    temp_database_url: str, damage: str, revision: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    if damage.startswith("bridge"):
        asyncio.run(_legacy_bridge(temp_database_url))
    elif damage != "neither":
        asyncio.run(_legacy_gateway(temp_database_url))
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
        "gateway_smallint": (
            "ALTER TABLE workspace.runner_ingress_rules ALTER COLUMN gateway_port TYPE smallint"
        ),
        "gateway_bigint": (
            "ALTER TABLE workspace.runner_ingress_rules ALTER COLUMN gateway_port TYPE bigint"
        ),
        "gateway_unvalidated_check": (
            "ALTER TABLE workspace.runner_ingress_rules "
            "DROP CONSTRAINT ck_runner_ingress_rules_gateway_port_range; "
            "ALTER TABLE workspace.runner_ingress_rules "
            "ADD CONSTRAINT ck_runner_ingress_rules_gateway_port_range "
            "CHECK(gateway_port BETWEEN 30000 AND 39999) NOT VALID"
        ),
        "bridge_unvalidated_fk": (
            "ALTER TABLE workspace.llm_bridge_grants "
            "DROP CONSTRAINT llm_bridge_grants_instance_id_fkey; "
            "ALTER TABLE workspace.llm_bridge_grants "
            "ADD CONSTRAINT llm_bridge_grants_instance_id_fkey FOREIGN KEY(instance_id) "
            "REFERENCES workspace.agent_instances(id) ON DELETE CASCADE NOT VALID"
        ),
    }

    async def damage_and_check(*, check: bool) -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            if check:
                assert await conn.fetchval("SELECT version_num FROM alembic_version") == revision
            else:
                await conn.execute(statements[damage])
                await conn.execute("UPDATE alembic_version SET version_num=$1", revision)
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
    command.downgrade(config, "0067")
    command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="bridge data"):
        command.downgrade(config, "0065")

    async def preserved() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0069"
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


@pytest.mark.integration
def test_unreconciled_gateway_only_0066_cannot_downgrade_as_bridge(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    asyncio.run(_legacy_gateway(temp_database_url))
    with pytest.raises(RuntimeError, match="legacy"):
        command.downgrade(config, "0065")

    async def unchanged() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0066"
            assert await conn.fetchval("SELECT to_regclass('workspace.runner_ingress_port_seq')")
        finally:
            await conn.close()

    asyncio.run(unchanged())


@pytest.mark.integration
def test_bridge_insert_cannot_commit_between_empty_check_and_drop(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "head")
    owner, workspace = uuid4(), uuid4()

    async def seed() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            await conn.execute(
                "INSERT INTO workspace.users(id,email,password_hash,display_name) "
                "VALUES($1,'race@example.test','synthetic','Migration')",
                owner,
            )
            await conn.execute(
                "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'race',$2)",
                workspace,
                owner,
            )
        finally:
            await conn.close()

    async def racing_insert() -> str:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            await conn.execute("SET lock_timeout='500ms'")
            try:
                await conn.execute(
                    "INSERT INTO workspace.llm_bridge_bridges(workspace_id,name,base_url) "
                    "VALUES($1,'racing','https://bridge.invalid')",
                    workspace,
                )
                return "committed"
            except asyncpg.LockNotAvailableError:
                return "blocked-by-migration-lock"
        finally:
            await conn.close()

    asyncio.run(seed())
    outcomes: list[str] = []

    def after_empty_check(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.startswith("SELECT EXISTS(SELECT 1 FROM workspace.llm_bridge_grants)"):
            # The SELECT result has been read by PostgreSQL. A second connection now
            # attempts a real commit before DROP; PostgreSQL lock_timeout bounds it.
            with ThreadPoolExecutor(max_workers=1) as pool:
                outcomes.append(
                    pool.submit(lambda: asyncio.run(racing_insert())).result(timeout=10)
                )

    event.listen(Engine, "after_cursor_execute", after_empty_check)
    try:
        command.downgrade(config, "0065")
    finally:
        event.remove(Engine, "after_cursor_execute", after_empty_check)
    assert outcomes == ["blocked-by-migration-lock"], (
        "a committed bridge insert was silently dropped"
    )


@pytest.mark.integration
@pytest.mark.parametrize("published", [False, True], ids=["runtime0068", "main0068"])
def test_both_published_0068_states_upgrade_without_replacing_data_or_objects(
    temp_database_url: str, published: bool
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0067")
    bridge = asyncio.run(_legacy_bridge(temp_database_url, revision="0068", create_tables=False))

    async def snapshot(*, prepare: bool) -> tuple[object, ...]:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            if prepare:
                if published:
                    # Frozen main0068 DDL and restrictive scope, independent of new migration.
                    await conn.execute(
                        "ALTER TABLE workspace.llm_bridge_grants ADD COLUMN bridge_ids jsonb; "
                        "UPDATE workspace.llm_bridge_grants SET bridge_ids='[]'::jsonb"
                    )
                await conn.execute("SELECT setval('workspace.runner_ingress_port_seq', 30123)")
                await conn.execute("SELECT setval('workspace.runner_public_port_seq', 20123)")
                await conn.execute(
                    "SELECT setval('workspace.runner_network_policy_revision_seq', 81)"
                )
            objects = await conn.fetch(
                "SELECT oid,relname FROM pg_class WHERE relnamespace='workspace'::regnamespace "
                "AND (relname LIKE 'llm_bridge_%' OR relkind='S') ORDER BY relname"
            )
            constraints = await conn.fetch(
                "SELECT oid,pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE connamespace='workspace'::regnamespace ORDER BY oid"
            )
            return tuple(map(tuple, objects)), tuple(map(tuple, constraints))
        finally:
            await conn.close()

    before = asyncio.run(snapshot(prepare=True))
    command.upgrade(config, "head")
    assert asyncio.run(snapshot(prepare=False)) == before

    async def preserved() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0069"
            grant = await conn.fetchrow(
                "SELECT enabled,can_write,can_account,can_takeover,"
                "platforms::text,bridge_ids::text "
                "FROM workspace.llm_bridge_grants WHERE instance_id=$1::uuid",
                bridge[2],
            )
            assert tuple(grant) == (
                True,
                True,
                False,
                True,
                '["codex.cloud"]',
                "[]" if published else None,
            )
            assert (
                await conn.fetchval(
                    "SELECT token_ref FROM workspace.llm_bridge_bridges WHERE id=$1::uuid",
                    bridge[0],
                )
                == bridge[1]
            )
            for sequence, expected in (
                ("runner_ingress_port_seq", 30124),
                ("runner_public_port_seq", 20124),
                ("runner_network_policy_revision_seq", 82),
            ):
                assert await conn.fetchval(f"SELECT nextval('workspace.{sequence}')") == expected
        finally:
            await conn.close()

    asyncio.run(preserved())
    command.upgrade(config, "head")


@pytest.mark.integration
@pytest.mark.parametrize("damage", ["type", "nullable", "default", "unknown_column"])
def test_unknown_0068_grant_shape_is_rejected_atomically(
    temp_database_url: str, damage: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0067")

    async def prepare() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            definitions = {
                "type": "bridge_ids text",
                "nullable": "bridge_ids jsonb NOT NULL",
                "default": "bridge_ids jsonb DEFAULT '[]'::jsonb",
                "unknown_column": "unexpected_grant_scope jsonb",
            }
            await conn.execute(
                "ALTER TABLE workspace.llm_bridge_grants ADD COLUMN " + definitions[damage]
            )
            await conn.execute("UPDATE alembic_version SET version_num='0068'")
        finally:
            await conn.close()

    asyncio.run(prepare())
    with pytest.raises(RuntimeError, match="legacy"):
        command.upgrade(config, "head")

    async def unchanged() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0068"
        finally:
            await conn.close()

    asyncio.run(unchanged())


@pytest.mark.integration
@pytest.mark.parametrize("scope", ["[]", '["00000000-0000-0000-0000-000000000001"]'])
def test_downgrade_refuses_to_remove_bridge_access_scopes(
    temp_database_url: str, scope: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0067")
    bridge = asyncio.run(_legacy_bridge(temp_database_url, revision="0067", create_tables=False))
    command.upgrade(config, "head")

    async def scoped(*, prepare: bool) -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            if prepare:
                await conn.execute(
                    "UPDATE workspace.llm_bridge_grants SET bridge_ids=$1::jsonb", scope
                )
            else:
                assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0069"
                assert (
                    await conn.fetchval(
                        "SELECT bridge_ids::text FROM workspace.llm_bridge_grants "
                        "WHERE instance_id=$1::uuid",
                        bridge[2],
                    )
                    == scope
                )
        finally:
            await conn.close()

    asyncio.run(scoped(prepare=True))
    with pytest.raises(RuntimeError, match=r"bridge.*scope"):
        command.downgrade(config, "0067")
    asyncio.run(scoped(prepare=False))


@pytest.mark.integration
@pytest.mark.parametrize("shape", ["bridge_only", "gateway_only", "neither"])
def test_partial_0068_runtime_is_rejected_without_repair(
    temp_database_url: str, shape: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0065")
    if shape == "bridge_only":
        asyncio.run(_legacy_bridge(temp_database_url))
    elif shape == "gateway_only":
        asyncio.run(_legacy_gateway(temp_database_url))

    async def state(*, prepare: bool) -> tuple[object, ...]:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            if prepare:
                await conn.execute("UPDATE alembic_version SET version_num='0068'")
            return tuple(
                await conn.fetchrow(
                    "SELECT (SELECT version_num FROM alembic_version), "
                    "to_regclass('workspace.llm_bridge_bridges'), "
                    "to_regclass('workspace.runner_ingress_port_seq')"
                )
            )
        finally:
            await conn.close()

    before = asyncio.run(state(prepare=True))
    with pytest.raises(RuntimeError, match="legacy"):
        command.upgrade(config, "head")
    assert asyncio.run(state(prepare=False)) == before


@pytest.mark.integration
def test_scope_update_cannot_commit_between_empty_check_and_column_drop(
    temp_database_url: str,
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0067")
    bridge = asyncio.run(_legacy_bridge(temp_database_url, revision="0067", create_tables=False))
    command.upgrade(config, "head")
    outcomes: list[str] = []

    async def restrict_access() -> str:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            await conn.execute("SET lock_timeout='500ms'")
            try:
                await conn.execute(
                    "UPDATE workspace.llm_bridge_grants SET bridge_ids='[]'::jsonb "
                    "WHERE instance_id=$1::uuid",
                    bridge[2],
                )
                return "committed"
            except asyncpg.LockNotAvailableError:
                return "blocked-by-migration-lock"
        finally:
            await conn.close()

    def after_empty_check(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.startswith(
            "SELECT EXISTS(SELECT 1 FROM workspace.llm_bridge_grants WHERE bridge_ids IS NOT NULL)"
        ):
            with ThreadPoolExecutor(max_workers=1) as pool:
                outcomes.append(
                    pool.submit(lambda: asyncio.run(restrict_access())).result(timeout=10)
                )

    event.listen(Engine, "after_cursor_execute", after_empty_check)
    try:
        command.downgrade(config, "0067")
    finally:
        event.remove(Engine, "after_cursor_execute", after_empty_check)
    assert outcomes == ["blocked-by-migration-lock"], "scope change was lost during downgrade"
