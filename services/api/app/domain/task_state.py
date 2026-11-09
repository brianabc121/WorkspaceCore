"""任务状态（T-0166，docs/v2/pages-redesign 规范第 4.2、4.3 节）。

五种互斥状态，所有列表、看板、统计、项目卡片和首页都用这里的同一套判定：
- done（已完成）：已完成，且这一轮对当前用户已查看；completion_seq = 0 的历史任务也算。
- awaiting_view（已完成待查看）：已完成，属于新的完成轮次，当前用户还没标“已查看”。
- blocked（阻塞）：未完成，且有人明确写了阻塞原因。暂停、中断、离线都不算阻塞。
- running（进行中）：未完成、未阻塞，有一段 ended_at 为空的执行记录（不看 started_at）。
- todo（待办）：其余未完成的。

查看状态按人隔离：A 标了已查看不会替 B 消掉提示。SQL 判定（work_state_expr）与按批读取
（load_states）的规则必须一致；读取一页任务的状态固定三次查询，不随条数增加。
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from sqlalchemy import and_, case, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskResultRead, TaskRun, User

WorkState = Literal["todo", "running", "awaiting_view", "blocked", "done"]
WORK_STATES: tuple[WorkState, ...] = get_args(WorkState)
# 没有用户上下文时（智能体工具等）按“谁都还没查看”判定
NO_USER = uuid.UUID(int=0)


def running_clause() -> Any:
    """有一段正在执行（ended_at 为空）。"""
    return exists().where(TaskRun.task_id == Task.id, TaskRun.ended_at.is_(None))


def viewed_clause(user_id: uuid.UUID) -> Any:
    """当前这一轮完成结果，这个用户已经查看过。"""
    return exists().where(
        TaskResultRead.task_id == Task.id,
        TaskResultRead.user_id == user_id,
        TaskResultRead.seen_completion_seq >= Task.completion_seq,
    )


def work_state_expr(user_id: uuid.UUID) -> Any:
    """在 SQL 里判定任务对这个用户的状态（与 classify 一致）。"""
    return case(
        (
            and_(Task.done.is_(True), or_(Task.completion_seq == 0, viewed_clause(user_id))),
            "done",
        ),
        (Task.done.is_(True), "awaiting_view"),
        (Task.blocked_at.is_not(None), "blocked"),
        (running_clause(), "running"),
        else_="todo",
    )


def attention_clause(user_id: uuid.UUID) -> Any:
    """需要这个用户关注：已完成待查看，或明确阻塞（项目“需要关注”、首页提示用）。"""
    return or_(
        and_(Task.done.is_(True), Task.completion_seq > 0, ~viewed_clause(user_id)),
        and_(Task.done.is_(False), Task.blocked_at.is_not(None)),
    )


def classify(task: Task, *, viewed: bool, running: bool) -> WorkState:
    """按已读到的记录判定状态（与 work_state_expr 一致）。viewed 指当前这一轮已查看。"""
    if task.done:
        return "done" if task.completion_seq == 0 or viewed else "awaiting_view"
    if task.blocked_at is not None:
        return "blocked"
    if running:
        return "running"
    return "todo"


@dataclass(frozen=True)
class ActiveRun:
    id: uuid.UUID
    executor_kind: str
    executor_name: str
    started_at: datetime


@dataclass(frozen=True)
class Blocker:
    reason: str
    blocked_at: datetime
    blocked_by: uuid.UUID | None
    blocked_by_name: str | None


@dataclass(frozen=True)
class TaskState:
    work_state: WorkState
    result_viewed: bool
    active_run: ActiveRun | None
    blocker: Blocker | None


async def load_states(
    db: AsyncSession, tasks: Sequence[Task], user_id: uuid.UUID
) -> dict[uuid.UUID, TaskState]:
    """一批任务对这个用户的状态：查看记录、正在执行的段、阻塞人名字各一次查询。"""
    if not tasks:
        return {}
    ids = [task.id for task in tasks]
    seen = dict(
        (
            await db.execute(
                select(TaskResultRead.task_id, TaskResultRead.seen_completion_seq).where(
                    TaskResultRead.task_id.in_(ids), TaskResultRead.user_id == user_id
                )
            )
        ).all()
    )
    runs: dict[uuid.UUID, TaskRun] = {}
    for open_run in await db.scalars(
        select(TaskRun)
        .where(TaskRun.task_id.in_(ids), TaskRun.ended_at.is_(None))
        .order_by(TaskRun.started_at.desc())
    ):
        runs.setdefault(open_run.task_id, open_run)
    blocker_ids = {task.blocked_by for task in tasks if task.blocked_by is not None}
    names: dict[uuid.UUID, str] = {}
    if blocker_ids:
        names = dict(
            (
                await db.execute(select(User.id, User.display_name).where(User.id.in_(blocker_ids)))
            ).all()
        )
    states: dict[uuid.UUID, TaskState] = {}
    for task in tasks:
        viewed = task.completion_seq == 0 or seen.get(task.id, -1) >= task.completion_seq
        run = runs.get(task.id)
        states[task.id] = TaskState(
            work_state=classify(task, viewed=viewed, running=run is not None),
            result_viewed=viewed,
            active_run=ActiveRun(
                id=run.id,
                executor_kind=run.executor_kind,
                executor_name=run.executor_name,
                started_at=run.started_at,
            )
            if run is not None
            else None,
            blocker=Blocker(
                reason=task.blocked_reason or "",
                blocked_at=task.blocked_at,
                blocked_by=task.blocked_by,
                blocked_by_name=names.get(task.blocked_by) if task.blocked_by else None,
            )
            if task.blocked_at is not None
            else None,
        )
    return states


# ---------------------------------------------------------------- 写入（与任务在同一个事务里）


def mark_done(task: Task, now: datetime) -> None:
    """未完成 → 完成：新的一轮（completion_seq 加 1），阻塞标记随之清除。

    调用方负责结束正在执行的段、写 completed 动态、加 version。
    """
    task.done = True
    task.done_at = now
    task.completion_seq += 1
    clear_block(task)


def mark_reopened(task: Task) -> None:
    """完成 → 未完成：轮次不变，下一次完成才是新的一轮。"""
    task.done = False
    task.done_at = None


def set_block(task: Task, reason: str, by: uuid.UUID | None, now: datetime) -> None:
    task.blocked_at = now
    task.blocked_reason = reason
    task.blocked_by = by


def clear_block(task: Task) -> None:
    task.blocked_at = None
    task.blocked_reason = None
    task.blocked_by = None


async def mark_viewed(db: AsyncSession, task: Task, user_id: uuid.UUID) -> None:
    """这个用户看过当前这一轮结果（重复调用幂等；不会把记录往回改小）。"""
    if task.completion_seq == 0:
        return
    statement = insert(TaskResultRead).values(
        task_id=task.id,
        user_id=user_id,
        workspace_id=task.workspace_id,
        seen_completion_seq=task.completion_seq,
        viewed_at=datetime.now(UTC),
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[TaskResultRead.task_id, TaskResultRead.user_id],
            set_={
                "seen_completion_seq": statement.excluded.seen_completion_seq,
                "viewed_at": statement.excluded.viewed_at,
            },
            where=TaskResultRead.seen_completion_seq < statement.excluded.seen_completion_seq,
        )
    )
