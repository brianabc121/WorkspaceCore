"""Prometheus HTTP API 客户端（核验 ADR 第 3.2 节；Prometheus v3.15.1）。

只发送工作台代码里固定的查询（app/domain/operations.py 的预设视图），不接受浏览器传入的 PromQL。
错误归类：
- 400 / 422：我们自己的查询有误，记为 VALIDATION_FAILED（内部错误，不重试）；
- 503、超时、连接失败：UNAVAILABLE（读请求由 ConnectorHttp 有限次重试），页面显示 stale；
- 响应不是预期的 JSON：UNAVAILABLE（reason: invalid_response）。
错误对象里不保存 Prometheus 的响应原文。
"""

import math
from dataclasses import dataclass
from typing import Any

import httpx

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp


@dataclass(frozen=True)
class Series:
    labels: dict[str, str]
    # (Unix 时间戳, 值)；NaN / ±Inf（例如除数为 0）为 None
    points: list[tuple[float, float | None]]


def _value(raw: object) -> float | None:
    try:
        value = float(str(raw))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _invalid() -> ConnectorError:
    return ConnectorError(
        "UNAVAILABLE", "Prometheus 返回了无法识别的响应", reason="invalid_response"
    )


@dataclass
class PrometheusClient:
    base_url: str
    http: ConnectorHttp

    async def ready(self) -> None:
        response = await self.http.request("GET", f"{self.base_url}/-/ready", headers={})
        if response.status_code != 200:
            raise ConnectorError(
                "UNAVAILABLE", "Prometheus 未就绪", status=response.status_code, reason="not_ready"
            )

    async def query_range(self, query: str, *, start: float, end: float, step: int) -> list[Series]:
        response = await self.http.request(
            "GET",
            f"{self.base_url}/api/v1/query_range",
            headers={},
            params={"query": query, "start": start, "end": end, "step": step},
        )
        data = self._data(response)
        if data.get("resultType") != "matrix" or not isinstance(data.get("result"), list):
            raise _invalid()
        series: list[Series] = []
        for item in data["result"]:
            metric = item.get("metric") if isinstance(item, dict) else None
            values = item.get("values") if isinstance(item, dict) else None
            if not isinstance(metric, dict) or not isinstance(values, list):
                raise _invalid()
            points: list[tuple[float, float | None]] = []
            for pair in values:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise _invalid()
                timestamp = _value(pair[0])
                if timestamp is None:
                    raise _invalid()
                points.append((timestamp, _value(pair[1])))
            series.append(Series({str(k): str(v) for k, v in metric.items()}, points))
        return series

    @staticmethod
    def _data(response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status in (400, 422):
            raise ConnectorError(
                "VALIDATION_FAILED", "预设查询被 Prometheus 拒绝", status=status, reason="bad_query"
            )
        if status != 200:
            raise ConnectorError(
                "UNAVAILABLE", "Prometheus 暂时不可用", status=status, reason="unavailable"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise _invalid() from error
        if not isinstance(body, dict) or body.get("status") != "success":
            raise _invalid()
        data = body.get("data")
        if not isinstance(data, dict):
            raise _invalid()
        return data
