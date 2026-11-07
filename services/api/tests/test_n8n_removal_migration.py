"""Remove retired workflow storage while retaining unrelated data and idempotency."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration
RETIRED = ("automation_connections", "automations", "automation_runs", "app_logins")


def test_removal_upgrade_and_empty_downgrade_preserve_unrelated_data(
    temp_database_url: str,
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0046")
    user, workspace, automation = uuid4(), uuid4(), uuid4()

    async def seed() -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            await conn.execute(
                "INSERT INTO workspace.users(id,email,password_hash,display_name) "
                "VALUES($1,'migration@example.test','synthetic','Migration')",
                user,
            )
            await conn.execute(
                "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'Migration',$2)",
                workspace,
                user,
            )
            await conn.execute(
                "INSERT INTO workspace.automation_connections"
                "(workspace_id,base_url,public_url,api_key_ref,trigger_secret_ref) "
                "VALUES($1,'http://retired','https://retired.example.test',"
                "'enc:v1:synthetic','enc:v1:synthetic')",
                workspace,
            )
            await conn.execute(
                "INSERT INTO workspace.automations(id,workspace_id,name,workflow_id,webhook_path) "
                "VALUES($1,$2,'Retired','retired','retired')",
                automation,
                workspace,
            )
            await conn.execute(
                "INSERT INTO workspace.automation_runs(workspace_id,automation_id,status,trace_id) "
                "VALUES($1,$2,'unknown','synthetic')",
                workspace,
                automation,
            )
            await conn.execute(
                "INSERT INTO workspace.app_logins(workspace_id,app,username,password_ref) "
                "VALUES($1,'n8n','retired@example.test','enc:v1:synthetic')",
                workspace,
            )
            for operation in ("automation.trigger", "task.create"):
                await conn.execute(
                    "INSERT INTO workspace.idempotency_requests"
                    "(workspace_id,actor_id,idempotency_key,request_fingerprint,status,"
                    "context_json,expires_at) VALUES($1,$2,$3,'synthetic','unknown',"
                    "jsonb_build_object('operation',$4::text),now()+interval '1 day')",
                    workspace,
                    user,
                    uuid4(),
                    operation,
                )
        finally:
            await conn.close()

    async def verify(removed: bool) -> None:
        conn = await asyncpg.connect(asyncpg_dsn(temp_database_url))
        try:
            for table in RETIRED:
                if removed:
                    assert (
                        await conn.fetchval("SELECT to_regclass($1)", f"workspace.{table}") is None
                    )
                else:
                    # Fixed constants, not caller input.
                    count = await conn.fetchval(f"SELECT count(*) FROM workspace.{table}")  # noqa: S608
                    assert count == 0
            assert await conn.fetchval("SELECT id FROM workspace.users WHERE id=$1", user) == user
            operations = await conn.fetch(
                "SELECT context_json->>'operation' AS operation FROM workspace.idempotency_requests"
            )
            assert [row["operation"] for row in operations] == ["task.create"]
            if not removed:
                check = await conn.fetchval(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid='workspace.app_logins'::regclass AND contype='c'"
                )
                assert "n8n" in check and "vikunja" not in check
        finally:
            await conn.close()

    asyncio.run(seed())
    command.upgrade(config, "head")
    asyncio.run(verify(True))
    command.check(config)
    command.downgrade(config, "0046")
    asyncio.run(verify(False))
    command.upgrade(config, "head")
    asyncio.run(verify(True))
