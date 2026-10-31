"""内存版 n8n 2.45.7（测试用）：只实现工作台用到的接口，行为按 T-0037 的实测
（docs/decisions/2026-10-31-n8n-trigger-and-execution-verification.md）。

- Webhook：Header Auth（X-Workspace-Trigger），未激活或路径不存在 404，密钥不对 403，受理 200；
  触发请求的 traceparent 原样记在执行详情的 tracingContext 里（列表里没有）。
- Public API：X-N8N-API-KEY；执行列表按 workflowId / startedAfter 筛选；详情在
  includeData=true&redactExecutionData=true 时只给遮蔽后的数据（节点名与错误类型）。
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

BASE_URL = "http://n8n:5678"
API_KEY = "n8n_api_" + "k" * 32
TRIGGER_SECRET = "trigger-secret-0123456789"  # noqa: S105  测试用的假值


@dataclass
class FakeN8n:
    api_key: str = API_KEY
    secret: str = TRIGGER_SECRET
    workflows: dict[str, dict[str, Any]] = field(default_factory=dict)
    executions: dict[str, dict[str, Any]] = field(default_factory=dict)
    received: list[dict[str, Any]] = field(default_factory=list)
    # 下一次触发：None 正常；"timeout" 读超时（结果未知）；"down" 连接失败
    trigger_failure: str | None = None
    # 新执行的最终状态与失败节点
    next_status: str = "success"
    failed_node: str = "发送邮件"
    down: bool = False

    def add_workflow(self, workflow_id: str, *, path: str, active: bool = True) -> None:
        self.workflows[workflow_id] = {
            "id": workflow_id,
            "name": f"wf-{workflow_id}",
            "active": active,
            "path": path,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path == "/healthz/readiness":
            return httpx.Response(200, json={"status": "ok"})
        if path.startswith("/webhook/"):
            return self._webhook(request, path.removeprefix("/webhook/"))
        if path.startswith("/api/v1/"):
            if request.headers.get("X-N8N-API-KEY") != self.api_key:
                return httpx.Response(401, json={"message": "Unauthorized"})
            return self._api(request, path.removeprefix("/api/v1"))
        return httpx.Response(404, json={"message": "Not Found"})

    def _webhook(self, request: httpx.Request, path: str) -> httpx.Response:
        workflow = next(
            (w for w in self.workflows.values() if w["path"] == path and w["active"]), None
        )
        if workflow is None:
            return httpx.Response(404, json={"code": 404, "message": "not registered"})
        if request.headers.get("X-Workspace-Trigger") != self.secret:
            return httpx.Response(403, text="Authorization data is wrong!")
        failure, self.trigger_failure = self.trigger_failure, None
        if failure == "down":
            raise httpx.ConnectError("refused", request=request)
        body = json.loads(request.content)
        self.received.append({"body": body, "traceparent": request.headers.get("traceparent")})
        execution_id = str(len(self.executions) + 1)
        now = datetime.now(UTC)
        self.executions[execution_id] = {
            "id": execution_id,
            "workflowId": workflow["id"],
            "status": self.next_status,
            "mode": "webhook",
            "startedAt": now.isoformat().replace("+00:00", "Z"),
            "stoppedAt": (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            if self.next_status in {"success", "error", "canceled", "crashed"}
            else None,
            "traceparent": request.headers.get("traceparent"),
        }
        if failure == "timeout":
            # 请求已被受理、工作流在执行，但响应没有回来
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"message": "Workflow was started"})

    def _api(self, request: httpx.Request, path: str) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        if path == "/workflows":
            items = [
                {"id": w["id"], "name": w["name"], "active": w["active"]}
                for w in self.workflows.values()
            ]
            return httpx.Response(
                200, json={"data": items[: int(query.get("limit", 100))], "nextCursor": None}
            )
        if path.startswith("/workflows/"):
            workflow = self.workflows.get(path.removeprefix("/workflows/"))
            if workflow is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={k: v for k, v in workflow.items() if k != "path"})
        if path == "/executions":
            items = list(self.executions.values())
            if "workflowId" in query:
                items = [e for e in items if e["workflowId"] == query["workflowId"]]
            if "startedAfter" in query:
                after = datetime.fromisoformat(query["startedAfter"])
                items = [
                    e
                    for e in items
                    if datetime.fromisoformat(e["startedAt"].replace("Z", "+00:00")) > after
                ]
            listed = [{k: v for k, v in e.items() if k != "traceparent"} for e in reversed(items)]
            return httpx.Response(
                200, json={"data": listed[: int(query.get("limit", 100))], "nextCursor": None}
            )
        if path.startswith("/executions/"):
            execution = self.executions.get(path.removeprefix("/executions/"))
            if execution is None:
                return httpx.Response(404, json={"message": "Not Found"})
            detail: dict[str, Any] = {k: v for k, v in execution.items() if k != "traceparent"}
            detail["tracingContext"] = {"traceparent": execution["traceparent"]}
            if query.get("includeData") == "true":
                assert query.get("redactExecutionData") == "true", "工作台只读遮蔽后的数据"
                result: dict[str, Any] = {"lastNodeExecuted": self.failed_node, "runData": {}}
                if execution["status"] in {"error", "crashed"}:
                    result["redactedError"] = {"type": "WrappedExecutionError"}
                detail["data"] = {"resultData": result, "redactionInfo": {"isRedacted": True}}
            return httpx.Response(200, json=detail)
        return httpx.Response(404, json={"message": "Not Found"})
