"""开发者页（Sprint 4）：GitHub 仓库、Issue、PR、Actions 运行，只读本地投影。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间，viewer 可读。sync 表示投影的新鲜度：
被 GitHub 限流时 status 为 stale / unavailable、reason 为 rate_limited 并带 retry_at（A17）。约定见
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 7 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import authenticated, get_developer_service, workspace_viewer
from app.domain.developer import DeveloperService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.developer import (
    DeveloperOverviewOut,
    GitHubStateFilter,
    IssuePage,
    OverviewRange,
    PullPage,
    RepoPage,
    RepositoryPermissionEvidenceOut,
    RunConclusionFilter,
    RunPage,
    RunStatusFilter,
)

router = APIRouter(tags=["developer"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Service = Annotated[DeveloperService, Depends(get_developer_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
Title = Annotated[str | None, Query(min_length=1, max_length=100, description="标题包含")]
_read_errors = error_responses(401, 404, 422)


@router.get(
    "/developer/repos",
    summary="GitHub 仓库（viewer，读本地投影）",
    responses=_read_errors,
)
async def list_repos(
    access: Viewer,
    service: Service,
    integration_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> RepoPage:
    return await service.repos(access, integration_id=integration_id, cursor=cursor, limit=limit)


@router.get(
    "/developer/issues",
    summary="GitHub Issue（viewer，读本地投影；不含 PR）",
    responses=_read_errors,
)
async def list_issues(
    access: Viewer,
    service: Service,
    repo_id: UUID | None = None,
    state: GitHubStateFilter = "open",
    q: Title = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> IssuePage:
    return await service.issues(
        access, repo_id=repo_id, state=state, q=q, cursor=cursor, limit=limit
    )


@router.get(
    "/developer/pulls",
    summary="GitHub PR（viewer，读本地投影）",
    responses=_read_errors,
)
async def list_pulls(
    access: Viewer,
    service: Service,
    repo_id: UUID | None = None,
    state: GitHubStateFilter = "open",
    q: Title = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> PullPage:
    return await service.pulls(
        access, repo_id=repo_id, state=state, q=q, cursor=cursor, limit=limit
    )


@router.get(
    "/developer/runs",
    summary="GitHub Actions 最近运行（viewer，读本地投影）",
    description="失败的运行带 failure_summary（失败的 job 与 step 名称，不含日志）。",
    responses=_read_errors,
)
async def list_runs(
    access: Viewer,
    service: Service,
    repo_id: UUID | None = None,
    status_filter: Annotated[RunStatusFilter | None, Query(alias="status")] = None,
    conclusion: RunConclusionFilter | None = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> RunPage:
    return await service.runs(
        access,
        repo_id=repo_id,
        status=status_filter,
        conclusion=conclusion,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/developer/overview",
    summary="开发者总览（viewer，读本地投影）",
    description="所选仓库范围的已同步统计、仓库概览（分页）、开放 PR、时间窗内失败运行与最近更新。"
    "只读本地 GitHub 投影：不访问 GitHub，不触发同步或测试连接。统计不能确定时为 null 并标 partial"
    "（缺少权限 / 仓库不可见 / 从未成功同步 / 失败运行缺时间），不当成 0。"
    "range 只控制失败运行的时间窗（按 GitHub 更新时间）；开放 Issue / PR 是当前状态。"
    "Token 被拒、未配置或停用时不返回任何记录，统计为 null；"
    "被限流时照常返回缓存并在 sync 里带 retry_at。"
    "cursor 只能在同一工作空间、集成与筛选条件下使用，否则 422。",
    responses=_read_errors,
)
async def developer_overview(
    access: Viewer,
    service: Service,
    repo_id: UUID | None = None,
    q: Annotated[
        str | None, Query(min_length=1, max_length=100, description="仓库名称包含")
    ] = None,
    window: Annotated[
        OverviewRange, Query(alias="range", description="失败运行的时间窗：24h / 7d")
    ] = "7d",
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 12,
) -> DeveloperOverviewOut:
    return await service.overview(
        access, repo_id=repo_id, q=q, window=window, cursor=cursor, limit=limit
    )


@router.get(
    "/developer/repos/{repo_id}/permissions",
    summary="仓库的读权限核验快照（viewer）",
    description="管理员上次“测试连接”时这个仓库四项读权限（metadata / issues / pull_requests / "
    "actions）的实际结果与核验时间；没有执行的项不出现在 checks 里（未核验），"
    "不从 Repo.access 推导。"
    "只读快照，不触发测试连接。仓库不在当前仓库清单里时 404；Token 被拒或未配置时不返回旧结果。",
    responses=_read_errors,
)
async def repository_permissions(
    access: Viewer, service: Service, repo_id: UUID
) -> RepositoryPermissionEvidenceOut:
    return await service.repository_permissions(access, repo_id)
