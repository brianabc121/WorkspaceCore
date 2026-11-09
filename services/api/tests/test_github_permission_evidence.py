"""逐仓库读权限的核验快照（T-0175，二批四页改造计划 Task 4 Step 2）。

管理员“测试连接”把实际得到的四项检查存进 Integration.settings_json；开发者页只读这份快照：
- 没有快照就是“未核验”，不从 Repo.access=ok 推导；
- metadata 失败只记 metadata 一项；中途限流只记已执行的几项，没检查到的仓库保留旧快照与旧时间；
- Token / 仓库清单 / 启停变化时清除；Token 被拒时不返回旧证据；
- 两个新 GET 不访问 GitHub、不排队同步、不做测试连接。
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.domain import sync
from tests.fake_github import TOKEN, FakeGitHub
from tests.helpers import ClientFactory, add_workspace, run_sql
from tests.workspace_support import Env, RecordingQueue, app_of, open_env, sync_context

pytestmark = pytest.mark.integration

FOUR = ["metadata", "issues", "pull_requests", "actions"]


@dataclass
class Ctx:
    env: Env
    fake: FakeGitHub

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def connect(self, repositories: list[str]) -> dict[str, Any]:
        created = await self.client.post(
            "/api/v1/integrations",
            json={
                "provider": "github",
                "name": "GitHub",
                "api_token": TOKEN,
                "repositories": repositories,
            },
            headers=self.env.headers(write=True),
        )
        assert created.status_code == 201, created.text
        integration: dict[str, Any] = created.json()
        await self.test(integration["id"])
        return integration

    async def test(self, integration_id: str) -> dict[str, Any]:
        tested = await self.client.post(
            f"/api/v1/integrations/{integration_id}/test", headers=self.env.headers(write=True)
        )
        assert tested.status_code == 200, tested.text
        data: dict[str, Any] = tested.json()
        return data

    async def current(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = (await self.env.get("/api/v1/integrations"))["items"]
        return items[0]

    async def patch(self, integration_id: str, **body: Any) -> dict[str, Any]:
        version = (await self.current())["version"]
        response = await self.client.patch(
            f"/api/v1/integrations/{integration_id}",
            json={"version": version, **body},
            headers=self.env.headers(write=True),
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def repo_ids(self) -> dict[str, str]:
        page = await self.env.get("/api/v1/developer/repos")
        return {r["full_name"]: r["id"] for r in page["items"]}

    async def evidence(self, repo_id: str, workspace: str | None = None) -> httpx.Response:
        return await self.client.get(
            f"/api/v1/developer/repos/{repo_id}/permissions",
            headers={"X-Workspace-Id": workspace or self.env.workspace_id},
        )

    async def permissions(self, repo_id: str) -> dict[str, Any]:
        response = await self.evidence(repo_id)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def stored(self) -> Any:
        raw = await run_sql(
            self.env.database_url,
            "SELECT settings_json -> 'github_permission_evidence' FROM workspace.integrations",
        )
        return json.loads(raw) if raw else None


@pytest.fixture
async def ctx(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Ctx]:
    fake = FakeGitHub()
    fake.add_repo("octo/app")
    fake.add_repo("octo/docs")
    fake.add_issue("octo/app", "登录失败")
    fake.add_run("octo/app", failed=True)
    async with open_env(
        make_client, migrated_database_url, integration_allowed_hosts="api.github.com"
    ) as env:
        app_of(env.client).state.connectors.transport = fake.transport
        yield Ctx(env, fake)


async def test_snapshot_records_actual_checks_and_nothing_secret(ctx: Ctx) -> None:
    ctx.fake.forbidden.add(("octo/docs", "actions"))
    await ctx.connect(["octo/app", "octo/docs"])
    ids = await ctx.repo_ids()
    app = await ctx.permissions(ids["octo/app"])
    assert app["full_name"] == "octo/app" and app["complete"] is True
    assert [c["name"] for c in app["checks"]] == FOUR
    assert all(c["ok"] for c in app["checks"]) and app["checked_at"] is not None
    assert app["sync"]["status"] == "ok"
    docs = await ctx.permissions(ids["octo/docs"])
    assert docs["complete"] is True
    assert docs["checks"][-1] == {"name": "actions", "ok": False, "code": "missing_permission"}
    # 同步记下缺少的是哪一类：总览里只有运行数未知，Issue / PR 仍可确定
    rows = {
        r["repo"]["full_name"]: r
        for r in (await ctx.env.get("/api/v1/developer/overview"))["repos"]["items"]
    }
    assert rows["octo/docs"]["repo"]["access"] == "missing_permission"
    assert rows["octo/docs"]["open_issue_count"] == 0
    assert rows["octo/docs"]["failed_run_count"] is None
    # 内存版 GitHub 的运行时间固定在 2026-10-30，是否落在近 7 天取决于今天；只要求能确定
    assert rows["octo/app"]["open_issue_count"] == 1
    assert rows["octo/app"]["failed_run_count"] is not None
    stored = await ctx.stored()
    assert stored["schema_version"] == 1
    assert set(stored["repositories"]) == {"octo/app", "octo/docs"}
    text = json.dumps(stored)
    for secret in [TOKEN, "Bearer", "authorization", "x-github", "message"]:
        assert secret.lower() not in text.lower()


async def test_without_a_snapshot_checks_are_unverified_even_if_access_is_ok(ctx: Ctx) -> None:
    await ctx.connect(["octo/app"])
    await run_sql(
        ctx.env.database_url,
        "UPDATE workspace.integrations"
        " SET settings_json = settings_json - 'github_permission_evidence'",
    )
    repo_id = (await ctx.repo_ids())["octo/app"]
    assert (await ctx.env.get("/api/v1/developer/repos"))["items"][0]["access"] == "ok"
    evidence = await ctx.permissions(repo_id)
    assert evidence == {
        "repo_id": repo_id,
        "full_name": "octo/app",
        "checked_at": None,
        "checks": [],
        "complete": False,
        "sync": evidence["sync"],
    }
    # 认不出的旧格式同样当作未核验
    await run_sql(
        ctx.env.database_url,
        "UPDATE workspace.integrations SET settings_json = settings_json"
        """ || '{"github_permission_evidence": {"schema_version": 99, "repositories": {}}}'""",
    )
    assert (await ctx.permissions(repo_id))["checks"] == []


async def test_metadata_failure_records_only_metadata(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app", "octo/docs"])
    ids = await ctx.repo_ids()
    del ctx.fake.repos["octo/docs"]  # 仓库对 Token 不可见了：metadata 404，其余三项不执行
    await ctx.test(integration["id"])
    docs = await ctx.permissions(ids["octo/docs"])
    assert docs["checks"] == [{"name": "metadata", "ok": False, "code": "not_found"}]
    assert docs["complete"] is False and docs["checked_at"] is not None
    assert (await ctx.permissions(ids["octo/app"]))["complete"] is True


async def test_interrupted_test_keeps_only_obtained_results(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app", "octo/docs"])
    ids = await ctx.repo_ids()
    before_app = await ctx.permissions(ids["octo/app"])
    before_docs = await ctx.permissions(ids["octo/docs"])

    def limited(request: httpx.Request) -> httpx.Response:
        # 检查到 octo/app 的 PR 权限时被次级限流：后面的检查都没有执行
        if request.url.path == "/repos/octo/app/pulls":
            return httpx.Response(
                429, json={"message": "secondary rate limit"}, headers={"retry-after": "30"}
            )
        return ctx.fake.handle(request)

    app_of(ctx.client).state.connectors.transport = httpx.MockTransport(limited)
    result = await ctx.test(integration["id"])
    assert result["ok"] is False
    app = await ctx.permissions(ids["octo/app"])
    assert [c["name"] for c in app["checks"]] == ["metadata", "issues"]
    assert all(c["ok"] for c in app["checks"]) and app["complete"] is False
    assert app["checked_at"] != before_app["checked_at"]
    # 没检查到的仓库保留上一次的结果与时间，不伪造本次核验
    docs = await ctx.permissions(ids["octo/docs"])
    assert docs["checks"] == before_docs["checks"] and docs["complete"] is True
    assert docs["checked_at"] == before_docs["checked_at"]


async def test_rate_limit_keeps_previous_snapshot_and_time(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app"])
    repo_id = (await ctx.repo_ids())["octo/app"]
    before = await ctx.permissions(repo_id)
    ctx.fake.rate_limit = "primary"
    result = await ctx.test(integration["id"])  # 第一个请求就被限流：没有任何新结果
    assert result["ok"] is False and result["repositories"] == []
    after = await ctx.permissions(repo_id)
    assert after["checks"] == before["checks"] and after["checked_at"] == before["checked_at"]
    assert after["complete"] is True


async def test_rate_limited_sync_reports_retry_and_keeps_snapshot(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app"])
    repo_id = (await ctx.repo_ids())["octo/app"]
    before = await ctx.permissions(repo_id)
    ctx.fake.rate_limit = "primary"
    result = await sync.sync_integration(
        sync_context(ctx.client), uuid.UUID(integration["id"]), force_full=True
    )
    assert result == "failed"
    after = await ctx.permissions(repo_id)
    assert after["sync"]["reason"] == "rate_limited" and after["sync"]["retry_at"] is not None
    assert after["checked_at"] == before["checked_at"] and after["complete"] is True
    overview = await ctx.env.get("/api/v1/developer/overview")
    assert overview["sync"]["reason"] == "rate_limited"
    assert [r["repo"]["full_name"] for r in overview["repos"]["items"]] == ["octo/app"]


async def test_configuration_changes_clear_the_snapshot(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app", "octo/docs"])
    integration_id = integration["id"]
    ids = await ctx.repo_ids()
    # 同样的仓库清单（顺序、大小写不同）不算修改
    await ctx.patch(integration_id, repositories=["Octo/Docs", "octo/app"])
    assert (await ctx.permissions(ids["octo/app"]))["complete"] is True
    # 仓库清单真的改了：清除
    await ctx.patch(integration_id, repositories=["octo/app"])
    assert await ctx.stored() is None
    assert (await ctx.permissions(ids["octo/app"]))["checks"] == []
    assert (await ctx.evidence(ids["octo/docs"])).status_code == 404  # 不在当前清单里
    # 重新测试后恢复；换 Token：清除
    await ctx.test(integration_id)
    assert (await ctx.permissions(ids["octo/app"]))["complete"] is True
    await ctx.patch(integration_id, api_token=TOKEN)
    assert await ctx.stored() is None
    assert (await ctx.permissions(ids["octo/app"]))["checked_at"] is None
    # 停用：清除，接口不返回任何证据；重新启用也要重新测试
    await ctx.test(integration_id)
    await ctx.patch(integration_id, enabled=False)
    assert await ctx.stored() is None
    disabled = await ctx.permissions(ids["octo/app"])
    assert disabled["sync"]["status"] == "not_configured"
    assert disabled["full_name"] is None and disabled["checks"] == []
    await ctx.patch(integration_id, enabled=True)
    assert (await ctx.permissions(ids["octo/app"]))["checks"] == []


async def test_rejected_token_hides_and_clears_old_evidence(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app"])
    repo_id = (await ctx.repo_ids())["octo/app"]
    assert (await ctx.permissions(repo_id))["complete"] is True
    ctx.fake.token = "github_pat_" + "rotated"  # GitHub 那边吊销了 Token
    await sync.sync_integration(
        sync_context(ctx.client), uuid.UUID(integration["id"]), force_full=True
    )
    rejected = await ctx.permissions(repo_id)
    assert rejected["sync"]["status"] == "unauthorized"
    assert rejected == {
        "repo_id": repo_id,
        "full_name": None,
        "checked_at": None,
        "checks": [],
        "complete": False,
        "sync": rejected["sync"],
    }
    assert await ctx.stored() is None
    overview = await ctx.env.get("/api/v1/developer/overview")
    assert overview["repos"]["items"] == [] and overview["summary"]["open_issue_count"] is None
    # 测试连接时 Token 被拒也一样清除
    await run_sql(
        ctx.env.database_url,
        "UPDATE workspace.integrations SET status = 'active', settings_json = settings_json"
        """ || '{"github_permission_evidence": {"schema_version": 1, "repositories": {}}}'""",
    )
    tested = await ctx.test(integration["id"])
    assert tested["status"] == "unauthorized"
    assert await ctx.stored() is None


async def test_new_reads_never_reach_github_or_start_work(ctx: Ctx) -> None:
    integration = await ctx.connect(["octo/app"])
    repo_id = (await ctx.repo_ids())["octo/app"]
    before = await ctx.current()
    calls: list[httpx.Request] = []

    def forbidden(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError(f"开发者页的读接口不应访问 GitHub：{request.url}")

    app = app_of(ctx.client)
    app.state.connectors.transport = httpx.MockTransport(forbidden)
    queue = RecordingQueue()
    app.state.jobs = queue
    variants: list[dict[str, Any]] = [
        {},
        {"range": "24h"},
        {"repo_id": repo_id},
        {"q": "octo", "limit": 1},
    ]
    for params in variants:
        data = await ctx.env.get("/api/v1/developer/overview", **params)
        assert data["summary"]["repo_count"] == 1
    assert (await ctx.permissions(repo_id))["complete"] is True
    assert calls == [] and queue.jobs == []
    after = await ctx.current()
    # 没有测试连接或同步：版本、测试时间、同步时间都没变
    assert after["version"] == before["version"]
    assert after["last_tested_at"] == before["last_tested_at"]
    assert after["sync"]["as_of"] == before["sync"]["as_of"]
    assert integration["id"] == after["id"]


async def test_permissions_are_scoped_to_the_workspace(ctx: Ctx) -> None:
    await ctx.connect(["octo/app"])
    repo_id = (await ctx.repo_ids())["octo/app"]
    assert (await ctx.evidence(str(uuid.uuid4()))).status_code == 404
    elsewhere = await add_workspace(ctx.env.database_url, ctx.env.owner_id, "别的空间")
    assert (await ctx.evidence(repo_id, workspace=str(elsewhere))).status_code == 404
    async with ctx.env.member("viewer@example.com", "viewer") as viewer:
        response = await viewer.get(
            f"/api/v1/developer/repos/{repo_id}/permissions",
            headers={"X-Workspace-Id": ctx.env.workspace_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["complete"] is True
