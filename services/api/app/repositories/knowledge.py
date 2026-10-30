"""知识来源、收藏与关联的数据访问。查询一律限定工作空间；收藏的可见性在这里统一过滤。"""

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bookmark, ExternalResource, KnowledgeSource, ResourceLink

LINKABLE_TYPES = ("task", "project")


def visible_to(user_id: uuid.UUID) -> ColumnElement[bool]:
    """工作空间可见的收藏，加上这个用户自己的私有收藏（契约第 4.3 节）。"""
    return or_(
        Bookmark.visibility == "workspace",
        and_(Bookmark.visibility == "private", Bookmark.owner_id == user_id),
    )


# ---------------------------------------------------------------- 知识来源


async def list_sources(db: AsyncSession, workspace_id: uuid.UUID) -> list[KnowledgeSource]:
    return list(
        await db.scalars(
            select(KnowledgeSource)
            .where(KnowledgeSource.workspace_id == workspace_id)
            .order_by(KnowledgeSource.created_at, KnowledgeSource.id)
        )
    )


async def get_source(
    db: AsyncSession, workspace_id: uuid.UUID, source_id: uuid.UUID, *, lock: bool = False
) -> KnowledgeSource | None:
    stmt = select(KnowledgeSource).where(
        KnowledgeSource.id == source_id, KnowledgeSource.workspace_id == workspace_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def count_bookmarks(
    db: AsyncSession, source_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """每个来源的收藏数（含所有人的私有收藏）。"""
    if not source_ids:
        return {}
    rows = await db.execute(
        select(Bookmark.source_id, func.count())
        .where(Bookmark.source_id.in_(source_ids))
        .group_by(Bookmark.source_id)
    )
    return {source_id: int(count) for source_id, count in rows.all()}


# ---------------------------------------------------------------- 收藏


async def get_bookmark(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    bookmark_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Bookmark | None:
    """别人的私有收藏与不存在一样（返回 None → 404）。"""
    stmt = select(Bookmark).where(
        Bookmark.id == bookmark_id, Bookmark.workspace_id == workspace_id, visible_to(user_id)
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def find_duplicate(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    affine_workspace_id: str,
    doc_id: str,
    visibility: str,
    owner_id: uuid.UUID,
    exclude_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """与部分唯一索引相同的去重范围：工作空间可见的一条 / 每个用户自己的私有一条。"""
    stmt = select(Bookmark.id).where(
        Bookmark.source_id == source_id,
        Bookmark.affine_workspace_id == affine_workspace_id,
        Bookmark.doc_id == doc_id,
        Bookmark.visibility == visibility,
    )
    if visibility == "private":
        stmt = stmt.where(Bookmark.owner_id == owner_id)
    if exclude_id is not None:
        stmt = stmt.where(Bookmark.id != exclude_id)
    return await db.scalar(stmt.limit(1))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_query(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    source_id: uuid.UUID | None,
    linked_resource_id: uuid.UUID | None,
    visibility: str,
    q: str | None,
    sort: str,
) -> Select[Bookmark]:
    stmt = select(Bookmark).where(Bookmark.workspace_id == workspace_id, visible_to(user_id))
    if source_id is not None:
        stmt = stmt.where(Bookmark.source_id == source_id)
    if visibility == "workspace":
        stmt = stmt.where(Bookmark.visibility == "workspace")
    elif visibility == "private":
        stmt = stmt.where(Bookmark.visibility == "private", Bookmark.owner_id == user_id)
    if linked_resource_id is not None:
        stmt = stmt.where(
            select(ResourceLink.id)
            .join(ExternalResource, ExternalResource.id == ResourceLink.target_resource_id)
            .where(
                ResourceLink.source_resource_id == Bookmark.id,
                ResourceLink.target_resource_id == linked_resource_id,
                ExternalResource.deleted_at.is_(None),
            )
            .exists()
        )
    if q:
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Bookmark.title.ilike(pattern, escape="\\"),
                Bookmark.note.ilike(pattern, escape="\\"),
            )
        )
    if sort == "title":
        return stmt.order_by(func.lower(Bookmark.title), Bookmark.id)
    return stmt.order_by(Bookmark.updated_at.desc(), Bookmark.id.desc())


# ---------------------------------------------------------------- 关联


async def linkable_resources(
    db: AsyncSession, workspace_id: uuid.UUID, resource_ids: Sequence[uuid.UUID]
) -> list[ExternalResource]:
    """本工作空间中未删除的项目 / 任务投影。"""
    if not resource_ids:
        return []
    return list(
        await db.scalars(
            select(ExternalResource).where(
                ExternalResource.id.in_(resource_ids),
                ExternalResource.workspace_id == workspace_id,
                ExternalResource.resource_type.in_(LINKABLE_TYPES),
                ExternalResource.deleted_at.is_(None),
            )
        )
    )


async def replace_links(
    db: AsyncSession, bookmark: Bookmark, resource_ids: Sequence[uuid.UUID]
) -> None:
    await db.execute(delete(ResourceLink).where(ResourceLink.source_resource_id == bookmark.id))
    db.add_all(
        ResourceLink(
            workspace_id=bookmark.workspace_id,
            source_resource_id=bookmark.id,
            target_resource_id=resource_id,
        )
        for resource_id in resource_ids
    )


async def links_for(
    db: AsyncSession, bookmark_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[ExternalResource]]:
    """每个收藏关联的、仍然存在的项目 / 任务（写了 tombstone 的不返回，A10）。"""
    if not bookmark_ids:
        return {}
    rows = await db.execute(
        select(ResourceLink.source_resource_id, ExternalResource)
        .join(ExternalResource, ExternalResource.id == ResourceLink.target_resource_id)
        .where(
            ResourceLink.source_resource_id.in_(bookmark_ids),
            ExternalResource.deleted_at.is_(None),
        )
        .order_by(ResourceLink.created_at, ResourceLink.id)
    )
    links: dict[uuid.UUID, list[ExternalResource]] = {}
    for bookmark_id, resource in rows.all():
        links.setdefault(bookmark_id, []).append(resource)
    return links
