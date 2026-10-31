"""通知中心与数据失效事件（T-0036，S5-03）：真实 PostgreSQL（已迁移）+ Redis。

覆盖 Sprint 5 契约第 4、5 节：通知只给自己看、按工作空间隔离、source_key 去重、已读 / 全部已读、
首页未读数一致、游标翻页；集成状态变化与服务健康告警产生的通知；写入提交后才发布失效事件，
回滚不发布；SSE 的鉴权、补发、reset、心跳、连接上限与会话失效后停止推送（A16、A18）。
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from app.domain import alerts, events, notifications
from app.schemas.operations import ServiceStatusOut
from tests.helpers import (
    BOB,
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    csrf,
    run_sql,
)

pytestmark = pytest.mark.integration

VIEWER = {"email": "viewer@example.com", "password": "viewer password 123"}


@dataclass
class Space:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str

    @property
    def app(self) -> FastAPI:
        return cast(FastAPI, cast(httpx.ASGITransport, self.client._transport).app)

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def notify_admins(self, key: str = "k1", **overrides: Any) -> int:
        options: dict[str, Any] = {
            "type_": "integration_unauthorized",
            "severity": "error",
            "title": "Vikunja「我的」的授权已失效",
            "body": "请更新 Token。",
            "link": "/settings/integrations",
            "key": key,
            **overrides,
        }
        async with self.app.state.sessionmaker() as db:
            added = await notifications.notify_admins(db, uuid.UUID(self.workspace_id), **options)
            await db.commit()
        await self.app.state.events.drain()
        return added

    async def page(self, **params: Any) -> dict[str, Any]:
        response = await self.client.get(
            "/api/v1/notifications", params=params, headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def stream_entries(self) -> list[tuple[str, set[str]]]:
        raw = await self.app.state.redis.xrange(events.stream_key(uuid.UUID(self.workspace_id)))
        return [(i.decode(), set(f[b"topics"].decode().split(","))) for i, f in raw]


@pytest.fixture
async def space(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Space]:
    async with make_client(sse_max_seconds=1.5, sse_block_seconds=0.2) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Space(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


# ---------------------------------------------------------------- 通知


async def test_notifications_go_to_admins_once_and_are_private(
    space: Space, make_client: ClientFactory
) -> None:
    viewer_id = await add_user(space.database_url, VIEWER["email"], VIEWER["password"])
    await add_member(space.database_url, uuid.UUID(space.workspace_id), viewer_id, "viewer")
    assert await space.notify_admins() == 1  # 只有 owner；viewer 不收管理类通知
    assert await space.notify_admins() == 0  # 同一件事一小时内只通知一次
    data = await space.page()
    assert data["unread_count"] == 1
    [item] = data["items"]
    assert item["type"] == "integration_unauthorized" and item["severity"] == "error"
    assert item["read_at"] is None and item["link"] == "/settings/integrations"
    overview = await space.client.get("/api/v1/dashboard/overview", headers=space.headers())
    assert overview.json()["summary"]["unread_notifications"] == 1

    async with make_client() as viewer:
        login = await viewer.post("/api/v1/auth/login", json=VIEWER)
        assert login.status_code == 200
        headers = {"X-Workspace-Id": space.workspace_id, **csrf(viewer)}
        own = await viewer.get("/api/v1/notifications", headers=headers)
        assert own.json()["items"] == [] and own.json()["unread_count"] == 0
        # 别人的通知 ID：按不存在处理，不报错也不改动
        marked = await viewer.patch(
            "/api/v1/notifications", json={"ids": [item["id"]]}, headers=headers
        )
        assert marked.status_code == 200
        assert marked.json() == {"updated": 0, "unread_count": 0}
    assert (await space.page())["unread_count"] == 1


async def test_notifications_are_scoped_to_the_workspace(space: Space) -> None:
    owner_id = await run_sql(
        space.database_url,
        "SELECT id FROM workspace.users WHERE email = $1",
        OWNER["email"].lower(),
    )
    other = await add_workspace(space.database_url, owner_id, "另一个空间")
    async with space.app.state.sessionmaker() as db:
        await notifications.notify_admins(
            db,
            other,
            type_="sync_failed",
            severity="warning",
            title="另一个空间的通知",
            body="",
            link=None,
            key="x",
        )
        await db.commit()
    assert (await space.page())["items"] == []
    elsewhere = await space.client.get(
        "/api/v1/notifications", headers={"X-Workspace-Id": str(other)}
    )
    assert [n["title"] for n in elsewhere.json()["items"]] == ["另一个空间的通知"]


async def test_mark_read_by_ids_and_all(space: Space) -> None:
    for key in ("a", "b", "c"):
        await space.notify_admins(key=key)
    items = (await space.page())["items"]
    assert len(items) == 3
    first = await space.client.patch(
        "/api/v1/notifications", json={"ids": [items[0]["id"]]}, headers=space.headers(write=True)
    )
    assert first.json() == {"updated": 1, "unread_count": 2}
    again = await space.client.patch(
        "/api/v1/notifications", json={"ids": [items[0]["id"]]}, headers=space.headers(write=True)
    )
    assert again.json() == {"updated": 0, "unread_count": 2}  # 已经是已读的不计
    unread = await space.page(unread_only=True)
    assert {n["id"] for n in unread["items"]} == {items[1]["id"], items[2]["id"]}
    everything = await space.client.patch(
        "/api/v1/notifications", json={"all": True}, headers=space.headers(write=True)
    )
    assert everything.json() == {"updated": 2, "unread_count": 0}
    assert all(n["read_at"] for n in (await space.page())["items"])
    no_csrf = await space.client.patch(
        "/api/v1/notifications", json={"all": True}, headers=space.headers()
    )
    assert no_csrf.status_code == 403


async def test_notification_pages_are_newest_first_with_a_cursor(space: Space) -> None:
    for key in ("1", "2", "3", "4", "5"):
        await space.notify_admins(key=key, title=f"通知 {key}")
    first = await space.page(limit=2)
    assert [n["title"] for n in first["items"]] == ["通知 5", "通知 4"]
    second = await space.page(limit=2, cursor=first["next_cursor"])
    third = await space.page(limit=2, cursor=second["next_cursor"])
    titles = [n["title"] for n in first["items"] + second["items"] + third["items"]]
    assert titles == ["通知 5", "通知 4", "通知 3", "通知 2", "通知 1"]
    assert third["next_cursor"] is None
    bad = await space.client.get(
        "/api/v1/notifications", params={"cursor": "bm90LWEtY3Vyc29y"}, headers=space.headers()
    )
    assert bad.status_code == 422


async def test_integration_status_changes_notify_admins(space: Space) -> None:
    integration_id = await run_sql(
        space.database_url,
        "INSERT INTO workspace.integrations (workspace_id, provider, name, base_url, public_url,"
        " status) VALUES ($1, 'vikunja', '我的', 'http://vikunja:3456', 'http://localhost:3456',"
        " 'active') RETURNING id",
        uuid.UUID(space.workspace_id),
    )
    from app.models import Integration

    async def change(new: str) -> None:
        async with space.app.state.sessionmaker() as db:
            integration = await db.get(Integration, integration_id)
            assert integration is not None
            old = integration.status
            integration.status = new
            await notifications.integration_status_changed(db, integration, old)
            await db.commit()

    await change("unavailable")
    await change("unauthorized")
    await change("active")
    await change("active")  # 没有变化：不通知
    types = [n["type"] for n in (await space.page())["items"]]
    assert types == ["integration_recovered", "integration_unauthorized", "sync_failed"]
    body = json.dumps((await space.page())["items"], ensure_ascii=False)
    assert "vikunja:3456" not in body  # 不含地址


async def test_service_alerts_need_two_failures_and_report_recovery(space: Space) -> None:
    redis = space.app.state.redis
    sessions = space.app.state.sessionmaker

    def status(state: str) -> ServiceStatusOut:
        return ServiceStatusOut(
            id="prometheus",
            name="Prometheus",
            kind="monitoring",
            status=state,
            reason="connect_failed" if state == "down" else None,
            checked_at=None,
        )

    assert await alerts.evaluate(redis, sessions, [status("ok")]) == []
    assert await alerts.evaluate(redis, sessions, [status("down")]) == []  # 偶发一次不算
    assert await alerts.evaluate(redis, sessions, [status("down")]) == ["service_down:prometheus"]
    assert await alerts.evaluate(redis, sessions, [status("down")]) == []  # 持续 down 不重复
    assert await alerts.evaluate(redis, sessions, [status("ok")]) == [
        "service_recovered:prometheus"
    ]
    assert await alerts.evaluate(redis, sessions, [status("not_configured")]) == []
    await space.app.state.events.drain()
    items = (await space.page())["items"]
    assert [n["type"] for n in items] == ["service_recovered", "service_down"]
    assert items[1]["title"] == "Prometheus 不可用" and items[1]["link"] == "/operations"


async def test_purge_keeps_unread_longer(space: Space) -> None:
    for key in ("old-read", "old-unread", "new"):
        await space.notify_admins(key=key, title=key)
    await run_sql(
        space.database_url,
        "UPDATE workspace.notifications SET created_at = now() - interval '100 days',"
        " read_at = CASE WHEN title = 'old-read' THEN now() END WHERE title LIKE 'old-%'",
    )
    async with space.app.state.sessionmaker() as db:
        from datetime import timedelta

        assert await notifications.purge(db, retention=timedelta(days=90)) == 1
        await db.commit()
    assert {n["title"] for n in (await space.page())["items"]} == {"old-unread", "new"}


# ---------------------------------------------------------------- 失效事件


async def test_events_are_published_only_after_commit(space: Space) -> None:
    body = {
        "title": "评审",
        "timezone": "Asia/Shanghai",
        "start_at": "2026-11-02T10:00:00+08:00",
        "end_at": "2026-11-02T11:00:00+08:00",
    }
    created = await space.client.post(
        "/api/v1/calendar/events", json=body, headers=space.headers(write=True)
    )
    assert created.status_code == 201
    await space.app.state.events.drain()
    entries = await space.stream_entries()
    assert entries and {"calendar", "overview"} <= entries[-1][1]
    count = len(entries)

    from app.models import CalendarEvent

    async with space.app.state.sessionmaker() as db:
        event = await db.get(CalendarEvent, uuid.UUID(created.json()["id"]))
        assert event is not None
        event.title = "不会提交"
        await db.flush()
        await db.rollback()
    await space.app.state.events.drain()
    assert len(await space.stream_entries()) == count  # 回滚的改动不发布


# ---------------------------------------------------------------- SSE


def parse_sse(text: str) -> list[dict[str, str]]:
    """把 text/event-stream 拆成事件（忽略心跳注释）。"""
    parsed: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            fields[name] = value.lstrip(" ")
        if fields:
            parsed.append(fields)
    return parsed


async def open_stream(space: Space, **headers: str) -> httpx.Response:
    return await space.client.get(
        "/api/v1/events/stream", params={"workspace_id": space.workspace_id}, headers=headers
    )


async def test_stream_pushes_invalidations_after_connecting(space: Space) -> None:
    async def publish_later() -> None:
        # 等连接真正开始推送（已占用一个连接名额）后再写入，事件一定在它的起点之后
        limiter = space.app.state.stream_limiter
        for _ in range(100):
            if limiter._counts:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)  # 占用名额之后才确定起点（resume_point）
        await space.notify_admins()

    publisher = asyncio.create_task(publish_later())
    response = await open_stream(space)
    await publisher
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    pushed = parse_sse(response.text)
    assert pushed, response.text
    first = pushed[0]
    assert first["event"] == "invalidate" and "-" in first["id"]
    data = json.loads(first["data"])
    assert data["workspace_id"] == space.workspace_id
    assert {"notifications", "overview"} <= set(data["topics"]) and data["reset"] is False
    assert set(data) == {"workspace_id", "topics", "reset", "at"}  # 不带数据本身


async def test_stream_replays_after_last_event_id_and_resets_when_unknown(space: Space) -> None:
    await space.notify_admins(key="a")
    await space.notify_admins(key="b")
    entries = await space.stream_entries()
    first_id = entries[0][0]
    replay = parse_sse((await open_stream(space, **{"Last-Event-ID": first_id})).text)
    assert [e["id"] for e in replay][: len(entries) - 1] == [i for i, _ in entries[1:]]
    assert all(not json.loads(e["data"])["reset"] for e in replay)

    reset = parse_sse((await open_stream(space, **{"Last-Event-ID": "not-an-id"})).text)
    data = json.loads(reset[0]["data"])
    assert data["reset"] is True and set(data["topics"]) == set(events.ALL_TOPICS)
    assert reset[0]["id"] == entries[-1][0]  # 之后从最新的位置续传

    # 早于保留范围的 ID：中间的事件已被裁掉，同样要求重新拉取
    key = events.stream_key(uuid.UUID(space.workspace_id))
    await space.app.state.redis.xtrim(key, maxlen=1, approximate=False)
    trimmed = parse_sse((await open_stream(space, **{"Last-Event-ID": first_id})).text)
    assert json.loads(trimmed[0]["data"])["reset"] is True


async def test_stream_requires_membership_and_limits_connections(
    space: Space, make_client: ClientFactory
) -> None:
    missing = await space.client.get("/api/v1/events/stream")
    assert missing.status_code == 422
    other = await space.client.get(
        "/api/v1/events/stream", params={"workspace_id": str(uuid.uuid4())}
    )
    assert other.status_code == 404
    assert other.headers["content-type"].startswith("application/json")
    async with make_client() as anonymous:
        unauthenticated = await anonymous.get(
            "/api/v1/events/stream", params={"workspace_id": space.workspace_id}
        )
        assert unauthenticated.status_code == 401
    bob_id = await add_user(space.database_url, BOB["email"], BOB["password"])
    assert bob_id  # 不是成员
    limiter = space.app.state.stream_limiter
    owner_id = await run_sql(
        space.database_url,
        "SELECT id FROM workspace.users WHERE email = $1",
        OWNER["email"].lower(),
    )
    for _ in range(limiter.per_user):
        limiter.acquire(owner_id)
    try:
        crowded = await open_stream(space)
        assert crowded.status_code == 429
        assert crowded.headers["Retry-After"] == "30"
    finally:
        for _ in range(limiter.per_user):
            limiter.release(owner_id)


async def test_stream_stops_after_logout(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with make_client(
        sse_max_seconds=5, sse_block_seconds=0.2, sse_recheck_seconds=0.3
    ) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        space = Space(client, setup.json()["workspaces"][0]["id"], migrated_database_url)
        session_token = client.cookies.get("ws_session")
        assert session_token

        async def logout_later() -> None:
            await asyncio.sleep(0.5)
            await run_sql(
                migrated_database_url, "UPDATE workspace.auth_sessions SET revoked_at = now()"
            )

        task = asyncio.create_task(logout_later())
        started = asyncio.get_running_loop().time()
        response = await open_stream(space)
        await task
        elapsed = asyncio.get_running_loop().time() - started
        assert response.status_code == 200
        assert elapsed < 3  # 会话失效后在一次复核内停止，不等到 5 秒的上限
