"""工作台项目与任务的读写（T-0082）。所有查询都带 workspace_id。"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, UnaryExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Task


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(column: Any, q: str) -> ColumnElement[bool]:
    result: ColumnElement[bool] = column.ilike(f"%{_escape_like(q)}%", escape="\\")
    return result


async def get_project(
    db: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID, *, lock: bool = False
) -> Project | None:
    stmt = select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result: Project | None = await db.scalar(stmt)
    return result


async def get_task(
    db: AsyncSession, workspace_id: uuid.UUID, task_id: uuid.UUID, *, lock: bool = False
) -> Task | None:
    stmt = select(Task).where(Task.id == task_id, Task.workspace_id == workspace_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result: Task | None = await db.scalar(stmt)
    return result


async def task_counts_by_project(
    db: AsyncSession, project_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    """项目 → (未完成数, 已完成数)。"""
    ids = list(set(project_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(
            Task.project_id,
            func.count().filter(Task.done.is_(False)),
            func.count().filter(Task.done.is_(True)),
        )
        .where(Task.project_id.in_(ids))
        .group_by(Task.project_id)
    )
    return {project_id: (int(o), int(d)) for project_id, o, d in rows}


async def list_projects(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    include_archived: bool,
    q: str | None,
    offset: int,
    limit: int,
) -> Sequence[Project]:
    conditions: list[ColumnElement[bool]] = [Project.workspace_id == workspace_id]
    if not include_archived:
        conditions.append(Project.archived.is_(False))
    if q:
        conditions.append(_contains(Project.title, q))
    stmt = (
        select(Project)
        .where(*conditions)
        .order_by(Project.archived, func.lower(Project.title), Project.id)
        .offset(offset)
        .limit(limit)
    )
    return list(await db.scalars(stmt))


async def list_tasks(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    status: str,
    project_id: uuid.UUID | None,
    priorities: Sequence[int] | None,
    due_before: datetime | None,
    due_after: datetime | None,
    q: str | None,
    sort: str,
    offset: int,
    limit: int,
) -> Sequence[Task]:
    conditions: list[ColumnElement[bool]] = [Task.workspace_id == workspace_id]
    if status != "all":
        conditions.append(Task.done.is_(status == "done"))
    if project_id is not None:
        conditions.append(Task.project_id == project_id)
    if priorities:
        conditions.append(Task.priority.in_(list(priorities)))
    if due_before is not None:
        conditions.append(Task.due_at < due_before)
    if due_after is not None:
        conditions.append(Task.due_at >= due_after)
    if q:
        conditions.append(_contains(Task.title, q))
    order: list[UnaryExpression[Any]]
    if sort == "priority":
        order = [Task.priority.desc(), Task.due_at.asc().nulls_last()]
    elif sort == "updated":
        order = [Task.updated_at.desc()]
    else:
        order = [Task.due_at.asc().nulls_last(), Task.priority.desc()]
    stmt = select(Task).where(*conditions).order_by(*order, Task.id).offset(offset).limit(limit)
    return list(await db.scalars(stmt))


async def project_progress(
    db: AsyncSession, workspace_id: uuid.UUID, *, limit: int
) -> list[tuple[Project, int, int]]:
    """未归档项目的未完成 / 已完成任务数：有未完成任务的项目在前（多的在前），其次按名称。"""
    open_count = func.count(Task.id).filter(Task.done.is_(False))
    done_count = func.count(Task.id).filter(Task.done.is_(True))
    stmt = (
        select(Project, open_count, done_count)
        .outerjoin(Task, Task.project_id == Project.id)
        .where(Project.workspace_id == workspace_id, Project.archived.is_(False))
        .group_by(Project.id)
        .order_by(open_count.desc(), func.lower(Project.title), Project.id)
        .limit(limit)
    )
    return [(row, int(o), int(d)) for row, o, d in (await db.execute(stmt)).all()]


async def task_counts(
    db: AsyncSession, workspace_id: uuid.UUID, *, now: datetime
) -> tuple[int, int]:
    """未完成任务数、其中已逾期（截止时间早于 now）的任务数。"""
    live = [Task.workspace_id == workspace_id, Task.done.is_(False)]
    pending = await db.scalar(select(func.count()).select_from(Task).where(*live))
    overdue = await db.scalar(
        select(func.count()).select_from(Task).where(*live, Task.due_at < now)
    )
    return int(pending or 0), int(overdue or 0)


async def task_summary(
    db: AsyncSession, workspace_id: uuid.UUID, *, due_until: datetime, today_limit: int
) -> tuple[int, int, Sequence[Task]]:
    """首页：未完成任务数、未归档项目数、截止时间早于 due_until 的未完成任务（含逾期）。"""
    pending, _ = await task_counts(db, workspace_id, now=due_until)
    projects = await db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.workspace_id == workspace_id, Project.archived.is_(False))
    )
    today = await db.scalars(
        select(Task)
        .where(Task.workspace_id == workspace_id, Task.done.is_(False), Task.due_at < due_until)
        .order_by(Task.due_at.asc(), Task.id)
        .limit(today_limit)
    )
    return pending, int(projects or 0), list(today)


async def titles_by_id(db: AsyncSession, project_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = list(set(project_ids))
    if not ids:
        return {}
    rows = await db.execute(select(Project.id, Project.title).where(Project.id.in_(ids)))
    return {project_id: title for project_id, title in rows}
