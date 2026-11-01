"""实例接口（V2）：只在两个接入入口开放，认证用实例凭证（外网模式另需客户端证书）。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 7、8 节：
- `/agent-api/v1/*`：取密钥、取 IM 令牌、心跳，写进 OpenAPI（标签 agent-api）；
- `/im/api/{route}`：接入网关转发 OpenIM REST（白名单 + 请求体里的用户 ID 等于令牌的用户）；
- `/internal/gateway/im-auth`：Caddy forward_auth 用来放行 `/im/ws` 与 `/im/obj/*`；
- `/internal/openim/{path_token}/{command}`：OpenIM 回调，只走内部网络。
后三类不进 OpenAPI（浏览器用不到，也不应当知道路径）。MCP（POST /mcp）在 OC-5 挂载。
认证失败一律 401 AGENT_UNAUTHORIZED，不区分原因。实现：OC-2（T-0054）、OC-3（T-0055）。
"""

import asyncio
import hmac
import json
import logging
from ipaddress import IPv4Network, IPv6Network, ip_address
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import client_ip, get_agent_service, get_db, get_redis
from app.core.errors import ApiError, not_implemented
from app.domain import im_callbacks
from app.domain.agents import AgentPrincipal, AgentService
from app.models import ImGroup
from app.schemas.agents import (
    HeartbeatRequest,
    HeartbeatResponse,
    ImTokenResponse,
    SecretResolveRequest,
    SecretResolveResponse,
)
from app.schemas.common import error_responses
from app.schemas.openim_callbacks import (
    BEFORE_COMMANDS,
    MAX_CALLBACK_BYTES,
    CallbackResponse,
    MsgCallback,
)

logger = logging.getLogger("app.im")

instance_token = HTTPBearer(
    scheme_name="InstanceToken",
    description="实例凭证（接入包里的 MYWS_INSTANCE_TOKEN）；外网入口另需客户端证书",
    auto_error=False,
)
InstanceCredentials = Annotated[HTTPAuthorizationCredentials | None, Security(instance_token)]

INGRESS_HEADER = "X-MyWS-Ingress"
CERT_HEADER = "X-MyWS-Client-Cert-SHA256"

Service = Annotated[AgentService, Depends(get_agent_service)]


async def instance_principal(
    request: Request, credentials: InstanceCredentials, service: Service
) -> AgentPrincipal:
    """实例认证。入口与证书指纹由接入入口的 Caddy 写进请求头；只在配置为可信时采用。"""
    settings = request.app.state.settings
    trusted = _from_ingress_proxy(request, settings.agent_ingress_proxy_networks)
    return await service.authenticate(
        credentials.credentials if credentials else None,
        ingress=request.headers.get(INGRESS_HEADER) if trusted else None,
        cert_fingerprint=request.headers.get(CERT_HEADER) if trusted else None,
        client_ip=client_ip(request, settings),
    )


def _from_ingress_proxy(request: Request, networks: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    """直接对端（不看 X-Forwarded-For）是否是接入入口的 Caddy。"""
    return _from_networks(request, networks)


def _from_networks(request: Request, networks: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    """直接对端（不看 X-Forwarded-For）是否在这些网段里。"""
    if not networks or request.client is None:
        return False
    try:
        peer = ip_address(request.client.host)
    except ValueError:
        return False
    return any(peer in network for network in networks)


Principal = Annotated[AgentPrincipal, Depends(instance_principal)]

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
    request: Request,
    response: Response,
    principal: Principal,
    service: Service,
    body: SecretResolveRequest,
) -> SecretResolveResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.resolve_secrets(
        principal, body.ids, client_ip=client_ip(request, request.app.state.settings)
    )


@router.post(
    "/im-token",
    summary="取 IM 令牌（实例的启动包装脚本）",
    description="签发会让同一实例正在用的旧令牌连接下线（OC-0 F1），所以只在启动时调用；"
    "每个实例每小时最多 10 次。",
    responses=error_responses(401, 429, 503),
)
async def issue_agent_im_token(
    response: Response, principal: Principal, service: Service
) -> ImTokenResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.issue_im_token(principal)


@router.post(
    "/heartbeat",
    summary="上报 OpenClaw 与插件版本（实例）",
    responses=_errors,
)
async def agent_heartbeat(
    principal: Principal, service: Service, body: HeartbeatRequest
) -> HeartbeatResponse:
    return await service.heartbeat(principal, body)


gateway = APIRouter(include_in_schema=False)


@gateway.post("/im/api/{route:path}")
async def forward_openim_rest(request: Request, route: str) -> None:
    raise not_implemented("T-0055")


@gateway.get("/internal/gateway/im-auth")
async def authorize_openim_passthrough(request: Request) -> None:
    raise not_implemented("T-0055")


# before 回调的判断上限：超过就拒收 5009（目标是 200 毫秒内，契约第 10 节）
JUDGE_TIMEOUT_SECONDS = 1.0


def _callback_reply(response: CallbackResponse) -> JSONResponse:
    return JSONResponse(response.model_dump(by_alias=True))


async def _read_callback(request: Request) -> dict[str, Any] | None:
    """读回调的请求体（上限 256 KiB）；超限或不是 JSON 对象时返回 None。"""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_CALLBACK_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_CALLBACK_BYTES:
            return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


async def _notify_relay_paused(sessions: async_sessionmaker[AsyncSession], group: ImGroup) -> None:
    try:
        async with sessions() as db:
            await im_callbacks.relay_paused(db, group)
    except Exception as error:  # 通知失败不影响已经发出的拒收应答
        logger.warning(
            "relay paused notification failed", extra={"error_type": type(error).__name__}
        )


@gateway.post("/internal/openim/{path_token}/{command}")
async def receive_openim_callback(
    request: Request,
    background: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    path_token: Annotated[str, Path(min_length=32, max_length=128)],
    command: Annotated[str, Path(max_length=64, pattern=r"^callback[A-Za-z]+Command$")],
) -> Response:
    """OpenIM 回调。地址口令不对、来源不在允许的网段或没有部署 OpenIM 时一律 404。"""
    settings = request.app.state.settings
    expected = settings.openim_callback_path_token
    if (
        expected is None
        or not settings.openim_enabled
        or not hmac.compare_digest(path_token.encode(), expected.get_secret_value().encode())
        or not _from_networks(request, settings.openim_callback_networks)
    ):
        raise ApiError(404, "NOT_FOUND", "资源不存在")
    payload = await _read_callback(request)
    if command in BEFORE_COMMANDS:
        return _callback_reply(await _judge(request, background, db, redis, payload))
    if payload is None:
        logger.warning("openim callback unreadable", extra={"command": command})
        return _callback_reply(CallbackResponse.allow())
    event_key = im_callbacks.inbox_event_key(command, payload)
    if event_key is not None:
        try:
            await im_callbacks.record_inbox(db, command, event_key, payload)
        except Exception as error:  # 写不进收件箱也放行：对账会补齐
            logger.warning(
                "openim callback not recorded",
                extra={"command": command, "error_type": type(error).__name__},
            )
    elif command != "callbackAfterImportFriendsCommand":
        # 工作台自己导入好友也会触发 AfterImportFriends（OC-0 F2），放行不处理
        await im_callbacks.count_ignored(redis, command)
    return _callback_reply(CallbackResponse.allow())


async def _judge(
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession,
    redis: Redis,
    payload: dict[str, Any] | None,
) -> CallbackResponse:
    """before 回调：判断不了（载荷无效）拒收 5005；数据库或 Redis 出错、超时拒收 5009。"""
    try:
        msg = MsgCallback.model_validate(payload) if payload is not None else None
    except ValueError:
        msg = None
    if msg is None:
        return CallbackResponse.reject(5005)
    judge = im_callbacks.CallbackJudge(
        db, redis, admin_user_id=request.app.state.settings.openim_admin_user_id
    )
    try:
        async with asyncio.timeout(JUDGE_TIMEOUT_SECONDS):
            verdict = await judge.judge(msg)
    except Exception as error:  # 无法判断时拒收，不放行（方案第 4.5 节）
        logger.warning(
            "openim callback policy unavailable", extra={"error_type": type(error).__name__}
        )
        return CallbackResponse.reject(5009)
    if verdict.paused_group is not None:
        background.add_task(
            _notify_relay_paused, request.app.state.sessionmaker, verdict.paused_group
        )
    return verdict.response
