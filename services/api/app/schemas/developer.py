"""开发者页：GitHub 仓库、Issue、PR 与 Actions 运行的只读投影（规范第 7.3 节）。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 7 节；字段与允许值来自
docs/decisions/2026-10-30-github-rest-and-affine-verification.md 第 4 节（REST API 2026-03-10）。
列表读本地投影并带 sync 新鲜度：被 GitHub 限流时 sync 带 retry_at，绝不把空列表当正常（A17）。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Page
from app.schemas.integrations import RepositoryPermissionCheck, SyncState

GitHubState = Literal["open", "closed"]
GitHubStateFilter = Literal["open", "closed", "all"]
# Actions 运行的 status 查询参数允许的值（核验 ADR 第 4 节，来自官方 OpenAPI）
RunStatusFilter = Literal[
    "completed",
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "stale",
    "success",
    "timed_out",
    "in_progress",
    "queued",
    "requested",
    "waiting",
    "pending",
]
RunConclusionFilter = Literal[
    "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"
]


class _Projected(BaseModel):
    id: UUID = Field(description="投影 UUID")
    integration_id: UUID
    external_id: str = Field(description="github:<类型>:<GitHub 的数字 id>；只用于显示与排错")
    source_url: str = Field(description="GitHub 上的地址（html_url）")
    source_updated_at: datetime | None = Field(description="GitHub 上最后修改的时间")
    synced_at: datetime


class _InRepo(_Projected):
    repo_id: UUID = Field(description="所属仓库的投影 UUID")
    repo_full_name: str


class RepoOut(_Projected):
    full_name: str
    description: str | None
    private: bool
    archived: bool
    default_branch: str
    pushed_at: datetime | None
    access: Literal["ok", "missing_permission", "not_found"] = Field(
        description="最近一次同步时 Token 对该仓库的访问情况"
    )


class IssueOut(_InRepo):
    number: int
    title: str
    state: GitHubState
    state_reason: str | None = Field(description="completed / reopened / not_planned / duplicate")
    author: str | None = Field(description="创建者的登录名")
    labels: list[str]
    assignees: list[str] = Field(description="登录名")
    comments: int
    created_at: datetime
    closed_at: datetime | None


class PullOut(_InRepo):
    number: int
    title: str
    state: GitHubState
    draft: bool
    merged: bool
    merged_at: datetime | None
    author: str | None
    labels: list[str]
    requested_reviewers: list[str] = Field(description="登录名")
    head_ref: str
    base_ref: str
    created_at: datetime
    closed_at: datetime | None


class FailedJob(BaseModel):
    name: str
    failed_steps: list[str] = Field(description="失败的 step 名称；不含日志")


class FailureSummary(BaseModel):
    """只来自 jobs 接口的名称与结论，不读 Actions 日志（日志里可能有密钥）。"""

    failed_jobs: list[FailedJob]


class RunOut(_InRepo):
    name: str | None
    display_title: str
    status: str | None = Field(description="queued / in_progress / completed 等")
    conclusion: str | None = Field(description="success / failure / cancelled 等；未结束时为 null")
    event: str
    head_branch: str | None
    head_sha: str
    run_number: int
    run_attempt: int
    actor: str | None = Field(description="触发者的登录名")
    run_started_at: datetime | None
    failure_summary: FailureSummary | None = Field(description="失败的运行才有")


class RepoPage(Page[RepoOut]):
    sync: SyncState


class IssuePage(Page[IssueOut]):
    sync: SyncState


class PullPage(Page[PullOut]):
    sync: SyncState


class RunPage(Page[RunOut]):
    sync: SyncState


# ---------------------------------------------------------------- 开发者总览（T-0175）

OverviewRange = Literal["24h", "7d"]


class RepoOverviewRow(BaseModel):
    """仓库概览的一行。

    子数字为 null 表示不能确定（缺少权限 / 仓库不可见 / 失败运行缺时间），不是 0。
    """

    repo: RepoOut
    open_issue_count: int | None = Field(ge=0, description="当前开放的 Issue（不含 PR）")
    open_pull_count: int | None = Field(ge=0, description="当前开放的 PR（不含已合并 / 已关闭）")
    failed_run_count: int | None = Field(
        ge=0,
        description="时间窗内更新的失败运行（completed 且 conclusion 为 failure / timed_out，"
        "按 source_updated_at 归窗）",
    )
    latest_run: RunOut | None = Field(
        description="这个仓库最近一次运行（按开始时间）；不与任何 PR 关联"
    )
    partial: bool = Field(description="这一行有不能确定的数字")


class RepoOverviewPage(Page[RepoOverviewRow]):
    """仓库按名称排序；next_cursor 只能在同一工作空间、集成与筛选条件下使用。"""


class DeveloperOverviewSummary(BaseModel):
    """所选仓库范围（全部页）的统计，只算已同步到本地的投影；不能确定时为 null。"""

    repo_count: int | None = Field(ge=0)
    open_issue_count: int | None = Field(ge=0, description="当前开放的 Issue（不含 PR）")
    open_pull_count: int | None = Field(ge=0, description="当前开放的 PR（不含已合并 / 已关闭）")
    failed_run_count: int | None = Field(
        ge=0, description="[window_start, window_end) 内更新的失败运行；同一运行重试只算一次"
    )
    window_start: datetime = Field(description="失败运行时间窗的开始（含）")
    window_end: datetime = Field(description="失败运行时间窗的结束（不含），即服务端当前时间")


class GitHubRecentUpdate(BaseModel):
    """投影的最近更新：只说明“更新于 / 同步于”，不是 GitHub 上的操作事件。"""

    id: UUID
    kind: Literal["repo", "issue", "pull", "run"]
    repo_id: UUID | None = Field(description="所属仓库的投影 UUID；仓库本身时为自己的 UUID")
    repo_full_name: str | None
    title: str
    source_url: str
    updated_at: datetime
    time_basis: Literal["source_updated_at", "synced_at"] = Field(
        description="source_updated_at：GitHub 上的修改时间；synced_at：没有修改时间，这是同步时间"
    )


class DeveloperOverviewOut(BaseModel):
    summary: DeveloperOverviewSummary
    repos: RepoOverviewPage
    attention_pulls: list[PullOut] = Field(
        description="最多 5 个开放 PR：已请求审查的在前，再按 GitHub 更新时间倒序；不推断审查结果"
    )
    failed_runs: list[RunOut] = Field(description="时间窗内更新的失败运行，最多 3 个")
    recent_updates: list[GitHubRecentUpdate] = Field(description="最多 5 条")
    sync: SyncState
    partial: bool = Field(
        description="有不能确定的统计、缺少权限的仓库、尚未同步的已配置仓库或缺时间的失败运行"
    )


class RepositoryPermissionEvidenceOut(BaseModel):
    """管理员上次“测试连接”时这个仓库四项读权限的实际结果；没有执行的项不出现在 checks 里。"""

    repo_id: UUID
    full_name: str | None = Field(description="Token 被拒或未配置时为 null")
    checked_at: datetime | None = Field(description="上次核验时间；从未核验时为 null")
    checks: list[RepositoryPermissionCheck]
    complete: bool = Field(description="metadata / issues / pull_requests / actions 四项都有结果")
    sync: SyncState
