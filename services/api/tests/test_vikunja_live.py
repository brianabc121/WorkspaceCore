"""对真实 Vikunja 实例的 Connector 测试（可选，T-0020）。

设置以下环境变量时运行，否则跳过（不受 REQUIRE_INTEGRATION 影响，CI 不运行）：
- TEST_VIKUNJA_URL：例如 http://127.0.0.1:3456（make vikunja-up 启动的实例）
- TEST_VIKUNJA_TOKEN：该实例的 API Token，至少需要 projects（read_all、read_one、create、delete）
  与 tasks（read_all、read_one、create、update、delete）权限；测试会建一个临时项目，结束后删除

核对内存版 Vikunja（tests/fake_vikunja.py）依赖的真实行为：version（updated）在写入响应与随后的
读取里完全一致、Markdown 描述原样往返、merge-patch 的 null 清除截止时间、删除后 404、坏 Token 401。
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy, host_of
from app.integrations.vikunja import VikunjaClient


@dataclass
class Live:
    client: VikunjaClient
    url: str
    token: str
    project_id: int

    def other_client(self, token: str) -> VikunjaClient:
        return VikunjaClient(_http(self.url), self.url, token)


def _http(url: str) -> ConnectorHttp:
    return ConnectorHttp(policy=OutboundPolicy(frozenset({host_of(url)})), read_retries=0)


@pytest.fixture
async def live() -> AsyncIterator[Live]:
    url = os.environ.get("TEST_VIKUNJA_URL", "").rstrip("/")
    token = os.environ.get("TEST_VIKUNJA_TOKEN", "")
    if not url or not token:
        pytest.skip("未设置 TEST_VIKUNJA_URL / TEST_VIKUNJA_TOKEN，跳过真实 Vikunja 测试")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=f"{url}/api/v2", headers=headers, timeout=10) as raw:
        created = await raw.post("/projects", json={"title": f"ws-live-{uuid.uuid4().hex[:8]}"})
        assert created.status_code in {200, 201}, created.status_code
        project_id = created.json()["id"]
        try:
            yield Live(VikunjaClient(_http(url), url, token), url, token, project_id)
        finally:
            await raw.delete(f"/projects/{project_id}")


async def test_instance_and_read_permissions(live: Live) -> None:
    assert (await live.client.info()).version == "2.7.1"
    assert await live.client.first_project_page()
    assert isinstance(await live.client.first_task_page(), list)
    projects = await live.client.list_projects(include_archived=True)
    assert live.project_id in {p.id for p in projects}


async def test_task_lifecycle_matches_what_the_projection_relies_on(live: Live) -> None:
    client = live.client
    started = datetime.now(UTC)
    description = "- 第一项\n- **第二项**\n\n[链接](https://example.com)"
    due = (started + timedelta(days=2)).replace(microsecond=0)
    created = await client.create_task(
        live.project_id, title="真实实例任务", description=description, priority=3, due_at=due
    )
    assert (created.project_id, created.priority, created.due_at) == (live.project_id, 3, due)
    assert created.done is False and created.done_at is None

    fetched = await client.get_task(created.id)
    # version 比较依赖这一点：写入响应里的 updated 与随后读取的完全一致
    assert fetched.version == created.version
    assert fetched.description == description  # Markdown 原样往返

    found = await client.find_created_tasks(
        live.project_id, title="真实实例任务", created_since=started - timedelta(seconds=30)
    )
    assert [t.id for t in found] == [created.id]
    changed = await client.list_tasks(updated_since=started - timedelta(seconds=30))
    assert created.id in {t.id for t in changed}

    # Vikunja 按秒保存 updated：同一秒内的两次修改 version 相同（核验 ADR 第 5.5 节）
    await asyncio.sleep(1.1)
    updated = await client.update_task(
        created.id, {"title": "改过的标题", "due_at": None, "done": True}
    )
    assert (updated.title, updated.due_at, updated.done) == ("改过的标题", None, True)
    assert updated.done_at is not None
    assert updated.description == description  # merge-patch 不动没发送的字段
    assert updated.version != created.version
    assert (await client.get_task(created.id)).version == updated.version

    await client.delete_task(created.id)
    with pytest.raises(ConnectorError) as caught:
        await client.get_task(created.id)
    assert caught.value.kind == "NOT_FOUND"
    with pytest.raises(ConnectorError) as again:
        await client.delete_task(created.id)
    assert again.value.kind == "NOT_FOUND"


async def test_rejected_token_and_webhook_permission(live: Live) -> None:
    with pytest.raises(ConnectorError) as caught:
        await live.other_client("tk_" + "0" * 40).first_project_page()
    assert (caught.value.kind, caught.value.resource_level) == ("AUTH_FAILED", False)
    # 结果取决于 Token 是否带 projects_webhooks 权限；两种都是合法配置
    assert isinstance(await live.client.can_manage_webhooks(live.project_id), bool)


async def test_validation_errors_do_not_echo_input(live: Live) -> None:
    with pytest.raises(ConnectorError) as caught:
        await live.client.update_task(0, {"title": ""})
    assert caught.value.kind in {"VALIDATION_FAILED", "NOT_FOUND"}
    assert all("value" not in message for _, message in caught.value.errors)
