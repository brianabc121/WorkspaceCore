"""Published PR0069 and canonical main0069/0070 must preserve their distinct histories."""

import asyncio
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn
from tests.test_migration_revision_collision import _legacy_bridge

HEAD = "rv2_20261115"
NEW_TABLES = (
    "llm_bridge_chat_groups",
    "llm_bridge_chat_group_members",
    "llm_bridge_installs",
    "llm_bridge_deploy_jobs",
)


async def _sql(url: str, statement: str, *args: Any) -> Any:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            return await conn.fetchval(statement, *args)
        # DDL may contain multiple statements; asyncpg's simple-query path accepts them.
        return await conn.execute(statement, *args)
    finally:
        await conn.close()


@pytest.mark.integration
def test_upgrading_old_pr0069_does_not_skip_canonical_chat_groups(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0068")
    # The published PR0069 was exactly bridge-scopes0068 plus a validation-only fence.
    bridge = asyncio.run(_legacy_bridge(temp_database_url, revision="0069", create_tables=False))
    asyncio.run(_sql(temp_database_url, "SELECT setval('workspace.runner_ingress_port_seq',30123)"))
    asyncio.run(_sql(temp_database_url, "SELECT setval('workspace.runner_public_port_seq',20123)"))
    asyncio.run(
        _sql(temp_database_url, "SELECT setval('workspace.runner_network_policy_revision_seq',81)")
    )
    # An explicit canonical target also catches a superficially fixed graph that skips0069.
    command.upgrade(config, "0070")
    for table in NEW_TABLES:
        assert asyncio.run(_sql(temp_database_url, "SELECT to_regclass($1)", "workspace." + table))
    command.upgrade(config, "head")
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == HEAD
    assert (
        asyncio.run(_sql(temp_database_url, "SELECT nextval('workspace.runner_ingress_port_seq')"))
        == 30124
    )
    assert (
        asyncio.run(_sql(temp_database_url, "SELECT nextval('workspace.runner_public_port_seq')"))
        == 20124
    )
    assert (
        asyncio.run(
            _sql(
                temp_database_url, "SELECT nextval('workspace.runner_network_policy_revision_seq')"
            )
        )
        == 82
    )
    assert (
        asyncio.run(
            _sql(
                temp_database_url,
                "SELECT token_ref FROM workspace.llm_bridge_bridges WHERE id=$1::uuid",
                bridge[0],
            )
        )
        == bridge[1]
    )


async def _snapshot(url: str) -> tuple[Any, ...]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        objects = await conn.fetch(
            "SELECT relname,oid FROM pg_class WHERE relnamespace='workspace'::regnamespace "
            "ORDER BY relname"
        )
        constraints = await conn.fetch(
            "SELECT oid,pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace='workspace'::regnamespace ORDER BY oid"
        )
        rows = {}
        for table in ("llm_bridge_bridges", "llm_bridge_grants", *NEW_TABLES):
            if await conn.fetchval("SELECT to_regclass($1)", "workspace." + table):
                rows[table] = await conn.fetchval(
                    "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),"  # noqa: S608
                    f"'[]')::text FROM workspace.{table} t"
                )
        return dict(objects), dict(constraints), rows
    finally:
        await conn.close()


async def _canonical_rows(url: str, revision: str) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        bridge = await conn.fetchrow("SELECT id,workspace_id FROM workspace.llm_bridge_bridges")
        group = uuid4()
        await conn.execute(
            "INSERT INTO workspace.llm_bridge_chat_groups(id,workspace_id,title,request_id) "
            "VALUES($1,$2,'historical group','old-group')",
            group,
            bridge["workspace_id"],
        )
        await conn.execute(
            "INSERT INTO workspace.llm_bridge_chat_group_members(group_id,position,bridge_id,"
            "bridge_name,platform,mode,session_id) "
            "VALUES($1,0,$2,'old','claude','chat','session-old')",
            group,
            bridge["id"],
        )
        if revision == "0070":
            await conn.execute(
                "INSERT INTO workspace.llm_bridge_installs(workspace_id,bridge_id,target_kind,"
                "server_name,address,ssh_port,username,host_key,slot,install_dir,mode) "
                "VALUES($1,$2,'runner','synthetic','test.invalid',22,'tester','synthetic-key',"
                "'slot-old','/srv/bridge','tunnel')",
                bridge["workspace_id"],
                bridge["id"],
            )
            await conn.execute(
                "INSERT INTO workspace.llm_bridge_deploy_jobs(workspace_id,kind,target_kind,"
                "server_name,name,slot,request_id) "
                "VALUES($1,'install','runner','synthetic','old','slot-old','job-old')",
                bridge["workspace_id"],
            )
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.parametrize("revision", ["0069", "0070"])
def test_canonical_main_history_preserves_all_existing_objects_and_rows(
    temp_database_url: str, revision: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, revision)
    asyncio.run(_legacy_bridge(temp_database_url, revision=revision, create_tables=False))
    asyncio.run(_canonical_rows(temp_database_url, revision))
    before = asyncio.run(_snapshot(temp_database_url))
    command.upgrade(config, "head")
    after = asyncio.run(_snapshot(temp_database_url))
    for old, new in zip(before, after, strict=True):
        assert all(new.get(key) == value for key, value in old.items())
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == HEAD


@pytest.mark.integration
@pytest.mark.parametrize(
    "damage",
    [
        "scope_missing",
        "scope_type",
        "gateway_bad",
        "partial_groups",
        "installs_without_groups",
        "generated_scope",
        "deferred_fk",
    ],
)
def test_ambiguous_old_pr0069_is_refused_without_revision_or_data_change(
    temp_database_url: str, damage: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0068")
    asyncio.run(_legacy_bridge(temp_database_url, revision="0069", create_tables=False))
    statements = {
        "generated_scope": (
            "ALTER TABLE workspace.llm_bridge_grants DROP COLUMN bridge_ids; "
            "ALTER TABLE workspace.llm_bridge_grants ADD COLUMN bridge_ids jsonb "
            "GENERATED ALWAYS AS ('[]'::jsonb) STORED"
        ),
        "deferred_fk": (
            "ALTER TABLE workspace.llm_bridge_grants ALTER CONSTRAINT "
            "fk_llm_bridge_grants_workspace_id_workspaces DEFERRABLE INITIALLY DEFERRED"
        ),
        "scope_missing": "ALTER TABLE workspace.llm_bridge_grants DROP COLUMN bridge_ids",
        "scope_type": "ALTER TABLE workspace.llm_bridge_grants ALTER COLUMN bridge_ids TYPE text",
        "gateway_bad": "ALTER SEQUENCE workspace.runner_ingress_port_seq CYCLE",
        "partial_groups": "CREATE TABLE workspace.llm_bridge_chat_groups(id uuid)",
        "installs_without_groups": "CREATE TABLE workspace.llm_bridge_installs(id uuid)",
    }
    asyncio.run(_sql(temp_database_url, statements[damage]))
    before = asyncio.run(_snapshot(temp_database_url))
    with pytest.raises(RuntimeError, match=r"legacy|canonical"):
        command.upgrade(config, "head")
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == "0069"


@pytest.mark.integration
@pytest.mark.parametrize(
    "revision,damage",
    [
        ("0069", "ALTER TABLE workspace.llm_bridge_chat_groups ADD COLUMN unknown text"),
        (
            "0069",
            "ALTER TABLE workspace.llm_bridge_chat_group_members "
            "DROP CONSTRAINT fk_llm_bridge_chat_group_members_group",
        ),
        ("0070", "ALTER TABLE workspace.llm_bridge_installs ALTER COLUMN ssh_port TYPE bigint"),
        ("0070", "DROP INDEX workspace.uq_llm_bridge_deploy_jobs_running"),
        (
            "0070",
            "ALTER TABLE workspace.llm_bridge_deploy_jobs DROP COLUMN finished_at; "
            "ALTER TABLE workspace.llm_bridge_deploy_jobs ADD COLUMN finished_at timestamptz "
            "GENERATED ALWAYS AS (TIMESTAMPTZ '2026-01-01 00:00:00+00') STORED",
        ),
    ],
)
def test_malformed_canonical_history_is_refused_atomically(
    temp_database_url: str, revision: str, damage: str
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, revision)
    asyncio.run(_legacy_bridge(temp_database_url, revision=revision, create_tables=False))
    asyncio.run(_canonical_rows(temp_database_url, revision))
    asyncio.run(_sql(temp_database_url, damage))
    before = asyncio.run(_snapshot(temp_database_url))
    with pytest.raises(RuntimeError, match="canonical"):
        command.upgrade(config, "head")
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert (
        asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == revision
    )


@pytest.mark.integration
def test_current_and_check_do_not_relabel_or_repair_old_pr0069(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0068")
    asyncio.run(_legacy_bridge(temp_database_url, revision="0069", create_tables=False))
    before = asyncio.run(_snapshot(temp_database_url))
    command.current(config)
    # A stale database cannot pass autogenerate check, but inspecting it must stay read-only.
    from alembic.util import CommandError

    with pytest.raises(CommandError):
        command.check(config)
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == "0069"


@pytest.mark.integration
@pytest.mark.parametrize("revision", ["0069", "0070"])
def test_canonical_history_missing_scopes_is_not_misclassified_as_runtime0068(
    temp_database_url: str,
    revision: str,
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, revision)
    asyncio.run(_legacy_bridge(temp_database_url, revision=revision, create_tables=False))
    asyncio.run(
        _sql(temp_database_url, "ALTER TABLE workspace.llm_bridge_grants DROP COLUMN bridge_ids")
    )
    before = asyncio.run(_snapshot(temp_database_url))
    with pytest.raises(RuntimeError, match=r"legacy|canonical"):
        command.upgrade(config, "head")
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert (
        asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == revision
    )


@pytest.mark.integration
def test_failed_canonical_ddl_rolls_back_old_pr0069_mapping(temp_database_url: str) -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    config = alembic_config(temp_database_url)
    command.upgrade(config, "0068")
    asyncio.run(_legacy_bridge(temp_database_url, revision="0069", create_tables=False))
    before = asyncio.run(_snapshot(temp_database_url))

    def fail_installs(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _ctx: Any, _many: Any
    ) -> None:
        if statement.strip().startswith("CREATE TABLE workspace.llm_bridge_installs"):
            raise RuntimeError("injected canonical DDL failure")

    event.listen(Engine, "after_cursor_execute", fail_installs)
    try:
        with pytest.raises(RuntimeError, match="injected canonical DDL failure"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "after_cursor_execute", fail_installs)
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == "0069"


def _runtime0068_fixture(url: str) -> tuple[str, str, str]:
    config = alembic_config(url)
    command.upgrade(config, "0067")
    bridge = asyncio.run(_legacy_bridge(url, revision="0068", create_tables=False))
    for sequence, value in (
        ("runner_ingress_port_seq", 30123),
        ("runner_public_port_seq", 20123),
        ("runner_network_policy_revision_seq", 81),
    ):
        asyncio.run(_sql(url, f"SELECT setval('workspace.{sequence}',{value})"))
    return bridge


async def _allocator_snapshot(url: str) -> tuple[Any, ...]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return tuple(
            [
                tuple(await conn.fetchrow(statement))
                for statement in (
                    "SELECT last_value,is_called FROM workspace.runner_ingress_port_seq",
                    "SELECT last_value,is_called FROM workspace.runner_public_port_seq",
                    "SELECT last_value,is_called FROM workspace.runner_network_policy_revision_seq",
                )
            ]
        )
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.parametrize("intermediate", ["0069", "0070"])
@pytest.mark.parametrize("scope", ["all", "none", "one"])
def test_runtime0068_staged_upgrade_preserves_history_and_scope(
    temp_database_url: str, intermediate: str, scope: str
) -> None:
    import json

    bridge = _runtime0068_fixture(temp_database_url)
    config = alembic_config(temp_database_url)
    before = asyncio.run(_snapshot(temp_database_url))
    allocators = asyncio.run(_allocator_snapshot(temp_database_url))
    command.upgrade(config, intermediate)
    # Every committed canonical stamp must already include main0068's scope column.
    assert asyncio.run(
        _sql(
            temp_database_url,
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='workspace' "
            "AND table_name='llm_bridge_grants' AND column_name='bridge_ids')",
        )
    )
    assert (
        asyncio.run(
            _sql(
                temp_database_url,
                "SELECT bridge_ids FROM workspace.llm_bridge_grants WHERE instance_id=$1::uuid",
                bridge[2],
            )
        )
        is None
    )
    after = asyncio.run(_snapshot(temp_database_url))
    for original, current in zip(before[:2], after[:2], strict=True):
        assert all(current.get(key) == value for key, value in original.items())
    for table, original_rows in before[2].items():
        rows = json.loads(after[2][table])
        if table == "llm_bridge_grants":
            for row in rows:
                row.pop("bridge_ids")
        assert rows == json.loads(original_rows)
    assert asyncio.run(_allocator_snapshot(temp_database_url)) == allocators
    # Preserve explicit restrictions introduced while paused at a canonical revision.
    value = None if scope == "all" else json.dumps([] if scope == "none" else [bridge[0]])
    asyncio.run(
        _sql(
            temp_database_url, "UPDATE workspace.llm_bridge_grants SET bridge_ids=$1::jsonb", value
        )
    )
    asyncio.run(_canonical_rows(temp_database_url, intermediate))
    before_final = asyncio.run(_snapshot(temp_database_url))
    command.upgrade(config, "head")
    final = asyncio.run(_snapshot(temp_database_url))
    for original, current in zip(before_final, final, strict=True):
        assert all(current.get(key) == val for key, val in original.items())
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == HEAD
    assert (
        asyncio.run(
            _sql(
                temp_database_url,
                "SELECT bridge_ids::text FROM workspace.llm_bridge_grants "
                "WHERE instance_id=$1::uuid",
                bridge[2],
            )
        )
        == value
    )
    assert asyncio.run(_allocator_snapshot(temp_database_url)) == allocators


@pytest.mark.integration
@pytest.mark.parametrize(
    "intermediate,table",
    [
        ("0069", "llm_bridge_chat_groups"),
        ("0070", "llm_bridge_installs"),
    ],
)
def test_runtime0068_scope_canonicalization_rolls_back_with_failed_staged_upgrade(
    temp_database_url: str, intermediate: str, table: str
) -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    _runtime0068_fixture(temp_database_url)
    before = asyncio.run(_snapshot(temp_database_url))
    allocators = asyncio.run(_allocator_snapshot(temp_database_url))

    observed_scopes: list[bool] = []

    def fail_ddl(
        conn: Any, _cursor: Any, statement: str, _params: Any, _ctx: Any, _many: Any
    ) -> None:
        from sqlalchemy import text

        if statement.strip().startswith("CREATE TABLE workspace." + table):
            observed_scopes.append(
                bool(
                    conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema='workspace' AND table_name='llm_bridge_grants' "
                            "AND column_name='bridge_ids')"
                        )
                    )
                )
            )
            raise RuntimeError("injected staged canonical DDL failure")

    event.listen(Engine, "after_cursor_execute", fail_ddl)
    try:
        with pytest.raises(RuntimeError, match="injected staged canonical DDL failure"):
            command.upgrade(alembic_config(temp_database_url), intermediate)
    finally:
        event.remove(Engine, "after_cursor_execute", fail_ddl)
    assert observed_scopes == [True]
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert asyncio.run(_allocator_snapshot(temp_database_url)) == allocators
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == "0068"


@pytest.mark.integration
def test_runtime0068_readonly_and_noop_commands_do_not_add_scopes(temp_database_url: str) -> None:
    from alembic.util import CommandError

    _runtime0068_fixture(temp_database_url)
    config = alembic_config(temp_database_url)
    before = asyncio.run(_snapshot(temp_database_url))
    command.current(config)
    with pytest.raises(CommandError):
        command.check(config)
    command.upgrade(config, "0068")
    assert asyncio.run(_snapshot(temp_database_url)) == before
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == "0068"


@pytest.mark.integration
@pytest.mark.parametrize("intermediate", ["0069", "0070"])
def test_runtime0068_explicit_canonical_target_can_continue_to_head(
    temp_database_url: str, intermediate: str
) -> None:
    _runtime0068_fixture(temp_database_url)
    config = alembic_config(temp_database_url)
    command.upgrade(config, intermediate)
    command.upgrade(config, "head")
    assert asyncio.run(_sql(temp_database_url, "SELECT version_num FROM alembic_version")) == HEAD
