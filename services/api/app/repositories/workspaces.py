"""工作空间与成员的数据访问。查询一律带上用户范围，不提供“列出全部工作空间”。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Workspace, WorkspaceMember
from app.schemas.workspaces import Role


async def create_with_owner(db: AsyncSession, *, name: str, owner_id: uuid.UUID) -> Workspace:
    workspace = Workspace(name=name, owner_id=owner_id)
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role=Role.owner.value))
    await db.flush()
    return workspace


async def list_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[Workspace, Role]]:
    rows = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at, Workspace.id)
    )
    return [(workspace, Role(role)) for workspace, role in rows]


async def get_for_member(
    db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Workspace, Role] | None:
    """用户是该工作空间成员时返回工作空间与角色，否则返回 None（不区分不存在与无权访问）。"""
    row = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user_id)
        )
    ).first()
    if row is None:
        return None
    workspace, role = row
    return workspace, Role(role)


async def ids_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        await db.scalars(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
        )
    )


async def lock(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """SELECT … FOR NO KEY UPDATE 锁住工作空间行（到事务结束），用于工作空间级的计数与切换。

    不用 FOR UPDATE：它会挡住其他事务插入引用该工作空间的行（外键检查需要 KEY SHARE 锁），
    例如审计日志。
    """
    await db.execute(
        select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(key_share=True)
    )
