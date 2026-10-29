"""Sprint 1 接口契约（docs/decisions/2026-10-30-sprint-1-auth-contract.md）。"""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import authenticated, get_db, get_redis
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


def test_all_sprint_1_endpoints_are_declared() -> None:
    paths = json.loads(render())["paths"]
    declared = {(method, path) for path, ops in paths.items() for method in ops}
    assert declared >= SPRINT_1_ENDPOINTS


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


async def test_unimplemented_endpoint_uses_unified_error_format(
    stub_client: httpx.AsyncClient,
) -> None:
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
    present = await stub_client.get(
        "/api/v1/audit-logs", headers={"X-Workspace-Id": str(uuid.uuid4())}
    )
    assert present.status_code == 501
