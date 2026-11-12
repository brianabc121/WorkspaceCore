"""容器服务器的读数接口（T-0185）：真实 PostgreSQL / Redis。

- runner-agent 上报的 CPU / 内存 / 数据卷读数与服务端接收时间；普通 poll 不刷新；旧 agent 兼容；
- 单环境详情：owner / admin 可读，member / viewer 403，跨工作空间与不存在 404；
  原智能体删除后仍可见；
- 列表：服务端搜索、排序、分页与匹配总数（501 条，越过原来的 500 条上限）、游标绑定范围；
- GET 不发任何指令（不连 Docker / SSH）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.domain import runner_specs as specs
from app.models import AgentInstance, RunnerCommand, RunnerEnvironment, RunnerHost
from tests.helpers import add_member, add_user, add_workspace
from tests.test_runner_api import (
    SETTINGS,
    add_host,
    agent,
    begin_sample,
    create_env,
    physical_id,
    poll,
    runner_headers,
)
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration

MIB = 1024 * 1024


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        yield value


async def detail(env: Env, env_id: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/runner/environments/{env_id}", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def report(env: Env, rtoken: str, containers: list[dict[str, Any]], **body: Any) -> None:
    response = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": await begin_sample(env, rtoken),
            "physical_host_id": physical_id(rtoken),
            "containers": containers,
            "volumes": [],
            "images": [],
            **body,
        },
        headers=runner_headers(rtoken),
    )
    assert response.status_code == 200, response.text


async def set_env(env: Env, env_id: str, **values: Any) -> None:
    async with app_of(env.client).state.sessionmaker() as db:
        await db.execute(
            update(RunnerEnvironment)
            .where(RunnerEnvironment.id == uuid.UUID(env_id))
            .values(**values)
        )
        await db.commit()


async def command_count(env: Env) -> int:
    async with app_of(env.client).state.sessionmaker() as db:
        return int(await db.scalar(select(func.count()).select_from(RunnerCommand)) or 0)


async def test_report_stores_readings_with_server_time(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    created = (await create_env(env, token, rtoken))["result"]["environment"]
    env_id = created["id"]
    before = await detail(env, env_id)
    assert before["metrics"]["cpu_status"] == "unavailable"  # 还没有读数：未知，不是 0
    assert before["metrics"]["memory_status"] == "unavailable"

    name = "mw-env-x"
    await report(
        env,
        rtoken,
        [
            {
                "env_id": env_id,
                "name": name,
                "state": "running",
                "mem_bytes": 640 * MIB,
                "cpu_used_cores": 0.5,
                "cpu_window_seconds": 60,
            }
        ],
        volumes=[{"env_id": env_id, "name": "v", "bytes": 1200 * MIB}],
    )
    after = await detail(env, env_id)
    m = after["metrics"]
    assert (m["cpu_status"], m["memory_status"], m["data_volume_status"]) == ("ok", "ok", "ok")
    assert m["cpu_used_cores"] == 0.5 and m["cpu_window_seconds"] == 60
    assert m["cpu_percent_of_limit"] == pytest.approx(0.5 / after["cpus"] * 100)
    assert m["memory_used_bytes"] == 640 * MIB
    assert m["memory_percent_of_limit"] == pytest.approx(640 / after["memory_mb"] * 100)
    assert m["data_volume_bytes"] == 1200 * MIB
    assert m["cpu_reported_at"] and m["memory_reported_at"] and m["data_volume_reported_at"]
    # 观测不是使用：不改最近使用时间与版本
    assert after["version"] == before["version"]
    assert after["last_used_at"] == before["last_used_at"]
    assert after["mem_bytes"] == 640 * MIB and after["disk_bytes"] == 1200 * MIB  # 旧字段照常

    # 普通 poll 只说明主机在线，不刷新容器读数的时间
    await poll(env, rtoken)
    assert (await detail(env, env_id))["metrics"]["cpu_reported_at"] == m["cpu_reported_at"]

    # 旧版 runner-agent：不带 CPU；这一轮内存也没读到；数据卷 -1 → CPU、内存未知，数据卷保留上次
    await report(
        env,
        rtoken,
        [{"env_id": env_id, "name": name, "state": "running"}],
        volumes=[{"env_id": env_id, "name": "v", "bytes": -1}],
    )
    m = (await detail(env, env_id))["metrics"]
    assert m["cpu_status"] == "unavailable" and m["cpu_used_cores"] is None
    assert m["memory_status"] == "unavailable" and m["memory_used_bytes"] is None
    assert m["data_volume_bytes"] == 1200 * MIB
    assert m["data_volume_reported_at"] == after["metrics"]["data_volume_reported_at"]

    # 超过 180 秒没有新读数：过期，数字与时间保留
    old = specs.now() - timedelta(seconds=181)
    await set_env(
        env,
        env_id,
        cpu_used_cores=0.25,
        cpu_window_seconds=60.0,
        cpu_reported_at=old,
        mem_bytes=100,
        mem_reported_at=old,
    )
    m = (await detail(env, env_id))["metrics"]
    assert (m["cpu_status"], m["memory_status"]) == ("stale", "stale")
    assert m["cpu_used_cores"] == 0.25 and m["memory_used_bytes"] == 100

    # 容器退出：环境停止，CPU / 内存为“未运行”，数据卷还在
    await report(
        env,
        rtoken,
        [{"env_id": env_id, "name": name, "state": "exited"}],
        volumes=[{"env_id": env_id, "name": "v", "bytes": 1300 * MIB}],
    )
    stopped = await detail(env, env_id)
    assert stopped["state"] == "stopped"
    m = stopped["metrics"]
    assert (m["cpu_status"], m["memory_status"]) == ("not_running", "not_running")
    assert m["cpu_used_cores"] is None and m["memory_used_bytes"] is None
    assert m["data_volume_status"] == "ok" and m["data_volume_bytes"] == 1300 * MIB


async def test_host_offline_makes_readings_stale(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    env_id = (await create_env(env, token, rtoken))["result"]["environment"]["id"]
    await report(
        env,
        rtoken,
        [
            {
                "env_id": env_id,
                "name": "c",
                "state": "running",
                "mem_bytes": 1,
                "cpu_used_cores": 0.1,
                "cpu_window_seconds": 60,
            }
        ],
    )
    async with app_of(env.client).state.sessionmaker() as db:
        await db.execute(
            update(RunnerHost).values(last_seen_at=specs.now() - timedelta(seconds=120))
        )
        await db.commit()
    m = (await detail(env, env_id))["metrics"]
    # 读数本身才 1 分钟内，但主机已经 2 分钟没联系：不当实时显示
    assert (m["cpu_status"], m["memory_status"]) == ("stale", "stale")


async def test_report_cannot_write_another_workspace(env: Env) -> None:
    rtoken = await add_host(env)
    other_ws = await add_workspace(env.database_url, env.owner_id, "另一个空间")
    async with app_of(env.client).state.sessionmaker() as db:
        host = RunnerHost(
            workspace_id=other_ws,
            name="b",
            token_hash="x" * 64,
            token_prefix="mwr_x",  # noqa: S106
            status="online",
            last_seen_at=specs.now(),
        )
        db.add(host)
        await db.flush()
        foreign = _env_row(other_ws, host.id, purpose="别人的环境", state="running")
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)
    await report(
        env,
        rtoken,
        [
            {
                "env_id": foreign_id,
                "name": "c",
                "state": "running",
                "mem_bytes": 1,
                "cpu_used_cores": 1.0,
                "cpu_window_seconds": 60,
            }
        ],
    )
    async with app_of(env.client).state.sessionmaker() as db:
        row = await db.get(RunnerEnvironment, uuid.UUID(foreign_id))
        assert row is not None
        assert row.cpu_used_cores is None and row.mem_bytes is None and row.cpu_reported_at is None
    # 也读不到别的工作空间的详情
    response = await env.client.get(
        f"/api/v1/runner/environments/{foreign_id}", headers=env.headers()
    )
    assert response.status_code == 404
    missing = await env.client.get(
        f"/api/v1/runner/environments/{uuid.uuid4()}", headers=env.headers()
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_detail_needs_owner_or_admin(env: Env, role: str) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    env_id = (await create_env(env, token, rtoken))["result"]["environment"]["id"]
    creds = {"email": f"{role}@example.com", "password": f"{role} password 123456"}
    user = await add_user(env.database_url, creds["email"], creds["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user, role)
    async with env.make_client(**SETTINGS) as other:
        assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
        headers = {"X-Workspace-Id": env.workspace_id}
        for path in (f"/api/v1/runner/environments/{env_id}", "/api/v1/runner/environments"):
            response = await other.get(path, headers=headers)
            assert response.status_code == 403, (path, response.text)


async def test_deleted_owner_keeps_the_environment_visible(env: Env) -> None:
    rtoken = await add_host(env)
    instance_id, token = await agent(env)
    env_id = (await create_env(env, token, rtoken))["result"]["environment"]["id"]
    await agent(env)  # 另一个还在的实例，不能把环境挂到它名下
    async with app_of(env.client).state.sessionmaker() as db:
        instance = await db.get(AgentInstance, uuid.UUID(instance_id))
        assert instance is not None
        await db.delete(instance)
        await db.commit()
    item = await detail(env, env_id)
    assert item["owner_instance_id"] is None
    assert item["owner"] == "已删除的实例"


def _env_row(workspace_id: uuid.UUID, host_id: uuid.UUID, **fields: Any) -> RunnerEnvironment:
    short = uuid.uuid4().hex[:12]
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": workspace_id,
        "host_id": host_id,
        "purpose": "批量环境",
        "lifetime": "session",
        "state": "stopped",
        "spec": {"image": "python:3.12-slim", "cpus": 2, "memory_mb": 2048, "disk_gb": 5},
        "container_name": f"mw-env-{short}",
        "volume_name": f"mw-vol-{short}",
        "network_name": f"mw-net-{short}",
    }
    values.update(fields)
    return RunnerEnvironment(**values)


async def _seed(env: Env, rows: list[dict[str, Any]]) -> list[str]:
    async with app_of(env.client).state.sessionmaker() as db:
        host = await db.scalar(
            select(RunnerHost).where(RunnerHost.workspace_id == uuid.UUID(env.workspace_id))
        )
        assert host is not None
        objects = [_env_row(uuid.UUID(env.workspace_id), host.id, **row) for row in rows]
        db.add_all(objects)
        await db.commit()
        return [str(o.id) for o in objects]


async def _list(env: Env, **params: Any) -> dict[str, Any]:
    response = await env.client.get(
        "/api/v1/runner/environments", params=params, headers=env.headers()
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_list_pages_search_and_total_beyond_500(env: Env) -> None:
    await add_host(env)
    base = specs.now() - timedelta(days=1)
    rows: list[dict[str, Any]] = [
        # 一半记录的最近使用时间相同：按 UUID 破同分，翻页不重不漏
        {"purpose": f"批量 {i:03d}", "last_used_at": base + timedelta(seconds=i // 2)}
        for i in range(500)
    ]
    rows.append({"purpose": "最后一条 100%_done", "last_used_at": base - timedelta(days=30)})
    ids = await _seed(env, rows)

    old = await _list(env)  # 旧页面：不传 limit，仍然最多 500 条
    assert len(old["items"]) == 500 and old["total"] == 501 and old["next_cursor"]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        page = await _list(env, **params)
        assert page["total"] == 501
        seen.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 11
    assert len(seen) == len(set(seen)) == 501
    assert set(seen) == set(ids)
    assert seen[-1] == ids[-1]  # 最久没用的排在最后

    # 搜索在服务端、分页之前：最后一条也能搜到；% 与 _ 按字面匹配
    found = await _list(env, q="100%_done", limit=50)
    assert found["total"] == 1 and found["items"][0]["id"] == ids[-1]
    assert (await _list(env, q="%", limit=50))["total"] == 1
    by_id = await _list(env, q=ids[123][:13], limit=50)
    assert ids[123] in {item["id"] for item in by_id["items"]}
    assert (await _list(env, q="批量 0", limit=50))["total"] == 100

    # 游标绑定筛选、排序、每页条数与工作空间
    first = await _list(env, limit=50)
    bad_params: list[dict[str, Any]] = [
        {"limit": 20, "cursor": first["next_cursor"]},
        {"limit": 50, "sort": "cpu", "cursor": first["next_cursor"]},
        {"limit": 50, "q": "批量", "cursor": first["next_cursor"]},
        {"limit": 50, "cursor": "not-a-cursor"},
    ]
    for params in bad_params:
        bad = await env.client.get(
            "/api/v1/runner/environments", params=params, headers=env.headers()
        )
        assert bad.status_code == 422, params
    other_ws = await add_workspace(env.database_url, env.owner_id, "另一个空间")
    elsewhere = await env.client.get(
        "/api/v1/runner/environments",
        params={"limit": 50, "cursor": first["next_cursor"]},
        headers={"X-Workspace-Id": str(other_ws)},
    )
    assert elsewhere.status_code == 422
    for params in ({"limit": 0}, {"limit": 501}, {"q": "x" * 101}, {"sort": "disk"}):
        response = await env.client.get(
            "/api/v1/runner/environments", params=params, headers=env.headers()
        )
        assert response.status_code == 422, params


async def test_sort_by_live_cpu_and_memory(env: Env) -> None:
    rtoken = await add_host(env)
    await poll(env, rtoken)
    now = specs.now()
    stale = now - timedelta(seconds=600)
    rows: list[dict[str, Any]] = [
        {
            "purpose": "闲",
            "state": "running",
            "cpu_used_cores": 0.0,
            "cpu_window_seconds": 60.0,
            "cpu_reported_at": now,
            "mem_bytes": 10,
            "mem_reported_at": now,
        },
        {
            "purpose": "忙",
            "state": "running",
            "cpu_used_cores": 1.5,
            "cpu_window_seconds": 60.0,
            "cpu_reported_at": now,
            "mem_bytes": 5,
            "mem_reported_at": now,
        },
        {
            "purpose": "过期但数字大",
            "state": "running",
            "cpu_used_cores": 9.0,
            "cpu_window_seconds": 60.0,
            "cpu_reported_at": stale,
            "mem_bytes": 999,
            "mem_reported_at": stale,
        },
        {
            "purpose": "已停止",
            "state": "stopped",
            "cpu_used_cores": 5.0,
            "cpu_window_seconds": 60.0,
            "cpu_reported_at": now,
            "mem_bytes": 500,
            "mem_reported_at": now,
        },
        {"purpose": "未知", "state": "running"},
    ]
    ids = await _seed(env, rows)
    by_cpu = [item["id"] for item in (await _list(env, sort="cpu", limit=50))["items"]]
    assert by_cpu[:2] == [ids[1], ids[0]]  # 实时的按读数从高到低，真实的 0 也算
    assert by_cpu[2:] == sorted(ids[2:])  # 过期、未运行、未知排在最后，按 UUID
    by_mem = [item["id"] for item in (await _list(env, sort="memory", limit=50))["items"]]
    assert by_mem[:2] == [ids[0], ids[1]]
    assert by_mem[2:] == sorted(ids[2:])


async def test_reads_do_not_send_commands(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    env_id = (await create_env(env, token, rtoken))["result"]["environment"]["id"]
    count = await command_count(env)
    await detail(env, env_id)
    await _list(env, limit=50, sort="cpu")
    await _list(env)
    await env.client.get("/api/v1/runner/overview", headers=env.headers())
    assert await command_count(env) == count
