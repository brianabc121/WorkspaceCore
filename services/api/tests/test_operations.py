"""运维（T-0035）：api 自身的指标、Prometheus 客户端、服务状态与预设指标视图。

覆盖 Sprint 5 契约第 3 节：指标标签不含原始路径与查询串（S5-05）、/metrics 不进 OpenAPI、
固定的 PromQL、Prometheus 不可达时返回缓存并标为 stale（不以 0 冒充）、没有部署时为 not_configured、
Worker 心跳、集成状态的映射，以及首页的 operations 模块。
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.deps import authenticated, get_db
from app.core.config import Settings
from app.domain.operations import VIEWS, WORKER_HEARTBEAT_KEY, integration_service, query_for
from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy
from app.integrations.prometheus import PrometheusClient
from app.main import create_app
from app.models import Integration
from app.schemas.integrations import SyncState
from tests.helpers import OWNER, ClientFactory, run_sql

# ---------------------------------------------------------------- api 自身的指标


async def test_metrics_use_route_templates_and_never_raw_paths() -> None:
    app = create_app(Settings(app_env="test", log_level="WARNING"))
    # 只看请求形态：不需要数据库与登录（路径参数校验失败返回 422）
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[authenticated] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        raw_id = "not-a-uuid-private-0123"
        await client.get(f"/api/v1/workspaces/{raw_id}?token=tk_should_not_appear")
        await client.get("/api/v1/no-such-route/abc123")
        await client.get("/healthz")
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert (
        'workspace_http_requests_total{method="GET",route="/api/v1/workspaces/{workspace_id}",'
        'status_class="4xx"} 1.0' in body
    )
    assert 'route="unmatched"' in body
    assert "workspace_http_request_duration_seconds_bucket" in body
    for leaked in (raw_id, "tk_should_not_appear", "abc123", "no-such-route"):
        assert leaked not in body
    assert 'route="/healthz"' not in body  # 探针不计入
    assert "/metrics" not in app.openapi()["paths"]


async def test_access_log_can_record_only_the_route_template() -> None:
    app = create_app(Settings(app_env="test", log_level="INFO", log_request_path="route"))
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[authenticated] = lambda: None
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Collect()
    request_logger = logging.getLogger("app.request")
    request_logger.addHandler(handler)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/api/v1/workspaces/private-id-123?q=private")
    finally:
        request_logger.removeHandler(handler)
    paths = [r.__dict__["path"] for r in records if r.getMessage() == "request"]
    assert paths == ["/api/v1/workspaces/{workspace_id}"]


def test_monitoring_urls_must_be_plain_http_urls() -> None:
    for url in ["ftp://prometheus:9090", "http://admin:pw@prometheus:9090", "prometheus"]:
        with pytest.raises(ValidationError):
            Settings(prometheus_url=url)
    settings = Settings(prometheus_url="http://prometheus:9090/", grafana_url="")
    assert settings.prometheus_url == "http://prometheus:9090"


# ---------------------------------------------------------------- Prometheus 客户端


def _prometheus(handler: Callable[[httpx.Request], httpx.Response]) -> PrometheusClient:
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({"prometheus"})),
        read_retries=0,
        transport=httpx.MockTransport(handler),
    )
    return PrometheusClient("http://prometheus:9090", http)


def _matrix(*series: tuple[dict[str, str], list[list[object]]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": labels, "values": values} for labels, values in series],
        },
    }


async def test_prometheus_matrix_values_are_parsed_and_nan_becomes_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        return httpx.Response(
            200, json=_matrix(({"job": "workspace-api"}, [[1.5, "0.25"], [61.5, "NaN"]]))
        )

    series = await _prometheus(handler).query_range("up", start=0, end=60, step=60)
    assert series[0].labels == {"job": "workspace-api"}
    assert series[0].points == [(1.5, 0.25), (61.5, None)]


@pytest.mark.parametrize(
    ("response", "kind", "reason"),
    [
        (
            httpx.Response(400, json={"status": "error", "errorType": "bad_data"}),
            "VALIDATION_FAILED",
            "bad_query",
        ),
        (httpx.Response(422, json={"status": "error"}), "VALIDATION_FAILED", "bad_query"),
        (httpx.Response(503, text="busy"), "UNAVAILABLE", "unavailable"),
        (httpx.Response(200, text="<html>"), "UNAVAILABLE", "invalid_response"),
        (
            httpx.Response(200, json={"status": "success", "data": {"resultType": "vector"}}),
            "UNAVAILABLE",
            "invalid_response",
        ),
    ],
)
async def test_prometheus_errors_are_classified(
    response: httpx.Response, kind: str, reason: str
) -> None:
    client = _prometheus(lambda request: response)
    with pytest.raises(ConnectorError) as raised:
        await client.query_range("up", start=0, end=60, step=60)
    assert (raised.value.kind, raised.value.reason) == (kind, reason)
    assert "busy" not in str(raised.value)


def test_preset_queries_are_fixed_and_widen_the_window_with_the_step() -> None:
    assert query_for("request_rate", 60) == "sum(rate(workspace_http_requests_total[300s]))"
    assert "[3600s]" in query_for("latency_p95", 3600)
    assert query_for("services_up", 60) == 'up{job=~"workspace-.*"}'
    assert 'status_class="5xx"' in query_for("error_rate", 60)
    assert "or vector(0)" in query_for("error_rate", 60)  # 没有 5xx 时为 0，没有请求时为 null


def _integration(status: str) -> Integration:
    return Integration(id=uuid.uuid4(), provider="vikunja", name="Vikunja", status=status)


@pytest.mark.parametrize(
    ("status", "sync", "expected"),
    [
        ("active", SyncState(status="ok", as_of=datetime.now(UTC)), ("ok", None)),
        (
            "unavailable",
            SyncState(status="stale", as_of=None, reason="source_unavailable"),
            ("degraded", "source_unavailable"),
        ),
        (
            "active",
            SyncState(status="stale", as_of=None, reason="rate_limited"),
            ("degraded", "rate_limited"),
        ),
        (
            "unauthorized",
            SyncState(status="unauthorized", as_of=None, reason="token_rejected"),
            ("down", "token_rejected"),
        ),
        (
            "unavailable",
            SyncState(status="unavailable", as_of=None, reason="not_synced"),
            ("down", "not_synced"),
        ),
        (
            "active",
            SyncState(status="unavailable", as_of=None, reason="not_synced"),
            ("unknown", "not_synced"),
        ),
        (
            "pending",
            SyncState(status="unavailable", as_of=None, reason="not_synced"),
            ("unknown", "not_tested"),
        ),
        (
            "disabled",
            SyncState(status="not_configured", as_of=None),
            ("not_configured", "disabled"),
        ),
    ],
)
def test_integration_status_mapping(
    status: str, sync: SyncState, expected: tuple[str, str | None]
) -> None:
    item = integration_service(_integration(status), sync)
    assert (item.status, item.reason) == expected
    assert item.id.startswith("integration:") and item.kind == "integration"


# ---------------------------------------------------------------- 接口（真实 PostgreSQL + Redis）


@dataclass
class FakeMonitoring:
    """模拟 Prometheus 与 Grafana；down=True 时连接失败。"""

    down: bool = False
    queries: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.host == "grafana":
            return httpx.Response(200, json={"database": "ok", "version": "13.2.5"})
        if request.url.path == "/-/ready":
            return httpx.Response(200, text="Prometheus Server is Ready.\n")
        query = parse_qs(urlsplit(str(request.url)).query)
        self.queries.append(query["query"][0])
        start, step = float(query["start"][0]), int(query["step"][0])
        labels = {"job": "workspace-api"} if query["query"][0].startswith("up") else {}
        return httpx.Response(200, json=_matrix((labels, [[start, "1"], [start + step, "NaN"]])))


MONITORING = {
    "prometheus_url": "http://prometheus:9090",
    "grafana_url": "http://grafana:3000",
    "grafana_dashboard_url": "http://localhost:3000/d/workspace-overview/my-workspace",
}


@dataclass
class Ops:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    fake: FakeMonitoring

    @property
    def app(self) -> FastAPI:
        return cast(FastAPI, cast(httpx.ASGITransport, self.client._transport).app)

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(
            path, params=params, headers={"X-Workspace-Id": self.workspace_id}
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def services(self) -> dict[str, dict[str, Any]]:
        data = await self.get("/api/v1/operations/services")
        return {item["id"]: item for item in data["items"]}


def _ops_fixture(**overrides: Any) -> Callable[..., AsyncIterator[Ops]]:
    async def fixture(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Ops]:
        async with make_client(service_status_cache_seconds=0, **overrides) as client:
            setup = await client.post("/api/v1/setup", json=OWNER)
            assert setup.status_code == 201
            fake = FakeMonitoring()
            app = cast(httpx.ASGITransport, client._transport).app
            app.state.connectors.transport = httpx.MockTransport(fake.handler)  # type: ignore[attr-defined]
            yield Ops(client, setup.json()["workspaces"][0]["id"], migrated_database_url, fake)

    return fixture


ops = pytest.fixture(name="ops")(_ops_fixture())
monitored = pytest.fixture(name="monitored")(_ops_fixture(**MONITORING))


@pytest.mark.integration
async def test_services_without_monitoring(ops: Ops) -> None:
    data = await ops.get("/api/v1/operations/services")
    ids = [item["id"] for item in data["items"]]
    assert ids == ["api", "worker", "postgres", "redis", "prometheus", "grafana", "n8n"]
    items = {item["id"]: item for item in data["items"]}
    assert items["api"]["status"] == "ok"
    assert items["postgres"]["status"] == "ok" and items["redis"]["status"] == "ok"
    assert (items["worker"]["status"], items["worker"]["reason"]) == ("unknown", "no_heartbeat")
    for name in ("prometheus", "grafana", "n8n"):
        assert items[name]["status"] == "not_configured", name
    assert all(item["kind"] in {"core", "monitoring", "integration"} for item in data["items"])
    assert "9090" not in json.dumps(data)  # 不透露内部地址

    redis = ops.app.state.redis
    await redis.set(WORKER_HEARTBEAT_KEY, datetime.now(UTC).isoformat())
    assert (await ops.services())["worker"]["status"] == "ok"
    await redis.set(WORKER_HEARTBEAT_KEY, (datetime.now(UTC) - timedelta(minutes=10)).isoformat())
    worker = (await ops.services())["worker"]
    assert (worker["status"], worker["reason"]) == ("down", "heartbeat_overdue")

    overview = await ops.get("/api/v1/dashboard/overview")
    assert overview["modules"]["operations"]["status"] == "unavailable"
    assert overview["modules"]["operations"]["reason"] == "service_down"
    await redis.set(WORKER_HEARTBEAT_KEY, datetime.now(UTC).isoformat())
    overview = await ops.get("/api/v1/dashboard/overview")
    assert overview["modules"]["operations"]["status"] == "ok"

    metrics = await ops.get("/api/v1/operations/metrics")
    assert metrics["sync"]["status"] == "not_configured"
    assert metrics["views"] == [] and metrics["grafana_url"] is None


@pytest.mark.integration
async def test_services_list_the_workspace_integrations(ops: Ops) -> None:
    integration_id = await run_sql(
        ops.database_url,
        "INSERT INTO workspace.integrations (workspace_id, provider, name, base_url, public_url,"
        " status) VALUES ($1, 'vikunja', 'Vikunja', 'http://vikunja:3456',"
        " 'http://localhost:3456', 'unauthorized') RETURNING id",
        uuid.UUID(ops.workspace_id),
    )
    items = await ops.services()
    item = items[f"integration:{integration_id}"]
    assert item["provider"] == "vikunja" and item["kind"] == "integration"
    assert (item["status"], item["reason"]) == ("down", "token_rejected")
    assert "vikunja:3456" not in json.dumps(item)


@pytest.mark.integration
async def test_metrics_send_only_preset_queries_and_degrade_to_stale(monitored: Ops) -> None:
    items = await monitored.services()
    assert items["prometheus"]["status"] == "ok" and items["grafana"]["status"] == "ok"

    data = await monitored.get(
        "/api/v1/operations/metrics", range="6h", views=["latency_p95", "services_up"]
    )
    assert data["sync"]["status"] == "ok" and data["step_seconds"] == 300
    assert data["grafana_url"] == MONITORING["grafana_dashboard_url"]
    assert [view["id"] for view in data["views"]] == ["latency_p95", "services_up"]
    assert monitored.fake.queries == [query_for("latency_p95", 300), query_for("services_up", 300)]
    up = data["views"][1]
    assert up["unit"] == "count" and up["series"][0]["label"] == "api"
    latency = data["views"][0]
    assert latency["series"][0]["label"] == ""
    assert [p["value"] for p in latency["series"][0]["points"]] == [1.0, None]  # NaN 不当成 0
    as_of = data["sync"]["as_of"]

    monitored.fake.down = True
    stale = await monitored.get(
        "/api/v1/operations/metrics", range="6h", views=["latency_p95", "services_up"]
    )
    assert stale["sync"]["status"] == "stale" and stale["sync"]["as_of"] == as_of
    assert stale["sync"]["reason"] == "connect_failed"
    assert stale["views"] == data["views"]
    never = await monitored.get("/api/v1/operations/metrics", range="24h")
    assert never["sync"]["status"] == "unavailable" and never["views"] == []
    assert (await monitored.services())["prometheus"]["status"] == "down"


@pytest.mark.integration
async def test_default_metrics_request_returns_every_preset_view(monitored: Ops) -> None:
    data = await monitored.get("/api/v1/operations/metrics")
    assert [view["id"] for view in data["views"]] == list(VIEWS)
    assert data["range"] == "1h" and data["step_seconds"] == 60
    assert all("{" not in q or "workspace" in q for q in monitored.fake.queries)


@pytest.mark.integration
async def test_operations_require_a_session(ops: Ops) -> None:
    async with httpx.AsyncClient(
        transport=ops.client._transport, base_url=str(ops.client.base_url)
    ) as anonymous:
        response = await anonymous.get(
            "/api/v1/operations/services", headers={"X-Workspace-Id": ops.workspace_id}
        )
    assert response.status_code == 401
