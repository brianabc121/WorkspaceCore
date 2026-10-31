"""通知的读写（Sprint 5 契约第 4 节）。所有查询都带 user_id 与 workspace_id。"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import Notification, WorkspaceMember


def _recipient(user_id: uuid.UUID, workspace_id: uuid.UUID) -> ColumnElement[bool]:
    return and_(Notification.user_id == user_id, Notification.workspace_id == workspace_id)


async def list_page(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    unread_only: bool,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[Notification]:
    stmt = select(Notification).where(_recipient(user_id, workspace_id))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if before is not None:
        created_at, notification_id = before
        stmt = stmt.where(
            or_(
                Notification.created_at < created_at,
                and_(Notification.created_at == created_at, Notification.id < notification_id),
            )
        )
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    return (await db.scalars(stmt)).all()


async def unread_count(db: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(_recipient(user_id, workspace_id), Notification.read_at.is_(None))
    )
    return int(count or 0)


async def mark_read(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ids: Sequence[uuid.UUID] | None,
) -> int:
    """ids 为 None 表示全部；别人的或不存在的 ID 直接忽略。返回新标为已读的条数。"""
    stmt = (
        update(Notification)
        .where(_recipient(user_id, workspace_id), Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    result = await db.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


async def add_for_roles(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    roles: Sequence[str],
    type_: str,
    severity: str,
    title: str,
    body: str,
    link: str | None,
    source_key: str,
) -> int:
    """给工作空间里指定角色的每个成员各写一条；同一个 source_key 已有的跳过。返回写入的条数。"""
    members = select(
        literal(workspace_id),
        WorkspaceMember.user_id,
        literal(type_),
        literal(severity),
        literal(title),
        literal(body),
        literal(link),
        literal(source_key),
    ).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role.in_(roles))
    stmt = (
        pg_insert(Notification)
        .from_select(
            [
                "workspace_id",
                "user_id",
                "type",
                "severity",
                "title",
                "body",
                "link",
                "source_key",
            ],
            members,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "workspace_id", "source_key"],
            index_where=Notification.source_key.is_not(None),
        )
    )
    result = await db.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


async def add_for_user(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    type_: str,
    severity: str,
    title: str,
    body: str,
    link: str | None,
    source_key: str,
) -> bool:
    stmt = (
        pg_insert(Notification)
        .values(
            workspace_id=workspace_id,
            user_id=user_id,
            type=type_,
            severity=severity,
            title=title,
            body=body,
            link=link,
            source_key=source_key,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "workspace_id", "source_key"],
            index_where=Notification.source_key.is_not(None),
        )
    )
    result = await db.execute(stmt)
    return bool(getattr(result, "rowcount", 0))


async def purge(db: AsyncSession, *, read_before: datetime, any_before: datetime) -> int:
    """清理：已读的超过 read_before，全部的超过 any_before。"""
    result = await db.execute(
        delete(Notification).where(
            or_(
                and_(Notification.read_at.is_not(None), Notification.created_at < read_before),
                Notification.created_at < any_before,
            )
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def workspace_ids(db: AsyncSession) -> Sequence[uuid.UUID]:
    """部署级的告警发给每个工作空间的 owner / admin。"""
    return (await db.scalars(select(WorkspaceMember.workspace_id).distinct())).all()
