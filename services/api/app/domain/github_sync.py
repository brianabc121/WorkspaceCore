"""GitHub 只读同步：仓库、Issue、PR、Actions 最近运行写入 external_resources 投影。

依据核验 ADR（docs/decisions/2026-10-30-github-rest-and-affine-verification.md）第 4、5 节与
Sprint 4 契约第 7 节：

- 只同步集成配置里列出的仓库；被移出配置的仓库及其子资源写 tombstone。
- 增量：Issue / PR 列表按 updated 倒序翻页，遇到游标之前的条目就停；第一页带 ETag，
  304 表示没有变化（不计额度）。全量：完整翻页，列表完整时为外部已经没有的写 tombstone。
- Actions 只保留每个仓库最近 RUNS_PER_REPO 次运行；失败的运行读一次 jobs 生成故障摘要（不读日志）。
- 某一类资源 403 / 404（缺少权限）只影响该类，记在仓库的 access 上；Token 失效、限流、
  服务不可用则整轮停下并抛出，由调用方更新集成状态。限流时记下 rate_limited_until（A17）。
- 游标与投影在同一个事务里提交；失败时游标不前移，只记错误分类。
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projection import FULL_SYNC
from app.integrations.base import ConnectorError
from app.integrations.github import FAILED_CONCLUSIONS, GitHubClient, Listing
from app.models import ExternalResource, Integration
from app.repositories import github as repo
from app.repositories import integrations as integration_repo
from app.repositories import projection as projection_repo

logger = logging.getLogger("app.sync")

CURSOR = "github"
RUNS_PER_REPO = 30
SUMMARIES_PER_SYNC = 5  # 每轮最多为几个失败运行读取 jobs，控制额度
OVERLAP = timedelta(minutes=2)


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(user: Any) -> str | None:
    return str(user["login"]) if isinstance(user, dict) and user.get("login") else None


def _logins(users: Any) -> list[str]:
    return [login for u in users or [] if (login := _login(u))]


def _labels(labels: Any) -> list[str]:
    return [
        str(label["name"])
        for label in labels or []
        if isinstance(label, dict) and label.get("name")
    ]


def repositories(integration: Integration) -> list[str]:
    value = integration.settings_json.get("repositories")
    return [str(v) for v in value] if isinstance(value, list) else []


# ---------------------------------------------------------------- 写入投影


async def _save(
    db: AsyncSession,
    integration: Integration,
    resource_type: str,
    item: dict[str, Any],
    *,
    title: str,
    metadata: dict[str, Any],
) -> ExternalResource:
    return await projection_repo.upsert(
        db,
        workspace_id=integration.workspace_id,
        integration_id=integration.id,
        resource_type=resource_type,
        external_id=str(item["id"]),
        title=title,
        source_url=str(item.get("html_url") or ""),
        metadata=metadata,
        source_updated_at=_dt(item.get("updated_at")),
    )


async def save_repo(
    db: AsyncSession, integration: Integration, item: dict[str, Any], access: str
) -> ExternalResource:
    return await _save(
        db,
        integration,
        repo.REPO,
        item,
        title=str(item.get("full_name") or ""),
        metadata={
            "full_name": item.get("full_name"),
            "description": item.get("description"),
            "private": bool(item.get("private")),
            "archived": bool(item.get("archived")),
            "default_branch": item.get("default_branch") or "",
            "pushed_at": item.get("pushed_at"),
            "access": access,
        },
    )


def _child_meta(repo_row: ExternalResource) -> dict[str, Any]:
    return {"repo_resource_id": str(repo_row.id), "repo_full_name": repo_row.title}


async def _save_issue(
    db: AsyncSession, integration: Integration, repo_row: ExternalResource, item: dict[str, Any]
) -> None:
    await _save(
        db,
        integration,
        repo.ISSUE,
        item,
        title=str(item.get("title") or ""),
        metadata={
            **_child_meta(repo_row),
            "number": item.get("number"),
            "state": item.get("state"),
            "state_reason": item.get("state_reason"),
            "author": _login(item.get("user")),
            "labels": _labels(item.get("labels")),
            "assignees": _logins(item.get("assignees")),
            "comments": int(item.get("comments") or 0),
            "created_at": item.get("created_at"),
            "closed_at": item.get("closed_at"),
        },
    )


async def _save_pull(
    db: AsyncSession, integration: Integration, repo_row: ExternalResource, item: dict[str, Any]
) -> None:
    head: dict[str, Any] = item["head"] if isinstance(item.get("head"), dict) else {}
    base: dict[str, Any] = item["base"] if isinstance(item.get("base"), dict) else {}
    await _save(
        db,
        integration,
        repo.PULL,
        item,
        title=str(item.get("title") or ""),
        metadata={
            **_child_meta(repo_row),
            "number": item.get("number"),
            "state": item.get("state"),
            "draft": bool(item.get("draft")),
            "merged": item.get("merged_at") is not None,
            "merged_at": item.get("merged_at"),
            "author": _login(item.get("user")),
            "labels": _labels(item.get("labels")),
            "requested_reviewers": _logins(item.get("requested_reviewers")),
            "head_ref": str(head.get("ref") or ""),
            "base_ref": str(base.get("ref") or ""),
            "created_at": item.get("created_at"),
            "closed_at": item.get("closed_at"),
        },
    )


async def _save_run(
    db: AsyncSession,
    integration: Integration,
    repo_row: ExternalResource,
    item: dict[str, Any],
    failure_summary: list[dict[str, Any]] | None,
) -> None:
    await _save(
        db,
        integration,
        repo.RUN,
        item,
        title=str(item.get("display_title") or item.get("name") or ""),
        metadata={
            **_child_meta(repo_row),
            "name": item.get("name"),
            "status": item.get("status"),
            "conclusion": item.get("conclusion"),
            "event": item.get("event") or "",
            "head_branch": item.get("head_branch"),
            "head_sha": item.get("head_sha") or "",
            "run_number": int(item.get("run_number") or 0),
            "run_attempt": int(item.get("run_attempt") or 1),
            "actor": _login(item.get("actor")),
            "run_started_at": item.get("run_started_at"),
            "failure_summary": {"failed_jobs": failure_summary} if failure_summary else None,
        },
    )


# ---------------------------------------------------------------- 同步


class _Run:
    """一轮同步的状态：ETag、各仓库的访问情况、剩余的故障摘要配额。"""

    def __init__(self, integration: Integration, *, full: bool) -> None:
        settings = integration.settings_json
        self.full = full
        self.etags: dict[str, str] = dict(settings.get("etags") or {}) if not full else {}
        self.access: dict[str, str] = {}
        self.summaries_left = SUMMARIES_PER_SYNC


async def sync(
    db: AsyncSession, integration: Integration, client: GitHubClient, *, full: bool
) -> None:
    """同步全部配置的仓库；失败时抛出 ConnectorError（限流时已记下恢复时间）。"""
    started = datetime.now(UTC)
    cursor = await integration_repo.get_cursor(db, integration.id, CURSOR)
    since = _dt(cursor.cursor_value) if cursor and not full else None
    state = _Run(integration, full=full or since is None)
    try:
        for full_name in repositories(integration):
            if client.budget_low():
                raise client.low_budget_error()
            await _sync_repo(db, integration, client, full_name, state, since)
    except ConnectorError as error:
        _remember_failure(integration, error, state)
        await integration_repo.upsert_cursor(
            db, integration.id, CURSOR, success_at=None, error=error.reason or error.kind
        )
        raise
    for repo_id in await repo.tombstone_unconfigured(db, integration.id, repositories(integration)):
        await repo.tombstone_children(db, integration.id, repo_id)
    settings = dict(integration.settings_json)
    settings.pop("rate_limited_until", None)
    integration.settings_json = {**settings, "etags": state.etags, "repo_access": state.access}
    await integration_repo.upsert_cursor(
        db, integration.id, CURSOR, success_at=started, cursor_value=(started - OVERLAP).isoformat()
    )
    if state.full:
        # 周期对账据此决定下一次全量的时间（与 Vikunja 共用 full_sync 游标）
        await integration_repo.upsert_cursor(db, integration.id, FULL_SYNC, success_at=started)
    logger.info(
        "github sync finished",
        extra={"integration_id": str(integration.id), "full": state.full},
    )


def _remember_failure(integration: Integration, error: ConnectorError, state: _Run) -> None:
    settings = dict(integration.settings_json)
    if error.kind == "RATE_LIMITED":
        wait = timedelta(seconds=error.retry_after or 60)
        settings["rate_limited_until"] = (datetime.now(UTC) + wait).isoformat()
    if state.access:
        settings["repo_access"] = {**(settings.get("repo_access") or {}), **state.access}
    integration.settings_json = settings


def _denied(error: ConnectorError) -> str | None:
    """资源级的拒绝：只影响这一类资源，不停止整轮同步。"""
    if error.kind == "NOT_FOUND":
        return "not_found"
    if error.kind == "AUTH_FAILED" and error.resource_level:
        return "missing_permission"
    return None


async def _sync_repo(
    db: AsyncSession,
    integration: Integration,
    client: GitHubClient,
    full_name: str,
    state: _Run,
    since: datetime | None,
) -> None:
    try:
        response = await client.get_repo(full_name)
    except ConnectorError as error:
        if (denied := _denied(error)) is None:
            raise
        state.access[full_name] = denied
        existing = await repo.repo_by_name(db, integration.id, full_name)
        if existing is not None:
            existing.metadata_json = {**existing.metadata_json, "access": denied}
            await repo.tombstone_children(db, integration.id, existing.id)
        return
    access = "ok"
    repo_row = await save_repo(db, integration, response.data, access)
    for kind in (repo.ISSUE, repo.PULL, repo.RUN):
        try:
            await _sync_kind(db, integration, client, repo_row, kind, state, since)
        except ConnectorError as error:
            if (denied := _denied(error)) is None:
                raise
            access = "missing_permission"
            await repo.tombstone_children(db, integration.id, repo_row.id, [kind])
    state.access[full_name] = access
    if access != "ok":
        repo_row.metadata_json = {**repo_row.metadata_json, "access": access}


async def _sync_kind(
    db: AsyncSession,
    integration: Integration,
    client: GitHubClient,
    repo_row: ExternalResource,
    kind: str,
    state: _Run,
    since: datetime | None,
) -> None:
    full_name = repo_row.title
    key = f"{kind}:{full_name.lower()}"
    etag = state.etags.get(key)
    listing: Listing
    if kind == repo.ISSUE:
        listing = await client.list_issues(
            full_name, etag=etag, since=None if state.full else since
        )
    elif kind == repo.PULL:
        listing = await client.list_pulls(full_name, etag=etag, since=None if state.full else since)
    else:
        listing = await client.list_runs(full_name, etag=etag, limit=RUNS_PER_REPO)
    if listing.not_modified:
        return
    if listing.etag:
        state.etags[key] = listing.etag
    for item in listing.items:
        if kind == repo.ISSUE:
            await _save_issue(db, integration, repo_row, item)
        elif kind == repo.PULL:
            await _save_pull(db, integration, repo_row, item)
        else:
            summary = await _summary(db, integration, client, repo_row, item, state)
            await _save_run(db, integration, repo_row, item, summary)
    if kind == repo.RUN or (state.full and listing.complete):
        # 运行只保留最近的一页；Issue / PR 只在完整的全量列表之后写 tombstone
        await repo.tombstone_missing_in_repo(
            db,
            integration_id=integration.id,
            resource_type=kind,
            repo_resource_id=repo_row.id,
            seen=[str(item["id"]) for item in listing.items],
        )


async def _summary(
    db: AsyncSession,
    integration: Integration,
    client: GitHubClient,
    repo_row: ExternalResource,
    run: dict[str, Any],
    state: _Run,
) -> list[dict[str, Any]] | None:
    """失败的运行读一次 jobs；同一次尝试已经有摘要的沿用，不重复读。"""
    if run.get("conclusion") not in FAILED_CONCLUSIONS:
        return None
    existing = await projection_repo.get_by_external(
        db, integration_id=integration.id, resource_type=repo.RUN, external_id=str(run["id"])
    )
    if existing is not None:
        meta = existing.metadata_json
        summary = meta.get("failure_summary")
        if isinstance(summary, dict) and meta.get("run_attempt") == run.get("run_attempt"):
            jobs = summary.get("failed_jobs")
            return list(jobs) if isinstance(jobs, list) else None
    if state.summaries_left <= 0:
        return None
    state.summaries_left -= 1
    return await client.failed_jobs(repo_row.title, int(run["id"]))
