"""开发者页（Sprint 4）：GitHub 仓库、Issue、PR、Actions 运行，只读本地投影。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间，viewer 可读。sync 表示投影的新鲜度：
被 GitHub 限流时 status 为 stale / unavailable、reason 为 rate_limited 并带 retry_at（A17）。约定见
docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 7 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Security

from app.api.deps import authenticated, workspace_viewer
from app.core.errors import not_implemented
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.developer import (
    GitHubStateFilter,
    IssuePage,
    PullPage,
    RepoPage,
    RunConclusionFilter,
    RunPage,
    RunStatusFilter,
)

router = APIRouter(tags=["developer"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
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
    integration_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> RepoPage:
    raise not_implemented("T-0028")


@router.get(
    "/developer/issues",
    summary="GitHub Issue（viewer，读本地投影；不含 PR）",
    responses=_read_errors,
)
async def list_issues(
    access: Viewer,
    repo_id: UUID | None = None,
    state: GitHubStateFilter = "open",
    q: Title = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> IssuePage:
    raise not_implemented("T-0028")


@router.get(
    "/developer/pulls",
    summary="GitHub PR（viewer，读本地投影）",
    responses=_read_errors,
)
async def list_pulls(
    access: Viewer,
    repo_id: UUID | None = None,
    state: GitHubStateFilter = "open",
    q: Title = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> PullPage:
    raise not_implemented("T-0028")


@router.get(
    "/developer/runs",
    summary="GitHub Actions 最近运行（viewer，读本地投影）",
    description="失败的运行带 failure_summary（失败的 job 与 step 名称，不含日志）。",
    responses=_read_errors,
)
async def list_runs(
    access: Viewer,
    repo_id: UUID | None = None,
    status_filter: Annotated[RunStatusFilter | None, Query(alias="status")] = None,
    conclusion: RunConclusionFilter | None = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> RunPage:
    raise not_implemented("T-0028")
