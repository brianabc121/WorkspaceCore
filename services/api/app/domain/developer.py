"""开发者页：读 GitHub 的本地投影（Sprint 4 契约第 7 节），不访问 GitHub。

每个列表都带 sync 新鲜度；限流时 reason 为 rate_limited 并带 retry_at（A17）。
没有 GitHub 集成时返回空列表与 not_configured。权限（viewer 可读、工作空间归属）由路由层检查；
投影按集成过滤，集成属于当前工作空间。
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.connectors import sync_state
from app.domain.github_sync import CURSOR
from app.domain.policy import WorkspaceAccess
from app.domain.tasks import decode_cursor, encode_cursor
from app.integrations.github import PROVIDER
from app.models import ExternalResource, Integration
from app.repositories import github as repo
from app.repositories import integrations as integration_repo
from app.schemas.developer import (
    FailureSummary,
    IssueOut,
    IssuePage,
    PullOut,
    PullPage,
    RepoOut,
    RepoPage,
    RunOut,
    RunPage,
)
from app.schemas.integrations import SyncState

PageT = TypeVar("PageT", RepoPage, IssuePage, PullPage, RunPage)


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _base(row: ExternalResource) -> dict[str, Any]:
    return {
        "id": row.id,
        "integration_id": row.integration_id,
        "external_id": f"github:{row.resource_type}:{row.external_id}",
        "source_url": row.source_url,
        "source_updated_at": row.source_updated_at,
        "synced_at": row.synced_at,
    }


def _in_repo(row: ExternalResource) -> dict[str, Any]:
    meta = row.metadata_json
    return {
        **_base(row),
        "repo_id": uuid.UUID(str(meta["repo_resource_id"])),
        "repo_full_name": str(meta.get("repo_full_name") or ""),
    }


def repo_out(row: ExternalResource) -> RepoOut:
    meta = row.metadata_json
    return RepoOut(
        **_base(row),
        full_name=row.title,
        description=meta.get("description"),
        private=bool(meta.get("private")),
        archived=bool(meta.get("archived")),
        default_branch=str(meta.get("default_branch") or ""),
        pushed_at=_dt(meta.get("pushed_at")),
        access=meta.get("access") or "ok",
    )


def issue_out(row: ExternalResource) -> IssueOut:
    meta = row.metadata_json
    return IssueOut(
        **_in_repo(row),
        number=int(meta.get("number") or 0),
        title=row.title,
        state="closed" if meta.get("state") == "closed" else "open",
        state_reason=meta.get("state_reason"),
        author=meta.get("author"),
        labels=list(meta.get("labels") or []),
        assignees=list(meta.get("assignees") or []),
        comments=int(meta.get("comments") or 0),
        created_at=_dt(meta.get("created_at")) or row.synced_at,
        closed_at=_dt(meta.get("closed_at")),
    )


def pull_out(row: ExternalResource) -> PullOut:
    meta = row.metadata_json
    return PullOut(
        **_in_repo(row),
        number=int(meta.get("number") or 0),
        title=row.title,
        state="closed" if meta.get("state") == "closed" else "open",
        draft=bool(meta.get("draft")),
        merged=bool(meta.get("merged")),
        merged_at=_dt(meta.get("merged_at")),
        author=meta.get("author"),
        labels=list(meta.get("labels") or []),
        requested_reviewers=list(meta.get("requested_reviewers") or []),
        head_ref=str(meta.get("head_ref") or ""),
        base_ref=str(meta.get("base_ref") or ""),
        created_at=_dt(meta.get("created_at")) or row.synced_at,
        closed_at=_dt(meta.get("closed_at")),
    )


def run_out(row: ExternalResource) -> RunOut:
    meta = row.metadata_json
    summary = meta.get("failure_summary")
    return RunOut(
        **_in_repo(row),
        name=meta.get("name"),
        display_title=row.title,
        status=meta.get("status"),
        conclusion=meta.get("conclusion"),
        event=str(meta.get("event") or ""),
        head_branch=meta.get("head_branch"),
        head_sha=str(meta.get("head_sha") or ""),
        run_number=int(meta.get("run_number") or 0),
        run_attempt=int(meta.get("run_attempt") or 1),
        actor=meta.get("actor"),
        run_started_at=_dt(meta.get("run_started_at")),
        failure_summary=FailureSummary.model_validate(summary) if summary else None,
    )


class DeveloperService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def _integration(self, access: WorkspaceAccess) -> tuple[Integration | None, SyncState]:
        integration = await integration_repo.get_provider(self.db, access.workspace.id, PROVIDER)
        cursor = (
            await integration_repo.get_cursor(self.db, integration.id, CURSOR)
            if integration
            else None
        )
        state = sync_state(
            integration, cursor, stale_after_seconds=self.settings.sync_stale_after_seconds
        )
        return integration, state

    async def _page(
        self,
        access: WorkspaceAccess,
        page_type: type[PageT],
        resource_type: str,
        convert: Callable[[ExternalResource], BaseModel],
        *,
        cursor: str | None,
        limit: int,
        integration_id: uuid.UUID | None = None,
        **filters: Any,
    ) -> PageT:
        offset = decode_cursor(cursor)
        integration, state = await self._integration(access)
        if integration is None or (integration_id is not None and integration.id != integration_id):
            return page_type(items=[], next_cursor=None, total_estimate=0, sync=state)
        stmt = repo.list_query(integration.id, resource_type, **filters)
        rows = list(await self.db.scalars(stmt.offset(offset).limit(limit + 1)))
        return page_type(
            items=[convert(r) for r in rows[:limit]],
            next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
            sync=state,
        )

    async def repos(
        self,
        access: WorkspaceAccess,
        *,
        integration_id: uuid.UUID | None,
        cursor: str | None,
        limit: int,
    ) -> RepoPage:
        return await self._page(
            access,
            RepoPage,
            repo.REPO,
            repo_out,
            cursor=cursor,
            limit=limit,
            integration_id=integration_id,
        )

    async def issues(
        self,
        access: WorkspaceAccess,
        *,
        repo_id: uuid.UUID | None,
        state: str,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> IssuePage:
        return await self._page(
            access,
            IssuePage,
            repo.ISSUE,
            issue_out,
            cursor=cursor,
            limit=limit,
            repo_id=repo_id,
            state=state,
            q=q,
        )

    async def pulls(
        self,
        access: WorkspaceAccess,
        *,
        repo_id: uuid.UUID | None,
        state: str,
        q: str | None,
        cursor: str | None,
        limit: int,
    ) -> PullPage:
        return await self._page(
            access,
            PullPage,
            repo.PULL,
            pull_out,
            cursor=cursor,
            limit=limit,
            repo_id=repo_id,
            state=state,
            q=q,
        )

    async def runs(
        self,
        access: WorkspaceAccess,
        *,
        repo_id: uuid.UUID | None,
        status: str | None,
        conclusion: str | None,
        cursor: str | None,
        limit: int,
    ) -> RunPage:
        return await self._page(
            access,
            RunPage,
            repo.RUN,
            run_out,
            cursor=cursor,
            limit=limit,
            repo_id=repo_id,
            status=status,
            conclusion=conclusion,
        )
