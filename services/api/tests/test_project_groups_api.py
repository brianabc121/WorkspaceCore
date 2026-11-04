"""项目成员与项目群（T-0110，docs/v2/AGENT_OPS_PLAN.md 第 2.1、2.2 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖：
- 新建项目时勾选智能体、建项目群（只限 owner / admin）：群名、公告、群主、成员（含 ws_notice）；
- OpenIM 不可用时项目照常建好、状态 pending，Worker 重试后建好且不会建出两个群；
- 加入 / 移出成员同步项目知识库权限与群成员；停用的实例不能加入；
- 改名、归档同步群名与公告；删除项目解散群；
- 旧项目补建群；后来才就绪的实例被补拉进群；知识库页改参与者也会同步。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain import project_groups
from app.domain.im import admin_im_user_id, openim_client
from tests.helpers import OWNER, add_member, add_user, run_sql
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_timeline_api import login

pytestmark = pytest.mark.integration

PASSWORD = "member password 123456"  # noqa: S105 - 测试账号


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def create(env: ImEnv, expect: int = 201, **body: Any) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/projects",
        json={"title": "发布 2.0", "description": "准备 2.0 的发布", **body},
        headers=env.headers(write=True),
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def run_worker(env: ImEnv) -> int:
    state = env.app.state
    client = openim_client(state.settings, state.connectors, state.redis)
    async with state.sessionmaker() as db:
        return await project_groups.ProjectGroupSync(db, state.settings, client).run_due()


async def agents(env: ImEnv, project_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/projects/{project_id}/agents", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def put_agent(env: ImEnv, project_id: str, instance_id: str, expect: int = 200) -> Any:
    response = await env.client.put(
        f"/api/v1/projects/{project_id}/agents/{instance_id}", headers=env.headers(write=True)
    )
    assert response.status_code == expect, response.text
    return response.json()


async def test_create_project_with_agents_builds_group(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    project = await create(env, instance_ids=[a["id"], b["id"], a["id"]], im_group=True)
    assert [m["name"] for m in project["agents"]] == ["乙", "甲"]
    group = project["im_group"]
    assert group["status"] == "ready" and group["error"] is None
    group_id = group["group_id"]
    assert group["conversation_id"] == f"sg_{group_id}"
    fake = env.fake.groups[group_id]
    owner_im = admin_im_user_id(env.owner_id)
    assert fake["name"] == "发布 2.0" and fake["notification"] == "准备 2.0 的发布"
    assert fake["owner"] == owner_im
    assert env.fake.members[f"sg_{group_id}"] == {
        owner_im,
        a["im_user_id"],
        b["im_user_id"],
        "ws_notice",
    }
    listed = await agents(env, project["id"])
    assert {i["name"]: i["in_group"] for i in listed["items"]} == {"甲": True, "乙": True}
    assert {i["access"] for i in listed["items"]} == {"read_write"}
    # 群出现在管理员的 IM 会话里
    conversations = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_conversations WHERE conversation_id = $1",
        f"sg_{group_id}",
    )
    assert conversations == 1
    # 智能体加入项目 = 项目知识库读写（project_agents）
    rows = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.project_agents WHERE project_id = $1",
        uuid.UUID(project["id"]),
    )
    assert rows == 2


async def test_member_cannot_pick_agents_or_group(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    member = await add_user(env.database_url, "member@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member, "member")
    await login(env, "member@example.com", PASSWORD)
    denied = await create(env, expect=403, instance_ids=[a["id"]])
    assert denied["error"]["code"] == "FORBIDDEN"
    await create(env, expect=403, im_group=True)
    plain = await create(env)
    assert plain["agents"] == [] and plain["im_group"]["status"] == "none"
    await put_agent(env, plain["id"], a["id"], expect=403)
    await login(env, OWNER["email"], OWNER["password"])
    # 停用的实例不能加入
    await env.set_enabled(a["id"], False)
    bad = await create(env, expect=422, instance_ids=[a["id"]])
    assert bad["error"]["code"] == "VALIDATION_FAILED"


async def test_group_is_retried_when_openim_is_down(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    env.fake.down = True
    project = await create(env, instance_ids=[a["id"]], im_group=True)
    assert project["im_group"]["status"] == "pending"
    assert project["im_group"]["error"] == "unavailable" and project["im_group"]["group_id"] is None
    assert project["agents"][0]["name"] == "甲"
    assert await run_worker(env) == 0
    env.fake.down = False
    assert await run_worker(env) == 1
    current = (await agents(env, project["id"]))["im_group"]
    assert current["status"] == "ready" and current["error"] is None
    assert await run_worker(env) == 0
    assert len(env.fake.groups) == 1
    # 手动重试也安全：已建好的只是重新同步
    response = await env.client.post(
        f"/api/v1/projects/{project['id']}/im-group", headers=env.headers(write=True)
    )
    assert response.status_code == 200, response.text
    assert response.json()["im_group"]["group_id"] == current["group_id"]
    assert len(env.fake.groups) == 1


async def test_members_follow_project(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    project = await create(env, instance_ids=[a["id"]], im_group=True)
    group_id = project["im_group"]["group_id"]
    listed = await put_agent(env, project["id"], b["id"])
    assert {i["name"] for i in listed["items"]} == {"甲", "乙"}
    assert b["im_user_id"] in env.fake.members[f"sg_{group_id}"]
    # 重复加入不变
    await put_agent(env, project["id"], b["id"])
    response = await env.client.delete(
        f"/api/v1/projects/{project['id']}/agents/{a['id']}", headers=env.headers(write=True)
    )
    assert response.status_code == 200, response.text
    assert [i["name"] for i in response.json()["items"]] == ["乙"]
    assert a["im_user_id"] not in env.fake.members[f"sg_{group_id}"]
    members = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_group_members m JOIN workspace.im_groups g "
        "ON g.id = m.group_pk WHERE g.group_id = $1 AND m.instance_id IS NOT NULL",
        group_id,
    )
    assert members == 1
    added = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'project.agent_added'",
    )
    assert added == 1


async def test_rename_archive_and_delete_follow_group(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    project = await create(env, instance_ids=[a["id"]], im_group=True)
    group_id = project["im_group"]["group_id"]
    response = await env.client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": project["version"], "title": "发布 2.1", "archived": True},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    assert response.json()["im_group"]["status"] == "ready"
    fake = env.fake.groups[group_id]
    assert fake["name"] == "发布 2.1"
    assert fake["notification"] == "（项目已归档）准备 2.0 的发布"
    response = await env.client.delete(
        f"/api/v1/projects/{project['id']}",
        params={"version": response.json()["version"]},
        headers=env.headers(write=True),
    )
    assert response.status_code == 204, response.text
    assert env.fake.groups[group_id]["dismissed"] is True
    dismissed = await run_sql(
        env.database_url,
        "SELECT dismissed_at IS NOT NULL FROM workspace.im_groups WHERE group_id = $1",
        group_id,
    )
    assert dismissed is True


async def test_old_project_can_request_group(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    project = await create(env, instance_ids=[a["id"]])
    assert project["im_group"]["status"] == "none" and not env.fake.groups
    response = await env.client.post(
        f"/api/v1/projects/{project['id']}/im-group", headers=env.headers(write=True)
    )
    assert response.status_code == 200, response.text
    group = response.json()["im_group"]
    assert group["status"] == "ready"
    assert env.fake.members[f"sg_{group['group_id']}"] == {
        admin_im_user_id(env.owner_id),
        a["im_user_id"],
        "ws_notice",
    }


async def test_late_ready_instance_and_kb_participants_sync(env: ImEnv) -> None:
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET im_ready = false WHERE id = $1",
        uuid.UUID(b["id"]),
    )
    project = await create(env, instance_ids=[a["id"], b["id"]], im_group=True)
    group_id = project["im_group"]["group_id"]
    assert b["im_user_id"] not in env.fake.members[f"sg_{group_id}"]
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET im_ready = true WHERE id = $1",
        uuid.UUID(b["id"]),
    )
    assert await run_worker(env) == 1
    assert b["im_user_id"] in env.fake.members[f"sg_{group_id}"]
    # 知识库页改参与者：项目群在下一分钟同步
    c = await env.create_instance(name="丙")
    listed = await env.client.get("/api/v1/knowledge/spaces", headers=env.headers())
    spaces = listed.json()["items"]
    space = next(s for s in spaces if s.get("project_id") == project["id"])
    response = await env.client.put(
        f"/api/v1/knowledge/spaces/{space['id']}/agents/{c['id']}",
        json={"access": "read"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    status = await run_sql(
        env.database_url,
        "SELECT im_group_status FROM workspace.projects WHERE id = $1",
        uuid.UUID(project["id"]),
    )
    assert status == "pending"
    assert await run_worker(env) == 1
    assert c["im_user_id"] in env.fake.members[f"sg_{group_id}"]
