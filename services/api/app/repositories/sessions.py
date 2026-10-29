"""会话的数据访问。只按 Token 哈希查询，不接触原始 Token。"""

import uuid
from datetime import datetime

from sqlalchemy import select, update
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


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
