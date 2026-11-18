"""模块建的容器服务器环境与托管进程（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 21.3、21.4）。

真实 PostgreSQL / Redis；测试代替 runner-agent 走 /runner-api/v1/*，模块的去处换成记录用的假对象。
覆盖：建模块归属的常驻环境；托管进程的标准输入和凭据不落库、领指令时才解开且只能领一次；
起来以后才进对账名单；输出按偏移确认（重复、超前的块不收）；结束只认第一次；没起来时通知模块；
排队时叫停直接取消；回收不把它当孤儿、不自动归档、按自己的空闲时长停止；容器服务器页面上不能删。
"""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app.domain import runner_reclaim
from app.domain import runner_specs as specs
from app.domain.runner import Runner, RunnerError, Viewer
from app.domain.runner_managed import (
    PROC_EXPIRE_GRACE,
    PROC_START_DEADLINE,
    ManagedRunner,
    agent_outdated,
    proc_input_key,
)
from app.models import (
    AgentInstance,
    RunnerCommand,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerTemplate,
)
from app.modules import registry
from app.modules.llm_bridge import remote_config as cfg
from app.runner_agent import VERSION
from tests.test_mcp_api import new_instance
from tests.test_runner_api import (
    SETTINGS,
    add_host,
    begin_sample,
    next_command,
    physical_id,
    report_result,
    runner_headers,
)
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        yield value


SECRET = "sk-ant-api03-" + "Z" * 40
PROMPT = "请把测试跑一遍"


class FakeSink:
    def __init__(self) -> None:
        self.chunks: list[tuple[int, bytes]] = []
        self.exits: list[dict[str, Any]] = []
        self.accept = True

    async def output(
        self, db: Any, settings: Any, run: RunnerRun, offset: int, data: bytes, runtime: Any = None
    ) -> int:
        if not self.accept:
            return offset
        self.chunks.append((offset, data))
        return offset + len(data)

    async def exit(
        self, db: Any, settings: Any, run: RunnerRun, info: dict[str, Any], runtime: Any = None
    ) -> None:
        self.exits.append({"run_id": str(run.id), **info})


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> FakeSink:
    fake = FakeSink()
    original = registry.runner_proc_sink
    monkeypatch.setattr(
        registry,
        "runner_proc_sink",
        lambda kind: fake if kind == "llm_bridge" else original(kind),
    )
    return fake


async def _setup(env: Env) -> tuple[str, uuid.UUID, uuid.UUID]:
    """容器服务器上线、一个构建好的模板、一个模块建的环境（回报建好）。"""
    rtoken = await add_host(env)
    app = app_of(env.client)
    workspace_id = uuid.UUID(env.workspace_id)
    async with app.state.sessionmaker() as db:
        host = await db.scalar(select(RunnerHost).where(RunnerHost.workspace_id == workspace_id))
        assert host is not None
        template = RunnerTemplate(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name="mw-llm-dev-test",
            description="测试",
            created_by=env.owner_id,
            spec=specs.normalize({"image": "node:22-bookworm-slim", "lifetime": "persistent"}),
            build_status="ready",
            build_host_id=host.id,
            image_ref="myworkspace/tpl-test:1",
            image_digest="sha256:" + "b" * 64,
        )
        db.add(template)
        await db.flush()
        managed = ManagedRunner(db, app.state.settings, app.state.redis)
        created = await managed.create(
            workspace_id,
            owner_kind="llm_bridge",
            owner_ref="container-1",
            owner_label="MCP 管理器「主桥」· 远程容器「开发机」",
            host=host,
            template=template,
            overrides={"cpus": 2, "memory_mb": 4096, "disk_gb": 20, "network": "all"},
            purpose="大模型开发环境：开发机",
            idle_stop_seconds=1800,
            actor_id=env.owner_id,
        )
        await db.commit()
        env_id = created.id
        host_id = host.id
    command = await next_command(env, rtoken, "create_env")
    assert command["payload"]["user"] == "user"  # 远程开发容器固定非 root
    assert await report_result(env, rtoken, command["id"], {"seconds": 3.0})
    return rtoken, env_id, host_id


async def _start(env: Env, env_id: uuid.UUID, **kwargs: Any) -> uuid.UUID:
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, env_id)
        assert target is not None
        run = await ManagedRunner(db, app.state.settings, app.state.redis).start_proc(
            target,
            owner_kind="llm_bridge",
            owner_ref="container-1",
            label="turn · claude · bs_test0001",
            argv=["/opt/mw/cli/bin/claude", "-p", "--output-format", "stream-json"],
            cwd="/workspace/sessions/bs_test0001",
            env_vars={"CLAUDE_CONFIG_DIR": "/workspace/.home/.claude", "HOME": "/elsewhere"},
            secret_env={"ANTHROPIC_API_KEY": SECRET},
            stdin=PROMPT.encode(),
            timeout_seconds=600,
            **kwargs,
        )
        await db.commit()
        return run.id


async def _report(env: Env, rtoken: str) -> dict[str, Any]:
    sample_id = await begin_sample(env, rtoken)
    response = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": sample_id,
            "physical_host_id": physical_id(rtoken),
            "mem_bytes": 32 * 1024**3,
            "mem_free_bytes": 24 * 1024**3,
            "disk_bytes": 500 * 1024**3,
            "disk_free_bytes": 400 * 1024**3,
            "load": 0.5,
        },
        headers=runner_headers(rtoken),
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def _output(env: Env, rtoken: str, run_id: uuid.UUID, offset: int, data: bytes) -> int:
    response = await env.client.post(
        f"/runner-api/v1/procs/{run_id}/output",
        json={"offset": offset, "data_b64": base64.b64encode(data).decode()},
        headers=runner_headers(rtoken),
    )
    assert response.status_code == 200, response.text
    acked: int = response.json()["acked"]
    return acked


async def test_managed_process_round_trip(env: Env, sink: FakeSink) -> None:
    rtoken, env_id, _ = await _setup(env)
    run_id = await _start(env, env_id)
    app = app_of(env.client)

    # 标准输入和凭据不落库，只在 Redis 里加密放着
    async with app.state.sessionmaker() as db:
        command = await db.scalar(
            select(RunnerCommand).where(
                RunnerCommand.run_id == run_id, RunnerCommand.kind == "proc_start"
            )
        )
        assert command is not None
        stored = json.dumps(command.payload, ensure_ascii=False)
        assert SECRET not in stored and PROMPT not in stored
        assert "HOME" not in command.payload["env"]  # HOME 由容器设定，调用方改不了
        command_id = command.id
        run = await db.get(RunnerRun, run_id)
        assert run is not None and run.kind == "proc" and run.status == "queued"
    sealed = await app.state.redis.get(proc_input_key(command_id))
    assert sealed is not None and SECRET.encode() not in sealed

    # 起来之前不在对账名单里
    assert (await _report(env, rtoken))["procs"] == []

    # 领指令时才解开，只能领一次
    command_out = await next_command(env, rtoken, "proc_start")
    proc_input = command_out["payload"]["proc_input"]
    assert base64.b64decode(proc_input["stdin_b64"]).decode() == PROMPT
    assert proc_input["secret_env"] == {"ANTHROPIC_API_KEY": SECRET}
    assert command_out["payload"]["proc_dir"] == f"/workspace/.mw/procs/{run_id}"
    assert await app.state.redis.get(proc_input_key(command_id)) is None

    assert await report_result(env, rtoken, command_out["id"], {"started": True, "pgid": 42})
    procs = (await _report(env, rtoken))["procs"]
    assert [p["run_id"] for p in procs] == [str(run_id)] and procs[0]["acked"] == 0

    # 输出按偏移确认；重复、超前的块不收
    assert await _output(env, rtoken, run_id, 0, b'{"type":"system"}\n') == 18
    assert await _output(env, rtoken, run_id, 0, b'{"type":"system"}\n') == 18
    assert await _output(env, rtoken, run_id, 50, b"x") == 18
    sink.accept = False  # 下游暂时收不下：不推进
    assert await _output(env, rtoken, run_id, 18, b"more\n") == 18
    sink.accept = True
    assert await _output(env, rtoken, run_id, 18, b"more\n") == 23
    assert [offset for offset, _ in sink.chunks] == [0, 18]
    assert (await _report(env, rtoken))["procs"][0]["acked"] == 23

    # 结束：只认第一次
    body = {"exit_code": 0, "state": "exited", "stderr_tail": "done", "error": None}
    first = await env.client.post(
        f"/runner-api/v1/procs/{run_id}/exit", json=body, headers=runner_headers(rtoken)
    )
    assert first.status_code == 200 and first.json() == {"accepted": True}
    again = await env.client.post(
        f"/runner-api/v1/procs/{run_id}/exit", json=body, headers=runner_headers(rtoken)
    )
    assert again.json() == {"accepted": False}
    assert len(sink.exits) == 1 and sink.exits[0]["state"] == "exited"
    async with app.state.sessionmaker() as db:
        finished = await db.get(RunnerRun, run_id)
        assert finished is not None and finished.status == "succeeded"
    assert (await _report(env, rtoken))["procs"] == []

    # 别的服务器的凭证碰不到这个进程
    missing = await env.client.post(
        f"/runner-api/v1/procs/{uuid.uuid4()}/output",
        json={"offset": 0, "data_b64": ""},
        headers=runner_headers(rtoken),
    )
    assert missing.status_code == 404


async def test_failed_start_queued_signal_and_input(env: Env, sink: FakeSink) -> None:
    rtoken, env_id, _ = await _setup(env)
    app = app_of(env.client)

    # 没起来：结束记失败并通知模块
    run_id = await _start(env, env_id)
    command = await next_command(env, rtoken, "proc_start")
    assert await report_result(env, rtoken, command["id"], {}, status="failed")
    assert sink.exits[-1]["run_id"] == str(run_id) and sink.exits[-1]["state"] == "failed"

    # 还在排队时叫停：撤掉指令，直接取消
    queued = await _start(env, env_id)
    async with app.state.sessionmaker() as db:
        run = await db.get(RunnerRun, queued)
        assert run is not None
        await ManagedRunner(db, app.state.settings, app.state.redis).signal(run, "TERM")
        await db.commit()
        assert run.status == "cancelled"

    # 交互输入：只在领指令时解开
    interactive = await _start(env, env_id, interactive=True)
    started = await next_command(env, rtoken, "proc_start")
    assert started["payload"]["interactive"] is True
    assert await report_result(env, rtoken, started["id"], {"started": True, "pgid": 7})
    async with app.state.sessionmaker() as db:
        run = await db.get(RunnerRun, interactive)
        assert run is not None
        managed = ManagedRunner(db, app.state.settings, app.state.redis)
        await managed.send_input(run, "code#state")
        await managed.signal(run, "TERM")
        await db.commit()
    sent = await next_command(env, rtoken, "proc_input")
    assert sent["payload"]["proc_input"] == {"data": "code#state"}
    stopped = await next_command(env, rtoken, "proc_signal")
    assert stopped["payload"]["signal"] == "TERM"


async def test_reclaim_and_admin_treat_module_environments_specially(
    env: Env, sink: FakeSink
) -> None:
    rtoken, env_id, _ = await _setup(env)
    app = app_of(env.client)

    # 没有创建者实例，但不是孤儿
    async with app.state.sessionmaker() as db:
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
        await db.commit()
        current = await db.get(RunnerEnvironment, env_id)
        assert current is not None and current.state == "running"
        assert current.no_auto_archive and current.owner_kind == "llm_bridge"

    # 空闲超过它自己的时长（30 分钟）就停；停了也不自动归档
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerEnvironment)
            .where(RunnerEnvironment.id == env_id)
            .values(last_used_at=specs.now() - timedelta(minutes=31))
        )
        await db.commit()
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
        await db.commit()
    stop = await next_command(env, rtoken, "stop_env")
    assert await report_result(env, rtoken, stop["id"], {})
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerEnvironment)
            .where(RunnerEnvironment.id == env_id)
            .values(last_used_at=specs.now() - timedelta(days=60))
        )
        await db.commit()
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
        await db.commit()
        current = await db.get(RunnerEnvironment, env_id)
        assert current is not None and current.state == "stopped"

    # 容器服务器页面：看得到申请方；不能删、不能归档；能启动
    listed = await env.get("/api/v1/runner/environments")
    item = next(e for e in listed["items"] if e["id"] == str(env_id))
    assert item["owner_kind"] == "llm_bridge"
    assert item["owner_label"] == "MCP 管理器「主桥」· 远程容器「开发机」"
    for action in ("delete", "archive"):
        denied = await env.client.post(
            f"/api/v1/runner/environments/{env_id}/actions",
            json={"action": action, "version": item["version"]},
            headers=env.headers(write=True),
        )
        assert denied.status_code == 409, denied.text
        assert denied.json()["error"]["code"] == "MANAGED_BY_MODULE"
    started = await env.client.post(
        f"/api/v1/runner/environments/{env_id}/actions",
        json={"action": "start", "version": item["version"]},
        headers=env.headers(write=True),
    )
    assert started.status_code == 200, started.text


async def test_exit_output_is_redacted_and_expired_starts_end_as_unknown(
    env: Env, sink: FakeSink
) -> None:
    rtoken, env_id, _ = await _setup(env)
    app = app_of(env.client)
    plain = "cbk_plainvalue_" + "q" * 20  # 不像任何已知格式的凭据：靠注入时记下的值脱敏
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, env_id)
        assert target is not None
        run = await ManagedRunner(db, app.state.settings, app.state.redis).start_proc(
            target,
            owner_kind="llm_bridge",
            owner_ref="container-1",
            label="turn · workbuddy · bs_test0002",
            argv=["/opt/mw/cli/bin/codebuddy", "-p"],
            cwd="/workspace/sessions/bs_test0002",
            secret_env={"CODEBUDDY_API_KEY": plain},
            timeout_seconds=600,
        )
        await db.commit()
        run_id = run.id
    command = await next_command(env, rtoken, "proc_start")
    assert await report_result(env, rtoken, command["id"], {"started": True, "pgid": 3})
    exited = await env.client.post(
        f"/runner-api/v1/procs/{run_id}/exit",
        json={
            "exit_code": 1,
            "state": "exited",
            "stderr_tail": f"auth failed for {plain} and {SECRET}",
            "error": f"bad key {plain}",
        },
        headers=runner_headers(rtoken),
    )
    assert exited.status_code == 200, exited.text
    async with app.state.sessionmaker() as db:
        stored = await db.get(RunnerRun, run_id)
        assert stored is not None
        assert plain not in stored.output and SECRET not in stored.output
        assert "***" in stored.output
    assert plain not in json.dumps(sink.exits) and SECRET not in json.dumps(sink.exits)

    # 起进程的指令过期（runner-agent 没回报）：运行记成结果未知，并告诉模块
    expired = await _start(env, env_id)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerCommand)
            .where(RunnerCommand.run_id == expired, RunnerCommand.kind == "proc_start")
            .values(deadline_at=specs.now() - timedelta(seconds=1))
        )
        await db.commit()
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
        await db.commit()
        stale = await db.get(RunnerRun, expired)
        assert stale is not None and stale.status == "unknown" and stale.finished_at is not None
    assert sink.exits[-1]["run_id"] == str(expired) and sink.exits[-1]["state"] == "lost"


async def test_started_proc_that_never_reports_ends_as_unknown(env: Env, sink: FakeSink) -> None:
    """集成审查第 3 条：进程起来了，之后再没有回报（外壳被杀、runner-agent 一直没上线）。
    过了时限加宽限，回收记为结果未知并通知模块，容器不再一直算“正忙”。"""
    rtoken, env_id, _ = await _setup(env)
    app = app_of(env.client)
    run_id = await _start(env, env_id)
    command = await next_command(env, rtoken, "proc_start")
    assert await report_result(env, rtoken, command["id"], {"started": True, "pgid": 9})
    async with app.state.sessionmaker() as db:
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
        await db.commit()
        fresh = await db.get(RunnerRun, run_id)
        assert fresh is not None and fresh.status == "running"  # 还在时限里：不动
        late = timedelta(seconds=600 + PROC_START_DEADLINE + PROC_EXPIRE_GRACE + 1)
        await db.execute(
            update(RunnerRun)
            .where(RunnerRun.id == run_id)
            .values(started_at=specs.now() - late, created_at=specs.now() - late)
        )
        await db.commit()
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
        await db.commit()
        stale = await db.get(RunnerRun, run_id, populate_existing=True)
        assert stale is not None and stale.status == "unknown" and stale.finished_at is not None
    assert sink.exits[-1]["run_id"] == str(run_id) and sink.exits[-1]["state"] == "lost"
    # 之后迟到的结束回报：不改已经记下的结果
    late_exit = await env.client.post(
        f"/runner-api/v1/procs/{run_id}/exit",
        json={"exit_code": 0, "state": "exited", "stderr_tail": "", "error": None},
        headers=runner_headers(rtoken),
    )
    assert late_exit.status_code == 200, late_exit.text
    async with app.state.sessionmaker() as db:
        kept = await db.get(RunnerRun, run_id)
        assert kept is not None and kept.status == "unknown"


def test_agent_version_gate() -> None:
    """托管进程的指令从 runner-agent 0.4.0 起才有：旧接入包要先重新部署。"""
    assert agent_outdated(RunnerHost(agent_version=VERSION)) is None
    assert agent_outdated(RunnerHost(agent_version="1.0")) is None
    for old in ("0.3.0", "0.1.0", None, "dev"):
        reason = agent_outdated(RunnerHost(agent_version=old))
        assert reason is not None and "重新部署接入包" in reason


async def test_old_agent_is_refused_before_any_command(env: Env) -> None:
    _, env_id, host_id = await _setup(env)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerHost).where(RunnerHost.id == host_id).values(agent_version="0.3.0")
        )
        await db.commit()
    with pytest.raises(RunnerError) as caught:
        await _start(env, env_id)
    assert caught.value.code == "AGENT_OUTDATED"
    async with app.state.sessionmaker() as db:
        kinds = set(
            await db.scalars(select(RunnerCommand.kind).where(RunnerCommand.host_id == host_id))
        )
        assert "proc_start" not in kinds
        assert not list(await db.scalars(select(RunnerRun).where(RunnerRun.kind == "proc")))


async def test_module_templates_cannot_be_squatted_or_changed(env: Env) -> None:
    """集成审查的阻塞问题：智能体抢先用模块的模板名建模板，再把镜像换掉。"""
    await add_host(env)
    app = app_of(env.client)
    workspace_id = uuid.UUID(env.workspace_id)
    instance_id = uuid.UUID(await new_instance(env))
    builtin_spec = {"image": cfg.BASE_IMAGE, "steps": cfg.BUILTIN_STEPS, "lifetime": "persistent"}
    async with app.state.sessionmaker() as db:
        host = await db.scalar(select(RunnerHost).where(RunnerHost.workspace_id == workspace_id))
        instance = await db.get(AgentInstance, instance_id)
        assert host is not None and instance is not None
        runner = Runner(db, app.state.settings, app.state.redis)
        name = f"mw-llm-dev-{str(host.id)[:8]}"

        # 保留前缀：智能体和管理员都不能用它建新模板
        for viewer in (Viewer(workspace_id, instance=instance), Viewer(workspace_id)):
            with pytest.raises(RunnerError) as caught:
                await runner.save_template(
                    viewer, name=name, description="", spec={"image": "evil/image:1"}
                )
            assert caught.value.code == "VALIDATION_FAILED" and "留给" in caught.value.message

        # 保留之前就被抢注的同名模板：模块不用、不改，换备用名
        squat = RunnerTemplate(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=name,
            owner_instance_id=instance.id,
            spec={"image": "evil/image:1"},
            build_host_id=host.id,
            build_status="ready",
            image_ref="evil/image:1",
        )
        db.add(squat)
        await db.flush()
        managed = ManagedRunner(db, app.state.settings, app.state.redis)
        mine = await managed.ensure_template(
            workspace_id,
            host,
            name=name,
            description="内置",
            spec=builtin_spec,
            actor_id=None,
            owner_kind="llm_bridge",
        )
        assert mine.id != squat.id
        assert mine.name == f"{name}-2" and mine.owner_kind == "llm_bridge"
        assert mine.owner_instance_id is None
        again = await managed.ensure_template(
            workspace_id,
            host,
            name=name,
            description="内置",
            spec=builtin_spec,
            actor_id=None,
            owner_kind="llm_bridge",
        )
        assert again.id == mine.id
        await db.refresh(squat)
        assert squat.spec == {"image": "evil/image:1"} and squat.owner_kind == "instance"

        # 模块的模板：智能体改不了（不论是不是它建的同名模板）
        with pytest.raises(RunnerError) as caught:
            await runner.save_template(
                Viewer(workspace_id, instance=instance),
                name=mine.name,
                description="",
                spec={"image": "evil/image:2"},
            )
        assert caught.value.code == "MANAGED_BY_MODULE"
        template_id, version = mine.id, mine.version
        await db.commit()

    # 管理员页面：保存、停用、重建都不行
    saved = await env.client.put(
        "/api/v1/runner/templates",
        json={"name": f"{name}-2", "spec": {"image": "evil/image:3"}, "version": version},
        headers=env.headers(write=True),
    )
    assert saved.status_code == 409 and saved.json()["error"]["code"] == "MANAGED_BY_MODULE"
    status = await env.client.put(
        f"/api/v1/runner/templates/{template_id}/status",
        json={"status": "disabled", "version": version},
        headers=env.headers(write=True),
    )
    assert status.status_code == 409 and status.json()["error"]["code"] == "MANAGED_BY_MODULE"
    rebuilt = await env.client.post(
        f"/api/v1/runner/templates/{template_id}/rebuild", headers=env.headers(write=True)
    )
    assert rebuilt.status_code == 409 and rebuilt.json()["error"]["code"] == "MANAGED_BY_MODULE"
    async with app.state.sessionmaker() as db:
        row = await db.get(RunnerTemplate, template_id)
        assert row is not None and row.spec["image"] == cfg.BASE_IMAGE and row.status == "active"


async def test_module_containers_left_without_a_registration_are_deleted(env: Env) -> None:
    """集成审查第 5 条：登记删了、容器服务器那边的删除失败，容器和存着凭据的数据卷不能一直留着。"""
    from app.modules.llm_bridge import remote as remote_module

    rtoken, env_id, _ = await _setup(env)  # 模块建的环境，没有远程容器的登记：就是留下的
    app = app_of(env.client)

    async def sweep() -> list[uuid.UUID]:
        async with app.state.sessionmaker() as db:
            done = await remote_module.delete_orphans(db, app.state.settings, app.state.redis)
            await db.commit()
            return done

    assert await sweep() == [env_id]
    assert await sweep() == []  # 删除指令还没回报：不重复发
    command = await next_command(env, rtoken, "delete_env")
    assert await report_result(env, rtoken, command["id"], {}, status="failed")
    assert await sweep() == []  # 刚失败：过一会儿再试
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerCommand)
            .where(RunnerCommand.id == uuid.UUID(command["id"]))
            .values(created_at=specs.now() - timedelta(seconds=remote_module.ORPHAN_RETRY + 1))
        )
        await db.commit()
    assert await sweep() == [env_id]
    retry = await next_command(env, rtoken, "delete_env")
    assert await report_result(env, rtoken, retry["id"], {})
    async with app.state.sessionmaker() as db:
        gone = await db.get(RunnerEnvironment, env_id)
        assert gone is not None and gone.state == "deleted"
    assert await sweep() == []
