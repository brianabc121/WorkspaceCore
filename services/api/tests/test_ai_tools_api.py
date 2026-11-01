"""MCP 工具调用记录与审批（T-0057，OC-5，契约第 6 节）。

真实 PostgreSQL / Redis + 内存版 Vikunja。

覆盖：
- A14：AI 发起的删除只生成待审批记录，不执行，并留下记录；
- 审批：If-Match、只能处理未过期的待审批、高危操作的二次认证（缺密码、密码错误）、按存下的参数执行；
- 去重、每个实例最多 20 条待审批、15 分钟过期；
- 版本保护：创建审批后任务被别人改过，执行以 VERSION_CONFLICT 失败；
- 权限：实例 scope 不够记 denied；member 只看以自己身份执行的记录；角标里的待审批数。
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest

from app.domain.ai_tools import AiToolService, ToolCallError, arguments_hash
from app.models import AgentInstance
from app.schemas.ai_tools import TOOLS, PendingApproval
from tests.fake_n8n import FakeN8n
from tests.helpers import BOB, OWNER, ClientFactory, csrf, run_sql
from tests.test_automations_api import CONNECTION, REPORT, SETTINGS
from tests.vikunja_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@dataclass
class N8nEnv:
    """只接内存版 n8n 的环境（测工作流的敏感参数）。"""

    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    fake: FakeN8n

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        work = value.fake.add_project("工作")
        value.fake.add_task(work["id"], "写周报", priority=3)
        await value.connect()
        yield value


async def new_instance(env: Env | N8nEnv, *, scope: str = "read_write", name: str = "alpha") -> str:
    response = await env.client.post(
        "/api/v1/agents/instances",
        json={"name": name, "network_mode": "intranet", "scope": scope, "max_role": "member"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 201, response.text
    instance_id: str = response.json()["id"]
    return instance_id


async def call(
    env: Env | N8nEnv, instance_id: str, tool: str, args: dict[str, Any]
) -> PendingApproval:
    """直接调用领域服务（经 /mcp 的调用见 tests/test_mcp_api.py）。"""
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        service = AiToolService(db, app.state.settings, app.state.connectors, app.state.redis)
        instance = await db.get(AgentInstance, uuid.UUID(instance_id))
        assert instance is not None
        requester = await service.requester(instance)
        return await service.request(requester, TOOLS[tool], args)


async def execution(env: Env | N8nEnv, action_id: uuid.UUID) -> httpx.Response:
    return await env.client.get(f"/api/v1/ai/tool-executions/{action_id}", headers=env.headers())


async def decide(
    env: Env | N8nEnv,
    action_id: uuid.UUID,
    verb: str,
    version: int | str,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    return await env.client.post(
        f"/api/v1/ai/tool-executions/{action_id}/{verb}",
        json=body or {},
        headers={**env.headers(write=True), "If-Match": f'"{version}"'},
    )


async def test_a14_ai_delete_only_creates_a_pending_record(env: Env) -> None:
    instance = await new_instance(env)
    task = await env.task_by_title("写周报")
    pending = await call(env, instance, "delete_task", {"task_id": task["id"]})
    assert pending.status == "pending_approval" and pending.deduplicated is False
    assert "删除任务「写周报」" in pending.summary
    # 没有执行：Vikunja 与投影里任务都还在
    assert any(t["title"] == "写周报" for t in env.fake.tasks.values())
    assert (await env.task_by_title("写周报"))["id"] == task["id"]
    detail = await execution(env, pending.action_id)
    body = detail.json()
    assert detail.headers["etag"] == '"1"'
    assert (body["status"], body["risk_level"], body["tool_name"]) == (
        "pending_approval",
        "critical",
        "delete_task",
    )
    assert body["executed_at"] is None and body["source"]["inferred"] is True
    listed = await env.client.get("/api/v1/ai/tool-executions", headers=env.headers())
    assert listed.json()["pending_count"] == 1
    badges = await env.client.get("/api/v1/im/badges", headers=env.headers())
    assert badges.json()["pending_approvals"] == 1


async def test_approving_a_critical_action_needs_the_password(env: Env) -> None:
    instance = await new_instance(env)
    task = await env.task_by_title("写周报")
    pending = await call(env, instance, "delete_task", {"task_id": task["id"]})
    missing = await decide(env, pending.action_id, "approve", 1)
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "STEP_UP_REQUIRED"
    wrong = await decide(env, pending.action_id, "approve", 1, {"password": "not my password"})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "STEP_UP_FAILED"
    stale = await decide(env, pending.action_id, "approve", 7, {"password": OWNER["password"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    approved = await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "succeeded" and body["decided_by"] == str(env.owner_id)
    assert approved.headers["etag"] == f'"{body["version"]}"'
    assert not any(t["title"] == "写周报" for t in env.fake.tasks.values())
    # 已处理的不能再批
    again = await decide(env, pending.action_id, "approve", body["version"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "EXECUTION_NOT_PENDING"


async def test_write_actions_execute_the_stored_arguments(env: Env) -> None:
    instance = await new_instance(env)
    event = {
        "title": "周会",
        "timezone": "Asia/Shanghai",
        "start_at": "2026-11-03T10:00:00+08:00",
        "end_at": "2026-11-03T11:00:00+08:00",
    }
    pending = await call(env, instance, "create_calendar_event", event)
    events = await env.client.get(
        "/api/v1/calendar/events",
        params={"start": "2026-11-01T00:00:00Z", "end": "2026-11-10T00:00:00Z"},
        headers=env.headers(),
    )
    assert events.json()["items"] == []  # 待审批时没有执行
    approved = await decide(env, pending.action_id, "approve", 1)
    assert approved.status_code == 200 and approved.json()["status"] == "succeeded"
    assert approved.json()["result_summary"] == "已新建日程「周会」"
    events = await env.client.get(
        "/api/v1/calendar/events",
        params={"start": "2026-11-01T00:00:00Z", "end": "2026-11-10T00:00:00Z"},
        headers=env.headers(),
    )
    assert [e["title"] for e in events.json()["items"]] == ["周会"]


async def test_duplicates_limits_and_scope(env: Env) -> None:
    instance = await new_instance(env)
    first = await call(env, instance, "create_bookmark", {"url": "https://a.test/x", "title": "x"})
    again = await call(env, instance, "create_bookmark", {"url": "https://a.test/x", "title": "x"})
    assert again.deduplicated is True and again.action_id == first.action_id
    for i in range(19):
        await call(env, instance, "create_bookmark", {"url": f"https://a.test/{i}", "title": "y"})
    with pytest.raises(ToolCallError) as too_many:
        await call(env, instance, "create_bookmark", {"url": "https://a.test/z", "title": "z"})
    assert too_many.value.code == "TOO_MANY_PENDING"
    with pytest.raises(ToolCallError) as invalid:
        await call(env, instance, "create_bookmark", {"url": "https://a.test/z", "extra": 1})
    assert invalid.value.code == "VALIDATION_FAILED"
    reader = await new_instance(env, scope="read", name="reader")
    with pytest.raises(ToolCallError) as denied:
        await call(env, reader, "create_bookmark", {"url": "https://a.test/q", "title": "q"})
    assert denied.value.code == "FORBIDDEN_SCOPE"
    listed = await env.client.get(
        "/api/v1/ai/tool-executions", params={"status": "denied"}, headers=env.headers()
    )
    assert [i["error_code"] for i in listed.json()["items"]] == ["FORBIDDEN_SCOPE"]


async def test_expired_and_rejected_actions_cannot_be_approved(env: Env) -> None:
    instance = await new_instance(env)
    stale = await call(env, instance, "create_bookmark", {"url": "https://a.test/s", "title": "s"})
    await run_sql(
        env.database_url,
        "UPDATE workspace.ai_tool_executions SET expires_at = now() - interval '1 minute'"
        " WHERE id = $1",
        stale.action_id,
    )
    late = await decide(env, stale.action_id, "approve", 1)
    assert late.status_code == 409 and late.json()["error"]["code"] == "EXECUTION_NOT_PENDING"
    assert (await execution(env, stale.action_id)).json()["status"] == "expired"
    rejected = await call(
        env, instance, "create_bookmark", {"url": "https://a.test/r", "title": "r"}
    )
    response = await decide(env, rejected.action_id, "reject", 1, {"reason": "不需要"})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["decision_reason"]) == ("rejected", "不需要")
    after = await decide(env, rejected.action_id, "approve", response.json()["version"])
    assert after.status_code == 409
    # 处理完的记录不再保存完整参数（只留脱敏的展示版本）
    for action_id in (stale.action_id, rejected.action_id):
        purged = await run_sql(
            env.database_url,
            "SELECT arguments IS NULL AND arguments_display <> '{}'::jsonb"
            " FROM workspace.ai_tool_executions WHERE id = $1",
            action_id,
        )
        assert purged is True


async def test_execution_fails_when_the_task_changed_after_the_request(env: Env) -> None:
    instance = await new_instance(env)
    task = await env.task_by_title("写周报")
    pending = await call(env, instance, "update_task", {"task_id": task["id"], "priority": 5})
    changed = await env.client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"version": task["version"], "title": "写月报"},
        headers=env.headers(write=True, key=uuid.uuid4()),
    )
    assert changed.status_code == 200, changed.text
    approved = await decide(env, pending.action_id, "approve", 1)
    body = approved.json()
    assert (body["status"], body["error_code"]) == ("failed", "VERSION_CONFLICT")
    assert (await env.task_by_title("写月报"))["priority"] == task["priority"]  # 没有覆盖


async def test_members_see_only_their_own_records(env: Env) -> None:
    instance = await new_instance(env)
    await call(env, instance, "create_bookmark", {"url": "https://a.test/m", "title": "m"})
    async with env.member(BOB["email"], "member") as other:
        listed = await other.get(
            "/api/v1/ai/tool-executions", headers={"X-Workspace-Id": env.workspace_id}
        )
        assert listed.status_code == 200, listed.text
        # 列表只有以自己身份执行的记录；pending_count 按契约是全工作空间的待审批总数
        assert listed.json()["items"] == [] and listed.json()["pending_count"] == 1
        forbidden = await other.post(
            f"/api/v1/ai/tool-executions/{uuid.uuid4()}/approve",
            json={},
            headers={"X-Workspace-Id": env.workspace_id, "If-Match": '"1"', **csrf(other)},
        )
        assert forbidden.status_code in (403, 404)


async def test_failed_execution_after_a_rollback_still_records_the_result(env: Env) -> None:
    """日程服务在事务里查到版本不符后抛错并回滚：记录仍要落到 failed，不能停在 executing。"""
    instance = await new_instance(env)
    created = await env.client.post(
        "/api/v1/calendar/events",
        json={
            "title": "评审",
            "timezone": "Asia/Shanghai",
            "start_at": "2026-11-04T10:00:00+08:00",
            "end_at": "2026-11-04T11:00:00+08:00",
        },
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    event = created.json()
    pending = await call(env, instance, "delete_calendar_event", {"event_id": event["id"]})
    renamed = await env.client.patch(
        f"/api/v1/calendar/events/{event['id']}",
        json={"version": event["version"], "title": "评审（改期）"},
        headers=env.headers(write=True),
    )
    assert renamed.status_code == 200, renamed.text
    approved = await decide(env, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert (body["status"], body["error_code"]) == ("failed", "VERSION_CONFLICT")
    assert body["version"] == 3 and approved.headers["etag"] == '"3"'
    assert (await execution(env, pending.action_id)).json()["status"] == "failed"


# ---------------------------------------------------------------- 工作流的敏感参数


@pytest.fixture
async def n8n(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[N8nEnv]:
    fake = FakeN8n()
    fake.add_workflow("wfReport01", path="daily-report")
    async with make_client(**SETTINGS) as client:
        cast(Any, client._transport).app.state.connectors.transport = httpx.MockTransport(
            fake.handler
        )
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        value = N8nEnv(client, setup.json()["workspaces"][0]["id"], migrated_database_url, fake)
        put = await client.put(
            "/api/v1/automations/connection", json=CONNECTION, headers=value.headers(write=True)
        )
        assert put.status_code == 200, put.text
        tested = await client.post(
            "/api/v1/automations/connection/test", headers=value.headers(write=True)
        )
        assert tested.status_code == 200 and tested.json()["ok"], tested.text
        yield value


async def test_sensitive_parameters_are_sealed_until_execution(n8n: N8nEnv) -> None:
    created = await n8n.client.post(
        "/api/v1/automations", json=REPORT, headers=n8n.headers(write=True)
    )
    assert created.status_code == 201, created.text
    automation_id = created.json()["id"]
    instance = await new_instance(n8n)
    args = {"automation_id": automation_id, "params": {"recipient": "boss@example.com", "days": 3}}
    pending = await call(n8n, instance, "trigger_automation", args)
    stored, display, digest = [
        await run_sql(n8n.database_url, sql, pending.action_id)
        for sql in (
            "SELECT arguments::text FROM workspace.ai_tool_executions WHERE id = $1",
            "SELECT arguments_display::text FROM workspace.ai_tool_executions WHERE id = $1",
            "SELECT arguments_hash FROM workspace.ai_tool_executions WHERE id = $1",
        )
    ]
    # 待审批期间：敏感参数加密保存，展示版本是 ***，哈希是 HMAC（不能用明文 SHA-256 反推）
    assert "boss@example.com" not in stored and "enc:v1:" in stored
    assert "boss@example.com" not in display and '"recipient": "***"' in display
    assert digest != arguments_hash("trigger_automation", args)
    detail = (await execution(n8n, pending.action_id)).json()
    assert detail["arguments"]["params"] == {"recipient": "***", "days": 3}
    same = await call(n8n, instance, "trigger_automation", args)
    assert same.deduplicated is True and same.action_id == pending.action_id
    other = {**args, "params": {"recipient": "cfo@example.com", "days": 3}}
    assert (await call(n8n, instance, "trigger_automation", other)).deduplicated is False

    approved = await decide(n8n, pending.action_id, "approve", 1, {"password": OWNER["password"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "succeeded", approved.json()
    # 执行时用的是解密后的原值；执行完清空完整参数，只留脱敏版本
    assert n8n.fake.received[-1]["body"]["params"] == {"recipient": "boss@example.com", "days": 3}
    after = await run_sql(
        n8n.database_url,
        "SELECT arguments FROM workspace.ai_tool_executions WHERE id = $1",
        pending.action_id,
    )
    assert after is None

    reader = await new_instance(n8n, scope="read", name="reader")
    with pytest.raises(ToolCallError):
        await call(n8n, reader, "trigger_automation", args)
    denied = await n8n.client.get(
        "/api/v1/ai/tool-executions", params={"status": "denied"}, headers=n8n.headers()
    )
    [record] = denied.json()["items"]
    assert record["arguments"]["params"] == {"recipient": "***", "days": "***"}


async def test_the_worker_expires_overdue_approvals(env: Env) -> None:
    instance = await new_instance(env)
    old = await call(env, instance, "create_bookmark", {"url": "https://a.test/o", "title": "o"})
    fresh = await call(env, instance, "create_bookmark", {"url": "https://a.test/f", "title": "f"})
    await run_sql(
        env.database_url,
        "UPDATE workspace.ai_tool_executions SET expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        old.action_id,
    )
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        service = AiToolService(db, app.state.settings, app.state.connectors, app.state.redis)
        assert await service.expire_due() == 1
    expired = (await execution(env, old.action_id)).json()
    assert (expired["status"], expired["version"]) == ("expired", 2)
    assert (await execution(env, fresh.action_id)).json()["status"] == "pending_approval"
    badges = await env.client.get("/api/v1/im/badges", headers=env.headers())
    assert badges.json()["pending_approvals"] == 1
