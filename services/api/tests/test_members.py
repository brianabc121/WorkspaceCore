"""工作空间成员管理（T-0064；规范第 9.2 节）：真实 PostgreSQL（已迁移）+ Redis。

覆盖：列表只给 admin 及以上；按邮箱加入已有账号；没有账号时创建账号（初始密码）并能登录；
admin 只管理 member / viewer，涉及 admin 只能由 owner；owner 不能被修改或移除；
移除后立即失去访问；非成员 404；全部写审计日志且不含密码。
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest

from tests.helpers import (
    BOB,
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    asyncpg_dsn,
    csrf,
    run_sql,
)

pytestmark = pytest.mark.integration

CAROL = {"email": "carol@example.com", "password": "carol password 123456"}


@dataclass
class World:
    owner: httpx.AsyncClient
    bob: httpx.AsyncClient
    make_client: ClientFactory
    owner_id: uuid.UUID
    bob_id: uuid.UUID
    workspace: uuid.UUID
    database_url: str

    def members(self, user_id: uuid.UUID | str | None = None) -> str:
        base = f"/api/v1/workspaces/{self.workspace}/members"
        return base if user_id is None else f"{base}/{user_id}"


@pytest.fixture
async def world(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[World]:
    async with make_client() as owner, make_client() as bob:
        setup = (await owner.post("/api/v1/setup", json=OWNER)).json()
        bob_id = await add_user(migrated_database_url, BOB["email"], BOB["password"])
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        yield World(
            owner=owner,
            bob=bob,
            make_client=make_client,
            owner_id=uuid.UUID(setup["user"]["id"]),
            bob_id=bob_id,
            workspace=uuid.UUID(setup["workspaces"][0]["id"]),
            database_url=migrated_database_url,
        )


async def _audit(world: World, action: str) -> list[str]:
    conn = await asyncpg.connect(asyncpg_dsn(world.database_url))
    try:
        rows = await conn.fetch(
            "SELECT metadata_redacted_json::text AS metadata FROM workspace.audit_logs"
            " WHERE action = $1 ORDER BY created_at",
            action,
        )
    finally:
        await conn.close()
    return [r["metadata"] for r in rows]


async def test_owner_adds_existing_and_new_accounts(world: World) -> None:
    owner = world.owner
    listed = (await owner.get(world.members())).json()["items"]
    assert [(m["email"], m["role"], m["you"]) for m in listed] == [
        ("owner@example.com", "owner", True)
    ]

    # 已有账号：按邮箱加入（大小写不敏感）
    added = await owner.post(
        world.members(), json={"email": "BOB@example.com", "role": "member"}, headers=csrf(owner)
    )
    assert added.status_code == 201, added.text
    assert added.headers.get("X-Account-Created") is None
    assert (added.json()["user_id"], added.json()["role"]) == (str(world.bob_id), "member")
    assert (await world.bob.get(f"/api/v1/workspaces/{world.workspace}")).status_code == 200

    # 重复加入
    again = await owner.post(
        world.members(), json={"email": BOB["email"], "role": "viewer"}, headers=csrf(owner)
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "MEMBER_EXISTS")
    # 已有账号不能借“加入”设置密码
    reset = await owner.post(
        world.members(),
        json={"email": OWNER["email"], "role": "viewer", "password": "x" * 12},
        headers=csrf(owner),
    )
    assert reset.status_code == 409

    # 没有账号：不给初始密码时提示，给了就创建
    missing = await owner.post(
        world.members(), json={"email": CAROL["email"], "role": "viewer"}, headers=csrf(owner)
    )
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "USER_NOT_FOUND")
    short = await owner.post(
        world.members(),
        json={"email": CAROL["email"], "role": "viewer", "display_name": "Carol", "password": "x"},
        headers=csrf(owner),
    )
    assert short.status_code == 422
    created = await owner.post(
        world.members(),
        json={
            "email": CAROL["email"],
            "role": "viewer",
            "display_name": "Carol",
            "password": CAROL["password"],
        },
        headers=csrf(owner),
    )
    assert created.status_code == 201, created.text
    assert created.headers["X-Account-Created"] == "true"
    async with world.make_client() as carol:
        login = await carol.post("/api/v1/auth/login", json=CAROL)
        assert login.status_code == 200
        workspaces = (await carol.get("/api/v1/workspaces")).json()["items"]
        assert [(w["id"], w["role"]) for w in workspaces] == [(str(world.workspace), "viewer")]

    roles = {m["email"]: m["role"] for m in (await owner.get(world.members())).json()["items"]}
    assert roles == {"owner@example.com": "owner", BOB["email"]: "member", CAROL["email"]: "viewer"}
    # 审计：新建账号与加入都有记录，不含密码
    assert len(await _audit(world, "user.created")) == 1
    added_logs = await _audit(world, "workspace.member_added")
    assert len(added_logs) == 2
    assert all(CAROL["password"] not in m for m in added_logs)


async def test_admin_manages_members_and_viewers_only(world: World) -> None:
    owner = world.owner
    await add_member(world.database_url, world.workspace, world.bob_id, "admin")
    carol_id = await add_user(world.database_url, CAROL["email"], CAROL["password"])
    await add_member(world.database_url, world.workspace, carol_id, "viewer")
    dave_id = await add_user(world.database_url, "dave@example.com", "dave password 123456")
    await add_member(world.database_url, world.workspace, dave_id, "admin")
    bob = world.bob

    assert (await bob.get(world.members())).status_code == 200
    # admin 能改 member / viewer
    changed = await bob.patch(world.members(carol_id), json={"role": "member"}, headers=csrf(bob))
    assert (changed.status_code, changed.json()["role"]) == (200, "member")
    # 不能授予 admin、不能改别的 admin、不能改自己、不能动 owner
    for target, role in ((carol_id, "admin"), (dave_id, "member"), (world.bob_id, "member")):
        denied = await bob.patch(world.members(target), json={"role": role}, headers=csrf(bob))
        assert (denied.status_code, denied.json()["error"]["code"]) == (403, "FORBIDDEN"), target
    owner_change = await bob.patch(
        world.members(world.owner_id), json={"role": "viewer"}, headers=csrf(bob)
    )
    assert owner_change.status_code == 403
    for target in (dave_id, world.owner_id):
        assert (await bob.delete(world.members(target), headers=csrf(bob))).status_code == 403
    new_admin = await bob.post(
        world.members(),
        json={
            "email": "eve@example.com",
            "role": "admin",
            "display_name": "Eve",
            "password": "e" * 12,
        },
        headers=csrf(bob),
    )
    assert new_admin.status_code == 403
    assert (
        await run_sql(
            world.database_url,
            "SELECT count(*) FROM workspace.users WHERE email = 'eve@example.com'",
        )
        == 0
    )
    # 不能授予 owner（请求校验）
    as_owner = await bob.patch(world.members(carol_id), json={"role": "owner"}, headers=csrf(bob))
    assert as_owner.status_code == 422

    # owner 可以收回 admin、移除 admin；但不能改或移除自己
    demoted = await owner.patch(
        world.members(dave_id), json={"role": "member"}, headers=csrf(owner)
    )
    assert demoted.json()["role"] == "member"
    self_change = await owner.patch(
        world.members(world.owner_id), json={"role": "admin"}, headers=csrf(owner)
    )
    assert self_change.status_code == 403
    assert (
        await owner.delete(world.members(world.owner_id), headers=csrf(owner))
    ).status_code == 403

    # admin 移除 member：立即失去访问
    async with world.make_client() as carol:
        assert (await carol.post("/api/v1/auth/login", json=CAROL)).status_code == 200
        assert (await carol.get(f"/api/v1/workspaces/{world.workspace}")).status_code == 200
        removed = await bob.delete(world.members(carol_id), headers=csrf(bob))
        assert removed.status_code == 204
        assert (await carol.get(f"/api/v1/workspaces/{world.workspace}")).status_code == 404
    assert (await bob.delete(world.members(carol_id), headers=csrf(bob))).status_code == 404

    changes = await _audit(world, "workspace.member_role_changed")
    assert len(changes) == 2
    assert len(await _audit(world, "workspace.member_removed")) == 1


async def test_members_need_admin_and_membership(world: World) -> None:
    # 非成员：404（不区分不存在）
    for response in (
        await world.bob.get(world.members()),
        await world.bob.post(
            world.members(), json={"email": BOB["email"], "role": "member"}, headers=csrf(world.bob)
        ),
        await world.owner.get(f"/api/v1/workspaces/{uuid.uuid4()}/members"),
    ):
        assert response.status_code == 404
    # member / viewer：403
    await add_member(world.database_url, world.workspace, world.bob_id, "member")
    assert (await world.bob.get(world.members())).status_code == 403
    assert (
        await world.bob.delete(world.members(world.owner_id), headers=csrf(world.bob))
    ).status_code == 403
    # 写操作要 CSRF
    no_csrf = await world.owner.patch(world.members(world.bob_id), json={"role": "viewer"})
    assert no_csrf.status_code == 403
    # 不存在的成员
    missing = await world.owner.patch(
        world.members(uuid.uuid4()), json={"role": "viewer"}, headers=csrf(world.owner)
    )
    assert missing.status_code == 404
