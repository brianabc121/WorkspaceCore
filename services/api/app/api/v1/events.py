"""SSE 事件流（Sprint 5）：`GET /api/v1/events/stream`，只发轻量的失效通知。

浏览器的 EventSource 不能带自定义请求头，所以工作空间用查询参数 workspace_id 指定，服务端照常
校验会话与成员身份。事件格式、重连与 Last-Event-ID 的语义见
docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md 第 5 节。
"""

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Security
from fastapi.sse import EventSourceResponse

from app.api.deps import authenticated
from app.core.errors import not_implemented
from app.schemas.common import error_responses
from app.schemas.notifications import InvalidateEvent

router = APIRouter(tags=["events"], dependencies=[Security(authenticated)])


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
    workspace_id: Annotated[
        UUID, Query(description="要订阅的工作空间（EventSource 不能带请求头）")
    ],
) -> None:
    """开始推送之前完成成员校验，失败时返回 JSON 错误而不是一个空的事件流。"""
    raise not_implemented("T-0036")


@router.get(
    "/events/stream",
    summary="SSE：数据失效通知（成员）",
    description="每个事件都是 `event: invalidate`，data 是 InvalidateEvent 的 JSON，"
    "id 用于断线重连（Last-Event-ID）；以冒号开头的行是心跳。"
    "SSE 只是提示，不是可靠队列：客户端重连后、页面重新可见时仍要按需重新拉取。",
    response_class=EventSourceResponse,
    dependencies=[Depends(_stream_access)],
    responses=_json_errors(401, 404, 422),
)
async def event_stream(
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", max_length=64, description="浏览器重连时自动带上"),
    ] = None,
) -> AsyncIterator[InvalidateEvent]:
    return
    yield
