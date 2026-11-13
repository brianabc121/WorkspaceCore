"""接口契约：Sprint 1（docs/decisions/2026-10-30-sprint-1-auth-contract.md）、
Sprint 2（…-sprint-2-dashboard-contract.md）、Sprint 3（…-sprint-3-tasks-contract.md）、
Sprint 4（…-sprint-4-knowledge-search-contract.md）、
Sprint 5（…-sprint-5-operations-automations-contract.md）、
V2 OC-1（…-v2-oc1-contract.md）、
工作台自己的项目与任务（2026-11-02-remove-vikunja.md）。"""

import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.api.agent_api import instance_principal
from app.api.deps import (
    authenticated,
    get_agent_service,
    get_ai_tool_service,
    get_calendar_service,
    get_db,
    get_developer_service,
    get_integration_service,
    get_knowledge_service,
    get_notification_service,
    get_operations_service,
    get_redis,
    get_search_service,
    get_task_service,
    get_workspace_service,
    workspace_admin,
    workspace_member,
    workspace_owner,
    workspace_viewer,
)
from app.core.errors import not_implemented
from scripts.export_openapi import OUTPUT, render

SPRINT_1_ENDPOINTS = {
    ("get", "/api/v1/setup/status"),
    ("post", "/api/v1/setup"),
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/auth/logout"),
    ("get", "/api/v1/auth/sessions"),
    ("delete", "/api/v1/auth/sessions/{session_id}"),
    ("post", "/api/v1/auth/password"),
    ("get", "/api/v1/me"),
    ("patch", "/api/v1/me"),
    ("get", "/api/v1/workspaces"),
    ("get", "/api/v1/workspaces/{workspace_id}"),
    ("patch", "/api/v1/workspaces/{workspace_id}"),
    ("get", "/api/v1/audit-logs"),
}

SPRINT_2_ENDPOINTS = {
    ("get", "/api/v1/dashboard/overview"),
    ("get", "/api/v1/dashboards"),
    ("post", "/api/v1/dashboards"),
    ("patch", "/api/v1/dashboards/{dashboard_id}"),
    ("get", "/api/v1/dashboards/{dashboard_id}/widgets"),
    ("post", "/api/v1/dashboards/{dashboard_id}/widgets"),
    ("patch", "/api/v1/widgets/{widget_id}"),
    ("delete", "/api/v1/widgets/{widget_id}"),
}

SPRINT_3_ENDPOINTS = {
    ("get", "/api/v1/integrations"),
    ("post", "/api/v1/integrations"),
    ("patch", "/api/v1/integrations/{integration_id}"),
    ("post", "/api/v1/integrations/{integration_id}/test"),
    ("post", "/api/v1/integrations/{integration_id}/sync"),
    ("get", "/api/v1/projects"),
    ("get", "/api/v1/tasks"),
    ("post", "/api/v1/tasks"),
    ("get", "/api/v1/tasks/{task_id}"),
    ("patch", "/api/v1/tasks/{task_id}"),
    ("delete", "/api/v1/tasks/{task_id}"),
}

# T-0082：移除 Vikunja 后项目由工作台自己管理；Vikunja 的 Webhook 端点已删除
NATIVE_PROJECT_ENDPOINTS = {
    ("post", "/api/v1/projects"),
    ("get", "/api/v1/projects/progress"),
    ("get", "/api/v1/projects/{project_id}"),
    ("patch", "/api/v1/projects/{project_id}"),
    ("delete", "/api/v1/projects/{project_id}"),
}

SPRINT_4_ENDPOINTS = {
    ("get", "/api/v1/knowledge/sources"),
    ("post", "/api/v1/knowledge/sources"),
    ("patch", "/api/v1/knowledge/sources/{source_id}"),
    ("delete", "/api/v1/knowledge/sources/{source_id}"),
    ("get", "/api/v1/knowledge/resources"),
    ("post", "/api/v1/knowledge/bookmarks"),
    ("patch", "/api/v1/knowledge/bookmarks/{bookmark_id}"),
    ("delete", "/api/v1/knowledge/bookmarks/{bookmark_id}"),
    ("get", "/api/v1/calendar/events"),
    ("post", "/api/v1/calendar/events"),
    ("patch", "/api/v1/calendar/events/{event_id}"),
    ("delete", "/api/v1/calendar/events/{event_id}"),
    ("get", "/api/v1/developer/repos"),
    ("get", "/api/v1/developer/issues"),
    ("get", "/api/v1/developer/pulls"),
    ("get", "/api/v1/developer/runs"),
    ("get", "/api/v1/search"),
}

SPRINT_5_ENDPOINTS = {
    ("get", "/api/v1/operations/services"),
    ("get", "/api/v1/operations/metrics"),
    ("get", "/api/v1/notifications"),
    ("patch", "/api/v1/notifications"),
    ("get", "/api/v1/events/stream"),
}
V2_ADMIN_ENDPOINTS = {
    ("get", "/api/v1/agents/settings"),
    ("patch", "/api/v1/agents/settings"),
    ("get", "/api/v1/agents/instances"),
    ("post", "/api/v1/agents/instances"),
    ("get", "/api/v1/agents/instances/{instance_id}"),
    ("patch", "/api/v1/agents/instances/{instance_id}"),
    ("post", "/api/v1/agents/instances/{instance_id}/disable"),
    ("post", "/api/v1/agents/instances/{instance_id}/enable"),
    ("post", "/api/v1/agents/instances/{instance_id}/bundle"),
    ("post", "/api/v1/agents/instances/{instance_id}/credentials/{credential_id}/revoke"),
    ("put", "/api/v1/agents/instances/{instance_id}/secret-grants"),
    ("get", "/api/v1/agents/secrets"),
    ("post", "/api/v1/agents/secrets"),
    ("get", "/api/v1/agents/secrets/accesses"),
    ("get", "/api/v1/agents/secrets/{secret_id}"),
    ("patch", "/api/v1/agents/secrets/{secret_id}"),
    ("post", "/api/v1/agents/secrets/{secret_id}/rotate"),
    ("delete", "/api/v1/agents/secrets/{secret_id}"),
    ("get", "/api/v1/im/status"),
    ("get", "/api/v1/im/badges"),
    ("get", "/api/v1/im/conversations"),
    ("post", "/api/v1/im/conversations"),
    ("patch", "/api/v1/im/conversations/{conversation_id}"),
    ("post", "/api/v1/im/conversations/{conversation_id}/read"),
    ("get", "/api/v1/im/conversations/{conversation_id}/messages"),
    ("post", "/api/v1/im/conversations/{conversation_id}/messages"),
    ("get", "/api/v1/im/conversations/{conversation_id}/pins"),
    ("post", "/api/v1/im/conversations/{conversation_id}/pins"),
    ("delete", "/api/v1/im/conversations/{conversation_id}/pins/{server_msg_id}"),
    ("get", "/api/v1/im/conversations/{conversation_id}/export"),
    ("post", "/api/v1/im/uploads"),
    ("get", "/api/v1/im/media/{media_id}"),
    ("post", "/api/v1/im/groups"),
    ("get", "/api/v1/im/groups/{group_id}"),
    ("patch", "/api/v1/im/groups/{group_id}"),
    ("delete", "/api/v1/im/groups/{group_id}"),
    ("post", "/api/v1/im/groups/{group_id}/members"),
    ("delete", "/api/v1/im/groups/{group_id}/members/{im_user_id}"),
    ("put", "/api/v1/im/groups/{group_id}/policy"),
    ("get", "/api/v1/im/contacts"),
    ("get", "/api/v1/im/contact-groups"),
    ("post", "/api/v1/im/contact-groups"),
    ("patch", "/api/v1/im/contact-groups/{contact_group_id}"),
    ("delete", "/api/v1/im/contact-groups/{contact_group_id}"),
    ("put", "/api/v1/im/contact-groups/{contact_group_id}/members"),
    ("get", "/api/v1/ai/tool-executions"),
    ("get", "/api/v1/ai/tool-executions/{execution_id}"),
    ("post", "/api/v1/ai/tool-executions/{execution_id}/approve"),
    ("post", "/api/v1/ai/tool-executions/{execution_id}/reject"),
}


V2_AGENT_ENDPOINTS = {
    ("post", "/agent-api/v1/secrets/resolve"),
    ("post", "/agent-api/v1/im-token"),
    ("post", "/agent-api/v1/heartbeat"),
}
WORKSPACE = {"X-Workspace-Id": str(uuid.uuid4())}


@pytest.fixture
async def stub_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """只验证接口形态（校验、错误格式）：用桩替换数据库、Redis 和登录校验。"""
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_redis] = lambda: None
    # 登录校验的桩：只有路由里会读的用户 ID（例如搜索按用户过滤私有收藏）
    app.dependency_overrides[authenticated] = lambda: SimpleNamespace(
        user=SimpleNamespace(id=uuid.UUID(int=0))
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def test_committed_openapi_matches_code() -> None:
    assert OUTPUT.read_text(encoding="utf-8") == render(), "请运行 make openapi 并提交"


def test_all_sprint_endpoints_are_declared() -> None:
    paths = json.loads(render())["paths"]
    declared = {(method, path) for path, ops in paths.items() for method in ops}
    assert declared >= (
        SPRINT_1_ENDPOINTS
        | SPRINT_2_ENDPOINTS
        | SPRINT_3_ENDPOINTS
        | NATIVE_PROJECT_ENDPOINTS
        | SPRINT_4_ENDPOINTS
        | SPRINT_5_ENDPOINTS
        | V2_ADMIN_ENDPOINTS
        | V2_AGENT_ENDPOINTS
    )


def test_vikunja_endpoints_are_removed() -> None:
    paths = json.loads(render())["paths"]
    assert not [path for path in paths if "vikunja" in path or "webhooks" in path]


def test_no_endpoint_documents_501() -> None:
    """未实现的端点在运行时返回 501，但不写进契约，实现后前端类型不需要变化。"""
    paths = json.loads(render())["paths"]
    assert not [
        (method, path)
        for path, ops in paths.items()
        for method, op in ops.items()
        if "501" in op["responses"]
    ]


def test_widget_types_are_a_discriminated_union() -> None:
    schema = json.loads(render())
    body = schema["paths"]["/api/v1/dashboards/{dashboard_id}/widgets"]["post"]["requestBody"]
    union = body["content"]["application/json"]["schema"]
    assert union["discriminator"]["propertyName"] == "widget_type"
    assert set(union["discriminator"]["mapping"]) == {
        "quick_links",
        "note",
        "todo_summary",
        "today_tasks",
        "calendar_agenda",
        "recent_docs",
        "github_activity",
        "service_health",
        "api_latency",
        "notifications",
        "project_progress",
        "im_quick_chat",
        "news_briefing",
        "work_summary",
        "task_progress",
        "my_todos",
    }


def test_validation_errors_are_documented_with_unified_format() -> None:
    """422 必须引用 ErrorResponse，不能出现 FastAPI 默认的 HTTPValidationError。"""
    schema = render()
    assert "HTTPValidationError" not in schema
    paths = json.loads(schema)["paths"]
    for path, ops in paths.items():
        for method, op in ops.items():
            if "422" in op["responses"]:
                ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
                assert ref.endswith("/ErrorResponse"), (method, path)


async def test_invalid_uuid_path_parameter_returns_unified_error(
    stub_client: httpx.AsyncClient,
) -> None:
    response = await stub_client.get("/api/v1/workspaces/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


class _UnfinishedWorkspaceService:
    async def update(self, *args: object, **kwargs: object) -> None:
        raise not_implemented("T-xxxx")


async def test_business_errors_use_unified_error_format(
    app: FastAPI, stub_client: httpx.AsyncClient
) -> None:
    app.dependency_overrides[get_workspace_service] = _UnfinishedWorkspaceService
    response = await stub_client.patch(
        f"/api/v1/workspaces/{uuid.uuid4()}", json={"name": "新名称"}
    )
    assert response.status_code == 501
    error = response.json()["error"]
    assert error["code"] == "NOT_IMPLEMENTED"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_request_validation_runs_before_handler(stub_client: httpx.AsyncClient) -> None:
    response = await stub_client.post(
        "/api/v1/setup",
        json={
            "setup_token": "t",
            "email": "owner@example.com",
            "password": "short-pw",
            "display_name": "Owner",
            "workspace_name": "Home",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "short-pw" not in response.text


async def test_workspace_scoped_endpoint_requires_workspace_header(
    stub_client: httpx.AsyncClient,
) -> None:
    missing = await stub_client.get("/api/v1/audit-logs")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_FAILED"
    invalid = await stub_client.get("/api/v1/audit-logs", headers={"X-Workspace-Id": "1"})
    assert invalid.status_code == 422


# ---------------------------------------------------------------- Sprint 3


class _ShapeOnlyService:
    """请求形态通过校验后到达这里：返回 501，表示“形态正确”（业务逻辑另有测试）。"""

    def __getattr__(self, name: str) -> object:
        async def unfinished(*args: object, **kwargs: object) -> None:
            raise not_implemented("contract-test")

        return unfinished


@pytest.fixture
def any_role(app: FastAPI) -> None:
    """跳过成员与角色检查与业务逻辑（它们需要数据库），只验证请求形态。"""
    for dependency in (workspace_viewer, workspace_member, workspace_admin, workspace_owner):
        app.dependency_overrides[dependency] = lambda: None
    for service in (
        get_task_service,
        get_integration_service,
        get_knowledge_service,
        get_calendar_service,
        get_developer_service,
        get_search_service,
        get_operations_service,
        get_notification_service,
        get_agent_service,
        get_ai_tool_service,
    ):
        app.dependency_overrides[service] = _ShapeOnlyService


def _operation(method: str, path: str) -> dict[str, object]:
    op: dict[str, object] = json.loads(render())["paths"][path][method]
    return op


def test_task_writes_require_an_idempotency_key_header() -> None:
    for method, path in [
        ("post", "/api/v1/tasks"),
        ("patch", "/api/v1/tasks/{task_id}"),
        ("delete", "/api/v1/tasks/{task_id}"),
    ]:
        params = _operation(method, path)["parameters"]
        assert isinstance(params, list)
        header = next(p for p in params if p["name"] == "Idempotency-Key")
        assert header["in"] == "header" and header["required"], (method, path)
        assert header["schema"]["format"] == "uuid", (method, path)


def test_api_token_is_write_only_and_never_part_of_a_response() -> None:
    schemas = json.loads(render())["components"]["schemas"]
    token = schemas["GitHubIntegrationCreate"]["properties"]["api_token"]
    assert token["writeOnly"] is True
    assert "api_token" not in schemas["IntegrationOut"]["properties"]
    assert "token_set" in schemas["IntegrationOut"]["properties"]


@pytest.mark.usefixtures("any_role")
async def test_missing_idempotency_key_is_a_validation_error(
    stub_client: httpx.AsyncClient,
) -> None:
    body = {"project_id": str(uuid.uuid4()), "title": "写周报"}
    missing = await stub_client.post("/api/v1/tasks", json=body, headers=WORKSPACE)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_FAILED"
    assert ["header", "Idempotency-Key"] in [
        e["loc"] for e in missing.json()["error"]["details"]["errors"]
    ]
    invalid = await stub_client.post(
        "/api/v1/tasks", json=body, headers={**WORKSPACE, "Idempotency-Key": "not-a-uuid"}
    )
    assert invalid.status_code == 422
    accepted = await stub_client.post(
        "/api/v1/tasks", json=body, headers={**WORKSPACE, "Idempotency-Key": str(uuid.uuid4())}
    )
    assert accepted.status_code == 501  # 形态正确，处理逻辑在 T-0020


@pytest.mark.usefixtures("any_role")
async def test_task_payload_rules(stub_client: httpx.AsyncClient) -> None:
    headers = {**WORKSPACE, "Idempotency-Key": str(uuid.uuid4())}
    path = f"/api/v1/tasks/{uuid.uuid4()}"
    cases = {
        "只有 version": {"version": "v1"},
        "标题为 null": {"version": "v1", "title": None},
        "优先级越界": {"version": "v1", "priority": 6},
        "截止时间没有时区": {"version": "v1", "due_at": "2026-11-05T18:00:00"},
        "多余字段": {"version": "v1", "assignee": "bob"},  # 单人版没有负责人
        "标签为 null": {"version": "v1", "labels": None},
    }
    for name, body in cases.items():
        response = await stub_client.patch(path, json=body, headers=headers)
        assert response.status_code == 422, name
    cleared = await stub_client.patch(path, json={"version": "v1", "due_at": None}, headers=headers)
    assert cleared.status_code == 501  # 显式清除截止时间是合法的
    no_version = await stub_client.delete(path, headers=headers)
    assert no_version.status_code == 422


@pytest.mark.usefixtures("any_role")
async def test_integration_payload_rules_do_not_echo_the_token(
    stub_client: httpx.AsyncClient,
) -> None:
    pasted = "not-a-github-token-0123456789"
    removed = {"provider": "vikunja", "name": "Vikunja", "base_url": "http://vikunja:3456"}
    for body in [{**removed, "api_token": "tk_example"}, {**removed, "api_token": pasted}]:
        response = await stub_client.post("/api/v1/integrations", json=body, headers=WORKSPACE)
        assert response.status_code == 422  # T-0082 移除了 Vikunja
        assert pasted not in response.text
    nothing = await stub_client.patch(
        f"/api/v1/integrations/{uuid.uuid4()}", json={"version": 1}, headers=WORKSPACE
    )
    assert nothing.status_code == 422


@pytest.mark.usefixtures("any_role")
async def test_project_payload_rules(stub_client: httpx.AsyncClient) -> None:
    for name, body in {
        "没有标题": {},
        "标题为空": {"title": "  "},
        "标题太长": {"title": "x" * 121},
        "多余字段": {"title": "a", "owner": "bob"},
    }.items():
        response = await stub_client.post("/api/v1/projects", json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    ok = await stub_client.post("/api/v1/projects", json={"title": "工作"}, headers=WORKSPACE)
    assert ok.status_code == 501
    path = f"/api/v1/projects/{uuid.uuid4()}"
    patches: dict[str, dict[str, object]] = {
        "只有 version": {"version": 1},
        "version 不是整数": {"version": "v1", "title": "a"},
        "archived 为 null": {"version": 1, "archived": None},
    }
    for name, patch in patches.items():
        response = await stub_client.patch(path, json=patch, headers=WORKSPACE)
        assert response.status_code == 422, name
    assert (await stub_client.delete(path, headers=WORKSPACE)).status_code == 422  # 没有 version
    assert (
        await stub_client.delete(path, params={"version": 1}, headers=WORKSPACE)
    ).status_code == 501


# ---------------------------------------------------------------- Sprint 4


def _schemas() -> dict[str, dict[str, object]]:
    schemas: dict[str, dict[str, object]] = json.loads(render())["components"]["schemas"]
    return schemas


def test_sprint_4_only_adds_optional_fields_to_existing_schemas() -> None:
    """契约第 1 节：已有字段与枚举不变，新字段在生成的 TypeScript 类型里是可选的。"""
    schemas = _schemas()
    for name, field in [
        ("IntegrationOut", "repositories"),
        ("IntegrationTestResult", "repositories"),
        ("SyncState", "retry_at"),
        ("ModuleStatus", "retry_at"),
    ]:
        assert field in schemas[name]["properties"], (name, field)  # type: ignore[operator]
        assert field not in schemas[name].get("required", []), (name, field)  # type: ignore[operator]
    check = schemas["IntegrationCheck"]["properties"]["name"]  # type: ignore[index]
    # T-0082 移除 Vikunja 时去掉了 tasks_read（只有 Vikunja 用）
    assert set(check["enum"]) == {"reachable", "version", "token", "webhooks"}


def test_integration_create_is_github_only() -> None:
    body = _operation("post", "/api/v1/integrations")["requestBody"]
    schema = body["content"]["application/json"]["schema"]  # type: ignore[index]
    assert schema["$ref"].endswith("/GitHubIntegrationCreate")
    github = _schemas()["GitHubIntegrationCreate"]["properties"]
    assert github["api_token"]["writeOnly"] is True  # type: ignore[index]
    assert "base_url" not in github  # type: ignore[operator]


@pytest.mark.usefixtures("any_role")
async def test_github_integration_payload_rules(stub_client: httpx.AsyncClient) -> None:
    classic = "ghp_classic0123456789abcdefghij"
    base = {"provider": "github", "name": "GitHub", "repositories": ["octo/hello-world"]}
    wrong = await stub_client.post(
        "/api/v1/integrations", json={**base, "api_token": classic}, headers=WORKSPACE
    )
    assert wrong.status_code == 422
    assert classic not in wrong.text
    # 测试用的假 Token
    token = "github_pat_11AAAAAAA0example"  # noqa: S105
    cases = {
        "没有仓库": {**base, "repositories": []},
        "仓库格式": {**base, "repositories": ["octo"]},
        "仓库重复": {**base, "repositories": ["octo/a", "Octo/A"]},
        "仓库是 ..": {**base, "repositories": ["octo/.."]},
        "不接受 base_url": {**base, "base_url": "https://github.example.com"},
        "仓库太多": {**base, "repositories": [f"octo/r{i}" for i in range(21)]},
    }
    for name, payload in cases.items():
        response = await stub_client.post(
            "/api/v1/integrations", json={**payload, "api_token": token}, headers=WORKSPACE
        )
        assert response.status_code == 422, name
    ok = await stub_client.post(
        "/api/v1/integrations", json={**base, "api_token": token}, headers=WORKSPACE
    )
    assert ok.status_code == 501  # 形态正确，GitHub 集成在 T-0028 实现
    patch = f"/api/v1/integrations/{uuid.uuid4()}"
    invalid: list[dict[str, object]] = [
        {"version": 1, "api_token": "sk_other"},
        {"version": 1, "repositories": None},
    ]
    for update in invalid:
        response = await stub_client.patch(patch, json=update, headers=WORKSPACE)
        assert response.status_code == 422, update
    accepted = await stub_client.patch(
        patch,
        json={"version": 1, "api_token": token, "repositories": ["octo/b"]},
        headers=WORKSPACE,
    )
    assert accepted.status_code == 501


@pytest.mark.usefixtures("any_role")
async def test_knowledge_source_and_bookmark_payload_rules(
    stub_client: httpx.AsyncClient,
) -> None:
    for url in [
        "http://169.254.169.254/latest",
        "javascript:alert(1)",
        "https://user:pw@affine.example.com",
        "https://affine.example.com/?a=1",
        "http://0.0.0.0:3010",
    ]:
        response = await stub_client.post(
            "/api/v1/knowledge/sources",
            json={"provider": "affine", "name": "AFFiNE", "base_url": url},
            headers=WORKSPACE,
        )
        assert response.status_code == 422, url
        assert "pw@" not in response.text
    for url in ["https://affine.example.com/affine/", "http://localhost:3010"]:
        ok = await stub_client.post(
            "/api/v1/knowledge/sources",
            json={"provider": "affine", "name": "AFFiNE", "base_url": url},
            headers=WORKSPACE,
        )
        assert ok.status_code == 501, url  # 本机开发的回环地址允许
    doc = "https://affine.example.com/workspace/w/d"
    linked = str(uuid.uuid4())
    bookmark_cases = {
        "没有标题": {"url": doc},
        "可见性": {"url": doc, "title": "设计", "visibility": "team"},
        "重复关联": {"url": doc, "title": "设计", "linked_resource_ids": [linked, linked]},
        "多余字段": {"url": doc, "title": "设计", "body": "正文"},
    }
    for name, body in bookmark_cases.items():
        response = await stub_client.post(
            "/api/v1/knowledge/bookmarks", json=body, headers=WORKSPACE
        )
        assert response.status_code == 422, name
    created = await stub_client.post(
        "/api/v1/knowledge/bookmarks", json={"url": doc, "title": "设计"}, headers=WORKSPACE
    )
    assert created.status_code == 501  # 链接是否属于知识来源在服务端校验（T-0027）
    path = f"/api/v1/knowledge/bookmarks/{uuid.uuid4()}"
    for body in [{"version": 1}, {"version": 1, "title": None}]:
        response = await stub_client.patch(path, json=body, headers=WORKSPACE)
        assert response.status_code == 422, body
    no_version = await stub_client.delete(path, headers=WORKSPACE)
    assert no_version.status_code == 422


@pytest.mark.usefixtures("any_role")
async def test_calendar_event_payload_rules(stub_client: httpx.AsyncClient) -> None:
    timed: dict[str, object] = {
        "title": "评审",
        "timezone": "Asia/Shanghai",
        "start_at": "2026-11-02T10:00:00+08:00",
        "end_at": "2026-11-02T11:00:00+08:00",
    }
    all_day: dict[str, object] = {
        "title": "出差",
        "timezone": "Asia/Shanghai",
        "all_day": True,
        "start_date": "2026-11-02",
        "end_date": "2026-11-04",
    }
    cases = {
        "没有时区偏移": {**timed, "start_at": "2026-11-02T10:00:00"},
        "时区名无效": {**timed, "timezone": "China/Beijing"},
        "结束早于开始": {**timed, "end_at": "2026-11-02T09:00:00+08:00"},
        "超过 92 天": {**timed, "end_at": "2027-03-02T10:00:00+08:00"},
        "全天事件带时刻": {**all_day, "start_at": timed["start_at"]},
        "定时事件带日期": {**timed, "start_date": "2026-11-02"},
        "不支持重复": {**timed, "rrule": "FREQ=WEEKLY"},
    }
    for name, body in cases.items():
        response = await stub_client.post("/api/v1/calendar/events", json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    for body in [timed, all_day]:
        response = await stub_client.post("/api/v1/calendar/events", json=body, headers=WORKSPACE)
        assert response.status_code == 501, body["title"]
    path = f"/api/v1/calendar/events/{uuid.uuid4()}"
    switch = await stub_client.patch(
        path, json={"version": 1, "all_day": True, "start_date": "2026-11-02"}, headers=WORKSPACE
    )
    assert switch.status_code == 422  # 改 all_day 要同时给出一对日期
    ok = await stub_client.patch(path, json={"version": 1, "title": "改期"}, headers=WORKSPACE)
    assert ok.status_code == 501
    missing = await stub_client.get("/api/v1/calendar/events", headers=WORKSPACE)
    assert missing.status_code == 422  # start / end 必填
    ranged = await stub_client.get(
        "/api/v1/calendar/events",
        params={"start": "2026-11-01T00:00:00+08:00", "end": "2026-11-08T00:00:00+08:00"},
        headers=WORKSPACE,
    )
    assert ranged.status_code == 501


@pytest.mark.usefixtures("any_role")
async def test_search_and_developer_query_rules(stub_client: httpx.AsyncClient) -> None:
    for params in [{}, {"q": "   "}, {"q": "x" * 101}, {"q": "设计", "types": "doc"}]:
        response = await stub_client.get("/api/v1/search", params=params, headers=WORKSPACE)
        assert response.status_code == 422, params
    ok = await stub_client.get(
        "/api/v1/search", params={"q": "设计", "types": ["task", "bookmark"]}, headers=WORKSPACE
    )
    assert ok.status_code == 501
    bad = await stub_client.get(
        "/api/v1/developer/runs", params={"status": "broken"}, headers=WORKSPACE
    )
    assert bad.status_code == 422
    for path in ["repos", "issues", "pulls", "runs"]:
        response = await stub_client.get(f"/api/v1/developer/{path}", headers=WORKSPACE)
        assert response.status_code == 501, path


# ---------------------------------------------------------------- Sprint 5


def _properties(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = _schemas()[name]["properties"]  # type: ignore[assignment]
    return properties


def test_sprint_5_keeps_existing_schemas_unchanged() -> None:
    """契约第 1 节：前端按枚举索引的类型不加值；新模块与新组件类型不影响已有类型。"""
    schemas = _schemas()
    check = schemas["IntegrationCheck"]["properties"]["name"]  # type: ignore[index]
    assert set(check["enum"]) == {"reachable", "version", "token", "webhooks"}
    provider = schemas["IntegrationOut"]["properties"]["provider"]  # type: ignore[index]
    assert provider["const"] == "github" or set(provider["enum"]) == {"github"}
    modules = schemas["DashboardOverview"]["properties"]["modules"]  # type: ignore[index]
    assert modules["additionalProperties"]["$ref"].endswith("/ModuleStatus")


def test_event_stream_is_documented_as_server_sent_events() -> None:
    op = _operation("get", "/api/v1/events/stream")
    params = op["parameters"]
    assert isinstance(params, list)
    workspace = next(p for p in params if p["name"] == "workspace_id")
    assert workspace["in"] == "query" and workspace["required"]
    assert "X-Workspace-Id" not in {p["name"] for p in params}  # EventSource 不能带请求头
    responses = op["responses"]
    assert isinstance(responses, dict)
    stream = responses["200"]["content"]["text/event-stream"]["itemSchema"]
    data = stream["properties"]["data"]
    assert data["contentSchema"]["$ref"].endswith("/InvalidateEvent")
    for code in ("401", "404", "422"):  # 推送开始之前的错误是普通 JSON
        assert list(responses[code]["content"]) == ["application/json"], code
    event = _properties("InvalidateEvent")
    assert set(event) == {"workspace_id", "topics", "reset", "at"}  # 只有失效提示，没有数据


def test_metrics_do_not_accept_queries() -> None:
    """浏览器只能选预设视图与时间范围，不能提交 PromQL（核验 ADR 第 3 节）。"""
    params = _operation("get", "/api/v1/operations/metrics")["parameters"]
    assert isinstance(params, list)
    assert {p["name"] for p in params} == {"range", "views", "X-Workspace-Id"}


@pytest.mark.usefixtures("any_role")
async def test_operations_query_rules(stub_client: httpx.AsyncClient) -> None:
    for params in [
        {"range": "30d"},
        {"views": "cpu_usage"},
        {"query": "up"},
        {"views": 'up{job="api"}'},
    ]:
        response = await stub_client.get(
            "/api/v1/operations/metrics", params=params, headers=WORKSPACE
        )
        expected = 501 if "query" in params else 422  # 不认识的查询参数被忽略，不会执行
        assert response.status_code == expected, params
    ok = await stub_client.get(
        "/api/v1/operations/metrics",
        params={"range": "7d", "views": ["latency_p95", "error_rate"]},
        headers=WORKSPACE,
    )
    assert ok.status_code == 501
    services = await stub_client.get("/api/v1/operations/services", headers=WORKSPACE)
    assert services.status_code == 501


@pytest.mark.usefixtures("any_role")
async def test_notification_rules(stub_client: httpx.AsyncClient) -> None:
    some = str(uuid.uuid4())
    cases: dict[str, dict[str, object]] = {
        "空请求": {},
        "空 ids": {"ids": []},
        "all 为 false": {"all": False},
        "二者都给": {"ids": [some], "all": True},
        "ids 太多": {"ids": [str(uuid.uuid4()) for _ in range(101)]},
        "ids 不是 UUID": {"ids": ["1"]},
        "多余字段": {"all": True, "read": True},
    }
    for name, body in cases.items():
        response = await stub_client.patch("/api/v1/notifications", json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    valid: list[dict[str, object]] = [{"all": True}, {"ids": [some]}]
    for body in valid:
        response = await stub_client.patch("/api/v1/notifications", json=body, headers=WORKSPACE)
        assert response.status_code == 501, body
    too_many = await stub_client.get(
        "/api/v1/notifications", params={"limit": 101}, headers=WORKSPACE
    )
    assert too_many.status_code == 422
    listed = await stub_client.get(
        "/api/v1/notifications", params={"unread_only": True}, headers=WORKSPACE
    )
    assert listed.status_code == 501


async def test_event_stream_rules(stub_client: httpx.AsyncClient) -> None:
    missing = await stub_client.get("/api/v1/events/stream", headers=WORKSPACE)
    assert missing.status_code == 422  # 工作空间用查询参数，不看请求头
    assert missing.headers["content-type"].startswith("application/json")
    invalid = await stub_client.get("/api/v1/events/stream", params={"workspace_id": "1"})
    assert invalid.status_code == 422
    # 成员校验、补发与重置的行为见 tests/test_notifications_api.py（T-0036）


# ---------------------------------------------------------------- V2 OC-1 契约


def _cases(cases: dict[str, dict[str, object]]) -> list[tuple[str, dict[str, object]]]:
    return list(cases.items())


def test_v2_admin_endpoints_use_the_session_and_agent_endpoints_the_instance_token() -> None:
    paths = json.loads(render())["paths"]
    for method, path in V2_AGENT_ENDPOINTS:
        assert paths[path][method]["security"] == [{"InstanceToken": []}], path
    for method, path in V2_ADMIN_ENDPOINTS:
        assert paths[path][method]["security"] == [{"SessionCookie": []}], path


def test_v2_internal_and_gateway_routes_are_not_documented() -> None:
    paths = json.loads(render())["paths"]
    assert not [
        p for p in paths if p.startswith(("/internal", "/api/v1/internal/", "/im/", "/mcp"))
    ]


def test_v2_secrets_and_credentials_never_appear_in_responses() -> None:
    schemas = _schemas()
    for name in ["AgentSecretCreate", "AgentSecretRotate"]:
        value = schemas[name]["properties"]["value"]  # type: ignore[index]
        assert value["writeOnly"] is True, name
    for name in [
        "AgentSecretOut",
        "AgentInstanceOut",
        "AgentCredentialOut",
        "SecretAccessOut",
        "AgentInstancePage",
    ]:
        props = schemas[name]["properties"]
        assert isinstance(props, dict)
        assert not set(props) & {"value", "token", "private_key", "client_private_key_pem"}, name
    approve = schemas["ApproveRequest"]["properties"]["password"]  # type: ignore[index]
    assert approve["anyOf"][0]["writeOnly"] is True


def test_v2_third_party_writes_require_an_idempotency_key_header() -> None:
    for method, path in [
        ("post", "/api/v1/im/conversations/{conversation_id}/messages"),
        ("post", "/api/v1/im/groups"),
    ]:
        params = _operation(method, path)["parameters"]
        assert isinstance(params, list)
        header = next(p for p in params if p["name"] == "Idempotency-Key")
        assert header["in"] == "header" and header["required"], (method, path)


def test_v2_approvals_require_if_match() -> None:
    for action in ["approve", "reject"]:
        params = _operation("post", f"/api/v1/ai/tool-executions/{{execution_id}}/{action}")[
            "parameters"
        ]
        assert isinstance(params, list)
        header = next(p for p in params if p["name"] == "If-Match")
        assert header["in"] == "header" and header["required"], action


def test_v2_keeps_existing_enums_and_only_adds_event_topics() -> None:
    schemas = _schemas()
    topics = schemas["InvalidateEvent"]["properties"]["topics"]["items"]["enum"]  # type: ignore[index]
    assert set(topics) == {
        "overview",
        "tasks",
        "projects",
        "knowledge",
        "calendar",
        "developer",
        "integrations",
        "notifications",
        "operations",
        "im",
        "agents",
        "ai",
        # 任务时间轴（T-0104）
        "timeline",
        # 项目 Issues（T-0123）
        "issues",
        # 新闻早报（T-0153）
        "news",
        # 插拔式模块的启用 / 停用（T-0159）、侧栏布局（T-0161）
        "modules",
        "navigation",
    }
    provider = schemas["IntegrationOut"]["properties"]["provider"]  # type: ignore[index]
    assert provider["const"] == "github" or set(provider["enum"]) == {"github"}


def test_v2_mcp_tools_match_the_plan() -> None:
    from pydantic import BaseModel

    from app.schemas.ai_tools import FORBIDDEN_TOOL_AREAS, TOOLS, PendingApproval
    from app.schemas.workspaces import Role

    by_risk: dict[str, set[str]] = {}
    for spec in TOOLS.values():
        by_risk.setdefault(spec.risk, set()).add(spec.name)
    assert by_risk == {
        "read": {
            "search",
            "list_tasks",
            "get_task",
            "list_projects",
            "calendar_agenda",
            "list_bookmarks",
            "list_knowledge_sources",
            "list_repos",
            "list_issues",
            "list_pull_requests",
            "service_status",
            "list_notifications",
            "dashboard_overview",
            "get_action_status",
            # 服务器状态：只看自己（T-0113）
            "get_host_status",
            # IM 会话目录、读自己的会话、发件记录（T-0117、T-0118）
            "im_list_targets",
            "im_read_messages",
            "im_outbox",
            # 知识库（T-0098，docs/v2/KNOWLEDGE_PLAN.md 3.7）
            "knowledge_search",
            "knowledge_get",
            "knowledge_list",
            "knowledge_brief",
            # 项目与任务的 README（T-0122）
            "readme_get",
            # 项目里的 Issues（T-0123）
            "issue_list",
            "issue_get",
            "issue_inbox",
            # Gmail 邮件：按实例的“查询邮件”开关（T-0129）
            "mail_accounts",
            "mail_search",
            "mail_get",
            "mail_thread",
            # 运行环境：只给打开了运行环境权限的实例（例外八，T-0137）
            "runner_templates",
            "runner_hosts",
            "env_find",
            "env_run",
            # 新闻早报：只给启用了早报的工作空间的实例（T-0153）
            "news_latest",
            "news_search",
            "news_by_date",
            "news_trending",
            "news_rss",
            "news_related",
            "news_briefing",
            # 大模型会话桥：只给打开了“大模型会话权限”的实例（T-0228）
            "llm_bridge_status",
            "llm_bridge_models",
            "llm_bridge_environments",
            "llm_bridge_session_list",
            "llm_bridge_session_get",
            "llm_bridge_turn_get",
        },
        "write": {
            "create_task",
            "update_task",
            "create_calendar_event",
            "create_bookmark",
            "mark_notification_read",
        },
        "critical": {"delete_task", "delete_calendar_event", "runner_request_capability"},
        # 直接执行、不审批（人类 2026-11-03 决定）
        "knowledge": {"knowledge_write", "knowledge_delete"},
        # 领取 / 放弃 / 完成任务：直接执行、不审批（人类 2026-11-04 决定，T-0111）
        "direct": {
            "claim_task",
            "release_task",
            "complete_task",
            # 项目管理：只给打开了项目管理权限的实例（T-0112）
            "create_project",
            "add_to_project",
            "list_peers",
            # 主动发消息：直接执行、不审批（人类 2026-11-04 决定，例外四，T-0118）
            "im_send",
            "im_cancel",
            # 唤醒：同样直接执行（例外四，T-0121）
            "schedule_wakeup",
            "cancel_wakeup",
            # 终止其他智能体的任务：只给打开了这个权限的实例（例外六，T-0126）
            "list_agent_runs",
            "stop_agent_run",
            # 改 README 的非自动部分：直接执行（例外五，T-0122）
            "readme_update",
            # 开 Issue、评论、更新：直接执行（例外五，T-0123）
            "issue_create",
            "issue_comment",
            "issue_update",
            # 发邮件：只给打开了发送权限的实例（例外七，T-0129）
            "mail_send",
            # 运行环境：建、用、停、删自己的容器环境，自己定义模板（例外八，T-0137 / T-0138）
            "runner_template_save",
            "env_create",
            "env_exec",
            "env_files",
            "env_setup",
            "env_update",
            "env_stop",
            "env_release",
            # 大模型会话桥：新建会话、发送、取消、归档，只给打开了“新建会话和发送”的实例。
            # 直接执行、不审批（人类 2026-11-13 批准，例外九，T-0228）
            "llm_bridge_session_create",
            "llm_bridge_send",
            "llm_bridge_turn_cancel",
            "llm_bridge_session_archive",
        },
    }
    for spec in TOOLS.values():
        assert spec.name.isidentifier() and spec.name.islower()
        assert not any(area in spec.name for area in FORBIDDEN_TOOL_AREAS), spec.name
        assert spec.input_model.model_config.get("extra") == "forbid", spec.name
        assert issubclass(spec.output_model, BaseModel)
        spec.input_model.model_json_schema()  # MCP 工具清单要用到，必须能生成
        if spec.risk == "read":
            assert spec.scope == "read" and spec.min_role == Role.viewer, spec.name
            assert spec.output_model is not PendingApproval, spec.name
        elif spec.risk in ("knowledge", "direct"):  # 直接执行，但仍要 read_write 与 member
            assert spec.scope == "read_write" and spec.min_role == Role.member, spec.name
            assert spec.output_model is not PendingApproval, spec.name
            # OpenClaw 把工具描述截到 1200 个字符（K-0 核验 3.2）
            assert len(spec.description) <= 1200, spec.name
        else:  # 写与高危：只生成待审批记录，不执行
            assert spec.scope == "read_write", spec.name
            assert spec.output_model is PendingApproval, spec.name


def test_v2_mcp_outputs_keep_free_text_under_untrusted() -> None:
    """来自第三方或用户输入的文字一律放在 untrusted 里（契约第 9.3 节）。"""
    from app.schemas.ai_tools import TOOLS

    free_text = {"title", "description", "note", "body", "name", "location", "context", "url"}

    def walk(schema: dict[str, object], defs: dict[str, dict[str, object]], seen: set[str]) -> None:
        props = schema.get("properties")
        if isinstance(props, dict):
            leaked = free_text & {k for k in props if k != "untrusted"}
            if schema.get("title") == "RunnerTemplateOut":  # 模板名受格式限制（小写字母数字）
                leaked -= {"name"}
            assert not leaked, (schema.get("title"), leaked)
            for key, sub in props.items():
                if key != "untrusted":
                    walk(sub, defs, seen)
        for key in ("items", "anyOf", "allOf"):
            sub = schema.get(key)
            for part in sub if isinstance(sub, list) else [sub] if isinstance(sub, dict) else []:
                walk(part, defs, seen)
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref not in seen:
            seen.add(ref)
            walk(defs[ref.rsplit("/", 1)[-1]], defs, seen)

    for spec in TOOLS.values():
        if spec.risk != "read" or spec.name == "get_action_status":
            continue
        schema = spec.output_model.model_json_schema()
        walk(schema, schema.get("$defs", {}), set())


def test_v2_openim_callback_samples_parse() -> None:
    """OC-0 实际抓到的回调载荷（open-im-server v3.8.3-patch.18）。"""
    from app.schemas.openim_callbacks import (
        CallbackResponse,
        DeleteFriendCallback,
        MsgCallback,
        UserStatusCallback,
    )

    msg = MsgCallback.model_validate(
        {
            "sendID": "ws_admin",
            "callbackCommand": "callbackBeforeSendSingleMsgCommand",
            "serverMsgID": "c0a649cc7a3906e2b21425acc0852c49",
            "clientMsgID": "68547bbdda84659ea4b1a5714203bcb5",
            "operationID": "45d7fa84a1334d3c81c9661f68600c1f",
            "senderPlatformID": 5,
            "senderNickname": "",
            "sessionType": 1,
            "msgFrom": 200,
            "contentType": 101,
            "status": 0,
            "sendTime": 1793465387450,
            "createTime": 1793465387449,
            "content": '{"content":"你好"}',
            "seq": 0,
            "atUserList": None,
            "faceURL": "",
            "ex": "",
            "recvID": "oc_a",
        }
    )
    assert (msg.send_id, msg.recv_id, msg.group_id, msg.session_type) == ("ws_admin", "oc_a", "", 1)
    group = MsgCallback.model_validate(
        {
            "sendID": "imAdmin",
            "callbackCommand": "callbackBeforeSendGroupMsgCommand",
            "serverMsgID": "ad54e6daf278466cf1f26e40062c2887",
            "clientMsgID": "4c49db1792e79cc1432bf9fd1a1836cc",
            "sessionType": 3,
            "contentType": 1501,
            "groupID": "3221856193",
        }
    )
    assert group.group_id == "3221856193" and group.recv_id == ""
    online = UserStatusCallback.model_validate(
        {
            "callbackCommand": "callbackAfterUserOnlineCommand",
            "operationID": "p_46_1",
            "platformID": 5,
            "platform": "Web",
            "userID": "oc_a",
            "seq": 1793465372741,
            "isAppBackground": False,
            "connID": "",
        }
    )
    assert (online.user_id, online.platform_id) == ("oc_a", 5)
    deleted = DeleteFriendCallback.model_validate(
        {
            "callbackCommand": "callbackAfterDeleteFriendCommand",
            "ownerUserID": "oc_a",
            "friendUserID": "ws_admin",
        }
    )
    assert deleted.friend_user_id == "ws_admin"
    assert CallbackResponse.allow().model_dump(by_alias=True) == {
        "actionCode": 0,
        "errCode": 0,
        "errMsg": "",
        "errDlt": "",
        "nextCode": 0,
    }
    reject = CallbackResponse.reject(5001).model_dump(by_alias=True)
    assert (reject["errCode"], reject["nextCode"], reject["errMsg"]) == (5001, 1, "RELAY_PAUSED")


@pytest.mark.usefixtures("any_role")
async def test_v2_instance_and_secret_payload_rules(stub_client: httpx.AsyncClient) -> None:
    create = "/api/v1/agents/instances"
    for name, body in _cases(
        {
            "缺网络模式": {"name": "a"},
            "未知网络模式": {"name": "a", "network_mode": "lan"},
            "名称为空": {"name": " ", "network_mode": "intranet"},
            "最高角色不能是 owner": {"name": "a", "network_mode": "intranet", "max_role": "owner"},
            "多余字段": {"name": "a", "network_mode": "intranet", "token": "x"},
        }
    ):
        response = await stub_client.post(create, json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    ok = await stub_client.post(
        create, json={"name": "a", "network_mode": "public"}, headers=WORKSPACE
    )
    assert ok.status_code == 501
    instance = f"{create}/{uuid.uuid4()}"
    for name, body in _cases(
        {
            "只有 version": {"version": 1},
            "不能改网络模式": {"version": 1, "network_mode": "public"},
            "null": {"version": 1, "name": None},
        }
    ):
        response = await stub_client.patch(instance, json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    grants = f"{instance}/secret-grants"
    some = str(uuid.uuid4())
    for name, body in _cases(
        {
            "ref_id 重复": {"version": 1, "grants": [{"secret_id": some, "ref_id": "k"}] * 2},
            "ref_id 格式": {"version": 1, "grants": [{"secret_id": some, "ref_id": "Bad-Id"}]},
            "太多": {
                "version": 1,
                "grants": [{"secret_id": some, "ref_id": f"k{i}"} for i in range(33)],
            },
        }
    ):
        response = await stub_client.put(grants, json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    bundle = await stub_client.post(
        f"{instance}/bundle", json={"version": 1, "previous": "never"}, headers=WORKSPACE
    )
    assert bundle.status_code == 422
    secret = "placeholder-" + uuid.uuid4().hex
    for name, body in _cases(
        {
            "缺值": {"name": "OpenAI"},
            "值太长": {"name": "OpenAI", "value": "x" * 8193},
            "名称格式": {"name": "-bad", "value": secret},
            "未知类型": {"name": "OpenAI", "kind": "password", "value": secret},
        }
    ):
        response = await stub_client.post("/api/v1/agents/secrets", json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
        assert secret not in response.text, name  # 校验错误不回显密钥
    created = await stub_client.post(
        "/api/v1/agents/secrets", json={"name": "OpenAI", "value": secret}, headers=WORKSPACE
    )
    assert created.status_code == 501


async def test_v2_agent_api_payload_rules(app: FastAPI, stub_client: httpx.AsyncClient) -> None:
    unauthenticated = await stub_client.post("/agent-api/v1/secrets/resolve", json={"ids": []})
    assert unauthenticated.status_code != 422  # 先认证再校验：未认证的请求看不到校验细节
    app.dependency_overrides[instance_principal] = lambda: None
    app.dependency_overrides[get_agent_service] = _ShapeOnlyService
    resolve = "/agent-api/v1/secrets/resolve"
    for name, body in _cases(
        {
            "空": {"ids": []},
            "太多": {"ids": [f"k{i}" for i in range(33)]},
            "格式": {"ids": ["../etc"]},
            "多余字段": {"ids": ["k"], "provider": "x"},
        }
    ):
        response = await stub_client.post(resolve, json=body)
        assert response.status_code == 422, name
    assert (await stub_client.post(resolve, json={"ids": ["openai_api_key"]})).status_code == 501
    heartbeat = await stub_client.post("/agent-api/v1/heartbeat", json={})
    assert heartbeat.status_code == 422


@pytest.mark.usefixtures("any_role")
async def test_v2_im_payload_rules(stub_client: httpx.AsyncClient) -> None:
    conversation = "/api/v1/im/conversations/si_oc_a_ws_b"
    headers = {**WORKSPACE, "Idempotency-Key": str(uuid.uuid4())}
    missing = await stub_client.post(
        f"{conversation}/messages", json={"kind": "text", "text": "hi"}, headers=WORKSPACE
    )
    assert missing.status_code == 422
    for name, body in _cases(
        {
            "文字缺正文": {"kind": "text"},
            "文字带图片": {"kind": "text", "text": "hi", "upload_id": str(uuid.uuid4())},
            "图片缺上传": {"kind": "image"},
            "太长": {"kind": "text", "text": "x" * 4001},
            "文件缺上传": {"kind": "file"},
            "视频带正文": {"kind": "video", "text": "hi", "upload_id": str(uuid.uuid4())},
            "未知类型": {"kind": "sticker", "text": "hi"},
        }
    ):
        response = await stub_client.post(f"{conversation}/messages", json=body, headers=headers)
        assert response.status_code == 422, name
    # 合法请求的处理（幂等、拒收、结果未知）由 test_im_api.py 对真实数据库覆盖（T-0055）
    bad_id = await stub_client.get("/api/v1/im/conversations/../../x/messages", headers=WORKSPACE)
    assert bad_id.status_code in {404, 422}
    for name, body in _cases({"空": {}, "null": {"muted": None}}):
        response = await stub_client.patch(conversation, json=body, headers=WORKSPACE)
        assert response.status_code == 422, name
    no_instance = await stub_client.post(
        "/api/v1/im/groups", json={"name": "g", "user_ids": [str(uuid.uuid4())]}, headers=headers
    )
    assert no_instance.status_code == 422
    policy = await stub_client.put(
        "/api/v1/im/groups/123/policy",
        json={"version": 1, "relay_enabled": True, "max_agent_streak": 0, "max_per_minute": 6},
        headers=WORKSPACE,
    )
    assert policy.status_code == 422


@pytest.mark.usefixtures("any_role")
async def test_v2_approval_rules(stub_client: httpx.AsyncClient) -> None:
    path = f"/api/v1/ai/tool-executions/{uuid.uuid4()}/approve"
    missing = await stub_client.post(path, json={}, headers=WORKSPACE)
    assert missing.status_code == 422
    assert ["header", "If-Match"] in [
        e["loc"] for e in missing.json()["error"]["details"]["errors"]
    ]
    bad = await stub_client.post(path, json={}, headers={**WORKSPACE, "If-Match": "abc"})
    assert bad.status_code == 422
    ok = await stub_client.post(path, json={}, headers={**WORKSPACE, "If-Match": '"3"'})
    assert ok.status_code == 501
    listed = await stub_client.get(
        "/api/v1/ai/tool-executions", params={"status": "approved"}, headers=WORKSPACE
    )
    assert listed.status_code == 422  # 没有 approved 这个状态：批准后直接 executing


async def test_v2_openim_callback_route_rules(stub_client: httpx.AsyncClient) -> None:
    short = await stub_client.post("/internal/openim/short/callbackAfterUserOnlineCommand")
    assert short.status_code == 422
    token = "t" * 32
    bad = await stub_client.post(f"/internal/openim/{token}/rm -rf")
    assert bad.status_code == 422
    # 没有部署 OpenIM（未配置地址与口令）时回调地址不存在（契约第 10 节；实现见 T-0055）
    missing = await stub_client.post(f"/internal/openim/{token}/callbackAfterUserOnlineCommand")
    assert missing.status_code == 404
