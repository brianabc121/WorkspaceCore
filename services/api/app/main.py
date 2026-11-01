"""FastAPI 应用入口：`uvicorn app.main:app`。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import __version__
from app.api import agent_api, health, mcp
from app.api import v1 as api_v1
from app.api.deps import deployment_health
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.metrics import CONTENT_TYPE, HttpMetrics
from app.core.middleware import install_middleware
from app.core.resources import create_engine, create_redis, create_stream_redis
from app.domain import events, mcp_tools, overview, stream
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialBox
from app.domain.operations import ServiceCache
from app.integrations.http import CircuitBreaker
from app.jobs.queue import ArqJobQueue

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    redis = create_redis(settings)
    stream_redis = create_stream_redis(settings)
    # 会话提交后由 EventBus 发布数据失效事件（SSE，Sprint 5 契约第 5 节）
    bus = events.EventBus(redis)
    app.state.sessionmaker = async_sessionmaker(
        engine, expire_on_commit=False, info=bus.session_info()
    )
    app.state.redis = redis
    app.state.stream_redis = stream_redis
    app.state.events = bus
    app.state.jobs = ArqJobQueue(redis)
    app.state.readiness_checks = {
        "database": health.database_check(engine),
        "redis": health.redis_check(redis),
    }
    # MCP 工具（V2 OC-5）：SDK 的会话管理器在后台任务里运行，每个应用实例一个
    mcp_runner = mcp_tools.McpRunner()
    await mcp_runner.start()
    app.state.mcp = mcp_runner.manager
    logger.info("api started", extra={"app_env": settings.app_env, "version": __version__})
    try:
        yield
    finally:
        app.state.mcp = None
        await mcp_runner.stop()
        await bus.drain()
        await stream_redis.aclose()
        await redis.aclose()
        await engine.dispose()
        logger.info("api stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.require_api_secrets()
    if settings.credential_encryption_key is not None:
        # 密钥格式错误时启动失败，而不是等到第一次使用凭证时才发现
        CredentialBox.from_settings(settings)
    configure_logging(settings.log_level)
    docs_enabled = not settings.is_production
    app = FastAPI(
        title="My Workspace API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if docs_enabled else None,
        docs_url="/api/v1/docs" if docs_enabled else None,
        redoc_url=None,
        # operationId 直接用函数名（要求全局唯一），前端生成的类型更易读
        generate_unique_id_function=lambda route: route.name,
    )
    app.state.settings = settings
    app.state.http_metrics = HttpMetrics()
    app.state.service_cache = ServiceCache()
    app.state.stream_limiter = stream.StreamLimiter(
        per_user=settings.sse_max_connections_per_user, total=settings.sse_max_connections
    )
    # 首页聚合的模块数据来源；测试可以替换成会失败或超时的 provider
    app.state.module_providers = overview.default_providers(
        lambda: app.state.sessionmaker(),
        settings.sync_stale_after_seconds,
        lambda: deployment_health(app),
    )
    app.state.overview_gate = asyncio.Semaphore(settings.dashboard_overview_concurrency)
    # Connector 共用的出站策略与熔断状态；测试可以替换 transport（httpx.MockTransport）
    app.state.connectors = ConnectorRuntime(settings, CircuitBreaker())
    app.state.readiness_checks = {}
    app.state.mcp = None
    install_error_handlers(app)
    install_middleware(app)
    app.include_router(health.router)
    app.include_router(api_v1.router)
    # V2 实例接口：只经接入入口到达（Caddy 管理员入口只转发 /api/*）
    app.include_router(agent_api.router)
    app.include_router(agent_api.gateway)
    app.include_router(mcp.router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        # 只在内部网络供 Prometheus 抓取：Caddy 不转发 /metrics（Sprint 5 契约第 3.2 节）
        body = request.app.state.http_metrics.render()
        return Response(content=body, media_type=CONTENT_TYPE)

    return app


app = create_app()
