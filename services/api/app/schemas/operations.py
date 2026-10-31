"""运维：服务状态与预设指标（docs/DEVELOPMENT_SPEC.md 第 7.4 节，Sprint 5）。

约定见 docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 3 节；
版本与安全边界见 docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md 第 3、4 节。
浏览器只拿到已聚合的结果：不接受也不返回 PromQL，不透露 Prometheus / Grafana 的内部地址与凭据。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.integrations import SyncState

ServiceKind = Literal["core", "monitoring", "integration"]
ServiceState = Literal["ok", "degraded", "down", "unknown", "not_configured"]
MetricViewId = Literal[
    "request_rate", "latency_p50", "latency_p95", "latency_p99", "error_rate", "services_up"
]
MetricRange = Literal["1h", "6h", "24h", "7d"]
MetricUnit = Literal["requests_per_second", "seconds", "ratio", "count"]


class ServiceStatusOut(BaseModel):
    id: str = Field(
        description="稳定的标识：核心服务为 api、worker、postgres、redis；"
        "监控为 prometheus、grafana；集成为 integration:<集成 UUID>；n8n 连接为 n8n"
    )
    name: str
    kind: ServiceKind
    provider: str | None = Field(
        default=None, description="集成的类型（vikunja / github / n8n）；其他服务为 null"
    )
    status: ServiceState = Field(
        description="ok 正常；degraded 可用但有问题（限流、部分检查失败）；down 不可用；"
        "unknown 还没检查过；not_configured 没有部署或没有配置"
    )
    reason: str | None = Field(
        default=None,
        description="机器可读的原因，例如 timeout、unreachable、rate_limited、token_rejected；"
        "不含地址、Token 等敏感信息",
    )
    checked_at: datetime | None = Field(description="最近一次检查的时间；从未检查过时为 null")
    latency_ms: int | None = Field(default=None, description="最近一次检查的耗时（毫秒）")


class ServiceStatusList(BaseModel):
    items: list[ServiceStatusOut]
    generated_at: datetime


class MetricPoint(BaseModel):
    at: datetime
    value: float | None = Field(description="没有数据或无法计算（例如除数为 0）时为 null")


class MetricSeries(BaseModel):
    label: str = Field(description="序列名，例如服务名；单序列视图为空字符串")
    points: list[MetricPoint]


class MetricViewOut(BaseModel):
    id: MetricViewId
    title: str
    unit: MetricUnit
    series: list[MetricSeries]


class MetricsOut(BaseModel):
    range: MetricRange
    step_seconds: int = Field(
        description="点与点之间的间隔：1h 为 60，6h 为 300，24h 为 900，7d 为 3600"
    )
    views: list[MetricViewOut] = Field(
        description="按请求的 views 顺序返回。Prometheus 不可达时返回最近一次成功查询的结果"
        "（sync 为 stale）；没有这样的结果时为空（sync 为 unavailable），不以 0 冒充"
    )
    sync: SyncState = Field(
        description="Prometheus 的状态：没有部署为 not_configured；不可达或超时为 stale "
        "（有之前的结果）或 unavailable；as_of 是返回的这批数据的查询时间"
    )
    grafana_url: str | None = Field(
        default=None, description="Grafana 仪表盘的外链（部署时配置）；未配置时为 null"
    )
