"""项目与任务的 README（T-0122，docs/v2/AGENT_MESSAGING_PLAN.md 第 9 节）。

真实 PostgreSQL + Redis。覆盖：

- 打开时按模板生成（项目 README 置顶在根目录，任务 README 在“任务导览”，标题带任务编号前 8 位）；
- 数据变了登记进刷新队列，Worker 合并更新：只替换自动部分，人写的不动，
  没变化不写版本，作者是工作台；
  删掉标记的那一块不再更新；同一篇 1 分钟内最多一次；
- 不能新建 readme 类型、不能改类型、不能移动、不能删除；
  任务删除时 README 进回收站；任务换项目 README 跟着走；
- 图谱默认不显示任务 README；对账补建缺的 README。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import readme, readme_marks
from tests.helpers import run_sql
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import agent, patch

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


async def new_project(env: ImEnv, title: str = "发布 2.0", description: str = "") -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/projects",
        json={"title": title, "description": description},
        headers=env.headers(write=True),
    )
    assert response.status_code == 201, response.text
    project: dict[str, Any] = response.json()
    return project


async def new_task(env: ImEnv, project_id: str, **fields: Any) -> dict[str, Any]:
    response = await env.client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "整理发布说明", **fields},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    task: dict[str, Any] = response.json()
    return task


async def project_readme(env: ImEnv, project_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/projects/{project_id}/readme", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def task_readme(env: ImEnv, task_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/tasks/{task_id}/readme", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def refresh(env: ImEnv) -> int:
    """跑一次 Worker 的刷新（先等提交后的登记写进 Redis，再把 1 分钟的限制清掉）。"""
    app = env.app
    await app.state.events.drain()
    redis = app.state.redis
    async for key in redis.scan_iter("readme:ran:*"):
        await redis.delete(key)
    later = datetime.now(UTC) + timedelta(seconds=30)
    total = 0
    for _ in range(3):  # s: / i: / w: 展开成项目键后再跑一轮
        total += await readme.process_due(
            app.state.sessionmaker, app.state.settings, redis, now=later
        )
    return total


async def test_project_and_task_readmes_are_generated_and_merged(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    project = await new_project(env, description="把 2.0 发出去。\n\n第二段不进简介。")
    first = await project_readme(env, project["id"])
    note = first["note"]
    assert (note["title"], note["type"], note["pinned"], note["folder"]) == (
        "README",
        "readme",
        True,
        "",
    )
    assert first["auto_sections"] == ["成员", "沟通", "任务概况", "知识库"]
    assert "把 2.0 发出去。" in note["body"] and "第二段不进简介" not in note["body"]
    assert "- 管理员：" in note["body"] and "- 项目简报：还没有" in note["body"]
    again = await project_readme(env, project["id"])
    assert again["note"]["id"] == note["id"] and again["note"]["version"] == 1  # 不会重复生成

    task = await new_task(env, project["id"], description="要做：\n- [ ] 写说明\n- [ ] 发公告")
    t = (await task_readme(env, task["id"]))["note"]
    assert t["title"] == f"整理发布说明（任务 {task['id'][:8]}）" and t["folder"] == "任务导览"
    assert "## 验收标准\n\n- [ ] 写说明\n- [ ] 发公告" in t["body"]
    assert "- 执行者：还没有分配" in t["body"]
    assert "[[README|项目 README]]" in t["body"]
    assert {link["note_id"] for link in t["links"]} == {note["id"]}

    # 人补充了“代码与资源”，然后数据变了：自动部分更新，人写的不动；作者是工作台
    edited = note["body"].replace(
        "（由人或智能体填写：仓库、分支约定、部署方式、文档链接）", "- 仓库：git.example/release"
    )
    saved = await env.client.patch(
        f"/api/v1/knowledge/notes/{note['id']}",
        json={"version": again["note"]["version"], "body": edited},
        headers=env.headers(write=True),
    )
    assert saved.status_code == 200, saved.text
    await patch(env, task, assignee_instance_id=a["id"])
    assert await refresh(env) >= 1
    after = (await project_readme(env, project["id"]))["note"]
    assert "- 仓库：git.example/release" in after["body"]
    assert f"[[task:{task['id']}|整理发布说明]]：智能体「甲」" in after["body"]
    assert after["version"] == 3
    author = await run_sql(
        env.database_url,
        "SELECT coalesce(author_user::text, author_instance::text, 'system') || ':' || reason"
        " FROM workspace.knowledge_note_revisions WHERE note_id = $1 AND version = 3",
        uuid.UUID(note["id"]),
    )
    assert author == "system:工作台自动更新"
    t2 = (await task_readme(env, task["id"]))["note"]
    assert "- 执行者：智能体「甲」" in t2["body"] and t2["version"] == 2
    # 没有变化：不写新版本
    await readme_marks.push(env.app.state.redis, [f"p:{project['id']}", f"t:{task['id']}"])
    await refresh(env)
    assert (await project_readme(env, project["id"]))["note"]["version"] == 3

    # 删掉“任务概况”的标记：那一块不再更新
    current = (await project_readme(env, project["id"]))["note"]
    handwritten = current["body"].replace("<!-- myws:auto 任务概况 -->\n", "")
    handwritten = handwritten.replace("\n<!-- /myws:auto -->\n\n## 知识库", "\n\n## 知识库")
    saved = await env.client.patch(
        f"/api/v1/knowledge/notes/{current['id']}",
        json={"version": current["version"], "body": handwritten},
        headers=env.headers(write=True),
    )
    assert saved.status_code == 200, saved.text
    await new_task(env, project["id"], title="第二个任务")
    await refresh(env)
    latest = await project_readme(env, project["id"])
    assert "任务概况" not in latest["auto_sections"]
    assert "第二个任务" not in latest["note"]["body"]


async def test_readme_is_protected_and_follows_its_task(env: ImEnv) -> None:
    project = await new_project(env)
    other = await new_project(env, title="文档")
    task = await new_task(env, project["id"])
    note = (await task_readme(env, task["id"]))["note"]
    space = (await project_readme(env, project["id"]))["note"]["space_id"]
    headers = env.headers(write=True)

    created = await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": space, "title": "我的 README", "type": "readme"},
        headers=headers,
    )
    assert created.status_code == 422 and created.json()["error"]["code"] == "SYSTEM_NOTE"
    retype = await env.client.patch(
        f"/api/v1/knowledge/notes/{note['id']}",
        json={"version": note["version"], "type": "record"},
        headers=headers,
    )
    assert retype.status_code == 422 and retype.json()["error"]["code"] == "SYSTEM_NOTE"
    refolder = await env.client.patch(
        f"/api/v1/knowledge/notes/{note['id']}",
        json={"version": note["version"], "folder": "别处"},
        headers=headers,
    )
    assert refolder.status_code == 422 and refolder.json()["error"]["code"] == "SYSTEM_NOTE"
    deleted = await env.client.delete(
        f"/api/v1/knowledge/notes/{note['id']}?version={note['version']}", headers=headers
    )
    assert deleted.status_code == 422 and deleted.json()["error"]["code"] == "SYSTEM_NOTE"

    # 改标题、移到别的项目：README 跟着改名、移到新项目的知识库
    await patch(env, task, title="改了名的任务", project_id=other["id"])
    await refresh(env)
    moved = (await task_readme(env, task["id"]))["note"]
    other_space = (await project_readme(env, other["id"]))["note"]["space_id"]
    assert moved["id"] == note["id"] and moved["space_id"] == other_space
    assert moved["title"] == f"改了名的任务（任务 {task['id'][:8]}）"

    # 图谱默认不显示任务 README
    graph = (await env.client.get("/api/v1/knowledge/graph", headers=env.headers())).json()
    titles = {n["title"] for n in graph["nodes"] if n["kind"] == "note"}
    assert "README" in titles and moved["title"] not in titles
    shown = (
        await env.client.get("/api/v1/knowledge/graph?task_readmes=true", headers=env.headers())
    ).json()
    assert moved["title"] in {n["title"] for n in shown["nodes"]}

    # 删除任务：README 进回收站
    current = (await env.client.get(f"/api/v1/tasks/{task['id']}", headers=env.headers())).json()
    removed = await env.client.delete(
        f"/api/v1/tasks/{task['id']}?version={current['version']}",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert removed.status_code == 204, removed.text
    trashed = await run_sql(
        env.database_url,
        "SELECT deleted_at IS NOT NULL FROM workspace.knowledge_notes WHERE id = $1",
        uuid.UUID(note["id"]),
    )
    assert trashed is True


async def test_reconcile_creates_missing_readmes(env: ImEnv) -> None:
    project = await new_project(env)
    task = await new_task(env, project["id"])
    app = env.app
    assert await readme.reconcile(app.state.sessionmaker, app.state.settings, app.state.redis) == 2
    assert await readme.reconcile(app.state.sessionmaker, app.state.settings, app.state.redis) == 0
    count = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.knowledge_notes"
        " WHERE type = 'readme' AND deleted_at IS NULL",
    )
    assert count == 2
    assert (await task_readme(env, task["id"]))["note"]["version"] == 1


async def join(env: ImEnv, project_id: str, instance_id: str) -> None:
    response = await env.client.put(
        f"/api/v1/projects/{project_id}/agents/{instance_id}", headers=env.headers(write=True)
    )
    assert response.status_code == 200, response.text


async def test_agent_reads_and_updates_readmes(env: ImEnv) -> None:
    from tests.helpers import tool_error
    from tests.test_task_claims_api import call, ok

    a, token = await agent(env, "甲")
    _, token_b = await agent(env, "乙")
    reader, token_r = await agent(env, "丙", scope="read")
    project = await new_project(env)
    task = await new_task(env, project["id"])
    await join(env, project["id"], a["id"])
    await join(env, project["id"], reader["id"])

    # 没参与的项目：当作不存在
    hidden = await call(env, token_b, "readme_get", {"task_id": task["id"]})
    assert tool_error(hidden) == {
        "code": "NOT_FOUND",
        "message": "没有这个项目或任务，或者你没有参与这个项目",
    } or tool_error(hidden)["message"].startswith("没有这个项目")
    both = await call(
        env, token, "readme_get", {"task_id": task["id"], "project_id": project["id"]}
    )
    assert tool_error(both)["code"] == "VALIDATION_FAILED"

    got = ok(await call(env, token, "readme_get", {"task_id": task["id"]}))
    assert got["project_id"] == project["id"] and got["task_id"] == task["id"]
    assert got["readme"]["auto_sections"] == ["状态", "讨论", "执行记录"]
    assert "进展与交接" in got["sections"]
    assert got["readme"]["untrusted"]["title"].startswith("整理发布说明（任务 ")
    project_got = ok(await call(env, token, "readme_get", {"project_id": project["id"]}))
    assert project_got["task_id"] is None and "成员" in project_got["readme"]["auto_sections"]

    updated = ok(
        await call(
            env,
            token,
            "readme_update",
            {
                "task_id": task["id"],
                "section": "进展与交接",
                "text": "接口写完了，还差文档",
                "mode": "replace",
            },
        )
    )
    assert updated["status"] == "updated" and updated["version"] == got["readme"]["version"] + 1
    again = ok(
        await call(
            env,
            token,
            "readme_update",
            {
                "task_id": task["id"],
                "section": "进展与交接",
                "text": "接口写完了，还差文档",
                "mode": "replace",
            },
        )
    )
    assert again["status"] == "unchanged"
    auto = await call(
        env, token, "readme_update", {"task_id": task["id"], "section": "状态", "text": "x"}
    )
    assert tool_error(auto)["code"] == "AUTO_SECTION"
    body = (await task_readme(env, task["id"]))["note"]["body"]
    assert "## 进展与交接\n\n接口写完了，还差文档" in body
    author = await run_sql(
        env.database_url,
        "SELECT author_instance FROM workspace.knowledge_note_revisions"
        " WHERE note_id = $1 ORDER BY version DESC LIMIT 1",
        uuid.UUID(updated["note_id"]),
    )
    assert str(author) == a["id"]
    # 只读实例：看得到，改不了
    assert ok(await call(env, token_r, "readme_get", {"project_id": project["id"]}))
    denied = await call(
        env, token_r, "readme_update", {"project_id": project["id"], "section": "约定", "text": "x"}
    )
    assert tool_error(denied)["code"] == "FORBIDDEN_SCOPE"
    # 执行记录里不存正文
    leaked = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions e"
        " WHERE tool_name = 'readme_update' AND row_to_json(e)::text LIKE '%接口写完了%'",
    )
    assert leaked == 0

    # list_projects 给参与的项目附上 README；get_task 附上任务 README
    listed = ok(await call(env, token, "list_projects", {}))
    item = next(p for p in listed["items"] if p["id"] == project["id"])
    assert item["readme"]["note_id"] == project_got["readme"]["note_id"]
    assert ok(await call(env, token_b, "list_projects", {}))["items"][0]["readme"] is None
    detail = ok(await call(env, token, "get_task", {"task_id": task["id"]}))
    assert detail["readme"]["note_id"] == updated["note_id"]


async def test_claim_returns_the_task_readme(env: ImEnv) -> None:
    from tests.test_task_claims_api import call, ok

    _, token = await agent(env, "甲")
    project = await new_project(env)
    task = await new_task(env, project["id"], description="- [ ] 跑通")
    opened = await patch(env, task, claim_mode="open")
    assert opened["claimable"] is True
    claimed = ok(await call(env, token, "claim_task", {"task_id": task["id"]}))
    assert "readme_update" in claimed["next_step"]
    assert "- [ ] 跑通" in claimed["readme"]["untrusted"]["body"]
    assert claimed["readme"]["auto_sections"] == ["状态", "讨论", "执行记录"]
    done = ok(await call(env, token, "complete_task", {"task_id": task["id"]}))
    assert "进展与交接" in done["next_step"]
    # 领取、完成写进任务动态 → 刷新后“执行记录”里有
    await refresh(env)
    body = (await task_readme(env, task["id"]))["note"]["body"]
    assert "智能体「甲」领取了" in body and "智能体「甲」完成了" in body
