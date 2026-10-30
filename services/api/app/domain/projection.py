"""Vikunja 数据 ↔ 投影行 ↔ 接口 DTO 的转换，以及全量同步（Sprint 3 契约第 3、5、6、10 节）。

全量同步是投影的基础：读出全部项目与任务，写入投影，外部已经没有的记 tombstone
（核验 ADR 第 1 节：删除后接口里整条消失，没有“已删除列表”）。增量同步只读上次以来更新过的任务；
Webhook 处理（app/domain/sync.py）复用这里的写入函数。乱序到达的旧数据不会覆盖新数据：
投影写入时比较外部的 updated（repositories/projection.upsert）。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.base import ConnectorError
from app.integrations.vikunja import (
    ProjectReader,
    VikunjaProject,
    VikunjaTask,
    project_source_url,
    task_source_url,
)
from app.models import ExternalResource, Integration
from app.repositories import integrations as integration_repo
from app.repositories import projection as repo
from app.schemas.tasks import ProjectOut, TaskOut

logger = logging.getLogger("app.sync")

# 最近一次全量同步的时间记在这个游标上（周期对账据此决定何时再做全量）
FULL_SYNC = "full_sync"
# 增量同步的时间窗口往前多读一段，抵御事务提交顺序与漏读（规范第 10 节）
INCREMENTAL_OVERLAP = timedelta(minutes=2)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def external_task_id(resource: ExternalResource) -> int:
    return int(resource.external_id)


def external_project_id(resource: ExternalResource) -> int:
    return int(resource.external_id)


async def save_project(
    db: AsyncSession, integration: Integration, project: VikunjaProject
) -> ExternalResource:
    return await repo.upsert(
        db,
        workspace_id=integration.workspace_id,
        integration_id=integration.id,
        resource_type=repo.PROJECT,
        external_id=str(project.id),
        title=project.title,
        source_url=project_source_url(integration.public_url, project.id),
        metadata={"archived": project.archived},
        source_updated_at=project.updated,
    )


async def save_task(
    db: AsyncSession,
    integration: Integration,
    task: VikunjaTask,
    *,
    project_ids: dict[str, uuid.UUID] | None = None,
) -> ExternalResource:
    if project_ids is None:
        project = await repo.get_by_external(
            db,
            integration_id=integration.id,
            resource_type=repo.PROJECT,
            external_id=str(task.project_id),
        )
        project_resource = project.id if project and project.deleted_at is None else None
    else:
        project_resource = project_ids.get(str(task.project_id))
    return await repo.upsert(
        db,
        workspace_id=integration.workspace_id,
        integration_id=integration.id,
        resource_type=repo.TASK,
        external_id=str(task.id),
        title=task.title,
        source_url=task_source_url(integration.public_url, task.id),
        metadata={
            "project_external_id": str(task.project_id),
            "project_resource_id": str(project_resource) if project_resource else None,
            "identifier": task.identifier,
            "description": task.description,
            "done": task.done,
            "done_at": _iso(task.done_at),
            "priority": task.priority,
            "due_at": _iso(task.due_at),
            "labels": list(task.labels),
            "version": task.version,
        },
        source_updated_at=task.updated,
    )


def project_out(resource: ExternalResource) -> ProjectOut:
    return ProjectOut(
        id=resource.id,
        integration_id=resource.integration_id,
        external_id=f"vikunja:project:{resource.external_id}",
        title=resource.title,
        archived=bool(resource.metadata_json.get("archived")),
        source_url=resource.source_url,
        source_updated_at=resource.source_updated_at,
        synced_at=resource.synced_at,
    )


def task_out(resource: ExternalResource) -> TaskOut:
    meta = resource.metadata_json
    project = meta.get("project_resource_id")
    labels = meta.get("labels")
    priority = meta.get("priority")
    return TaskOut(
        id=resource.id,
        integration_id=resource.integration_id,
        project_id=uuid.UUID(project) if isinstance(project, str) else None,
        external_id=f"vikunja:task:{resource.external_id}",
        identifier=meta.get("identifier") if isinstance(meta.get("identifier"), str) else None,
        title=resource.title,
        description=str(meta.get("description") or ""),
        done=bool(meta.get("done")),
        done_at=_parse(meta.get("done_at")),
        priority=priority if isinstance(priority, int) and 0 <= priority <= 5 else 0,
        due_at=_parse(meta.get("due_at")),
        labels=[str(label) for label in labels] if isinstance(labels, list) else [],
        source_url=resource.source_url,
        version=str(meta.get("version") or ""),
        source_updated_at=resource.source_updated_at,
        synced_at=resource.synced_at,
    )


async def _save_projects(
    db: AsyncSession, integration: Integration, projects: list[VikunjaProject]
) -> None:
    """写入全部项目；列表里已经没有的项目连同它的任务记 tombstone。"""
    for project in projects:
        await save_project(db, integration, project)
    removed = await repo.tombstone_missing(
        db,
        integration_id=integration.id,
        resource_type=repo.PROJECT,
        seen=[str(p.id) for p in projects],
    )
    await repo.tombstone_project_tasks(
        db, integration_id=integration.id, project_external_ids=removed
    )


async def _record_failure(
    db: AsyncSession, integration: Integration, error: ConnectorError
) -> None:
    await integration_repo.upsert_cursor(
        db, integration.id, repo.TASK, success_at=None, error=error.reason or error.kind
    )


async def full_sync(db: AsyncSession, integration: Integration, client: ProjectReader) -> None:
    """读出全部项目与任务写入投影，并为外部已删除的记 tombstone；成功后更新同步游标。

    失败时只记录错误码并抛出 ConnectorError，由调用方决定集成状态；投影里已有的数据不动。
    游标与投影在同一个事务里提交（规范第 10 节：一批处理完毕且提交后才前移）。
    """
    started = datetime.now(UTC)
    try:
        projects = await client.list_projects(include_archived=True)
        tasks = await client.list_tasks()
    except ConnectorError as error:
        await _record_failure(db, integration, error)
        raise
    await _save_projects(db, integration, projects)
    project_ids = await repo.project_ids_by_external(db, integration.id)
    for task in tasks:
        await save_task(db, integration, task, project_ids=project_ids)
    removed = await repo.tombstone_missing(
        db, integration_id=integration.id, resource_type=repo.TASK, seen=[str(t.id) for t in tasks]
    )
    newest = max((t.updated for t in tasks), default=None)
    await _advance(db, integration, started, newest)
    await integration_repo.upsert_cursor(db, integration.id, FULL_SYNC, success_at=started)
    logger.info(
        "full sync finished",
        extra={
            "integration_id": str(integration.id),
            "projects": len(projects),
            "tasks": len(tasks),
            "tombstoned_tasks": len(removed),
        },
    )


async def incremental_sync(
    db: AsyncSession, integration: Integration, client: ProjectReader
) -> None:
    """只读上次以来更新过的任务（往前重叠 INCREMENTAL_OVERLAP）；项目每次全量读。

    项目数量少，全量读顺带发现被删除的项目。

    任务的删除在增量里看不到，由定期的全量同步补上。从未同步过时直接做全量。
    """
    cursor = await integration_repo.get_cursor(db, integration.id, repo.TASK)
    since = _parse(cursor.cursor_value) if cursor else None
    if since is None:
        await full_sync(db, integration, client)
        return
    started = datetime.now(UTC)
    try:
        projects = await client.list_projects(include_archived=True)
        tasks = await client.list_tasks(updated_since=since - INCREMENTAL_OVERLAP)
    except ConnectorError as error:
        await _record_failure(db, integration, error)
        raise
    await _save_projects(db, integration, projects)
    project_ids = await repo.project_ids_by_external(db, integration.id)
    for task in tasks:
        await save_task(db, integration, task, project_ids=project_ids)
    newest = max((t.updated for t in tasks), default=since)
    await _advance(db, integration, started, max(newest, since))
    logger.info(
        "incremental sync finished",
        extra={"integration_id": str(integration.id), "tasks": len(tasks)},
    )


async def _advance(
    db: AsyncSession, integration: Integration, started: datetime, newest: datetime | None
) -> None:
    """游标记 Vikunja 自己的 updated（对方的时钟），不受两边时钟偏差影响。"""
    await integration_repo.upsert_cursor(db, integration.id, repo.PROJECT, success_at=started)
    await integration_repo.upsert_cursor(
        db, integration.id, repo.TASK, success_at=started, cursor_value=_iso(newest)
    )
