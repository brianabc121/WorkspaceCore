"""接口集成测试的公共部分：真实 PostgreSQL / Redis，项目与任务用工作台自己的接口创建（T-0082）。"""

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import httpx
from pydantic import SecretStr

from app.domain.sync import SyncContext
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf

CREDENTIAL_KEY = secrets.token_hex(32)
# 读请求不重试，让失败场景的测试保持简短；需要访问内存版第三方的测试自己加 integration_allowed_hosts
CONNECTOR_SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(CREDENTIAL_KEY),
    "connector_read_retries": 0,
}


class RecordingQueue:
    """代替 ARQ 的作业队列：只记录投递了什么（Worker 的作业由测试直接调用）。"""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []
        self.fail = False

    async def enqueue(self, function: str, *args: Any) -> None:
        if self.fail:
            raise ConnectionError("queue unavailable")
        self.jobs.append((function, args))

    def names(self) -> list[str]:
        return [name for name, _ in self.jobs]


def app_of(client: httpx.AsyncClient) -> Any:
    return cast(httpx.ASGITransport, client._transport).app


def use_queue(client: httpx.AsyncClient) -> RecordingQueue:
    queue = RecordingQueue()
    app_of(client).state.jobs = queue
    return queue


def sync_context(client: httpx.AsyncClient) -> SyncContext:
    """Worker 作业用的上下文：与这个客户端的应用共用数据库、Redis、Connector。"""
    app = app_of(client)
    return SyncContext(
        sessions=app.state.sessionmaker,
        runtime=app.state.connectors,
        redis=app.state.redis,
        settings=app.state.settings,
    )


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    owner_id: uuid.UUID
    database_url: str
    make_client: ClientFactory
    queue: RecordingQueue

    @property
    def sync(self) -> SyncContext:
        return sync_context(self.client)

    def headers(self, *, write: bool = False, key: uuid.UUID | None = None) -> dict[str, str]:
        headers = {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        return headers

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(path, params=params, headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    # ------------------------------------------------------------ 项目

    async def create_project(self, body: dict[str, Any]) -> httpx.Response:
        return await self.client.post(
            "/api/v1/projects", json=body, headers=self.headers(write=True)
        )

    async def update_project(self, project_id: str, body: dict[str, Any]) -> httpx.Response:
        return await self.client.patch(
            f"/api/v1/projects/{project_id}", json=body, headers=self.headers(write=True)
        )

    async def delete_project(self, project_id: str, version: int) -> httpx.Response:
        return await self.client.delete(
            f"/api/v1/projects/{project_id}",
            params={"version": version},
            headers=self.headers(write=True),
        )

    async def add_project(
        self, title: str, *, description: str = "", archived: bool = False
    ) -> dict[str, Any]:
        """新建一个项目（archived=True 时再归档），返回项目。"""
        response = await self.create_project({"title": title, "description": description})
        assert response.status_code == 201, response.text
        project: dict[str, Any] = response.json()
        if archived:
            updated = await self.update_project(
                project["id"], {"version": project["version"], "archived": True}
            )
            assert updated.status_code == 200, updated.text
            project = updated.json()
        return project

    async def projects(self) -> dict[str, str]:
        """项目标题 → UUID（含已归档）。"""
        items = (await self.get("/api/v1/projects", include_archived=True))["items"]
        return {item["title"]: item["id"] for item in items}

    # ------------------------------------------------------------ 任务

    async def tasks(self, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = (await self.get("/api/v1/tasks", **params))["items"]
        return items

    async def titles(self) -> list[str]:
        """所有任务（含已完成）的标题，排好序。"""
        return sorted(t["title"] for t in await self.tasks(status="all", limit=100))

    async def task_by_title(self, title: str) -> dict[str, Any]:
        matches = [t for t in await self.tasks(status="all", limit=100) if t["title"] == title]
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

    async def add_task(
        self, project_id: str, title: str, *, done: bool = False, **fields: Any
    ) -> dict[str, Any]:
        """新建一个任务（done=True 时再标记完成），返回任务。"""
        response = await self.create_task({"project_id": project_id, "title": title, **fields})
        assert response.status_code == 201, response.text
        task: dict[str, Any] = response.json()
        if done:
            updated = await self.update_task(task["id"], {"version": task["version"], "done": True})
            assert updated.status_code == 200, updated.text
            task = updated.json()
        return task

    @asynccontextmanager
    async def member(self, email: str, role: str) -> AsyncIterator[httpx.AsyncClient]:
        """以另一个成员身份登录的客户端。"""
        password = "member password 123456"  # noqa: S105 - 测试密码
        user_id = await add_user(self.database_url, email, password)
        await add_member(self.database_url, uuid.UUID(self.workspace_id), user_id, role)
        async with self.make_client(**CONNECTOR_SETTINGS) as other:
            use_queue(other)
            login = await other.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text
            yield other


@asynccontextmanager
async def open_env(
    make_client: ClientFactory, database_url: str, **overrides: Any
) -> AsyncIterator[Env]:
    async with make_client(**{**CONNECTOR_SETTINGS, **overrides}) as client:
        queue = use_queue(client)
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201, setup.text
        data = setup.json()
        yield Env(
            client=client,
            workspace_id=data["workspaces"][0]["id"],
            owner_id=uuid.UUID(data["user"]["id"]),
            database_url=database_url,
            make_client=make_client,
            queue=queue,
        )
