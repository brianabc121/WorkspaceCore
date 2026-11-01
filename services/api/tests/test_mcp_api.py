"""`POST /mcp`（T-0057，OC-5，契约第 9 节）：真实 PostgreSQL / Redis + 内存版 Vikunja + MCP SDK。

覆盖：实例认证（与 /agent-api 相同，失败一律 401）、只接受 POST、工具清单按实例的权限范围、
经 MCP 发起的删除只生成待审批（A14）、get_action_status、错误格式（isError + error.code）、
scope 不够写 denied 记录、按实例限流、读工具也写记录。
"""

import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.domain import mcp_tools
from app.domain.agents import AgentService
from app.models import AgentInstance
from app.schemas.ai_tools import TOOLS
from tests.helpers import OWNER
from tests.vikunja_support import Env, app_of, open_env

pytestmark = pytest.mark.integration

SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(secrets.token_hex(32)),
    # 测试客户端的对端地址是 127.0.0.1，当作接入入口的 Caddy
    "agent_ingress_trusted_proxies": "127.0.0.1/32",
    "agent_intranet_url": "https://10.0.0.5",
}
PROTOCOL = "2025-11-25"


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        work = value.fake.add_project("工作")
        value.fake.add_task(work["id"], "写周报", priority=3)
        await value.connect()
        yield value


async def new_instance(env: Env, *, scope: str = "read_write", name: str = "alpha") -> str:
    response = await env.client.post(
        "/api/v1/agents/instances",
        json={"name": name, "network_mode": "intranet", "scope": scope, "max_role": "member"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 201, response.text
    instance_id: str = response.json()["id"]
    return instance_id


async def issue_token(env: Env, instance_id: str) -> str:
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        service = AgentService(db, app.state.settings)
        instance = await db.get(AgentInstance, uuid.UUID(instance_id))
        assert instance is not None
        issued = await service.issue_token(instance, keep_previous=True)
        await db.commit()
        return issued.token


def mcp_headers(token: str | None, ingress: str = "intranet") -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": PROTOCOL,
        "X-MyWS-Ingress": ingress,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def rpc(
    env: Env, token: str | None, method: str, params: dict[str, Any] | None = None, **kw: Any
) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return await env.client.post("/mcp", json=body, headers=mcp_headers(token, **kw))


async def call(env: Env, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = await rpc(env, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


async def records(env: Env, **params: Any) -> list[dict[str, Any]]:
    listed = await env.client.get(
        "/api/v1/ai/tool-executions", params=params, headers=env.headers()
    )
    assert listed.status_code == 200, listed.text
    items: list[dict[str, Any]] = listed.json()["items"]
    return items


async def test_mcp_requires_an_instance_credential(env: Env) -> None:
    instance = await new_instance(env)
    token = await issue_token(env, instance)
    missing = await rpc(env, None, "tools/list")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AGENT_UNAUTHORIZED"
    assert missing.headers["www-authenticate"].startswith("Bearer")
    wrong = await rpc(env, "myws_" + "x" * 40, "tools/list")
    assert wrong.status_code == 401
    # 内网凭证不能从外网入口用（入口不对也是同一个 401）
    elsewhere = await rpc(env, token, "tools/list", ingress="public")
    assert elsewhere.status_code == 401
    # 无状态模式不开 SSE 流，也没有会话可删
    for method in ("GET", "DELETE"):
        other = await env.client.request(method, "/mcp", headers=mcp_headers(token))
        assert other.status_code == 405
    disabled = await env.client.post(
        f"/api/v1/agents/instances/{instance}/disable",
        json={"version": 1},
        headers=env.headers(write=True),
    )
    assert disabled.status_code == 200, disabled.text
    assert (await rpc(env, token, "tools/list")).status_code == 401


async def test_initialize_and_tool_list_follow_the_instance_scope(env: Env) -> None:
    writer = await issue_token(env, await new_instance(env))
    reader = await issue_token(env, await new_instance(env, scope="read", name="reader"))
    init = await rpc(
        env,
        writer,
        "initialize",
        {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "openclaw-test", "version": "1"},
        },
    )
    assert init.status_code == 200, init.text
    result = init.json()["result"]
    assert result["serverInfo"]["name"] == "myworkspace"
    assert set(result["capabilities"]) == {"tools"}  # 不提供 prompts、resources（OC-0 M5）

    listed = (await rpc(env, writer, "tools/list")).json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert {"create_task", "delete_task", "trigger_automation", "get_action_status"} <= names
    bookmark = next(tool for tool in listed if tool["name"] == "create_bookmark")
    assert bookmark["inputSchema"]["additionalProperties"] is False
    delete = next(tool for tool in listed if tool["name"] == "delete_task")
    assert delete["annotations"]["destructiveHint"] is True
    read_only = (await rpc(env, reader, "tools/list")).json()["result"]["tools"]
    assert {tool["name"] for tool in read_only} == {
        name for name, spec in TOOLS.items() if spec.risk == "read"
    }


async def test_a14_delete_through_mcp_only_creates_a_pending_record(env: Env) -> None:
    token = await issue_token(env, await new_instance(env))
    task = await env.task_by_title("写周报")
    result = await call(env, token, "delete_task", {"task_id": task["id"]})
    assert result["isError"] is False
    pending = result["structuredContent"]
    assert pending["status"] == "pending_approval" and pending["deduplicated"] is False
    assert "删除任务「写周报」" in pending["summary"]
    assert any(t["title"] == "写周报" for t in env.fake.tasks.values())  # 没有执行

    status = await call(env, token, "get_action_status", {"action_id": pending["action_id"]})
    assert status["structuredContent"]["status"] == "pending_approval"
    approved = await env.client.post(
        f"/api/v1/ai/tool-executions/{pending['action_id']}/approve",
        json={"password": OWNER["password"]},
        headers={**env.headers(write=True), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text
    status = await call(env, token, "get_action_status", {"action_id": pending["action_id"]})
    assert status["structuredContent"]["status"] == "succeeded"
    assert status["structuredContent"]["result_summary"] == "已删除任务"
    assert not any(t["title"] == "写周报" for t in env.fake.tasks.values())
    # get_action_status 也是一次工具调用：写了读记录
    reads = await records(env, tool_name="get_action_status")
    assert [r["status"] for r in reads] == ["succeeded", "succeeded"]


async def test_tool_errors_use_is_error_with_a_code(env: Env) -> None:
    token = await issue_token(env, await new_instance(env))
    unknown = await call(env, token, "list_members", {})
    assert unknown["isError"] is True
    assert unknown["structuredContent"]["error"]["code"] == "NOT_FOUND"
    invalid = await call(env, token, "create_bookmark", {"url": "https://a.test", "extra": 1})
    assert invalid["structuredContent"]["error"]["code"] == "VALIDATION_FAILED"
    missing = await call(env, token, "delete_task", {"task_id": str(uuid.uuid4())})
    assert missing["structuredContent"]["error"]["code"] == "NOT_FOUND"
    # 别的实例发起的操作查不到
    other = await issue_token(env, await new_instance(env, name="beta"))
    pending = await call(env, other, "create_bookmark", {"url": "https://a.test/b", "title": "b"})
    action_id = pending["structuredContent"]["action_id"]
    hidden = await call(env, token, "get_action_status", {"action_id": action_id})
    assert hidden["structuredContent"]["error"]["code"] == "NOT_FOUND"
    failed = await records(env, tool_name="get_action_status", status="failed")
    assert [r["error_code"] for r in failed] == ["NOT_FOUND"]


async def test_scope_denials_are_recorded(env: Env) -> None:
    reader = await issue_token(env, await new_instance(env, scope="read", name="reader"))
    project = (await env.projects())["工作"]
    denied = await call(
        env, reader, "create_task", {"project_id": project, "title": "AI 想建的任务"}
    )
    assert denied["isError"] is True
    assert denied["structuredContent"]["error"]["code"] == "FORBIDDEN_SCOPE"
    [record] = await records(env, status="denied")
    assert (record["tool_name"], record["error_code"]) == ("create_task", "FORBIDDEN_SCOPE")
    assert record["instance_name"] == "reader"


async def test_calls_are_rate_limited_per_instance(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_tools, "CALLS_PER_MINUTE", 2)
    token = await issue_token(env, await new_instance(env))
    other = await issue_token(env, await new_instance(env, name="beta"))
    args = {"action_id": str(uuid.uuid4())}
    for _ in range(2):
        result = await call(env, token, "get_action_status", args)
        assert result["structuredContent"]["error"]["code"] == "NOT_FOUND"
    limited = await call(env, token, "get_action_status", args)
    assert limited["structuredContent"]["error"]["code"] == "RATE_LIMITED"
    # 按实例计数：另一个实例不受影响
    fine = await call(env, other, "get_action_status", args)
    assert fine["structuredContent"]["error"]["code"] == "NOT_FOUND"


async def test_the_official_mcp_client_can_use_the_endpoint(env: Env) -> None:
    """用 MCP SDK 自带的客户端（与 OpenClaw 同样走 Streamable HTTP）：初始化、列工具、调用。"""
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    token = await issue_token(env, await new_instance(env))
    task = await env.task_by_title("写周报")
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app_of(env.client)),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}", "X-MyWS-Ingress": "intranet"},
    )
    async with (
        http,
        streamable_http_client("http://testserver/mcp", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        info = await session.initialize()
        assert info.server_info.name == "myworkspace"
        tools = await session.list_tools()
        assert "delete_task" in {tool.name for tool in tools.tools}
        result = await session.call_tool("delete_task", {"task_id": task["id"]})
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["status"] == "pending_approval"
    assert any(t["title"] == "写周报" for t in env.fake.tasks.values())
