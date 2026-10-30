"""本地日历（Sprint 4 契约第 5 节）：定时 / 全天事件、区间查询、乐观并发、权限与工作空间隔离。"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.helpers import BOB, OWNER, ClientFactory, add_member, add_user, csrf, run_sql

pytestmark = pytest.mark.integration

TIMED = {
    "title": "评审",
    "timezone": "Asia/Shanghai",
    "start_at": "2026-11-02T10:00:00+08:00",
    "end_at": "2026-11-02T11:30:00+08:00",
    "location": "三楼会议室",
}
ALL_DAY = {
    "title": "出差",
    "timezone": "Asia/Shanghai",
    "all_day": True,
    "start_date": "2026-11-03",
    "end_date": "2026-11-05",
}
WEEK = {"start": "2026-11-01T00:00:00+08:00", "end": "2026-11-08T00:00:00+08:00"}


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def create(self, body: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/calendar/events", json=body, headers=self.headers(write=True)
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def events(self, **params: Any) -> dict[str, Any]:
        response = await self.client.get(
            "/api/v1/calendar/events", params={**WEEK, **params}, headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client() as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


async def test_timed_and_all_day_events_are_stored_in_utc(env: Env) -> None:
    timed = await env.create(TIMED)
    assert timed["start_at"] == "2026-11-02T02:00:00Z"
    assert timed["end_at"] == "2026-11-02T03:30:00Z"
    assert timed["start_date"] is None and timed["all_day"] is False
    all_day = await env.create(ALL_DAY)
    # 全天事件按 timezone 当地 0 点换算：上海 11 月 3 日 0 点 = UTC 11 月 2 日 16 点
    assert all_day["start_at"] == "2026-11-02T16:00:00Z"
    assert all_day["end_at"] == "2026-11-04T16:00:00Z"
    assert (all_day["start_date"], all_day["end_date"]) == ("2026-11-03", "2026-11-05")
    listed = await env.events()
    assert [e["title"] for e in listed["items"]] == ["评审", "出差"]


async def test_range_returns_overlapping_events_only(env: Env) -> None:
    await env.create(TIMED)
    await env.create({**TIMED, "title": "跨天", "start_at": "2026-10-31T23:00:00+08:00"})
    await env.create(
        {
            **TIMED,
            "title": "下周",
            "start_at": "2026-11-09T10:00:00+08:00",
            "end_at": "2026-11-09T11:00:00+08:00",
        }
    )
    titles = [e["title"] for e in (await env.events())["items"]]
    assert titles == ["跨天", "评审"]  # 跨天的事件结束在区间内，同样返回
    edge = await env.events(start="2026-11-02T11:30:00+08:00", end="2026-11-02T12:00:00+08:00")
    assert edge["items"] == []  # 结束时间不含
    page = await env.events(limit=1)
    assert len(page["items"]) == 1 and page["next_cursor"]
    rest = await env.events(limit=1, cursor=page["next_cursor"])
    assert [e["title"] for e in rest["items"]] == ["评审"]


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-08T00:00:00+08:00", "end": "2026-11-01T00:00:00+08:00"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
        {"start": "2026-11-01T00:00:00", "end": "2026-11-02T00:00:00"},
    ],
)
async def test_invalid_ranges_are_rejected(env: Env, params: dict[str, str]) -> None:
    response = await env.client.get("/api/v1/calendar/events", params=params, headers=env.headers())
    assert response.status_code == 422


async def test_update_merges_and_revalidates(env: Env) -> None:
    event = await env.create(TIMED)
    path = f"/api/v1/calendar/events/{event['id']}"
    moved = await env.client.patch(
        path,
        json={"version": event["version"], "end_at": "2026-11-02T12:00:00+08:00"},
        headers=env.headers(write=True),
    )
    assert moved.status_code == 200
    assert moved.json()["start_at"] == event["start_at"]  # 没改的字段沿用
    assert moved.json()["end_at"] == "2026-11-02T04:00:00Z"
    backwards = await env.client.patch(
        path,
        json={"version": moved.json()["version"], "end_at": "2026-11-02T09:00:00+08:00"},
        headers=env.headers(write=True),
    )
    assert backwards.status_code == 422  # 合并后结束早于开始
    wrong_kind = await env.client.patch(
        path,
        json={"version": moved.json()["version"], "start_date": "2026-11-02"},
        headers=env.headers(write=True),
    )
    assert wrong_kind.status_code == 422  # 定时事件不能只填日期
    to_all_day = await env.client.patch(
        path,
        json={
            "version": moved.json()["version"],
            "all_day": True,
            "start_date": "2026-11-02",
            "end_date": "2026-11-03",
            "timezone": "UTC",
        },
        headers=env.headers(write=True),
    )
    assert to_all_day.status_code == 200
    data = to_all_day.json()
    assert data["all_day"] is True
    assert data["start_at"] == "2026-11-02T00:00:00Z"
    stale = await env.client.patch(
        path, json={"version": event["version"], "title": "旧"}, headers=env.headers(write=True)
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    retimezoned = await env.client.patch(
        path,
        json={"version": data["version"], "timezone": "Asia/Shanghai"},
        headers=env.headers(write=True),
    )
    assert retimezoned.json()["start_at"] == "2026-11-01T16:00:00Z"  # 全天事件随时区重新换算


async def test_delete_with_version(env: Env) -> None:
    event = await env.create(TIMED)
    path = f"/api/v1/calendar/events/{event['id']}"
    stale = await env.client.delete(path, params={"version": 99}, headers=env.headers(write=True))
    assert stale.status_code == 409
    gone = await env.client.delete(
        path, params={"version": event["version"]}, headers=env.headers(write=True)
    )
    assert gone.status_code == 204
    again = await env.client.delete(
        path, params={"version": event["version"]}, headers=env.headers(write=True)
    )
    assert again.status_code == 404
    audit = await run_sql(
        env.database_url,
        "SELECT string_agg(metadata_redacted_json::text, ' ') FROM workspace.audit_logs"
        " WHERE action LIKE 'calendar_event.%'",
    )
    assert "评审" not in audit and "会议室" not in audit


async def test_viewers_read_and_other_workspaces_see_nothing(
    env: Env, make_client: ClientFactory
) -> None:
    event = await env.create(TIMED)
    bob_id = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), bob_id, "viewer")
    async with make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        read = {"X-Workspace-Id": env.workspace_id}
        listed = await bob.get("/api/v1/calendar/events", params=WEEK, headers=read)
        assert [e["id"] for e in listed.json()["items"]] == [event["id"]]
        write = {**read, **csrf(bob)}
        assert (
            await bob.post("/api/v1/calendar/events", json=TIMED, headers=write)
        ).status_code == 403
        assert (
            await bob.delete(
                f"/api/v1/calendar/events/{event['id']}", params={"version": 1}, headers=write
            )
        ).status_code == 403
    other = await run_sql(
        env.database_url,
        "INSERT INTO workspace.workspaces (name, owner_id)"
        " SELECT '另一个', owner_id FROM workspace.workspaces WHERE id = $1 RETURNING id",
        uuid.UUID(env.workspace_id),
    )
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.workspace_members (workspace_id, user_id, role)"
        " SELECT $1, owner_id, 'owner' FROM workspace.workspaces WHERE id = $2",
        other,
        uuid.UUID(env.workspace_id),
    )
    elsewhere = {"X-Workspace-Id": str(other), **csrf(env.client)}
    assert (await env.client.get("/api/v1/calendar/events", params=WEEK, headers=elsewhere)).json()[
        "items"
    ] == []
    patched = await env.client.patch(
        f"/api/v1/calendar/events/{event['id']}",
        json={"version": event["version"], "title": "x"},
        headers=elsewhere,
    )
    assert patched.status_code == 404
