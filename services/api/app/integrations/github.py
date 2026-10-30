"""GitHub REST API 只读客户端。

依据核验 ADR：docs/decisions/2026-10-30-github-rest-and-affine-verification.md。

- 固定 `X-GitHub-Api-Version: 2026-03-10` 与 `Accept: application/vnd.github+json`（第 2 节）。
- 只发 GET；分页用响应头 `Link` 里的 URL 原样翻页，不自己拼（第 4 节）。
- 条件请求：带上次的 `ETag`，返回 304 时不计额度（第 5 节，实测）。
- 重定向与分页链接只跟随到同一个 API 地址（仓库改名、转移），其余一律拒绝。
  API 地址默认 https://api.github.com，只有测试与验收环境会改成模拟服务（GITHUB_API_URL）。
- 错误分类（第 5 节）：401 → AUTH_FAILED；403 / 429 且是限流 → RATE_LIMITED（带 retry_after 秒数）；
  其余 403 → 资源级 AUTH_FAILED；404 → NOT_FOUND；400 / 410 版本不受支持 → UNAVAILABLE。
- 记录最近一次的 `x-ratelimit-remaining` / `x-ratelimit-reset`，同步时额度接近用完就提前停下。
"""

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp
from app.schemas.integrations import GITHUB_API_URL, GITHUB_API_VERSION

PROVIDER = "github"
PER_PAGE = 100
MAX_PAGES = 10  # 每个列表最多 1000 条；超过时视为不完整，不据此写 tombstone
MAX_REDIRECTS = 3
LOW_BUDGET = 50  # 剩余额度低于它就提前停下，等到重置时间
SECONDARY_WAIT_SECONDS = 60  # 文档：没有 retry-after 也没有 reset 时至少等 1 分钟
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
_LINK = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')


@dataclass(frozen=True)
class GitHubResponse:
    status: int
    data: Any
    etag: str | None
    next_url: str | None
    oauth_scopes: str | None


@dataclass(frozen=True)
class Listing:
    items: list[dict[str, Any]]
    complete: bool  # 翻到了最后一页（没有被 MAX_PAGES 或提前停止截断）
    etag: str | None
    not_modified: bool = False


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("message", "")).lower() if isinstance(body, dict) else ""


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def classify(response: httpx.Response, *, now: float | None = None) -> ConnectorError:
    """把非 2xx / 304 响应归类（不保留响应原文）。"""
    status = response.status_code
    headers = response.headers
    message = _message(response)
    if status in (403, 429):
        wait = _int(headers.get("retry-after"))
        reset = _int(headers.get("x-ratelimit-reset"))
        if wait is None and headers.get("x-ratelimit-remaining") == "0" and reset is not None:
            wait = max(0, reset - int(now if now is not None else time.time())) + 1
        if wait is None and (status == 429 or "rate limit" in message):
            wait = SECONDARY_WAIT_SECONDS
        if wait is not None:
            return ConnectorError(
                "RATE_LIMITED",
                "GitHub 限流",
                status=status,
                retry_after=wait,
                reason="rate_limited",
            )
        return ConnectorError(
            "AUTH_FAILED",
            "Token 缺少访问该资源的权限",
            status=status,
            resource_level=True,
            reason="missing_permission",
        )
    if status == 401:
        return ConnectorError(
            "AUTH_FAILED", "GitHub 拒绝了 Token", status=status, reason="token_rejected"
        )
    if status == 404:
        return ConnectorError("NOT_FOUND", "资源不存在或不可见", status=status, reason="not_found")
    if status == 410 or (status == 400 and "version" in message):
        return ConnectorError(
            "UNAVAILABLE",
            "GitHub 不再支持该 API 版本",
            status=status,
            reason="api_version_unsupported",
        )
    if status == 422:
        return ConnectorError("VALIDATION_FAILED", "GitHub 拒绝了请求参数", status=status)
    return ConnectorError(
        "UNAVAILABLE", "GitHub 暂时不可用", status=status, reason="upstream_error"
    )


class GitHubClient:
    def __init__(self, http: ConnectorHttp, token: str, base_url: str = GITHUB_API_URL) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._origin = _origin(self._base)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "my-workspace",
        }
        self.remaining: int | None = None
        self.reset_at: int | None = None

    # ---------------------------------------------------------------- 基础

    def budget_low(self) -> bool:
        return self.remaining is not None and self.remaining < LOW_BUDGET

    def low_budget_error(self) -> ConnectorError:
        wait = max(0, (self.reset_at or 0) - int(time.time())) + 1 if self.reset_at else 60
        return ConnectorError(
            "RATE_LIMITED", "GitHub 额度即将用完", retry_after=wait, reason="rate_limited"
        )

    async def get(
        self,
        path_or_url: str,
        params: Mapping[str, Any] | None = None,
        *,
        etag: str | None = None,
    ) -> GitHubResponse:
        url = path_or_url if "://" in path_or_url else self._base + path_or_url
        if _origin(url) != self._origin:
            raise ConnectorError(
                "UNAVAILABLE", "不允许访问该地址", reason="destination_not_allowed"
            )
        headers = dict(self._headers)
        if etag:
            headers["If-None-Match"] = etag
        for _ in range(MAX_REDIRECTS + 1):
            response = await self._http.request("GET", url, headers=headers, params=params)
            self._remember_budget(response)
            if response.status_code in (301, 302, 307, 308):
                location = response.headers.get("location", "")
                if _origin(location) != self._origin:
                    raise ConnectorError(
                        "UNAVAILABLE",
                        "GitHub 重定向到了不允许的地址",
                        reason="redirect_not_allowed",
                    )
                url, params = location, None  # Location 已带上查询参数
                continue
            if response.status_code == 304:
                return GitHubResponse(304, None, etag, None, None)
            if response.status_code >= 400:
                raise classify(response)
            try:
                data = response.json()
            except ValueError as error:
                raise ConnectorError(
                    "UNAVAILABLE", "GitHub 返回了无法解析的内容", reason="invalid_response"
                ) from error
            return GitHubResponse(
                response.status_code,
                data,
                response.headers.get("etag"),
                self._next_link(response.headers.get("link")),
                response.headers.get("x-oauth-scopes"),
            )
        raise ConnectorError("UNAVAILABLE", "GitHub 重定向次数过多", reason="too_many_redirects")

    def _next_link(self, header: str | None) -> str | None:
        for url, rel in _LINK.findall(header or ""):
            if rel == "next" and _origin(url) == self._origin:
                return str(url)
        return None

    def _remember_budget(self, response: httpx.Response) -> None:
        remaining = _int(response.headers.get("x-ratelimit-remaining"))
        if remaining is not None:
            self.remaining = remaining
            self.reset_at = _int(response.headers.get("x-ratelimit-reset"))

    async def _list(
        self,
        path: str,
        params: Mapping[str, Any],
        *,
        etag: str | None = None,
        key: str | None = None,
        stop_before: datetime | None = None,
    ) -> Listing:
        """按 Link 翻页。stop_before：列表按 updated 倒序时，遇到更早的条目就停（增量同步）。"""
        first = await self.get(path, params, etag=etag)
        if first.status == 304:
            return Listing([], complete=True, etag=etag, not_modified=True)
        items: list[dict[str, Any]] = []
        page: GitHubResponse | None = first
        pages = 0
        while page is not None:
            batch = page.data.get(key, []) if key else page.data
            if not isinstance(batch, list):
                raise ConnectorError(
                    "UNAVAILABLE", "GitHub 返回了意外的内容", reason="invalid_response"
                )
            for item in batch:
                if stop_before and _updated(item) and _updated(item) < stop_before:  # type: ignore[operator]
                    return Listing(items, complete=True, etag=first.etag)
                items.append(item)
            pages += 1
            if page.next_url is None:
                return Listing(items, complete=True, etag=first.etag)
            if pages >= MAX_PAGES or self.budget_low():
                return Listing(items, complete=False, etag=first.etag)
            page = await self.get(page.next_url)
        return Listing(items, complete=True, etag=first.etag)

    # ---------------------------------------------------------------- 资源

    async def get_repo(self, full_name: str) -> GitHubResponse:
        return await self.get(f"/repos/{full_name}")

    async def list_issues(
        self, full_name: str, *, etag: str | None = None, since: datetime | None = None
    ) -> Listing:
        """Issue 列表里带 pull_request 键的是 PR（核验 ADR 第 4 节），这里去掉。"""
        listing = await self._list(
            f"/repos/{full_name}/issues",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": PER_PAGE},
            etag=etag,
            stop_before=since,
        )
        issues = [item for item in listing.items if "pull_request" not in item]
        return Listing(issues, listing.complete, listing.etag, listing.not_modified)

    async def list_pulls(
        self, full_name: str, *, etag: str | None = None, since: datetime | None = None
    ) -> Listing:
        return await self._list(
            f"/repos/{full_name}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc", "per_page": PER_PAGE},
            etag=etag,
            stop_before=since,
        )

    async def list_runs(
        self, full_name: str, *, etag: str | None = None, limit: int = 30
    ) -> Listing:
        """只取最近的运行（第一页）。"""
        first = await self.get(f"/repos/{full_name}/actions/runs", {"per_page": limit}, etag=etag)
        if first.status == 304:
            return Listing([], complete=True, etag=etag, not_modified=True)
        runs = first.data.get("workflow_runs", []) if isinstance(first.data, dict) else []
        return Listing(list(runs), complete=True, etag=first.etag)

    async def failed_jobs(self, full_name: str, run_id: int) -> list[dict[str, Any]]:
        """失败运行的故障摘要：失败的 job 与 step 名称（不读日志）。"""
        response = await self.get(
            f"/repos/{full_name}/actions/runs/{run_id}/jobs",
            {"filter": "latest", "per_page": PER_PAGE},
        )
        jobs = response.data.get("jobs", []) if isinstance(response.data, dict) else []
        summary = []
        for job in jobs:
            if job.get("conclusion") not in FAILED_CONCLUSIONS:
                continue
            steps = [
                str(step.get("name"))
                for step in job.get("steps") or []
                if step.get("conclusion") in FAILED_CONCLUSIONS
            ]
            summary.append({"name": str(job.get("name") or ""), "failed_steps": steps})
        return summary

    async def probe(self, path: str) -> GitHubResponse:
        """测试连接用：读一条记录。"""
        return await self.get(path, {"per_page": 1})


def _updated(item: Mapping[str, Any]) -> datetime | None:
    value = item.get("updated_at")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
