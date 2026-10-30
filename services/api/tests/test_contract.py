"""接口契约：Sprint 1（docs/decisions/2026-10-30-sprint-1-auth-contract.md）、
Sprint 2（…-sprint-2-dashboard-contract.md）、Sprint 3（…-sprint-3-tasks-contract.md）。"""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import (
    authenticated,
    get_db,
    get_integration_service,
    get_redis,
    get_task_service,
    get_workspace_service,
    workspace_admin,
    workspace_member,
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
    ("post", "/api/v1/webhooks/vikunja/{integration_id}"),
}
WORKSPACE = {"X-Workspace-Id": str(uuid.uuid4())}


@pytest.fixture
async def stub_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """只验证接口形态（校验、错误格式）：用桩替换数据库、Redis 和登录校验。"""
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_redis] = lambda: None
    app.dependency_overrides[authenticated] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def test_committed_openapi_matches_code() -> None:
    assert OUTPUT.read_text(encoding="utf-8") == render(), "请运行 make openapi 并提交"


def test_all_sprint_endpoints_are_declared() -> None:
    paths = json.loads(render())["paths"]
    declared = {(method, path) for path, ops in paths.items() for method in ops}
    assert declared >= SPRINT_1_ENDPOINTS | SPRINT_2_ENDPOINTS | SPRINT_3_ENDPOINTS


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
    for dependency in (workspace_viewer, workspace_member, workspace_admin):
        app.dependency_overrides[dependency] = lambda: None
    app.dependency_overrides[get_task_service] = _ShapeOnlyService
    app.dependency_overrides[get_integration_service] = _ShapeOnlyService


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
    token = schemas["IntegrationCreate"]["properties"]["api_token"]
    assert token["writeOnly"] is True
    assert "api_token" not in schemas["IntegrationOut"]["properties"]
    assert "token_set" in schemas["IntegrationOut"]["properties"]


def test_webhook_endpoint_is_documented_without_session() -> None:
    op = _operation("post", "/api/v1/webhooks/vikunja/{integration_id}")
    params = op["parameters"]
    assert isinstance(params, list)
    assert {p["name"] for p in params} == {"integration_id", "X-Vikunja-Signature"}
    assert "202" in op["responses"]  # type: ignore[operator]


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
        "多余字段": {"version": "v1", "labels": ["x"]},
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
    pasted = "not-a-vikunja-key-0123456789"
    base = {"provider": "vikunja", "name": "Vikunja", "base_url": "http://vikunja:3456"}
    wrong = await stub_client.post(
        "/api/v1/integrations", json={**base, "api_token": pasted}, headers=WORKSPACE
    )
    assert wrong.status_code == 422
    assert pasted not in wrong.text
    for url in ["ftp://vikunja", "http://user:pw@vikunja", "http://vikunja/?a=1", "vikunja"]:
        response = await stub_client.post(
            "/api/v1/integrations",
            json={**base, "base_url": url, "api_token": "tk_example"},
            headers=WORKSPACE,
        )
        assert response.status_code == 422, url
        assert "pw@" not in response.text
    ok = await stub_client.post(
        "/api/v1/integrations",
        json={**base, "base_url": "http://vikunja:3456/", "api_token": "tk_example"},
        headers=WORKSPACE,
    )
    assert ok.status_code == 501
    nothing = await stub_client.patch(
        f"/api/v1/integrations/{uuid.uuid4()}", json={"version": 1}, headers=WORKSPACE
    )
    assert nothing.status_code == 422


async def test_webhook_endpoint_does_not_require_a_session(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/webhooks/vikunja/{uuid.uuid4()}",
            content=b"{}",
            headers={"Content-Type": "application/json", "X-Vikunja-Signature": "00"},
        )
    # 没有会话也不是 401 UNAUTHORIZED：签名校验在 T-0021 实现
    assert response.status_code == 501
