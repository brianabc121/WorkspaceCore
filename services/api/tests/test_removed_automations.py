"""移除工作流后，旧入口不可用，其余工作台能力仍注册。"""

import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.commands.connect import parser
from app.domain.stream import _event
from app.schemas.ai_tools import TOOLS
from app.schemas.notifications import InvalidateEvent


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/automations"),
        ("GET", "/api/v1/automations/connection"),
        ("POST", "/api/v1/automations/00000000-0000-0000-0000-000000000001/trigger"),
        ("GET", "/api/v1/app-logins"),
        ("POST", "/api/v1/app-logins/n8n/reveal"),
    ],
)
async def test_retired_routes_return_not_found(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)
    assert response.status_code == 404


def test_retired_tools_are_absent_and_core_tools_remain() -> None:
    assert {"list_automations", "trigger_automation"}.isdisjoint(TOOLS)
    assert {"list_tasks", "create_task", "list_notifications", "get_action_status"} <= TOOLS.keys()
    assert {"runner_templates", "env_find", "env_create", "env_exec"} <= TOOLS.keys()


def test_retired_api_contract_is_absent_and_core_endpoints_remain(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    assert not any(path.startswith(("/api/v1/automations", "/api/v1/app-logins")) for path in paths)
    assert {
        "/api/v1/tasks",
        "/api/v1/notifications",
        "/api/v1/events/stream",
        "/api/v1/operations/services",
        "/api/v1/ai/tool-executions",
    } <= paths.keys()


def test_old_stream_entries_keep_retained_invalidation_topics() -> None:
    # Redis 的旧事件在升级后仍可能被重放；保留通知失效，不再输出旧工作流主题。
    event = _event(uuid.uuid4(), "1-0", ["automations", "notifications"])
    assert isinstance(event.data, InvalidateEvent)
    assert event.data.topics == ["notifications"]


@pytest.mark.parametrize(
    "argv",
    [
        ["n8n", "--owner-email", "owner@example.com"],
        ["n8n-owner"],
        ["public-urls", "--n8n", "https://legacy.example.com"],
        ["save-login", "--app", "n8n", "--username", "owner@example.com"],
    ],
)
def test_retired_connect_commands_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        parser().parse_args(argv)
    assert error.value.code == 2


def test_retained_connect_commands_are_available() -> None:
    assert parser().parse_args(["status"]).command == "status"
    assert parser().parse_args(["storage-check"]).command == "storage-check"
