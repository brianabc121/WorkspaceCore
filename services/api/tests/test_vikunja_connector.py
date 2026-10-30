"""Vikunja Connector 与出站 HTTP 客户端的单元测试（T-0020）：不需要外部服务。

- 解析：零值日期、越界优先级、标签、字段缺失或类型变化（规范第 9 节“Schema 变化”）
- 分页、增量过滤、merge-patch 请求的格式
- 错误归类：401 / 403 / 404 / 422 / 429 / 5xx，不保存对方回显的内容
- 超时与重试：读请求有限次重试；写请求从不重试，结果未知时归为 TIMEOUT_UNKNOWN
- SSRF：私有地址、DNS 指向内网、重定向；熔断
- 与归档的 OpenAPI（infra/vikunja/openapi/）对照：用到的端点、参数与字段都存在
"""

import asyncio
import hashlib
import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.logging import request_id_var
from app.integrations.base import ConnectorError, Readable
from app.integrations.http import CircuitBreaker, ConnectorHttp, OutboundPolicy
from app.integrations.vikunja import (
    PAGE_SIZE,
    TaskConnector,
    VikunjaClient,
    VikunjaProject,
    VikunjaTask,
    WebhookRegistrar,
    error_from_response,
    parse_project,
    parse_task,
    project_source_url,
    task_source_url,
)
from tests.fake_vikunja import BASE_URL, TOKEN, ZERO, FakeVikunja

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI = REPO_ROOT / "infra" / "vikunja" / "openapi" / "vikunja-2.7.1-api-v2.json"
OPENAPI_SHA256 = "26f814bba64d350839b2ad4793f1b7181752f7ac21b233f5c5d2c7713c93f83a"

ALLOW_FAKE = OutboundPolicy(frozenset({"vikunja.test"}))


def http_for(
    transport: httpx.AsyncBaseTransport,
    *,
    policy: OutboundPolicy = ALLOW_FAKE,
    retries: int = 2,
    breaker: CircuitBreaker | None = None,
) -> ConnectorHttp:
    return ConnectorHttp(
        policy=policy,
        read_retries=retries,
        breaker=breaker or CircuitBreaker(),
        transport=transport,
        backoff_base_seconds=0,
    )


def client_for(fake: FakeVikunja, **options: Any) -> VikunjaClient:
    return VikunjaClient(http_for(fake.transport, **options), BASE_URL, TOKEN)


def raw_task(**overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": 7,
        "project_id": 3,
        "identifier": "#7",
        "title": "写周报",
        "description": "**加粗**",
        "done": False,
        "done_at": ZERO,
        "priority": 3,
        "due_date": "2026-11-01T09:30:00Z",
        "labels": [{"id": 1, "title": "工作"}, {"id": 2}],
        "created": "2026-10-30T08:00:00Z",
        "updated": "2026-10-30T08:05:00Z",
    }
    task.update(overrides)
    return task


# ---------------------------------------------------------------- 能力分层


def test_vikunja_client_implements_the_capability_protocols() -> None:
    client = client_for(FakeVikunja())
    # 这几行由 mypy 检查：VikunjaClient 的方法签名与规范第 7 节的能力分层一致
    readable: Readable[VikunjaProject, VikunjaTask] = client
    writable: TaskConnector = client
    webhooks: WebhookRegistrar = client
    assert readable.provider == writable.provider == "vikunja"
    assert webhooks is client


# ---------------------------------------------------------------- 解析


def test_parse_task_maps_zero_dates_priority_and_labels() -> None:
    task = parse_task(raw_task())
    assert task.due_at == datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    assert task.done_at is None  # 零值日期 → 未设置
    assert task.labels == ("工作",)  # 没有标题的标签忽略
    assert task.version == "2026-10-30T08:05:00Z"
    assert parse_task(raw_task(due_date=ZERO)).due_at is None
    # API 不校验优先级范围：越界的值按“未设置”处理
    assert parse_task(raw_task(priority=9)).priority == 0
    assert parse_task(raw_task(priority="high")).priority == 0
    assert parse_task(raw_task(labels=None)).labels == ()


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "7"},
        {"project_id": None},
        {"title": None},
        {"updated": None},
    ],
)
def test_parse_task_rejects_schema_changes(broken: dict[str, Any]) -> None:
    with pytest.raises(ConnectorError) as caught:
        parse_task(raw_task(**broken))
    assert caught.value.kind == "UNAVAILABLE"
    assert caught.value.reason == "invalid_response"


def test_parse_project_and_source_urls() -> None:
    project = parse_project({"id": 3, "title": "工作", "is_archived": True, "updated": ZERO})
    assert project.archived is True
    assert project.updated is None
    with pytest.raises(ConnectorError):
        parse_project({"title": "没有 id"})
    assert task_source_url("https://tasks.example.com/", 7) == "https://tasks.example.com/tasks/7"
    assert project_source_url("https://tasks.example.com", 3) == (
        "https://tasks.example.com/projects/3"
    )


# ---------------------------------------------------------------- 读取与分页


async def test_list_tasks_reads_every_page_with_markdown_descriptions() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    for n in range(PAGE_SIZE * 2 + 5):
        fake.add_task(project["id"], f"任务 {n}")
    tasks = await client_for(fake).list_tasks()
    assert len(tasks) == PAGE_SIZE * 2 + 5
    assert [t.id for t in tasks] == sorted(t.id for t in tasks)
    pages = fake.calls("GET", "/tasks")
    assert [r.url.params["page"] for r in pages] == ["1", "2", "3"]
    first = pages[0]
    assert first.url.params["per_page"] == str(PAGE_SIZE)
    assert first.url.params["format"] == "markdown"
    assert first.url.params["sort_by"] == "id"
    assert first.headers["Authorization"] == f"Bearer {TOKEN}"
    assert first.headers["User-Agent"].startswith("my-workspace/")


async def test_incremental_filter_and_find_created_tasks() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    old = "2026-01-01T00:00:00Z"
    fake.add_task(project["id"], "旧任务", created=old, updated=old)
    new = fake.add_task(project["id"], "新任务")
    client = client_for(fake)
    since = datetime.now(UTC) - timedelta(minutes=1)
    updated = await client.list_tasks(updated_since=since)
    assert [t.id for t in updated] == [new["id"]]
    assert fake.calls("GET", "/tasks")[-1].url.params["filter"].startswith("updated >= ")
    found = await client.find_created_tasks(project["id"], title="新任务", created_since=since)
    assert [t.id for t in found] == [new["id"]]
    assert await client.find_created_tasks(project["id"], title="别的", created_since=since) == []


async def test_list_projects_includes_archived_when_asked() -> None:
    fake = FakeVikunja()
    fake.add_project("进行中")
    fake.add_project("已归档", archived=True)
    client = client_for(fake)
    assert len(await client.list_projects(include_archived=True)) == 2
    assert [p.title for p in await client.list_projects(include_archived=False)] == ["进行中"]


async def test_instance_info_and_webhook_permission() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    client = client_for(fake)
    assert (await client.info()).version == "2.7.1"
    assert await client.can_manage_webhooks(project["id"]) is True
    fake.webhooks_allowed = False  # Token 缺少 projects_webhooks 权限时 Vikunja 返回 401
    assert await client.can_manage_webhooks(project["id"]) is False


async def test_request_id_is_forwarded() -> None:
    fake = FakeVikunja()
    reset = request_id_var.set("req-123")
    try:
        await client_for(fake).info()
    finally:
        request_id_var.reset(reset)
    assert fake.requests[-1].headers["X-Request-ID"] == "req-123"


# ---------------------------------------------------------------- 写入的请求格式


async def test_create_task_sends_markdown_and_seconds_precision_due_date() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    due = datetime(2026, 11, 2, 10, 0, 0, 123456, tzinfo=UTC)
    task = await client_for(fake).create_task(
        project["id"], title="新任务", description="- 一\n- 二", priority=2, due_at=due
    )
    request = fake.calls("POST", r"/projects/\d+/tasks")[0]
    assert request.url.params["format"] == "markdown"
    assert json.loads(request.content) == {
        "title": "新任务",
        "description": "- 一\n- 二",
        "priority": 2,
        "due_date": "2026-11-02T10:00:00Z",
    }
    assert task.due_at == due.replace(microsecond=0)


async def test_update_task_uses_merge_patch_and_null_clears_the_due_date() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    raw = fake.add_task(project["id"], "旧标题", due_date="2026-11-01T00:00:00Z")
    task = await client_for(fake).update_task(raw["id"], {"title": "新标题", "due_at": None})
    request = fake.calls("PATCH", r"/tasks/\d+")[0]
    assert request.headers["Content-Type"] == "application/merge-patch+json"
    assert request.headers["X-Vikunja-Format"] == "markdown"
    assert json.loads(request.content) == {"title": "新标题", "due_date": None}
    assert task.title == "新标题"
    assert task.due_at is None


# ---------------------------------------------------------------- 错误归类


def response(status: int, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    return httpx.Response(status, json=body or {}, headers=headers)


def test_error_classification_does_not_keep_external_content() -> None:
    unauthorized = error_from_response(response(401, {"code": 11, "message": "secret detail"}))
    assert (unauthorized.kind, unauthorized.resource_level) == ("AUTH_FAILED", False)
    assert "secret detail" not in str(unauthorized)
    forbidden = error_from_response(response(403, {"code": 3002}))
    assert (forbidden.kind, forbidden.resource_level) == ("AUTH_FAILED", True)
    assert error_from_response(response(404, {"code": 4002})).kind == "NOT_FOUND"
    invalid = error_from_response(
        response(
            422,
            {"errors": [{"location": "body.title", "message": "too short", "value": "TOP-SECRET"}]},
        )
    )
    assert invalid.kind == "VALIDATION_FAILED"
    assert invalid.errors == [("body.title", "too short")]
    assert "TOP-SECRET" not in repr(invalid.errors)
    limited = error_from_response(response(429, headers={"Retry-After": "12"}))
    assert (limited.kind, limited.retry_after) == ("RATE_LIMITED", 12)
    assert error_from_response(response(429, headers={"Retry-After": "soon"})).retry_after is None
    assert error_from_response(response(500)).kind == "UNAVAILABLE"
    assert error_from_response(httpx.Response(422, text="not json")).errors == []


async def test_invalid_json_body_is_classified_as_invalid_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))
    client = VikunjaClient(http_for(transport), BASE_URL, TOKEN)
    with pytest.raises(ConnectorError) as caught:
        await client.info()
    assert caught.value.reason == "invalid_response"


async def test_invalid_token_is_auth_failed() -> None:
    fake = FakeVikunja(token="tk_" + "b" * 40)
    with pytest.raises(ConnectorError) as caught:
        await client_for(fake).first_project_page()
    assert caught.value.kind == "AUTH_FAILED"
    assert caught.value.resource_level is False


# ---------------------------------------------------------------- 超时与重试


async def test_reads_retry_on_5xx_and_connection_errors() -> None:
    fake = FakeVikunja()
    fake.fault("GET", "/info", 503)
    fake.fault("GET", "/info", "connect")
    assert (await client_for(fake).info()).version == "2.7.1"
    assert len(fake.calls("GET", "/info")) == 3


async def test_reads_give_up_after_the_retry_limit() -> None:
    fake = FakeVikunja()
    fake.fault("GET", "/info", 503, times=10)
    with pytest.raises(ConnectorError) as caught:
        await client_for(fake, retries=1).info()
    assert caught.value.kind == "UNAVAILABLE"
    assert len(fake.calls("GET", "/info")) == 2


async def test_rate_limit_is_not_retried() -> None:
    fake = FakeVikunja()
    fake.fault("GET", "/info", 429, headers={"Retry-After": "5"})
    with pytest.raises(ConnectorError) as caught:
        await client_for(fake).info()
    assert (caught.value.kind, caught.value.retry_after) == ("RATE_LIMITED", 5)
    assert len(fake.calls("GET", "/info")) == 1


async def test_writes_are_never_retried() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    fake.fault("POST", r"/projects/\d+/tasks", 503)
    with pytest.raises(ConnectorError) as caught:
        await client_for(fake).create_task(
            project["id"], title="一次", description="", priority=0, due_at=None
        )
    assert caught.value.kind == "UNAVAILABLE"
    assert caught.value.not_sent is False  # 已发出：不能当成没有处理
    assert len(fake.calls("POST", r"/projects/\d+/tasks")) == 1
    assert fake.tasks == {}


async def test_lost_write_response_is_timeout_unknown() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    fake.fault("POST", r"/projects/\d+/tasks", "lose_response")
    with pytest.raises(ConnectorError) as caught:
        await client_for(fake).create_task(
            project["id"], title="可能创建了", description="", priority=0, due_at=None
        )
    assert caught.value.kind == "TIMEOUT_UNKNOWN"
    assert len(fake.tasks) == 1  # 对方实际已经创建：调用方必须先查询结果


async def test_connection_failure_on_write_is_not_sent() -> None:
    fake = FakeVikunja()
    project = fake.add_project("工作")
    fake.fault("POST", r"/projects/\d+/tasks", "connect")
    with pytest.raises(ConnectorError) as caught:
        await client_for(fake).create_task(
            project["id"], title="没发出", description="", priority=0, due_at=None
        )
    assert (caught.value.kind, caught.value.not_sent) == ("UNAVAILABLE", True)


async def test_redirects_are_not_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    client = VikunjaClient(http_for(httpx.MockTransport(handler)), BASE_URL, TOKEN)
    with pytest.raises(ConnectorError):
        await client.info()
    assert seen == [f"{BASE_URL}/api/v2/info"]


# ---------------------------------------------------------------- SSRF 与熔断


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:3456",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:3456",
        "http://[fd00::1]",
        "http://0.0.0.0",
        "http:///no-host",
    ],
)
async def test_private_destinations_are_rejected(url: str) -> None:
    with pytest.raises(ConnectorError) as caught:
        await OutboundPolicy().check(url)
    assert caught.value.reason == "destination_not_allowed"
    assert caught.value.not_sent is True


def fake_dns(monkeypatch: pytest.MonkeyPatch, answers: dict[str, list[str]] | Exception) -> None:
    async def getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        if isinstance(answers, Exception):
            raise answers
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in answers.get(host, [])
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)


async def test_hostnames_are_checked_after_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns(
        monkeypatch,
        {
            "tasks.example.com": ["93.184.215.14"],
            "rebind.example.com": ["93.184.215.14", "10.0.0.8"],
            "vikunja": ["172.18.0.4"],
        },
    )
    await OutboundPolicy().check("https://tasks.example.com")
    for url in ("https://rebind.example.com", "http://vikunja:3456", "https://empty.example.com"):
        with pytest.raises(ConnectorError) as caught:
            await OutboundPolicy().check(url)
        assert caught.value.reason == "destination_not_allowed"
    # 部署时显式允许的内网服务（Compose 里的 vikunja 容器）
    await OutboundPolicy(frozenset({"vikunja"})).check("http://vikunja:3456")


async def test_dns_failure_is_reported_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns(monkeypatch, socket.gaierror("no such host"))
    with pytest.raises(ConnectorError) as caught:
        await OutboundPolicy().check("https://missing.example.com")
    assert (caught.value.reason, caught.value.not_sent) == ("dns_failed", True)


async def test_policy_is_checked_before_sending() -> None:
    fake = FakeVikunja()
    client = VikunjaClient(
        http_for(fake.transport, policy=OutboundPolicy()), "http://127.0.0.1:3456", TOKEN
    )
    with pytest.raises(ConnectorError) as caught:
        await client.info()
    assert caught.value.reason == "destination_not_allowed"
    assert fake.requests == []


async def test_circuit_breaker_opens_and_half_opens() -> None:
    fake = FakeVikunja()
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=60)
    fake.fault("GET", "/info", 503, times=2)  # 只有 502–504 与连接错误计入熔断
    client = client_for(fake, retries=0, breaker=breaker)
    for _ in range(2):
        with pytest.raises(ConnectorError):
            await client.info()
    with pytest.raises(ConnectorError) as caught:
        await client.info()
    assert (caught.value.reason, caught.value.not_sent) == ("circuit_open", True)
    assert len(fake.calls("GET", "/info")) == 2  # 熔断期间不再发请求
    # 冷却期满：放行一次试探请求，成功后恢复
    breaker._opened_at = {key: at - 61 for key, at in breaker._opened_at.items()}
    assert (await client.info()).version == "2.7.1"
    assert (await client.info()).version == "2.7.1"


# ---------------------------------------------------------------- 与归档的 OpenAPI 对照


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    raw = OPENAPI.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == OPENAPI_SHA256, "归档的 OpenAPI 被改动了"
    spec: dict[str, Any] = json.loads(raw)
    return spec


def _param_names(spec: dict[str, Any], path: str, method: str) -> set[str]:
    item = spec["paths"][path]
    params = [*item.get("parameters", []), *item[method].get("parameters", [])]
    return {p["name"] for p in params if "name" in p}


def test_endpoints_and_parameters_exist_in_the_archived_openapi(openapi: dict[str, Any]) -> None:
    assert openapi["servers"] == [{"url": "/api/v2"}]
    used = {
        ("/info", "get"): set(),
        ("/projects", "get"): {"page", "per_page", "is_archived"},
        ("/projects/{project}/webhooks", "get"): set(),
        ("/projects/{project}/tasks", "get"): {"page", "per_page", "filter", "format"},
        ("/projects/{project}/tasks", "post"): {"format"},
        ("/tasks", "get"): {"page", "per_page", "filter", "sort_by", "order_by", "format"},
        ("/tasks/{task}", "get"): {"format"},
        ("/tasks/{task}", "patch"): set(),
        ("/tasks/{task}", "delete"): set(),
    }
    for (path, method), params in used.items():
        assert method in openapi["paths"].get(path, {}), f"{method.upper()} {path} 不存在"
        assert params <= _param_names(openapi, path, method), (path, method)


def test_fields_we_read_and_write_exist_in_the_archived_openapi(openapi: dict[str, Any]) -> None:
    schemas = openapi["components"]["schemas"]
    task_fields = set(schemas["Task"]["properties"])
    assert {
        "id",
        "project_id",
        "identifier",
        "title",
        "description",
        "done",
        "done_at",
        "priority",
        "due_date",
        "labels",
        "created",
        "updated",
    } <= task_fields
    assert {"id", "title", "is_archived", "updated"} <= set(schemas["Project"]["properties"])
    assert "title" in schemas["Label"]["properties"]
    assert "version" in schemas["VikunjaInfos"]["properties"]
    for page in ("PaginatedTask", "PaginatedProject"):
        assert {"items", "page", "per_page", "total", "total_pages"} <= set(
            schemas[page]["properties"]
        )
    patch = openapi["paths"]["/tasks/{task}"]["patch"]["requestBody"]["content"]
    merge = patch["application/merge-patch+json"]["schema"]
    assert merge.get("additionalProperties") is False  # 未知字段会 422：只能发这些字段
    assert {"title", "description", "priority", "due_date", "done", "project_id"} <= set(
        merge["properties"]
    )
