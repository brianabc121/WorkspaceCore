"""会话的数据访问。只按 Token 哈希查询，不接触原始 Token。"""

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthSession


async def create(
    db: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> AuthSession:
    session = AuthSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(session)
    await db.flush()
    return session


async def get_by_token_hash(db: AsyncSession, token_hash: str) -> AuthSession | None:
    return await db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))


async def revoke(db: AsyncSession, session: AuthSession, now: datetime) -> None:
    session.revoked_at = now
    await db.flush()


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    """撤销该用户全部未撤销的会话，返回撤销的数量。"""
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(AuthSession.id)
    )
    return len(result.all())


def _active(now: datetime, idle_since: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > now,
        func.coalesce(AuthSession.last_seen_at, AuthSession.created_at) > idle_since,
    )


async def list_active_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime, idle_since: datetime
) -> list[AuthSession]:
    """未撤销、未过期、未闲置超时的会话，最近活动的在前。"""
    return list(
        await db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user_id, *_active(now, idle_since))
            .order_by(
                func.coalesce(AuthSession.last_seen_at, AuthSession.created_at).desc(),
                AuthSession.id,
            )
        )
    )


async def get_active_for_user(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    idle_since: datetime,
) -> AuthSession | None:
    return await db.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            *_active(now, idle_since),
        )
    )
