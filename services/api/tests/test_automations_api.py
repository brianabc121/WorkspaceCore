"""自动化（T-0037，S5-04）：n8n 连接、白名单、受控触发与执行记录对账。

真实 PostgreSQL + Redis + 内存版 n8n（tests/fake_n8n.py）。覆盖 Sprint 5 契约第 6 节：
密钥只写不读、白名单登记时核对工作流、策略校验（停用、连接、未激活、参数）、
Idempotency-Key（重放不重复触发、换内容 409）、明确拒绝与结果未知、按 traceparent 对应执行记录、
遮蔽后的错误摘要、无法确认的执行、通知与审计（只记参数名，不记参数值）、权限与运维页 / 首页模块。
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from app.domain import automations
from tests.fake_n8n import API_KEY, BASE_URL, TRIGGER_SECRET, FakeN8n
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql
from tests.vikunja_support import CONNECTOR_SETTINGS

pytestmark = pytest.mark.integration

MEMBER = {"email": "member@example.com", "password": "member password 123"}
SETTINGS = {**CONNECTOR_SETTINGS, "integration_allowed_hosts": "n8n"}
CONNECTION = {"base_url": BASE_URL, "api_key": API_KEY, "trigger_secret": TRIGGER_SECRET}
REPORT = {
    "name": "发送日报",
    "workflow_id": "wfReport01",
    "webhook_path": "daily-report",
    "params": [
        {
            "name": "recipient",
            "label": "收件人",
            "type": "string",
            "required": True,
            "max_length": 100,
            "sensitive": True,
        },
        {"name": "days", "label": "天数", "type": "number"},
    ],
}


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str
    fake: FakeN8n

    @property
    def app(self) -> FastAPI:
        return cast(FastAPI, cast(httpx.ASGITransport, self.client._transport).app)

    def headers(self, *, write: bool = False, key: uuid.UUID | None = None) -> dict[str, str]:
        headers = {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        return headers

    async def connect(self) -> dict[str, Any]:
        put = await self.client.put(
            "/api/v1/automations/connection", json=CONNECTION, headers=self.headers(write=True)
        )
        assert put.status_code == 200, put.text
        tested = await self.client.post(
            "/api/v1/automations/connection/test", headers=self.headers(write=True)
        )
        assert tested.status_code == 200 and tested.json()["ok"], tested.text
        data: dict[str, Any] = put.json()
        return data

    async def register(self, **overrides: Any) -> dict[str, Any]:
        created = await self.client.post(
            "/api/v1/automations", json={**REPORT, **overrides}, headers=self.headers(write=True)
        )
        assert created.status_code == 201, created.text
        data: dict[str, Any] = created.json()
        return data

    async def trigger(
        self,
        automation_id: str,
        params: dict[str, Any],
        key: uuid.UUID | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> httpx.Response:
        who = client or self.client
        headers = {
            "X-Workspace-Id": self.workspace_id,
            **csrf(who),
            "Idempotency-Key": str(key or uuid.uuid4()),
        }
        return await who.post(
            f"/api/v1/automations/{automation_id}/trigger", json={"params": params}, headers=headers
        )

    async def reconcile(self) -> int:
        state = self.app.state
        count = await automations.reconcile_all(
            state.sessionmaker, state.settings, state.connectors
        )
        await state.events.drain()
        return count

    async def runs(self, automation_id: str) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"/api/v1/automations/{automation_id}/runs", headers=self.headers()
        )
        assert response.status_code == 200, response.text
        items: list[dict[str, Any]] = response.json()["items"]
        return items

    async def notifications(self) -> list[dict[str, Any]]:
        response = await self.client.get("/api/v1/notifications", headers=self.headers())
        items: list[dict[str, Any]] = response.json()["items"]
        return items


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    fake = FakeN8n()
    fake.add_workflow("wfReport01", path="daily-report")
    async with make_client(**SETTINGS) as client:
        cast(Any, client._transport).app.state.connectors.transport = httpx.MockTransport(
            fake.handler
        )
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url, fake)


@pytest.fixture
async def ready(env: Env) -> tuple[Env, dict[str, Any]]:
    await env.connect()
    return env, await env.register()


# ---------------------------------------------------------------- 连接


async def test_connection_secrets_are_write_only(env: Env) -> None:
    empty = await env.client.get("/api/v1/automations/connection", headers=env.headers())
    assert empty.json()["configured"] is False
    missing = await env.client.put(
        "/api/v1/automations/connection",
        json={"base_url": BASE_URL},
        headers=env.headers(write=True),
    )
    assert missing.status_code == 422  # 新建时三项都要给
    created = await env.connect()
    text = json.dumps(created)
    assert API_KEY not in text and TRIGGER_SECRET not in text
    assert created["api_key_set"] and created["trigger_secret_set"]
    assert created["tested_version"] == "2.45.7"
    stored = await run_sql(
        env.database_url, "SELECT api_key_ref FROM workspace.automation_connections"
    )
    assert stored.startswith("enc:v1:") and API_KEY not in stored
    shown = (await env.client.get("/api/v1/automations/connection", headers=env.headers())).json()
    assert shown["status"] == "active" and shown["instance_version"] is None
    stale = await env.client.put(
        "/api/v1/automations/connection",
        json={"version": 99, "public_url": "http://localhost:5678"},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    private = await env.client.put(
        "/api/v1/automations/connection",
        json={"version": shown["version"], "base_url": "http://10.0.0.5:5678"},
        headers=env.headers(write=True),
    )
    assert private.status_code == 422  # SSRF：私有地址只允许 INTEGRATION_ALLOWED_HOSTS 里的主机


async def test_rejected_api_key_marks_the_connection_unauthorized(env: Env) -> None:
    env.fake.api_key = "n8n_api_other_" + "x" * 20
    await env.client.put(
        "/api/v1/automations/connection", json=CONNECTION, headers=env.headers(write=True)
    )
    tested = await env.client.post(
        "/api/v1/automations/connection/test", headers=env.headers(write=True)
    )
    result = tested.json()
    assert result["ok"] is False and result["status"] == "unauthorized"
    assert {c["name"]: c["ok"] for c in result["checks"]} == {
        "reachable": True,
        "version": True,
        "token": False,
    }
    page = await env.client.get("/api/v1/automations", headers=env.headers())
    assert page.json()["connection"]["status"] == "unauthorized"


# ---------------------------------------------------------------- 白名单


async def test_registration_checks_the_workflow_and_duplicates(env: Env) -> None:
    unconfigured = await env.client.post(
        "/api/v1/automations", json=REPORT, headers=env.headers(write=True)
    )
    assert unconfigured.status_code == 409  # 还没有 n8n 连接
    await env.connect()
    created = await env.register()
    assert created["workflow_active"] is True and created["last_run"] is None
    assert [p["name"] for p in created["params"]] == ["recipient", "days"]
    again = await env.client.post(
        "/api/v1/automations", json=REPORT, headers=env.headers(write=True)
    )
    assert again.status_code == 409
    assert again.json()["error"]["details"]["existing_id"] == created["id"]
    missing = await env.client.post(
        "/api/v1/automations",
        json={**REPORT, "workflow_id": "wfMissing", "webhook_path": "other"},
        headers=env.headers(write=True),
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["details"]["reason"] == "workflow_not_found"
    listed = await env.client.get("/api/v1/automations", headers=env.headers())
    assert [a["id"] for a in listed.json()["items"]] == [created["id"]]
    assert listed.json()["connection"]["status"] == "ok"


async def test_update_and_soft_delete(ready: tuple[Env, dict[str, Any]]) -> None:
    env, automation = ready
    path = f"/api/v1/automations/{automation['id']}"
    renamed = await env.client.patch(
        path,
        json={"version": automation["version"], "name": "周报"},
        headers=env.headers(write=True),
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "周报"
    stale = await env.client.patch(
        path, json={"version": automation["version"], "name": "x"}, headers=env.headers(write=True)
    )
    assert stale.status_code == 409
    removed = await env.client.delete(
        path, params={"version": renamed.json()["version"]}, headers=env.headers(write=True)
    )
    assert removed.status_code == 204
    listed = await env.client.get("/api/v1/automations", headers=env.headers())
    assert listed.json()["items"] == []
    kept = await run_sql(env.database_url, "SELECT count(*) FROM workspace.automations")
    assert kept == 1  # 软删除：执行记录与审计仍可追溯
    # 同一个工作流可以重新登记
    assert (await env.register())["id"] != automation["id"]


# ---------------------------------------------------------------- 触发


async def test_trigger_queues_once_per_idempotency_key(ready: tuple[Env, dict[str, Any]]) -> None:
    env, automation = ready
    key = uuid.uuid4()
    params = {"recipient": "boss@example.com", "days": 7}
    first = await env.trigger(automation["id"], params, key)
    assert first.status_code == 202, first.text
    run = first.json()
    assert run["status"] == "queued" and run["triggered_by"] is not None
    [sent] = env.fake.received
    assert sent["body"] == {"workspace_run_id": run["id"], "params": params}
    assert sent["traceparent"].split("-")[1] == uuid.UUID(run["id"]).hex
    replay = await env.trigger(automation["id"], params, key)
    assert replay.status_code == 202 and replay.json()["id"] == run["id"]
    assert len(env.fake.received) == 1  # 没有再触发一次
    reused = await env.trigger(automation["id"], {**params, "days": 1}, key)
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    # 审计：只记参数名，不记参数值（契约第 9 节）
    audit = await env.client.get("/api/v1/audit-logs", headers=env.headers())
    entry = next(e for e in audit.json()["items"] if e["action"] == "automation.triggered")
    assert entry["metadata"]["outcome"] == "queued"
    assert entry["metadata"]["params"] == ["days", "recipient"]
    assert "boss@example.com" not in json.dumps(audit.json())
    stored = await run_sql(
        env.database_url, "SELECT row_to_json(r)::text FROM workspace.automation_runs r"
    )
    assert "boss@example.com" not in stored  # 执行记录不保存参数


async def test_trigger_policy_and_parameter_rules(
    ready: tuple[Env, dict[str, Any]], make_client: ClientFactory
) -> None:
    env, automation = ready
    cases: dict[str, dict[str, Any]] = {
        "未声明的参数": {"recipient": "a@example.com", "cc": "x"},
        "类型不对": {"recipient": "a@example.com", "days": "seven"},
        "缺少必填": {"days": 1},
        "太长": {"recipient": "a" * 101},
    }
    for name, params in cases.items():
        response = await env.trigger(automation["id"], params)
        assert response.status_code == 422, name
        assert "a" * 101 not in response.text and "seven" not in response.text
    assert env.fake.received == []
    disabled = await env.client.patch(
        f"/api/v1/automations/{automation['id']}",
        json={"version": automation["version"], "enabled": False},
        headers=env.headers(write=True),
    )
    stopped = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    assert stopped.status_code == 409
    assert stopped.json()["error"]["details"]["reason"] == "disabled"
    await env.client.patch(
        f"/api/v1/automations/{automation['id']}",
        json={"version": disabled.json()["version"], "enabled": True},
        headers=env.headers(write=True),
    )
    env.fake.workflows["wfReport01"]["active"] = False
    await env.reconcile()  # 对账时刷新工作流的激活状态
    inactive = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    assert inactive.json()["error"]["details"]["reason"] == "workflow_inactive"
    assert env.fake.received == []
    audit = await env.client.get("/api/v1/audit-logs", headers=env.headers())
    outcomes = [
        e["metadata"]["outcome"]
        for e in audit.json()["items"]
        if e["action"] == "automation.triggered"
    ]
    assert "rejected:workflow_inactive" in outcomes and "rejected:disabled" in outcomes

    # 角色：viewer 不能触发，member 可以
    viewer_id = await add_user(env.database_url, MEMBER["email"], MEMBER["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), viewer_id, "viewer")
    env.fake.workflows["wfReport01"]["active"] = True
    await env.reconcile()
    async with make_client(**SETTINGS) as other:
        cast(Any, other._transport).app.state.connectors.transport = httpx.MockTransport(
            env.fake.handler
        )
        await other.post("/api/v1/auth/login", json=MEMBER)
        denied = await env.trigger(automation["id"], {"recipient": "a@example.com"}, client=other)
        assert denied.status_code == 403
        await run_sql(
            env.database_url,
            "UPDATE workspace.workspace_members SET role = 'member' WHERE user_id = $1",
            viewer_id,
        )
        allowed = await env.trigger(automation["id"], {"recipient": "a@example.com"}, client=other)
        assert allowed.status_code == 202
        settings = await other.get(
            "/api/v1/automations/connection", headers={"X-Workspace-Id": env.workspace_id}
        )
        assert settings.status_code == 403  # 连接设置只给 admin


async def test_definite_rejections_keep_no_run(ready: tuple[Env, dict[str, Any]]) -> None:
    env, automation = ready
    env.fake.secret = "rotated-secret-0123456789"  # noqa: S105  测试用的假值
    wrong = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    assert wrong.status_code == 502
    assert wrong.json()["error"]["code"] == "INTEGRATION_UNAUTHORIZED"
    env.fake.secret = TRIGGER_SECRET
    env.fake.workflows["wfReport01"]["path"] = "renamed-in-n8n"
    gone = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    assert gone.status_code == 409
    assert gone.json()["error"]["details"]["reason"] == "webhook_not_found"
    assert await env.runs(automation["id"]) == []
    notices = [n["type"] for n in await env.notifications()]
    assert notices == ["automation_misconfigured"]  # 同一个自动化一小时内只通知一次
    env.fake.workflows["wfReport01"]["path"] = "daily-report"
    await env.reconcile()  # 404 之后先标为未激活；对账读到工作流仍激活后恢复
    env.fake.down = True
    unreachable = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    assert unreachable.status_code == 503
    assert await env.runs(automation["id"]) == []


# ---------------------------------------------------------------- 对账


async def test_unknown_result_is_reconciled_by_traceparent(
    ready: tuple[Env, dict[str, Any]],
) -> None:
    env, automation = ready
    env.fake.trigger_failure = "timeout"
    key = uuid.uuid4()
    unknown = await env.trigger(automation["id"], {"recipient": "a@example.com"}, key)
    assert unknown.status_code == 202 and unknown.json()["status"] == "unknown"
    # 先放进一个别人的执行，确认按 traceparent 对应而不是按时间猜
    env.fake.executions["99"] = {
        **env.fake.executions["1"],
        "id": "99",
        "traceparent": "00-" + "0" * 32 + "-" + "1" * 16 + "-01",
    }
    await env.reconcile()
    [run] = await env.runs(automation["id"])
    assert run["id"] == unknown.json()["id"]
    assert (run["status"], run["external_execution_id"]) == ("success", "1")
    assert run["finished_at"] is not None
    replay = await env.trigger(automation["id"], {"recipient": "a@example.com"}, key)
    assert replay.json()["status"] == "success"  # 重放返回最新状态，不会重发
    assert len(env.fake.received) == 1
    assert [n["type"] for n in await env.notifications()] == ["automation_succeeded"]


async def test_failed_execution_keeps_only_the_redacted_summary(
    ready: tuple[Env, dict[str, Any]],
) -> None:
    env, automation = ready
    env.fake.next_status = "running"
    queued = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    await env.reconcile()
    [run] = await env.runs(automation["id"])
    assert run["status"] == "running" and run["started_at"] is not None
    env.fake.executions["1"]["status"] = "error"
    env.fake.executions["1"]["stoppedAt"] = env.fake.executions["1"]["startedAt"]
    await env.reconcile()
    [run] = await env.runs(automation["id"])
    assert run["id"] == queued.json()["id"] and run["status"] == "error"
    assert run["error_summary"] == "工作流在节点「发送邮件」失败（WrappedExecutionError）"
    [notice] = await env.notifications()
    assert notice["type"] == "automation_failed" and notice["severity"] == "error"
    assert notice["body"] == run["error_summary"]
    last = (await env.client.get("/api/v1/automations", headers=env.headers())).json()["items"][0]
    assert last["last_run"]["id"] == run["id"]


async def test_runs_that_never_appear_are_marked_unconfirmed(
    ready: tuple[Env, dict[str, Any]],
) -> None:
    env, automation = ready
    env.fake.trigger_failure = "timeout"
    unknown = await env.trigger(automation["id"], {"recipient": "a@example.com"})
    env.fake.executions.clear()  # n8n 里没有对应的执行
    await run_sql(
        env.database_url,
        "UPDATE workspace.automation_runs SET created_at = now() - interval '20 minutes'",
    )
    await env.reconcile()
    [run] = await env.runs(automation["id"])
    assert run["id"] == unknown.json()["id"] and run["status"] == "unknown"
    assert run["error_summary"].startswith("无法确认是否执行")
    assert [n["severity"] for n in await env.notifications()] == ["warning"]


async def test_operations_and_overview_show_the_n8n_connection(env: Env) -> None:
    services = await env.client.get("/api/v1/operations/services", headers=env.headers())
    n8n = next(i for i in services.json()["items"] if i["id"] == "n8n")
    assert n8n["status"] == "not_configured"
    overview = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert overview.json()["modules"]["automations"]["reason"] == "not_configured"
    await env.connect()
    services = await env.client.get("/api/v1/operations/services", headers=env.headers())
    n8n = next(i for i in services.json()["items"] if i["id"] == "n8n")
    assert n8n["status"] == "ok" and n8n["provider"] == "n8n"
    assert BASE_URL not in json.dumps(n8n)
    overview = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert overview.json()["modules"]["automations"]["status"] == "ok"
    env.fake.api_key = "n8n_api_revoked_" + "z" * 20
    await env.reconcile()
    overview = await env.client.get("/api/v1/dashboard/overview", headers=env.headers())
    assert overview.json()["modules"]["automations"]["status"] == "unauthorized"
    assert "integration_unauthorized" in [n["type"] for n in await env.notifications()]
