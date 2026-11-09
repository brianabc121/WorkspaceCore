"""审计查询与导出测试的公共部分（T-0197）：各角色的客户端，以及直接写入审计记录。

审计记录用 SQL 直接插入：可以指定时间、操作者与“旧数据”里的敏感键，大量记录也能一次写完。
"""

import json
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg
import httpx

from app.domain import security
from tests.helpers import OWNER, ClientFactory, add_member, asyncpg_dsn, run_sql

PASSWORD = "audit password 123456"  # noqa: S105


@dataclass
class AuditWorld:
    """owner（首次安装）、同一个工作空间的 admin / member / viewer，以及另一个工作空间的 outsider。

    owner 同时是 outsider 工作空间的 admin（用来测试游标不能跨工作空间使用）。
    """

    owner: httpx.AsyncClient
    admin: httpx.AsyncClient
    member: httpx.AsyncClient
    viewer: httpx.AsyncClient
    outsider: httpx.AsyncClient
    owner_id: uuid.UUID
    admin_id: uuid.UUID
    member_id: uuid.UUID
    viewer_id: uuid.UUID
    outsider_id: uuid.UUID
    workspace: uuid.UUID
    other_workspace: uuid.UUID
    database_url: str

    def headers(self, workspace: uuid.UUID | None = None) -> dict[str, str]:
        return {"X-Workspace-Id": str(workspace or self.workspace)}

    async def list(
        self, client: httpx.AsyncClient, workspace: uuid.UUID | None = None, **params: Any
    ) -> httpx.Response:
        return await client.get(
            "/api/v1/audit-logs", params=params, headers=self.headers(workspace)
        )

    async def detail(
        self, client: httpx.AsyncClient, entry_id: uuid.UUID, workspace: uuid.UUID | None = None
    ) -> httpx.Response:
        return await client.get(f"/api/v1/audit-logs/{entry_id}", headers=self.headers(workspace))

    async def export(
        self, client: httpx.AsyncClient, workspace: uuid.UUID | None = None, **params: Any
    ) -> httpx.Response:
        return await client.get(
            "/api/v1/audit-logs/export", params=params, headers=self.headers(workspace)
        )


async def add_named_user(database_url: str, email: str, display_name: str) -> uuid.UUID:
    user_id: uuid.UUID = await run_sql(
        database_url,
        "INSERT INTO workspace.users (email, password_hash, display_name)"
        " VALUES ($1, $2, $3) RETURNING id",
        email,
        security.hash_password(PASSWORD),
        display_name,
    )
    return user_id


@asynccontextmanager
async def open_audit_world(
    make_client: ClientFactory, database_url: str
) -> AsyncIterator[AuditWorld]:
    async with (
        make_client() as owner,
        make_client() as admin,
        make_client() as member,
        make_client() as viewer,
        make_client() as outsider,
    ):
        setup = (await owner.post("/api/v1/setup", json=OWNER)).json()
        workspace = uuid.UUID(setup["workspaces"][0]["id"])
        ids: dict[str, uuid.UUID] = {}
        for role, client in (("admin", admin), ("member", member), ("viewer", viewer)):
            email = f"{role}@example.com"
            ids[role] = await add_named_user(database_url, email, f"{role.title()} 用户")
            await add_member(database_url, workspace, ids[role], role)
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
            assert login.status_code == 200
        outsider_id = await add_named_user(database_url, "outsider@example.com", "Outsider")
        other = await run_sql(
            database_url,
            "INSERT INTO workspace.workspaces (name, owner_id)"
            " VALUES ('外部空间', $1) RETURNING id",
            outsider_id,
        )
        await add_member(database_url, other, outsider_id, "owner")
        await add_member(database_url, other, uuid.UUID(setup["user"]["id"]), "admin")
        login = await outsider.post(
            "/api/v1/auth/login", json={"email": "outsider@example.com", "password": PASSWORD}
        )
        assert login.status_code == 200
        yield AuditWorld(
            owner=owner,
            admin=admin,
            member=member,
            viewer=viewer,
            outsider=outsider,
            owner_id=uuid.UUID(setup["user"]["id"]),
            admin_id=ids["admin"],
            member_id=ids["member"],
            viewer_id=ids["viewer"],
            outsider_id=outsider_id,
            workspace=workspace,
            other_workspace=other,
            database_url=database_url,
        )


async def insert_audit(database_url: str, rows: Sequence[Mapping[str, Any]]) -> list[uuid.UUID]:
    """逐条插入审计记录（绕过 AuditTrail：可以模拟旧数据里没有脱敏的键）。"""
    conn = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        ids = []
        for row in rows:
            ids.append(
                await conn.fetchval(
                    "INSERT INTO workspace.audit_logs (workspace_id, actor_id, action,"
                    " resource_type, resource_id, metadata_redacted_json, ip_hash, created_at)"
                    " VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8) RETURNING id",
                    row.get("workspace_id"),
                    row.get("actor_id"),
                    row["action"],
                    row.get("resource_type"),
                    row.get("resource_id"),
                    json.dumps(row.get("metadata", {}), ensure_ascii=False),
                    row.get("ip_hash"),
                    row["created_at"],
                )
            )
        return ids
    finally:
        await conn.close()


async def bulk_audit(
    database_url: str,
    workspace_id: uuid.UUID | None,
    count: int,
    *,
    start: datetime,
    step: timedelta = timedelta(seconds=1),
    action: str = "bulk.row",
    actor_id: uuid.UUID | None = None,
) -> None:
    """一条 SQL 插入 count 条：created_at 从 start 起每条加 step，resource_id 是序号。"""
    await run_sql(
        database_url,
        "INSERT INTO workspace.audit_logs"
        " (workspace_id, actor_id, action, resource_type, resource_id, created_at)"
        " SELECT $1, $2, $3, 'bulk', g::text,"
        " $4::timestamptz + make_interval(secs => g * $5::float8)"
        " FROM generate_series(0, $6 - 1) AS g",
        workspace_id,
        actor_id,
        action,
        start,
        step.total_seconds(),
        count,
    )


async def count_audit(database_url: str, action: str | None = None) -> int:
    if action is None:
        total: int = await run_sql(database_url, "SELECT count(*) FROM workspace.audit_logs")
    else:
        total = await run_sql(
            database_url, "SELECT count(*) FROM workspace.audit_logs WHERE action = $1", action
        )
    return total


async def all_pages(
    world: AuditWorld, client: httpx.AsyncClient, **params: Any
) -> list[dict[str, Any]]:
    """按同样的筛选翻完所有页。"""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = dict(params) | ({"cursor": cursor} if cursor else {})
        response = await world.list(client, **query)
        assert response.status_code == 200, response.text
        page = response.json()
        items += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            return items
