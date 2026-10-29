"""FastAPI 应用入口：`uvicorn app.main:app`。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.api import v1 as api_v1
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import install_middleware
from app.core.resources import create_engine, create_redis

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    redis = create_redis(settings)
    app.state.readiness_checks = {
        "database": health.database_check(engine),
        "redis": health.redis_check(redis),
    }
    logger.info("api started", extra={"app_env": settings.app_env, "version": __version__})
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()
        logger.info("api stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    docs_enabled = not settings.is_production
    app = FastAPI(
        title="My Workspace API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if docs_enabled else None,
        docs_url="/api/v1/docs" if docs_enabled else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.readiness_checks = {}
    install_error_handlers(app)
    install_middleware(app)
    app.include_router(health.router)
    app.include_router(api_v1.router)
    return app


app = create_app()
