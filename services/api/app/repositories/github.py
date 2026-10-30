"""GitHub 投影的数据访问（resource_type：repo / issue / pull / run，都在 external_resources 里）。

Issue、PR、运行的 metadata_json 里有 repo_resource_id（所属仓库的投影 UUID），
按仓库写 tombstone 与过滤都用它。
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExternalResource

REPO = "repo"
ISSUE = "issue"
PULL = "pull"
RUN = "run"
CHILD_TYPES = (ISSUE, PULL, RUN)

_meta = ExternalResource.metadata_json
_repo_ref = _meta["repo_resource_id"].astext


async def repo_by_name(
    db: AsyncSession, integration_id: uuid.UUID, full_name: str
) -> ExternalResource | None:
    """按 full_name 找仓库投影（GitHub 不区分大小写），包括已写 tombstone 的。"""
    return await db.scalar(
        select(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == REPO,
            func.lower(ExternalResource.title) == full_name.lower(),
        )
        .order_by(ExternalResource.synced_at.desc())
        .limit(1)
    )


async def tombstone_children(
    db: AsyncSession,
    integration_id: uuid.UUID,
    repo_resource_id: uuid.UUID,
    resource_types: Iterable[str] = CHILD_TYPES,
) -> None:
    """仓库被移出配置或不再可见：它的 Issue、PR、运行一起写 tombstone。"""
    await db.execute(
        update(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type.in_(list(resource_types)),
            _repo_ref == str(repo_resource_id),
            ExternalResource.deleted_at.is_(None),
        )
        .values(deleted_at=func.now(), synced_at=func.now())
    )


async def tombstone_missing_in_repo(
    db: AsyncSession,
    *,
    integration_id: uuid.UUID,
    resource_type: str,
    repo_resource_id: uuid.UUID,
    seen: Iterable[str],
) -> None:
    """完整列表之后：这个仓库里外部已经没有的资源写 tombstone。"""
    await db.execute(
        update(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == resource_type,
            _repo_ref == str(repo_resource_id),
            ExternalResource.deleted_at.is_(None),
            ExternalResource.external_id.not_in(list(seen)),
        )
        .values(deleted_at=func.now(), synced_at=func.now())
    )


async def tombstone_unconfigured(
    db: AsyncSession, integration_id: uuid.UUID, configured: Iterable[str]
) -> list[uuid.UUID]:
    """不在配置里的仓库写 tombstone，返回它们的投影 UUID（调用方再处理子资源）。"""
    names = [name.lower() for name in configured]
    stmt = (
        update(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.resource_type == REPO,
            ExternalResource.deleted_at.is_(None),
            func.lower(ExternalResource.title).not_in(names),
        )
        .values(deleted_at=func.now(), synced_at=func.now())
        .returning(ExternalResource.id)
    )
    return list(await db.scalars(stmt))


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_query(
    integration_id: uuid.UUID,
    resource_type: str,
    *,
    repo_id: uuid.UUID | None = None,
    state: str | None = None,
    q: str | None = None,
    status: str | None = None,
    conclusion: str | None = None,
) -> Select[ExternalResource]:
    """开发者页的列表（只含未删除的）；排序见 Sprint 4 契约第 7 节。"""
    stmt = select(ExternalResource).where(
        ExternalResource.integration_id == integration_id,
        ExternalResource.resource_type == resource_type,
        ExternalResource.deleted_at.is_(None),
    )
    if repo_id is not None:
        stmt = stmt.where(_repo_ref == str(repo_id))
    if state in {"open", "closed"}:
        stmt = stmt.where(_meta["state"].astext == state)
    if q:
        stmt = stmt.where(ExternalResource.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if status is not None:
        stmt = stmt.where(_meta["status"].astext == status)
    if conclusion is not None:
        stmt = stmt.where(_meta["conclusion"].astext == conclusion)
    if resource_type == REPO:
        return stmt.order_by(func.lower(ExternalResource.title), ExternalResource.id)
    if resource_type == RUN:
        return stmt.order_by(
            _meta["run_started_at"].astext.desc().nulls_last(), ExternalResource.id.desc()
        )
    return stmt.order_by(
        ExternalResource.source_updated_at.desc().nulls_last(), ExternalResource.id.desc()
    )


async def repo_names(db: AsyncSession, repo_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = list(set(repo_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(ExternalResource.id, ExternalResource.title).where(ExternalResource.id.in_(ids))
    )
    return {resource_id: title for resource_id, title in rows.all()}
