"""AI 审批读侧：汇总、可见筛选项与到期排序（T-0190，计划 A1）。

契约见 docs/v2/ai-admin-redesign/docs/superpowers/specs/2026-11-09-ai-admin-contract.md 第 2 节。
真实 PostgreSQL / Redis；记录直接写进 ai_tool_executions，服务端时钟固定在 now
（替换 ai_tools._now）。

覆盖：
- 汇总按 _visible 做 SQL 聚合：超过一页、多个实例与登记人；owner / admin 看全空间，
  member / viewer 只算自己的；其他工作空间的统计与筛选项都不泄漏；
  原列表的 pending_count 收紧为可见范围；
- 筛选项只按可见范围 + period，不被已选条件锁死；
  被删实例的记录保留历史名称、不进选项、不串到别的实例；
- 时间边界：expires_at = now 不算待审批，now + 180 秒算即将到期，now + 181 秒不算；
  高危是待审批的子集；failed / unknown 算异常，rejected / expired / denied 不算；
  24h / 7d / all 按 created_at 的边界；
- 读工具、知识库、直接执行的工具不会变成待审批；汇总不写数据库、不投递作业、不触发过期；
- 列表：period、sort=expires_asc（空值最后、同分稳定、翻页不重不漏）；
  游标绑定空间、用户、筛选与排序，换条件 422；旧游标与旧的默认调用不变。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
import httpx
import pytest

from app.domain import ai_tools
from app.domain.ai_tools import AiToolService
from app.models import AgentInstance
from app.schemas.ai_tools import APPROVAL_TTL, TOOLS
from tests.helpers import ClientFactory, add_workspace, asyncpg_dsn, run_sql
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration

SUMMARY = "/api/v1/ai/tool-executions/summary"
LIST = "/api/v1/ai/tool-executions"
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)

INSERT = (
    "INSERT INTO workspace.ai_tool_executions (workspace_id, instance_id, instance_name,"
    " acting_user_id, tool_name, risk_level, status, summary, arguments_hash, expires_at,"
    " created_at)"
    " SELECT $1::uuid, $2::uuid, $3::text, $4::uuid, $5::text, $6::text, $7::text,"
    " '测试 #' || g, gen_random_uuid()::text, $8::timestamptz, $9::timestamptz"
    " FROM generate_series(1, $10::int) AS g RETURNING id"
)


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """固定服务端时钟（ai_tools._now）；种子数据的时间都相对它来写。"""
    fixed = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(ai_tools, "_now", lambda: fixed)
    return fixed


@pytest.fixture
async def env(
    make_client: ClientFactory, migrated_database_url: str, now: datetime
) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


async def instance_in(env: Env, name: str, workspace_id: str | None = None) -> str:
    response = await env.client.post(
        "/api/v1/agents/instances",
        json={"name": name, "network_mode": "intranet", "scope": "read_write"},
        headers={**env.headers(write=True), "X-Workspace-Id": workspace_id or env.workspace_id},
    )
    assert response.status_code == 201, response.text
    instance_id: str = response.json()["id"]
    return instance_id


async def user_id(env: Env, email: str) -> uuid.UUID:
    found: uuid.UUID = await run_sql(
        env.database_url, "SELECT id FROM workspace.users WHERE email = $1", email
    )
    return found


async def seed(
    env: Env,
    n: int = 1,
    *,
    at: datetime,
    status: str = "pending_approval",
    expires: datetime | Literal["ttl"] | None = "ttl",
    risk: str = "write",
    tool: str = "create_bookmark",
    instance: str | None = None,
    name: str = "alpha",
    user: uuid.UUID | Literal["owner"] | None = "owner",
    workspace: str | None = None,
) -> list[uuid.UUID]:
    """直接写 n 条调用记录（created_at = at；待审批默认 15 分钟后到期）。"""
    if expires == "ttl":
        expires = (
            at + APPROVAL_TTL if status in ("pending_approval", "rejected", "expired") else None
        )
    conn = await asyncpg.connect(asyncpg_dsn(env.database_url))
    try:
        rows = await conn.fetch(
            INSERT,
            uuid.UUID(workspace or env.workspace_id),
            uuid.UUID(instance) if instance else None,
            name,
            env.owner_id if user == "owner" else user,
            tool,
            risk,
            status,
            expires,
            at,
            n,
        )
    finally:
        await conn.close()
    return [row["id"] for row in rows]


async def stored(env: Env) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(asyncpg_dsn(env.database_url))
    try:
        return list(
            await conn.fetch(
                "SELECT id, status, version, expires_at, created_at"
                " FROM workspace.ai_tool_executions WHERE workspace_id = $1",
                uuid.UUID(env.workspace_id),
            )
        )
    finally:
        await conn.close()


async def get_summary(
    client: httpx.AsyncClient, workspace_id: str, **params: Any
) -> dict[str, Any]:
    response = await client.get(SUMMARY, params=params, headers={"X-Workspace-Id": workspace_id})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def get_list(client: httpx.AsyncClient, workspace_id: str, **params: Any) -> httpx.Response:
    return await client.get(LIST, params=params, headers={"X-Workspace-Id": workspace_id})


def counts(body: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        body["pending_count"],
        body["expiring_count"],
        body["critical_pending_count"],
        body["exception_count"],
    )


def options(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(item["id"], item["name"]) for item in body["instances"]]


async def every_page(env: Env, **params: Any) -> list[str]:
    """按 next_cursor 翻完所有页，返回记录 ID 的顺序。"""
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await get_list(env.client, env.workspace_id, **query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            return ids
    raise AssertionError("翻页没有结束")


# ---------------------------------------------------------------- 可见范围


async def test_summary_counts_the_visible_scope_across_pages(env: Env, now: datetime) -> None:
    alpha = await instance_in(env, "alpha")
    beta = await instance_in(env, "beta")
    other = str(await add_workspace(env.database_url, env.owner_id, "另一个工作空间"))
    gamma = await instance_in(env, "gamma", other)
    async with (
        env.member("admin@example.com", "admin") as admin,
        env.member("member@example.com", "member") as member,
        env.member("viewer@example.com", "viewer") as viewer,
    ):
        member_id = await user_id(env, "member@example.com")
        viewer_id = await user_id(env, "viewer@example.com")
        ago = now - MINUTE
        # owner 登记的 alpha：55 条待审批（超过列表一页 50 条）与 3 条失败
        await seed(env, 55, at=ago, instance=alpha)
        await seed(env, 3, at=ago, status="failed", instance=alpha, tool="create_task")
        # member 登记的 beta：4 条待审批（1 条高危）与 2 条结果未知
        await seed(env, 3, at=ago, instance=beta, name="beta", user=member_id, tool="update_task")
        await seed(
            env,
            at=ago,
            instance=beta,
            name="beta",
            user=member_id,
            risk="critical",
            tool="delete_task",
        )
        await seed(
            env,
            2,
            at=ago,
            status="unknown",
            instance=beta,
            name="beta",
            user=member_id,
            tool="update_task",
        )
        # viewer 身份执行的记录（以 alpha 的名义）
        await seed(env, at=ago, instance=alpha, user=viewer_id)
        await seed(
            env,
            at=ago,
            status="failed",
            instance=alpha,
            user=viewer_id,
            tool="create_calendar_event",
        )
        # 另一个工作空间
        await seed(
            env,
            7,
            at=ago,
            instance=gamma,
            name="gamma",
            tool="mark_notification_read",
            workspace=other,
        )
        await seed(
            env,
            2,
            at=ago,
            status="failed",
            instance=gamma,
            name="gamma",
            tool="mark_notification_read",
            workspace=other,
        )

        everything = ["create_bookmark", "create_calendar_event", "create_task", "delete_task"]
        everything += ["update_task"]
        for client in (env.client, admin):
            body = await get_summary(client, env.workspace_id)
            assert counts(body) == (60, 0, 1, 6)
            assert options(body) == [(alpha, "alpha"), (beta, "beta")]
            assert body["tools"] == everything
            # 汇总不是第一页的数量；列表的 pending_count 也是全空间
            listed = await get_list(client, env.workspace_id)
            assert len(listed.json()["items"]) == 50 and listed.json()["next_cursor"]
            assert listed.json()["pending_count"] == 60

        mine = await get_summary(member, env.workspace_id)
        assert counts(mine) == (4, 0, 1, 2)
        assert options(mine) == [(beta, "beta")] and mine["tools"] == ["delete_task", "update_task"]
        listed = await get_list(member, env.workspace_id)
        assert len(listed.json()["items"]) == 6
        # 回归：pending_count 只算自己可见的（以前是全空间的 60）
        assert listed.json()["pending_count"] == 4

        seen = await get_summary(viewer, env.workspace_id)
        assert counts(seen) == (1, 0, 0, 1)
        assert options(seen) == [(alpha, "alpha")]
        assert seen["tools"] == ["create_bookmark", "create_calendar_event"]
        assert (await get_list(viewer, env.workspace_id)).json()["pending_count"] == 1

        elsewhere = await get_summary(env.client, other)
        assert counts(elsewhere) == (7, 0, 0, 2)
        assert options(elsewhere) == [(gamma, "gamma")]
        assert elsewhere["tools"] == ["mark_notification_read"]
        assert (await get_list(env.client, other)).json()["pending_count"] == 7
        # 不是那个空间的成员：不能借 X-Workspace-Id 读到
        denied = await member.get(SUMMARY, headers={"X-Workspace-Id": other})
        assert denied.status_code in (403, 404)


async def test_deleted_instances_keep_their_name_and_never_become_an_option(
    env: Env, now: datetime
) -> None:
    alpha = await instance_in(env, "alpha")
    beta = await instance_in(env, "beta")
    await seed(env, at=now - 10 * MINUTE, status="succeeded", instance=alpha)
    await seed(env, at=now - 5 * MINUTE, status="succeeded", instance=alpha, name="alpha 改名")
    await seed(env, at=now - 3 * MINUTE, status="failed", instance=beta, name="beta")
    await run_sql(
        env.database_url, "DELETE FROM workspace.agent_instances WHERE id = $1", uuid.UUID(beta)
    )
    # 另一个早已删除的实例恰好也叫 alpha，而且是最新的记录：不能被当成 alpha 的名称
    await seed(env, at=now - MINUTE, status="failed", instance=None, name="alpha")

    body = await get_summary(env.client, env.workspace_id)
    assert options(body) == [(alpha, "alpha 改名")]
    assert counts(body) == (0, 0, 0, 2)  # 被删实例的记录仍在“全部”的统计里
    items = (await get_list(env.client, env.workspace_id)).json()["items"]
    assert [(i["instance_id"], i["instance_name"]) for i in items] == [
        (None, "alpha"),
        (None, "beta"),
        (alpha, "alpha 改名"),
        (alpha, "alpha"),
    ]
    only = (await get_list(env.client, env.workspace_id, instance_id=alpha)).json()["items"]
    assert {i["instance_id"] for i in only} == {alpha} and len(only) == 2
    narrowed = await get_summary(env.client, env.workspace_id, instance_id=alpha)
    assert counts(narrowed) == (0, 0, 0, 0) and options(narrowed) == [(alpha, "alpha 改名")]


# ---------------------------------------------------------------- 时间与分类


async def test_boundaries_use_the_server_clock_and_summary_writes_nothing(
    env: Env, now: datetime
) -> None:
    def pending(expires: datetime, **fields: Any) -> Any:
        return seed(env, at=expires - APPROVAL_TTL, expires=expires, **fields)

    [edge] = await pending(now)  # 刚好到期：不算待审批
    await pending(now + 180 * SECOND)  # 即将到期（含边界）
    await pending(now + 181 * SECOND)  # 待审批，但不算即将到期
    await pending(now + MINUTE, risk="critical", tool="delete_task")  # 高危且即将到期
    [late] = await pending(now - SECOND, risk="critical", tool="delete_task")  # 已到期、还没清理
    hour_ago = now - timedelta(hours=1)
    for status in ("failed", "unknown", "rejected", "expired", "denied", "executing", "succeeded"):
        await seed(env, at=hour_ago, status=status, tool="create_task")

    before = await stored(env)
    notes = "SELECT count(*) FROM workspace.notifications WHERE workspace_id = $1"
    notes_before = await run_sql(env.database_url, notes, uuid.UUID(env.workspace_id))
    jobs_before = list(env.queue.jobs)

    body = await get_summary(env.client, env.workspace_id)
    assert counts(body) == (3, 2, 1, 2)
    assert body["critical_pending_count"] <= body["pending_count"]
    assert body["expiring_count"] <= body["pending_count"]
    assert datetime.fromisoformat(body["as_of"]) == now
    assert (body["period"], body["expiring_window_seconds"]) == ("all", 180)
    again = await get_summary(env.client, env.workspace_id, period="24h")
    assert counts(again) == (3, 2, 1, 2)

    # 汇总不写库：到期的记录仍停在 pending_approval、版本不变，没有通知、没有投递作业
    assert {r["id"]: tuple(r) for r in await stored(env)} == {r["id"]: tuple(r) for r in before}
    assert await run_sql(env.database_url, notes, uuid.UUID(env.workspace_id)) == notes_before
    assert env.queue.jobs == jobs_before
    states = {row["id"]: (row["status"], row["version"]) for row in before}
    assert states[edge] == states[late] == ("pending_approval", 1)

    # 列表照旧清理过期项；汇总前后口径一致
    await get_list(env.client, env.workspace_id)
    states = {row["id"]: (row["status"], row["version"]) for row in await stored(env)}
    assert states[edge] == states[late] == ("expired", 2)
    assert counts(await get_summary(env.client, env.workspace_id)) == (3, 2, 1, 2)


async def test_period_filters_calls_by_created_at(env: Env, now: datetime) -> None:
    alpha = await instance_in(env, "alpha")
    beta = await instance_in(env, "beta")
    day, week = timedelta(hours=24), timedelta(days=7)
    await seed(env, at=now - day, status="failed", instance=alpha)  # 24h 内（含边界）
    await seed(env, at=now - day - SECOND, status="failed", instance=alpha, tool="create_task")
    await seed(env, at=now - week, status="failed", instance=alpha, tool="update_task")  # 7d 内
    # 只在“全部”里
    await seed(
        env,
        at=now - week - SECOND,
        status="unknown",
        instance=beta,
        name="beta",
        tool="delete_task",
    )
    # 25 小时前发起、还没到期的待审批（人为数据）：period 同样作用于待审批的统计
    await seed(
        env,
        at=now - day - timedelta(hours=1),
        expires=now + 10 * MINUTE,
        tool="create_calendar_event",
    )

    recent = await get_summary(env.client, env.workspace_id, period="24h")
    assert counts(recent) == (0, 0, 0, 1) and recent["period"] == "24h"
    assert recent["tools"] == ["create_bookmark"] and options(recent) == [(alpha, "alpha")]
    weekly = await get_summary(env.client, env.workspace_id, period="7d")
    assert counts(weekly) == (1, 0, 0, 3)
    assert weekly["tools"] == [
        "create_bookmark",
        "create_calendar_event",
        "create_task",
        "update_task",
    ]
    assert options(weekly) == [(alpha, "alpha")]
    everything = await get_summary(env.client, env.workspace_id, period="all")
    assert counts(everything) == (1, 0, 0, 4)
    assert len(everything["tools"]) == 5 and options(everything) == [
        (alpha, "alpha"),
        (beta, "beta"),
    ]
    assert counts(await get_summary(env.client, env.workspace_id)) == counts(everything)

    for period, size in (("24h", 1), ("7d", 4), ("all", 5)):
        listed = await get_list(env.client, env.workspace_id, period=period)
        assert listed.status_code == 200 and len(listed.json()["items"]) == size, period
    assert len((await get_list(env.client, env.workspace_id)).json()["items"]) == 5
    for path in (SUMMARY, LIST):
        bad = await env.client.get(path, params={"period": "30d"}, headers=env.headers())
        assert bad.status_code == 422, path
    bad = await get_list(env.client, env.workspace_id, sort="newest")
    assert bad.status_code == 422


async def test_filters_narrow_the_counts_but_not_the_options(env: Env, now: datetime) -> None:
    alpha = await instance_in(env, "alpha")
    beta = await instance_in(env, "beta")
    ago = now - MINUTE
    await seed(env, at=ago, instance=alpha)
    await seed(env, at=ago, instance=beta, name="beta", risk="critical", tool="delete_task")
    await seed(env, at=ago, status="failed", instance=beta, name="beta", tool="create_task")
    full = await get_summary(env.client, env.workspace_id)
    assert counts(full) == (2, 0, 1, 1)
    cases: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = [
        ({"instance_id": beta}, (1, 0, 1, 1)),
        ({"risk_level": "critical"}, (1, 0, 1, 0)),
        ({"risk_level": ["write", "critical"]}, (2, 0, 1, 1)),
        ({"tool_name": "create_task"}, (0, 0, 0, 1)),
        ({"instance_id": str(uuid.uuid4())}, (0, 0, 0, 0)),
        ({"status": "failed"}, (2, 0, 1, 1)),  # 汇总不接收 status
    ]
    for params, expected in cases:
        body = await get_summary(env.client, env.workspace_id, **params)
        assert counts(body) == expected, params
        assert options(body) == options(full) == [(alpha, "alpha"), (beta, "beta")], params
        assert body["tools"] == full["tools"] == ["create_bookmark", "create_task", "delete_task"]


async def test_read_direct_and_knowledge_tools_never_wait_for_approval(
    env: Env, now: datetime
) -> None:
    instance_id = await instance_in(env, "alpha")
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        service = AiToolService(db, app.state.settings, app.state.connectors, app.state.redis)
        instance = await db.get(AgentInstance, uuid.UUID(instance_id))
        assert instance is not None
        requester = await service.requester(instance)
        task = {"task_id": str(uuid.uuid4())}
        await service.record_read(requester, TOOLS["list_tasks"], {}, ok=True)
        await service.record_read(
            requester, TOOLS["get_task"], task, ok=False, error_code="NOT_FOUND"
        )
        await service.record_knowledge(
            requester, TOOLS["knowledge_write"], {"title": "x"}, ok=True, summary="写笔记"
        )
        await service.record_knowledge(
            requester, TOOLS["claim_task"], task, ok=False, summary="领取", error_code="CONFLICT"
        )
    body = await get_summary(env.client, env.workspace_id)
    assert counts(body) == (0, 0, 0, 2)
    assert body["tools"] == ["claim_task", "get_task", "knowledge_write", "list_tasks"]
    pending = await get_list(env.client, env.workspace_id, status="pending_approval")
    assert pending.json()["items"] == [] and pending.json()["pending_count"] == 0
    items = (await get_list(env.client, env.workspace_id, sort="expires_asc")).json()["items"]
    assert len(items) == 4 and {i["expires_at"] for i in items} == {None}


# ---------------------------------------------------------------- 列表：排序与游标


async def test_expires_asc_puts_nulls_last_and_pages_stably(env: Env, now: datetime) -> None:
    alpha = await instance_in(env, "alpha")
    base = now - 5 * MINUTE
    await seed(env, at=base, expires=now + 2 * MINUTE, instance=alpha)
    await seed(env, 3, at=base - MINUTE, expires=now + 5 * MINUTE, instance=alpha)  # 完全同分
    await seed(env, at=base - 2 * MINUTE, expires=now + 5 * MINUTE, instance=alpha)
    await seed(env, at=base, expires=now + 10 * MINUTE, instance=alpha)
    await seed(env, at=base - 3 * MINUTE, status="rejected", expires=now + MINUTE)
    for minutes in (30, 20, 40):
        await seed(env, at=now - minutes * MINUTE, status="succeeded", expires=None)
    await seed(env, 2, at=now - 50 * MINUTE, status="failed", expires=None)  # 同分的空值

    rows = await stored(env)
    by_expiry = sorted(
        rows,
        key=lambda r: (r["expires_at"] is None, r["expires_at"] or now, r["created_at"], r["id"]),
    )
    expected = [str(r["id"]) for r in by_expiry]
    assert await every_page(env, sort="expires_asc", limit=2) == expected
    assert await every_page(env, sort="expires_asc", limit=100) == expected
    waiting = [str(r["id"]) for r in by_expiry if r["status"] == "pending_approval"]
    assert len(waiting) == 6
    assert await every_page(env, sort="expires_asc", status="pending_approval", limit=4) == waiting

    # 旧的默认调用：创建时间倒序，响应形状不变
    newest = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)
    assert await every_page(env, limit=3) == [str(r["id"]) for r in newest]
    assert await every_page(env, sort="created_desc", limit=3) == [str(r["id"]) for r in newest]
    first = (await get_list(env.client, env.workspace_id, limit=3)).json()
    assert set(first) == {"items", "next_cursor", "total_estimate", "pending_count"}
    assert first["total_estimate"] is None and first["pending_count"] == 6


async def test_cursors_are_bound_to_the_conditions_they_came_from(env: Env, now: datetime) -> None:
    alpha = await instance_in(env, "alpha")
    other = str(await add_workspace(env.database_url, env.owner_id, "另一个工作空间"))
    for minutes in range(1, 7):
        await seed(
            env,
            at=now - minutes * MINUTE,
            status="failed" if minutes % 2 else "pending_approval",
            instance=alpha,
        )
    first = (await get_list(env.client, env.workspace_id, limit=2)).json()
    cursor = first["next_cursor"]
    assert cursor
    same = await get_list(env.client, env.workspace_id, limit=2, cursor=cursor)
    assert same.status_code == 200, same.text
    changed: list[dict[str, Any]] = [
        {"status": "failed"},
        {"risk_level": "critical"},
        {"tool_name": "create_task"},
        {"instance_id": alpha},
        {"period": "7d"},
        {"sort": "expires_asc"},
    ]
    for params in changed:
        response = await get_list(env.client, env.workspace_id, limit=2, cursor=cursor, **params)
        assert response.status_code == 422, params
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["errors"][0]["loc"] == ["query", "cursor"]

    by_expiry = await get_list(env.client, env.workspace_id, limit=2, sort="expires_asc")
    expiry_cursor = by_expiry.json()["next_cursor"]
    assert expiry_cursor
    swapped = await get_list(env.client, env.workspace_id, limit=2, cursor=expiry_cursor)
    assert swapped.status_code == 422
    kept = await get_list(
        env.client, env.workspace_id, limit=2, sort="expires_asc", cursor=expiry_cursor
    )
    assert kept.status_code == 200, kept.text

    # 别的空间、别的用户拿到这个游标也不能接着翻（游标不是授权凭据）
    assert (await get_list(env.client, other, limit=2, cursor=cursor)).status_code == 422
    async with (
        env.member("admin@example.com", "admin") as admin,
        env.member("member@example.com", "member") as member,
    ):
        for client in (admin, member):
            reused = await get_list(client, env.workspace_id, limit=2, cursor=cursor)
            assert reused.status_code == 422

    # 旧格式游标（创建时间|ID）：没有新条件时照旧可用，结果与新游标一致；带新条件 422
    last = first["items"][-1]
    legacy = f"{last['created_at']}|{last['id']}"
    old = await get_list(env.client, env.workspace_id, limit=2, cursor=legacy)
    assert old.status_code == 200, old.text
    assert old.json()["items"] == same.json()["items"]
    for params in ({"sort": "expires_asc"}, {"period": "24h"}):
        response = await get_list(env.client, env.workspace_id, limit=2, cursor=legacy, **params)
        assert response.status_code == 422, params
    garbage = await get_list(env.client, env.workspace_id, cursor="v2.not-base64!")
    assert garbage.status_code == 422
