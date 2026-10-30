"""Sprint 1 接口契约（docs/decisions/2026-10-30-sprint-1-auth-contract.md）。"""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import authenticated, get_db, get_redis, get_workspace_service
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


def test_all_sprint_1_and_2_endpoints_are_declared() -> None:
    paths = json.loads(render())["paths"]
    declared = {(method, path) for path, ops in paths.items() for method in ops}
    assert declared >= SPRINT_1_ENDPOINTS | SPRINT_2_ENDPOINTS


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
