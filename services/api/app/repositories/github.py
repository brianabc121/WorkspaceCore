"""GitHub 投影的数据访问（resource_type：repo / issue / pull / run，都在 external_resources 里）。

Issue、PR、运行的 metadata_json 里有 repo_resource_id（所属仓库的投影 UUID），
按仓库写 tombstone 与过滤都用它。
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
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


# ---------------------------------------------------------------- 开发者总览（T-0175）
# 只读本地投影的批量聚合：每类数据一条查询，不逐仓库查询，不访问 GitHub。

# 计入“失败运行”的结论：cancelled / skipped / neutral 等不算失败
OVERVIEW_FAILED = ("failure", "timed_out")


def overview_repo_query(
    integration_id: uuid.UUID,
    configured: Iterable[str],
    *,
    repo_id: uuid.UUID | None = None,
    q: str | None = None,
) -> Select[ExternalResource]:
    """当前仓库清单里、未写 tombstone 的仓库（按名称排序）；q 为名称包含（转义 LIKE 通配符）。"""
    names = [name.lower() for name in configured]
    stmt = select(ExternalResource).where(
        ExternalResource.integration_id == integration_id,
        ExternalResource.resource_type == REPO,
        ExternalResource.deleted_at.is_(None),
        func.lower(ExternalResource.title).in_(names),
    )
    if repo_id is not None:
        stmt = stmt.where(ExternalResource.id == repo_id)
    if q:
        stmt = stmt.where(ExternalResource.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
    return stmt.order_by(func.lower(ExternalResource.title), ExternalResource.id)


def _children(integration_id: uuid.UUID, repo_ids: list[uuid.UUID], *kinds: str) -> Any:
    return and_(
        ExternalResource.integration_id == integration_id,
        ExternalResource.resource_type.in_(kinds),
        ExternalResource.deleted_at.is_(None),
        _repo_ref.in_([str(value) for value in repo_ids]),
    )


def _failed_run() -> Any:
    return and_(
        ExternalResource.resource_type == RUN,
        _meta["status"].astext == "completed",
        _meta["conclusion"].astext.in_(OVERVIEW_FAILED),
    )


def _open_pull() -> Any:
    """开放 PR：state=open，且没有合并记录（防御：已合并的不算开放）。"""
    return and_(
        ExternalResource.resource_type == PULL,
        _meta["state"].astext == "open",
        func.coalesce(_meta["merged"].astext, "false") != "true",
        _meta["merged_at"].astext.is_(None),
    )


async def overview_counts(
    db: AsyncSession,
    integration_id: uuid.UUID,
    repo_ids: list[uuid.UUID],
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, dict[str, int]]:
    """每个仓库的开放 Issue / 开放 PR / 窗内失败运行 / 缺时间的失败运行（一次分组查询）。"""
    if not repo_ids:
        return {}
    failed = _failed_run()
    stmt = (
        select(
            _repo_ref.label("repo"),
            func.count()
            .filter(ExternalResource.resource_type == ISSUE, _meta["state"].astext == "open")
            .label("issues"),
            func.count().filter(_open_pull()).label("pulls"),
            func.count()
            .filter(
                failed,
                ExternalResource.source_updated_at >= window_start,
                ExternalResource.source_updated_at < window_end,
            )
            .label("failed"),
            func.count()
            .filter(failed, ExternalResource.source_updated_at.is_(None))
            .label("untimed"),
        )
        .where(_children(integration_id, repo_ids, ISSUE, PULL, RUN))
        .group_by(_repo_ref)
    )
    return {
        str(row.repo): {
            "issues": int(row.issues),
            "pulls": int(row.pulls),
            "failed": int(row.failed),
            "untimed": int(row.untimed),
        }
        for row in (await db.execute(stmt)).all()
    }


async def latest_runs(
    db: AsyncSession, integration_id: uuid.UUID, repo_ids: list[uuid.UUID]
) -> dict[str, ExternalResource]:
    """每个仓库最近一次运行（按开始时间，同时间按 id），一次 DISTINCT ON 查询。"""
    if not repo_ids:
        return {}
    stmt = (
        select(ExternalResource)
        .where(_children(integration_id, repo_ids, RUN))
        .ext(distinct_on(_repo_ref))
        .order_by(
            _repo_ref,
            _meta["run_started_at"].astext.desc().nulls_last(),
            ExternalResource.source_updated_at.desc().nulls_last(),
            ExternalResource.id.desc(),
        )
    )
    return {str(row.metadata_json["repo_resource_id"]): row for row in await db.scalars(stmt)}


async def attention_pulls(
    db: AsyncSession, integration_id: uuid.UUID, repo_ids: list[uuid.UUID], *, limit: int
) -> list[ExternalResource]:
    """开放 PR：已请求审查（requested_reviewers 非空）的在前，再按 GitHub 更新时间倒序、id 稳定。"""
    if not repo_ids:
        return []
    reviewers = _meta["requested_reviewers"]
    # 不是数组（缺字段 / null）时按没有请求审查处理；CASE 保证只对数组取长度
    reviewer_count = case(
        (func.jsonb_typeof(reviewers) == "array", func.jsonb_array_length(reviewers)), else_=0
    )
    stmt = (
        select(ExternalResource)
        .where(_children(integration_id, repo_ids, PULL), _open_pull())
        .order_by(
            (reviewer_count > 0).desc(),
            ExternalResource.source_updated_at.desc().nulls_last(),
            ExternalResource.id.desc(),
        )
        .limit(limit)
    )
    return list(await db.scalars(stmt))


async def failed_runs(
    db: AsyncSession,
    integration_id: uuid.UUID,
    repo_ids: list[uuid.UUID],
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[ExternalResource]:
    """时间窗内更新的失败运行（按 GitHub 更新时间倒序）。"""
    if not repo_ids:
        return []
    stmt = (
        select(ExternalResource)
        .where(
            _children(integration_id, repo_ids, RUN),
            _failed_run(),
            ExternalResource.source_updated_at >= window_start,
            ExternalResource.source_updated_at < window_end,
        )
        .order_by(ExternalResource.source_updated_at.desc(), ExternalResource.id.desc())
        .limit(limit)
    )
    return list(await db.scalars(stmt))


async def recent_updates(
    db: AsyncSession, integration_id: uuid.UUID, repo_ids: list[uuid.UUID], *, limit: int
) -> list[ExternalResource]:
    """所选仓库及其 Issue / PR / 运行里最近更新的投影（没有 GitHub 修改时间的按同步时间）。"""
    if not repo_ids:
        return []
    moment = func.coalesce(ExternalResource.source_updated_at, ExternalResource.synced_at)
    stmt = (
        select(ExternalResource)
        .where(
            ExternalResource.integration_id == integration_id,
            ExternalResource.deleted_at.is_(None),
            or_(
                and_(ExternalResource.resource_type == REPO, ExternalResource.id.in_(repo_ids)),
                _children(integration_id, repo_ids, *CHILD_TYPES),
            ),
        )
        .order_by(moment.desc(), ExternalResource.id.desc())
        .limit(limit)
    )
    return list(await db.scalars(stmt))
