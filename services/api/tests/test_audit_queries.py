"""审计日志的服务端筛选、游标与单条详情（T-0197）。

契约 docs/v2/ai-admin-redesign/…-contract.md 第 5.1 节。真实 PostgreSQL（已迁移）+ Redis；
审计记录直接用 SQL 写入，时间放在过去，避开首次安装与登录写的记录。
"""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.audit_labels import action_label, actions_matching_label
from tests.audit_support import (
    AuditWorld,
    add_named_user,
    all_pages,
    bulk_audit,
    insert_audit,
    open_audit_world,
)
from tests.helpers import OWNER, ClientFactory, add_member, run_sql

pytestmark = pytest.mark.integration

T0 = datetime(2026, 3, 1, tzinfo=UTC)
WINDOW: dict[str, Any] = {"after": T0.isoformat(), "before": (T0 + timedelta(days=30)).isoformat()}
IP_HASH = "feedface" * 8


@dataclass
class Seed:
    noise: list[str]  # 40 条 bulk.row，比其余种子都新（新的在前）
    rotated: uuid.UUID
    secret_created: uuid.UUID
    system: uuid.UUID
    removed_actor: uuid.UUID
    unknown: uuid.UUID
    project_row: uuid.UUID
    deleted_project_row: uuid.UUID
    bad_id_row: uuid.UUID
    foreign_project_row: uuid.UUID
    user_row: uuid.UUID
    other_workspace_row: uuid.UUID
    orphan_row: uuid.UUID
    removed_user: uuid.UUID


@pytest.fixture
async def world(
    make_client: ClientFactory, migrated_database_url: str
) -> AsyncIterator[AuditWorld]:
    async with open_audit_world(make_client, migrated_database_url) as opened:
        yield opened


@pytest.fixture
async def seed(world: AuditWorld) -> Seed:
    url, ws = world.database_url, world.workspace
    secret_id = await run_sql(
        url,
        "INSERT INTO workspace.agent_secrets (workspace_id, name) VALUES ($1, 'prod-deploy-key')"
        " RETURNING id",
        ws,
    )
    project_id = await run_sql(
        url,
        "INSERT INTO workspace.projects (workspace_id, title) VALUES ($1, '新项目名') RETURNING id",
        ws,
    )
    foreign_project = await run_sql(
        url,
        "INSERT INTO workspace.projects (workspace_id, title)"
        " VALUES ($1, '别人的项目') RETURNING id",
        world.other_workspace,
    )
    # 曾经是成员、已被移出的用户
    removed = await add_named_user(url, "frank@example.com", "Frank 已离开")
    await add_member(url, ws, removed, "member")
    await run_sql(
        url,
        "DELETE FROM workspace.workspace_members WHERE workspace_id = $1 AND user_id = $2",
        ws,
        removed,
    )
    await bulk_audit(
        url,
        ws,
        40,
        start=T0 + timedelta(days=10),
        step=timedelta(minutes=1),
        actor_id=world.owner_id,
    )
    noise = await run_sql(
        url,
        "SELECT array_agg(id::text ORDER BY created_at DESC, id DESC) FROM workspace.audit_logs"
        " WHERE action = 'bulk.row'",
    )
    secret = {"resource_type": "agent_secret", "resource_id": str(secret_id)}
    rows: list[dict[str, Any]] = [
        {
            "workspace_id": ws,
            "actor_id": world.admin_id,
            "action": "agents.secret.rotated",
            **secret,
            "metadata": {"name": "deploy-key", "value_version": 3},
            "ip_hash": IP_HASH,
            "created_at": T0 + timedelta(days=1),
        },
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "agents.secret.created",
            **secret,
            "metadata": {"name": "deploy-key", "kind": "api_token"},
            "created_at": T0 + timedelta(days=1, hours=-1),
        },
        {
            "workspace_id": ws,
            "action": "auth.login_failed",
            "resource_type": "user",
            "resource_id": str(world.owner_id),
            "metadata": {"reason": "invalid_password"},
            "created_at": T0 + timedelta(days=2),
        },
        {
            "workspace_id": ws,
            "actor_id": removed,
            "action": "workspace.updated",
            "resource_type": "workspace",
            "resource_id": str(ws),
            "metadata": {"name": {"old": "a", "new": "b"}},
            "created_at": T0 + timedelta(days=3),
        },
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "custom.mystery_event",
            "resource_type": "mystery",
            "resource_id": "x-1",
            "metadata": {"note": "zzz-hidden-note"},
            "created_at": T0 + timedelta(days=4),
        },
        {
            "workspace_id": ws,
            "actor_id": world.member_id,
            "action": "project.updated",
            "resource_type": "project",
            "resource_id": str(project_id),
            "metadata": {"title": "旧项目名"},
            "created_at": T0 + timedelta(days=5),
        },
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "project.deleted",
            "resource_type": "project",
            "resource_id": str(uuid.uuid4()),
            "metadata": {"title": "已删项目"},
            "created_at": T0 + timedelta(days=5, hours=1),
        },
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "project.updated",
            "resource_type": "project",
            "resource_id": "not-a-uuid",
            "created_at": T0 + timedelta(days=5, hours=2),
        },
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "project.updated",
            "resource_type": "project",
            "resource_id": str(foreign_project),
            "created_at": T0 + timedelta(days=5, hours=3),
        },
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "workspace.member_role_changed",
            "resource_type": "user",
            "resource_id": str(world.member_id),
            "metadata": {"role": {"old": "viewer", "new": "member"}},
            "created_at": T0 + timedelta(days=6),
        },
        {
            "workspace_id": world.other_workspace,
            "actor_id": world.outsider_id,
            "action": "agents.secret.rotated",
            "resource_type": "agent_secret",
            "resource_id": str(uuid.uuid4()),
            "metadata": {"name": "deploy-key", "value_version": 2},
            "created_at": T0 + timedelta(days=1),
        },
        {
            "action": "auth.login_failed",
            "metadata": {"reason": "unknown_account", "name": "deploy-key"},
            "created_at": T0 + timedelta(days=1),
        },
    ]
    ids = await insert_audit(url, rows)
    return Seed(
        noise=noise,
        rotated=ids[0],
        secret_created=ids[1],
        system=ids[2],
        removed_actor=ids[3],
        unknown=ids[4],
        project_row=ids[5],
        deleted_project_row=ids[6],
        bad_id_row=ids[7],
        foreign_project_row=ids[8],
        user_row=ids[9],
        other_workspace_row=ids[10],
        orphan_row=ids[11],
        removed_user=removed,
    )


def ids_of(response_items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in response_items]


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---------------------------------------------------------------- 筛选在分页前生效


async def test_keyword_filter_applies_before_limit(world: AuditWorld, seed: Seed) -> None:
    first = (await world.list(world.owner, limit=5)).json()
    assert str(seed.rotated) not in ids_of(first["items"])

    found = await world.list(world.owner, q="deploy-key", limit=5)
    assert found.status_code == 200
    # 只有本工作空间的两条；另一个工作空间与 workspace_id 为空的记录不出现
    assert ids_of(found.json()["items"]) == [str(seed.rotated), str(seed.secret_created)]
    assert found.json()["next_cursor"] is None
    assert found.json()["total_estimate"] is None

    upper = await world.list(world.owner, q="DEPLOY-KEY", limit=5)
    assert ids_of(upper.json()["items"]) == ids_of(found.json()["items"])

    by_resource_id = await world.list(world.owner, q="x-1")
    assert ids_of(by_resource_id.json()["items"]) == [str(seed.unknown)]
    by_action = await world.list(world.owner, q="mystery")
    assert ids_of(by_action.json()["items"]) == [str(seed.unknown)]
    by_title = await world.list(world.owner, q="旧项目")
    assert ids_of(by_title.json()["items"]) == [str(seed.project_row)]

    # 不搜 ip_hash、metadata 里 name / title 以外的键；通配符按字面
    for q in ("feedface", "zzz-hidden", "%", "agents_secret", "unknown_account"):
        assert (await world.list(world.owner, q=q)).json()["items"] == [], q


async def test_keyword_matches_builtin_chinese_labels(world: AuditWorld, seed: Seed) -> None:
    label = action_label("agents.secret.rotated")
    assert label is not None
    response = await world.list(world.owner, q=label, **WINDOW)
    assert response.status_code == 200
    items = response.json()["items"]
    assert str(seed.rotated) in ids_of(items)
    allowed = set(actions_matching_label(label))
    assert "agents.secret.rotated" in allowed
    assert all(item["action"] in allowed for item in items)
    # 未知事件仍能按原 action 查到
    raw = await world.list(world.owner, q="custom.mystery_event")
    assert ids_of(raw.json()["items"]) == [str(seed.unknown)]


async def test_time_range_after_inclusive_before_exclusive(world: AuditWorld, seed: Seed) -> None:
    after, before = T0 + timedelta(days=1), T0 + timedelta(days=2)
    response = await world.list(world.owner, after=after.isoformat(), before=before.isoformat())
    assert response.status_code == 200
    assert ids_of(response.json()["items"]) == [str(seed.rotated)]

    # 换成 +08:00 表示的同一时刻，结果相同
    shanghai = "2026-03-02T08:00:00+08:00", "2026-03-03T08:00:00+08:00"
    same = await world.list(world.owner, after=shanghai[0], before=shanghai[1])
    assert ids_of(same.json()["items"]) == [str(seed.rotated)]

    invalid: list[dict[str, Any]] = [
        {"after": after.isoformat()},
        {"before": before.isoformat()},
        {"after": "2026-03-02T00:00:00", "before": before.isoformat()},
        {"after": after.isoformat(), "before": "2026-03-03T00:00:00"},
        {"after": after.isoformat(), "before": after.isoformat()},
        {"after": before.isoformat(), "before": after.isoformat()},
    ]
    for params in invalid:
        bad = await world.list(world.owner, **params)
        assert bad.status_code == 422, params
        assert error_code(bad) == "VALIDATION_FAILED"


async def test_actor_and_system_filters(world: AuditWorld, seed: Seed) -> None:
    by_admin = await world.list(world.owner, actor_id=str(world.admin_id), **WINDOW)
    assert ids_of(by_admin.json()["items"]) == [str(seed.rotated)]
    system = await world.list(world.owner, system_only="true", **WINDOW)
    assert ids_of(system.json()["items"]) == [str(seed.system)]
    assert all(item["actor_id"] is None for item in system.json()["items"])
    not_system = await world.list(
        world.owner, system_only="false", actor_id=str(world.admin_id), **WINDOW
    )
    assert ids_of(not_system.json()["items"]) == [str(seed.rotated)]

    both = await world.list(world.owner, system_only="true", actor_id=str(world.admin_id))
    assert both.status_code == 422
    assert error_code(both) == "VALIDATION_FAILED"


async def test_action_exact_prefix_and_resource_type(world: AuditWorld, seed: Seed) -> None:
    exact = await world.list(world.owner, action="agents.secret.rotated", **WINDOW)
    assert ids_of(exact.json()["items"]) == [str(seed.rotated)]
    prefix = await world.list(world.owner, action_prefix="agents.secret.", **WINDOW)
    assert ids_of(prefix.json()["items"]) == [str(seed.rotated), str(seed.secret_created)]
    # 前缀按字面：% 与 _ 不是通配符
    for literal in ("%", "agents_secret", "agents.secret._"):
        assert (await world.list(world.owner, action_prefix=literal)).json()["items"] == []

    projects = await world.list(world.owner, resource_type="project", **WINDOW)
    assert ids_of(projects.json()["items"]) == [
        str(seed.foreign_project_row),
        str(seed.bad_id_row),
        str(seed.deleted_project_row),
        str(seed.project_row),
    ]

    invalid: list[dict[str, Any]] = [
        {"action": "a.b", "action_prefix": "a."},
        {"action": "a" * 129},
        {"action_prefix": "a" * 129},
        {"resource_type": "r" * 65},
        {"q": "q" * 101},
        {"q": ""},
    ]
    for params in invalid:
        bad = await world.list(world.owner, **params)
        assert bad.status_code == 422, params
        assert error_code(bad) == "VALIDATION_FAILED"


# ---------------------------------------------------------------- 游标


async def test_cursor_pages_and_is_bound_to_filters_and_workspace(
    world: AuditWorld, seed: Seed
) -> None:
    pages = await all_pages(world, world.owner, action="bulk.row", limit=7)
    assert ids_of(pages) == seed.noise

    first = (await world.list(world.owner, action="bulk.row", limit=7)).json()
    cursor = first["next_cursor"]
    assert cursor
    # 同样的条件可以继续翻；条件变了（结果哪怕相同）不能复用
    assert (await world.list(world.owner, action="bulk.row", cursor=cursor)).status_code == 200
    for changed in (
        {"action_prefix": "bulk."},
        {},
        {"action": "bulk.row", "q": "1"},
        {"action": "bulk.row", **WINDOW},
        {"action": "bulk.row", "actor_id": str(world.owner_id)},
    ):
        reused = await world.list(world.owner, cursor=cursor, **changed)
        assert reused.status_code == 422, changed
        assert error_code(reused) == "VALIDATION_FAILED"
    # 换工作空间不能复用（owner 也是另一个工作空间的 admin）
    elsewhere = await world.list(
        world.owner, world.other_workspace, action="bulk.row", cursor=cursor
    )
    assert elsewhere.status_code == 422

    # 篡改游标里的位置
    raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert isinstance(raw, dict)
    tampered = dict(raw)
    tampered["i"] = str(uuid.uuid4())
    forged = base64.urlsafe_b64encode(json.dumps(tampered).encode()).decode().rstrip("=")
    bad = await world.list(world.owner, action="bulk.row", cursor=forged)
    assert bad.status_code == 422
    # 不暴露搜索词：游标里只有位置与签名
    with_q = (await world.list(world.owner, q="bulk", limit=1)).json()["next_cursor"]
    assert "bulk" not in base64.urlsafe_b64decode(with_q + "=" * (-len(with_q) % 4)).decode()


# ---------------------------------------------------------------- 权限与范围


async def test_roles_and_workspace_scope(world: AuditWorld, seed: Seed) -> None:
    for client in (world.member, world.viewer):
        for response in (
            await world.list(client),
            await world.detail(client, seed.rotated),
            await world.export(client, **WINDOW),
        ):
            assert response.status_code == 403
            assert error_code(response) == "FORBIDDEN"
    for response in (
        await world.list(world.outsider),
        await world.detail(world.outsider, seed.rotated),
        await world.export(world.outsider, **WINDOW),
    ):
        assert response.status_code == 404
        assert error_code(response) == "NOT_FOUND"

    assert (await world.detail(world.admin, seed.rotated)).status_code == 200
    for entry in (seed.other_workspace_row, seed.orphan_row, uuid.uuid4()):
        missing = await world.detail(world.admin, entry)
        assert missing.status_code == 404
        assert error_code(missing) == "NOT_FOUND"
    # 换成另一个工作空间的请求头也看不到这个工作空间的记录
    cross = await world.detail(world.owner, seed.rotated, world.other_workspace)
    assert cross.status_code == 404


async def test_rows_without_workspace_are_never_listed(world: AuditWorld, seed: Seed) -> None:
    everything = await all_pages(world, world.owner, limit=100)
    listed = set(ids_of(everything))
    assert str(seed.orphan_row) not in listed
    assert str(seed.other_workspace_row) not in listed
    assert str(seed.rotated) in listed
    in_scope = await run_sql(
        world.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE workspace_id = $1",
        world.workspace,
    )
    assert len(listed) == in_scope


# ---------------------------------------------------------------- 名称与标签


async def test_labels_names_and_metadata_are_honest(world: AuditWorld, seed: Seed) -> None:
    owner_view = (await world.detail(world.owner, seed.rotated)).json()
    assert set(owner_view) == {
        "id",
        "action",
        "actor_id",
        "resource_type",
        "resource_id",
        "metadata",
        "created_at",
        "action_label",
        "actor_label",
        "resource_current_name",
    }
    assert owner_view["action"] == "agents.secret.rotated"
    assert owner_view["action_label"] == action_label("agents.secret.rotated")
    assert owner_view["actor_label"] == "Admin 用户"
    # 当前名称与记录里的名称分开：记录写的是 deploy-key，密钥现在叫 prod-deploy-key
    assert owner_view["resource_current_name"] == "prod-deploy-key"
    # 只有新版本号：不补旧值、不补 before / after
    assert owner_view["metadata"] == {"name": "deploy-key", "value_version": 3}
    assert IP_HASH not in json.dumps(owner_view)

    # admin 不能借审计读到 owner 专属的密钥名
    admin_view = (await world.detail(world.admin, seed.rotated)).json()
    assert admin_view["resource_current_name"] is None
    assert admin_view["actor_label"] == "Admin 用户"
    listed = (await world.list(world.admin, q="deploy-key")).json()["items"]
    assert [item["resource_current_name"] for item in listed] == [None, None]
    assert "prod-deploy-key" not in json.dumps(listed)

    removed = (await world.detail(world.admin, seed.removed_actor)).json()
    assert removed["actor_id"] == str(seed.removed_user)
    assert removed["actor_label"] is None  # 已被移出：不解析，界面退回显示 ID
    assert removed["resource_current_name"] == OWNER["workspace_name"]
    assert removed["metadata"] == {"name": {"old": "a", "new": "b"}}

    system = (await world.detail(world.admin, seed.system)).json()
    assert system["actor_id"] is None
    assert system["actor_label"] == "系统"
    assert system["resource_current_name"] == OWNER["display_name"]

    unknown = (await world.detail(world.admin, seed.unknown)).json()
    assert unknown["action"] == "custom.mystery_event"
    assert unknown["action_label"] is None
    assert unknown["resource_current_name"] is None

    project = (await world.detail(world.admin, seed.project_row)).json()
    assert project["resource_current_name"] == "新项目名"
    assert project["metadata"] == {"title": "旧项目名"}
    assert project["actor_label"] == "Member 用户"

    user = (await world.detail(world.admin, seed.user_row)).json()
    assert user["resource_current_name"] == "Member 用户"

    # 已删除、ID 不合法、属于别的工作空间的对象：没有当前名称，整页照常返回
    page = await world.list(world.admin, resource_type="project", **WINDOW)
    assert page.status_code == 200
    names = {item["id"]: item["resource_current_name"] for item in page.json()["items"]}
    assert names == {
        str(seed.project_row): "新项目名",
        str(seed.deleted_project_row): None,
        str(seed.bad_id_row): None,
        str(seed.foreign_project_row): None,
    }
    assert "别人的项目" not in page.text


async def test_view_metadata_hides_ip_and_old_unredacted_keys(world: AuditWorld) -> None:
    [entry] = await insert_audit(
        world.database_url,
        [
            {
                "workspace_id": world.workspace,
                "actor_id": world.owner_id,
                "action": "legacy.event",
                "metadata": {
                    "name": "legacy",
                    "client_ip": "203.0.113.9",
                    "nested": {"ip": "192.0.2.1", "api_token": "tok-SECRET-123"},
                },
                "created_at": T0,
            }
        ],
    )
    view = (await world.detail(world.owner, entry)).json()
    assert view["metadata"] == {"name": "legacy", "nested": {"api_token": "[REDACTED]"}}
    text = json.dumps(view)
    for leaked in ("203.0.113.9", "192.0.2.1", "tok-SECRET-123"):
        assert leaked not in text


def test_builtin_labels_do_not_claim_completion() -> None:
    started = action_label("agents.ssh.install_started")
    assert started is not None
    assert "成功" not in started and "完成" not in started
    assert action_label("agents.ssh.install_succeeded") != started
    assert action_label("something.never.recorded") is None
    assert actions_matching_label("") == []
    assert "auth.login_failed" in actions_matching_label("登录失败")
