"""知识总览的统计接口（T-0172，二批四页改造规范 4.2）：真实 PostgreSQL / Redis。

覆盖：全量笔记数不受图谱 2000 上限与列表分页影响；空空间、归档空间；
笔记关联按有向 (source, target) 去重，隐藏、删除的端点与资源链接不计；
同一笔记多次修订只算一次本周更新；时间窗上下界；参数校验（不带时区、先后颠倒、recent_limit 越界）；
viewer 可读；跨工作空间隔离；读取不写版本、审计与阅读用量。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from tests.helpers import add_user, add_workspace, asyncpg_dsn, csrf, run_sql
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration

OVERVIEW = "/api/v1/knowledge/overview"
# 远离“现在”的一周（周一 00:00 到下周一 00:00，UTC），时间窗用例把笔记的 updated_at 放在这里
WEEK_START = datetime(2026, 6, 1, tzinfo=UTC)
WEEK_END = WEEK_START + timedelta(days=7)
OLD = datetime(2026, 1, 5, tzinfo=UTC)
# 每篇笔记的版本号：读取前后应完全相同
VERSIONS = "SELECT string_agg(id || ':' || version, ',' ORDER BY id) FROM workspace.knowledge_notes"


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


def window(after: datetime = WEEK_START, before: datetime = WEEK_END) -> dict[str, str]:
    return {"updated_after": after.isoformat(), "updated_before": before.isoformat()}


async def overview(
    env: Env, *, client: httpx.AsyncClient | None = None, **params: Any
) -> httpx.Response:
    query = {**window(), **params}
    return await (client or env.client).get(OVERVIEW, params=query, headers=env.headers())


async def ok(env: Env, **params: Any) -> dict[str, Any]:
    response = await overview(env, **params)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def spaces(env: Env) -> dict[str, dict[str, Any]]:
    """知识库名 → 知识库（顺便补建项目的知识库）。"""
    items = (await env.get("/api/v1/knowledge/spaces"))["items"]
    return {item["name"]: item for item in items}


async def new_note(env: Env, space_id: str, title: str, body: str = "", **fields: Any) -> str:
    response = await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": space_id, "title": title, "body": body, **fields},
        headers=env.headers(write=True),
    )
    assert response.status_code == 201, response.text
    note_id: str = response.json()["id"]
    return note_id


async def delete_note(env: Env, note_id: str) -> None:
    current = await env.get(f"/api/v1/knowledge/notes/{note_id}")
    response = await env.client.delete(
        f"/api/v1/knowledge/notes/{note_id}",
        params={"version": current["version"]},
        headers=env.headers(write=True),
    )
    assert response.status_code == 204, response.text


async def set_updated(env: Env, note_id: str, at: datetime) -> None:
    await run_sql(
        env.database_url,
        "UPDATE workspace.knowledge_notes SET updated_at = $1 WHERE id = $2",
        at,
        uuid.UUID(note_id),
    )


async def bulk_notes(
    database_url: str, workspace_id: uuid.UUID, space_id: uuid.UUID, count: int, prefix: str
) -> None:
    """直接写库造大量笔记（不经接口，免得测试太慢）；修改时间都在很久以前。"""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await conn.execute(
            "INSERT INTO workspace.knowledge_notes"
            " (workspace_id, space_id, title, title_key, created_at, updated_at)"
            " SELECT $1, $2, $3 || g, lower($3 || g), $4, $4 FROM generate_series(1, $5) g",
            workspace_id,
            space_id,
            prefix,
            OLD,
            count,
        )
    finally:
        await conn.close()


async def count_rows(env: Env, table: str) -> int:
    value: int = await run_sql(env.database_url, f"SELECT count(*) FROM workspace.{table}")  # noqa: S608 - 表名是测试里写死的
    return value


async def test_totals_cover_every_visible_note_beyond_graph_and_page_limits(env: Env) -> None:
    project = await env.add_project("工作台改版")
    await env.add_project("旧项目", archived=True)
    await env.add_project("空项目")
    by_name = await spaces(env)
    public = by_name["公共知识库"]["id"]
    project_space = by_name["工作台改版"]["id"]
    archived_space = by_name["旧项目"]["id"]
    empty_space = by_name["空项目"]["id"]
    assert by_name["旧项目"]["archived"] is True

    # 通过接口建的笔记：乙有别名，甲用标题和别名各链一次（两行链接指向同一篇 = 重复边）
    b = await new_note(env, public, "乙", aliases=["第二篇"])
    c = await new_note(env, project_space, "丙")
    d = await new_note(env, archived_space, "丁", "回到 [[公共知识库/乙]]")
    e = await new_note(env, public, "戊")
    a = await new_note(
        env,
        public,
        "甲",
        f"见 [[乙]] 与 [[第二篇]]，[[丙]]，[[戊]]，[[task:{uuid.uuid4()}]]，[[甲]]",
    )
    task = await env.add_task(project["id"], "接入任务")
    f = await new_note(env, public, "己", f"指向 [[甲]] 和 [[task:{task['id']}]]")
    # 删除端点：戊（被链）与己（链出）进回收站，它们的关系不再计入
    await delete_note(env, e)
    await delete_note(env, f)
    # 2100 篇老笔记，超过图谱的 2000 篇上限，也超过任何一页
    await bulk_notes(
        env.database_url, uuid.UUID(env.workspace_id), uuid.UUID(public), 2100, "批量笔记 "
    )
    # 不可见的工作空间：别人的空间里也有笔记和链接，一概不算
    outsider = await add_user(env.database_url, "eve@example.com", "eve password 123456")
    other_ws = await add_workspace(env.database_url, outsider, "别人的空间")
    other_space = await run_sql(
        env.database_url,
        "INSERT INTO workspace.knowledge_spaces (workspace_id, kind, name, agent_access)"
        " VALUES ($1, 'public', '公共知识库', 'read_write') RETURNING id",
        other_ws,
    )
    await bulk_notes(env.database_url, other_ws, other_space, 30, "别人的笔记 ")

    data = await ok(env)
    summary = data["summary"]
    # 可见未删除：甲乙丙丁 + 2100 篇；4 个知识库（公共、工作台改版、归档的旧项目、空项目）
    assert summary["note_count"] == 2104
    assert summary["space_count"] == 4
    # 甲→乙（两行只算一次）、甲→丙、丁→乙；甲→戊、己→甲（删除端点）、资源链接、自己链自己都不算
    assert summary["link_count"] == 3
    assert summary["updated_note_count"] == 0
    graph = await env.get("/api/v1/knowledge/graph")
    assert graph["truncated"] is True and graph["total_notes"] == 2104
    assert len([n for n in graph["nodes"] if n["kind"] == "note"]) == 2000

    # 最近更新：按 updated_at、id 倒序，每篇一次，不带正文
    recent = data["recent_notes"]
    assert len(recent) == 6
    assert [n["title"] for n in recent[:4]] == ["甲", "丁", "丙", "乙"]
    assert all("body" not in n for n in recent)
    assert recent[0]["space_name"] == "公共知识库" and recent[0]["updated_by"]["kind"] == "user"
    assert len({n["id"] for n in recent}) == len(recent)
    batch = recent[4:]
    assert [n["id"] for n in batch] == sorted((n["id"] for n in batch), reverse=True)
    many = await ok(env, recent_limit=20)
    assert len(many["recent_notes"]) == 20
    assert len({n["id"] for n in many["recent_notes"]}) == 20
    assert datetime.fromisoformat(data["generated_at"]).tzinfo is not None

    # 选一个知识库：空库是 1 个库、0 篇；归档的库照常统计；跨库的关联两边都算
    empty = await ok(env, space_id=empty_space)
    assert empty["summary"] == {
        "note_count": 0,
        "space_count": 1,
        "link_count": 0,
        "updated_note_count": 0,
    }
    assert empty["recent_notes"] == []
    archived = await ok(env, space_id=archived_space)
    assert archived["summary"]["note_count"] == 1 and archived["summary"]["link_count"] == 1
    assert [n["id"] for n in archived["recent_notes"]] == [d]
    in_project = await ok(env, space_id=project_space)
    assert in_project["summary"]["note_count"] == 1
    assert in_project["summary"]["link_count"] == 1  # 甲→丙：至少一端在这个库
    in_public = await ok(env, space_id=public)
    assert in_public["summary"]["note_count"] == 2102
    assert in_public["summary"]["link_count"] == 3
    assert {n["id"] for n in in_public["recent_notes"][:2]} == {a, b}
    assert c not in {n["id"] for n in in_public["recent_notes"]}

    # 别的工作空间的知识库、不存在的知识库：404
    assert (await overview(env, space_id=str(other_space))).status_code == 404
    assert (await overview(env, space_id=str(uuid.uuid4()))).status_code == 404


async def test_week_window_counts_distinct_notes_with_exact_bounds(env: Env) -> None:
    public = (await spaces(env))["公共知识库"]["id"]
    edited = await new_note(env, public, "改了三次")
    for version in (1, 2, 3):
        response = await env.client.patch(
            f"/api/v1/knowledge/notes/{edited}",
            json={"version": version, "body": f"第 {version + 1} 版"},
            headers=env.headers(write=True),
        )
        assert response.status_code == 200, response.text
    at_start = await new_note(env, public, "周一零点")
    at_end = await new_note(env, public, "下周一零点")
    before = await new_note(env, public, "上周日")
    inside = await new_note(env, public, "周三")
    await set_updated(env, edited, WEEK_START + timedelta(days=1))
    await set_updated(env, at_start, WEEK_START)
    await set_updated(env, at_end, WEEK_END)
    await set_updated(env, before, WEEK_START - timedelta(microseconds=1))
    await set_updated(env, inside, WEEK_START + timedelta(days=2))

    data = await ok(env)
    # 下界含、上界不含；改了三次的笔记只算一次
    assert data["summary"]["updated_note_count"] == 3
    assert data["summary"]["note_count"] == 5
    # 时间窗不截掉总数和最近更新
    assert [n["title"] for n in data["recent_notes"]] == [
        "下周一零点",
        "周三",
        "改了三次",
        "周一零点",
        "上周日",
    ]
    # 同一时刻的不同时区写法结果相同
    shanghai = await ok(
        env,
        updated_after="2026-06-01T08:00:00+08:00",
        updated_before="2026-06-08T08:00:00+08:00",
    )
    assert shanghai["summary"]["updated_note_count"] == 3

    # 参数校验：缺少、不带时区、先后颠倒或相等、recent_limit 越界都是 422
    assert (await env.client.get(OVERVIEW, headers=env.headers())).status_code == 422
    naive = await overview(env, updated_after="2026-06-01T00:00:00")
    assert naive.status_code == 422, naive.text
    assert (await overview(env, updated_before="2026-06-08T00:00:00")).status_code == 422
    reversed_window = await overview(
        env, updated_after=WEEK_END.isoformat(), updated_before=WEEK_START.isoformat()
    )
    assert reversed_window.status_code == 422
    assert reversed_window.json()["error"]["code"] == "VALIDATION_FAILED"
    assert (
        await overview(
            env, updated_after=WEEK_START.isoformat(), updated_before=WEEK_START.isoformat()
        )
    ).status_code == 422
    assert (await overview(env, recent_limit=0)).status_code == 422
    assert (await overview(env, recent_limit=21)).status_code == 422
    assert (await overview(env, space_id="not-a-uuid")).status_code == 422


async def test_viewer_reads_without_side_effects_and_workspaces_are_isolated(env: Env) -> None:
    public = (await spaces(env))["公共知识库"]["id"]
    first = await new_note(env, public, "值班手册", "见 [[发布流程]]")
    await new_note(env, public, "发布流程")
    tables = ("knowledge_note_revisions", "audit_logs", "knowledge_read_stats", "knowledge_notes")
    note_versions = await run_sql(env.database_url, VERSIONS)

    async with env.member("viewer@example.com", "viewer") as viewer:
        # 登录本身会记审计：登录之后再数
        counts = {table: await count_rows(env, table) for table in tables}
        response = await overview(env, client=viewer)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["summary"]["note_count"] == 2 and data["summary"]["link_count"] == 1
        assert data["summary"]["space_count"] == 1
        assert {n["id"] for n in data["recent_notes"]} >= {first}
        assert (await overview(env, space_id=public)).status_code == 200
        # 读取不产生版本、审计、阅读用量，也不改笔记
        assert {table: await count_rows(env, table) for table in tables} == counts
    assert await run_sql(env.database_url, VERSIONS) == note_versions

    # 不是成员的人看不到这个工作空间；它自己的工作空间也看不到这里的笔记
    outsider = await add_user(env.database_url, "eve@example.com", "eve password 123456")
    other_ws = await add_workspace(env.database_url, outsider, "别人的空间")
    async with env.make_client() as eve:
        login = await eve.post(
            "/api/v1/auth/login",
            json={"email": "eve@example.com", "password": "eve password 123456"},
        )
        assert login.status_code == 200, login.text
        foreign = await eve.get(
            OVERVIEW, params=window(), headers={"X-Workspace-Id": env.workspace_id, **csrf(eve)}
        )
        assert foreign.status_code == 404
        own = await eve.get(
            OVERVIEW, params=window(), headers={"X-Workspace-Id": str(other_ws), **csrf(eve)}
        )
        assert own.status_code == 200, own.text
        assert own.json()["summary"]["note_count"] == 0
        assert own.json()["recent_notes"] == []
        foreign_space = await eve.get(
            OVERVIEW,
            params={**window(), "space_id": public},
            headers={"X-Workspace-Id": str(other_ws), **csrf(eve)},
        )
        assert foreign_space.status_code == 404
