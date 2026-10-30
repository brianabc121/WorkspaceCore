"""仪表盘与组件的数据访问。查询一律限定工作空间：组件通过所属仪表盘确认归属。"""

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dashboard, Widget


async def list_for_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> list[Dashboard]:
    """默认仪表盘在前，其余按创建时间。"""
    return list(
        await db.scalars(
            select(Dashboard)
            .where(Dashboard.workspace_id == workspace_id)
            .order_by(Dashboard.is_default.desc(), Dashboard.created_at, Dashboard.id)
        )
    )


async def count_for_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Dashboard)
            .where(Dashboard.workspace_id == workspace_id)
        )
        or 0
    )


async def get_default(db: AsyncSession, workspace_id: uuid.UUID) -> Dashboard | None:
    return await db.scalar(
        select(Dashboard).where(Dashboard.workspace_id == workspace_id, Dashboard.is_default)
    )


async def get_for_workspace(
    db: AsyncSession, *, dashboard_id: uuid.UUID, workspace_id: uuid.UUID, lock: bool = False
) -> Dashboard | None:
    """lock=True 时 SELECT … FOR UPDATE，用于版本检查与递增（到事务结束）。"""
    stmt = select(Dashboard).where(
        Dashboard.id == dashboard_id, Dashboard.workspace_id == workspace_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def create(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    title: str,
    is_default: bool = False,
) -> Dashboard:
    dashboard = Dashboard(
        workspace_id=workspace_id, owner_id=owner_id, title=title, is_default=is_default
    )
    db.add(dashboard)
    await db.flush()
    return dashboard


async def clear_default(db: AsyncSession, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    """取消该工作空间的默认仪表盘，返回被取消的仪表盘 ID（同时把它们的版本 +1）。"""
    result = await db.execute(
        update(Dashboard)
        .where(Dashboard.workspace_id == workspace_id, Dashboard.is_default)
        .values(is_default=False, version=Dashboard.version + 1)
        .returning(Dashboard.id)
    )
    return [row[0] for row in result]


# ---------------------------------------------------------------- 组件


async def list_widgets(db: AsyncSession, dashboard_id: uuid.UUID) -> list[Widget]:
    return list(
        await db.scalars(
            select(Widget)
            .where(Widget.dashboard_id == dashboard_id)
            .order_by(Widget.created_at, Widget.id)
        )
    )


async def count_widgets(db: AsyncSession, dashboard_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(Widget).where(Widget.dashboard_id == dashboard_id)
        )
        or 0
    )


async def get_widget_for_workspace(
    db: AsyncSession, *, widget_id: uuid.UUID, workspace_id: uuid.UUID, refresh: bool = False
) -> Widget | None:
    """refresh=True 时用数据库里的最新值覆盖会话中已加载的对象（拿到锁之后重新读取用）。"""
    stmt = (
        select(Widget)
        .join(Dashboard, Dashboard.id == Widget.dashboard_id)
        .where(Widget.id == widget_id, Dashboard.workspace_id == workspace_id)
    )
    if refresh:
        stmt = stmt.execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def add_widget(
    db: AsyncSession,
    *,
    dashboard_id: uuid.UUID,
    widget_type: str,
    config: dict[str, Any],
    layout: dict[str, Any],
) -> Widget:
    widget = Widget(
        dashboard_id=dashboard_id, widget_type=widget_type, config_json=config, layout_json=layout
    )
    db.add(widget)
    await db.flush()
    return widget


async def delete_widget(db: AsyncSession, widget_id: uuid.UUID) -> None:
    await db.execute(delete(Widget).where(Widget.id == widget_id))
