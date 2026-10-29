"""测试夹具。

单元测试不需要外部服务。标记为 integration 的测试需要真实的 PostgreSQL / Redis：
- TEST_DATABASE_URL：有建库权限的连接串，例如 postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
- TEST_REDIS_URL：例如 redis://localhost:6379/15
未设置时跳过；设置 REQUIRE_INTEGRATION=1（CI 中）时改为失败，避免集成测试被悄悄跳过。
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app
from tests.helpers import BASE_URL, SETUP_TOKEN, ClientFactory, alembic_config, asyncpg_dsn


def _integration_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        message = f"未设置 {name}，跳过集成测试"
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            pytest.fail(message.replace("跳过", "但 REQUIRE_INTEGRATION=1 要求运行"))
        pytest.skip(message)
    return value


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env="test", log_level="WARNING")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def redis_url() -> str:
    return _integration_env("TEST_REDIS_URL")


@pytest.fixture
def temp_database_url() -> Iterator[str]:
    """在测试服务器上新建一个空数据库，测试结束后删除。"""
    admin_url = _integration_env("TEST_DATABASE_URL")
    name = f"ws_test_{uuid4().hex[:12]}"
    admin_dsn = asyncpg_dsn(admin_url)

    async def execute(sql: str) -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(execute(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def migrated_database_url(temp_database_url: str) -> str:
    command.upgrade(alembic_config(temp_database_url), "head")
    return temp_database_url


@pytest.fixture
def make_client(migrated_database_url: str, redis_url: str) -> ClientFactory:
    """接口集成测试：真实 PostgreSQL（已迁移）+ Redis。每次调用得到独立的客户端（各有 Cookie）。"""

    @asynccontextmanager
    async def factory(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
        options: dict[str, Any] = {
            "app_env": "test",
            "log_level": "WARNING",
            "database_url": SecretStr(migrated_database_url),
            "redis_url": SecretStr(redis_url),
            "setup_token": SecretStr(SETUP_TOKEN),
            "public_base_url": BASE_URL,
            "login_max_failures_per_account": 3,
            "login_max_failures_per_ip": 50,
            "login_failure_delay_seconds": 0,
        }
        options.update(overrides)
        app = create_app(Settings(**options))
        async with app.router.lifespan_context(app):
            # 清空测试用 Redis 库里的限流计数；同一个测试里的多个客户端请在开头一起创建
            await app.state.redis.flushdb()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
                yield client

    return factory
