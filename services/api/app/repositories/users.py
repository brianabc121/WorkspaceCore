"""用户的数据访问。邮箱一律以小写保存与查询。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(User)) or 0)


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email.strip().lower()))


async def get(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def create(db: AsyncSession, *, email: str, password_hash: str, display_name: str) -> User:
    user = User(email=email.strip().lower(), password_hash=password_hash, display_name=display_name)
    db.add(user)
    await db.flush()
    return user


async def lock(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """SELECT … FOR UPDATE 锁住用户行（到事务结束），并用数据库里的最新值刷新会话中的对象。

    登录签发会话与修改密码都要先拿这把锁，保证二者串行。
    """
    return await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
