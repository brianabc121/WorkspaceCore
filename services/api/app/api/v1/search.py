"""跨资源搜索（Sprint 4）：任务、项目、收藏、日程、GitHub 仓库 / Issue / PR。

工作空间范围，用 X-Workspace-Id 指定工作空间，viewer 可用；只返回当前用户可见、且未删除的资源。
约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 8 节。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import authenticated, get_search_service, workspace_viewer
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.domain.search import SearchService
from app.schemas.common import error_responses
from app.schemas.search import SearchPage, SearchQuery, SearchType

router = APIRouter(tags=["search"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[SearchService, Depends(get_search_service)]


@router.get(
    "/search",
    summary="跨资源搜索（viewer）",
    description="中文按字面包含与相似度匹配（不分词），相关性有限；排序见契约第 8 节。",
    responses=error_responses(401, 404, 422),
)
async def search(
    access: Viewer,
    context: Context,
    service: Service,
    q: Annotated[SearchQuery, Query(description="去掉首尾空白后 1–100 字")],
    types: Annotated[list[SearchType] | None, Query(description="可重复；默认全部类型")] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> SearchPage:
    return await service.search(context, access, q=q, types=types, cursor=cursor, limit=limit)
