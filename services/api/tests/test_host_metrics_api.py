"""服务器状态（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节）。真实 PostgreSQL + Redis。

覆盖：上报（实例凭证、严格校验、限流）、两次之差算 CPU 使用率与网速（计数器变小记为空）、
当前值与每小时趋势、实例列表的小指示、告警（只提醒一次、恢复后再提醒）、30 天清理、
get_host_status 只看自己。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import host_metrics
from app.models import AgentHostState
from tests.helpers import run_sql
from tests.test_agents_api import agent_headers
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import agent, call, ok

pytestmark = pytest.mark.integration

GB = 1 << 30


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
) -> dict[str, Any]:
    return {
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
            "limit": None,
        },
        "disks": {
            "data": {"total": 100 * GB, "free": data_free},
            "root": {"total": 200 * GB, "free": 150 * GB},
        },
        "network": {"rx_bytes": rx, "tx_bytes": tx},
        "uptime_seconds": 3600,
    }


async def report(env: ImEnv, token: str, payload: dict[str, Any], expect: int = 200) -> Any:
    response = await env.client.post(
        "/agent-api/v1/host-metrics", json=payload, headers=agent_headers(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


async def shift_state(env: ImEnv, instance_id: str, seconds: int) -> None:
    """把上一次上报的时间往前挪，模拟过了一段时间（限流按分钟，测试里不真的等）。"""
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_host_state"
        " SET reported_at = reported_at - make_interval(secs => $2) WHERE instance_id = $1",
        uuid.UUID(instance_id),
        float(seconds),
    )
    await env.app.state.redis.delete(f"host_reports:{instance_id}")


async def status(env: ImEnv, instance_id: str, span: str = "24h") -> dict[str, Any]:
    response = await env.client.get(
        f"/api/v1/agents/instances/{instance_id}/host",
        params={"range": span},
        headers=env.headers(),
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_report_computes_rates_and_trends(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    first = await status(env, a["id"])
    assert first["reported"] is False and first["current"] is None
    await report(env, token, body())
    current = (await status(env, a["id"]))["current"]
    assert current["cpu_percent"] is None and current["rx_rate"] is None  # 第一次只记基准
    assert current["cores"] == 4 and current["load"] == [0.5, 0.4, 0.3]
    assert current["mem_used"] == 8 * GB and current["data_disk"]["used_percent"] == 50.0
    assert current["stale"] is False and current["cpu_model"] == "Test CPU"
    await shift_state(env, a["id"], 60)
    await report(env, token, body(total=1_001_000, idle=900_750, rx=70_000, tx=11_000))
    current = (await status(env, a["id"]))["current"]
    assert current["cpu_percent"] == 25.0
    assert 990 <= current["rx_rate"] <= 1010 and 95 <= current["tx_rate"] <= 105
    # 计数器变小（重启）：这一分钟记为空
    await shift_state(env, a["id"], 60)
    await report(env, token, body(total=10, idle=5, rx=1, tx=1))
    current = (await status(env, a["id"]))["current"]
    assert current["cpu_percent"] is None and current["rx_rate"] is None
    trend = await status(env, a["id"], "7d")
    assert trend["range"] == "7d" and len(trend["points"]) >= 1
    point = trend["points"][-1]
    assert point["mem_avg"] == 50.0 and point["data_avg"] == 50.0
    summary = (await env.client.get("/api/v1/agents/host-summary", headers=env.headers())).json()[
        "items"
    ]
    mine = next(i for i in summary if i["instance_id"] == a["id"])
    assert mine["mem_used_percent"] == 50.0 and mine["disk_used_percent"] == 50.0


async def test_report_is_strict_and_rate_limited(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    extra = body()
    extra["processes"] = ["node"]
    await report(env, token, extra, expect=422)
    negative = body()
    negative["memory"]["total"] = -1
    await report(env, token, negative, expect=422)
    unauthenticated = await env.client.post("/agent-api/v1/host-metrics", json=body())
    assert unauthenticated.status_code == 401
    await report(env, token, body())
    await report(env, token, body())
    await report(env, token, body(), expect=429)


async def test_alerts_once_until_recovered(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    await report(env, token, body(data_free=5 * GB, mem_available=GB // 2))
    state = env.app.state
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 2  # 磁盘、内存
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0  # 恢复前不重复提醒
    shown = await status(env, a["id"])
    assert shown["alerts"] == ["disk_low", "memory_low"]
    count = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.notifications WHERE type = 'host.disk_low'",
    )
    assert count >= 1
    # 恢复后再出现，再提醒一次
    await shift_state(env, a["id"], 60)
    await report(env, token, body())
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0
    assert (await status(env, a["id"]))["alerts"] == []
    await shift_state(env, a["id"], 60)
    await report(env, token, body(data_free=5 * GB))
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1
    # 在线却 30 分钟没有上报
    await shift_state(env, a["id"], 31 * 60)
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1
        row = await db.get(AgentHostState, uuid.UUID(a["id"]))
        assert row is not None and "not_reporting" in row.alerts


async def test_load_alert_needs_fifteen_minutes(env: ImEnv) -> None:
    a, _ = await agent(env, "甲")
    token = await env.issue_token(a["id"])
    await report(env, token, body(load=(9.0, 9.0, 9.0)))
    state = env.app.state
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 0  # 只有一分钟
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    for n in range(1, 16):
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.agent_host_samples (instance_id, minute, workspace_id, load15,"
            " cores) VALUES ($1, $2, $3, 9.0, 4) ON CONFLICT DO NOTHING",
            uuid.UUID(a["id"]),
            now - timedelta(minutes=n),
            uuid.UUID(env.workspace_id),
        )
    async with state.sessionmaker() as db:
        assert await host_metrics.check_alerts(db) == 1


async def test_purge_and_agent_reads_only_itself(env: ImEnv) -> None:
    a, token_a = await agent(env, "甲")
    b, _ = await agent(env, "乙")
    report_a = await env.issue_token(a["id"])
    report_b = await env.issue_token(b["id"])
    await report(env, report_a, body(data_free=5 * GB))
    await report(env, report_b, body(data_free=90 * GB))
    mine = ok(await call(env, token_a, "get_host_status", {}))
    assert mine["reported"] is True and mine["data_disk"]["used_percent"] == 95.0
    assert mine["cores"] == 4 and mine["last_24h"]["disk_max"] is not None
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_host_samples SET minute = minute - interval '31 days'"
        " WHERE instance_id = $1",
        uuid.UUID(b["id"]),
    )
    async with env.app.state.sessionmaker() as db:
        assert await host_metrics.purge(db) == 1
        await db.commit()
