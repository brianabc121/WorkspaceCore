"""内存版 GitHub REST API（只读），用 httpx.MockTransport 接到 Connector 上。

响应形状、请求头、分页、ETag 与限流行为按核验 ADR
（docs/decisions/2026-10-30-github-rest-and-affine-verification.md 第 2 ~ 5 节）的实测与官方文档：
版本头不对 400；Token 不对 401 `Bad credentials`；主限流 403 + x-ratelimit-remaining: 0；
次级限流 429 + retry-after；Issue 列表里带 pull_request 键的是 PR；Link 头 rel="next"；
If-None-Match 命中返回 304 且不计额度。真实 GitHub 的其他差异见 T-0032 的验收报告。
"""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

BASE = "https://api.github.com"
TOKEN = "github_pat_11FAKE00000_" + "t" * 40
VERSION = "2026-03-10"


def _ts(offset_minutes: int = 0) -> str:
    moment = datetime(2026, 10, 30, 12, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes)
    return moment.isoformat().replace("+00:00", "Z")


class FakeGitHub:
    def __init__(self) -> None:
        self.repos: dict[str, dict[str, Any]] = {}
        self.issues: dict[str, list[dict[str, Any]]] = {}
        self.pulls: dict[str, list[dict[str, Any]]] = {}
        self.runs: dict[str, list[dict[str, Any]]] = {}
        self.jobs: dict[int, list[dict[str, Any]]] = {}
        self.forbidden: set[tuple[str, str]] = set()  # (仓库, issues / pulls / actions)
        self.renamed: dict[str, str] = {}
        self.token = TOKEN
        self.rate_limit: str | None = None  # primary / secondary
        self.classic_scopes: str | None = None
        self.remaining = 5000
        self.reset = int(time.time()) + 3600
        self.requests: list[httpx.Request] = []
        self._next_id = 9000
        self._clock = 0

    # ---------------------------------------------------------------- 数据

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def tick(self) -> str:
        self._clock += 1
        return _ts(self._clock)

    def add_repo(self, full_name: str, **fields: Any) -> dict[str, Any]:
        repo = {
            "id": self._id(),
            "full_name": full_name,
            "name": full_name.split("/")[1],
            "description": fields.pop("description", None),
            "private": fields.pop("private", False),
            "archived": False,
            "default_branch": "main",
            "pushed_at": self.tick(),
            "updated_at": self.tick(),
            "html_url": f"https://github.com/{full_name}",
            **fields,
        }
        key = full_name.lower()
        self.repos[key] = repo
        for store in (self.issues, self.pulls, self.runs):
            store.setdefault(key, [])
        return repo

    def add_issue(self, full_name: str, title: str, **fields: Any) -> dict[str, Any]:
        number = len(self.issues[full_name.lower()]) + 1
        issue = {
            "id": self._id(),
            "number": number,
            "title": title,
            "state": "open",
            "state_reason": None,
            "user": {"login": "octocat"},
            "labels": [{"name": "bug"}],
            "assignees": [{"login": "hubot"}],
            "comments": 2,
            "created_at": self.tick(),
            "updated_at": self.tick(),
            "closed_at": None,
            "html_url": f"https://github.com/{full_name}/issues/{number}",
            **fields,
        }
        self.issues[full_name.lower()].append(issue)
        return issue

    def add_pull(self, full_name: str, title: str, **fields: Any) -> dict[str, Any]:
        number = 100 + len(self.pulls[full_name.lower()])
        pull = {
            "id": self._id(),
            "number": number,
            "title": title,
            "state": "open",
            "draft": False,
            "merged_at": None,
            "user": {"login": "octocat"},
            "labels": [],
            "requested_reviewers": [{"login": "reviewer"}],
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "created_at": self.tick(),
            "updated_at": self.tick(),
            "closed_at": None,
            "html_url": f"https://github.com/{full_name}/pull/{number}",
            **fields,
        }
        self.pulls[full_name.lower()].append(pull)
        # Issue 列表里同样出现这个 PR（带 pull_request 键，id 与 PR 的不同）
        self.issues[full_name.lower()].append(
            {
                **pull,
                "id": self._id(),
                "pull_request": {"url": "x"},
                "comments": 0,
                "assignees": [],
            }
        )
        return pull

    def add_run(
        self, full_name: str, *, conclusion: str | None = "success", failed: bool = False
    ) -> dict[str, Any]:
        run_id = self._id()
        run = {
            "id": run_id,
            "name": "CI",
            "display_title": f"运行 {run_id}",
            "status": "completed" if conclusion else "in_progress",
            "conclusion": "failure" if failed else conclusion,
            "event": "push",
            "head_branch": "main",
            "head_sha": "abc123",
            "run_number": len(self.runs[full_name.lower()]) + 1,
            "run_attempt": 1,
            "actor": {"login": "octocat"},
            "run_started_at": self.tick(),
            "updated_at": self.tick(),
            "html_url": f"https://github.com/{full_name}/actions/runs/{run_id}",
        }
        self.runs[full_name.lower()].insert(0, run)
        if failed:
            self.jobs[run_id] = [
                {
                    "name": "test",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Checkout", "conclusion": "success"},
                        {"name": "Run pytest", "conclusion": "failure"},
                    ],
                },
                {"name": "lint", "conclusion": "success", "steps": []},
            ]
        return run

    # ---------------------------------------------------------------- HTTP

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, path_suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(path_suffix)]

    def _limit_headers(self) -> dict[str, str]:
        return {
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": str(self.remaining),
            "x-ratelimit-used": str(5000 - self.remaining),
            "x-ratelimit-reset": str(self.reset),
            "x-ratelimit-resource": "core",
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET", "GitHub 只读：不应发出写请求"
        if request.headers.get("x-github-api-version") != VERSION:
            return httpx.Response(400, json={"message": "Bad Request", "errors": "version"})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if self.rate_limit == "primary":
            headers = {**self._limit_headers(), "x-ratelimit-remaining": "0"}
            return httpx.Response(403, json={"message": "API rate limit exceeded"}, headers=headers)
        if self.rate_limit == "secondary":
            return httpx.Response(
                429,
                json={"message": "You have exceeded a secondary rate limit"},
                headers={"retry-after": "30"},
            )
        status, body = self._route(request)
        if status in (301, 404, 403):
            return httpx.Response(status, **body)
        text = json.dumps(body, sort_keys=True)
        etag = '"' + hashlib.sha256(text.encode()).hexdigest()[:32] + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={**self._limit_headers(), "etag": etag})
        self.remaining -= 1
        headers = {**self._limit_headers(), "etag": etag}
        if self.classic_scopes is not None:
            headers["x-oauth-scopes"] = self.classic_scopes
        if isinstance(body, dict) and "_link" in body:
            headers["link"] = body.pop("_link")
            text = json.dumps(body.get("items", body))
        return httpx.Response(200, content=text.encode(), headers=headers)

    def _route(self, request: httpx.Request) -> tuple[int, Any]:
        parts = request.url.path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "repos":
            return 404, {"json": {"message": "Not Found"}}
        full_name = f"{parts[1]}/{parts[2]}".lower()
        if full_name in self.renamed:
            target = self.renamed[full_name]
            tail = "/".join(parts[3:])
            location = f"{BASE}/repos/{target}" + (f"/{tail}" if tail else "")
            if request.url.query:
                location += "?" + request.url.query.decode()
            return 301, {"headers": {"location": location}}
        repo = self.repos.get(full_name)
        if repo is None:
            return 404, {"json": {"message": "Not Found"}}
        rest = parts[3:]
        if not rest:
            return 200, repo
        kind = {"issues": "issues", "pulls": "pulls", "actions": "actions"}.get(rest[0])
        if kind and (full_name, kind) in self.forbidden:
            return 403, {"json": {"message": "Resource not accessible by personal access token"}}
        params = parse_qs(urlsplit(str(request.url)).query)
        if rest == ["issues"]:
            return 200, self._paged(request, sorted_desc(self.issues[full_name]), params)
        if rest == ["pulls"]:
            return 200, self._paged(request, sorted_desc(self.pulls[full_name]), params)
        if rest == ["actions", "runs"]:
            per_page = int(params.get("per_page", ["30"])[0])
            runs = self.runs[full_name]
            return 200, {"total_count": len(runs), "workflow_runs": runs[:per_page]}
        if len(rest) == 4 and rest[:2] == ["actions", "runs"] and rest[3] == "jobs":
            jobs = self.jobs.get(int(rest[2]), [])
            return 200, {"total_count": len(jobs), "jobs": jobs}
        return 404, {"json": {"message": "Not Found"}}

    def _paged(
        self, request: httpx.Request, items: list[dict[str, Any]], params: dict[str, list[str]]
    ) -> Any:
        per_page = int(params.get("per_page", ["30"])[0])
        page = int(params.get("page", ["1"])[0])
        chunk = items[(page - 1) * per_page : page * per_page]
        if page * per_page >= len(items):
            return chunk
        query = {k: v[0] for k, v in params.items()}
        query["page"] = str(page + 1)
        link = f'<{BASE}{request.url.path}?{urlencode(query)}>; rel="next"'
        return {"_link": link, "items": chunk}


def sorted_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda i: i["updated_at"], reverse=True)
