"""测试共用的小工具。"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.domain import security


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """把 SQLAlchemy 的 postgresql+asyncpg:// 地址转换成 asyncpg 能直接使用的 DSN。"""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def settings_for(database_url: str, redis_url: str) -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        readiness_timeout_seconds=1.0,
    )


API_DIR = Path(__file__).resolve().parent.parent


def alembic_config(database_url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


# ---------------------------------------------------------------- 接口集成测试

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

BASE_URL = "http://testserver"
SETUP_TOKEN = "test-setup-token-0123456789"  # noqa: S105
OWNER = {
    "setup_token": SETUP_TOKEN,
    "email": "Owner@Example.com",
    "password": "correct horse battery staple",
    "display_name": "Owner",
    "workspace_name": "我的工作空间",
}


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("ws_csrf") or ""}


async def run_sql(database_url: str, sql: str, *args: Any) -> Any:
    """执行一条 SQL，返回第一行第一列（没有结果时为 None）。"""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


BOB = {"email": "bob@example.com", "password": "bob password 123456"}


async def add_user(database_url: str, email: str, password: str) -> uuid.UUID:
    user_id: uuid.UUID = await run_sql(
        database_url,
        "INSERT INTO workspace.users (email, password_hash, display_name)"
        " VALUES ($1, $2, 'Bob') RETURNING id",
        email,
        security.hash_password(password),
    )
    return user_id


async def add_member(
    database_url: str, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> None:
    await run_sql(
        database_url,
        "INSERT INTO workspace.workspace_members (workspace_id, user_id, role) VALUES ($1, $2, $3)",
        workspace_id,
        user_id,
        role,
    )


async def add_workspace(database_url: str, owner_id: uuid.UUID, name: str) -> uuid.UUID:
    workspace_id: uuid.UUID = await run_sql(
        database_url,
        "INSERT INTO workspace.workspaces (name, owner_id) VALUES ($1, $2) RETURNING id",
        name,
        owner_id,
    )
    await add_member(database_url, workspace_id, owner_id, "owner")
    return workspace_id
