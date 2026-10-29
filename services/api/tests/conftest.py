"""测试夹具。

单元测试不需要外部服务。标记为 integration 的测试需要真实的 PostgreSQL / Redis：
- TEST_DATABASE_URL：有建库权限的连接串，例如 postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
- TEST_REDIS_URL：例如 redis://localhost:6379/15
未设置时跳过；设置 REQUIRE_INTEGRATION=1（CI 中）时改为失败，避免集成测试被悄悄跳过。
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app
from tests.helpers import asyncpg_dsn


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
