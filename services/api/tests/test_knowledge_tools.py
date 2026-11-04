"""智能体的知识库工具（T-0098，docs/v2/KNOWLEDGE_PLAN.md 3.7、3.8）。

经 POST /mcp，真实 PostgreSQL / Redis。

覆盖：工具清单按实例的权限范围；写入直接生效（不进审批）、留版本、幂等重试；知识库权限
（公共 / 自己的 / 参与的项目 / 别的智能体）；简报只有人能改；密钥拦截；写入开关；
删除上限与自动暂停（并通知管理员）；人修改智能体写的内容时回写《收到的纠正》；
同名、相近标题两篇都标冲突；通用 search 工具不返回笔记；调用记录的风险等级是 knowledge。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain import knowledge_base
from tests.helpers import tool_error
from tests.test_mcp_api import SETTINGS, call, issue_token, new_instance, records, rpc
from tests.workspace_support import Env, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        yield value


async def spaces(env: Env) -> dict[str, dict[str, Any]]:
    """按 名称 → 知识库。"""
    listed = await env.get("/api/v1/knowledge/spaces")
    return {s["name"]: s for s in listed["items"]}


def ok(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    data: dict[str, Any] = result["structuredContent"]
    return data


def err(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    code: str = tool_error(result)["code"]
    return code


def write_args(space_id: str, title: str, body: str = "正文", **extra: Any) -> dict[str, Any]:
    return {
        "space_id": space_id,
        "title": title,
        "body": body,
        "reason": "测试写入",
        "idempotency_key": str(uuid.uuid4()),
        **extra,
    }


async def agent(env: Env, name: str, scope: str = "read_write") -> tuple[str, str]:
    instance = await new_instance(env, scope=scope, name=name)
    return instance, await issue_token(env, instance)


async def test_tool_list_follows_scope_and_writes_apply_without_approval(env: Env) -> None:
    instance, token = await agent(env, "ops-claw")
    _, reader = await agent(env, "reader", scope="read")
    names = {t["name"] for t in ok_list(await call_list(env, token))}
    assert {"knowledge_search", "knowledge_write", "knowledge_delete"} <= names
    read_names = {t["name"] for t in ok_list(await call_list(env, reader))}
    assert "knowledge_search" in read_names and "knowledge_write" not in read_names

    own = (await spaces(env))["ops-claw"]
    written = ok(
        await call(
            env,
            token,
            "knowledge_write",
            write_args(own["id"], "重启 api 的正确步骤", "先报告再重启"),
        )
    )
    assert written["created"] is True and written["version"] == 1 and written["replayed"] is False
    note = await env.get(f"/api/v1/knowledge/notes/{written['note_id']}")
    assert note["updated_by"] == {"kind": "agent", "id": instance, "name": "ops-claw"}
    assert note["source_kind"] == "observation" and note["reason"] == "测试写入"
    # 没有待审批记录：直接执行；调用记录的风险等级是 knowledge
    rows = await records(env)
    assert [(r["tool_name"], r["risk_level"], r["status"]) for r in rows][:1] == [
        ("knowledge_write", "knowledge", "succeeded")
    ]
    assert "body" not in rows[0]["arguments"]  # 正文不进调用记录
    assert (await env.get("/api/v1/ai/tool-executions", status=["pending_approval"]))["items"] == []

    # 修改要带 base_version；读到的版本过期时 VERSION_CONFLICT
    args = write_args(
        own["id"],
        "重启 api 的正确步骤",
        "先报告，等审批，再重启",
        note_id=written["note_id"],
        base_version=1,
    )
    updated = ok(await call(env, token, "knowledge_write", args))
    assert updated["version"] == 2 and updated["created"] is False
    stale = write_args(
        own["id"], "重启 api 的正确步骤", "x", note_id=written["note_id"], base_version=1
    )
    assert err(await call(env, token, "knowledge_write", stale)) == "VERSION_CONFLICT"


async def call_list(env: Env, token: str) -> dict[str, Any]:
    response = await rpc(env, token, "tools/list")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def ok_list(result: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = result["tools"]
    return tools


async def test_idempotent_retries_replay_and_reused_keys_fail(env: Env) -> None:
    _, token = await agent(env, "doc-claw")
    own = (await spaces(env))["doc-claw"]
    args = write_args(own["id"], "写作风格偏好", "短句，先结论")
    first = ok(await call(env, token, "knowledge_write", args))
    again = ok(await call(env, token, "knowledge_write", args))
    assert again["replayed"] is True and again["note_id"] == first["note_id"]
    assert (await env.get(f"/api/v1/knowledge/notes/{first['note_id']}"))["version"] == 1
    reused = {**args, "body": "换了内容"}
    assert err(await call(env, token, "knowledge_write", reused)) == "IDEMPOTENCY_KEY_REUSED"


async def test_space_access_rules(env: Env) -> None:
    alpha, alpha_token = await agent(env, "alpha")
    _, beta_token = await agent(env, "beta")
    project = await env.add_project("工作台 V2")
    listed = await spaces(env)
    project_space, public, alpha_space = listed["工作台 V2"], listed["公共知识库"], listed["alpha"]

    # 公共库：默认读写
    ok(await call(env, beta_token, "knowledge_write", write_args(public["id"], "值班制度")))
    # 别的智能体的知识库：默认只读
    ok(
        await call(
            env,
            alpha_token,
            "knowledge_write",
            write_args(alpha_space["id"], "alpha 的经验", "磁盘告警处理"),
        )
    )
    assert (
        err(
            await call(
                env, beta_token, "knowledge_write", write_args(alpha_space["id"], "beta 写给 alpha")
            )
        )
        == "READ_ONLY"
    )
    found = ok(await call(env, beta_token, "knowledge_search", {"query": "磁盘"}))
    assert [h["untrusted"]["title"] for h in found["items"]] == ["alpha 的经验"]
    assert found["items"][0]["space"]["access"] == "read"
    assert found["items"][0]["origin"] == {
        "by": "agent",
        "agent_name": "alpha",
        "conflict": False,
        "external": False,
    }
    # alpha 把自己的知识库设为别人不可读
    hidden = await env.client.patch(
        f"/api/v1/knowledge/spaces/{alpha_space['id']}",
        json={"version": alpha_space["version"], "agent_access": "none"},
        headers=env.headers(write=True),
    )
    assert hidden.status_code == 200
    assert ok(await call(env, beta_token, "knowledge_search", {"query": "磁盘"}))["items"] == []
    assert (
        err(await call(env, beta_token, "knowledge_list", {"space_id": alpha_space["id"]}))
        == "NOT_FOUND"
    )

    # 项目库：不参与就看不到；只读参与不能写；读写参与可以写
    assert (
        err(
            await call(
                env, alpha_token, "knowledge_write", write_args(project_space["id"], "风险清单")
            )
        )
        == "NOT_FOUND"
    )
    for access, expected in (("read", "READ_ONLY"), ("read_write", None)):
        put = await env.client.put(
            f"/api/v1/knowledge/spaces/{project_space['id']}/agents/{alpha}",
            json={"access": access},
            headers=env.headers(write=True),
        )
        assert put.status_code == 200, put.text
        result = await call(
            env,
            alpha_token,
            "knowledge_write",
            write_args(project_space["id"], f"风险清单 {access}"),
        )
        if expected:
            assert err(result) == expected
        else:
            ok(result)
    visible = ok(await call(env, alpha_token, "knowledge_list", {}))
    assert {s["untrusted"]["name"]: s["access"] for s in visible["spaces"]} == {
        "公共知识库": "read_write",
        "alpha": "read_write",
        "beta": "read",  # 别的智能体的知识库默认只读（方案第 12 节第 3 条）
        "工作台 V2": "read_write",
    }
    # 归档项目：智能体只读
    archived = await env.client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": project["version"], "archived": True},
        headers=env.headers(write=True),
    )
    assert archived.status_code == 200, archived.text
    assert (
        err(
            await call(
                env, alpha_token, "knowledge_write", write_args(project_space["id"], "归档后")
            )
        )
        == "READ_ONLY"
    )


async def test_brief_is_human_only_and_reads_are_tracked(env: Env) -> None:
    alpha, token = await agent(env, "alpha")
    await env.add_project("OpenClaw 接入")
    project_space = (await spaces(env))["OpenClaw 接入"]
    await env.client.put(
        f"/api/v1/knowledge/spaces/{project_space['id']}/agents/{alpha}",
        json={"access": "read_write"},
        headers=env.headers(write=True),
    )
    brief = await env.client.post(
        "/api/v1/knowledge/notes",
        json={
            "space_id": project_space["id"],
            "title": "项目简报",
            "type": "brief",
            "body": "目标：上线",
        },
        headers=env.headers(write=True),
    )
    assert brief.status_code == 201, brief.text
    # 智能体不能写简报，也不能改简报
    assert (
        err(
            await call(
                env,
                token,
                "knowledge_write",
                write_args(project_space["id"], "简报 2", type="brief"),
            )
        )
        == "VALIDATION_FAILED"
    )
    edit = write_args(
        project_space["id"], "项目简报", "改目标", note_id=brief.json()["id"], base_version=1
    )
    assert err(await call(env, token, "knowledge_write", edit)) == "BRIEF_HUMAN_ONLY"
    read = ok(
        await call(env, token, "knowledge_brief", {"project_id": project_space["project_id"]})
    )
    assert read["brief"]["version"] == 1 and read["brief"]["untrusted"]["body"] == "目标：上线"
    agents = (await env.get(f"/api/v1/knowledge/spaces/{project_space['id']}"))["agents"]
    assert [(a["name"], a["brief_version_read"]) for a in agents] == [("alpha", 1)]
    note = await env.get(f"/api/v1/knowledge/notes/{brief.json()['id']}")
    assert note["usage"]["reads_30d"] == 1 and note["usage"]["by_agent"][0]["name"] == "alpha"


async def test_unread_brief_is_hinted_in_notices_and_get_task(env: Env) -> None:
    alpha, token = await agent(env, "alpha")
    project = await env.add_project("工作台 V2")
    task = await env.add_task(project["id"], "发布 11 月版本")
    space = (await spaces(env))["工作台 V2"]
    await env.client.put(
        f"/api/v1/knowledge/spaces/{space['id']}/agents/{alpha}",
        json={"access": "read_write"},
        headers=env.headers(write=True),
    )
    # 还没有简报：不提示
    first = ok(await call(env, token, "get_task", {"task_id": task["id"]}))
    assert first["brief"] is None
    brief = await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": space["id"], "title": "简报", "type": "brief", "body": "目标"},
        headers=env.headers(write=True),
    )
    assert brief.status_code == 201, brief.text
    hinted = ok(await call(env, token, "get_task", {"task_id": task["id"]}))
    assert hinted["brief"] == {
        "project_id": project["id"],
        "version": 1,
        "read_version": None,
        "unread": True,
    }
    searched = ok(await call(env, token, "knowledge_search", {"query": "目标"}))
    stale = [n for n in searched["notices"] if "简报已更新" in n]
    assert stale and project["id"] in stale[0] and "工作台 V2" not in stale[0], stale
    ok(await call(env, token, "knowledge_brief", {"project_id": project["id"]}))
    after = ok(await call(env, token, "get_task", {"task_id": task["id"]}))
    assert after["brief"]["unread"] is False and after["brief"]["read_version"] == 1
    again = ok(await call(env, token, "knowledge_search", {"query": "目标"}))
    assert not any("简报已更新" in n for n in again["notices"])
    # 人更新简报后再次提示
    await env.client.patch(
        f"/api/v1/knowledge/notes/{brief.json()['id']}",
        json={"version": 1, "body": "目标：11 月上线"},
        headers=env.headers(write=True),
    )
    updated = ok(await call(env, token, "get_task", {"task_id": task["id"]}))
    assert updated["brief"]["version"] == 2 and updated["brief"]["unread"] is True


async def test_weekly_activity_for_agent_and_space(env: Env) -> None:
    alpha, token = await agent(env, "alpha")
    own = (await spaces(env))["alpha"]
    written = ok(await call(env, token, "knowledge_write", write_args(own["id"], "经验一")))
    ok(await call(env, token, "knowledge_get", {"note_id": written["note_id"]}))
    overview = await env.get(f"/api/v1/knowledge/agents/{alpha}")
    weeks = overview["weekly"]
    assert len(weeks) == 8 and weeks[0]["week_start"] < weeks[-1]["week_start"]
    assert (
        weeks[-1]["writes"] == 1 and weeks[-1]["reads_own"] == 1 and weeks[-1]["reads_shared"] == 0
    )
    human = await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": own["id"], "title": "人写的"},
        headers=env.headers(write=True),
    )
    assert human.status_code == 201
    activity = await env.get(f"/api/v1/knowledge/spaces/{own['id']}/activity")
    assert len(activity["weeks"]) == 8
    assert activity["weeks"][-1] | {"week_start": None} == {
        "week_start": None,
        "human": 1,
        "agent": 1,
    }


async def test_secrets_and_disabled_writes_are_blocked(env: Env) -> None:
    instance, token = await agent(env, "ops-claw")
    own = (await spaces(env))["ops-claw"]
    leaked = write_args(own["id"], "GitHub 同步失败", "用这个令牌：ghp_" + "b" * 36)
    assert err(await call(env, token, "knowledge_write", leaked)) == "SECRET_DETECTED"
    assert (await env.get("/api/v1/knowledge/notes"))["items"] == []
    changes = await env.get("/api/v1/knowledge/changes", kind="guard")
    assert [(c["guard"], c["author"]["name"]) for c in changes["items"]] == [
        ("secret_blocked", "ops-claw")
    ]
    assert "ghp_" not in changes["items"][0]["detail"]

    disabled = await env.client.patch(
        f"/api/v1/knowledge/spaces/{own['id']}",
        json={"version": own["version"], "writes_enabled": False},
        headers=env.headers(write=True),
    )
    assert disabled.status_code == 200
    assert (
        err(await call(env, token, "knowledge_write", write_args(own["id"], "x")))
        == "WRITES_DISABLED"
    )
    searched = ok(await call(env, token, "knowledge_search", {"query": "x"}))
    assert "管理员关闭了你的写入，现在只能读。" in searched["notices"]
    _ = instance


async def test_delete_limit_pauses_deletes_and_notifies_admins(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge_base, "AGENT_DELETES_PER_HOUR", 2)
    _, token = await agent(env, "doc-claw")
    own = (await spaces(env))["doc-claw"]
    notes = [
        ok(await call(env, token, "knowledge_write", write_args(own["id"], f"旧笔记 {i}")))
        for i in range(3)
    ]

    async def remove(item: dict[str, Any]) -> dict[str, Any]:
        args = {
            "note_id": item["note_id"],
            "base_version": item["version"],
            "reason": "已过时",
            "idempotency_key": str(uuid.uuid4()),
        }
        return await call(env, token, "knowledge_delete", args)

    assert ok(await remove(notes[0]))["purge_after"] is not None
    assert ok(await remove(notes[1]))["replayed"] is False
    assert err(await remove(notes[2])) == "DELETES_PAUSED"
    space = (await spaces(env))["doc-claw"]
    assert space["deletes_paused_at"] is not None
    assert len((await env.get("/api/v1/knowledge/trash"))["items"]) == 2
    alerts = await env.get("/api/v1/notifications")
    assert any(n["type"] == "knowledge_delete_paused" for n in alerts["items"])
    # 解除暂停后可以再删
    resumed = await env.client.patch(
        f"/api/v1/knowledge/spaces/{space['id']}",
        json={"version": space["version"], "resume_deletes": True},
        headers=env.headers(write=True),
    )
    assert resumed.status_code == 200 and resumed.json()["deletes_paused_at"] is None


async def test_write_rate_limit(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge_base, "AGENT_WRITES_PER_HOUR", 1)
    _, token = await agent(env, "monitor-claw")
    own = (await spaces(env))["monitor-claw"]
    ok(await call(env, token, "knowledge_write", write_args(own["id"], "告警阈值")))
    assert (
        err(await call(env, token, "knowledge_write", write_args(own["id"], "延迟规律")))
        == "RATE_LIMITED"
    )


async def test_human_corrections_are_written_back_to_the_agent(env: Env) -> None:
    _, token = await agent(env, "ops-claw")
    public = (await spaces(env))["公共知识库"]
    written = ok(
        await call(env, token, "knowledge_write", write_args(public["id"], "密钥轮换", "每月轮换"))
    )
    edited = await env.client.patch(
        f"/api/v1/knowledge/notes/{written['note_id']}",
        json={"version": 1, "body": "每季度轮换", "reason": "频率写错了"},
        headers=env.headers(write=True),
    )
    assert edited.status_code == 200, edited.text
    searched = ok(await call(env, token, "knowledge_search", {"query": "轮换"}))
    assert any("1 条新的纠正" in n for n in searched["notices"])
    own = (await spaces(env))["ops-claw"]
    correction = ok(
        await call(env, token, "knowledge_get", {"space_id": own["id"], "title": "收到的纠正"})
    )
    body = correction["item"]["untrusted"]["body"]
    assert "修改了《密钥轮换》（公共知识库）：频率写错了" in body
    assert correction["item"]["type"] == "correction"
    # 读过之后提醒消失；智能体不能改《收到的纠正》
    again = ok(await call(env, token, "knowledge_search", {"query": "轮换"}))
    assert not any("纠正" in n for n in again["notices"])
    tamper = write_args(
        own["id"],
        "收到的纠正",
        "清空",
        note_id=correction["item"]["id"],
        base_version=correction["item"]["version"],
    )
    assert err(await call(env, token, "knowledge_write", tamper)) == "SYSTEM_NOTE"


async def test_similar_titles_mark_both_notes_without_blocking(env: Env) -> None:
    _, token = await agent(env, "ops-claw")
    listed = await spaces(env)
    public, own = listed["公共知识库"], listed["ops-claw"]
    human = await env.client.post(
        "/api/v1/knowledge/notes",
        json={"space_id": public["id"], "title": "发布前检查清单", "body": "1. 备份"},
        headers=env.headers(write=True),
    )
    assert human.status_code == 201, human.text
    written = ok(
        await call(
            env, token, "knowledge_write", write_args(own["id"], "发布前检查清单", "我的版本")
        )
    )
    assert written["created"] is True
    assert "可能重复" in written["notices"][0] and "knowledge_get" in written["notices"][0]
    mine = await env.get(f"/api/v1/knowledge/notes/{written['note_id']}")
    theirs = await env.get(f"/api/v1/knowledge/notes/{human.json()['id']}")
    assert "公共知识库" in mine["conflict_note"] and "ops-claw" in theirs["conflict_note"]
    # 另一篇只加标记，不算一次修改
    assert theirs["version"] == 1 and theirs["updated_at"] == human.json()["updated_at"]
    # 人处理过（编辑保存）就清除
    edited = await env.client.patch(
        f"/api/v1/knowledge/notes/{human.json()['id']}",
        json={"version": 1, "body": "1. 备份\n2. 合并了智能体的版本"},
        headers=env.headers(write=True),
    )
    assert edited.status_code == 200 and edited.json()["conflict_note"] is None


async def test_generic_search_tool_does_not_return_notes(env: Env) -> None:
    _, token = await agent(env, "alpha")
    public = (await spaces(env))["公共知识库"]
    ok(
        await call(
            env, token, "knowledge_write", write_args(public["id"], "发布流程", "备份、迁移、发布")
        )
    )
    generic = ok(await call(env, token, "search", {"q": "发布流程"}))
    assert generic["items"] == []
    found = ok(await call(env, token, "knowledge_search", {"query": "发布流程"}))
    assert [h["untrusted"]["title"] for h in found["items"]] == ["发布流程"]
    got = ok(await call(env, token, "knowledge_get", {"note_id": found["items"][0]["id"]}))
    assert got["item"]["untrusted"]["body"] == "备份、迁移、发布"
