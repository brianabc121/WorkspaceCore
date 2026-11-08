"""工作台项目与任务的读写（T-0082）。所有查询都带 workspace_id。"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, UnaryExpression, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentInstance,
    Issue,
    IssueTask,
    Project,
    ProjectAgent,
    Task,
    TaskRun,
    User,
)


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
    assignee_instance_id: uuid.UUID | None = None,
    claimable_for: uuid.UUID | None = None,
    assignee_user_id: uuid.UUID | None = None,
) -> Sequence[Task]:
    conditions: list[ColumnElement[bool]] = [Task.workspace_id == workspace_id]
    if assignee_instance_id is not None:
        conditions.append(Task.assignee_instance_id == assignee_instance_id)
    if assignee_user_id is not None:
        conditions.append(Task.assignee_user_id == assignee_user_id)
    if claimable_for is not None:
        # 这个实例现在能领取的（T-0111）：可自由接取、没有执行者、没完成、在范围内
        member = exists().where(
            ProjectAgent.project_id == Task.project_id, ProjectAgent.instance_id == claimable_for
        )
        conditions += [
            Task.claim_mode == "open",
            Task.done.is_(False),
            Task.assignee_user_id.is_(None),
            Task.assignee_instance_id.is_(None),
            or_(Task.claim_scope == "all", member),
        ]
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


# ---------------------------------------------------------------- 首页任务进展（T-0163）

PROGRESS_ORDER = ("review", "blocked", "in_progress", "todo")


def _open_blockers(task_id: Any) -> Any:
    """任务关联的、未关闭的阻塞 Issue 数（与 issues.open_blockers 同样的条件）。"""
    return (
        select(func.count())
        .select_from(Issue)
        .join(IssueTask, IssueTask.issue_id == Issue.id)
        .where(
            IssueTask.task_id == task_id,
            Issue.kind == "blocker",
            Issue.state == "open",
            Issue.deleted_at.is_(None),
        )
        .correlate(Task)
        .scalar_subquery()
    )


def _progress_base(workspace_id: uuid.UUID) -> Any:
    """未归档项目里、首页要显示的任务（未完成的，以及已完成待查看的）和它们的状态。"""
    running = exists().where(TaskRun.task_id == Task.id, TaskRun.ended_at.is_(None))
    status = case(
        (and_(Task.done.is_(False), _open_blockers(Task.id) > 0), "blocked"),
        (Task.done.is_(True), "review"),
        (or_(running, Task.started_at.is_not(None)), "in_progress"),
        else_="todo",
    )
    return (
        select(Task.id.label("task_id"), status.label("status"))
        .join(Project, Project.id == Task.project_id)
        .where(
            Task.workspace_id == workspace_id,
            Project.archived.is_(False),
            or_(Task.done.is_(False), Task.review_pending.is_(True)),
        )
        .subquery()
    )


async def progress_counts(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, int]:
    base = _progress_base(workspace_id)
    rows = (await db.execute(select(base.c.status, func.count()).group_by(base.c.status))).all()
    counts = {key: 0 for key in PROGRESS_ORDER}
    counts.update({str(status): int(count) for status, count in rows})
    return counts


async def progress_items(
    db: AsyncSession, workspace_id: uuid.UUID, *, status: str, limit: int
) -> list[tuple[Task, str, str, str | None, str | None]]:
    """一页任务：(任务, 项目名, 状态, 执行者是成员时的名字, 执行者是实例时的名字)。"""
    base = _progress_base(workspace_id)
    rank = case({key: index for index, key in enumerate(PROGRESS_ORDER)}, value=base.c.status)
    stmt = (
        select(Task, Project.title, base.c.status, User.display_name, AgentInstance.name)
        .join(base, base.c.task_id == Task.id)
        .join(Project, Project.id == Task.project_id)
        .outerjoin(User, User.id == Task.assignee_user_id)
        .outerjoin(AgentInstance, AgentInstance.id == Task.assignee_instance_id)
    )
    if status != "all":
        stmt = stmt.where(base.c.status == status)
    stmt = stmt.order_by(rank, Task.updated_at.desc(), Task.id).limit(limit)
    return [
        (task, str(title), str(state), user, instance)
        for task, title, state, user, instance in (await db.execute(stmt)).all()
    ]


async def first_blockers(
    db: AsyncSession, task_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[Issue, int]]:
    """每个任务第一个（编号最小的）未关闭阻塞 Issue，以及一共几条。"""
    if not task_ids:
        return {}
    rows = (
        await db.execute(
            select(IssueTask.task_id, Issue)
            .join(Issue, Issue.id == IssueTask.issue_id)
            .where(
                IssueTask.task_id.in_(list(task_ids)),
                Issue.kind == "blocker",
                Issue.state == "open",
                Issue.deleted_at.is_(None),
            )
            .order_by(IssueTask.task_id, Issue.number)
        )
    ).all()
    found: dict[uuid.UUID, tuple[Issue, int]] = {}
    for task_id, issue in rows:
        first, count = found.get(task_id, (issue, 0))
        found[task_id] = (first, count + 1)
    return found
