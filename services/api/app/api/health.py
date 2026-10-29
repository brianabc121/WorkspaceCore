"""/healthz 与 /readyz（见 docs/decisions/2026-10-29-sprint-0-contracts.md 第 3 节）。

/healthz 只表示进程存活；/readyz 并发检查各依赖，任一失败返回 503。
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.schemas.health import CheckStatus, HealthResponse, ReadinessResponse

ReadinessCheck = Callable[[], Awaitable[None]]

router = APIRouter(tags=["health"])
logger = logging.getLogger("app.health")


def database_check(engine: AsyncEngine) -> ReadinessCheck:
    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return check


def redis_check(client: Redis) -> ReadinessCheck:
    async def check() -> None:
        # redis-py 的类型标注把 ping() 写成“可能同步、可能异步”，这里按实际返回处理
        result = client.ping()
        if inspect.isawaitable(result):
            await result

    return check


def get_readiness_checks(request: Request) -> dict[str, ReadinessCheck]:
    checks: dict[str, ReadinessCheck] = request.app.state.readiness_checks
    return checks


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def _run_check(name: str, check: ReadinessCheck, limit_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(limit_seconds):
            await check()
    except Exception as exc:
        # 只记录异常类型：异常信息里可能带有主机名、用户名等连接细节
        logger.warning(
            "readiness check failed", extra={"check": name, "error_type": type(exc).__name__}
        )
        return "error"
    return "ok"


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "至少一项依赖不可用"}},
)
async def readyz(
    response: Response,
    checks: Annotated[dict[str, ReadinessCheck], Depends(get_readiness_checks)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ReadinessResponse:
    names = list(checks)
    statuses = await asyncio.gather(
        *(_run_check(name, checks[name], settings.readiness_timeout_seconds) for name in names)
    )
    results = dict(zip(names, statuses, strict=True))
    # 没有注册任何检查（例如 lifespan 未运行）时不能报告就绪
    ready = bool(statuses) and all(status == "ok" for status in statuses)
    if not ready:
        response.status_code = 503
    return ReadinessResponse(status="ok" if ready else "unavailable", checks=results)
