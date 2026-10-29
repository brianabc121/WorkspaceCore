"""需要真实 PostgreSQL / Redis 的测试（见 conftest.py 的说明）。"""

import asyncio

import asyncpg
import httpx
import pytest
from alembic import command
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

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
    finally:
        await pool.aclose()
