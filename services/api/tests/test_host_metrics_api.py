"""服务器状态（T-0113；一台服务器多个实例 T-0146）。真实 PostgreSQL + Redis。

覆盖：上报（实例凭证、严格校验、限流）、两次之差算整机 CPU 使用率与每个实例的 CPU 核数、网速
（计数器变小记为空）；按服务器指纹归到同一台服务器（每分钟一份整机样本、网速相加）、
重启后跟着换指纹、换到另一台机器、手动指定的不改；当前值与每小时趋势、实例列表的小指示；
整机告警与单个实例的告警（只提醒一次、恢复后再提醒）、30 天清理；get_host_status 只看自己。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import host_metrics
from app.models import AgentHostState, AgentServer
from tests.helpers import run_sql
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import agent, call, ok

pytestmark = pytest.mark.integration

GB = 1 << 30
MB = 1 << 20
KEY_A = "a" * 64
KEY_B = "b" * 64
KEY_C = "c" * 64


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(make_client, migrated_database_url) as env:
        yield env


def body(
    *,
    total: int = 1_000_000,
    idle: int = 900_000,
    rx: int = 10_000,
    tx: int = 5_000,
    data_free: int = 50 * GB,
    mem_available: int = 8 * GB,
    load: tuple[float, float, float] = (0.5, 0.4, 0.3),
    key: str | None = KEY_A,
    uptime: int = 3600,
    usec: int | None = 1_000_000,
    mem: int = 512 * MB,
    limit: int | None = None,
    data_bytes: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cpu": {
            "cores": 4,
            "model": "Test CPU",
            "load": list(load),
            "total_ticks": total,
            "idle_ticks": idle,
        },
        "memory": {
            "total": 16 * GB,
            "available": mem_available,
            "swap_total": 0,
            "swap_free": 0,
            "limit": limit,
        },
        "disks": {
            "data": {"total": 100 * GB, "free": data_free},
            "root": {"total": 200 * GB, "free": 150 * GB},
        },
        "network": {"rx_bytes": rx, "tx_bytes": tx},
        "uptime_seconds": uptime,
        "container": (
            {"cpu_usec": usec, "mem_bytes": mem, "pids": 12, "data_bytes": data_bytes}
            if usec is not None
            else None
        ),
    }
    if key is not None:
        payload["host_key"] = key
    return payload


async def report(env: ImEnv, token: str, payload: dict[str, Any], expect: int = 200) -> Any:
    response = await env.client.post(
        "/agent-api/v1/host-metrics", json=payload, headers=agent_headers(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


async def shift(env: ImEnv, seconds: int) -> None:
    """把这个工作空间所有实例与服务器上一次上报的时间往前挪，模拟过了一段时间（不真的等）。"""
    for table in ("agent_host_state", "agent_servers"):
        await run_sql(
            env.database_url,
            f"UPDATE workspace.{table} SET reported_at = reported_at - make_interval(secs => $2)"  # noqa: S608
            " WHERE workspace_id = $1 AND reported_at IS NOT NULL",
            uuid.UUID(env.workspace_id),
            float(seconds),
        )
    for key in await env.app.state.redis.keys("host_reports:*"):
        await env.app.state.redis.delete(key)


async def status(env: ImEnv, instance_id: str, span: str = "24h") -> dict[str, Any]:
    response = await env.client.get(
        f"/api/v1/agents/instances/{instance_id}/host",
        params={"range": span},
        headers=env.headers(),
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def server_of(env: ImEnv, instance_id: str) -> str | None:
    value = await run_sql(
        env.database_url,
        "SELECT server_id::text FROM workspace.agent_instances WHERE id = $1",
        uuid.UUID(instance_id),
    )
    return str(value) if value is not None else None


async def count(env: ImEnv, sql: str, *args: Any) -> int:
    return int(await run_sql(env.database_url, sql, *args))


async def test_report_computes_rates_and_trends(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    first = await status(env, a["id"])
    assert first["reported"] is False and first["current"] is None and first["agent"] is None
    await report(env, token, body(data_bytes=300 * MB))
    shown = await status(env, a["id"])
    current, me = shown["current"], shown["agent"]
    assert shown["server"]["name"] == "自动识别的服务器（甲）"
    assert current["cpu_percent"] is None and current["rx_rate"] is None  # 第一次只记基准
    assert current["cores"] == 4 and current["load"] == [0.5, 0.4, 0.3]
    assert current["mem_used"] == 8 * GB and current["data_disk"]["used_percent"] == 50.0
    assert current["stale"] is False and current["cpu_model"] == "Test CPU"
    assert me["available"] is True and me["cpu_cores"] is None and me["mem_bytes"] == 512 * MB
    assert me["mem_limit"] is None and me["mem_total"] == 16 * GB and me["pids"] == 12
    assert me["data_bytes"] == 300 * MB and me["restarts_1h"] == 0
    await shift(env, 60)
    # 60 秒里整机 CPU 25%，自己容器用了 60 秒 CPU（1 个核，占 4 核的 25%）
    await report(
        env,
        token,
        body(total=1_001_000, idle=900_750, rx=70_000, tx=11_000, usec=61_000_000),
    )
    shown = await status(env, a["id"])
    current, me = shown["current"], shown["agent"]
    assert current["cpu_percent"] == 25.0
    assert 990 <= current["rx_rate"] <= 1010 and 95 <= current["tx_rate"] <= 105
    assert 0.98 <= me["cpu_cores"] <= 1.02 and 24.5 <= me["cpu_percent"] <= 25.5
    assert 990 <= me["rx_rate"] <= 1010
    assert me["data_bytes"] == 300 * MB  # 不报数据目录大小的分钟沿用上一次的值
    # 计数器变小（重启）：这一分钟记为空，记一次容器重启
    await shift(env, 60)
    await report(env, token, body(total=10, idle=5, rx=1, tx=1, usec=5))
    shown = await status(env, a["id"])
    assert shown["current"]["cpu_percent"] is None and shown["current"]["rx_rate"] is None
    assert shown["agent"]["cpu_cores"] is None and shown["agent"]["restarts_1h"] == 1
    trend = await status(env, a["id"], "7d")
    assert trend["range"] == "7d" and len(trend["points"]) >= 1
    point = trend["points"][-1]
    assert point["mem_avg"] == 50.0 and point["data_avg"] == 50.0
    assert len(trend["agent_points"]) >= 1 and trend["agent_points"][-1]["mem_max"] == 512 * MB
    summary = (await env.client.get("/api/v1/agents/host-summary", headers=env.headers())).json()[
        "items"
    ]
    mine = next(i for i in summary if i["instance_id"] == a["id"])
    assert mine["mem_used_percent"] == 50.0 and mine["disk_used_percent"] == 50.0
    assert mine["server_id"] == shown["server"]["id"] and mine["agent_mem_bytes"] == 512 * MB


async def test_report_is_strict_and_rate_limited(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    extra = body()
    extra["processes"] = ["node"]
    await report(env, token, extra, expect=422)
    negative = body()
    negative["memory"]["total"] = -1
    await report(env, token, negative, expect=422)
    bad_key = body(key="not-a-hash")
    await report(env, token, bad_key, expect=422)
    bad_box = body()
    bad_box["container"]["cmdline"] = "node"
    await report(env, token, bad_box, expect=422)
    unauthenticated = await env.client.post("/agent-api/v1/host-metrics", json=body())
    assert unauthenticated.status_code == 401
    # 读不到 cgroup 与 boot_id 的旧写法仍然能报：只有整机数据，这个智能体显示“无法获取”
    await report(env, token, body(key=None, usec=None))
    await report(env, token, body())
    await report(env, token, body(), expect=429)
    shown = await status(env, a["id"])
    assert shown["agent"]["available"] is True and shown["server"] is not None


async def test_servers_grouped_by_fingerprint(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    c, _ = await agent(env, "丙")
    ta, tb, tc = [await env.issue_token(x["id"]) for x in (a, b, c)]
    await report(env, ta, body(rx=0))
    await report(env, tb, body(rx=0))
    await report(env, tc, body(key=KEY_B, rx=0))
    server = await server_of(env, a["id"])
    assert server is not None and await server_of(env, b["id"]) == server
    assert await server_of(env, c["id"]) not in (None, server)
    servers = "SELECT count(*) FROM workspace.agent_servers WHERE workspace_id = $1"
    assert await count(env, servers, uuid.UUID(env.workspace_id)) == 2
    # 同一台服务器同一分钟只有一份整机样本
    samples = "SELECT count(*) FROM workspace.agent_server_samples WHERE server_id = $1"
    assert await count(env, samples, uuid.UUID(server)) == 1
    # 整机网速是这台服务器上各实例之和
    await shift(env, 60)
    await report(env, ta, body(rx=60_000))
    await report(env, tb, body(rx=120_000))
    current = (await status(env, a["id"]))["current"]
    assert 2_900 <= current["rx_rate"] <= 3_100

    # 机器重启：两个实例一起换了新指纹（开机才 30 秒），仍然是同一台，不拆成两台
    await shift(env, 300)
    await report(env, ta, body(key=KEY_C, uptime=30))
    await report(env, tb, body(key=KEY_C, uptime=30))
    assert await server_of(env, a["id"]) == server and await server_of(env, b["id"]) == server
    assert await count(env, servers, uuid.UUID(env.workspace_id)) == 2

    # 甲搬到丙那台机器上（那台一直在线）：归到丙的服务器；原来那台还有乙，保留
    await shift(env, 60)
    await report(env, ta, body(key=KEY_B))
    assert await server_of(env, a["id"]) == await server_of(env, c["id"])
    assert await server_of(env, b["id"]) == server

    # 乙搬到一台新机器上（开机很久了，原来那台刚刚还在报）：新建一台；原来那台空了就删掉
    await shift(env, 60)
    await report(env, tb, body(key="d" * 64, uptime=86_400))
    assert await server_of(env, b["id"]) not in (None, server)
    gone = "SELECT count(*) FROM workspace.agent_servers WHERE id = $1"
    assert await count(env, gone, uuid.UUID(server)) == 0


async def test_manual_assignment_is_kept(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    ta, tb = await env.issue_token(a["id"]), await env.issue_token(b["id"])
    await report(env, ta, body(key=KEY_A))
    await report(env, tb, body(key=KEY_B))
    server_a = await server_of(env, a["id"])
    assert server_a is not None
    # 管理员把乙指定到甲那台（PR-B 有页面；这里直接改库）
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET server_id = $2, server_manual = true WHERE id = $1",
        uuid.UUID(b["id"]),
        uuid.UUID(server_a),
    )
    await shift(env, 60)
    await report(env, tb, body(key=KEY_B, data_free=1 * GB))
    assert await server_of(env, b["id"]) == server_a  # 指纹对不上也不改归属
    async with env.app.state.sessionmaker() as db:
        row = await db.get(AgentServer, uuid.UUID(server_a))
        assert row is not None and row.machine_key == KEY_A
        assert row.latest["data_free"] == 50 * GB  # 对不上的整机数据不记到这台


async def test_ssh_server_takes_over_fingerprint_from_auto_server(env: ImEnv) -> None:
    """部署过程中定时任务先上报、按指纹自动建了一台；部署完实例归到 SSH 服务器后，
    那台 SSH 服务器（还没有指纹）接过指纹，自动建的那台并过来、删掉（T-0147，agents CI 发现）。"""
    a, _ = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    ta, tb = await env.issue_token(a["id"]), await env.issue_token(b["id"])
    await report(env, ta, body(key=KEY_A))
    await report(env, tb, body(key=KEY_A))
    auto = await server_of(env, a["id"])
    assert auto is not None and await server_of(env, b["id"]) == auto
    ssh = uuid.uuid4()
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.agent_servers (id, workspace_id, name, kind)"
        " VALUES ($1, $2, '验收服务器', 'ssh')",
        ssh,
        uuid.UUID(env.workspace_id),
    )
    # 部署完：甲归到 SSH 服务器（以部署为准）；乙还在自动识别的那台
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET server_id = $2, server_manual = true WHERE id = $1",
        uuid.UUID(a["id"]),
        ssh,
    )
    await shift(env, 60)
    await report(env, ta, body(key=KEY_A, data_free=7 * GB))
    # SSH 服务器接过指纹、记下整机数据；乙并过来（不是手动指定的）；自动识别的那台删掉
    assert await server_of(env, a["id"]) == str(ssh) and await server_of(env, b["id"]) == str(ssh)
    gone = "SELECT count(*) FROM workspace.agent_servers WHERE id = $1"
    assert await count(env, gone, uuid.UUID(auto)) == 0
    async with env.app.state.sessionmaker() as db:
        row = await db.get(AgentServer, ssh)
        assert row is not None and row.machine_key == KEY_A
        assert row.latest["data_free"] == 7 * GB
    shown = await status(env, b["id"])
    assert shown["server"]["id"] == str(ssh) and shown["current"] is not None


async def test_server_alerts_once_until_recovered(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    token = await env.issue_token(a["id"])
    other = await env.issue_token(b["id"])
    await report(env, token, body(data_free=5 * GB, mem_available=GB // 2))
    await report(env, other, body(data_free=5 * GB, mem_available=GB // 2))
    state = env.app.state
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 2  # 磁盘、内存：一台服务器只提醒一次
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0  # 恢复前不重复提醒
    shown = await status(env, a["id"])
    assert shown["alerts"] == ["disk_low", "memory_low"] and shown["agent_alerts"] == []
    assert (await status(env, b["id"]))["alerts"] == ["disk_low", "memory_low"]
    notified = "SELECT count(*) FROM workspace.notifications WHERE type = 'host.disk_low'"
    assert await count(env, notified) >= 1
    # 恢复后再出现，再提醒一次
    await shift(env, 60)
    await report(env, token, body())
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0
    assert (await status(env, a["id"]))["alerts"] == []
    await shift(env, 60)
    await report(env, token, body(data_free=5 * GB))
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1
    # 有在线实例，30 分钟没有任何实例上报
    await shift(env, 31 * 60)
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1
        server = await db.get(AgentServer, uuid.UUID(str(await server_of(env, a["id"]))))
        assert server is not None and "not_reporting" in server.alerts


async def test_load_alert_needs_fifteen_minutes(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    await report(env, token, body(load=(9.0, 9.0, 9.0)))
    state = env.app.state
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0  # 只有一分钟
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    server = uuid.UUID(str(await server_of(env, a["id"])))
    for n in range(1, 16):
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.agent_server_samples (server_id, minute, workspace_id, load15,"
            " cores) VALUES ($1, $2, $3, 9.0, 4) ON CONFLICT DO NOTHING",
            server,
            now - timedelta(minutes=n),
            uuid.UUID(env.workspace_id),
        )
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1


async def test_agent_alerts(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    await report(env, token, body(mem=980 * MB, limit=1024 * MB))
    state = env.app.state
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0  # 只有一分钟
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    for n in range(16):
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.agent_instance_samples (instance_id, minute, workspace_id,"
            " mem_bytes, mem_limit, cpu_percent) VALUES ($1, $2, $3, $4, $5, 95.0)"
            " ON CONFLICT (instance_id, minute) DO UPDATE SET cpu_percent = 95.0",
            uuid.UUID(a["id"]),
            now - timedelta(minutes=n),
            uuid.UUID(env.workspace_id),
            980 * MB,
            1024 * MB,
        )
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 2  # 内存接近上限、占满 CPU
    shown = await status(env, a["id"])
    assert shown["agent_alerts"] == ["cpu_hog", "mem_near_limit"] and shown["alerts"] == []
    title = "SELECT count(*) FROM workspace.notifications WHERE type = 'host.mem_near_limit'"
    assert await count(env, title) >= 1
    # 一小时内重启 3 次（cgroup 的 CPU 累计值变小 3 次）
    for usec in (10, 5, 1):
        await shift(env, 60)
        await report(env, token, body(usec=usec))
    async with state.sessionmaker() as db:
        row = await db.get(AgentHostState, uuid.UUID(a["id"]))
        assert row is not None and len(row.container["restarts"]) == 3
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1
    assert "restarting" in (await status(env, a["id"]))["agent_alerts"]


async def test_purge_and_agent_reads_only_itself(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    report_a = await env.issue_token(a["id"])
    report_b = await env.issue_token(b["id"])
    await report(env, report_a, body(data_free=5 * GB, key=KEY_A, mem=100 * MB))
    await report(env, report_b, body(data_free=90 * GB, key=KEY_B, mem=900 * MB))
    mine = ok(await call(env, token_a, "get_host_status", {}))
    assert mine["reported"] is True and mine["data_disk"]["used_percent"] == 95.0
    assert mine["cores"] == 4 and mine["last_24h"]["disk_max"] is not None
    assert mine["me"]["mem_bytes"] == 100 * MB and mine["me"]["available"] is True
    assert "乙" not in str(mine)
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_server_samples SET minute = minute - interval '31 days'"
        " WHERE server_id = (SELECT server_id FROM workspace.agent_instances WHERE id = $1)",
        uuid.UUID(b["id"]),
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instance_samples SET minute = minute - interval '31 days'"
        " WHERE instance_id = $1",
        uuid.UUID(b["id"]),
    )
    async with env.app.state.sessionmaker() as db:
        assert await host_metrics.purge(db) == 2
        await db.commit()


async def test_limits_go_into_bundle(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    path = f"/api/v1/agents/instances/{a['id']}/limits"
    version = (
        await env.client.get(f"/api/v1/agents/instances/{a['id']}", headers=env.headers())
    ).json()["version"]
    bad = await env.client.put(
        path,
        json={"mem_limit_mb": 100, "cpus": None, "version": version},
        headers=env.headers(write=True),
    )
    assert bad.status_code == 422
    put = await env.client.put(
        path,
        json={"mem_limit_mb": 2048, "cpus": 1.5, "version": version},
        headers=env.headers(write=True),
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"mem_limit_mb": 2048, "cpus": 1.5}
    shown = (
        await env.client.get(f"/api/v1/agents/instances/{a['id']}", headers=env.headers())
    ).json()
    assert shown["limits"] == {"mem_limit_mb": 2048, "cpus": 1.5}
    stale = await env.client.put(
        path,
        json={"mem_limit_mb": None, "cpus": None, "version": version},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    audited = (
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'agents.instance.limits_updated'"
    )
    assert await count(env, audited) == 1
