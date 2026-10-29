"""工作空间权限、设置与审计日志的接口集成测试（T-0011）：真实 PostgreSQL（已迁移）+ Redis。

覆盖 S1-03（角色策略、防 IDOR：两个用户、两个工作空间）、S1-04（个人资料与工作空间设置）、
S1-05（审计日志的写入、脱敏、分页与只追加）。
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
import pytest

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


@dataclass
class World:
    owner: httpx.AsyncClient
    bob: httpx.AsyncClient
    owner_id: uuid.UUID
    bob_id: uuid.UUID
    owner_workspace: uuid.UUID
    bob_workspace: uuid.UUID
    database_url: str


@pytest.fixture
async def world(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[World]:
    """owner 通过首次安装创建；bob 是另一个用户，有自己的工作空间。"""
    async with make_client() as owner, make_client() as bob:
        setup = (await owner.post("/api/v1/setup", json=OWNER)).json()
        bob_id = await add_user(migrated_database_url, BOB["email"], BOB["password"])
        bob_workspace = await add_workspace(migrated_database_url, bob_id, "Bob 的空间")
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        yield World(
            owner=owner,
            bob=bob,
            owner_id=uuid.UUID(setup["user"]["id"]),
            bob_id=bob_id,
            owner_workspace=uuid.UUID(setup["workspaces"][0]["id"]),
            bob_workspace=bob_workspace,
            database_url=migrated_database_url,
        )


async def audit_logs(
    client: httpx.AsyncClient, workspace_id: uuid.UUID, **params: Any
) -> httpx.Response:
    return await client.get(
        "/api/v1/audit-logs", params=params, headers={"X-Workspace-Id": str(workspace_id)}
    )


async def test_non_members_cannot_see_or_change_other_workspaces(world: World) -> None:
    """S1-03 防 IDOR：别人的工作空间与不存在的工作空间返回完全相同的 404。"""
    bob, target = world.bob, world.owner_workspace
    attempts = [
        await bob.get(f"/api/v1/workspaces/{target}"),
        await bob.patch(f"/api/v1/workspaces/{target}", json={"name": "改掉"}, headers=csrf(bob)),
        await audit_logs(bob, target),
        await bob.get(f"/api/v1/workspaces/{uuid.uuid4()}"),
        await world.owner.get(f"/api/v1/workspaces/{world.bob_workspace}"),
    ]
    for response in attempts:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["message"] == attempts[0].json()["error"]["message"]

    listed = (await bob.get("/api/v1/workspaces")).json()["items"]
    assert [w["id"] for w in listed] == [str(world.bob_workspace)]
    name = await run_sql(
        world.database_url, "SELECT name FROM workspace.workspaces WHERE id = $1", target
    )
    assert name == OWNER["workspace_name"]


@pytest.mark.parametrize(
    ("role", "can_edit", "can_read_audit"),
    [("viewer", False, False), ("member", False, False), ("admin", True, True)],
)
async def test_roles_are_enforced_on_the_server(
    world: World, role: str, can_edit: bool, can_read_audit: bool
) -> None:
    await add_member(world.database_url, world.owner_workspace, world.bob_id, role)
    bob, target = world.bob, world.owner_workspace

    detail = await bob.get(f"/api/v1/workspaces/{target}")
    assert detail.status_code == 200
    assert detail.json()["role"] == role

    edit = await bob.patch(
        f"/api/v1/workspaces/{target}", json={"name": f"{role} 改的名字"}, headers=csrf(bob)
    )
    assert edit.status_code == (200 if can_edit else 403)
    if not can_edit:
        assert edit.json()["error"]["code"] == "FORBIDDEN"

    logs = await audit_logs(bob, target)
    assert logs.status_code == (200 if can_read_audit else 403)


async def test_profile_and_workspace_settings(world: World) -> None:
    owner, target = world.owner, world.owner_workspace

    no_csrf = await owner.patch("/api/v1/me", json={"display_name": "新名字"})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_FAILED"

    profile = await owner.patch(
        "/api/v1/me", json={"display_name": "  新名字  "}, headers=csrf(owner)
    )
    assert profile.status_code == 200
    assert profile.json()["display_name"] == "新名字"
    assert (await owner.get("/api/v1/me")).json()["user"]["display_name"] == "新名字"

    before = (await owner.get(f"/api/v1/workspaces/{target}")).json()
    renamed = await owner.patch(
        f"/api/v1/workspaces/{target}", json={"name": "新的工作空间"}, headers=csrf(owner)
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "新的工作空间"
    assert renamed.json()["role"] == "owner"
    assert renamed.json()["updated_at"] >= before["updated_at"]

    empty = await owner.patch(
        f"/api/v1/workspaces/{target}", json={"name": "   "}, headers=csrf(owner)
    )
    assert empty.status_code == 422


async def test_audit_log_records_security_events(world: World) -> None:
    owner, target = world.owner, world.owner_workspace
    await owner.post("/api/v1/auth/logout", headers=csrf(owner))
    wrong = {"email": OWNER["email"], "password": "not the password"}
    assert (await owner.post("/api/v1/auth/login", json=wrong)).status_code == 401
    unknown = {"email": "nobody@example.com", "password": "whatever"}
    assert (await owner.post("/api/v1/auth/login", json=unknown)).status_code == 401
    good = {"email": OWNER["email"], "password": OWNER["password"]}
    assert (await owner.post("/api/v1/auth/login", json=good)).status_code == 200
    await owner.patch(f"/api/v1/workspaces/{target}", json={"name": "改名"}, headers=csrf(owner))
    await owner.patch("/api/v1/me", json={"display_name": "Owner 2"}, headers=csrf(owner))

    response = await audit_logs(owner, target)
    assert response.status_code == 200
    entries = response.json()["items"]
    assert [e["action"] for e in entries] == [
        "user.updated",
        "workspace.updated",
        "auth.login_succeeded",
        "auth.login_failed",
        "auth.logout",
        "setup.completed",
    ]
    by_action = {e["action"]: e for e in entries}
    assert by_action["auth.login_failed"]["actor_id"] is None
    assert by_action["auth.login_failed"]["metadata"] == {"reason": "invalid_password"}
    assert by_action["workspace.updated"]["metadata"] == {
        "name": {"old": OWNER["workspace_name"], "new": "改名"}
    }
    assert by_action["auth.login_succeeded"]["actor_id"] == str(world.owner_id)
    assert all("ip_hash" not in e for e in entries)
    assert OWNER["password"] not in response.text
    # bob 的登录记录在他自己的工作空间里，不会出现在这里
    assert all(e["actor_id"] != str(world.bob_id) for e in entries)

    ip_hashes = await run_sql(
        world.database_url,
        "SELECT array_agg(DISTINCT ip_hash) FROM workspace.audit_logs WHERE workspace_id = $1",
        target,
    )
    assert len(ip_hashes) == 1
    assert len(ip_hashes[0]) == 64
    assert "127.0.0.1" not in ip_hashes[0]
    # 不存在的账户登录失败：不属于任何工作空间，不记录邮箱
    orphan = await run_sql(
        world.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs WHERE workspace_id IS NULL",
    )
    assert "unknown_account" in orphan
    assert "nobody@example.com" not in orphan


async def test_audit_log_pagination(world: World) -> None:
    owner, target = world.owner, world.owner_workspace
    for index in range(5):
        await owner.patch("/api/v1/me", json={"display_name": f"名字 {index}"}, headers=csrf(owner))
    everything = (await audit_logs(owner, target, limit=100)).json()
    assert everything["next_cursor"] is None
    assert len(everything["items"]) == 6  # setup.completed + 5 次 user.updated

    collected: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (await audit_logs(owner, target, **params)).json()
        collected += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert collected == [e["id"] for e in everything["items"]]

    for bad in ("not-a-cursor", "W10", "WyJ4IiwieSJd"):
        invalid = await audit_logs(owner, target, cursor=bad)
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_audit_logs_are_append_only(world: World) -> None:
    with pytest.raises(asyncpg.PostgresError, match="只允许追加"):
        await run_sql(world.database_url, "UPDATE workspace.audit_logs SET action = 'forged'")
