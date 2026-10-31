"""SSE 事件流的读取端（Sprint 5 契约第 5 节）：从工作空间的 Redis Stream 读出失效事件。

- 没有 Last-Event-ID：从当前最新的位置开始，只推之后的事件。
- 有 Last-Event-ID：补发它之后保留着的事件；格式不对、流已过期或中间的事件已被裁掉时，
  先发一条 reset（topics 为全部），客户端应重新拉取当前页面的全部数据。
- XREAD 阻塞 block_seconds 秒没有新事件就返回，循环外由 FastAPI 每 15 秒发一次心跳注释。
- 连接有最长时间（到期后服务端关闭，浏览器带着 Last-Event-ID 自动重连），并定期复核会话
  与成员身份：登出、会话过期或被移出工作空间后停止推送（A18）。
"""

import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi.sse import ServerSentEvent
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.domain import events
from app.schemas.notifications import EventTopic, InvalidateEvent

_STREAM_ID = re.compile(r"^(\d{1,20})-(\d{1,20})$")
_ZERO = "0-0"


def _parse(stream_id: str) -> tuple[int, int] | None:
    match = _STREAM_ID.fullmatch(stream_id)
    return (int(match[1]), int(match[2])) if match else None


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


@dataclass
class StreamLimiter:
    """每个 api 进程内的 SSE 连接数上限（全部与每个用户）。"""

    per_user: int
    total: int
    _counts: dict[uuid.UUID, int] = field(default_factory=dict)

    def full(self, user_id: uuid.UUID) -> bool:
        return sum(self._counts.values()) >= self.total or self._counts.get(user_id, 0) >= (
            self.per_user
        )

    def acquire(self, user_id: uuid.UUID) -> None:
        self._counts[user_id] = self._counts.get(user_id, 0) + 1

    def release(self, user_id: uuid.UUID) -> None:
        left = self._counts.get(user_id, 0) - 1
        if left > 0:
            self._counts[user_id] = left
        else:
            self._counts.pop(user_id, None)


async def _info(redis: Redis, key: str) -> dict[str, object] | None:
    try:
        info: dict[str, object] = await redis.xinfo_stream(key)
    except ResponseError:
        return None  # 流不存在：还没有过事件，或 24 小时没有新事件已过期
    return info


async def resume_point(redis: Redis, key: str, last_event_id: str | None) -> tuple[str, bool]:
    """返回 (从哪个 ID 之后开始读, 是否需要先发 reset)。"""
    info = await _info(redis, key)
    latest = _text(info["last-generated-id"]) if info else _ZERO  # type: ignore[arg-type]
    if last_event_id is None:
        return latest, False
    last = _parse(last_event_id)
    if info is None:
        # 客户端什么都没收到过（0-0）就不用重置；否则不知道中间丢了什么
        return _ZERO, last != (0, 0)
    newest = _parse(latest)
    first = _parse(_text(info["recorded-first-entry-id"]))  # type: ignore[arg-type]
    trimmed = int(info["entries-added"]) > int(info["length"])  # type: ignore[call-overload]
    if last is None or newest is None or last > newest:
        return latest, True
    if trimmed and first is not None and last < first:
        # 客户端看到的最后一条比最早保留的还旧，中间的事件已被裁掉
        return latest, True
    return last_event_id, False


def _event(
    workspace_id: uuid.UUID, stream_id: str, topics: list[str], *, reset: bool = False
) -> ServerSentEvent:
    valid: list[EventTopic] = [t for t in topics if t in events.ALL_TOPICS]
    data = InvalidateEvent(
        workspace_id=workspace_id,
        topics=valid or list(events.ALL_TOPICS),
        reset=reset,
        at=datetime.now(UTC),
    )
    return ServerSentEvent(event="invalidate", id=stream_id, data=data)


async def invalidations(
    redis: Redis,
    workspace_id: uuid.UUID,
    last_event_id: str | None,
    *,
    block_seconds: float,
    max_seconds: float,
    recheck_seconds: float,
    still_allowed: Callable[[], Awaitable[bool]],
    disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[ServerSentEvent]:
    key = events.stream_key(workspace_id)
    position, reset = await resume_point(redis, key, last_event_id)
    if reset:
        yield _event(workspace_id, position, list(events.ALL_TOPICS), reset=True)
    started = time.monotonic()
    next_check = started + recheck_seconds
    while time.monotonic() - started < max_seconds:
        if await disconnected():
            return
        batches = await redis.xread({key: position}, count=100, block=int(block_seconds * 1000))
        for _key, entries in batches or []:
            for raw_id, fields in entries:
                position = _text(raw_id)
                topics = _text(fields.get(b"topics", fields.get("topics", b""))).split(",")
                yield _event(workspace_id, position, topics)
        if time.monotonic() >= next_check:
            if not await still_allowed():
                return
            next_check = time.monotonic() + recheck_seconds
