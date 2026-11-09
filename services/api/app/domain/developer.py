"""开发者页：读 GitHub 的本地投影（Sprint 4 契约第 7 节），不访问 GitHub。

每个列表都带 sync 新鲜度；限流时 reason 为 rate_limited 并带 retry_at（A17）。
没有 GitHub 集成时返回空列表与 not_configured。权限（viewer 可读、工作空间归属）由路由层检查；
投影按集成过滤，集成属于当前工作空间。
"""

import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import github_permission_evidence as evidence
from app.domain import github_sync
from app.domain.connectors import sync_state
from app.domain.github_sync import CURSOR
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.tasks import decode_cursor, encode_cursor
from app.integrations.github import PROVIDER
from app.models import ExternalResource, Integration
from app.repositories import github as repo
from app.repositories import integrations as integration_repo
from app.schemas.developer import (
    DeveloperOverviewOut,
    DeveloperOverviewSummary,
    FailureSummary,
    GitHubRecentUpdate,
    IssueOut,
    IssuePage,
    OverviewRange,
    PullOut,
    PullPage,
    RepoOut,
    RepoOverviewPage,
    RepoOverviewRow,
    RepoPage,
    RepositoryPermissionEvidenceOut,
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


# ---------------------------------------------------------------- 开发者总览（T-0175）

_WINDOWS: dict[str, timedelta] = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
ATTENTION_PULLS = 5
FAILED_RUNS = 3
RECENT_UPDATES = 5
# Token 被拒、未配置或停用：不返回任何私有记录，统计一律未知
_WITHHELD = frozenset({"unauthorized", "not_configured"})


def _bad_cursor() -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": ["query", "cursor"], "msg": "游标无效", "type": "value_error"}]
        },
    )


def _scope_key(*parts: object) -> str:
    """游标绑定的范围：工作空间、集成与全部筛选条件；换了任何一项旧游标都不能用。"""
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _encode_overview_cursor(offset: int, key: str) -> str:
    raw = json.dumps({"o": offset, "k": key}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_overview_cursor(cursor: str | None, key: str) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise _bad_cursor() from None
    if not isinstance(data, dict) or data.get("k") != key:
        raise _bad_cursor()
    offset = data.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1_000_000:
        raise _bad_cursor()
    return offset


def _known(row: ExternalResource, kind: str, *, confirmed: bool) -> bool:
    """这个仓库的某类子资源能不能确定：仓库看不到或缺少该类权限时不能（不能当成 0）。

    同步时记下了缺少哪几类（denied_kinds）就只影响那几类；旧数据没有这个字段时三类都按未知处理。
    从未成功同步过（confirmed 为假）时，本地可能只有半轮同步写下的数据，也一律未知。
    """
    if not confirmed:
        return False
    meta = row.metadata_json
    access = meta.get("access") or "ok"
    if access == "ok":
        return True
    denied = meta.get("denied_kinds")
    return access == "missing_permission" and isinstance(denied, list) and kind not in denied


def _total(values: list[int | None]) -> int | None:
    """任一仓库不能确定，汇总就不能确定：不拿已知子集冒充全量。"""
    return None if any(value is None for value in values) else sum(v or 0 for v in values)


def recent_update(row: ExternalResource) -> GitHubRecentUpdate:
    meta = row.metadata_json
    if row.resource_type == repo.REPO:
        repo_id: uuid.UUID | None = row.id
        repo_name: str | None = row.title
    else:
        raw = meta.get("repo_resource_id")
        repo_id = uuid.UUID(str(raw)) if raw else None
        repo_name = str(meta.get("repo_full_name") or "") or None
    return GitHubRecentUpdate(
        id=row.id,
        kind=row.resource_type,  # 只查 repo / issue / pull / run，pydantic 会校验
        repo_id=repo_id,
        repo_full_name=repo_name,
        title=row.title,
        source_url=row.source_url,
        updated_at=row.source_updated_at or row.synced_at,
        time_basis="source_updated_at" if row.source_updated_at else "synced_at",
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

    # ------------------------------------------------------------ 总览与权限快照（T-0175）

    async def _scoped_repo(
        self, integration: Integration, repo_id: uuid.UUID
    ) -> ExternalResource | None:
        """当前仓库清单里、未写 tombstone 的仓库；不在范围内的一律当作不存在。"""
        stmt = repo.overview_repo_query(
            integration.id, github_sync.repositories(integration), repo_id=repo_id
        )
        return await self.db.scalar(stmt.limit(1))

    async def overview(
        self,
        access: WorkspaceAccess,
        *,
        repo_id: uuid.UUID | None,
        q: str | None,
        window: OverviewRange,
        cursor: str | None,
        limit: int,
    ) -> DeveloperOverviewOut:
        """开发者总览：只读本地投影的批量聚合，不访问 GitHub、不触发同步或测试连接。"""
        window_end = datetime.now(UTC)
        window_start = window_end - _WINDOWS[window]
        integration, state = await self._integration(access)
        key = _scope_key(
            access.workspace.id, integration.id if integration else None, repo_id, q, window
        )
        offset = _decode_overview_cursor(cursor, key)
        if repo_id is not None and (
            integration is None or await self._scoped_repo(integration, repo_id) is None
        ):
            raise not_found()
        if integration is None or state.status in _WITHHELD:
            return self._withheld(state, window_start, window_end)

        configured = github_sync.repositories(integration)
        rows = list(
            await self.db.scalars(
                repo.overview_repo_query(integration.id, configured, repo_id=repo_id, q=q)
            )
        )
        ids = [row.id for row in rows]
        counts = await repo.overview_counts(
            self.db, integration.id, ids, window_start=window_start, window_end=window_end
        )
        confirmed = state.as_of is not None
        zero = {"issues": 0, "pulls": 0, "failed": 0, "untimed": 0}
        figures: dict[uuid.UUID, tuple[int | None, int | None, int | None]] = {}
        for row in rows:
            count = counts.get(str(row.id), zero)
            runs_known = _known(row, repo.RUN, confirmed=confirmed) and count["untimed"] == 0
            figures[row.id] = (
                count["issues"] if _known(row, repo.ISSUE, confirmed=confirmed) else None,
                count["pulls"] if _known(row, repo.PULL, confirmed=confirmed) else None,
                count["failed"] if runs_known else None,  # 缺时间的失败运行不猜归属
            )
        summary = DeveloperOverviewSummary(
            repo_count=len(rows) if confirmed else None,
            open_issue_count=_total([f[0] for f in figures.values()]) if confirmed else None,
            open_pull_count=_total([f[1] for f in figures.values()]) if confirmed else None,
            failed_run_count=_total([f[2] for f in figures.values()]) if confirmed else None,
            window_start=window_start,
            window_end=window_end,
        )
        # 已配置但本地还没有投影的仓库（从未同步到或刚加入清单）：范围不完整
        synced = {row.title.lower() for row in rows}
        needle = q.lower() if q else ""
        unsynced = repo_id is None and any(
            name.lower() not in synced and needle in name.lower() for name in configured
        )

        page = rows[offset : offset + limit]
        latest = await repo.latest_runs(self.db, integration.id, [row.id for row in page])
        items: list[RepoOverviewRow] = []
        for row in page:
            issues, pulls, failed = figures[row.id]
            run = latest.get(str(row.id))
            items.append(
                RepoOverviewRow(
                    repo=repo_out(row),
                    open_issue_count=issues,
                    open_pull_count=pulls,
                    failed_run_count=failed,
                    latest_run=run_out(run)
                    if run is not None and _known(row, repo.RUN, confirmed=confirmed)
                    else None,
                    partial=None in (issues, pulls, failed),
                )
            )
        pull_ids = [r.id for r in rows if _known(r, repo.PULL, confirmed=confirmed)]
        run_ids = [r.id for r in rows if _known(r, repo.RUN, confirmed=confirmed)]
        pulls_out = await repo.attention_pulls(
            self.db, integration.id, pull_ids, limit=ATTENTION_PULLS
        )
        failed_out = await repo.failed_runs(
            self.db,
            integration.id,
            run_ids,
            window_start=window_start,
            window_end=window_end,
            limit=FAILED_RUNS,
        )
        recent = await repo.recent_updates(self.db, integration.id, ids, limit=RECENT_UPDATES)
        partial = (
            unsynced
            or any(None in values for values in figures.values())
            or None
            in (
                summary.repo_count,
                summary.open_issue_count,
                summary.open_pull_count,
                summary.failed_run_count,
            )
        )
        return DeveloperOverviewOut(
            summary=summary,
            repos=RepoOverviewPage(
                items=items,
                next_cursor=_encode_overview_cursor(offset + limit, key)
                if len(rows) > offset + limit
                else None,
                total_estimate=len(rows),
            ),
            attention_pulls=[pull_out(row) for row in pulls_out],
            failed_runs=[run_out(row) for row in failed_out],
            recent_updates=[recent_update(row) for row in recent],
            sync=state,
            partial=partial,
        )

    @staticmethod
    def _withheld(
        state: SyncState, window_start: datetime, window_end: datetime
    ) -> DeveloperOverviewOut:
        """未配置 / 停用 / Token 被拒：不返回私有记录，统计未知。"""
        return DeveloperOverviewOut(
            summary=DeveloperOverviewSummary(
                repo_count=None,
                open_issue_count=None,
                open_pull_count=None,
                failed_run_count=None,
                window_start=window_start,
                window_end=window_end,
            ),
            repos=RepoOverviewPage(items=[], next_cursor=None, total_estimate=None),
            attention_pulls=[],
            failed_runs=[],
            recent_updates=[],
            sync=state,
            partial=True,
        )

    async def repository_permissions(
        self, access: WorkspaceAccess, repo_id: uuid.UUID
    ) -> RepositoryPermissionEvidenceOut:
        """一个仓库上次“测试连接”时实际得到的四项读权限结果；只读快照，不触发测试连接。"""
        integration, state = await self._integration(access)
        row = await self._scoped_repo(integration, repo_id) if integration else None
        if integration is None or row is None:
            raise not_found()
        if state.status in _WITHHELD:
            return RepositoryPermissionEvidenceOut(
                repo_id=row.id,
                full_name=None,
                checked_at=None,
                checks=[],
                complete=False,
                sync=state,
            )
        checked_at, checks = evidence.read(integration, row.title)
        return RepositoryPermissionEvidenceOut(
            repo_id=row.id,
            full_name=row.title,
            checked_at=checked_at,
            checks=checks,
            complete=evidence.complete(checks),
            sync=state,
        )
