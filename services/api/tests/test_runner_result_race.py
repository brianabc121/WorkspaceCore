"""运行服务器的指令结果并发回报（agents 场景 26 在 CI 里偶发失败的根因）：真实 PostgreSQL。

一次性环境的“建环境”与“命令结束”两条结果几乎同时到达时，先提交的“命令结束”把环境标成待删除；
后提交的“建环境”如果拿着读到的旧状态（创建中），会把它改回运行中，环境就再也不会被删。
Runner.apply_result 锁住环境这一行，后到的那条要等前一条提交、读到最新状态。
"""

import asyncio
import base64
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select

from app.domain.runner import Runner
from app.models import RunnerCommand, RunnerEnvironment
from tests.test_mcp_api import call
from tests.test_runner_api import SETTINGS, add_host, agent, next_command
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        yield value


async def _command(db: Any, command_id: str) -> RunnerCommand:
    command: RunnerCommand | None = await db.scalar(
        select(RunnerCommand).where(RunnerCommand.id == uuid.UUID(command_id)).with_for_update()
    )
    assert command is not None
    return command


async def test_late_create_result_does_not_undo_pending_delete(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    tool = asyncio.create_task(
        call(
            env,
            token,
            "env_create",
            {
                "purpose": "一次性",
                "lifetime": "once",
                "spec": {"image": "python:3.12-slim"},
                "command": ["python", "-c", "print(1)"],
            },
        )
    )
    created = await next_command(env, rtoken, "create_env")
    executed = await next_command(env, rtoken, "exec")
    env_id = uuid.UUID(created["payload"]["env_id"])
    app = app_of(env.client)

    # “命令结束”先处理：环境标成待删除，事务还没提交
    async with app.state.sessionmaker() as first:
        runner = Runner(first, app.state.settings, app.state.redis)
        await runner.apply_result(
            await _command(first, executed["id"]),
            "done",
            {"exit_code": 0, "output_b64": base64.b64encode(b"1\n").decode(), "seconds": 0.1},
            None,
        )
        await first.flush()

        # “建环境”的结果同时到：要等上一条提交后才能读环境
        async def late_create() -> None:
            async with app.state.sessionmaker() as second:
                other = Runner(second, app.state.settings, app.state.redis)
                await other.apply_result(
                    await _command(second, created["id"]), "done", {"seconds": 1}, None
                )
                await second.commit()

        racing = asyncio.create_task(late_create())
        await asyncio.sleep(0.5)
        assert not racing.done()  # 被环境这一行的锁挡住
        await first.commit()
    await asyncio.wait_for(racing, timeout=10)

    async with app.state.sessionmaker() as db:
        row = await db.get(RunnerEnvironment, env_id)
        assert row is not None
        assert row.state == "pending_delete", row.state  # 没被改回 running
    result = (await tool)["structuredContent"]
    assert result["run"]["status"] == "succeeded"
