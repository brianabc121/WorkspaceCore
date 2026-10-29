"""审计日志的数据访问：只有写入与按工作空间查询，不提供修改和删除。"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def add_many(db: AsyncSession, entries: Sequence[AuditLog]) -> None:
    db.add_all(entries)
    await db.flush()


async def list_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
) -> list[AuditLog]:
    """新的在前；before 为上一页最后一条的 (created_at, id)。"""
    stmt = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if before is not None:
        stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) < tuple_(*before))
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    return list(await db.scalars(stmt))
