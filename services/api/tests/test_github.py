"""GitHub 只读 Connector（T-0028）。

覆盖：错误与限流分类、分页与 ETag、集成的新建 / 测试 / 同步、开发者页。内存版 GitHub 见
tests/fake_github.py；验收场景 A13（Token 失效不无限重试）、A17（限流不把空列表当正常）。
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pytest

from app.domain import sync
from app.integrations.base import ConnectorError
from app.integrations.github import GitHubClient, classify
from app.integrations.http import ConnectorHttp, OutboundPolicy
from tests.fake_github import BASE, TOKEN, FakeGitHub
from tests.helpers import OWNER, ClientFactory, csrf, run_sql
from tests.vikunja_support import CONNECTOR_SETTINGS, RecordingQueue, app_of, sync_context

SETTINGS = {**CONNECTOR_SETTINGS, "integration_allowed_hosts": "api.github.com"}


def _client(fake: FakeGitHub) -> GitHubClient:
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({"api.github.com"})),
        read_retries=0,
        transport=fake.transport,
    )
    return GitHubClient(http, TOKEN, BASE)


# ---------------------------------------------------------------- 客户端（不需要数据库）


@pytest.mark.parametrize(
    ("status", "headers", "body", "kind", "wait"),
    [
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1100"}, {}, "RATE_LIMITED", 101),
        (429, {"retry-after": "30"}, {}, "RATE_LIMITED", 30),
        (403, {}, {"message": "You have exceeded a secondary rate limit"}, "RATE_LIMITED", 60),
        (429, {}, {}, "RATE_LIMITED", 60),
        (
            403,
            {},
            {"message": "Resource not accessible by personal access token"},
            "AUTH_FAILED",
            None,
        ),
        (401, {}, {"message": "Bad credentials"}, "AUTH_FAILED", None),
        (404, {}, {"message": "Not Found"}, "NOT_FOUND", None),
        (410, {}, {}, "UNAVAILABLE", None),
        (502, {}, {}, "UNAVAILABLE", None),
    ],
)
def test_errors_are_classified(
    status: int, headers: dict[str, str], body: dict[str, str], kind: str, wait: int | None
) -> None:
    error = classify(httpx.Response(status, json=body, headers=headers), now=1000)
    assert error.kind == kind
    assert error.retry_after == wait
    if status == 403 and kind == "AUTH_FAILED":
        assert error.resource_level  # 缺少权限只影响这个资源，不让整个集成失效
    if status == 401:
        assert not error.resource_level


async def test_client_sends_version_header_and_paginates_by_link() -> None:
    fake = FakeGitHub()
    fake.add_repo("octo/app")
    for i in range(250):
        fake.add_issue("octo/app", f"问题 {i}")
    listing = await _client(fake).list_issues("octo/app")
    assert len(listing.items) == 250 and listing.complete
    pages = fake.calls("/issues")
    assert len(pages) == 3  # per_page=100，按 Link 翻页
    assert all(r.headers["x-github-api-version"] == "2026-03-10" for r in pages)
    assert all(r.headers["accept"] == "application/vnd.github+json" for r in pages)


async def test_issue_listing_skips_pull_requests_and_stops_at_since() -> None:
    fake = FakeGitHub()
    fake.add_repo("octo/app")
    old = fake.add_issue("octo/app", "旧问题")
    fake.add_pull("octo/app", "新功能")
    new = fake.add_issue("octo/app", "新问题")
    client = _client(fake)
    titles = [i["title"] for i in (await client.list_issues("octo/app")).items]
    assert titles == ["新问题", "旧问题"]  # PR 不在 Issue 里
    since = (await client.list_issues("octo/app", since=_parse(new["updated_at"]))).items
    assert [i["id"] for i in since] == [new["id"]]
    assert old["id"] not in [i["id"] for i in since]


async def test_conditional_request_and_redirects() -> None:
    fake = FakeGitHub()
    fake.add_repo("octo/new-name")
    fake.renamed["octo/old-name"] = "octo/new-name"
    client = _client(fake)
    first = await client.list_runs("octo/new-name")
    remaining = fake.remaining
    again = await client.list_runs("octo/new-name", etag=first.etag)
    assert again.not_modified
    assert fake.remaining == remaining  # 304 不计额度
    moved = await client.get_repo("octo/old-name")
    assert moved.data["full_name"] == "octo/new-name"  # 改名后的 301 跟随到同一个 API 地址


async def test_redirects_and_links_to_other_hosts_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://evil.example.com/repos/x/y"})

    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({"api.github.com"})),
        read_retries=0,
        transport=httpx.MockTransport(handler),
    )
    client = GitHubClient(http, TOKEN, BASE)
    with pytest.raises(ConnectorError) as raised:
        await client.get_repo("x/y")
    assert raised.value.reason == "redirect_not_allowed"
    assert client._next_link('<https://evil.example.com/x?page=2>; rel="next"') is None


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------- 集成与同步（真实数据库）


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    fake: FakeGitHub

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def create(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "provider": "github",
            "name": "GitHub",
            "api_token": TOKEN,
            "repositories": ["octo/app"],
            **overrides,
        }
        response = await self.client.post(
            "/api/v1/integrations", json=body, headers=self.headers(write=True)
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def test(self, integration: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/integrations/{integration['id']}/test", headers=self.headers(write=True)
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/v1/developer/{path}", params=params, headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    fake = FakeGitHub()
    fake.add_repo("octo/app", description="主仓库")
    fake.add_issue("octo/app", "登录失败")
    fake.add_pull("octo/app", "修复登录")
    fake.add_run("octo/app")
    fake.add_run("octo/app", failed=True)
    async with make_client(**SETTINGS) as client:
        app = app_of(client)
        app.state.connectors.transport = fake.transport
        app.state.jobs = RecordingQueue()
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url, fake)


@pytest.mark.integration
async def test_create_test_and_initial_sync(env: Env) -> None:
    integration = await env.create()
    assert integration["provider"] == "github"
    assert integration["base_url"] == "https://api.github.com"
    assert integration["public_url"] == "https://github.com"
    assert integration["repositories"] == ["octo/app"]
    assert integration["tested_version"] == "2026-03-10"
    assert integration["webhooks"]["reason"] == "not_supported"
    assert "github_pat_" not in str(integration)
    stored = await run_sql(
        env.database_url, "SELECT credential_ref FROM workspace.integration_accounts"
    )
    assert stored.startswith("enc:v1:")
    result = await env.test(integration)
    assert result["ok"] is True and result["status"] == "active"
    assert [c["name"] for c in result["checks"]] == ["reachable", "version", "token", "webhooks"]
    assert result["repositories"][0]["full_name"] == "octo/app"
    assert {c["name"] for c in result["repositories"][0]["checks"]} == {
        "metadata",
        "issues",
        "pull_requests",
        "actions",
    }
    [repo] = (await env.get("repos"))["items"]
    assert repo["full_name"] == "octo/app" and repo["access"] == "ok"
    assert repo["external_id"].startswith("github:repo:")
    issues = await env.get("issues")
    assert [i["title"] for i in issues["items"]] == ["登录失败"]  # PR 不在 Issue 列表里
    assert issues["items"][0]["labels"] == ["bug"] and issues["items"][0]["author"] == "octocat"
    assert issues["sync"]["status"] == "ok"
    [pull] = (await env.get("pulls"))["items"]
    assert pull["head_ref"] == "feature" and pull["requested_reviewers"] == ["reviewer"]
    runs = (await env.get("runs"))["items"]
    failed = [r for r in runs if r["conclusion"] == "failure"]
    assert failed[0]["failure_summary"] == {
        "failed_jobs": [{"name": "test", "failed_steps": ["Run pytest"]}]
    }
    assert next(r for r in runs if r["conclusion"] == "success")["failure_summary"] is None
    assert not any("/logs" in str(r.url) for r in env.fake.requests)  # 不读 Actions 日志
    filtered = await env.get("runs", conclusion="failure")
    assert len(filtered["items"]) == 1
    overview = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert overview.json()["modules"]["github"]["status"] == "ok"


@pytest.mark.integration
async def test_missing_permissions_are_reported_per_repository(env: Env) -> None:
    env.fake.forbidden.add(("octo/app", "actions"))
    env.fake.classic_scopes = "repo"
    integration = await env.create()
    result = await env.test(integration)
    assert result["ok"] is False
    checks = {c["name"]: c for c in result["repositories"][0]["checks"]}
    assert checks["actions"] == {"name": "actions", "ok": False, "code": "missing_permission"}
    assert checks["issues"]["ok"] is True
    assert any("经典 Token" in w for w in result["warnings"])
    # 缺少 Actions 权限只影响运行：Issue 照常同步，仓库标记 missing_permission
    assert (await env.get("issues"))["items"]
    assert (await env.get("runs"))["items"] == []
    assert (await env.get("repos"))["items"][0]["access"] == "missing_permission"


@pytest.mark.integration
async def test_rate_limit_is_reported_not_hidden(env: Env) -> None:
    """A17：被限流时列表照常返回投影，sync 标明 rate_limited 与恢复时间；限流期间不再请求。"""
    integration = await env.create()
    await env.test(integration)
    env.fake.rate_limit = "primary"
    env.fake.add_issue("octo/app", "限流期间的新问题")
    ctx = sync_context(env.client)
    assert (
        await sync.sync_integration(ctx, uuid.UUID(integration["id"]), force_full=True) == "failed"
    )
    issues = await env.get("issues")
    assert [i["title"] for i in issues["items"]] == ["登录失败"]  # 旧数据照常显示
    assert issues["sync"]["status"] == "stale"
    assert issues["sync"]["reason"] == "rate_limited"
    assert issues["sync"]["retry_at"] is not None
    module = (await env.client.get("/api/v1/dashboard/overview", headers=env.headers())).json()
    assert module["modules"]["github"]["reason"] == "rate_limited"
    assert module["modules"]["github"]["retry_at"] is not None
    before = len(env.fake.requests)
    assert await sync.sync_integration(ctx, uuid.UUID(integration["id"])) == "skipped"
    assert len(env.fake.requests) == before  # 恢复时间之前不再请求（避免被封禁）
    listed = await env.client.get("/api/v1/integrations", headers=env.headers())
    assert listed.json()["items"][0]["status"] == "active"  # 限流不是故障


@pytest.mark.integration
async def test_secondary_rate_limit_before_first_sync(env: Env) -> None:
    env.fake.rate_limit = "secondary"
    integration = await env.create()
    result = await env.test(integration)
    assert result["ok"] is False
    await sync.sync_integration(sync_context(env.client), uuid.UUID(integration["id"]))
    issues = await env.get("issues")
    assert issues["items"] == []
    assert issues["sync"]["status"] in {"unavailable", "stale"}


@pytest.mark.integration
async def test_rejected_token_stops_syncing(env: Env) -> None:
    """A13：Token 失效后集成转为 unauthorized，Worker 不再同步，页面提示重新连接。"""
    integration = await env.create()
    await env.test(integration)
    env.fake.token = "github_pat_" + "rotated"  # GitHub 那边吊销了原来的 Token
    ctx = sync_context(env.client)
    assert (
        await sync.sync_integration(ctx, uuid.UUID(integration["id"]), force_full=True) == "failed"
    )
    issues = await env.get("issues")
    assert issues["sync"]["status"] == "unauthorized"
    before = len(env.fake.requests)
    assert await sync.reconcile_all(ctx) == 0
    assert len(env.fake.requests) == before


@pytest.mark.integration
async def test_incremental_sync_uses_etags_and_full_sync_tombstones(env: Env) -> None:
    integration = await env.create()
    await env.test(integration)
    ctx = sync_context(env.client)
    remaining = env.fake.remaining
    assert await sync.sync_integration(ctx, uuid.UUID(integration["id"])) == "incremental"
    assert env.fake.remaining == remaining - 1  # 只有仓库本身计额度，三个列表都是 304
    assert all(r.headers.get("if-none-match") for r in env.fake.calls("/issues")[-1:])
    # 外部删除的 Issue 在全量同步后写 tombstone，列表里不再出现
    env.fake.issues["octo/app"] = [i for i in env.fake.issues["octo/app"] if "pull_request" in i]
    assert await sync.sync_integration(ctx, uuid.UUID(integration["id"]), force_full=True) == "full"
    assert (await env.get("issues"))["items"] == []
    assert (await env.get("pulls"))["items"]


@pytest.mark.integration
async def test_repositories_can_be_changed(env: Env) -> None:
    env.fake.add_repo("octo/docs")
    integration = await env.create(repositories=["octo/app", "octo/docs"])
    await env.test(integration)
    assert {r["full_name"] for r in (await env.get("repos"))["items"]} == {"octo/app", "octo/docs"}
    current = (await env.client.get("/api/v1/integrations", headers=env.headers())).json()["items"][
        0
    ]
    path = f"/api/v1/integrations/{integration['id']}"
    for body in [{"base_url": "https://github.example.com"}, {"api_token": "tk_" + "a" * 30}]:
        response = await env.client.patch(
            path, json={"version": current["version"], **body}, headers=env.headers(write=True)
        )
        assert response.status_code == 422, body
    moved = await env.client.patch(
        path,
        json={"version": current["version"], "repositories": ["octo/docs"]},
        headers=env.headers(write=True),
    )
    assert moved.status_code == 200
    assert moved.json()["repositories"] == ["octo/docs"]
    assert moved.json()["status"] == "active"  # 改仓库列表不需要重新测试
    await sync.sync_integration(
        sync_context(env.client), uuid.UUID(integration["id"]), force_full=True
    )
    assert [r["full_name"] for r in (await env.get("repos"))["items"]] == ["octo/docs"]
    assert (await env.get("issues"))["items"] == []  # 移出的仓库的 Issue 一起写 tombstone


@pytest.mark.integration
async def test_developer_pages_without_integration(env: Env) -> None:
    for path in ["repos", "issues", "pulls", "runs"]:
        page = await env.get(path)
        assert page["items"] == []
        assert page["sync"]["status"] == "not_configured"
    overview = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert overview.json()["modules"]["github"]["reason"] == "not_configured"
