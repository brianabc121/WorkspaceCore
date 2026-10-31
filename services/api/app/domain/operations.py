"""运维：服务状态与预设指标（Sprint 5 契约第 3 节；核验 ADR 第 3、4 节）。

- 服务状态：api 由响应本身证明可用；PostgreSQL / Redis 与 /readyz 相同；
  Worker 看它每分钟写一次的心跳；Prometheus / Grafana 用各自的就绪接口；
  集成沿用集成状态与最近一次同步，不在请求里访问第三方。
  部署级的检查结果在进程内缓存 service_status_cache_seconds 秒，每项检查都有超时。
- 预设指标：只发送下面 VIEWS 里固定的 PromQL，浏览器只能选视图 ID 与时间范围。
  查询成功的结果写进 Redis；Prometheus 不可达时返回最近一次的结果并标为 stale，
  没有就返回空（unavailable），不以 0 冒充。
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import github_sync
from app.domain.connectors import ConnectorRuntime, sync_state
from app.domain.policy import WorkspaceAccess
from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy, host_of
from app.integrations.prometheus import PrometheusClient
from app.models import Integration
from app.repositories import integrations as integration_repo
from app.schemas.integrations import SyncState
from app.schemas.operations import (
    MetricPoint,
    MetricRange,
    MetricSeries,
    MetricsOut,
    MetricUnit,
    MetricViewId,
    MetricViewOut,
    ServiceKind,
    ServiceState,
    ServiceStatusList,
    ServiceStatusOut,
)

logger = logging.getLogger("app.operations")

WORKER_HEARTBEAT_KEY = "ops:worker:heartbeat"
_METRICS_CACHE_KEY = "ops:metrics:{range}:{view}"
# 最近一次成功的指标结果保留多久（Prometheus 长时间不可用时，太旧的数据不再展示）
_METRICS_CACHE_TTL_SECONDS = 7 * 24 * 3600
ReadinessCheck = Callable[[], Awaitable[None]]

# 时间范围 → (秒数, 步长)；契约第 3.2 节
RANGES: dict[MetricRange, tuple[int, int]] = {
    "1h": (3600, 60),
    "6h": (6 * 3600, 300),
    "24h": (24 * 3600, 900),
    "7d": (7 * 24 * 3600, 3600),
}

_REQUESTS = "workspace_http_requests_total"
_BUCKETS = "workspace_http_request_duration_seconds_bucket"


def _latency(quantile: str) -> str:
    return f"histogram_quantile({quantile}, sum by (le) (rate({_BUCKETS}[{{window}}])))"


@dataclass(frozen=True)
class ViewSpec:
    title: str
    unit: MetricUnit
    # {window} 为 rate 的窗口：不小于 5 分钟，也不小于步长（否则点与点之间的请求会漏掉）
    query: str
    # 多序列视图：用这个标签作为序列名；单序列视图为 None
    label: str | None = None


VIEWS: dict[MetricViewId, ViewSpec] = {
    "request_rate": ViewSpec(
        "请求速率", "requests_per_second", f"sum(rate({_REQUESTS}[{{window}}]))"
    ),
    "latency_p50": ViewSpec("延迟 P50", "seconds", _latency("0.5")),
    "latency_p95": ViewSpec("延迟 P95", "seconds", _latency("0.95")),
    "latency_p99": ViewSpec("延迟 P99", "seconds", _latency("0.99")),
    # 没有 5xx 时分子是空向量，用 vector(0) 补成 0；没有请求时分母为 0，结果为 NaN → null
    "error_rate": ViewSpec(
        "5xx 占比",
        "ratio",
        f'(sum(rate({_REQUESTS}{{{{status_class="5xx"}}}}[{{window}}])) or vector(0))'
        f" / sum(rate({_REQUESTS}[{{window}}]))",
    ),
    "services_up": ViewSpec("服务在线", "count", 'up{{job=~"workspace-.*"}}', label="job"),
}


def window_for(step: int) -> str:
    return f"{max(step, 300)}s"


def query_for(view: MetricViewId, step: int) -> str:
    return VIEWS[view].query.format(window=window_for(step))


# ---------------------------------------------------------------- 服务状态


@dataclass
class ServiceCache:
    """部署级检查的进程内缓存（每个 api 进程一份）。"""

    entries: dict[str, tuple[float, ServiceStatusOut]] = field(default_factory=dict)

    def get(self, key: str) -> ServiceStatusOut | None:
        entry = self.entries.get(key)
        if entry is None or entry[0] < time.monotonic():
            return None
        return entry[1]

    def put(self, key: str, value: ServiceStatusOut, ttl: float) -> None:
        if ttl > 0:
            self.entries[key] = (time.monotonic() + ttl, value)


_NAMES = {
    "api": "API",
    "worker": "Worker",
    "postgres": "PostgreSQL",
    "redis": "Redis",
    "prometheus": "Prometheus",
    "grafana": "Grafana",
}


def _status(
    service_id: str,
    kind: ServiceKind,
    state: ServiceState,
    *,
    reason: str | None = None,
    checked_at: datetime | None = None,
    latency_ms: int | None = None,
    name: str | None = None,
    provider: str | None = None,
) -> ServiceStatusOut:
    return ServiceStatusOut(
        id=service_id,
        name=name or _NAMES[service_id],
        kind=kind,
        provider=provider,
        status=state,
        reason=reason,
        checked_at=checked_at,
        latency_ms=latency_ms,
    )


def _reason_of(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectorError):
        return error.reason or "unreachable"
    return "unreachable"


def integration_service(integration: Integration, sync: SyncState) -> ServiceStatusOut:
    """集成的状态（契约第 3.1 节）：沿用集成状态与同步新鲜度，不访问第三方。"""
    state: ServiceState
    reason = sync.reason
    if integration.status == "disabled":
        state, reason = "not_configured", "disabled"
    elif integration.status == "pending":
        state, reason = "unknown", "not_tested"
    elif sync.status == "unauthorized":
        state = "down"
    elif sync.status == "ok":
        state = "ok"
    elif sync.status == "stale" or reason == "rate_limited":
        state = "degraded"
    elif reason == "not_synced" and integration.status == "active":
        state = "unknown"
    else:
        state = "down"
    return _status(
        f"integration:{integration.id}",
        "integration",
        state,
        reason=reason,
        checked_at=sync.as_of,
        name=integration.name,
        provider=integration.provider,
    )


@dataclass
class DeploymentHealth:
    """部署级的检查（与工作空间无关）：核心服务与监控组件。运维页与首页的 operations 模块共用。"""

    redis: Redis
    settings: Settings
    runtime: ConnectorRuntime
    readiness_checks: dict[str, ReadinessCheck]
    cache: ServiceCache

    async def core(self) -> list[ServiceStatusOut]:
        """api、worker、postgres、redis（契约第 3.1 节的顺序）。"""
        api = _status("api", "core", "ok", checked_at=datetime.now(UTC), latency_ms=0)
        worker, postgres, redis = await asyncio.gather(
            self._worker(), self._postgres(), self._redis()
        )
        return [api, worker, postgres, redis]

    async def monitoring(self) -> list[ServiceStatusOut]:
        return list(
            await asyncio.gather(
                self._cached("prometheus", self._prometheus),
                self._cached("grafana", self._grafana),
            )
        )

    async def _cached(
        self, key: str, check: Callable[[], Awaitable[ServiceStatusOut]]
    ) -> ServiceStatusOut:
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        result = await check()
        self.cache.put(key, result, self.settings.service_status_cache_seconds)
        return result

    def _postgres(self) -> Awaitable[ServiceStatusOut]:
        return self._cached("postgres", lambda: self._readiness("postgres", "database"))

    def _redis(self) -> Awaitable[ServiceStatusOut]:
        return self._cached("redis", lambda: self._readiness("redis", "redis"))

    async def _timed(
        self,
        service_id: str,
        kind: ServiceKind,
        probe: Callable[[], Awaitable[None]],
        limit_seconds: float,
    ) -> ServiceStatusOut:
        started = time.perf_counter()
        checked_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(limit_seconds):
                await probe()
        except Exception as error:
            logger.warning(
                "service check failed",
                extra={"service": service_id, "error_type": type(error).__name__},
            )
            return _status(
                service_id,
                kind,
                "down",
                reason=_reason_of(error),
                checked_at=checked_at,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        return _status(
            service_id,
            kind,
            "ok",
            checked_at=checked_at,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    async def _readiness(self, service_id: str, check_name: str) -> ServiceStatusOut:
        check = self.readiness_checks.get(check_name)
        if check is None:
            return _status(service_id, "core", "unknown", reason="not_checked")
        return await self._timed(service_id, "core", check, self.settings.readiness_timeout_seconds)

    async def _worker(self) -> ServiceStatusOut:
        try:
            async with asyncio.timeout(self.settings.readiness_timeout_seconds):
                raw = await self.redis.get(WORKER_HEARTBEAT_KEY)
        except Exception as error:
            logger.warning(
                "worker heartbeat unreadable", extra={"error_type": type(error).__name__}
            )
            return _status("worker", "core", "unknown", reason="heartbeat_unreadable")
        if raw is None:
            return _status("worker", "core", "unknown", reason="no_heartbeat")
        try:
            beat = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else str(raw))
        except ValueError:
            return _status("worker", "core", "unknown", reason="no_heartbeat")
        age = datetime.now(UTC) - beat
        if age > timedelta(seconds=self.settings.worker_heartbeat_stale_seconds):
            return _status("worker", "core", "down", reason="heartbeat_overdue", checked_at=beat)
        return _status("worker", "core", "ok", checked_at=beat)

    def _monitoring_http(self) -> ConnectorHttp:
        """访问内部监控组件：地址来自部署配置，只允许这两个主机名；不重试，失败就显示 down。"""
        hosts = {host_of(u) for u in (self.settings.prometheus_url, self.settings.grafana_url) if u}
        timeout = self.settings.monitoring_timeout_seconds
        return ConnectorHttp(
            policy=OutboundPolicy(frozenset(hosts)),
            connect_timeout=timeout,
            read_timeout=timeout,
            read_retries=0,
            breaker=self.runtime.breaker,
            transport=self.runtime.transport,
        )

    def prometheus_http(self) -> ConnectorHttp:
        """查询指标：允许重试一次（读请求）。"""
        timeout = self.settings.monitoring_timeout_seconds
        return ConnectorHttp(
            policy=OutboundPolicy(frozenset({host_of(self.settings.prometheus_url)})),
            connect_timeout=timeout,
            read_timeout=timeout,
            read_retries=1,
            breaker=self.runtime.breaker,
            transport=self.runtime.transport,
        )

    async def _prometheus(self) -> ServiceStatusOut:
        if not self.settings.prometheus_url:
            return _status("prometheus", "monitoring", "not_configured", reason="not_deployed")
        client = PrometheusClient(self.settings.prometheus_url, self._monitoring_http())
        return await self._timed(
            "prometheus", "monitoring", client.ready, self.settings.monitoring_timeout_seconds
        )

    async def _grafana(self) -> ServiceStatusOut:
        if not self.settings.grafana_url:
            return _status("grafana", "monitoring", "not_configured", reason="not_deployed")
        http = self._monitoring_http()
        url = f"{self.settings.grafana_url}/api/health"

        async def probe() -> None:
            response = await http.request("GET", url, headers={})
            if response.status_code != 200:
                raise ConnectorError(
                    "UNAVAILABLE", "Grafana 未就绪", status=response.status_code, reason="not_ready"
                )

        return await self._timed(
            "grafana", "monitoring", probe, self.settings.monitoring_timeout_seconds
        )


@dataclass
class OperationsService:
    db: AsyncSession
    health: DeploymentHealth

    async def services(self, access: WorkspaceAccess) -> ServiceStatusList:
        """部署级的服务对所有成员相同；集成只列出当前工作空间的（规范第 8 节项目规则 8）。"""
        now = datetime.now(UTC)
        core, monitoring = await asyncio.gather(self.health.core(), self.health.monitoring())
        items = [*core, *monitoring, *await self._integrations(access.workspace.id)]
        # n8n 连接由 T-0037 接入；在那之前按“没有配置”列出（契约第 3.1 节）
        items.append(_status("n8n", "integration", "not_configured", name="n8n", provider="n8n"))
        return ServiceStatusList(items=items, generated_at=now)

    async def _integrations(self, workspace_id: uuid.UUID) -> list[ServiceStatusOut]:
        items: list[ServiceStatusOut] = []
        for integration in await integration_repo.list_for_workspace(self.db, workspace_id):
            resource = github_sync.CURSOR if integration.provider == "github" else "task"
            cursor = await integration_repo.get_cursor(self.db, integration.id, resource)
            state = sync_state(
                integration,
                cursor,
                stale_after_seconds=self.health.settings.sync_stale_after_seconds,
            )
            items.append(integration_service(integration, state))
        return items

    # ---------------------------------------------------------------- 预设指标

    async def metrics(self, range_: MetricRange, views: Sequence[MetricViewId]) -> MetricsOut:
        seconds, step = RANGES[range_]
        requested = list(dict.fromkeys(views)) or list(VIEWS)
        grafana = self.health.settings.grafana_dashboard_url or None
        if not self.health.settings.prometheus_url:
            return MetricsOut(
                range=range_,
                step_seconds=step,
                views=[],
                sync=SyncState(status="not_configured", as_of=None, reason="not_deployed"),
                grafana_url=grafana,
            )
        now = datetime.now(UTC)
        end = (int(now.timestamp()) // step) * step
        client = PrometheusClient(
            self.health.settings.prometheus_url, self.health.prometheus_http()
        )
        window = (end - seconds, end)
        try:
            # 任何一个视图失败就取消其余查询，整体按降级处理（同一时刻的数据要么都新、要么都旧）
            async with asyncio.timeout(self.health.settings.monitoring_timeout_seconds * 2):
                async with asyncio.TaskGroup() as group:
                    tasks = [
                        group.create_task(
                            self._query(client, view, start=window[0], end=window[1], step=step)
                        )
                        for view in requested
                    ]
            results = [task.result() for task in tasks]
        except (TimeoutError, ExceptionGroup) as error:
            cause = error.exceptions[0] if isinstance(error, ExceptionGroup) else error
            logger.warning("metrics query failed", extra={"error_type": type(cause).__name__})
            return await self._from_cache(range_, step, requested, _reason_of(cause), grafana)
        await self._store(range_, results, now)
        return MetricsOut(
            range=range_,
            step_seconds=step,
            views=results,
            sync=SyncState(status="ok", as_of=now),
            grafana_url=grafana,
        )

    @staticmethod
    async def _query(
        client: PrometheusClient, view: MetricViewId, *, start: int, end: int, step: int
    ) -> MetricViewOut:
        spec = VIEWS[view]
        series = await client.query_range(query_for(view, step), start=start, end=end, step=step)
        out: list[MetricSeries] = []
        for item in series:
            label = ""
            if spec.label is not None:
                label = item.labels.get(spec.label, "").removeprefix("workspace-")
            points = [
                MetricPoint(at=datetime.fromtimestamp(ts, UTC), value=value)
                for ts, value in item.points
            ]
            out.append(MetricSeries(label=label, points=points))
        out.sort(key=lambda s: s.label)
        return MetricViewOut(id=view, title=spec.title, unit=spec.unit, series=out)

    async def _store(
        self, range_: MetricRange, results: Sequence[MetricViewOut], at: datetime
    ) -> None:
        try:
            async with self.health.redis.pipeline(transaction=False) as pipe:
                for view in results:
                    payload = json.dumps(
                        {"as_of": at.isoformat(), "view": view.model_dump(mode="json")}
                    )
                    key = _METRICS_CACHE_KEY.format(range=range_, view=view.id)
                    pipe.set(key, payload, ex=_METRICS_CACHE_TTL_SECONDS)
                await pipe.execute()
        except Exception as error:
            # 缓存只用于降级展示，写不进去不影响这次响应
            logger.warning("metrics cache write failed", extra={"error_type": type(error).__name__})

    async def _from_cache(
        self,
        range_: MetricRange,
        step: int,
        views: Sequence[MetricViewId],
        reason: str,
        grafana: str | None,
    ) -> MetricsOut:
        cached: list[MetricViewOut] = []
        as_of: datetime | None = None
        try:
            keys = [_METRICS_CACHE_KEY.format(range=range_, view=view) for view in views]
            raws = await self.health.redis.mget(keys)
        except Exception as error:
            logger.warning("metrics cache read failed", extra={"error_type": type(error).__name__})
            raws = []
        for raw in raws:
            if raw is None:
                continue
            try:
                entry = json.loads(raw)
                view = MetricViewOut.model_validate(entry["view"])
                at = datetime.fromisoformat(entry["as_of"])
            except (ValueError, KeyError, TypeError):
                continue
            cached.append(view)
            as_of = at if as_of is None else min(as_of, at)
        status = "stale" if cached else "unavailable"
        return MetricsOut(
            range=range_,
            step_seconds=step,
            views=cached,
            sync=SyncState(status=status, as_of=as_of, reason=reason),
            grafana_url=grafana,
        )
