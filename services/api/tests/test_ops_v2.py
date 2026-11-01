"""V2 运维（T-0062，方案第 2、6.1 节）：运维页的 OpenIM 与 OpenClaw 实例状态、/metrics 的 V2 指标。

- 服务列表：openim（没部署是 not_configured；回调收件箱有卡住的事件时 degraded；
  OpenIM 不可用时 down）；
  本工作空间的实例只对 owner / admin 列出，状态来自实例表；
- /metrics：实例数、待审批数、回调积压（抓取时查数据库），取密钥、认证失败、MCP 调用（Redis 计数）；
  标签里没有实例 ID、Token 或用户输入。
"""

import json
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql
from tests.test_agents_api import SETTINGS, agent_headers, resolve
from tests.test_im_accounts_callbacks import ImEnv, im_env

pytestmark = pytest.mark.integration

PROTOCOL = "2025-11-25"
DASHBOARD = (
    Path(__file__).resolve().parents[3]
    / "infra/monitoring/grafana/dashboards/workspace-overview.json"
)


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url, service_status_cache_seconds=0) as env:
        yield env


async def services(client: httpx.AsyncClient, workspace_id: str) -> dict[str, dict[str, Any]]:
    response = await client.get(
        "/api/v1/operations/services", headers={"X-Workspace-Id": workspace_id}
    )
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()["items"]}


async def test_services_show_openim_and_the_workspace_instances(env: ImEnv) -> None:
    # 按名称排序（名称用 ASCII 开头，排序不受数据库排序规则影响）
    b = await env.create_instance(name="B 乙")
    a = await env.create_instance(name="A 甲")
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET status = 'online', last_seen_at = now()"
        " WHERE id = $1",
        uuid.UUID(a["id"]),
    )
    items = await services(env.client, env.workspace_id)
    assert list(items)[-3:] == ["openim", f"agent:{a['id']}", f"agent:{b['id']}"]
    assert (items["openim"]["kind"], items["openim"]["status"]) == ("agents", "ok")
    first = items[f"agent:{a['id']}"]
    assert (first["name"], first["kind"], first["provider"]) == ("A 甲", "agents", "openclaw")
    assert first["status"] == "ok" and first["checked_at"] is not None
    second = items[f"agent:{b['id']}"]
    assert (second["status"], second["reason"]) == ("unknown", "agent_not_connected")

    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET status = 'offline' WHERE id = $1",
        uuid.UUID(a["id"]),
    )
    await env.set_enabled(b["id"], False)
    items = await services(env.client, env.workspace_id)
    assert (items[f"agent:{a['id']}"]["status"], items[f"agent:{a['id']}"]["reason"]) == (
        "down",
        "agent_offline",
    )
    assert (items[f"agent:{b['id']}"]["status"], items[f"agent:{b['id']}"]["reason"]) == (
        "not_configured",
        "agent_disabled",
    )

    # 回调收件箱里有等待超过 5 分钟的事件：降级（对账兜底，但投影会落后）
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.im_webhook_inbox (event_key, command, payload_json, received_at)"
        " VALUES ('stuck', 'callbackAfterSendSingleMsgCommand', '{}',"
        " now() - interval '10 minutes')",
    )
    openim = (await services(env.client, env.workspace_id))["openim"]
    assert (openim["status"], openim["reason"]) == ("degraded", "inbox_backlog")
    await run_sql(env.database_url, "DELETE FROM workspace.im_webhook_inbox")

    env.fake.down = True
    openim = (await services(env.client, env.workspace_id))["openim"]
    assert openim["status"] == "down"
    assert "openim-server" not in str(openim) and "10002" not in str(openim)


async def test_members_below_admin_do_not_see_instances(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    # 没有部署 OpenIM：openim 为 not_configured；实例照样能登记
    settings = {**SETTINGS, "service_status_cache_seconds": 0}
    async with make_client(**settings) as owner, make_client(**settings) as viewer:
        setup = await owner.post("/api/v1/setup", json=OWNER)
        workspace = setup.json()["workspaces"][0]["id"]
        created = await owner.post(
            "/api/v1/agents/instances",
            json={"name": "甲", "network_mode": "intranet"},
            headers={"X-Workspace-Id": workspace, **csrf(owner)},
        )
        assert created.status_code == 201, created.text
        user = await add_user(migrated_database_url, "viewer@example.com", "viewer password 123")
        await add_member(migrated_database_url, uuid.UUID(workspace), user, "member")
        login = await viewer.post(
            "/api/v1/auth/login",
            json={"email": "viewer@example.com", "password": "viewer password 123"},
        )
        assert login.status_code == 200

        mine = await services(owner, workspace)
        assert (mine["openim"]["status"], mine["openim"]["reason"]) == (
            "not_configured",
            "not_deployed",
        )
        assert f"agent:{created.json()['id']}" in mine
        theirs = await services(viewer, workspace)
        assert "openim" in theirs
        assert not [key for key in theirs if key.startswith("agent:")]


async def test_metrics_cover_instances_secrets_mcp_and_approvals(env: ImEnv) -> None:
    a = await env.create_instance(name="甲", scope="read_write", max_role="member")
    await env.create_instance(name="乙")
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET status = 'online' WHERE id = $1",
        uuid.UUID(a["id"]),
    )
    token = await env.issue_token(a["id"])

    # 取密钥：一个没有授权的 ID（not_found）；一次凭证错误（认证失败，按入口计）
    assert (await resolve(env.client, token, ["model_api_key"])).status_code == 200
    wrong = await env.client.post(
        "/agent-api/v1/secrets/resolve",
        json={"ids": ["x"]},
        headers=agent_headers("mwa_" + "0" * 40),
    )
    assert wrong.status_code == 401

    # MCP：写工具只生成待审批（计 ok），不存在的工具计为 unknown / not_found
    headers = {
        **agent_headers(token),
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
    }
    for name, arguments in (
        ("create_bookmark", {"url": "https://a.test/doc", "title": "文档"}),
        ("no_such_tool", {}),
    ):
        response = await env.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text

    body = (await env.client.get("/metrics")).text
    for line in (
        'workspace_agent_instances{status="online"} 1.0',
        'workspace_agent_instances{status="pending"} 1.0',
        'workspace_agent_instances{status="disabled"} 0.0',
        "workspace_ai_pending_approvals 1.0",
        'workspace_im_inbox_events{status="pending"} 0.0',
        'workspace_agent_secret_resolves_total{result="not_found"} 1.0',
        'workspace_agent_auth_failures_total{ingress="intranet"} 1.0',
        'workspace_mcp_tool_calls_total{outcome="ok",tool="create_bookmark"} 1.0',
        'workspace_mcp_tool_calls_total{outcome="not_found",tool="unknown"} 1.0',
        "workspace_im_relay_pauses_total 0.0",
        "workspace_im_reconcile_filled_messages_total 0.0",
    ):
        assert line in body, line
    for leaked in (a["id"], token, "no_such_tool", "a.test", env.workspace_id):
        assert leaked not in body
    # 请求指标照常在前面
    assert "workspace_http_requests_total" in body
    # Grafana 预置仪表盘查询的指标都存在（改名时一起改仪表盘）
    queries = [
        target["expr"]
        for panel in json.loads(DASHBOARD.read_text())["panels"]
        for target in panel["targets"]
    ]
    names = {name for expr in queries for name in re.findall(r"workspace_[a-z_]+", expr)}
    assert {"workspace_agent_instances", "workspace_mcp_tool_calls_total"} <= names
    for name in names:
        assert re.search(rf"^{name}[{{ ]", body, re.MULTILINE), name
