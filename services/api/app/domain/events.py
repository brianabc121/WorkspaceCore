"""数据失效事件（Sprint 5 契约第 5 节）：事务提交之后，按工作空间发一条轻量的“哪些数据变了”。

- **来源**：在会话上收集，提交成功后才发布，回滚则丢弃。
  - ORM 对象的增删改由 after_flush 自动收集（按模型映射到 topics）；
  - 用 Core 语句写入的地方（投影的 upsert / tombstone、批量通知）调用 `mark()` 显式登记。
- **分发**：每个工作空间一条 Redis Stream（`events:ws:<ID>`），多个 api 进程与 Worker
  都写同一条，SSE 连接用 XREAD 读取。流的 ID（例如 `1730340000123-0`）就是 SSE 的 `id`，
  断线后按 Last-Event-ID 续传。流只保留最近的 STREAM_MAXLEN 条，24 小时没有新事件就过期。
- **不是可靠队列**：发布失败只记日志，不影响写入本身；客户端重连、页面重新可见时
  会重新拉取（规范第 10 节）。
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, get_args

from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import (
    Bookmark,
    CalendarEvent,
    ExternalResource,
    Integration,
    KnowledgeSource,
    Notification,
    ResourceLink,
)
from app.schemas.notifications import EventTopic

logger = logging.getLogger("app.events")

ALL_TOPICS: tuple[EventTopic, ...] = get_args(EventTopic)
STREAM_MAXLEN = 500
STREAM_TTL_SECONDS = 24 * 3600
_INFO_KEY = "workspace_events"
_BUS_KEY = "event_bus"

Topics = frozenset[EventTopic]


def stream_key(workspace_id: uuid.UUID) -> str:
    return f"events:ws:{workspace_id}"


def resource_topics(resource_type: str) -> tuple[EventTopic, ...]:
    """外部资源投影：Vikunja 的任务 / 项目，其余是 GitHub（仓库、Issue、PR、Actions）。"""
    if resource_type == "task":
        return ("tasks", "overview")
    if resource_type == "project":
        return ("projects", "tasks", "overview")
    return ("developer", "overview")


_TOPICS: dict[type, Callable[[Any], Iterable[EventTopic]]] = {
    ExternalResource: lambda obj: resource_topics(obj.resource_type),
    CalendarEvent: lambda _: ("calendar", "overview"),
    Bookmark: lambda _: ("knowledge", "overview"),
    ResourceLink: lambda _: ("knowledge", "tasks"),
    KnowledgeSource: lambda _: ("knowledge", "overview"),
    Integration: lambda _: ("integrations", "operations", "overview"),
    Notification: lambda _: ("notifications", "overview"),
}


def _pending(session: Session) -> dict[uuid.UUID, set[EventTopic]]:
    pending: dict[uuid.UUID, set[EventTopic]] = session.info.setdefault(_INFO_KEY, {})
    return pending


def mark(db: AsyncSession | Session, workspace_id: uuid.UUID, *topics: EventTopic) -> None:
    """登记一次变化；提交成功后才发布。"""
    session = db.sync_session if isinstance(db, AsyncSession) else db
    _pending(session).setdefault(workspace_id, set()).update(topics)


def _workspace_of(obj: Any) -> uuid.UUID | None:
    workspace_id = getattr(obj, "workspace_id", None)
    return workspace_id if isinstance(workspace_id, uuid.UUID) else None


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _context: Any) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        topics_of = _TOPICS.get(type(obj))
        workspace_id = _workspace_of(obj)
        if topics_of is not None and workspace_id is not None:
            _pending(session).setdefault(workspace_id, set()).update(topics_of(obj))


@event.listens_for(Session, "after_commit")
def _publish(session: Session) -> None:
    pending: dict[uuid.UUID, set[EventTopic]] = session.info.pop(_INFO_KEY, {})
    bus: EventBus | None = session.info.get(_BUS_KEY)
    if bus is not None and pending:
        bus.schedule({ws: frozenset(topics) for ws, topics in pending.items()})


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop(_INFO_KEY, None)


class EventBus:
    """发布到 Redis Stream。API 与 Worker 各有一个，挂在各自的 sessionmaker 上。"""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._tasks: set[asyncio.Task[None]] = set()

    def session_info(self) -> dict[str, Any]:
        """传给 async_sessionmaker(info=…)：这个会话提交后的事件由本对象发布。"""
        return {_BUS_KEY: self}

    def schedule(self, changes: dict[uuid.UUID, Topics]) -> None:
        """在 after_commit 里调用（同步上下文）：交给事件循环异步发布，不拖慢提交。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.publish(changes))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def publish(self, changes: dict[uuid.UUID, Topics]) -> None:
        at = datetime.now(UTC).isoformat()
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for workspace_id, topics in changes.items():
                    key = stream_key(workspace_id)
                    fields = {"topics": ",".join(sorted(topics)), "at": at}
                    pipe.xadd(key, fields, maxlen=STREAM_MAXLEN, approximate=True)  # type: ignore[arg-type]
                    pipe.expire(key, STREAM_TTL_SECONDS)
                await pipe.execute()
        except Exception as error:
            logger.warning("event publish failed", extra={"error_type": type(error).__name__})

    async def drain(self) -> None:
        """测试与关闭时等待已排队的发布完成。"""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
