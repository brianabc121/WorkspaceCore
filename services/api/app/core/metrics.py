"""api 自身的 Prometheus 指标（Sprint 5 契约第 3.2 节，核验 ADR 第 3.3 节）。

- 标签只有方法、路由模板（例如 `/api/v1/tasks/{task_id}`）与状态码类别：不放原始路径、
  查询串、用户输入或 Token（S5-05）。没有匹配到路由的请求记为 `unmatched`。
- 每个应用实例一份独立的 CollectorRegistry：测试里创建多个应用时互不影响。
- `/metrics` 不经 Caddy 转发（Caddyfile 只转发 /api/*、/healthz、/readyz），只供内部网络里的
  Prometheus 抓取。
"""

from dataclasses import dataclass, field

from prometheus_client import (
    CONTENT_TYPE_PLAIN_0_0_4,
    CollectorRegistry,
    Counter,
    Histogram,
    ProcessCollector,
    generate_latest,
)

CONTENT_TYPE = CONTENT_TYPE_PLAIN_0_0_4
UNMATCHED_ROUTE = "unmatched"
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
# 不计入请求指标：探针与抓取本身每隔几秒一次，会冲淡真实流量的延迟分位
EXCLUDED_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})


@dataclass
class HttpMetrics:
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)

    def __post_init__(self) -> None:
        self.requests = Counter(
            "workspace_http_requests",
            "按路由模板与状态码类别统计的请求次数",
            ["method", "route", "status_class"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "workspace_http_request_duration_seconds",
            "请求耗时（秒），从收到请求到开始返回响应",
            ["method", "route"],
            registry=self.registry,
        )
        ProcessCollector(registry=self.registry)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        method = method if method in _METHODS else "OTHER"
        self.requests.labels(method, route, f"{status // 100}xx").inc()
        self.duration.labels(method, route).observe(seconds)

    def render(self) -> bytes:
        return generate_latest(self.registry)
