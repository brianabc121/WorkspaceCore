"""需要真实 PostgreSQL / Redis 的测试（见 conftest.py 的说明）。"""

import asyncio

import asyncpg
import httpx
import pytest
from alembic import command
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

from app.domain.operations import WORKER_HEARTBEAT_KEY
from app.jobs.worker import heartbeat
from app.main import create_app
from tests.helpers import alembic_config, asyncpg_dsn, settings_for

pytestmark = pytest.mark.integration


async def _schema_state(database_url: str) -> tuple[bool, set[str]]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        has_schema = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'workspace')"
        )
        rows = await conn.fetch(
            "SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'pg_trgm')"
        )
    finally:
        await conn.close()
    return bool(has_schema), {row["extname"] for row in rows}


EXPECTED_TABLES = {
    "users",
    "auth_sessions",
    "external_identities",
    "workspaces",
    "workspace_members",
    "audit_logs",
    "dashboards",
    "widgets",
    "integrations",
    "integration_accounts",
    "external_resources",
    "idempotency_requests",
    "sync_cursors",
    # T-0082：工作台自己的项目与任务；webhook_inbox 随 Vikunja 一起删除
    "projects",
    "tasks",
}


async def _tables(database_url: str) -> set[str]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'workspace'")
    finally:
        await conn.close()
    return {row["tablename"] for row in rows}


def test_migrations_upgrade_downgrade_on_empty_database(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)

    command.upgrade(config, "head")
    assert asyncio.run(_schema_state(temp_database_url)) == (True, {"pgcrypto", "pg_trgm"})
    assert asyncio.run(_tables(temp_database_url)) >= EXPECTED_TABLES
    # ORM 模型与迁移一致：autogenerate 检测不到任何差异
    command.check(config)

    command.downgrade(config, "base")
    assert asyncio.run(_schema_state(temp_database_url)) == (False, set())

    # 回退后能再次升级
    command.upgrade(config, "head")
    assert asyncio.run(_schema_state(temp_database_url)) == (True, {"pgcrypto", "pg_trgm"})


async def _note_columns(database_url: str) -> dict[str, str]:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        rows = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'workspace' AND table_name = 'knowledge_notes'"
        )
        fks = await conn.fetch(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'workspace.knowledge_notes'::regclass AND contype = 'f'"
        )
    finally:
        await conn.close()
    columns = {row["column_name"]: row["is_nullable"] for row in rows}
    columns.update({row["conname"]: "fk" for row in fks})
    return columns


def test_migration_0025_drops_note_confirmation(temp_database_url: str) -> None:
    """T-0102：0025 删除“人已确认”的两列；降级加回空列与外键。"""
    config = alembic_config(temp_database_url)

    command.upgrade(config, "0025")
    columns = asyncio.run(_note_columns(temp_database_url))
    assert "confirmed_by" not in columns
    assert "confirmed_at" not in columns
    assert "fk_knowledge_notes_confirmed_by_users" not in columns

    command.downgrade(config, "0024")
    columns = asyncio.run(_note_columns(temp_database_url))
    assert columns["confirmed_by"] == "YES"
    assert columns["confirmed_at"] == "YES"
    assert columns["fk_knowledge_notes_confirmed_by_users"] == "fk"

    command.upgrade(config, "head")
    command.check(config)


def test_migration_0027_task_timeline_keeps_existing_tasks(temp_database_url: str) -> None:
    """T-0104：0027 给已有任务加上空的执行者 / 计划开始，降级删除新表与新字段，任务保留。"""
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0026")
    asyncio.run(_seed_task(temp_database_url))

    command.upgrade(config, "0027")
    row = asyncio.run(
        _fetch_one(
            temp_database_url,
            "SELECT title, planned_start_at, started_at, assignee_user_id, assignee_instance_id"
            " FROM workspace.tasks",
        )
    )
    assert dict(row) == {
        "title": "已有任务",
        "planned_start_at": None,
        "started_at": None,
        "assignee_user_id": None,
        "assignee_instance_id": None,
    }
    assert {"task_runs", "agent_usage_minutes", "agent_usage_cursors"} <= asyncio.run(
        _tables(temp_database_url)
    )

    command.downgrade(config, "0026")
    tables = asyncio.run(_tables(temp_database_url))
    assert not {"task_runs", "agent_usage_minutes", "agent_usage_cursors"} & tables
    columns = asyncio.run(
        _fetch_one(
            temp_database_url,
            "SELECT array_agg(column_name::text) AS names FROM information_schema.columns"
            " WHERE table_schema = 'workspace' AND table_name = 'tasks'",
        )
    )["names"]
    assert "assignee_user_id" not in columns and "planned_start_at" not in columns
    assert asyncio.run(_fetch_one(temp_database_url, "SELECT title FROM workspace.tasks"))[0] == (
        "已有任务"
    )

    command.upgrade(config, "head")
    command.check(config)


async def _seed_task(database_url: str) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        user = await conn.fetchval(
            "INSERT INTO workspace.users (email, display_name, password_hash)"
            " VALUES ('a@example.com', 'A', 'x') RETURNING id"
        )
        workspace = await conn.fetchval(
            "INSERT INTO workspace.workspaces (name, owner_id) VALUES ('W', $1) RETURNING id", user
        )
        project = await conn.fetchval(
            "INSERT INTO workspace.projects (workspace_id, title) VALUES ($1, 'P') RETURNING id",
            workspace,
        )
        await conn.execute(
            "INSERT INTO workspace.tasks (workspace_id, project_id, title)"
            " VALUES ($1, $2, '已有任务')",
            workspace,
            project,
        )
    finally:
        await conn.close()


async def _fetch_one(database_url: str, sql: str) -> asyncpg.Record:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        row = await conn.fetchrow(sql)
    finally:
        await conn.close()
    assert row is not None
    return row


async def _get_readyz(database_url: str, redis_url: str) -> httpx.Response:
    app = create_app(settings_for(database_url, redis_url))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/readyz")


def test_readyz_with_real_dependencies(temp_database_url: str, redis_url: str) -> None:
    response = asyncio.run(_get_readyz(temp_database_url, redis_url))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_readyz_when_redis_is_unreachable(temp_database_url: str) -> None:
    # 端口 1 上不会有 Redis，连接会被立即拒绝
    response = asyncio.run(_get_readyz(temp_database_url, "redis://127.0.0.1:1/0"))
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "error"}


async def test_worker_runs_heartbeat_job(redis_url: str) -> None:
    redis_settings = RedisSettings.from_dsn(redis_url)
    pool = await create_pool(redis_settings)
    try:
        job = await pool.enqueue_job("heartbeat")
        assert job is not None
        worker = Worker(
            functions=[heartbeat], redis_settings=redis_settings, burst=True, handle_signals=False
        )
        try:
            await worker.async_run()
        finally:
            await worker.close()
        assert await job.result(timeout=5) == "ok"
        assert await pool.get(WORKER_HEARTBEAT_KEY) is not None  # 运维页读取的心跳
    finally:
        await pool.delete(WORKER_HEARTBEAT_KEY)
        await pool.aclose()


def test_migration_0041_assigns_allowed_mailboxes_to_existing_agents(
    temp_database_url: str,
) -> None:
    """T-0132：0041 把“允许智能体访问”的邮箱分配给同一工作空间现有的全部实例，删掉 agents_allowed；
    降级时有分配的邮箱 agents_allowed = true，其余 false。"""
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0040")
    asyncio.run(_seed_mailboxes(temp_database_url))

    command.upgrade(config, "0041")
    pairs = asyncio.run(
        _fetch_one(
            temp_database_url,
            "SELECT array_agg(i.name || '/' || a.email ORDER BY i.name, a.email) AS pairs"
            " FROM workspace.agent_mail_accounts m"
            " JOIN workspace.agent_instances i ON i.id = m.instance_id"
            " JOIN workspace.mail_accounts a ON a.id = m.account_id",
        )
    )["pairs"]
    assert sorted(pairs) == sorted(["甲/allowed@gmail.com", "乙/allowed@gmail.com"])
    columns = asyncio.run(
        _fetch_one(
            temp_database_url,
            "SELECT array_agg(column_name::text) AS names FROM information_schema.columns"
            " WHERE table_schema = 'workspace' AND table_name = 'mail_accounts'",
        )
    )["names"]
    assert "agents_allowed" not in columns

    command.downgrade(config, "0040")
    allowed = asyncio.run(
        _fetch_one(
            temp_database_url,
            "SELECT array_agg(email || '=' || agents_allowed::text ORDER BY email) AS rows"
            " FROM workspace.mail_accounts",
        )
    )["rows"]
    assert allowed == ["allowed@gmail.com=true", "closed@gmail.com=false", "denied@gmail.com=false"]
    assert "agent_mail_accounts" not in asyncio.run(_tables(temp_database_url))

    command.upgrade(config, "head")
    command.check(config)


async def _seed_mailboxes(database_url: str) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        user = await conn.fetchval(
            "INSERT INTO workspace.users (email, display_name, password_hash)"
            " VALUES ('a@example.com', 'A', 'x') RETURNING id"
        )
        workspace = await conn.fetchval(
            "INSERT INTO workspace.workspaces (name, owner_id) VALUES ('W', $1) RETURNING id", user
        )
        for name in ("甲", "乙"):
            await conn.execute(
                "INSERT INTO workspace.agent_instances"
                " (workspace_id, name, network_mode, im_user_id, im_nickname)"
                " VALUES ($1, $2, 'public', $3, $2)",
                workspace,
                name,
                f"oc_{name}",
            )
        for email, allowed, status in (
            ("allowed@gmail.com", True, "active"),
            ("denied@gmail.com", False, "active"),
            ("closed@gmail.com", True, "disconnected"),
        ):
            await conn.execute(
                "INSERT INTO workspace.mail_accounts (workspace_id, email, agents_allowed, status)"
                " VALUES ($1, $2, $3, $4)",
                workspace,
                email,
                allowed,
                status,
            )
    finally:
        await conn.close()
