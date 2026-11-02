"""外部资源投影（external_resources，现在只有 GitHub）的写入。

并发（T-0061）：写投影的函数先取这个集成的事务级 advisory lock，同一个集成的投影写入事务依次进行。
取锁时不自动 flush：会话里还没写出去的改动（例如集成状态）在拿到锁之后才写，不会先占住别的行锁。
T-0082 移除 Vikunja 后，项目与任务在 projects / tasks 表里（app/repositories/tasks.py）。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import events
from app.models import ExternalResource

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


async def lock_integration(db: AsyncSession, integration_id: uuid.UUID) -> None:
    """取这个集成的投影写锁，到事务结束自动释放；同一事务里重复取不会阻塞。"""
    with db.no_autoflush:
        await db.execute(_LOCK, {"key": f"projection:{integration_id}"})


async def upsert(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    resource_type: str,
    external_id: str,
    title: str,
    source_url: str,
    metadata: dict[str, Any],
    source_updated_at: datetime | None,
) -> ExternalResource:
    """写入或更新投影；外部更新时间比已存的旧时不覆盖（乱序的事件不写入旧数据）。"""
    await lock_integration(db, integration_id)
    events.mark(db, workspace_id, *events.resource_topics(resource_type))
    values = {
        "workspace_id": workspace_id,
        "integration_id": integration_id,
        "resource_type": resource_type,
        "external_id": external_id,
        "title": title,
        "source_url": source_url,
        "metadata_json": metadata,
        "source_updated_at": source_updated_at,
        "synced_at": func.now(),
        "deleted_at": None,
    }
    insert = pg_insert(ExternalResource).values(**values)
    upsert_stmt = insert.on_conflict_do_update(
        index_elements=["integration_id", "resource_type", "external_id"],
        set_={k: insert.excluded[k] for k in values if k not in {"workspace_id", "integration_id"}},
        where=or_(
            ExternalResource.source_updated_at.is_(None),
            insert.excluded.source_updated_at.is_(None),
            insert.excluded.source_updated_at >= ExternalResource.source_updated_at,
        ),
    ).returning(ExternalResource)
    result = await db.scalars(upsert_stmt, execution_options={"populate_existing": True})
    row: ExternalResource | None = result.first()
    if row is None:  # 已存的数据更新：保持不变
        row = await get_by_external(
            db, integration_id=integration_id, resource_type=resource_type, external_id=external_id
        )
    if row is None:
        raise RuntimeError("投影写入后读取不到对应的行")
    return row


async def get_by_external(
    db: AsyncSession, *, integration_id: uuid.UUID, resource_type: str, external_id: str
) -> ExternalResource | None:
    return await db.scalar(
        select(ExternalResource).where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == resource_type,
            ExternalResource.external_id == external_id,
        )
    )
