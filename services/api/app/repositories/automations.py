"""自动化的读写（Sprint 5 契约第 6 节）。所有查询都带 workspace_id；白名单查询排除已软删除的。"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Automation, AutomationConnection, AutomationRun

OPEN_STATUSES = ("queued", "running", "unknown")


async def get_connection(
    db: AsyncSession, workspace_id: uuid.UUID, *, lock: bool = False
) -> AutomationConnection | None:
    stmt = select(AutomationConnection).where(AutomationConnection.workspace_id == workspace_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result: AutomationConnection | None = await db.scalar(stmt)
    return result


async def connections_to_reconcile(db: AsyncSession) -> Sequence[AutomationConnection]:
    stmt = select(AutomationConnection).where(
        AutomationConnection.status.in_(("active", "unavailable"))
    )
    return (await db.scalars(stmt)).all()


async def get(
    db: AsyncSession, workspace_id: uuid.UUID, automation_id: uuid.UUID, *, lock: bool = False
) -> Automation | None:
    stmt = select(Automation).where(
        Automation.id == automation_id,
        Automation.workspace_id == workspace_id,
        Automation.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result: Automation | None = await db.scalar(stmt)
    return result


async def list_page(
    db: AsyncSession, workspace_id: uuid.UUID, *, offset: int, limit: int
) -> Sequence[Automation]:
    stmt = (
        select(Automation)
        .where(Automation.workspace_id == workspace_id, Automation.deleted_at.is_(None))
        .order_by(Automation.name, Automation.id)
        .offset(offset)
        .limit(limit)
    )
    return (await db.scalars(stmt)).all()


async def list_active(db: AsyncSession, workspace_id: uuid.UUID) -> Sequence[Automation]:
    stmt = select(Automation).where(
        Automation.workspace_id == workspace_id, Automation.deleted_at.is_(None)
    )
    return (await db.scalars(stmt)).all()


async def find_conflict(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    webhook_path: str | None,
    workflow_id: str | None,
    exclude: uuid.UUID | None = None,
) -> Automation | None:
    conditions = []
    if webhook_path is not None:
        conditions.append(Automation.webhook_path == webhook_path)
    if workflow_id is not None:
        conditions.append(Automation.workflow_id == workflow_id)
    if not conditions:
        return None
    stmt = select(Automation).where(
        Automation.workspace_id == workspace_id,
        Automation.deleted_at.is_(None),
        or_(*conditions),
    )
    if exclude is not None:
        stmt = stmt.where(Automation.id != exclude)
    result: Automation | None = await db.scalar(stmt.limit(1))
    return result


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> AutomationRun | None:
    result: AutomationRun | None = await db.get(AutomationRun, run_id)
    return result


async def last_runs(
    db: AsyncSession, automation_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, AutomationRun]:
    if not automation_ids:
        return {}
    stmt = (
        select(AutomationRun)
        .where(AutomationRun.automation_id.in_(automation_ids))
        .order_by(
            AutomationRun.automation_id,
            AutomationRun.created_at.desc(),
            AutomationRun.id.desc(),
        )
        .ext(distinct_on(AutomationRun.automation_id))
    )
    return {run.automation_id: run for run in (await db.scalars(stmt)).all()}


async def list_runs(
    db: AsyncSession,
    automation_id: uuid.UUID,
    *,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[AutomationRun]:
    stmt = select(AutomationRun).where(AutomationRun.automation_id == automation_id)
    if before is not None:
        created_at, run_id = before
        stmt = stmt.where(
            or_(
                AutomationRun.created_at < created_at,
                and_(AutomationRun.created_at == created_at, AutomationRun.id < run_id),
            )
        )
    stmt = stmt.order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc()).limit(limit)
    return (await db.scalars(stmt)).all()


async def open_runs(
    db: AsyncSession, workspace_id: uuid.UUID, *, since: datetime
) -> Sequence[AutomationRun]:
    """还没有结束的执行（对账用），按创建时间排序；锁住，避免两个 Worker 同时处理。"""
    stmt = (
        select(AutomationRun)
        .where(
            AutomationRun.workspace_id == workspace_id,
            AutomationRun.status.in_(OPEN_STATUSES),
            AutomationRun.created_at >= since,
        )
        .order_by(AutomationRun.created_at, AutomationRun.id)
        .with_for_update(skip_locked=True)
    )
    return (await db.scalars(stmt)).all()


async def mapped_execution_ids(db: AsyncSession, automation_id: uuid.UUID) -> set[str]:
    stmt = select(AutomationRun.external_execution_id).where(
        AutomationRun.automation_id == automation_id,
        AutomationRun.external_execution_id.is_not(None),
    )
    return {value for value in (await db.scalars(stmt)).all() if value}
