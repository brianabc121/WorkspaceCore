"""集成、凭证、同步游标与幂等记录的数据访问。查询一律限定工作空间。"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyRequest, Integration, IntegrationAccount, SyncCursor


async def list_for_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> list[Integration]:
    return list(
        await db.scalars(
            select(Integration)
            .where(Integration.workspace_id == workspace_id)
            .order_by(Integration.created_at, Integration.id)
        )
    )


async def count_provider(db: AsyncSession, workspace_id: uuid.UUID, provider: str) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Integration)
            .where(Integration.workspace_id == workspace_id, Integration.provider == provider)
        )
        or 0
    )


async def get_for_workspace(
    db: AsyncSession, *, integration_id: uuid.UUID, workspace_id: uuid.UUID, lock: bool = False
) -> Integration | None:
    stmt = select(Integration).where(
        Integration.id == integration_id, Integration.workspace_id == workspace_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def get_by_id(db: AsyncSession, integration_id: uuid.UUID) -> Integration | None:
    return await db.get(Integration, integration_id)


async def get_provider(
    db: AsyncSession, workspace_id: uuid.UUID, provider: str
) -> Integration | None:
    """V1 每个工作空间每个 provider 最多一个集成（契约第 4.3 节）。"""
    return await db.scalar(
        select(Integration)
        .where(Integration.workspace_id == workspace_id, Integration.provider == provider)
        .order_by(Integration.created_at)
        .limit(1)
    )


async def get_account(db: AsyncSession, integration_id: uuid.UUID) -> IntegrationAccount | None:
    return await db.scalar(
        select(IntegrationAccount).where(IntegrationAccount.integration_id == integration_id)
    )


async def get_cursor(
    db: AsyncSession, integration_id: uuid.UUID, resource_type: str
) -> SyncCursor | None:
    return await db.scalar(
        select(SyncCursor).where(
            SyncCursor.integration_id == integration_id,
            SyncCursor.resource_type == resource_type,
        )
    )


async def cursors(
    db: AsyncSession, integration_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SyncCursor]:
    """每个集成的任务同步游标（任务的新鲜度代表整个集成的新鲜度）。"""
    if not integration_ids:
        return {}
    rows = await db.scalars(
        select(SyncCursor).where(
            SyncCursor.integration_id.in_(integration_ids), SyncCursor.resource_type == "task"
        )
    )
    return {row.integration_id: row for row in rows}


async def upsert_cursor(
    db: AsyncSession,
    integration_id: uuid.UUID,
    resource_type: str,
    *,
    success_at: datetime | None,
    cursor_value: str | None = None,
    error: str | None = None,
) -> SyncCursor:
    cursor = await get_cursor(db, integration_id, resource_type)
    if cursor is None:
        cursor = SyncCursor(integration_id=integration_id, resource_type=resource_type)
        db.add(cursor)
    if success_at is not None:
        cursor.last_success_at = success_at
        cursor.last_error = None
        if cursor_value is not None:
            cursor.cursor_value = cursor_value
    else:
        cursor.last_error = error
    await db.flush()
    return cursor


# ---------------------------------------------------------------- 幂等记录


async def get_idempotency(
    db: AsyncSession, *, actor_id: uuid.UUID, key: uuid.UUID, lock: bool = False
) -> IdempotencyRequest | None:
    stmt = select(IdempotencyRequest).where(
        IdempotencyRequest.actor_id == actor_id, IdempotencyRequest.idempotency_key == key
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def delete_idempotency(db: AsyncSession, record_id: uuid.UUID) -> None:
    await db.execute(delete(IdempotencyRequest).where(IdempotencyRequest.id == record_id))


async def purge_expired_idempotency(db: AsyncSession, now: datetime) -> int:
    result = await db.execute(delete(IdempotencyRequest).where(IdempotencyRequest.expires_at < now))
    return int(getattr(result, "rowcount", 0) or 0)
