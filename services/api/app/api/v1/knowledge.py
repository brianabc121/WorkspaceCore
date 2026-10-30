"""知识入口（Sprint 4）：AFFiNE 外部地址（知识来源）与文档收藏。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可读，member 及以上可收藏，
admin 及以上配置知识来源；私有收藏只有创建者能看到。工作台不向 AFFiNE 发任何请求。约定见
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 3、4 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Security, status

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_knowledge_service,
    workspace_admin,
    workspace_member,
    workspace_viewer,
)
from app.domain.auth import AuthContext
from app.domain.knowledge import KnowledgeService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import Page, error_responses
from app.schemas.knowledge import (
    BookmarkCreate,
    BookmarkUpdate,
    KnowledgeResourceOut,
    KnowledgeSort,
    KnowledgeSourceCreate,
    KnowledgeSourceOut,
    KnowledgeSourceUpdate,
    KnowledgeVisibilityFilter,
    Version,
)

router = APIRouter(tags=["knowledge"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[KnowledgeService, Depends(get_knowledge_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)


# ---------------------------------------------------------------- 知识来源


@router.get(
    "/knowledge/sources",
    summary="知识来源（AFFiNE 外部地址）列表（viewer）",
    responses=_read_errors,
)
async def list_sources(access: Viewer, service: Service) -> Page[KnowledgeSourceOut]:
    return await service.list_sources(access)


@router.post(
    "/knowledge/sources",
    status_code=status.HTTP_201_CREATED,
    summary="配置 AFFiNE 外部地址（admin）",
    description="只检查地址字符串，工作台从不访问它；"
    "每个工作空间最多 1 个（第 2 个返回 409 LIMIT_EXCEEDED）。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_source(
    access: Admin, context: Context, service: Service, body: KnowledgeSourceCreate
) -> KnowledgeSourceOut:
    return await service.create_source(context, access, body)


@router.patch(
    "/knowledge/sources/{source_id}",
    summary="修改知识来源（admin，乐观并发）",
    description="修改 base_url 后，该来源下所有收藏的链接都随之改为新地址。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_source(
    access: Admin, context: Context, service: Service, source_id: UUID, body: KnowledgeSourceUpdate
) -> KnowledgeSourceOut:
    return await service.update_source(context, access, source_id, body)


@router.delete(
    "/knowledge/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除知识来源（admin；连同它的全部收藏，前端先二次确认）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_source(
    access: Admin,
    context: Context,
    service: Service,
    source_id: UUID,
    version: Annotated[Version, Query()],
) -> Response:
    await service.delete_source(context, access, source_id, version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- 收藏


@router.get(
    "/knowledge/resources",
    summary="文档收藏列表（viewer；私有收藏只返回自己的）",
    description="linked_resource_id 只返回关联了该 Vikunja 项目 / 任务的收藏"
    "（任务抽屉的“相关文档”）。",
    responses=_read_errors,
)
async def list_resources(
    access: Viewer,
    context: Context,
    service: Service,
    source_id: UUID | None = None,
    linked_resource_id: UUID | None = None,
    visibility: KnowledgeVisibilityFilter = "all",
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    sort: KnowledgeSort = "updated",
    cursor: Cursor = None,
    limit: Limit = 30,
) -> Page[KnowledgeResourceOut]:
    return await service.list_resources(
        context,
        access,
        source_id=source_id,
        linked_resource_id=linked_resource_id,
        visibility=visibility,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/knowledge/bookmarks",
    status_code=status.HTTP_201_CREATED,
    summary="收藏 AFFiNE 文档（member）",
    description="链接按核验 ADR 第 8.3 节校验（不合规返回 422，details.reason 说明原因）；"
    "没有配置知识来源时返回 409 INTEGRATION_UNAVAILABLE；同一文档已有同样可见性的收藏时返回 "
    "409 BOOKMARK_EXISTS（details.existing_id）。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_bookmark(
    access: Member, context: Context, service: Service, body: BookmarkCreate
) -> KnowledgeResourceOut:
    return await service.create_bookmark(context, access, body)


@router.patch(
    "/knowledge/bookmarks/{bookmark_id}",
    summary="修改收藏（member，乐观并发；只有创建者能改 visibility）",
    dependencies=_write,
    responses=_write_errors,
)
async def update_bookmark(
    access: Member, context: Context, service: Service, bookmark_id: UUID, body: BookmarkUpdate
) -> KnowledgeResourceOut:
    return await service.update_bookmark(context, access, bookmark_id, body)


@router.delete(
    "/knowledge/bookmarks/{bookmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除收藏（member；私有收藏只有创建者能删）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_bookmark(
    access: Member,
    context: Context,
    service: Service,
    bookmark_id: UUID,
    version: Annotated[Version, Query()],
) -> Response:
    await service.delete_bookmark(context, access, bookmark_id, version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
