"""知识入口的业务：AFFiNE 外部地址（知识来源）、文档收藏、可见性与关联、审计。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 3、4 节。
工作台不向 AFFiNE 发任何请求：链接只按 app.domain.deeplinks 做字符串校验。
权限（viewer 读 / member 收藏 / admin 配置来源、工作空间归属）由路由层先行检查；
私有收藏的可见性在每一条查询里过滤（别人的私有收藏一律 404）。
"""

import uuid
from typing import Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.dashboards import version_conflict
from app.domain.deeplinks import DocLink, LinkError, build_url, parse_doc_link
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.tasks import decode_cursor, encode_cursor
from app.models import Bookmark, ExternalResource, KnowledgeSource
from app.repositories import knowledge as repo
from app.repositories import workspaces as workspace_repo
from app.schemas.common import Page
from app.schemas.knowledge import (
    AFFINE_TESTED_VERSION,
    BookmarkCreate,
    BookmarkUpdate,
    KnowledgeResourceOut,
    KnowledgeSourceCreate,
    KnowledgeSourceOut,
    KnowledgeSourceUpdate,
    ResourceLinkOut,
)

MAX_SOURCES_PER_WORKSPACE = 1  # V1（契约第 3 节）


def _invalid(field: str, message: str, reason: str | None = None) -> ApiError:
    details: dict[str, object] = {
        "errors": [{"loc": ["body", field], "msg": message, "type": "value_error"}]
    }
    if reason is not None:
        details["reason"] = reason
    return ApiError(422, "VALIDATION_FAILED", "请求参数校验失败", details=details)


def _no_source() -> ApiError:
    return ApiError(
        409,
        "INTEGRATION_UNAVAILABLE",
        "请先在设置里配置 AFFiNE 地址",
        details={"reason": "not_configured"},
    )


def _exists(existing_id: uuid.UUID) -> ApiError:
    return ApiError(
        409,
        "BOOKMARK_EXISTS",
        "这篇文档已经收藏过了",
        details={"existing_id": str(existing_id)},
    )


def _source_out(source: KnowledgeSource, bookmark_count: int) -> KnowledgeSourceOut:
    return KnowledgeSourceOut(
        id=source.id,
        provider="affine",
        name=source.name,
        base_url=source.base_url,
        tested_version=AFFINE_TESTED_VERSION,
        bookmark_count=bookmark_count,
        version=source.version,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _link_out(resource: ExternalResource) -> ResourceLinkOut:
    return ResourceLinkOut(
        resource_id=resource.id,
        resource_type=cast(Literal["task", "project"], resource.resource_type),
        title=resource.title,
        source_url=resource.source_url,
    )


def _bookmark_out(
    bookmark: Bookmark, source: KnowledgeSource, links: list[ExternalResource]
) -> KnowledgeResourceOut:
    return KnowledgeResourceOut(
        id=bookmark.id,
        source_id=bookmark.source_id,
        provider="affine",
        resource_type="affine_doc",
        title=bookmark.title,
        note=bookmark.note,
        url=build_url(source.base_url, bookmark.suffix),
        link_kind=cast(Literal["workspace_doc", "share"], bookmark.link_kind),
        affine_workspace_id=bookmark.affine_workspace_id,
        doc_id=bookmark.doc_id,
        visibility=cast(Literal["workspace", "private"], bookmark.visibility),
        owner_id=bookmark.owner_id,
        links=[_link_out(r) for r in links],
        version=bookmark.version,
        created_at=bookmark.created_at,
        updated_at=bookmark.updated_at,
    )


class KnowledgeService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 知识来源

    async def list_sources(self, access: WorkspaceAccess) -> Page[KnowledgeSourceOut]:
        sources = await repo.list_sources(self.db, access.workspace.id)
        counts = await repo.count_bookmarks(self.db, [s.id for s in sources])
        items = [_source_out(s, counts.get(s.id, 0)) for s in sources]
        return Page(items=items, next_cursor=None, total_estimate=len(items))

    async def create_source(
        self, context: AuthContext, access: WorkspaceAccess, body: KnowledgeSourceCreate
    ) -> KnowledgeSourceOut:
        workspace_id = access.workspace.id
        # 锁住工作空间行再计数：并发创建时只有一个成功
        await workspace_repo.lock(self.db, workspace_id)
        if len(await repo.list_sources(self.db, workspace_id)) >= MAX_SOURCES_PER_WORKSPACE:
            raise ApiError(409, "LIMIT_EXCEEDED", "每个工作空间只能配置一个 AFFiNE 地址")
        source = KnowledgeSource(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            provider=body.provider,
            name=body.name,
            base_url=body.base_url,
        )
        self.db.add(source)
        await self.db.flush()
        await self.audit.record(
            "knowledge_source.created",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="knowledge_source",
            resource_id=source.id,
            metadata={"provider": body.provider},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(source)
        return _source_out(source, 0)

    async def update_source(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        source_id: uuid.UUID,
        body: KnowledgeSourceUpdate,
    ) -> KnowledgeSourceOut:
        source = await self._locked_source(access, source_id)
        if source.version != body.version:
            raise version_conflict(source.version)
        fields = sorted(body.model_fields_set - {"version"})
        if body.name is not None:
            source.name = body.name
        if body.base_url is not None:
            # 收藏只存 suffix，链接随之改为新地址（契约第 3 节）
            source.base_url = body.base_url
        source.version += 1
        await self.audit.record(
            "knowledge_source.updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="knowledge_source",
            resource_id=source.id,
            metadata={"fields": fields},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(source)
        counts = await repo.count_bookmarks(self.db, [source.id])
        return _source_out(source, counts.get(source.id, 0))

    async def delete_source(
        self, context: AuthContext, access: WorkspaceAccess, source_id: uuid.UUID, version: int
    ) -> None:
        source = await self._locked_source(access, source_id)
        if source.version != version:
            raise version_conflict(source.version)
        count = (await repo.count_bookmarks(self.db, [source.id])).get(source.id, 0)
        await self.db.delete(source)  # 收藏与关联随外键级联删除
        await self.audit.record(
            "knowledge_source.deleted",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="knowledge_source",
            resource_id=source_id,
            metadata={"bookmarks": count},
            client_ip=context.client_ip,
        )
        await self.db.commit()

    async def _locked_source(
        self, access: WorkspaceAccess, source_id: uuid.UUID
    ) -> KnowledgeSource:
        source = await repo.get_source(self.db, access.workspace.id, source_id, lock=True)
        if source is None:
            raise not_found()
        return source

    # ---------------------------------------------------------------- 收藏

    async def list_resources(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        *,
        source_id: uuid.UUID | None,
        linked_resource_id: uuid.UUID | None,
        visibility: str,
        q: str | None,
        sort: str,
        cursor: str | None,
        limit: int,
    ) -> Page[KnowledgeResourceOut]:
        offset = decode_cursor(cursor)
        stmt = repo.list_query(
            access.workspace.id,
            context.user.id,
            source_id=source_id,
            linked_resource_id=linked_resource_id,
            visibility=visibility,
            q=q,
            sort=sort,
        )
        rows = list(await self.db.scalars(stmt.offset(offset).limit(limit + 1)))
        page = rows[:limit]
        sources = {s.id: s for s in await repo.list_sources(self.db, access.workspace.id)}
        links = await repo.links_for(self.db, [b.id for b in page])
        return Page(
            items=[_bookmark_out(b, sources[b.source_id], links.get(b.id, [])) for b in page],
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
        )

    async def create_bookmark(
        self, context: AuthContext, access: WorkspaceAccess, body: BookmarkCreate
    ) -> KnowledgeResourceOut:
        workspace_id = access.workspace.id
        sources = await repo.list_sources(self.db, workspace_id)
        if not sources:
            raise _no_source()
        source = sources[0]
        link = self._parse(body.url, source)
        resources = await self._linkable(access, body.linked_resource_ids)
        if existing := await repo.find_duplicate(
            self.db,
            source_id=source.id,
            affine_workspace_id=link.affine_workspace_id,
            doc_id=link.doc_id,
            visibility=body.visibility,
            owner_id=context.user.id,
        ):
            raise _exists(existing)
        bookmark = Bookmark(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            source_id=source.id,
            title=body.title,
            note=body.note,
            link_kind=link.link_kind,
            affine_workspace_id=link.affine_workspace_id,
            doc_id=link.doc_id,
            suffix=link.suffix,
            visibility=body.visibility,
            owner_id=context.user.id,
        )
        self.db.add(bookmark)
        await self._flush_unique(bookmark, source, context.user.id)
        await repo.replace_links(self.db, bookmark, [r.id for r in resources])
        await self.audit.record(
            "bookmark.created",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="bookmark",
            resource_id=bookmark.id,
            metadata={"visibility": body.visibility, "links": len(resources)},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(bookmark)
        return _bookmark_out(bookmark, source, resources)

    async def update_bookmark(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        bookmark_id: uuid.UUID,
        body: BookmarkUpdate,
    ) -> KnowledgeResourceOut:
        bookmark = await self._visible(context, access, bookmark_id, lock=True)
        if bookmark.version != body.version:
            raise version_conflict(bookmark.version)
        fields = body.model_fields_set - {"version"}
        source = await repo.get_source(self.db, access.workspace.id, bookmark.source_id)
        if source is None:  # 外键保证不会发生
            raise not_found()
        # 先算出修改后的值并做完检查，再改对象：检查用的查询会触发 autoflush
        visibility = body.visibility or bookmark.visibility
        if visibility != bookmark.visibility and bookmark.owner_id != context.user.id:
            raise ApiError(403, "FORBIDDEN", "只有创建者能修改可见性")
        link = self._parse(body.url, source) if body.url is not None else None
        doc = (
            (link.affine_workspace_id, link.doc_id)
            if link
            else (bookmark.affine_workspace_id, bookmark.doc_id)
        )
        if fields & {"url", "visibility"} and (
            existing := await repo.find_duplicate(
                self.db,
                source_id=source.id,
                affine_workspace_id=doc[0],
                doc_id=doc[1],
                visibility=visibility,
                owner_id=bookmark.owner_id or context.user.id,
                exclude_id=bookmark.id,
            )
        ):
            raise _exists(existing)
        resources = (
            await self._linkable(access, body.linked_resource_ids)
            if body.linked_resource_ids is not None
            else None
        )

        bookmark.visibility = visibility
        if link is not None:
            bookmark.link_kind = link.link_kind
            bookmark.affine_workspace_id = link.affine_workspace_id
            bookmark.doc_id = link.doc_id
            bookmark.suffix = link.suffix
        if body.title is not None:
            bookmark.title = body.title
        if body.note is not None:
            bookmark.note = body.note
        bookmark.version += 1
        await self._flush_unique(bookmark, source, context.user.id)
        if resources is not None:
            await repo.replace_links(self.db, bookmark, [r.id for r in resources])
        await self.audit.record(
            "bookmark.updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="bookmark",
            resource_id=bookmark.id,
            metadata={"fields": sorted(fields)},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(bookmark)
        links = await repo.links_for(self.db, [bookmark.id])
        return _bookmark_out(bookmark, source, links.get(bookmark.id, []))

    async def delete_bookmark(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        bookmark_id: uuid.UUID,
        version: int,
    ) -> None:
        bookmark = await self._visible(context, access, bookmark_id, lock=True)
        if bookmark.version != version:
            raise version_conflict(bookmark.version)
        await self.db.delete(bookmark)
        await self.audit.record(
            "bookmark.deleted",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="bookmark",
            resource_id=bookmark_id,
            metadata={},
            client_ip=context.client_ip,
        )
        await self.db.commit()

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _parse(url: str, source: KnowledgeSource) -> DocLink:
        try:
            return parse_doc_link(url, source.base_url)
        except LinkError as error:
            raise _invalid("url", str(error), error.reason) from None

    async def _linkable(
        self, access: WorkspaceAccess, resource_ids: list[uuid.UUID]
    ) -> list[ExternalResource]:
        found = await repo.linkable_resources(self.db, access.workspace.id, resource_ids)
        if len(found) != len(resource_ids):
            raise _invalid("linked_resource_ids", "关联的项目或任务不存在或已删除")
        order = {resource_id: i for i, resource_id in enumerate(resource_ids)}
        return sorted(found, key=lambda r: order[r.id])

    async def _visible(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        bookmark_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> Bookmark:
        bookmark = await repo.get_bookmark(
            self.db, access.workspace.id, context.user.id, bookmark_id, lock=lock
        )
        if bookmark is None:
            raise not_found()
        return bookmark

    async def _flush_unique(
        self, bookmark: Bookmark, source: KnowledgeSource, user_id: uuid.UUID
    ) -> None:
        """并发收藏同一文档时，部分唯一索引兜底：冲突同样返回 409 BOOKMARK_EXISTS。

        调用前对象上的值已经是要写入的值；失败时 savepoint 回滚，事务的其余部分不受影响。
        """
        # 回滚 savepoint 会让对象过期：先取出查重要用的值
        doc = (bookmark.affine_workspace_id, bookmark.doc_id, bookmark.visibility)
        owner_id, bookmark_id = bookmark.owner_id or user_id, bookmark.id
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            existing = await repo.find_duplicate(
                self.db,
                source_id=source.id,
                affine_workspace_id=doc[0],
                doc_id=doc[1],
                visibility=doc[2],
                owner_id=owner_id,
                exclude_id=bookmark_id,
            )
            if existing is None:
                raise
            raise _exists(existing) from None
