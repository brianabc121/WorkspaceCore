"""OpenClaw 智能体只读总览（T-0180）：全量统计、筛选与分页、归属、真实状态、不连外部、批量查询。"""

import uuid
from collections.abc import Iterator
from typing import Any

import asyncssh
import httpx
import pytest
from sqlalchemy import event

from app.integrations import ssh
from tests import test_agent_ssh_install as base
from tests.helpers import ClientFactory, add_member, add_user, add_workspace, csrf, run_sql
from tests.test_agent_ssh_install import PASSWORD, SETTINGS, Owner

pytestmark = pytest.mark.integration

owner = base.owner


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """添加服务器时只读主机公钥：这里不连真的服务器。"""

    async def probe(address: str, port: int) -> ssh.HostKey:
        key = asyncssh.generate_private_key("ssh-ed25519")
        return ssh.HostKey(
            algorithm="ssh-ed25519",
            public_key=" ".join(key.export_public_key().decode().split()[:2]),
            fingerprint=key.get_fingerprint("sha256"),
        )

    monkeypatch.setattr(ssh, "probe_host_key", probe)


@pytest.fixture
def no_outside_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """总览不能连 SSH、不能调 OpenIM：一旦调用就记下来。"""
    calls: list[str] = []

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append("ssh")
        raise AssertionError("总览不应连接 SSH")

    monkeypatch.setattr(asyncssh, "connect", refuse)
    from app.integrations.openim import OpenIMClient

    for name in dir(OpenIMClient):
        if not name.startswith("_") and callable(getattr(OpenIMClient, name)):

            async def blocked(*args: Any, _name: str = name, **kwargs: Any) -> Any:
                calls.append(f"openim:{_name}")
                raise AssertionError("总览不应调用 OpenIM")

            monkeypatch.setattr(OpenIMClient, name, blocked)
    yield calls


async def _call(owner: Owner, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await owner.client.request(method, path, headers=owner.headers(), **kwargs)


async def _overview(owner: Owner, **params: Any) -> dict[str, Any]:
    response = await _call(owner, "GET", "/api/v1/agents/overview", params=params)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def _all_pages(owner: Owner, **params: Any) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        page = await _overview(owner, **query)
        items.extend(page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            return items, pages


async def _instance(owner: Owner, name: str, status: str = "pending") -> str:
    response = await _call(
        owner, "POST", "/api/v1/agents/instances", json={"name": name, "network_mode": "public"}
    )
    assert response.status_code == 201, response.text
    instance_id = str(response.json()["id"])
    if status != "pending":
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_instances SET status = $1 WHERE id = $2",
            status,
            uuid.UUID(instance_id),
        )
    return instance_id


async def _ssh_server(owner: Owner, port: int) -> tuple[str, uuid.UUID]:
    response = await _call(
        owner,
        "POST",
        "/api/v1/agents/servers",
        json={
            "host": "127.0.0.1",
            "port": port,
            "username": "deploy",
            "login_password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    server_id = str(response.json()["server"]["id"])
    target = await run_sql(
        owner.database_url,
        "SELECT id FROM workspace.agent_ssh_targets WHERE server_id = $1",
        uuid.UUID(server_id),
    )
    return server_id, target


async def _deployment(
    owner: Owner,
    instance_id: str,
    server_id: str,
    *,
    installed: bool,
    runtime: bool | None,
    assign: bool = True,
) -> None:
    if assign:
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_instances SET server_id = $1, server_manual = true"
            " WHERE id = $2",
            uuid.UUID(server_id),
            uuid.UUID(instance_id),
        )
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_deployments"
        " (workspace_id, instance_id, server_id, install_dir, installed_at, runtime_installed)"
        " VALUES ($1, $2, $3, '/srv/x', CASE WHEN $4 THEN now() END, $5)",
        uuid.UUID(owner.workspace),
        uuid.UUID(instance_id),
        uuid.UUID(server_id),
        installed,
        runtime,
    )


async def _job(owner: Owner, instance_id: str, target: uuid.UUID, kind: str, status: str) -> None:
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_ssh_jobs (workspace_id, instance_id, target_id, kind, status,"
        " finished_at) VALUES ($1, $2, $3, $4, $5, CASE WHEN $5 <> 'running' THEN now() END)",
        uuid.UUID(owner.workspace),
        uuid.UUID(instance_id),
        target,
        kind,
        status,
    )


async def _fleet(owner: Owner) -> dict[str, Any]:
    """两台 SSH 服务器、一台自动识别的服务器，以及各种状态的实例。"""
    ssh_a, target_a = await _ssh_server(owner, 2201)
    ssh_b, target_b = await _ssh_server(owner, 2202)
    auto = uuid.uuid4()
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_servers (id, workspace_id, name, kind)"
        " VALUES ($1, $2, '自动识别', 'auto')",
        auto,
        uuid.UUID(owner.workspace),
    )
    ids: dict[str, str] = {}
    ids["deployed"] = await _instance(owner, "甲-已部署", "online")
    await _deployment(owner, ids["deployed"], ssh_a, installed=True, runtime=True)
    await _job(owner, ids["deployed"], target_a, "install", "succeeded")
    # 第一次部署进行中：实例还没归到这台服务器（部署记录优先）
    ids["deploying"] = await _instance(owner, "乙-部署中")
    await _deployment(owner, ids["deploying"], ssh_b, installed=False, runtime=None, assign=False)
    await _job(owner, ids["deploying"], target_b, "install", "running")
    ids["failed"] = await _instance(owner, "丙-失败", "offline")
    await _deployment(owner, ids["failed"], ssh_a, installed=False, runtime=None)
    await _job(owner, ids["failed"], target_a, "install", "failed")
    ids["interrupted"] = await _instance(owner, "丁-中断")
    await _deployment(owner, ids["interrupted"], ssh_a, installed=False, runtime=None)
    await _job(owner, ids["interrupted"], target_a, "install", "interrupted")
    ids["retained"] = await _instance(owner, "戊-保留目录", "offline")
    await _deployment(owner, ids["retained"], ssh_a, installed=False, runtime=False)
    await _job(owner, ids["retained"], target_a, "uninstall", "succeeded")
    ids["legacy"] = await _instance(owner, "己-旧记录", "online")
    await _deployment(owner, ids["legacy"], ssh_a, installed=True, runtime=None)
    ids["manual"] = await _instance(owner, "庚-手动归属", "offline")
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_instances SET server_id = $1, server_manual = true WHERE id = $2",
        auto,
        uuid.UUID(ids["manual"]),
    )
    ids["disabled"] = await _instance(owner, "辛-停用", "disabled")
    ids["unassigned"] = await _instance(owner, "壬_100%")
    return {"ids": ids, "ssh_a": ssh_a, "ssh_b": ssh_b, "auto": str(auto)}


# ---------------------------------------------------------------- 统计、筛选、分页


async def test_summary_is_the_whole_set_and_rows_tell_the_truth(
    owner: Owner, no_outside_calls: list[str]
) -> None:
    fleet = await _fleet(owner)
    ids = fleet["ids"]
    first = await _overview(owner, limit=2)
    summary = first["summary"]
    assert summary == {
        "total": 9,
        "online": 2,
        "offline": 3,
        "pending": 3,
        "disabled": 1,
        "deploying": 1,
        # 失败、中断、没停用但离线（丙既失败又离线，只算一次）
        "attention": 4,
    }
    assert len(first["items"]) == 2 and first["next_cursor"] and first["generated_at"]
    items, pages = await _all_pages(owner, limit=2)
    assert pages == 5 and len(items) == 9
    assert len({row["instance"]["id"] for row in items}) == 9
    rows = {row["instance"]["id"]: row for row in items}

    deployed = rows[ids["deployed"]]
    assert deployed["server"]["id"] == fleet["ssh_a"]
    assert deployed["deployment"]["runtime_installed"] is True
    assert deployed["deployment_project"] == (
        "myworkspace-openclaw-" + ids["deployed"].replace("-", "")[:12]
    )
    assert deployed["latest_job"]["status"] == "succeeded"
    # 部署记录优先于实例上的归属
    assert rows[ids["deploying"]]["server"]["id"] == fleet["ssh_b"]
    assert rows[ids["deploying"]]["instance"]["server_id"] is None
    assert rows[ids["deploying"]]["latest_job"]["status"] == "running"
    assert rows[ids["failed"]]["latest_job"]["status"] == "failed"
    assert rows[ids["retained"]]["deployment"]["runtime_installed"] is False
    assert rows[ids["legacy"]]["deployment"]["runtime_installed"] is None
    assert rows[ids["manual"]]["server"]["id"] == fleet["auto"]
    assert rows[ids["manual"]]["deployment"] is None
    assert rows[ids["manual"]]["deployment_project"] is None
    assert rows[ids["unassigned"]]["server"] is None
    # 离线 / 停用不推出“容器已停止”：没有容器状态字段；心跳只读 last_seen_at
    for row in items:
        assert "container_status" not in row and "container_status" not in row["instance"]
        assert "last_seen_at" in row["instance"]
    # 有运行状态未知的旧部署：服务器的成功数是 null（待核验），不当成 0
    assert deployed["server"]["successful_deployments"] is None
    assert deployed["server"]["deployment_counts_partial"] is True
    assert rows[ids["deploying"]]["server"]["successful_deployments"] == 0

    # status 只筛列表，不改 summary
    offline = await _overview(owner, status="offline", limit=50)
    assert {row["instance"]["id"] for row in offline["items"]} == {
        ids["failed"],
        ids["retained"],
        ids["manual"],
    }
    assert offline["summary"] == summary
    # server_id：部署记录优先，其次手动 / 自动归属；summary 随范围变
    on_a = await _overview(owner, server_id=fleet["ssh_a"], limit=50)
    assert {row["instance"]["id"] for row in on_a["items"]} == {
        ids["deployed"],
        ids["failed"],
        ids["interrupted"],
        ids["retained"],
        ids["legacy"],
    }
    assert on_a["summary"]["total"] == 5 and on_a["summary"]["attention"] == 3
    on_b = await _overview(owner, server_id=fleet["ssh_b"])
    assert [row["instance"]["id"] for row in on_b["items"]] == [ids["deploying"]]
    assert on_b["summary"]["deploying"] == 1
    # q：名称包含（% 与 _ 按字面匹配）
    found = await _overview(owner, q="_100%")
    assert [row["instance"]["name"] for row in found["items"]] == ["壬_100%"]
    assert found["summary"]["total"] == 1
    assert (await _overview(owner, q="100"))["summary"]["total"] == 1
    assert (await _overview(owner, q="%"))["summary"]["total"] == 1
    assert (await _overview(owner, q="部署"))["summary"]["total"] == 2
    assert no_outside_calls == []


async def test_bad_cursor_and_limits_are_rejected(owner: Owner) -> None:
    for params in ({"cursor": "not-a-cursor"}, {"limit": 0}, {"limit": 101}, {"q": ""}):
        response = await _call(owner, "GET", "/api/v1/agents/overview", params=params)
        assert response.status_code == 422, (params, response.text)
    empty = await _overview(owner)
    assert empty["items"] == [] and empty["next_cursor"] is None
    assert empty["summary"]["total"] == 0


async def test_queries_do_not_grow_with_the_page(owner: Owner, no_outside_calls: list[str]) -> None:
    await _fleet(owner)
    engine = owner.app.state.sessionmaker.kw["bind"].sync_engine
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        await _overview(owner, limit=1)
        small = len(statements)
        statements.clear()
        await _overview(owner, limit=50)
        large = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    # 一页 1 行和一页 9 行的查询次数一样（批量读取，没有逐行 N+1）
    assert large == small, (small, large)
    assert no_outside_calls == []


async def test_only_owner_and_only_this_workspace(owner: Owner, make_client: ClientFactory) -> None:
    fleet = await _fleet(owner)
    creds = {"email": "admin@example.com", "password": "admin password 123456"}
    user = await add_user(owner.database_url, creds["email"], creds["password"])
    await add_member(owner.database_url, uuid.UUID(owner.workspace), user, "admin")
    other_ws = await add_workspace(owner.database_url, user, "另一个空间")
    async with make_client(**SETTINGS) as other:
        assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
        as_admin = await other.get(
            "/api/v1/agents/overview", headers={"X-Workspace-Id": owner.workspace}
        )
        assert as_admin.status_code == 403
        # 在自己的空间里是 owner：看不到别的空间的实例与服务器
        mine = await other.get(
            "/api/v1/agents/overview",
            params={"server_id": fleet["ssh_a"]},
            headers={"X-Workspace-Id": str(other_ws), **csrf(other)},
        )
        assert mine.status_code == 200, mine.text
        assert mine.json()["items"] == [] and mine.json()["summary"]["total"] == 0
