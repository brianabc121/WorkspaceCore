"""Sprint 3 接口集成测试的公共部分：真实 PostgreSQL / Redis + 内存版 Vikunja。

内存版 Vikunja 见 tests/fake_vikunja.py。
"""

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import httpx
from pydantic import SecretStr

from tests.fake_vikunja import BASE_URL, TOKEN, FakeVikunja
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf

CREDENTIAL_KEY = secrets.token_hex(32)
# 内存版 Vikunja 的主机名在允许列表里；读请求不重试，让失败场景的测试保持简短
CONNECTOR_SETTINGS: dict[str, Any] = {
    "integration_allowed_hosts": "vikunja.test",
    "credential_encryption_key": SecretStr(CREDENTIAL_KEY),
    "connector_read_retries": 0,
}


def app_of(client: httpx.AsyncClient) -> Any:
    return cast(httpx.ASGITransport, client._transport).app


def use_fake(client: httpx.AsyncClient, fake: FakeVikunja) -> None:
    app_of(client).state.connectors.transport = fake.transport


def integration_body(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": "vikunja",
        "name": "我的 Vikunja",
        "base_url": BASE_URL,
        "public_url": "https://tasks.example.com",
        "api_token": TOKEN,
        **overrides,
    }


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    owner_id: uuid.UUID
    database_url: str
    fake: FakeVikunja
    make_client: ClientFactory

    def headers(self, *, write: bool = False, key: uuid.UUID | None = None) -> dict[str, str]:
        headers = {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        return headers

    async def add_integration(self, **overrides: Any) -> httpx.Response:
        return await self.client.post(
            "/api/v1/integrations",
            json=integration_body(**overrides),
            headers=self.headers(write=True),
        )

    async def test_integration(self, integration_id: str) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/integrations/{integration_id}/test", headers=self.headers(write=True)
        )
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    async def connect(self) -> dict[str, Any]:
        """保存并测试集成；测试通过后会立即全量同步。"""
        created = await self.add_integration()
        assert created.status_code == 201, created.text
        result = await self.test_integration(created.json()["id"])
        assert result["ok"] is True, result
        return await self.integration()

    async def integration(self) -> dict[str, Any]:
        response = await self.client.get("/api/v1/integrations", headers=self.headers())
        assert response.status_code == 200, response.text
        items: list[dict[str, Any]] = response.json()["items"]
        assert len(items) == 1
        return items[0]

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(path, params=params, headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def projects(self) -> dict[str, str]:
        """项目标题 → 投影 UUID。"""
        items = (await self.get("/api/v1/projects", include_archived=True))["items"]
        return {item["title"]: item["id"] for item in items}

    async def tasks(self, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = (await self.get("/api/v1/tasks", **params))["items"]
        return items

    async def task_by_title(self, title: str) -> dict[str, Any]:
        matches = [t for t in await self.tasks(status="all") if t["title"] == title]
        assert len(matches) == 1, matches
        return matches[0]

    async def create_task(
        self, body: dict[str, Any], key: uuid.UUID | None = None
    ) -> httpx.Response:
        return await self.client.post(
            "/api/v1/tasks", json=body, headers=self.headers(write=True, key=key or uuid.uuid4())
        )

    async def update_task(
        self, task_id: str, body: dict[str, Any], key: uuid.UUID | None = None
    ) -> httpx.Response:
        return await self.client.patch(
            f"/api/v1/tasks/{task_id}",
            json=body,
            headers=self.headers(write=True, key=key or uuid.uuid4()),
        )

    async def delete_task(
        self, task_id: str, version: str, key: uuid.UUID | None = None
    ) -> httpx.Response:
        return await self.client.delete(
            f"/api/v1/tasks/{task_id}",
            params={"version": version},
            headers=self.headers(write=True, key=key or uuid.uuid4()),
        )

    @asynccontextmanager
    async def member(self, email: str, role: str) -> AsyncIterator[httpx.AsyncClient]:
        """以另一个成员身份登录的客户端（同样连接内存版 Vikunja）。"""
        password = "member password 123456"  # noqa: S105 - 测试密码
        user_id = await add_user(self.database_url, email, password)
        await add_member(self.database_url, uuid.UUID(self.workspace_id), user_id, role)
        async with self.make_client(**CONNECTOR_SETTINGS) as other:
            use_fake(other, self.fake)
            login = await other.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text
            yield other


@asynccontextmanager
async def open_env(make_client: ClientFactory, database_url: str) -> AsyncIterator[Env]:
    fake = FakeVikunja()
    async with make_client(**CONNECTOR_SETTINGS) as client:
        use_fake(client, fake)
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201, setup.text
        data = setup.json()
        yield Env(
            client=client,
            workspace_id=data["workspaces"][0]["id"],
            owner_id=uuid.UUID(data["user"]["id"]),
            database_url=database_url,
            fake=fake,
            make_client=make_client,
        )
