"""`POST /mcp`：MCP 工具（V2 OC-5，契约第 9 节）。

只在两个接入入口开放（Caddy 管理员入口只转发 /api/*）。

- 认证与 `/agent-api/v1/*` 相同（`instance_principal`）：失败一律 401 `AGENT_UNAUTHORIZED`；
- 认证通过后把请求原样交给 MCP SDK（无状态、JSON 响应），实例 ID 放进 ASGI scope 的 state；
- 只接受 POST：无状态模式下 GET 会打开一条一直不结束的 SSE 流，DELETE 没有会话可删，都返回 405。
不进 OpenAPI：浏览器用不到，工具清单在 `app/schemas/ai_tools.py`。
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from app.api.agent_api import Principal
from app.core.logging import request_id_var
from app.domain.mcp_tools import CALLER_STATE_KEY, McpCaller

router = APIRouter()


class _McpResponse(Response):
    """把这次请求交给 MCP SDK 处理（请求体还没有读过）。"""

    def __init__(self, caller: McpCaller) -> None:
        super().__init__()
        self.caller = caller

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = scope["app"].state.mcp
        state = {**scope.get("state", {}), CALLER_STATE_KEY: self.caller}
        await manager.handle_request({**scope, "state": state}, receive, send)


@router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request, principal: Principal) -> Response:
    if getattr(request.app.state, "mcp", None) is None:
        return JSONResponse(
            {"error": {"code": "SERVICE_UNAVAILABLE", "message": "MCP 服务没有启动"}},
            status_code=503,
        )
    caller = McpCaller(instance_id=principal.instance.id, request_id=request_id_var.get())
    return _McpResponse(caller)
