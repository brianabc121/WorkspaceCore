"""Webhook Inbox（webhook_inbox）的读写（规范第 10 节）。

领取用 `FOR UPDATE SKIP LOCKED`：多个 Worker 同时领取时拿到的事件互不重叠；领取后立即提交为
processing，并把 next_retry_at 设为租约到期时间，处理它的进程中途退出后可以重新领取。
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookInboxEvent

_CLAIMABLE = ("pending", "retry", "processing")


async def insert(
    db: AsyncSession,
    *,
    integration_id: uuid.UUID,
    event_key: str,
    event_type: str,
    payload: dict[str, Any],
    now: datetime,
) -> bool:
    """写入一个事件；同一个 event_key 已存在时忽略（返回 False）。"""
    stmt = (
        pg_insert(WebhookInboxEvent)
        .values(
            integration_id=integration_id,
            event_key=event_key,
            event_type=event_type,
            payload_json=payload,
            status="pending",
            next_retry_at=now,
        )
        .on_conflict_do_nothing(index_elements=["integration_id", "event_key"])
        .returning(WebhookInboxEvent.id)
    )
    return (await db.scalar(stmt)) is not None


async def claim(
    db: AsyncSession, *, now: datetime, lease: timedelta, limit: int
) -> Sequence[WebhookInboxEvent]:
    """领取到期的事件并标为 processing（调用方提交）。"""
    stmt = (
        select(WebhookInboxEvent)
        .where(
            WebhookInboxEvent.status.in_(_CLAIMABLE),
            WebhookInboxEvent.next_retry_at <= now,
        )
        .order_by(WebhookInboxEvent.received_at, WebhookInboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    events = list(await db.scalars(stmt))
    for event in events:
        event.status = "processing"
        event.attempts += 1
        event.next_retry_at = now + lease
    return events


async def get(db: AsyncSession, event_id: uuid.UUID) -> WebhookInboxEvent | None:
    return await db.get(WebhookInboxEvent, event_id, with_for_update=True)


async def purge_completed(db: AsyncSession, *, before: datetime) -> int:
    result = await db.execute(
        delete(WebhookInboxEvent).where(
            WebhookInboxEvent.status == "completed", WebhookInboxEvent.processed_at < before
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
