"""运行服务器的回收（T-0139）：真实 PostgreSQL / Redis，测试代替 runner-agent。

覆盖：常驻环境归档前做快照（分块加密存到对象存储，落盘的不是明文）、归档后再用自动重建并恢复快照、
任务完成 / Issue 关闭 / 项目归档 / 创建者停用触发删除、会话环境停止到点归档、内存紧张按保留分停止、
撤销待删除、常驻环境删除前先快照并保留、保留分的计算、过期清理。
"""

import asyncio
import math
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update

from app.domain import runner_placement, runner_reclaim
from app.domain import runner_specs as specs
from app.domain.placement import PlacementError, ResourceRequest, rank_candidates
from app.models import (
    AgentWakeup,
    RunnerCommand,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerSnapshot,
)
from app.schemas.placement import Placement
from tests.helpers import tool_error
from tests.test_mcp_api import call
from tests.test_runner_api import (
    POLL,
    SETTINGS,
    add_host,
    agent,
    begin_sample,
    create_env,
    next_command,
    physical_id,
    poll,
    report_result,
    runner_headers,
)
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str, tmp_path: Path) -> AsyncIterator[Env]:
    async with open_env(
        make_client, migrated_database_url, **SETTINGS, file_dir=str(tmp_path)
    ) as value:
        yield value


async def tick(env: Env) -> runner_reclaim.TickResult:
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        return await runner_reclaim.tick(db, app.state.settings, app.state.redis)


async def set_env(env: Env, env_id: str, **values: Any) -> None:
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerEnvironment)
            .where(RunnerEnvironment.id == uuid.UUID(env_id))
            .values(**values)
        )
        await db.commit()


async def get_env(env: Env, env_id: str) -> RunnerEnvironment:
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        found: RunnerEnvironment | None = await db.get(RunnerEnvironment, uuid.UUID(env_id))
        assert found is not None
        return found


async def action(env: Env, env_id: str, name: str) -> dict[str, Any]:
    current = await get_env(env, env_id)
    response = await env.client.post(
        f"/api/v1/runner/environments/{env_id}/actions",
        json={"action": name, "version": current.version},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def stop_done(env: Env, rtoken: str) -> None:
    command = await next_command(env, rtoken, "stop_env")
    await report_result(env, rtoken, command["id"], {})


async def upload_snapshot(
    env: Env, rtoken: str, command: dict[str, Any], parts: list[bytes]
) -> None:
    snapshot_id = command["payload"]["snapshot_id"]
    for index, data in enumerate(parts):
        response = await env.client.put(
            f"/runner-api/v1/snapshots/{snapshot_id}/parts/{index}",
            content=data,
            headers={**runner_headers(rtoken), "Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 200, response.text
    await report_result(
        env,
        rtoken,
        command["id"],
        {
            "entries": [{"path": "data", "first": 0, "parts": len(parts), "bytes": 9}],
            "parts": len(parts),
            "bytes": sum(len(p) for p in parts),
        },
    )


async def test_persistent_archive_snapshots_encrypted_and_rebuilds(
    env: Env, tmp_path: Path
) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    created = await create_env(
        env,
        token,
        rtoken,
        lifetime="persistent",
        spec={"image": "python:3.12-slim", "persist": ["data"]},
    )
    env_id = created["result"]["environment"]["id"]
    setup = await call(
        env,
        token,
        "env_setup",
        {"environment_id": env_id, "steps": ["pip install -r requirements.txt"]},
    )
    assert setup["isError"] is False, setup
    archived = await action(env, env_id, "archive")
    assert archived["state"] == "running"  # 先停止、做快照，完成后才归档
    await stop_done(env, rtoken)
    snap = await next_command(env, rtoken, "snapshot")
    assert snap["payload"]["paths"] == ["data"] and snap["payload"]["then"] == "archive"
    secret_part = b"PLAINTEXT-SNAPSHOT-DATA" * 100
    await upload_snapshot(env, rtoken, snap, [secret_part, b"second"])
    # 落盘的是密文：对象存储（本地目录）里找不到明文
    stored = await asyncio.to_thread(
        lambda: [p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()]
    )
    assert stored and not any(b"PLAINTEXT-SNAPSHOT-DATA" in data for data in stored)
    # 不是这台服务器的环境的快照、已经结束上传的快照不能再写
    closed = await env.client.put(
        f"/runner-api/v1/snapshots/{snap['payload']['snapshot_id']}/parts/9",
        content=b"x",
        headers=runner_headers(rtoken),
    )
    assert closed.status_code == 409
    archive = await next_command(env, rtoken, "archive_env")
    await report_result(env, rtoken, archive["id"], {})
    stored_env = await get_env(env, env_id)
    assert stored_env.state == "archived" and stored_env.snapshot_key
    # 再用：自动按配置记录重建，带快照与初始化步骤
    task = asyncio.create_task(
        call(
            env, token, "env_exec", {"environment_id": env_id, "command": "ls", "wait_seconds": 20}
        )
    )
    rebuild = await next_command(env, rtoken, "create_env")
    payload = rebuild["payload"]
    assert payload["snapshot"]["id"] == snap["payload"]["snapshot_id"]
    assert payload["setup"] == ["pip install -r requirements.txt"]
    part = await env.client.get(
        f"/runner-api/v1/snapshots/{payload['snapshot']['id']}/parts/0",
        headers=runner_headers(rtoken),
    )
    assert part.status_code == 200 and part.content == secret_part
    assert part.headers["cache-control"] == "no-store"
    missing = await env.client.get(
        f"/runner-api/v1/snapshots/{payload['snapshot']['id']}/parts/5",
        headers=runner_headers(rtoken),
    )
    assert missing.status_code == 404

    def tamper() -> None:  # 改动密文的一个字节：解密校验失败
        target = next(p for p in tmp_path.rglob("000001") if p.is_file())
        data = bytearray(target.read_bytes())
        data[-1] ^= 1
        target.write_bytes(bytes(data))

    await asyncio.to_thread(tamper)
    corrupt = await env.client.get(
        f"/runner-api/v1/snapshots/{payload['snapshot']['id']}/parts/1",
        headers=runner_headers(rtoken),
    )
    assert corrupt.status_code == 500 and corrupt.json()["error"]["code"] == "SNAPSHOT_CORRUPT"
    await report_result(env, rtoken, rebuild["id"], {"seconds": 12.5})
    executed = await next_command(env, rtoken, "exec")
    await report_result(env, rtoken, executed["id"], {"exit_code": 0, "output_b64": ""})
    assert (await task)["structuredContent"]["status"] == "succeeded"
    rebuilt = await get_env(env, env_id)
    assert rebuilt.state == "running" and rebuilt.rebuild_seconds == 12.5


async def test_event_conditions_and_orphans_trigger_delete(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    project = await env.add_project("评估")
    task = await env.add_task(project["id"], "跑基准")
    env_task = (await create_env(env, token, rtoken, delete_when=[f"task_done:{task['id']}"]))[
        "result"
    ]["environment"]
    env_project = (
        await create_env(env, token, rtoken, delete_when=[f"project_archived:{project['id']}"])
    )["result"]["environment"]
    assert (await tick(env)).pending_delete == 0
    await env.update_task(task["id"], {"version": task["version"], "done": True})
    archived = await env.client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"archived": True, "version": project["version"]},
        headers=env.headers(write=True),
    )
    assert archived.status_code == 200, archived.text
    result = await tick(env)
    assert result.pending_delete == 2
    for item in (env_task, env_project):
        stored = await get_env(env, item["id"])
        assert stored.state == "pending_delete" and "删除条件触发" in (stored.state_reason or "")
    # 创建者实例停用：它的环境进入待删除
    _other, token2 = await agent(env)
    orphan = (await create_env(env, token2, rtoken))["result"]["environment"]
    detail = await env.client.get(f"/api/v1/agents/instances/{_other}", headers=env.headers())
    disabled = await env.client.post(
        f"/api/v1/agents/instances/{_other}/disable",
        json={"version": detail.json()["version"]},
        headers=env.headers(write=True),
    )
    assert disabled.status_code == 200, disabled.text
    await tick(env)
    assert (await get_env(env, orphan["id"])).state == "pending_delete"


async def test_idle_tiers_watermark_and_keep(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    session = (await create_env(env, token, rtoken))["result"]["environment"]
    # 空闲到点：停止
    await set_env(env, session["id"], last_used_at=specs.now() - timedelta(minutes=30))
    assert (await tick(env)).stopped == 1
    await stop_done(env, rtoken)
    assert (await get_env(env, session["id"])).state == "stopped"
    # 停止 24 小时：会话环境直接归档（没有快照）
    await set_env(env, session["id"], stopped_at=specs.now() - timedelta(hours=25))
    assert (await tick(env)).archived == 1
    archive = await next_command(env, rtoken, "archive_env")
    await report_result(env, rtoken, archive["id"], {})
    assert (await get_env(env, session["id"])).state == "archived"
    # 内存紧张：按保留分停止
    busy = (await create_env(env, token, rtoken, spec={"image": "a:1", "memory_mb": 8192}))[
        "result"
    ]["environment"]
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await db.execute(update(RunnerHost).values(mem_bytes=9 * 1024**3))
        await db.commit()
    result = await tick(env)
    assert result.stopped == 1
    stop = await next_command(env, rtoken, "stop_env")
    assert stop["payload"]["env_id"] == busy["id"]
    await report_result(env, rtoken, stop["id"], {})
    # 撤销待删除（env_update keep）
    persistent = (await create_env(env, token, rtoken, lifetime="persistent"))["result"][
        "environment"
    ]
    await action(env, persistent["id"], "delete")
    stored = await get_env(env, persistent["id"])
    assert stored.state == "pending_delete"
    async with app.state.sessionmaker() as db:
        wakeups = list(
            await db.scalars(
                select(AgentWakeup).where(AgentWakeup.session_key == f"runner:env:{stored.id}")
            )
        )
    assert wakeups and "keep=true" in wakeups[0].prompt
    kept = await call(env, token, "env_update", {"environment_id": persistent["id"], "keep": True})
    assert kept["structuredContent"]["environment"]["state"] == "stopped"
    other = await call(env, token, "env_update", {"environment_id": session["id"], "keep": True})
    assert tool_error(other)["code"] == "VALIDATION_FAILED"


async def test_persistent_delete_snapshots_first_and_keeps_it(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    persistent = (await create_env(env, token, rtoken, lifetime="persistent"))["result"][
        "environment"
    ]
    await action(env, persistent["id"], "delete")
    await stop_done(env, rtoken)
    await tick(env)  # 到期：常驻环境先做快照
    snap = await next_command(env, rtoken, "snapshot")
    assert snap["payload"]["then"] == "delete" and snap["payload"]["paths"] == ["."]
    await upload_snapshot(env, rtoken, snap, [b"workspace tar"])
    delete = await next_command(env, rtoken, "delete_env")
    await report_result(env, rtoken, delete["id"], {})
    stored = await get_env(env, persistent["id"])
    assert stored.state == "deleted"
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        snapshot = await db.scalar(
            select(RunnerSnapshot).where(RunnerSnapshot.environment_id == stored.id)
        )
        assert snapshot is not None and snapshot.status == "ready"
        assert snapshot.expires_at is not None and snapshot.expires_at > specs.now() + timedelta(
            days=29
        )
    # 管理员在保留期内恢复：按快照重建
    restored = await action(env, persistent["id"], "restore")
    assert restored["state"] == "creating"
    rebuild = await next_command(env, rtoken, "create_env")
    assert rebuild["payload"]["snapshot"]["id"] == snap["payload"]["snapshot_id"]
    # 快照过期后清理掉块
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerSnapshot).values(expires_at=specs.now() - timedelta(minutes=1))
        )
        await db.commit()
    assert (await tick(env)).purged >= 1
    async with app.state.sessionmaker() as db:
        snapshot = await db.scalar(
            select(RunnerSnapshot).where(RunnerSnapshot.environment_id == stored.id)
        )
        assert snapshot is not None and snapshot.status == "deleted"


async def test_failed_snapshot_keeps_the_environment(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    persistent = (await create_env(env, token, rtoken, lifetime="persistent"))["result"][
        "environment"
    ]
    await action(env, persistent["id"], "archive")
    await stop_done(env, rtoken)
    snap = await next_command(env, rtoken, "snapshot")
    await report_result(env, rtoken, snap["id"], {}, status="failed")
    stored = await get_env(env, persistent["id"])
    assert stored.state == "stopped" and "快照失败" in (stored.state_reason or "")
    notes = await env.client.get("/api/v1/notifications", headers=env.headers())
    assert any(n["type"] == "runner.snapshot_failed" for n in notes.json()["items"])


async def test_retention_score_and_output_purge(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    created = (await create_env(env, token, rtoken))["result"]["environment"]
    listed = await env.client.get("/api/v1/runner/environments", headers=env.headers())
    score = listed.json()["items"][0]["retention_score"]
    assert isinstance(score, float) and score > 0
    assert listed.json()["items"][0]["retention_infinite"] is False
    assert listed.json()["items"][0]["snapshot_at"] is None
    stored = await get_env(env, created["id"])
    now = specs.now()
    fresh = runner_reclaim.retention_score(stored, 10, now, snapshot_max_bytes=5 * 1024**3)
    old = runner_reclaim.retention_score(
        stored, 10, now + timedelta(days=14), snapshot_max_bytes=5 * 1024**3
    )
    assert old == pytest.approx(fresh / 4, rel=0.01)  # 每空闲 7 天减半
    stored.lifetime = "persistent"
    stored.state = "stopped"
    stored.disk_bytes = 6 * 1024**3
    assert math.isinf(
        runner_reclaim.retention_score(stored, 0, now, snapshot_max_bytes=5 * 1024**3)
    )
    # 运行输出保留 7 天
    run = await call(
        env,
        token,
        "env_exec",
        {"environment_id": created["id"], "command": "echo hi", "wait_seconds": 0},
    )
    command = await next_command(env, rtoken, "exec")
    await report_result(env, rtoken, command["id"], {"exit_code": 0, "output_b64": "aGkK"})
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await db.execute(update(RunnerRun).values(finished_at=specs.now() - timedelta(days=8)))
        await db.commit()
    await tick(env)
    async with app.state.sessionmaker() as db:
        found = await db.get(RunnerRun, uuid.UUID(run["structuredContent"]["run_id"]))
        assert found is not None and found.output == ""


async def test_disk_watermarks_prune_and_refuse(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    session = (await create_env(env, token, rtoken))["result"]["environment"]
    await call(env, token, "env_stop", {"environment_id": session["id"]})
    await stop_done(env, rtoken)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerHost).values(
                report={"disk_bytes": 100 * 1024**3, "disk_free_bytes": 3 * 1024**3}
            )
        )
        await db.commit()
    result = await tick(env)
    assert result.archived == 1  # 磁盘超过高水位：按保留分归档停止的环境
    await next_command(env, rtoken, "archive_env")
    prune = await next_command(env, rtoken, "prune_images")
    assert isinstance(prune["payload"]["keep"], list)
    notes = await env.client.get("/api/v1/notifications", headers=env.headers())
    assert notes.status_code == 200, notes.text
    assert any(n["type"] == "runner.disk_emergency" for n in notes.json()["items"])
    refused = await call(
        env,
        token,
        "env_create",
        {"purpose": "x", "lifetime": "session", "spec": {"image": "alpine:3"}},
    )
    error = tool_error(refused)
    assert error["code"] == "NO_ELIGIBLE_SERVER"


async def test_create_waits_for_confirmed_stop_before_reusing_capacity(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    first = (await create_env(env, token, rtoken))["result"]["environment"]
    app = app_of(env.client)
    first_stored = await get_env(env, first["id"])
    # 后续领指令的 poll 也必须维持同一台 4 GiB 主机的容量。
    monkeypatch.setitem(POLL, "mem_bytes", 4 * 1024**3)
    await poll(env, rtoken)
    report = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": await begin_sample(env, rtoken),
            "physical_host_id": physical_id(rtoken),
            "mem_bytes": 4 * 1024**3,
            "mem_free_bytes": 3 * 1024**3,
            "disk_bytes": 500 * 1024**3,
            "disk_free_bytes": 400 * 1024**3,
            "load": 0.1,
            "containers": [
                {
                    "env_id": first["id"],
                    "name": first_stored.container_name,
                    "state": "running",
                    "memory_working_set_bytes": 1024**3,
                }
            ],
        },
        headers=runner_headers(rtoken),
    )
    assert report.status_code == 200, report.text
    assert report.json() == {"lost": [], "stray": []}

    async def assert_capacity_refused(purpose: str) -> None:
        refused = await call(
            env,
            token,
            "env_create",
            {"purpose": purpose, "lifetime": "session", "spec": {"image": "alpine:3"}},
        )
        assert tool_error(refused)["code"] == "NO_ELIGIBLE_SERVER"
        # 拒绝必须来自真实容量不足，缺失或过期读数不能冒充这个用例。
        async with app.state.sessionmaker() as db:
            host = await db.scalar(
                select(RunnerHost).where(RunnerHost.workspace_id == uuid.UUID(env.workspace_id))
            )
            assert host is not None
            environments = list(
                await db.scalars(
                    select(RunnerEnvironment).where(RunnerEnvironment.host_id == host.id)
                )
            )
            with pytest.raises(PlacementError) as rejected:
                rank_candidates(
                    [runner_placement.candidate_for(host, environments)],
                    ResourceRequest(cpus=2, memory_mb=2048, disk_gb=20, runtime="gvisor"),
                    Placement(),
                    specs.now(),
                )
            assert rejected.value.reasons == {str(host.id): "INSUFFICIENT_CAPACITY"}

    # 再建一个 2 GiB 的会超过整台服务器的 90%；筛选不会自行释放预约。
    await assert_capacity_refused("x")
    # 容量筛选不会自行释放预约；实际停止回执到达后才重新使用。
    await call(env, token, "env_stop", {"environment_id": first["id"]})
    stop = await next_command(env, rtoken, "stop_env")
    assert stop["payload"]["env_id"] == first["id"]
    assert (await get_env(env, first["id"])).state == "running"
    await assert_capacity_refused("stop-in-flight")
    await report_result(env, rtoken, stop["id"], {})
    assert (await get_env(env, first["id"])).state == "stopped"
    # 腾出来之后再建就成了
    second = (await create_env(env, token, rtoken, spec={"image": "alpine:3"}))["result"]
    assert second["environment"]["state"] == "running"
    # 正在执行运行的不会被停：腾不出来就直接说满了
    await call(
        env,
        token,
        "env_exec",
        {"environment_id": second["environment"]["id"], "command": "sleep 60", "wait_seconds": 0},
    )
    await next_command(env, rtoken, "exec")
    await assert_capacity_refused("y")


async def test_archived_env_pending_delete_has_no_container(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    project = await env.add_project("评估")
    task = await env.add_task(project["id"], "跑基准")
    created = (
        await create_env(
            env,
            token,
            rtoken,
            lifetime="persistent",
            delete_when=[f"task_done:{task['id']}"],
        )
    )["result"]["environment"]
    # 当作已经归档：容器与数据卷都删了，只剩配置记录与快照
    snapshot_id = uuid.uuid4()
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        stored = await db.get(RunnerEnvironment, uuid.UUID(created["id"]))
        assert stored is not None
        db.add(
            RunnerSnapshot(
                id=snapshot_id,
                workspace_id=stored.workspace_id,
                environment_id=stored.id,
                status="ready",
                key_ref="sealed",
                store="local",
            )
        )
        stored.state = "archived"
        stored.archived_at = specs.now()
        stored.snapshot_key = str(snapshot_id)
        stored.snapshot_at = specs.now()
        await db.commit()
    await env.update_task(task["id"], {"version": task["version"], "done": True})
    assert (await tick(env)).pending_delete == 1
    # 撤销：回到归档（不是停止，没有容器可以启动）
    kept = await action(env, created["id"], "keep")
    assert kept["state"] == "archived"
    # 删除：不用再做快照、也不用下发 delete_env，直接删记录；快照保留 30 天
    await action(env, created["id"], "delete")
    await tick(env)
    stored_env = await get_env(env, created["id"])
    assert stored_env.state == "deleted"
    async with app.state.sessionmaker() as db:
        snapshot = await db.get(RunnerSnapshot, snapshot_id)
        assert snapshot is not None and snapshot.expires_at is not None
        assert snapshot.expires_at > specs.now() + timedelta(days=29)
        kinds = list(
            await db.scalars(
                select(RunnerCommand.kind).where(
                    RunnerCommand.environment_id == uuid.UUID(created["id"])
                )
            )
        )
    assert "snapshot" not in kinds and "delete_env" not in kinds
