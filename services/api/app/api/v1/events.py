"""SSE 事件流（Sprint 5）：`GET /api/v1/events/stream`，只发轻量的失效通知。

浏览器的 EventSource 不能带自定义请求头，所以工作空间用查询参数 workspace_id 指定，服务端照常
校验会话与成员身份。事件格式、重连与 Last-Event-ID 的语义见
docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 5 节。
"""

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Security
from fastapi.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticated, client_ip, get_db, session_cookie
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import policy, stream
from app.domain.auth import AuthContext, AuthService
from app.schemas.common import error_responses
from app.schemas.notifications import InvalidateEvent
from app.schemas.workspaces import Role

router = APIRouter(tags=["events"], dependencies=[Security(authenticated)])
logger = logging.getLogger("app.events")


def _json_errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """错误在开始推送之前返回，是普通的 JSON；不写明的话会被当成 text/event-stream。"""
    errors = error_responses(*status_codes)
    for response in errors.values():
        response.pop("model")
        response["content"] = {
            "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
        }
    return errors


async def _stream_access(
    request: Request,
    workspace_id: Annotated[
        UUID, Query(description="要订阅的工作空间（EventSource 不能带请求头）")
    ],
    context: Annotated[AuthContext, Security(authenticated)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> policy.WorkspaceAccess:
    """开始推送之前完成成员校验，失败时返回 JSON 错误而不是一个空的事件流。"""
    access = await policy.require_role(
        db, user_id=context.user.id, workspace_id=workspace_id, minimum=Role.viewer
    )
    # 推送期间不占用数据库连接（之后的复核各自开短会话）
    await db.close()
    limiter: stream.StreamLimiter = request.app.state.stream_limiter
    if limiter.full(context.user.id):
        raise ApiError(
            429,
            "RATE_LIMITED",
            "打开的实时连接太多，请关闭其他标签页后重试",
            headers={"Retry-After": "30"},
        )
    return access


@router.get(
    "/events/stream",
    summary="SSE：数据失效通知（成员）",
    description="每个事件都是 `event: invalidate`，data 是 InvalidateEvent 的 JSON，"
    "id 用于断线重连（Last-Event-ID）；以冒号开头的行是心跳。"
    "SSE 只是提示，不是可靠队列：客户端重连后、页面重新可见时仍要按需重新拉取。",
    response_class=EventSourceResponse,
    responses=_json_errors(401, 404, 422, 429),
)
async def event_stream(
    request: Request,
    access: Annotated[policy.WorkspaceAccess, Depends(_stream_access)],
    token: Annotated[str | None, Security(session_cookie)],
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", max_length=64, description="浏览器重连时自动带上"),
    ] = None,
) -> AsyncIterator[InvalidateEvent]:
    # 实际产出的是 ServerSentEvent（带 event 与 id），FastAPI 原样编码；注解写 InvalidateEvent，
    # OpenAPI 才会把 data 的结构写进契约
    app = request.app
    settings: Settings = app.state.settings
    limiter: stream.StreamLimiter = app.state.stream_limiter
    user_id = access.user_id
    if user_id is None:  # policy.require_role 总会填上
        return

    async def still_allowed() -> bool:
        """会话仍有效、仍是成员（登出、过期、被移出工作空间后停止推送）。"""
        try:
            async with app.state.sessionmaker() as db:
                auth = AuthService(db, app.state.redis, settings)
                await auth.authenticate(token, client_ip=client_ip(request, settings))
                await policy.require_role(
                    db, user_id=user_id, workspace_id=access.workspace.id, minimum=Role.viewer
                )
        except ApiError:
            return False
        return True

    limiter.acquire(user_id)
    try:
        async for item in stream.invalidations(
            app.state.stream_redis,
            access.workspace.id,
            last_event_id,
            block_seconds=settings.sse_block_seconds,
            max_seconds=settings.sse_max_seconds,
            recheck_seconds=settings.sse_recheck_seconds,
            still_allowed=still_allowed,
            disconnected=request.is_disconnected,
        ):
            yield item  # type: ignore[misc]
    finally:
        limiter.release(user_id)
