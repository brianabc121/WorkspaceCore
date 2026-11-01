"""外部资源投影（external_resources）的读写。查询一律限定工作空间并排除 tombstone。

任务的展示字段放在 metadata_json 里（project_resource_id、done、priority、due_at、labels、
version 等）；
V1 的数据量下按 JSONB 表达式过滤与排序即可，不另建列。

并发（T-0061）：写投影的函数先取这个集成的事务级 advisory lock，同一个集成的投影写入事务
（API 写任务、Webhook 处理、定时对账）依次进行。否则会死锁：对账重存项目时，搜索触发器
改写这个项目下所有任务的搜索文档（先锁 search_documents 再锁任务行），API 删除任务的顺序相反。
取锁时不自动 flush：会话里还没写出去的改动（例如集成状态）在拿到锁之后才写，不会先占住别的行锁。
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, cast, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

from app.domain import events
from app.models import ExternalResource

PROJECT = "project"
TASK = "task"

_meta = ExternalResource.metadata_json
_done = cast(_meta["done"].astext, Boolean)
_priority = cast(_meta["priority"].astext, Integer)
_due_at = cast(_meta["due_at"].astext, DateTime(timezone=True))
_project = _meta["project_resource_id"].astext
_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


async def lock_integration(db: AsyncSession, integration_id: uuid.UUID) -> None:
    """取这个集成的投影写锁，到事务结束自动释放；同一事务里重复取不会阻塞。"""
    with db.no_autoflush:
        await db.execute(_LOCK, {"key": f"projection:{integration_id}"})


async def upsert(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    resource_type: str,
    external_id: str,
    title: str,
    source_url: str,
    metadata: dict[str, Any],
    source_updated_at: datetime | None,
) -> ExternalResource:
    """写入或更新投影；外部更新时间比已存的旧时不覆盖（乱序的事件不写入旧数据）。"""
    await lock_integration(db, integration_id)
    events.mark(db, workspace_id, *events.resource_topics(resource_type))
    values = {
        "workspace_id": workspace_id,
        "integration_id": integration_id,
        "resource_type": resource_type,
        "external_id": external_id,
        "title": title,
        "source_url": source_url,
        "metadata_json": metadata,
        "source_updated_at": source_updated_at,
        "synced_at": func.now(),
        "deleted_at": None,
    }
    insert = pg_insert(ExternalResource).values(**values)
    upsert_stmt = insert.on_conflict_do_update(
        index_elements=["integration_id", "resource_type", "external_id"],
        set_={k: insert.excluded[k] for k in values if k not in {"workspace_id", "integration_id"}},
        where=or_(
            ExternalResource.source_updated_at.is_(None),
            insert.excluded.source_updated_at.is_(None),
            insert.excluded.source_updated_at >= ExternalResource.source_updated_at,
        ),
    ).returning(ExternalResource)
    result = await db.scalars(upsert_stmt, execution_options={"populate_existing": True})
    row: ExternalResource | None = result.first()
    if row is None:  # 已存的数据更新：保持不变
        row = await get_by_external(
            db, integration_id=integration_id, resource_type=resource_type, external_id=external_id
        )
    if row is None:
        raise RuntimeError("投影写入后读取不到对应的行")
    return row


async def get_by_external(
    db: AsyncSession, *, integration_id: uuid.UUID, resource_type: str, external_id: str
) -> ExternalResource | None:
    return await db.scalar(
        select(ExternalResource).where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == resource_type,
            ExternalResource.external_id == external_id,
        )
    )


async def get_live(
    db: AsyncSession, *, workspace_id: uuid.UUID, resource_type: str, resource_id: uuid.UUID
) -> ExternalResource | None:
    return await db.scalar(
        select(ExternalResource).where(
            ExternalResource.id == resource_id,
            ExternalResource.workspace_id == workspace_id,
            ExternalResource.resource_type == resource_type,
            ExternalResource.deleted_at.is_(None),
        )
    )


async def get_any(
    db: AsyncSession, *, workspace_id: uuid.UUID, resource_type: str, resource_id: uuid.UUID
) -> ExternalResource | None:
    """包括已 tombstone 的行（删除接口需要区分“已删除”与“不存在”）。"""
    return await db.scalar(
        select(ExternalResource).where(
            ExternalResource.id == resource_id,
            ExternalResource.workspace_id == workspace_id,
            ExternalResource.resource_type == resource_type,
        )
    )


async def tombstone(db: AsyncSession, resource: ExternalResource) -> None:
    await lock_integration(db, resource.integration_id)
    await db.execute(
        update(ExternalResource)
        .where(ExternalResource.id == resource.id, ExternalResource.deleted_at.is_(None))
        .values(deleted_at=func.now(), synced_at=func.now())
    )
    events.mark(db, resource.workspace_id, *events.resource_topics(resource.resource_type))


async def tombstone_missing(
    db: AsyncSession, *, integration_id: uuid.UUID, resource_type: str, seen: Iterable[str]
) -> list[str]:
    """全量列表之后：外部已经没有的资源记 tombstone（核验 ADR 第 1 节：删除后接口里整条消失）。

    返回被记 tombstone 的外部 ID。
    """
    await lock_integration(db, integration_id)
    stmt = (
        update(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == resource_type,
            ExternalResource.deleted_at.is_(None),
            ExternalResource.external_id.not_in(list(seen)),
        )
        .values(deleted_at=func.now(), synced_at=func.now())
        .returning(ExternalResource.external_id)
    )
    return list(await db.scalars(stmt))


async def tombstone_external(
    db: AsyncSession, *, integration_id: uuid.UUID, resource_type: str, external_id: str
) -> None:
    await lock_integration(db, integration_id)
    await db.execute(
        update(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == resource_type,
            ExternalResource.external_id == external_id,
            ExternalResource.deleted_at.is_(None),
        )
        .values(deleted_at=func.now(), synced_at=func.now())
    )


async def tombstone_project_tasks(
    db: AsyncSession, *, integration_id: uuid.UUID, project_external_ids: Sequence[str]
) -> None:
    """项目在 Vikunja 里删除后，它的任务也随之消失（Vikunja 不为这些任务单独发事件）。"""
    if not project_external_ids:
        return
    await lock_integration(db, integration_id)
    await db.execute(
        update(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == TASK,
            ExternalResource.deleted_at.is_(None),
            _meta["project_external_id"].astext.in_(list(project_external_ids)),
        )
        .values(deleted_at=func.now(), synced_at=func.now())
    )


async def project_ids_by_external(
    db: AsyncSession, integration_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    rows = await db.execute(
        select(ExternalResource.external_id, ExternalResource.id).where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == PROJECT,
            ExternalResource.deleted_at.is_(None),
        )
    )
    return {external_id: resource_id for external_id, resource_id in rows}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_projects(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID | None,
    include_archived: bool,
    q: str | None,
    offset: int,
    limit: int,
) -> Sequence[ExternalResource]:
    conditions: list[ColumnElement[bool]] = [
        ExternalResource.workspace_id == workspace_id,
        ExternalResource.resource_type == PROJECT,
        ExternalResource.deleted_at.is_(None),
    ]
    if integration_id is not None:
        conditions.append(ExternalResource.integration_id == integration_id)
    if not include_archived:
        conditions.append(cast(_meta["archived"].astext, Boolean).is_not(True))
    if q:
        conditions.append(ExternalResource.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    stmt = (
        select(ExternalResource)
        .where(*conditions)
        .order_by(func.lower(ExternalResource.title), ExternalResource.id)
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
) -> Sequence[ExternalResource]:
    conditions: list[ColumnElement[bool]] = [
        ExternalResource.workspace_id == workspace_id,
        ExternalResource.resource_type == TASK,
        ExternalResource.deleted_at.is_(None),
    ]
    if status != "all":
        conditions.append(_done.is_(status == "done"))
    if project_id is not None:
        conditions.append(_project == str(project_id))
    if priorities:
        conditions.append(_priority.in_(list(priorities)))
    if due_before is not None:
        conditions.append(_due_at < due_before)
    if due_after is not None:
        conditions.append(_due_at >= due_after)
    if q:
        conditions.append(ExternalResource.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    order: list[UnaryExpression[Any]]
    if sort == "priority":
        order = [_priority.desc(), _due_at.asc().nulls_last()]
    elif sort == "updated":
        order = [ExternalResource.source_updated_at.desc().nulls_last()]
    else:
        order = [_due_at.asc().nulls_last(), _priority.desc()]
    stmt = (
        select(ExternalResource)
        .where(*conditions)
        .order_by(*order, ExternalResource.id)
        .offset(offset)
        .limit(limit)
    )
    return list(await db.scalars(stmt))


async def task_summary(
    db: AsyncSession, workspace_id: uuid.UUID, *, due_until: datetime, today_limit: int
) -> tuple[int, int, Sequence[ExternalResource]]:
    """首页：未完成任务数、未归档项目数、截止时间早于 due_until 的未完成任务（含逾期）。"""
    live = [ExternalResource.workspace_id == workspace_id, ExternalResource.deleted_at.is_(None)]
    pending = await db.scalar(
        select(func.count())
        .select_from(ExternalResource)
        .where(*live, ExternalResource.resource_type == TASK, _done.is_not(True))
    )
    projects = await db.scalar(
        select(func.count())
        .select_from(ExternalResource)
        .where(
            *live,
            ExternalResource.resource_type == PROJECT,
            cast(_meta["archived"].astext, Boolean).is_not(True),
        )
    )
    today = await db.scalars(
        select(ExternalResource)
        .where(
            *live,
            ExternalResource.resource_type == TASK,
            _done.is_not(True),
            _due_at < due_until,
        )
        .order_by(_due_at.asc(), ExternalResource.id)
        .limit(today_limit)
    )
    return int(pending or 0), int(projects or 0), list(today)


async def titles_by_id(db: AsyncSession, resource_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = list(set(resource_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(ExternalResource.id, ExternalResource.title).where(ExternalResource.id.in_(ids))
    )
    return {resource_id: title for resource_id, title in rows}
