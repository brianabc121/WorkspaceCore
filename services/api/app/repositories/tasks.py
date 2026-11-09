"""工作台项目与任务的读写（T-0082）。所有查询都带 workspace_id。"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, UnaryExpression, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task_state import attention_clause, work_state_expr
from app.models import (
    AgentInstance,
    Project,
    ProjectAgent,
    Task,
    TaskEvent,
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


def last_activity_expr() -> Any:
    """项目的最近更新：项目本身或其中任务最后一次修改的时间（T-0166）。"""
    latest_task = (
        select(func.max(Task.updated_at)).where(Task.project_id == Project.id).scalar_subquery()
    )
    return func.greatest(Project.updated_at, latest_task)


def project_attention(user_id: uuid.UUID) -> Any:
    """项目需要这个用户关注：有他还没查看的完成结果，或有明确阻塞（与任务页同一判定）。"""
    return exists().where(Task.project_id == Project.id, attention_clause(user_id))


async def list_projects(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    include_archived: bool,
    q: str | None,
    offset: int,
    limit: int,
    archived_only: bool = False,
    attention_for: uuid.UUID | None = None,
    sort: str = "title",
) -> Sequence[Project]:
    conditions: list[ColumnElement[bool]] = [Project.workspace_id == workspace_id]
    if archived_only:
        conditions.append(Project.archived.is_(True))
    elif not include_archived:
        conditions.append(Project.archived.is_(False))
    if q:
        conditions.append(_contains(Project.title, q))
    if attention_for is not None:
        conditions.append(project_attention(attention_for))
    order: list[Any] = [Project.archived]
    if sort == "updated":
        order.append(last_activity_expr().desc())
    order += [func.lower(Project.title), Project.id]
    stmt = select(Project).where(*conditions).order_by(*order).offset(offset).limit(limit)
    return list(await db.scalars(stmt))


async def project_task_states(
    db: AsyncSession, project_ids: Iterable[uuid.UUID], user_id: uuid.UUID
) -> tuple[dict[uuid.UUID, dict[str, int]], dict[uuid.UUID, datetime]]:
    """项目 → 五种状态各几项（按这个用户），以及其中任务最后修改的时间。一次查询。"""
    ids = list(set(project_ids))
    if not ids:
        return {}, {}
    base = (
        select(
            Task.project_id.label("project_id"),
            work_state_expr(user_id).label("state"),
            Task.updated_at.label("updated_at"),
        )
        .where(Task.project_id.in_(ids))
        .subquery()
    )
    rows = await db.execute(
        select(base.c.project_id, base.c.state, func.count(), func.max(base.c.updated_at)).group_by(
            base.c.project_id, base.c.state
        )
    )
    states: dict[uuid.UUID, dict[str, int]] = {}
    latest: dict[uuid.UUID, datetime] = {}
    for project_id, state, count, updated in rows:
        states.setdefault(project_id, {})[str(state)] = int(count)
        if updated is not None and (project_id not in latest or updated > latest[project_id]):
            latest[project_id] = updated
    return states, latest


async def project_summary(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    completed_after: datetime,
    completed_before: datetime,
) -> tuple[int, int, int]:
    """未归档项目数、其中需要这个用户关注的项目数、时间窗内完成且现在仍完成的任务数。"""
    live = [Project.workspace_id == workspace_id, Project.archived.is_(False)]
    active = await db.scalar(select(func.count()).select_from(Project).where(*live))
    attention = await db.scalar(
        select(func.count()).select_from(Project).where(*live, project_attention(user_id))
    )
    completed = await db.scalar(
        select(func.count())
        .select_from(Task)
        .join(Project, Project.id == Task.project_id)
        .where(
            *live,
            Task.done.is_(True),
            Task.done_at >= completed_after,
            Task.done_at < completed_before,
        )
    )
    return int(active or 0), int(attention or 0), int(completed or 0)


async def project_activity(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    project_id: uuid.UUID | None,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[tuple[TaskEvent, str, uuid.UUID, str]]:
    """最近的任务动态（新的在前）：(动态, 任务标题, 项目 ID, 项目名)。"""
    stmt = (
        select(TaskEvent, Task.title, Project.id, Project.title)
        .join(Task, Task.id == TaskEvent.task_id)
        .join(Project, Project.id == Task.project_id)
        .where(TaskEvent.workspace_id == workspace_id, Task.workspace_id == workspace_id)
    )
    if project_id is not None:
        stmt = stmt.where(Project.id == project_id)
    if before is not None:
        stmt = stmt.where(
            or_(
                TaskEvent.created_at < before[0],
                and_(TaskEvent.created_at == before[0], TaskEvent.id < before[1]),
            )
        )
    stmt = stmt.order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc()).limit(limit)
    return [
        (event, str(task_title), pid, str(project_title))
        for event, task_title, pid, project_title in (await db.execute(stmt)).all()
    ]


def task_conditions(
    *,
    workspace_id: uuid.UUID,
    status: str = "all",
    project_id: uuid.UUID | None = None,
    priorities: Sequence[int] | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    q: str | None = None,
    assignee_instance_id: uuid.UUID | None = None,
    claimable_for: uuid.UUID | None = None,
    assignee_user_id: uuid.UUID | None = None,
) -> list[ColumnElement[bool]]:
    """任务列表与状态计数共用的筛选条件（不含状态，T-0166）。"""
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
    return conditions


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
    work_state: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Sequence[Task]:
    conditions = task_conditions(
        workspace_id=workspace_id,
        status=status,
        project_id=project_id,
        priorities=priorities,
        due_before=due_before,
        due_after=due_after,
        q=q,
        assignee_instance_id=assignee_instance_id,
        claimable_for=claimable_for,
        assignee_user_id=assignee_user_id,
    )
    if work_state is not None and user_id is not None:
        conditions.append(work_state_expr(user_id) == work_state)
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


async def state_counts(
    db: AsyncSession, conditions: Sequence[ColumnElement[bool]], user_id: uuid.UUID
) -> dict[str, int]:
    """符合条件的任务按五种状态各几项（这个用户的查看状态）。"""
    base = select(work_state_expr(user_id).label("state")).where(*conditions).subquery()
    rows = (await db.execute(select(base.c.state, func.count()).group_by(base.c.state))).all()
    return {str(state): int(count) for state, count in rows}


# ------------------------------------------------ 首页任务进展（T-0163；T-0166 改用同一判定）

PROGRESS_ORDER = ("awaiting_view", "blocked", "running", "todo")


def _progress_base(workspace_id: uuid.UUID, user_id: uuid.UUID) -> Any:
    """未归档项目里、首页要显示的任务（未完成的，以及这个用户待查看的）和它们的状态。"""
    return (
        select(Task.id.label("task_id"), work_state_expr(user_id).label("status"))
        .join(Project, Project.id == Task.project_id)
        .where(
            Task.workspace_id == workspace_id,
            Project.archived.is_(False),
            # 已完成且是历史轮次（0）的任务一定是“已完成”，先排除
            or_(Task.done.is_(False), Task.completion_seq > 0),
        )
        .subquery()
    )


async def progress_counts(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, int]:
    base = _progress_base(workspace_id, user_id)
    rows = (
        await db.execute(
            select(base.c.status, func.count())
            .where(base.c.status != "done")
            .group_by(base.c.status)
        )
    ).all()
    counts = {key: 0 for key in PROGRESS_ORDER}
    counts.update({str(status): int(count) for status, count in rows})
    return counts


async def progress_items(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, *, status: str, limit: int
) -> list[tuple[Task, str, str, str | None, str | None]]:
    """一页任务：(任务, 项目名, 状态, 执行者是成员时的名字, 执行者是实例时的名字)。"""
    base = _progress_base(workspace_id, user_id)
    rank = case({key: index for index, key in enumerate(PROGRESS_ORDER)}, value=base.c.status)
    stmt = (
        select(Task, Project.title, base.c.status, User.display_name, AgentInstance.name)
        .join(base, base.c.task_id == Task.id)
        .join(Project, Project.id == Task.project_id)
        .outerjoin(User, User.id == Task.assignee_user_id)
        .outerjoin(AgentInstance, AgentInstance.id == Task.assignee_instance_id)
        .where(base.c.status != "done")
    )
    if status != "all":
        stmt = stmt.where(base.c.status == status)
    stmt = stmt.order_by(rank, Task.updated_at.desc(), Task.id).limit(limit)
    return [
        (task, str(title), str(state), user, instance)
        for task, title, state, user, instance in (await db.execute(stmt)).all()
    ]
