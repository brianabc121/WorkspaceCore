"""开发者总览（T-0175，二批四页改造计划 Task 4 Step 1）。

真实 PostgreSQL / Redis；投影直接写 external_resources（模拟 GitHub 同步的结果），只测读侧聚合：
- 统计覆盖所选范围的全部页，不受分页影响；跨工作空间、第二个集成、tombstone、移出清单的仓库都不算；
- 开放 Issue 不含 PR、开放 PR 不含已合并 / 已关闭；失败只算 completed 且 failure / timed_out，按
  source_updated_at 归窗，同一运行只算一次；
- 缺少权限 / 仓库不可见 / 失败运行缺时间 → 对应数字为 null、标 partial，不当成 0；
- 开放 PR 的排序只用 requested_reviewers 证据；最近更新标明时间依据；
- 游标绑定工作空间、集成与筛选；未配置 / 停用 / Token 被拒时不返回私有记录。
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from tests.helpers import ClientFactory, add_workspace, asyncpg_dsn
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _fresh_now() -> None:
    """每个用例开始时重新取“现在”：NOW 若停在导入时刻，全量测试跑到后面时
    “两分钟前同步”的记录会超过 sync_stale_after_seconds（15 分钟）被判为过期（本地全量复现）。"""
    global NOW
    NOW = datetime.now(UTC)


class Seed:
    """直接写集成与投影；同一个 asyncpg 连接，批量插入。"""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn

    async def integration(
        self,
        workspace_id: uuid.UUID,
        repositories: list[str],
        *,
        status: str = "active",
        synced: bool = True,
        settings: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        name: str = "GitHub",
    ) -> uuid.UUID:
        integration_id: uuid.UUID = await self.conn.fetchval(
            "INSERT INTO workspace.integrations (workspace_id, provider, name, base_url,"
            " public_url, status, settings_json, created_at)"
            " VALUES ($1, 'github', $5, 'https://api.github.com', 'https://github.com',"
            " $2, $3::jsonb, $4) RETURNING id",
            workspace_id,
            status,
            json.dumps({"repositories": repositories, **(settings or {})}),
            created_at or NOW - timedelta(days=30),
            name,
        )
        if synced:
            await self.conn.execute(
                "INSERT INTO workspace.sync_cursors (integration_id, resource_type,"
                " last_success_at) VALUES ($1, 'github', $2)",
                integration_id,
                NOW - timedelta(minutes=2),
            )
        return integration_id

    async def rows(self, rows: list[dict[str, Any]]) -> list[uuid.UUID]:
        ids = [uuid.uuid4() for _ in rows]
        await self.conn.executemany(
            "INSERT INTO workspace.external_resources (id, workspace_id, integration_id,"
            " resource_type, external_id, title, source_url, metadata_json, source_updated_at,"
            " synced_at, deleted_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)",
            [
                (
                    row_id,
                    row["workspace_id"],
                    row["integration_id"],
                    row["type"],
                    str(uuid.uuid4().int)[:12],
                    row["title"],
                    row.get("url", "https://github.com/" + row["title"]),
                    json.dumps(row.get("meta", {})),
                    row.get("updated", NOW - timedelta(hours=1)),
                    row.get("synced", NOW - timedelta(minutes=2)),
                    row.get("deleted"),
                )
                for row_id, row in zip(ids, rows, strict=True)
            ],
        )
        return ids

    async def repo(
        self, workspace_id: uuid.UUID, integration_id: uuid.UUID, name: str, **meta: Any
    ) -> uuid.UUID:
        deleted = meta.pop("deleted", None)
        [repo_id] = await self.rows(
            [
                {
                    "workspace_id": workspace_id,
                    "integration_id": integration_id,
                    "type": "repo",
                    "title": name,
                    "meta": {"full_name": name, "access": "ok", "default_branch": "main", **meta},
                    "deleted": deleted,
                }
            ]
        )
        return repo_id

    async def child(
        self,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        repo_id: uuid.UUID,
        repo_name: str,
        kind: str,
        title: str,
        **fields: Any,
    ) -> uuid.UUID:
        meta = {"repo_resource_id": str(repo_id), "repo_full_name": repo_name, "number": 1}
        meta.update(fields.pop("meta", {}))
        [child_id] = await self.rows(
            [
                {
                    "workspace_id": workspace_id,
                    "integration_id": integration_id,
                    "type": kind,
                    "title": title,
                    "meta": meta,
                    **fields,
                }
            ]
        )
        return child_id


def issue(state: str = "open") -> dict[str, Any]:
    return {"meta": {"state": state}}


def pull(
    state: str = "open", *, merged: bool = False, reviewers: list[str] | None = None
) -> dict[str, Any]:
    return {
        "meta": {
            "state": state,
            "merged": merged,
            "merged_at": NOW.isoformat() if merged else None,
            "requested_reviewers": reviewers or [],
            "head_ref": "feature",
            "base_ref": "main",
        }
    }


def run(
    conclusion: str | None,
    *,
    status: str = "completed",
    updated: datetime | None = NOW - timedelta(hours=1),
    attempt: int = 1,
) -> dict[str, Any]:
    summary = (
        {"failed_jobs": [{"name": "web", "failed_steps": ["Typecheck"]}]}
        if conclusion == "failure"
        else None
    )
    return {
        "meta": {
            "status": status,
            "conclusion": conclusion,
            "event": "push",
            "head_branch": "main",
            "head_sha": "abc123",
            "run_number": 1,
            "run_attempt": attempt,
            "run_started_at": (updated or NOW).isoformat(),
            "failure_summary": summary,
        },
        "updated": updated,
    }


class Ctx:
    def __init__(self, env: Env, seed: Seed) -> None:
        self.env = env
        self.seed = seed
        self.ws = uuid.UUID(env.workspace_id)

    async def overview(self, *, workspace: str | None = None, **params: Any) -> dict[str, Any]:
        response = await self.get(workspace=workspace, **params)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def get(self, *, workspace: str | None = None, **params: Any) -> Any:
        return await self.env.client.get(
            "/api/v1/developer/overview",
            params={k: v for k, v in params.items() if v is not None},
            headers={"X-Workspace-Id": workspace or self.env.workspace_id},
        )


@pytest.fixture
async def ctx(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Ctx]:
    async with open_env(make_client, migrated_database_url) as env:
        conn = await asyncpg.connect(asyncpg_dsn(migrated_database_url))
        try:
            yield Ctx(env, Seed(conn))
        finally:
            await conn.close()


async def test_summary_covers_every_page_and_only_the_current_scope(ctx: Ctx) -> None:
    """超过 100 个仓库：统计是全量的，分页稳定；别的工作空间、第二个集成、移出清单的仓库、
    tombstone 都不算。"""
    seed, ws = ctx.seed, ctx.ws
    names = [f"org/repo-{i:03d}" for i in range(105)]
    integration = await seed.integration(ws, names)
    repo_ids = await seed.rows(
        [
            {
                "workspace_id": ws,
                "integration_id": integration,
                "type": "repo",
                "title": name,
                "meta": {"full_name": name, "access": "ok", "default_branch": "main"},
            }
            for name in names
        ]
    )
    await seed.rows(
        [
            {
                "workspace_id": ws,
                "integration_id": integration,
                "type": "issue",
                "title": f"问题 {name}",
                "meta": {"repo_resource_id": str(repo_id), "repo_full_name": name, "state": "open"},
            }
            for repo_id, name in zip(repo_ids, names, strict=True)
        ]
    )
    first, first_name = repo_ids[0], names[0]

    async def child(kind: str, title: str, **fields: Any) -> uuid.UUID:
        return await seed.child(ws, integration, first, first_name, kind, title, **fields)

    await child("issue", "另一个开放问题", **issue())
    await child("issue", "已关闭的问题", **issue("closed"))
    await child("issue", "已删除的问题", **issue(), deleted=NOW)
    await child("pull", "开放的 PR", **pull())
    await child("pull", "已合并的 PR", **pull("closed", merged=True))
    await child("pull", "已关闭的 PR", **pull("closed"))
    # 移出清单但还没写 tombstone 的仓库、写了 tombstone 的仓库：都不算
    old = await seed.repo(ws, integration, "org/removed")
    await seed.child(ws, integration, old, "org/removed", "issue", "旧仓库的问题", **issue())
    # 同一工作空间里第二个 GitHub 集成（V1 只用最早的那个）与别的工作空间的同名仓库
    other = await seed.integration(ws, [first_name], created_at=NOW, name="第二个")
    shadow = await seed.repo(ws, other, first_name)
    await seed.child(ws, other, shadow, first_name, "issue", "第二个集成的问题", **issue())
    elsewhere = await add_workspace(ctx.env.database_url, ctx.env.owner_id, "别的空间")
    foreign = await seed.integration(elsewhere, [first_name])
    foreign_repo = await seed.repo(elsewhere, foreign, first_name)
    await seed.child(elsewhere, foreign, foreign_repo, first_name, "issue", "别处", **issue())

    data = await ctx.overview()
    summary = data["summary"]
    assert summary["repo_count"] == 105
    assert summary["open_issue_count"] == 106  # 每个仓库 1 个 + 第一个仓库多 1 个
    assert summary["open_pull_count"] == 1  # 不含已合并 / 已关闭
    assert summary["failed_run_count"] == 0
    window = datetime.fromisoformat(summary["window_end"]) - datetime.fromisoformat(
        summary["window_start"]
    )
    assert window == timedelta(days=7)
    assert data["partial"] is False and data["sync"]["status"] == "ok"
    page = data["repos"]
    assert len(page["items"]) == 12 and page["total_estimate"] == 105
    row = page["items"][0]
    assert row["repo"]["full_name"] == first_name
    assert (row["open_issue_count"], row["open_pull_count"], row["failed_run_count"]) == (2, 1, 0)
    assert row["partial"] is False and row["latest_run"] is None

    seen = [item["repo"]["full_name"] for item in page["items"]]
    cursor = page["next_cursor"]
    while cursor:
        more = await ctx.overview(cursor=cursor, limit=50)
        assert more["summary"]["repo_count"] == 105  # 每一页的汇总都是全量
        seen += [item["repo"]["full_name"] for item in more["repos"]["items"]]
        cursor = more["repos"]["next_cursor"]
    assert seen == sorted(names)

    # 游标不能换筛选、时间窗或工作空间使用
    second = page["next_cursor"]
    assert (await ctx.get(cursor=second, q="repo")).status_code == 422
    assert (await ctx.get(cursor=second, range="24h")).status_code == 422
    assert (await ctx.get(cursor=second, workspace=str(elsewhere))).status_code == 422
    assert (await ctx.get(cursor="not-a-cursor")).status_code == 422
    assert (await ctx.get(cursor=second, limit=50)).status_code == 200  # 页大小可以变

    # 别的工作空间只看到自己的
    foreign_view = await ctx.overview(workspace=str(elsewhere))
    assert foreign_view["summary"]["repo_count"] == 1
    assert foreign_view["summary"]["open_issue_count"] == 1


async def test_name_search_escapes_like_wildcards(ctx: Ctx) -> None:
    seed, ws = ctx.seed, ctx.ws
    integration = await seed.integration(ws, ["org/a_b", "org/axb", "org/Web"])
    for name in ["org/a_b", "org/axb", "org/Web"]:
        await seed.repo(ws, integration, name)
    found = await ctx.overview(q="a_b")
    assert [r["repo"]["full_name"] for r in found["repos"]["items"]] == ["org/a_b"]
    assert (await ctx.overview(q="%"))["summary"]["repo_count"] == 0
    assert (await ctx.overview(q="web"))["summary"]["repo_count"] == 1  # 不区分大小写
    invalid: list[dict[str, Any]] = [
        {"q": ""},
        {"q": "x" * 101},
        {"range": "30d"},
        {"limit": 0},
        {"limit": 101},
    ]
    for bad in invalid:
        assert (await ctx.get(**bad)).status_code == 422, bad
    assert (await ctx.get(cursor="c" * 513)).status_code == 422


async def test_failed_runs_window_conclusions_and_retries(ctx: Ctx) -> None:
    seed, ws = ctx.seed, ctx.ws
    integration = await seed.integration(ws, ["org/app", "org/untimed"])
    app = await seed.repo(ws, integration, "org/app")

    async def add(title: str, **fields: Any) -> uuid.UUID:
        return await seed.child(ws, integration, app, "org/app", "run", title, **fields)

    recent = await add("半小时前失败", **run("failure", updated=NOW - timedelta(minutes=30)))
    await add("三天前超时", **run("timed_out", updated=NOW - timedelta(days=3)))
    await add("八天前失败（超窗）", **run("failure", updated=NOW - timedelta(days=8)))
    for conclusion in ["cancelled", "skipped", "neutral", "success", "startup_failure"]:
        await add(f"{conclusion} 不算失败", **run(conclusion))
    await add("仍在运行", **run(None, status="in_progress"))
    # 同一个运行重跑了三次：投影只有一行，只算一次
    retried = await add(
        "重跑三次后失败", **run("failure", updated=NOW - timedelta(hours=30), attempt=3)
    )

    week = await ctx.overview(repo_id=str(app))
    assert week["summary"]["failed_run_count"] == 3
    assert [r["id"] for r in week["failed_runs"]][:2] == [str(recent), str(retried)]
    assert len(week["failed_runs"]) == 3  # 最多 3 个
    assert week["failed_runs"][0]["failure_summary"] == {
        "failed_jobs": [{"name": "web", "failed_steps": ["Typecheck"]}]
    }
    assert week["repos"]["items"][0]["latest_run"]["display_title"] == "半小时前失败"
    assert week["partial"] is False
    day = await ctx.overview(repo_id=str(app), range="24h")
    assert day["summary"]["failed_run_count"] == 1
    assert [r["id"] for r in day["failed_runs"]] == [str(recent)]

    # 失败运行缺少更新时间：不猜它在不在窗里，这个仓库的数字未知
    untimed = await seed.repo(ws, integration, "org/untimed")
    await seed.child(
        ws, integration, untimed, "org/untimed", "run", "缺时间", **run("failure", updated=None)
    )
    everything = await ctx.overview()
    assert everything["summary"]["failed_run_count"] is None
    assert everything["partial"] is True
    rows = {r["repo"]["full_name"]: r for r in everything["repos"]["items"]}
    assert rows["org/untimed"]["failed_run_count"] is None and rows["org/untimed"]["partial"]
    assert rows["org/app"]["failed_run_count"] == 3
    assert (await ctx.overview(repo_id=str(app)))["partial"] is False


async def test_missing_permissions_are_unknown_not_zero(ctx: Ctx) -> None:
    seed, ws = ctx.seed, ctx.ws
    configured = ["org/ok", "org/no-actions", "org/gone", "org/legacy", "org/new"]
    integration = await seed.integration(ws, configured)
    ok = await seed.repo(ws, integration, "org/ok")
    no_actions = await seed.repo(
        ws, integration, "org/no-actions", access="missing_permission", denied_kinds=["run"]
    )
    await seed.repo(ws, integration, "org/gone", access="not_found", denied_kinds=[])
    await seed.repo(ws, integration, "org/legacy", access="missing_permission")
    for title in ["甲", "乙"]:
        await seed.child(ws, integration, no_actions, "org/no-actions", "issue", title, **issue())
    await seed.child(ws, integration, ok, "org/ok", "issue", "丙", **issue())

    data = await ctx.overview()
    summary = data["summary"]
    assert summary["repo_count"] == 4  # org/new 还没有同步到：不在已同步范围内，但标 partial
    assert summary["open_issue_count"] is None
    assert summary["open_pull_count"] is None
    assert summary["failed_run_count"] is None
    assert data["partial"] is True
    rows = {r["repo"]["full_name"]: r for r in data["repos"]["items"]}
    assert (
        rows["org/no-actions"]["open_issue_count"],
        rows["org/no-actions"]["open_pull_count"],
    ) == (
        2,
        0,
    )
    assert rows["org/no-actions"]["failed_run_count"] is None
    for name in ["org/gone", "org/legacy"]:
        counts = [
            rows[name][k] for k in ("open_issue_count", "open_pull_count", "failed_run_count")
        ]
        assert counts == [None, None, None] and rows[name]["partial"] is True
    assert rows["org/ok"]["partial"] is False

    single = await ctx.overview(repo_id=str(no_actions))
    assert single["summary"]["open_issue_count"] == 2
    assert single["summary"]["failed_run_count"] is None and single["partial"] is True
    assert (await ctx.overview(repo_id=str(ok)))["partial"] is False
    # 名称筛选只命中还没同步到的已配置仓库：0 个仓库，但范围不完整
    unsynced = await ctx.overview(q="new")
    assert unsynced["summary"]["repo_count"] == 0 and unsynced["partial"] is True
    assert (await ctx.overview(q="org/ok"))["partial"] is False


async def test_attention_pulls_and_recent_updates_use_only_real_evidence(ctx: Ctx) -> None:
    seed, ws = ctx.seed, ctx.ws
    integration = await seed.integration(ws, ["org/app"])
    app = await seed.repo(ws, integration, "org/app")

    async def add(kind: str, title: str, hours: float, **fields: Any) -> uuid.UUID:
        fields["updated"] = NOW - timedelta(hours=hours)
        return await seed.child(ws, integration, app, "org/app", kind, title, **fields)

    fresh = await add("pull", "最新但没请求审查", 1, **pull())
    requested_old = await add("pull", "较早、已请求审查", 9, **pull(reviewers=["alice"]))
    requested_new = await add("pull", "较新、已请求审查", 5, **pull(reviewers=["bob"]))
    for i in range(4):
        await add("pull", f"其他开放 PR {i}", 20 + i, **pull())
    await add("pull", "已合并", 0.5, **pull("closed", merged=True))
    await add("pull", "已关闭", 0.5, **pull("closed"))
    data = await ctx.overview()
    attention = data["attention_pulls"]
    assert len(attention) == 5
    assert [p["id"] for p in attention[:3]] == [str(requested_new), str(requested_old), str(fresh)]
    assert all(p["state"] == "open" and not p["merged"] for p in attention)
    assert "ci_status" not in attention[0] and "head_sha" not in attention[0]  # 不推断 CI 关联
    assert data["summary"]["open_pull_count"] == 7

    # 只有同步时间的记录：time_basis 是 synced_at，不编造 GitHub 上的更新时间
    synced_only = await seed.child(
        ws,
        integration,
        app,
        "org/app",
        "issue",
        "只有同步时间",
        **issue(),
        updated=None,
        synced=NOW + timedelta(minutes=1),
    )
    updates = (await ctx.overview())["recent_updates"]
    assert len(updates) == 5
    assert updates[0]["id"] == str(synced_only)
    assert updates[0]["time_basis"] == "synced_at" and updates[0]["kind"] == "issue"
    assert updates[0]["repo_id"] == str(app) and updates[0]["repo_full_name"] == "org/app"
    assert {u["time_basis"] for u in updates[1:]} == {"source_updated_at"}
    assert set(updates[0]) == {
        "id",
        "kind",
        "repo_id",
        "repo_full_name",
        "title",
        "source_url",
        "updated_at",
        "time_basis",
    }


async def test_withheld_states_and_rate_limited_cache(ctx: Ctx) -> None:
    seed, ws = ctx.seed, ctx.ws
    # 没有集成：not_configured，统计未知；指定仓库 404
    empty = await ctx.overview()
    assert empty["sync"]["status"] == "not_configured"
    assert empty["summary"]["repo_count"] is None and empty["repos"]["items"] == []
    assert (await ctx.get(repo_id=str(uuid.uuid4()))).status_code == 404

    # 按现在的时间算：NOW 是导入时定的，全量测试跑到这里可能已经过了十几分钟（限流早已过期）
    until = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    integration = await seed.integration(ws, ["org/app"], settings={"rate_limited_until": until})
    app = await seed.repo(ws, integration, "org/app")
    await seed.child(ws, integration, app, "org/app", "issue", "缓存的问题", **issue())
    # 限流：照常返回缓存，sync 带 retry_at 与 as_of
    limited = await ctx.overview()
    assert limited["sync"]["reason"] == "rate_limited" and limited["sync"]["status"] == "stale"
    assert limited["sync"]["retry_at"] is not None and limited["sync"]["as_of"] is not None
    assert limited["summary"]["open_issue_count"] == 1
    assert [r["repo"]["full_name"] for r in limited["repos"]["items"]] == ["org/app"]
    assert len(limited["recent_updates"]) == 2

    # 不在当前范围的仓库 404（不存在 / 别的工作空间）
    assert (await ctx.get(repo_id=str(uuid.uuid4()))).status_code == 404
    elsewhere = await add_workspace(ctx.env.database_url, ctx.env.owner_id, "别的空间")
    assert (await ctx.get(repo_id=str(app), workspace=str(elsewhere))).status_code == 404

    for status, expected in [("unauthorized", "unauthorized"), ("disabled", "not_configured")]:
        await seed.conn.execute(
            "UPDATE workspace.integrations SET status = $1 WHERE id = $2", status, integration
        )
        withheld = await ctx.overview()
        assert withheld["sync"]["status"] == expected
        assert withheld["repos"]["items"] == [] and withheld["recent_updates"] == []
        assert withheld["attention_pulls"] == [] and withheld["failed_runs"] == []
        assert all(
            withheld["summary"][k] is None
            for k in ("repo_count", "open_issue_count", "open_pull_count", "failed_run_count")
        )
        assert withheld["partial"] is True
        # 指定仓库也只返回空结果，不泄漏记录
        assert (await ctx.overview(repo_id=str(app)))["repos"]["items"] == []


async def test_never_synced_counts_are_unknown(ctx: Ctx) -> None:
    """从未成功同步：本地只有半轮同步写下的数据时，数字一律未知。"""
    seed, ws = ctx.seed, ctx.ws
    integration = await seed.integration(ws, ["org/app"], synced=False)
    app = await seed.repo(ws, integration, "org/app")
    await seed.child(ws, integration, app, "org/app", "issue", "半轮同步", **issue())
    data = await ctx.overview()
    assert data["sync"]["reason"] == "not_synced"
    assert data["summary"]["repo_count"] is None and data["summary"]["open_issue_count"] is None
    assert data["repos"]["items"][0]["open_issue_count"] is None and data["partial"] is True


async def test_viewer_can_read_and_outsiders_cannot(ctx: Ctx) -> None:
    seed, ws = ctx.seed, ctx.ws
    integration = await seed.integration(ws, ["org/app"])
    await seed.repo(ws, integration, "org/app")
    async with ctx.env.member("viewer@example.com", "viewer") as viewer:
        response = await viewer.get(
            "/api/v1/developer/overview", headers={"X-Workspace-Id": ctx.env.workspace_id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["repo_count"] == 1
        elsewhere = await add_workspace(ctx.env.database_url, ctx.env.owner_id, "别的空间")
        denied = await viewer.get(
            "/api/v1/developer/overview", headers={"X-Workspace-Id": str(elsewhere)}
        )
        assert denied.status_code in {403, 404}
