"""实例接口（V2）：只在两个接入入口开放，认证用实例凭证（外网模式另需客户端证书）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 7、8 节：
- `/agent-api/v1/*`：取密钥、取 IM 令牌、心跳，写进 OpenAPI（标签 agent-api）；
- `/im/api/{route}`：接入网关转发 OpenIM REST（白名单 + 请求体里的用户 ID 等于令牌的用户）；
- `/internal/gateway/im-auth`：Caddy forward_auth 用来放行 `/im/ws` 与 `/im/obj/*`；
- `/internal/openim/{path_token}/{command}`：OpenIM 回调，只走内部网络。
后三类不进 OpenAPI（浏览器用不到，也不应当知道路径）。MCP（POST /mcp）在 OC-5 挂载。
认证失败一律 401 AGENT_UNAUTHORIZED，不区分原因。实现：OC-2（T-0054）、OC-3（T-0055）。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import not_implemented
from app.schemas.agents import (
    HeartbeatRequest,
    HeartbeatResponse,
    ImTokenResponse,
    SecretResolveRequest,
    SecretResolveResponse,
)
from app.schemas.common import error_responses

instance_token = HTTPBearer(
    scheme_name="InstanceToken",
    description="实例凭证（接入包里的 MYWS_INSTANCE_TOKEN）；外网入口另需客户端证书",
    auto_error=False,
)
InstanceCredentials = Annotated[HTTPAuthorizationCredentials | None, Security(instance_token)]

router = APIRouter(prefix="/agent-api/v1", tags=["agent-api"])
_errors = error_responses(401, 422, 429)


@router.post(
    "/secrets/resolve",
    summary="取密钥（实例）",
    description="未授权与不存在都返回 errors[id].code = NOT_FOUND；每次取用都写记录。"
    "响应带 Cache-Control: no-store。",
    responses=_errors,
)
async def resolve_agent_secrets(
    credentials: InstanceCredentials, body: SecretResolveRequest
) -> SecretResolveResponse:
    raise not_implemented("T-0054")


@router.post(
    "/im-token",
    summary="取 IM 令牌（实例的启动包装脚本）",
    description="签发会让同一实例正在用的旧令牌连接下线（OC-0 F1），所以只在启动时调用；"
    "每个实例每小时最多 10 次。",
    responses=error_responses(401, 429, 503),
)
async def issue_agent_im_token(credentials: InstanceCredentials) -> ImTokenResponse:
    raise not_implemented("T-0055")


@router.post(
    "/heartbeat",
    summary="上报 OpenClaw 与插件版本（实例）",
    responses=_errors,
)
async def agent_heartbeat(
    credentials: InstanceCredentials, body: HeartbeatRequest
) -> HeartbeatResponse:
    raise not_implemented("T-0054")


gateway = APIRouter(include_in_schema=False)


@gateway.post("/im/api/{route:path}")
async def forward_openim_rest(request: Request, route: str) -> None:
    raise not_implemented("T-0055")


@gateway.get("/internal/gateway/im-auth")
async def authorize_openim_passthrough(request: Request) -> None:
    raise not_implemented("T-0055")


@gateway.post("/internal/openim/{path_token}/{command}")
async def receive_openim_callback(
    request: Request,
    path_token: Annotated[str, Path(min_length=32, max_length=128)],
    command: Annotated[str, Path(max_length=64, pattern=r"^callback[A-Za-z]+Command$")],
) -> None:
    raise not_implemented("T-0055")
