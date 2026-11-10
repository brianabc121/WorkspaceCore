"""运行服务器指令的下发顺序（T-0213；agents 场景 26 在 main 上偶发失败的根因）：真实 PostgreSQL。

用到归档的环境时，工作台在同一个事务里先写“重建”（create_env）、再写“执行”（exec）。
created_at 的默认值是 now()（事务开始的时间），两条完全相同；下发只按 created_at 排序时，
先后由行在表里的位置决定，exec 可能先发到 runner-agent，在容器重建之前执行，报“容器不存在”。
指令带上写入顺序 seq，下发按 created_at、seq 排序。
"""

import asyncio
import base64
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app.domain import runner_specs as specs
from app.domain.runner import Runner
from app.domain.runner_hosts import RunnerHosts
from app.models import RunnerCommand, RunnerEnvironment, RunnerHost
from tests.test_mcp_api import call
from tests.test_runner_api import (
    SETTINGS,
    add_host,
    agent,
    create_env,
    poll,
    report_result,
)
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        yield value


async def _archived_env(env: Env) -> tuple[str, str, uuid.UUID]:
    """建一个常驻环境，再当作已经归档（容器与数据卷都删了）。"""
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    created = await create_env(env, token, rtoken, lifetime="persistent")
    env_id = uuid.UUID(created["result"]["environment"]["id"])
    async with app_of(env.client).state.sessionmaker() as db:
        stored = await db.get(RunnerEnvironment, env_id)
        assert stored is not None
        stored.state = "archived"
        stored.archived_at = specs.now()
        await db.commit()
    return rtoken, token, env_id


async def test_same_transaction_commands_are_sent_in_write_order(env: Env) -> None:
    """created_at 相同、行在表里的位置与写入顺序相反时，仍然先下发重建、再下发执行。"""
    _rtoken, _token, env_id = await _archived_env(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        runner = Runner(db, app.state.settings, app.state.redis)
        stored = await db.get(RunnerEnvironment, env_id)
        assert stored is not None
        host_id = stored.host_id
        await runner.start_run(None, stored, ["true"], 60)  # 归档：先写 create_env，再写 exec
        await db.flush()
        rebuild = await db.scalar(
            select(RunnerCommand).where(
                RunnerCommand.environment_id == env_id,
                RunnerCommand.kind == "create_env",
                RunnerCommand.status == "pending",
            )
        )
        assert rebuild is not None
        # 同一个事务里再改一下 create_env：它的新版本落在 exec 之后（表里的物理位置反过来了）
        rebuild.deadline_at = specs.now() + timedelta(hours=2)
        await db.commit()

    async with app.state.sessionmaker() as db:
        stamps = list(
            await db.scalars(
                select(RunnerCommand.created_at).where(
                    RunnerCommand.environment_id == env_id, RunnerCommand.status == "pending"
                )
            )
        )
        assert len(stamps) == 2 and stamps[0] == stamps[1]  # 同一个事务：created_at 一样
        # 按表的顺序读（小表上常见的计划）：只按 created_at 排序时，先拿到的是 exec
        await db.execute(text("SET LOCAL enable_indexscan = off"))
        await db.execute(text("SET LOCAL enable_bitmapscan = off"))
        host = await db.get(RunnerHost, host_id)
        assert host is not None
        taken = await RunnerHosts(db, app.state.settings, app.state.redis).take(
            host, client_ip="127.0.0.1"
        )
        await db.commit()
    kinds = [c.kind for c in taken if c.payload.get("env_id") == str(env_id)]
    assert kinds == ["create_env", "exec"], kinds


async def test_exec_on_archived_env_rebuilds_first(env: Env) -> None:
    """经工具 env_exec：一次领到的指令里重建在前，回报后命令照常跑完。"""
    rtoken, token, env_id = await _archived_env(env)
    tool = asyncio.create_task(
        call(env, token, "env_exec", {"environment_id": str(env_id), "command": ["true"]})
    )
    received: list[dict[str, Any]] = []
    for _ in range(100):
        batch = await poll(env, rtoken)
        received += [c for c in batch if c["payload"].get("env_id") == str(env_id)]
        if len(received) >= 2:
            break
        await asyncio.sleep(0.05)
    assert [c["kind"] for c in received] == ["create_env", "exec"], received
    assert await report_result(env, rtoken, received[0]["id"], {"seconds": 1.0})
    assert await report_result(
        env,
        rtoken,
        received[1]["id"],
        {"exit_code": 0, "output_b64": base64.b64encode(b"ok\n").decode(), "seconds": 0.1},
    )
    result = (await tool)["structuredContent"]
    assert result["status"] == "succeeded", result
    async with app_of(env.client).state.sessionmaker() as db:
        stored = await db.get(RunnerEnvironment, env_id)
        assert stored is not None and stored.state == "running"
