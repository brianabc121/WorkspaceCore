"""跨资源搜索（Sprint 4 契约第 8 节；规范第 5.3 节；A10、A11）。

索引表 search_documents 由数据库触发器维护（迁移 0008），这里只查询：

- 每次查询都限定工作空间；私有收藏只有创建者可见（owner_id 为 NULL 的文档对所有成员可见）；
  删除或写了 tombstone 的资源，触发器已经把它移出索引。
- 匹配：标题或正文包含（ILIKE，中文按字面包含，不分词），或英文全文检索（tsvector，带词干）。
- 排序：标题完全匹配 > 标题前缀 > 标题包含（含英文词干命中）> 只在正文命中；
  同一档按更新时间倒序，再按 id。
"""

import uuid
from typing import cast
from urllib.parse import urlsplit

from sqlalchemy import and_, case, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.policy import WorkspaceAccess
from app.domain.tasks import decode_cursor, encode_cursor
from app.models import Bookmark, KnowledgeSource, SearchDocument
from app.schemas.search import SearchPage, SearchProvider, SearchResultOut, SearchType

_CONFIG = literal("english").cast(REGCONFIG)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _safe_url(url: str | None) -> str | None:
    """只返回 http(s) 链接（规范 5.3：禁止脚本 URL）。"""
    if not url:
        return None
    return url if urlsplit(url).scheme in {"http", "https"} else None


async def rebuild(db: AsyncSession) -> int:
    """按当前数据整体重建索引，返回索引条数。"""
    count = int(await db.scalar(text("SELECT workspace.search_rebuild()")) or 0)
    await db.commit()
    return count


class SearchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def search(
        self,
        user_id: uuid.UUID,
        access: WorkspaceAccess,
        *,
        q: str,
        types: list[SearchType] | None,
        cursor: str | None,
        limit: int,
    ) -> SearchPage:
        offset = decode_cursor(cursor)
        d = SearchDocument
        pattern = f"%{_escape_like(q)}%"
        query = func.plainto_tsquery(_CONFIG, q)
        title_hit = or_(
            d.title.ilike(pattern, escape="\\"),
            func.to_tsvector(_CONFIG, d.title).op("@@")(query),
        )
        bucket = case(
            (func.lower(d.title) == q.lower(), 0),
            (d.title.ilike(f"{_escape_like(q)}%", escape="\\"), 1),
            (title_hit, 2),
            else_=3,
        )
        url = func.coalesce(d.source_url, KnowledgeSource.base_url + Bookmark.suffix)
        stmt = (
            select(d, bucket.label("bucket"), url.label("url"))
            .outerjoin(Bookmark, and_(d.resource_type == "bookmark", Bookmark.id == d.resource_id))
            .outerjoin(KnowledgeSource, KnowledgeSource.id == Bookmark.source_id)
            .where(
                d.workspace_id == access.workspace.id,
                or_(d.owner_id.is_(None), d.owner_id == user_id),
                or_(
                    title_hit,
                    d.body.ilike(pattern, escape="\\"),
                    d.search_vector.op("@@")(query),
                ),
            )
            .order_by(bucket, d.source_updated_at.desc().nulls_last(), d.id)
            .offset(offset)
            .limit(limit + 1)
        )
        if types:
            stmt = stmt.where(d.resource_type.in_(list(types)))
        rows = (await self.db.execute(stmt)).all()
        items = [
            SearchResultOut(
                resource_type=cast(SearchType, doc.resource_type),
                id=doc.resource_id,
                title=doc.title,
                context=doc.context,
                provider=cast(SearchProvider, doc.provider),
                source_url=_safe_url(link),
                updated_at=doc.source_updated_at,
                match="title" if level < 3 else "text",
            )
            for doc, level, link in rows[:limit]
        ]
        return SearchPage(
            items=items,
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )
