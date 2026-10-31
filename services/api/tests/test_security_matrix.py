"""接口安全矩阵（T-0043，规范第 12 节）：按 OpenAPI 枚举全部 /api/v1 接口逐个检查。

新增的接口自动纳入检查。

- 未登录：除首次安装、登录、Webhook 外，一律 401
- 写请求没有 CSRF Token：一律 403 CSRF_FAILED
- 非成员带别人的工作空间 ID：一律 404（与不存在的工作空间一样，不暴露存在与否）
- viewer：工作空间内的写接口一律 403（只改自己通知的已读状态除外）

鉴权、CSRF、成员与角色检查大多在依赖里，先于请求体校验执行，
所以多数接口用占位的 ID 与空请求体就能检查；
少数在业务层检查成员与角色的接口（BODIES）给一个合法的请求体。
"""

import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.helpers import BOB, OWNER, ClientFactory, add_member, add_user, add_workspace, csrf

pytestmark = pytest.mark.integration

OPENAPI = Path(__file__).resolve().parent.parent / "openapi.json"
# 不需要会话的接口：首次安装（凭安装凭据）、登录、Webhook（凭签名）
PUBLIC = {
    ("POST", "/api/v1/setup"),
    ("GET", "/api/v1/setup/status"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/webhooks/vikunja/{integration_id}"),
}
# viewer 也可以执行的写操作：只改自己的通知
VIEWER_WRITES = {("PATCH", "/api/v1/notifications")}
CAROL = {"email": "carol@example.com", "password": "carol password 123456"}
# 成员与角色在业务层检查的接口：空请求体会先得到 422（对任何工作空间都一样，不暴露是否存在）
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("PATCH", "/api/v1/workspaces/{workspace_id}"): {"name": "安全矩阵改的名字"},
}


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    headers: frozenset[str]

    @property
    def is_write(self) -> bool:
        return self.method != "GET"

    @property
    def in_workspace(self) -> bool:
        return "X-Workspace-Id" in self.headers or self.path == "/api/v1/events/stream"

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def operations() -> list[Operation]:
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    found = []
    for path, methods in spec["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for method, op in methods.items():
            headers = frozenset(p["name"] for p in op.get("parameters", []) if p["in"] == "header")
            found.append(Operation(method.upper(), path, headers))
    return found


async def call(
    client: httpx.AsyncClient,
    op: Operation,
    *,
    workspace: uuid.UUID | None = None,
    with_csrf: bool = True,
) -> httpx.Response:
    path = re.sub(r"\{workspace_id\}", str(workspace or uuid.uuid4()), op.path)
    path = re.sub(r"\{[a-z_]+\}", lambda _: str(uuid.uuid4()), path)
    headers: dict[str, str] = {"Idempotency-Key": str(uuid.uuid4())}
    params: dict[str, Any] = {}
    if workspace:
        headers["X-Workspace-Id"] = str(workspace)
        if op.path == "/api/v1/events/stream":
            params["workspace_id"] = str(workspace)
    if with_csrf and op.is_write:
        headers.update(csrf(client))
    body = BODIES.get((op.method, op.path), {}) if op.method in {"POST", "PUT", "PATCH"} else None
    return await client.request(op.method, path, headers=headers, params=params, json=body)


@dataclass
class World:
    anonymous: httpx.AsyncClient
    owner: httpx.AsyncClient
    viewer: httpx.AsyncClient
    outsider: httpx.AsyncClient
    workspace: uuid.UUID


@pytest.fixture
async def world(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[World]:
    """owner 通过首次安装创建；bob 是 owner 工作空间的 viewer；carol 有自己的工作空间、不是成员。"""
    async with (
        make_client() as anonymous,
        make_client() as owner,
        make_client() as viewer,
        make_client() as outsider,
    ):
        setup = (await owner.post("/api/v1/setup", json=OWNER)).json()
        workspace = uuid.UUID(setup["workspaces"][0]["id"])
        bob_id = await add_user(migrated_database_url, BOB["email"], BOB["password"])
        await add_member(migrated_database_url, workspace, bob_id, "viewer")
        carol_id = await add_user(migrated_database_url, CAROL["email"], CAROL["password"])
        await add_workspace(migrated_database_url, carol_id, "Carol 的空间")
        assert (await viewer.post("/api/v1/auth/login", json=BOB)).status_code == 200
        assert (await outsider.post("/api/v1/auth/login", json=CAROL)).status_code == 200
        yield World(anonymous, owner, viewer, outsider, workspace)


def test_matrix_covers_every_documented_operation() -> None:
    ops = operations()
    assert len(ops) >= 60  # Sprint 1 ~ 5 的全部接口
    assert {(o.method, o.path) for o in ops} >= PUBLIC


async def test_every_private_operation_requires_a_session(world: World) -> None:
    unexpected, checked = [], 0
    for op in operations():
        if (op.method, op.path) in PUBLIC:
            continue
        checked += 1
        response = await call(world.anonymous, op, workspace=world.workspace)
        if response.status_code != 401:
            unexpected.append(f"{op} → {response.status_code}")
    assert unexpected == [] and checked >= 60


async def test_every_write_requires_a_csrf_token(world: World) -> None:
    unexpected, checked = [], 0
    for op in operations():
        if not op.is_write or (op.method, op.path) in PUBLIC:
            continue
        checked += 1
        workspace = world.workspace if op.in_workspace or "{workspace_id}" in op.path else None
        response = await call(world.owner, op, workspace=workspace, with_csrf=False)
        code = response.json().get("error", {}).get("code") if response.content else None
        if response.status_code != 403 or code != "CSRF_FAILED":
            unexpected.append(f"{op} → {response.status_code} {code}")
    assert unexpected == [] and checked >= 33


async def test_non_members_get_404_for_every_workspace_operation(world: World) -> None:
    unexpected, checked = [], 0
    for op in operations():
        if not (op.in_workspace or "{workspace_id}" in op.path):
            continue
        checked += 1
        response = await call(world.outsider, op, workspace=world.workspace)
        code = response.json().get("error", {}).get("code") if response.content else None
        if response.status_code != 404 or code != "NOT_FOUND":
            unexpected.append(f"{op} → {response.status_code} {code}")
    assert unexpected == [] and checked >= 53


async def test_viewers_cannot_write_anything_in_the_workspace(world: World) -> None:
    unexpected, checked = [], 0
    for op in operations():
        if not op.is_write or (op.method, op.path) in VIEWER_WRITES:
            continue
        if not (op.in_workspace or "{workspace_id}" in op.path):
            continue  # 登出、改密码、改个人资料等只作用于自己的账号
        checked += 1
        response = await call(world.viewer, op, workspace=world.workspace)
        code = response.json().get("error", {}).get("code") if response.content else None
        if response.status_code != 403 or code != "FORBIDDEN":
            unexpected.append(f"{op} → {response.status_code} {code}")
    assert unexpected == [] and checked >= 28
