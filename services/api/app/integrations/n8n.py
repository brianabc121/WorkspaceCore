"""n8n 2.45.7 客户端（核验 ADR 第 5 节；T-0037 的实测见
docs/decisions/2026-10-31-n8n-trigger-and-execution-verification.md）。

只用实测过的接口：
- `GET /healthz/readiness`：数据库迁移完成后才返回 200；
- Public API（请求头 `X-N8N-API-KEY`，只读 scope）：
  - `GET /api/v1/workflows/{id}`：核对工作流存在、是否激活；
  - `GET /api/v1/executions?workflowId=&startedAfter=&limit=&cursor=`：列表里没有 tracingContext；
  - `GET /api/v1/executions/{id}`：详情里有 `tracingContext.traceparent`（触发请求带的原样保留）；
  - `GET /api/v1/executions/{id}?includeData=true&redactExecutionData=true`：数据被遮蔽，
    只读 `resultData.lastNodeExecuted` 与 `resultData.redactedError.type` 作为错误摘要；
- `POST /webhook/<path>`：Header Auth（`X-Workspace-Trigger`），带 `traceparent`；
  200 已受理（工作流异步执行），403 密钥不对，404 路径不存在或工作流未激活。
错误对象里不保存 n8n 的响应原文。
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp

TRIGGER_HEADER = "X-Workspace-Trigger"
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")
# 错误摘要里只保留节点名与错误类型这类短文本
_MAX_PART = 120


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    active: bool


@dataclass(frozen=True)
class Execution:
    id: str
    status: str
    started_at: datetime | None
    stopped_at: datetime | None
    trace_id: str | None = None


def trace_id_of(traceparent: object) -> str | None:
    if not isinstance(traceparent, str):
        return None
    match = _TRACEPARENT.fullmatch(traceparent.strip().lower())
    return match[1] if match else None


def _dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _invalid() -> ConnectorError:
    return ConnectorError("UNAVAILABLE", "n8n 返回了无法识别的响应", reason="invalid_response")


def _read_error(response: httpx.Response) -> ConnectorError | None:
    status = response.status_code
    if status < 400:
        return None
    if status == 401:
        return ConnectorError(
            "AUTH_FAILED", "n8n 拒绝了 API Key", status=status, reason="token_rejected"
        )
    if status == 403:
        # 只读 Key 缺少某个 scope
        return ConnectorError(
            "AUTH_FAILED", "API Key 没有所需的权限", status=status, reason="missing_scope"
        )
    if status == 404:
        return ConnectorError("NOT_FOUND", "n8n 里没有这个资源", status=status, reason="not_found")
    if status == 429:
        return ConnectorError("RATE_LIMITED", "n8n 限流", status=status, reason="rate_limited")
    return ConnectorError("UNAVAILABLE", "n8n 暂时不可用", status=status, reason="unavailable")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise _invalid() from error
    if not isinstance(body, dict):
        raise _invalid()
    return body


def _execution(item: dict[str, Any]) -> Execution:
    execution_id = item.get("id")
    status = item.get("status")
    if not isinstance(execution_id, (str, int)) or not isinstance(status, str):
        raise _invalid()
    tracing = item.get("tracingContext")
    trace_id = trace_id_of(tracing.get("traceparent")) if isinstance(tracing, dict) else None
    return Execution(
        id=str(execution_id),
        status=status,
        started_at=_dt(item.get("startedAt")),
        stopped_at=_dt(item.get("stoppedAt")),
        trace_id=trace_id,
    )


def _short(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    return text[:_MAX_PART]


@dataclass
class N8nClient:
    base_url: str
    api_key: str
    http: ConnectorHttp

    def _headers(self) -> dict[str, str]:
        return {"X-N8N-API-KEY": self.api_key}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.http.request(
            "GET", f"{self.base_url}{path}", headers=self._headers(), params=params
        )
        error = _read_error(response)
        if error is not None:
            raise error
        return _json(response)

    async def ready(self) -> None:
        response = await self.http.request("GET", f"{self.base_url}/healthz/readiness", headers={})
        if response.status_code != 200:
            raise ConnectorError(
                "UNAVAILABLE", "n8n 未就绪", status=response.status_code, reason="not_ready"
            )

    async def ping(self) -> None:
        """最便宜的一次带 Key 的读取：Key 失效时尽早发现。"""
        await self._get("/api/v1/workflows", {"limit": 1})

    async def check_token(self) -> None:
        """API Key 能列出工作流与执行（只读 scope）。"""
        await self._get("/api/v1/workflows", {"limit": 1})
        await self._get("/api/v1/executions", {"limit": 1})

    async def get_workflow(self, workflow_id: str) -> Workflow:
        body = await self._get(f"/api/v1/workflows/{workflow_id}")
        active = body.get("active")
        name = body.get("name")
        if not isinstance(active, bool) or not isinstance(name, str):
            raise _invalid()
        return Workflow(id=str(body.get("id", workflow_id)), name=name, active=active)

    async def list_executions(
        self, workflow_id: str, *, started_after: datetime, limit: int = 50, max_pages: int = 3
    ) -> list[Execution]:
        """按开始时间筛选；列表里没有 tracingContext，需要再读详情。"""
        items: list[Execution] = []
        cursor: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "workflowId": workflow_id,
                "startedAfter": started_after.isoformat(),
                "limit": limit,
            }
            if cursor:
                params["cursor"] = cursor
            body = await self._get("/api/v1/executions", params)
            data = body.get("data")
            if not isinstance(data, list):
                raise _invalid()
            items.extend(_execution(item) for item in data if isinstance(item, dict))
            next_cursor = body.get("nextCursor")
            cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
            if cursor is None:
                break
        return items

    async def get_execution(self, execution_id: str) -> Execution:
        return _execution(await self._get(f"/api/v1/executions/{execution_id}"))

    async def error_summary(self, execution_id: str) -> str | None:
        """失败的节点名与错误类型（数据已被 n8n 遮蔽，不含任何参数与错误正文）。"""
        body = await self._get(
            f"/api/v1/executions/{execution_id}",
            {"includeData": "true", "redactExecutionData": "true"},
        )
        data = body.get("data")
        result = data.get("resultData") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            return None
        node = _short(result.get("lastNodeExecuted"))
        error = result.get("redactedError") or result.get("error")
        kind = _short(error.get("type") or error.get("name")) if isinstance(error, dict) else None
        if node and kind:
            return f"工作流在节点「{node}」失败（{kind}）"
        if node:
            return f"工作流在节点「{node}」失败"
        return None

    async def trigger(
        self, webhook_path: str, secret: str, body: dict[str, Any], traceparent: str
    ) -> None:
        """写请求：不自动重试；读超时、连接中断视为结果未知（TIMEOUT_UNKNOWN）。"""
        response = await self.http.request(
            "POST",
            f"{self.base_url}/webhook/{webhook_path}",
            headers={TRIGGER_HEADER: secret, "traceparent": traceparent},
            json=body,
            content_type="application/json",
        )
        status = response.status_code
        if status < 300:
            return
        if status in (401, 403):
            raise ConnectorError(
                "AUTH_FAILED", "n8n 拒绝了共享密钥", status=status, reason="trigger_rejected"
            )
        if status == 404:
            raise ConnectorError(
                "NOT_FOUND",
                "Webhook 路径不存在或工作流未激活",
                status=status,
                reason="webhook_not_found",
            )
        if status in (400, 413, 422):
            raise ConnectorError(
                "VALIDATION_FAILED", "n8n 拒绝了请求内容", status=status, reason="rejected"
            )
        if status == 503:
            raise ConnectorError(
                "UNAVAILABLE", "n8n 暂时不可用", status=status, not_sent=True, reason="unavailable"
            )
        # 其他 5xx：工作流可能已经开始执行，结果未知
        raise ConnectorError(
            "TIMEOUT_UNKNOWN", "触发结果未知", status=status, reason="result_unknown"
        )
