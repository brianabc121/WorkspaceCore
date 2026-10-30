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
from app.schemas.integrations import SyncState

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
