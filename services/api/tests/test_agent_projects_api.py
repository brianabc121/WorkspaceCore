"""智能体的项目管理权限（T-0112，docs/v2/AGENT_OPS_PLAN.md 第 2.3 节）。

真实 PostgreSQL + Redis，模拟 OpenIM。

覆盖：
- 没打开权限：工具列表里没有 create_project / add_to_project / list_peers，直接调用返回
  FORBIDDEN_CAPABILITY 并留“拒绝”记录；
- 打开权限（owner / admin，乐观并发、审计）后：建项目（项目知识库成员、项目群、群主是开权限的人）、
  拉别的智能体进自己建的项目（不能拉进别人建的项目）、列出智能体；
- 限额；关闭权限后已建的项目保留、不能再建；member 不能开关。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.im import admin_im_user_id
from tests.helpers import OWNER, add_member, add_user, run_sql, tool_error
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_mcp_api import mcp_headers
from tests.test_task_claims_api import agent, call, ok
from tests.test_timeline_api import login

pytestmark = pytest.mark.integration

PASSWORD = "admin password 123456"  # noqa: S105 - 测试账号


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def set_admin(env: ImEnv, instance_id: str, enabled: bool, expect: int = 200) -> Any:
    current = await env.instance(instance_id)
    response = await env.client.put(
        f"/api/v1/agents/instances/{instance_id}/project-admin",
        json={"enabled": enabled, "version": current["version"]},
        headers=env.headers(write=True),
    )
    assert response.status_code == expect, response.text
    return response.json()


async def tool_names(env: ImEnv, token: str) -> set[str]:
    response = await env.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=mcp_headers(token),
    )
    assert response.status_code == 200, response.text
    return {t["name"] for t in response.json()["result"]["tools"]}


async def test_tools_are_hidden_and_refused_without_permission(env: ImEnv) -> None:
    _, token = await agent(env, "甲")
    names = await tool_names(env, token)
    assert {"create_project", "add_to_project", "list_peers"}.isdisjoint(names)
    assert "claim_task" in names
    refused = tool_error(await call(env, token, "create_project", {"title": "新项目"}))
    assert refused["code"] == "FORBIDDEN_CAPABILITY"
    denied = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.ai_tool_executions WHERE tool_name = 'create_project'",
    )
    assert denied == "denied"
    projects = await run_sql(env.database_url, "SELECT count(*) FROM workspace.projects")
    assert projects == 0


async def test_create_project_and_add_agents(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, token_b = await agent(env, "乙")
    c, _ = await agent(env, "丙")
    granted = await set_admin(env, a["id"], True)
    assert granted["enabled"] is True and granted["granted_by"] == str(env.owner_id)
    assert {"create_project", "add_to_project", "list_peers"} <= await tool_names(env, token_a)
    listed = ok(await call(env, token_a, "list_peers", {}))["items"]
    assert {i["untrusted"]["name"] for i in listed} == {"甲", "乙", "丙"}
    assert all(i["idle"] for i in listed)
    created = ok(
        await call(
            env,
            token_a,
            "create_project",
            {"title": "数据迁移", "description": "把旧库迁到新库", "instance_ids": [b["id"]]},
        )
    )
    # 返回时项目群在建（pending），工具返回之前就建好了
    assert created["status"] == "created" and created["im_group"] == "pending"
    project_id = created["project_id"]
    project = (await env.client.get(f"/api/v1/projects/{project_id}", headers=env.headers())).json()
    assert {m["name"] for m in project["agents"]} == {"甲", "乙"}
    assert project["im_group"]["status"] == "ready"
    group_id = project["im_group"]["group_id"]
    assert env.fake.groups[group_id]["owner"] == admin_im_user_id(env.owner_id)
    assert {a["im_user_id"], b["im_user_id"]} <= env.fake.members[f"sg_{group_id}"]
    # 拉丙进自己建的项目
    added = ok(
        await call(
            env,
            token_a,
            "add_to_project",
            {"project_id": project_id, "instance_ids": [c["id"], b["id"]]},
        )
    )
    assert added["added"] == [c["id"]] and added["already"] == [b["id"]]
    assert c["im_user_id"] in env.fake.members[f"sg_{group_id}"]
    # 不能拉进别人建的项目（网页上建的）
    other = await env.client.post(
        "/api/v1/projects", json={"title": "别人的项目"}, headers=env.headers(write=True)
    )
    refused = tool_error(
        await call(
            env,
            token_a,
            "add_to_project",
            {"project_id": other.json()["id"], "instance_ids": [c["id"]]},
        )
    )
    assert refused["code"] == "FORBIDDEN_PROJECT"
    # 乙没有权限
    denied = tool_error(await call(env, token_b, "create_project", {"title": "x"}))
    assert denied["code"] == "FORBIDDEN_CAPABILITY"
    audits = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs"
        " WHERE action = 'agents.instance.project_admin_enabled'",
    )
    assert audits == 1
    notified = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.notifications WHERE type = 'project.created_by_agent'",
    )
    assert notified >= 1


async def test_rate_limit_and_disable_keeps_projects(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    await set_admin(env, a["id"], True)
    for n in range(5):
        ok(await call(env, token_a, "create_project", {"title": f"项目 {n}"}))
    limited = tool_error(await call(env, token_a, "create_project", {"title": "第六个"}))
    assert limited["code"] == "RATE_LIMITED"
    await set_admin(env, a["id"], False)
    refused = tool_error(await call(env, token_a, "list_peers", {}))
    assert refused["code"] == "FORBIDDEN_CAPABILITY"
    count = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.projects WHERE created_by_instance = $1",
        uuid.UUID(a["id"]),
    )
    assert count == 5


async def test_only_admins_toggle_and_version_is_checked(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    member = await add_user(env.database_url, "member@example.com", PASSWORD)
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member, "member")
    stale = (await env.instance(a["id"]))["version"]
    await set_admin(env, a["id"], True)
    response = await env.client.put(
        f"/api/v1/agents/instances/{a['id']}/project-admin",
        json={"enabled": False, "version": stale},
        headers=env.headers(write=True),
    )
    assert response.status_code == 409, response.text
    await login(env, "member@example.com", PASSWORD)
    forbidden = await env.client.put(
        f"/api/v1/agents/instances/{a['id']}/project-admin",
        json={"enabled": False, "version": stale + 1},
        headers=env.headers(write=True),
    )
    assert forbidden.status_code == 403, forbidden.text
    await login(env, OWNER["email"], OWNER["password"])
    shown = await env.instance(a["id"])
    assert shown["project_admin"]["enabled"] is True
