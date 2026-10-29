"""测试共用的小工具。"""

from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.core.config import Settings


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
