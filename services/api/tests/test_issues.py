"""项目里的 Issues（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 第 8 节）。

真实 PostgreSQL + Redis。覆盖：

- 人：按项目编号、模板、@ 提到与 #编号 引用、通知、关闭要原因与重复、谁能关闭、
  锁定与隐藏只有管理员、viewer 只读、乐观并发、删除进回收站、全局搜索、Issue 提示开关；
- 智能体（MCP 工具，例外五）：没参与的项目当作不存在、只读参与只能读、指派范围、
  client_key 重试不重复、密钥拦截、收件箱与已读、合并唤醒、连续 10 条评论暂停、锁定、
  不能锁定、每小时限额、执行记录不存正文；
- 任务：关联的未关闭“阻塞”挡住 complete_task（人照常能完成）、get_task 附上 Issue、
  release_task 提醒开交接 Issue、README 的“沟通”“讨论”。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.domain import issues as issues_domain
from tests.helpers import BOB, ClientFactory, add_member, add_user, csrf, run_sql, tool_error
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_readme import join, new_project, new_task, project_readme, refresh, task_readme
from tests.test_task_claims_api import agent, call, ok, patch

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def open_issue(env: ImEnv, project_id: str, expect: int = 201, **body: Any) -> dict[str, Any]:
    response = await env.client.post(
        f"/api/v1/projects/{project_id}/issues",
        json={"title": "接口怎么约定", **body},
        headers=env.headers(write=True),
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def update(
    client: httpx.AsyncClient,
    workspace_id: str,
    issue: dict[str, Any],
    expect: int = 200,
    **body: Any,
) -> dict[str, Any]:
    response = await client.patch(
        f"/api/v1/issues/{issue['id']}",
        json={"version": issue["version"], **body},
        headers={"X-Workspace-Id": workspace_id, **csrf(client)},
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def comment(env: ImEnv, issue_id: str, body: str, expect: int = 201) -> dict[str, Any]:
    response = await env.client.post(
        f"/api/v1/issues/{issue_id}/comments", json={"body": body}, headers=env.headers(write=True)
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def detail(env: ImEnv, issue_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/issues/{issue_id}", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def bob_client(
    env: ImEnv, make_client: ClientFactory, role: str = "member"
) -> tuple[uuid.UUID, Any]:
    bob_id = await add_user(env.database_url, BOB["email"], BOB["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), bob_id, role)
    context = make_client()
    client = await context.__aenter__()
    assert (await client.post("/api/v1/auth/login", json=BOB)).status_code == 200
    return bob_id, (context, client)


async def notifications_of(env: ImEnv, user_id: uuid.UUID) -> list[str]:
    rows = await run_sql(
        env.database_url,
        "SELECT coalesce(array_agg(title ORDER BY created_at), '{}') FROM workspace.notifications"
        " WHERE user_id = $1 AND type = 'issue.activity'",
        user_id,
    )
    return list(rows)


async def wakeups(env: ImEnv, instance_id: str) -> list[tuple[str, str]]:
    rows = await run_sql(
        env.database_url,
        "SELECT coalesce(array_agg(status || '|' || prompt ORDER BY created_at), '{}')"
        " FROM workspace.agent_wakeups WHERE instance_id = $1 AND source = 'issue'",
        uuid.UUID(instance_id),
    )
    return [tuple(r.split("|", 1)) for r in rows]


# ---------------------------------------------------------------- 人


async def test_humans_number_mention_close_and_moderate(
    env: ImEnv, make_client: ClientFactory
) -> None:
    project = await new_project(env)
    templates = (await env.client.get("/api/v1/issue-templates", headers=env.headers())).json()
    assert [t["kind"] for t in templates["items"]][:3] == ["question", "proposal", "bug"]
    first = await open_issue(env, project["id"], kind="contract", use_template=True)
    assert first["number"] == 1 and "## 调用方" in first["body"] and first["can_moderate"]
    assert [e["kind"] for e in first["events"]] == ["opened"]

    bob_id, (bob_ctx, bob) = await bob_client(env, make_client)
    try:
        # @Bob 提到 Bob；#1 引用第一个：那边的时间线记“被提到”
        second = await open_issue(env, project["id"], title="缺陷", body="@Bob 看下 #1", kind="bug")
        assert second["number"] == 2
        assert await notifications_of(env, bob_id) == ["Owner 开了 #2"]
        assert [e["kind"] for e in (await detail(env, first["id"]))["events"]] == [
            "opened",
            "mentioned",
        ]
        listed = (
            await env.client.get(
                f"/api/v1/projects/{project['id']}/issues",
                params={"kind": "bug"},
                headers=env.headers(),
            )
        ).json()
        assert [i["number"] for i in listed["items"]] == [2] and listed["open_count"] == 2

        # 关闭要写原因；重复时写明重复哪一个
        await update(env.client, env.workspace_id, first, 422, state="closed")
        await update(
            env.client, env.workspace_id, first, 422, state="closed", close_reason="duplicate"
        )
        closed = await update(
            env.client,
            env.workspace_id,
            second,
            state="closed",
            close_reason="duplicate",
            duplicate_of=1,
        )
        assert (closed["state"], closed["close_reason"], closed["duplicate_of"]) == (
            "closed",
            "duplicate",
            first["id"],
        )
        await update(env.client, env.workspace_id, second, 409, title="旧版本")  # 乐观并发

        # Bob（member）：能开、评论；不能关闭别人开的、不能锁定、不能隐藏、不能删除
        ws = env.workspace_id
        mine = await bob.post(
            f"/api/v1/projects/{project['id']}/issues",
            json={"title": "Bob 的提问", "kind": "question"},
            headers={"X-Workspace-Id": ws, **csrf(bob)},
        )
        assert mine.status_code == 201, mine.text
        first = await detail(env, first["id"])
        assert first["can_close"] is True
        bob_view = await bob.get(f"/api/v1/issues/{first['id']}", headers={"X-Workspace-Id": ws})
        assert bob_view.json()["can_close"] is False and bob_view.json()["can_moderate"] is False
        await update(bob, ws, first, 403, state="closed", close_reason="resolved")
        await update(bob, ws, first, 403, locked=True)
        own = await update(bob, ws, mine.json(), state="closed", close_reason="wontfix")
        assert own["state"] == "closed"
        said = await bob.post(
            f"/api/v1/issues/{first['id']}/comments",
            json={"body": "我来看"},
            headers={"X-Workspace-Id": ws, **csrf(bob)},
        )
        assert said.status_code == 201
        hide = f"/api/v1/issues/{first['id']}/comments/{said.json()['id']}/hide"
        assert (
            await bob.post(hide, headers={"X-Workspace-Id": ws, **csrf(bob)})
        ).status_code == 403
        assert (
            await bob.delete(
                f"/api/v1/issues/{first['id']}", headers={"X-Workspace-Id": ws, **csrf(bob)}
            )
        ).status_code == 403
        # 作者（Owner）收到评论通知
        assert await notifications_of(env, env.owner_id) == ["Bob 评论了 #1"]
    finally:
        await bob_ctx.__aexit__(None, None, None)

    # 管理员隐藏评论：正文为空、不物理删除
    hidden = await env.client.post(hide, headers=env.headers(write=True))
    assert hidden.status_code == 200 and hidden.json()["body"] is None and hidden.json()["hidden"]
    assert (await detail(env, first["id"]))["comments"][0]["hidden"] is True

    # 密钥拦截
    leaked = await comment(env, first["id"], "token ghp_" + "a" * 36, expect=422)
    assert leaked["error"]["code"] == "SECRET_BLOCKED"

    # 全局搜索收录标题；删除后搜不到、读不到
    await env.app.state.events.drain()
    found = await env.client.get(
        "/api/v1/search", params={"q": "接口怎么约定"}, headers=env.headers()
    )
    assert found.status_code == 200, found.text
    hits = [h for h in found.json()["items"] if h["resource_type"] == "project_issue"]
    assert hits and hits[0]["title"].startswith("#1 ")
    deleted = await env.client.delete(
        f"/api/v1/issues/{first['id']}", headers=env.headers(write=True)
    )
    assert deleted.status_code == 204
    gone = await env.client.get(f"/api/v1/issues/{first['id']}", headers=env.headers())
    assert gone.status_code == 404

    # Issue 提示开关：管理员打开 / 关闭，写审计
    put = await env.client.put(
        f"/api/v1/projects/{project['id']}/issue-notices",
        json={"enabled": True},
        headers=env.headers(write=True),
    )
    assert put.status_code == 200 and put.json() == {"enabled": True}
    shown = await env.client.get(f"/api/v1/projects/{project['id']}", headers=env.headers())
    assert shown.json()["issue_notices"] is True
    # 正文里的 #编号 按项目内编号读；删除的、不存在的 404
    by_number = await env.client.get(
        f"/api/v1/projects/{project['id']}/issues/2", headers=env.headers()
    )
    assert by_number.status_code == 200 and by_number.json()["title"] == "缺陷"
    for number in (1, 99):
        missing = await env.client.get(
            f"/api/v1/projects/{project['id']}/issues/{number}", headers=env.headers()
        )
        assert missing.status_code == 404
    assert (
        await run_sql(
            env.database_url,
            "SELECT count(*) FROM workspace.audit_logs WHERE action = 'issues.notices_enabled'",
        )
        == 1
    )


async def test_viewers_only_read(env: ImEnv, make_client: ClientFactory) -> None:
    project = await new_project(env)
    issue = await open_issue(env, project["id"])
    _, (ctx, viewer) = await bob_client(env, make_client, role="viewer")
    try:
        ws = env.workspace_id
        seen = await viewer.get(f"/api/v1/issues/{issue['id']}", headers={"X-Workspace-Id": ws})
        assert seen.status_code == 200
        denied = await viewer.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body": "我也说一句"},
            headers={"X-Workspace-Id": ws, **csrf(viewer)},
        )
        assert denied.status_code == 403
    finally:
        await ctx.__aexit__(None, None, None)


# ---------------------------------------------------------------- 智能体


async def test_agents_use_issue_tools(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, token_b = await agent(env, "乙")
    _, token_r = await agent(env, "丙", scope="read")
    project = await new_project(env)
    task = await new_task(env, project["id"])
    await join(env, project["id"], a["id"])
    await join(env, project["id"], b["id"])
    await run_sql(
        env.database_url,
        "UPDATE workspace.project_agents SET access = 'read' WHERE instance_id = $1",
        uuid.UUID(b["id"]),
    )
    # 丙没参与：当作不存在
    hidden = tool_error(await call(env, token_r, "issue_list", {"project_id": project["id"]}))
    assert hidden["code"] == "NOT_FOUND"
    # 乙只读参与：能读不能开
    assert ok(await call(env, token_b, "issue_list", {"project_id": project["id"]}))["items"] == []
    denied = tool_error(
        await call(env, token_b, "issue_create", {"project_id": project["id"], "title": "想开"})
    )
    assert denied["code"] == "READ_ONLY"

    # 甲开一个接口约定：套模板，指派给乙（项目里的智能体）和管理员，关联任务
    created = ok(
        await call(
            env,
            token_a,
            "issue_create",
            {
                "project_id": project["id"],
                "title": "用户接口的字段",
                "kind": "contract",
                "use_template": True,
                "assignee_instance_ids": [b["id"]],
                "assignee_user_ids": [str(env.owner_id)],
                "task_ids": [task["id"]],
                "client_key": "contract-1",
            },
        )
    )
    assert created["status"] == "created" and created["number"] == 1
    again = ok(
        await call(
            env,
            token_a,
            "issue_create",
            {"project_id": project["id"], "title": "用户接口的字段", "client_key": "contract-1"},
        )
    )
    assert again["status"] == "already_created" and again["issue_id"] == created["issue_id"]
    assert await notifications_of(env, env.owner_id) == ["甲 开了 #1"]
    # 被指派的乙排了一次 Issue 唤醒（提示词不含正文）
    assert [s for s, _ in await wakeups(env, b["id"])] == ["pending"]
    assert "用户接口的字段" not in (await wakeups(env, b["id"]))[0][1]

    # 智能体不能指派给普通成员；不能写密钥；不能锁定
    member_id = await add_user(env.database_url, "m@example.com", "member password 123456")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), member_id, "member")
    bad = tool_error(
        await call(
            env,
            token_a,
            "issue_create",
            {"project_id": project["id"], "title": "x", "assignee_user_ids": [str(member_id)]},
        )
    )
    assert bad["code"] == "VALIDATION_FAILED"
    secret = tool_error(
        await call(
            env,
            token_a,
            "issue_create",
            {"project_id": project["id"], "title": "x", "body": "key ghp_" + "b" * 36},
        )
    )
    assert secret["code"] == "SECRET_BLOCKED"
    lock = tool_error(
        await call(env, token_a, "issue_update", {"issue_id": created["issue_id"], "locked": True})
    )
    assert lock["code"] == "VALIDATION_FAILED"

    # 乙的收件箱有它；读过就不在了；get_task 附上未关闭的 Issue
    inbox = ok(await call(env, token_b, "issue_inbox", {}))
    assert [i["number"] for i in inbox["items"]] == [1] and inbox["items"][0]["unread"]
    got = ok(await call(env, token_b, "issue_get", {"project_id": project["id"], "number": 1}))
    assert "## 调用方" in got["untrusted"]["body"] and got["can_comment"] is False  # 只读
    assert got["issue"]["assignees"][0]["untrusted"]["name"] in ("乙", "Owner")
    assert ok(await call(env, token_b, "issue_inbox", {}))["items"] == []
    task_view = ok(await call(env, token_a, "get_task", {"task_id": task["id"]}))
    assert [i["number"] for i in task_view["issues"]] == [1]

    # 管理员在评论里 @甲：甲排唤醒；第二条动态合并进同一个唤醒
    await comment(env, created["issue_id"], "@甲 字段名用下划线")
    await comment(env, created["issue_id"], "@甲 还有分页")
    assert [s for s, _ in await wakeups(env, a["id"])] == ["pending"]
    assert "有 2 条" in (await wakeups(env, a["id"]))[0][1]

    # 连续 10 条智能体评论后暂停；人评论后恢复
    d, token_d = await agent(env, "丁")
    await join(env, project["id"], d["id"])
    for i in range(10):
        token = token_a if i % 2 == 0 else token_d
        said = ok(
            await call(
                env, token, "issue_comment", {"issue_id": created["issue_id"], "body": f"第 {i} 条"}
            )
        )
    assert said["agent_comments_left"] == 0
    paused = tool_error(
        await call(env, token_a, "issue_comment", {"issue_id": created["issue_id"], "body": "再说"})
    )
    assert paused["code"] == "ISSUE_PAUSED"
    await comment(env, created["issue_id"], "人来了")
    resumed = ok(
        await call(
            env,
            token_a,
            "issue_comment",
            {"issue_id": created["issue_id"], "body": "好的", "client_key": "c-1"},
        )
    )
    retried = ok(
        await call(
            env,
            token_a,
            "issue_comment",
            {"issue_id": created["issue_id"], "body": "好的", "client_key": "c-1"},
        )
    )
    assert (
        retried["status"] == "already_commented" and retried["comment_id"] == resumed["comment_id"]
    )

    # 锁定后智能体不能评论
    current = await detail(env, created["issue_id"])
    await update(env.client, env.workspace_id, current, locked=True)
    locked = tool_error(
        await call(env, token_a, "issue_comment", {"issue_id": created["issue_id"], "body": "嗯"})
    )
    assert locked["code"] == "ISSUE_LOCKED"

    # 甲关闭自己开的（带原因），加标签；丁（没开、没被指派）关不了
    current = await detail(env, created["issue_id"])
    await update(env.client, env.workspace_id, current, locked=False)
    cannot = tool_error(
        await call(
            env,
            token_d,
            "issue_update",
            {"issue_id": created["issue_id"], "state": "closed", "close_reason": "resolved"},
        )
    )
    assert cannot["code"] == "FORBIDDEN_ROLE"
    closed = ok(
        await call(
            env,
            token_a,
            "issue_update",
            {
                "issue_id": created["issue_id"],
                "state": "closed",
                "close_reason": "resolved",
                "add_labels": ["api"],
            },
        )
    )
    assert closed["status"] == "updated" and closed["issue"]["state"] == "closed"
    assert closed["issue"]["untrusted"]["labels"] == ["api"]

    # 执行记录：每次调用一条，正文只记长度
    rows = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions WHERE tool_name = 'issue_comment'",
    )
    assert rows >= 12
    leaked = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions"
        " WHERE row_to_json(ai_tool_executions)::text LIKE '%字段名用下划线%'"
        " OR row_to_json(ai_tool_executions)::text LIKE '%第 3 条%'",
    )
    assert leaked == 0


async def test_blockers_stop_agents_from_completing(env: ImEnv) -> None:
    a, token = await agent(env, "甲")
    project = await new_project(env)
    task = await new_task(env, project["id"])
    await join(env, project["id"], a["id"])
    await run_sql(
        env.database_url,
        "UPDATE workspace.tasks SET assignee_instance_id = $1 WHERE id = $2",
        uuid.UUID(a["id"]),
        uuid.UUID(task["id"]),
    )
    blocker = await open_issue(
        env, project["id"], title="等数据库账号", kind="blocker", task_ids=[task["id"]]
    )
    other = await open_issue(env, project["id"], title="顺手的问题", task_ids=[task["id"]])
    blocked = tool_error(await call(env, token, "complete_task", {"task_id": task["id"]}))
    assert blocked["code"] == "TASK_BLOCKED" and "#1" in blocked["message"]
    assert "#2" not in blocked["message"]  # 不是“阻塞”类型的不算
    linked = (
        await env.client.get(f"/api/v1/tasks/{task['id']}/issues", headers=env.headers())
    ).json()
    assert [i["number"] for i in linked["items"]] == [1, 2] and linked["open_count"] == 2

    # README：项目“沟通”列出未关闭的 Issue；任务“讨论”列出关联的
    await refresh(env)
    body = (await project_readme(env, project["id"]))["note"]["body"]
    assert "- Issues：未关闭 2 个" in body and "#1（阻塞）等数据库账号" in body
    t_body = (await task_readme(env, task["id"]))["note"]["body"]
    assert "- #1（阻塞，未关闭）等数据库账号" in t_body

    await update(env.client, env.workspace_id, blocker, state="closed", close_reason="resolved")
    await refresh(env)
    t_body = (await task_readme(env, task["id"]))["note"]["body"]
    assert "- #1（阻塞，已关闭）等数据库账号" in t_body
    done = ok(await call(env, token, "complete_task", {"task_id": task["id"]}))
    assert done["status"] == "completed"
    assert other["state"] == "open"


async def test_people_can_complete_despite_blockers_and_release_hint(env: ImEnv) -> None:
    project = await new_project(env)
    task = await new_task(env, project["id"])
    await open_issue(env, project["id"], kind="blocker", task_ids=[task["id"]])
    done = await patch(env, task, done=True)
    assert done["done"] is True
    _, token = await agent(env, "甲")
    project2 = await new_project(env, title="第二个")
    task2 = await new_task(env, project2["id"])
    await patch(env, task2, claim_mode="open")
    ok(await call(env, token, "claim_task", {"task_id": task2["id"]}))
    released = ok(
        await call(env, token, "release_task", {"task_id": task2["id"], "reason": "换人做"})
    )
    assert "issue_create" in released["next_step"] and "handoff" in released["next_step"]


async def test_agent_issue_limits(env: ImEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(issues_domain, "ISSUES_PER_HOUR", 2)
    a, token = await agent(env, "甲")
    project = await new_project(env)
    await join(env, project["id"], a["id"])
    for i in range(2):
        ok(
            await call(
                env, token, "issue_create", {"project_id": project["id"], "title": f"第 {i}"}
            )
        )
    limited = tool_error(
        await call(env, token, "issue_create", {"project_id": project["id"], "title": "第 3"})
    )
    assert limited["code"] == "RATE_LIMITED"
    # 别的工作空间看不到：换一个不存在的项目号也是 NOT_FOUND
    missing = tool_error(await call(env, token, "issue_list", {"project_id": str(uuid.uuid4())}))
    assert missing["code"] == "NOT_FOUND"
