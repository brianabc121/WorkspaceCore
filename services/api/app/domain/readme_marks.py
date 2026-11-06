"""哪些 README 要更新（T-0122，方案 9.4）：ORM 的 after_flush 里收集，提交后写进 Redis。

只依赖模型，app/domain/events.py 在同一个 after_flush / after_commit 里调用
（不经过知识库服务，避免循环导入）。
键：p:项目、t:任务、s:知识库（简报、决定等笔记变了）、i:实例（改名、说明、权限）、w:工作空间（管理员变了）、
q:Issue（标题、状态变了：展开成关联的任务，T-0123）。
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import (
    AgentInstance,
    ImGroup,
    Issue,
    IssueTask,
    KnowledgeNote,
    Project,
    ProjectAgent,
    Task,
    TaskEvent,
    WorkspaceMember,
)

DIRTY_KEY = "readme:dirty"
DEBOUNCE_SECONDS = 5
MARK_NOTE_TYPES = frozenset({"brief", "decision", "rule", "process", "runbook"})
_INFO = "readme_marks"


def _changed(obj: Any, *names: str) -> bool:
    state = inspect(obj)
    return any(state.attrs[name].history.has_changes() for name in names)


def _old(obj: Any, name: str) -> Any:
    deleted = inspect(obj).attrs[name].history.deleted
    return deleted[0] if deleted else None


def collect(session: Session) -> None:
    found: set[str] = session.info.setdefault(_INFO, set())
    for obj in session.new:
        _mark(found, obj, new=True, deleted=False)
    for obj in session.dirty:
        _mark(found, obj, new=False, deleted=False)
    for obj in session.deleted:
        _mark(found, obj, new=False, deleted=True)


def _mark(found: set[str], obj: Any, *, new: bool, deleted: bool) -> None:
    if isinstance(obj, Task):
        if obj.project_id is not None:
            found.add(f"p:{obj.project_id}")
        if not new and not deleted:
            old = _old(obj, "project_id")
            if isinstance(old, uuid.UUID):
                found.add(f"p:{old}")
        if not deleted:
            found.add(f"t:{obj.id}")
    elif isinstance(obj, Project):
        if not deleted:
            found.add(f"p:{obj.id}")
    elif isinstance(obj, ProjectAgent | ImGroup):
        if obj.project_id is not None:
            found.add(f"p:{obj.project_id}")
    elif isinstance(obj, TaskEvent):
        if new:
            found.add(f"t:{obj.task_id}")
    elif isinstance(obj, KnowledgeNote):
        if obj.type in MARK_NOTE_TYPES and obj.space_id is not None:
            found.add(f"s:{obj.space_id}")
    elif isinstance(obj, AgentInstance):
        if not new and not deleted and _changed(obj, "name", "description", "scope"):
            found.add(f"i:{obj.id}")
    elif isinstance(obj, WorkspaceMember):
        found.add(f"w:{obj.workspace_id}")
    elif isinstance(obj, Issue):
        # 评论只改动态时间，不必刷新；开、关、改标题、改类型、删除才刷新
        if new or deleted or _changed(obj, "state", "title", "kind", "deleted_at"):
            found.add(f"p:{obj.project_id}")
            if not new:
                found.add(f"q:{obj.id}")
    elif isinstance(obj, IssueTask):
        found.add(f"t:{obj.task_id}")


def pop(session: Session) -> set[str]:
    popped: set[str] = session.info.pop(_INFO, set())
    return popped


def discard(session: Session) -> None:
    session.info.pop(_INFO, None)


async def push(redis: Redis, keys: Iterable[str], delay: float = DEBOUNCE_SECONDS) -> None:
    """排进刷新队列：已经在排的不提前也不推后（NX），短时间内的多次变化合并成一次。"""
    at = datetime.now(UTC).timestamp() + delay
    items = {key: at for key in keys}
    if items:
        await redis.zadd(DIRTY_KEY, items, nx=True)
