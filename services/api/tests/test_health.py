import asyncio
import logging

import httpx
from fastapi import FastAPI

from app.api.health import ReadinessCheck, get_readiness_checks
from app.core.config import Settings
from app.core.middleware import REQUEST_ID_HEADER


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("db.internal:5432 password=secret")


async def _hang() -> None:
    await asyncio.sleep(10)


def _use_checks(app: FastAPI, checks: dict[str, ReadinessCheck]) -> None:
    app.dependency_overrides[get_readiness_checks] = lambda: checks


async def test_healthz_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_all_ok(app: FastAPI, client: httpx.AsyncClient) -> None:
    _use_checks(app, {"database": _ok, "redis": _ok})
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_readyz_reports_failure_without_leaking_details(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _use_checks(app, {"database": _fail, "redis": _ok})
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "error", "redis": "ok"},
    }
    assert "secret" not in response.text


async def test_readyz_times_out_slow_dependency(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.settings = app.state.settings.model_copy(update={"readiness_timeout_seconds": 0.05})
    _use_checks(app, {"database": _ok, "redis": _hang})
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "error"}


async def test_readyz_without_registered_checks_is_not_ready(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _use_checks(app, {})
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {}}


def test_log_level_is_case_insensitive() -> None:
    assert Settings(log_level="info").log_level == "INFO"


async def test_request_id_is_generated(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers[REQUEST_ID_HEADER].startswith("req_")


async def test_valid_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={REQUEST_ID_HEADER: "abc-123"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_invalid_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={REQUEST_ID_HEADER: "bad id\nwith newline"})
    assert response.headers[REQUEST_ID_HEADER].startswith("req_")


async def test_unknown_route_uses_unified_error_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_unhandled_error_returns_500_without_details(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "internal detail" not in response.text
    assert response.headers[REQUEST_ID_HEADER].startswith("req_")


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def test_probe_requests_are_logged_at_debug_unless_failing(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    request_logger = logging.getLogger("app.request")
    collect = _Collect()
    request_logger.addHandler(collect)
    previous_level = request_logger.level
    request_logger.setLevel(logging.DEBUG)
    try:
        _use_checks(app, {"database": _ok})
        await client.get("/healthz")
        await client.get("/readyz")
        await client.get("/api/v1/nope")
        _use_checks(app, {"database": _fail})
        await client.get("/readyz")
    finally:
        request_logger.removeHandler(collect)
        request_logger.setLevel(previous_level)

    levels = [(r.__dict__["path"], r.__dict__["status"], r.levelno) for r in collect.records]
    assert levels == [
        ("/healthz", 200, logging.DEBUG),
        ("/readyz", 200, logging.DEBUG),
        ("/api/v1/nope", 404, logging.INFO),
        ("/readyz", 503, logging.INFO),
    ]
