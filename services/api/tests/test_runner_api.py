"""运行服务器（T-0136 ~ T-0138）：真实 PostgreSQL / Redis。

测试代替 runner-agent 走 /runner-api/v1/*。覆盖：运行服务器凭证与入口（失败一律 401）、接入包、
实例的运行环境权限（不开看不到工具、调用 FORBIDDEN_CAPABILITY）、
env_create → 下发 create_env → 回报 → running；env_exec 同步返回与“结束后唤醒”；密钥只在下发时解密、
不落库、输出脱敏；复用、限额、kvm 能力检查；模板构建；对账（丢失 / 记录外）；指令超时；
关掉权限时停止环境；管理员接口。真实 Docker 与 gVisor 的行为在 CI 的运行服务器验收（T-0141）里测。
"""

import asyncio
import base64
import hashlib
import io
import secrets
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from app.domain import runner_reclaim
from app.domain import runner_specs as specs
from app.models import AgentSecretAccess, AgentWakeup, RunnerCommand, RunnerEnvironment
from tests.helpers import tool_error
from tests.test_mcp_api import call, issue_token, new_instance, rpc
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration

SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(secrets.token_hex(32)),
    "agent_ingress_trusted_proxies": "127.0.0.1/32",
    "agent_intranet_url": "https://10.0.0.5:8443",
}
SECRET_VALUE = "ghp_" + "S3cr3tValue" * 3
POLL = {
    "agent_version": "0.1.0",
    "docker_version": "29.9.1",
    "runtimes": ["io.containerd.runc.v2", "runc", "runsc"],
    "kvm": False,
    "cpus": 8,
    "mem_bytes": 32 * 1024**3,
    "disk_bytes": 500 * 1024**3,
    "disk_free_bytes": 400 * 1024**3,
    "wait_seconds": 0,
}


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, **SETTINGS) as value:
        yield value


def runner_headers(token: str | None, ingress: str = "intranet") -> dict[str, str]:
    headers = {"X-MyWS-Ingress": ingress}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def physical_id(token: str) -> str:
    """Synthetic stable host identity; real agents read the host machine-id mount."""
    return hashlib.sha256(("test-host:" + token).encode()).hexdigest()


async def add_host(env: Env) -> str:
    """添加运行服务器并下载接入包，返回接入包里的凭证。"""
    created = await env.client.post(
        "/api/v1/runner/host",
        json={"name": "runner-1", "ingress": "intranet"},
        headers=env.headers(write=True),
    )
    assert created.status_code == 201, created.text
    bundle = await env.client.post("/api/v1/runner/host/bundle", headers=env.headers(write=True))
    assert bundle.status_code == 200, bundle.text
    assert bundle.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
        names = set(zf.namelist())
        assert "myworkspace-runner/agent/runner_agent/proxy.py" in names
        assert "myworkspace-runner/install-gvisor.sh" in names
        compose = zf.read("myworkspace-runner/compose.yaml").decode()
        assert "@sha256:" in compose and "__IMAGE__" not in compose
        assert "privileged" not in compose
        assert "extra_hosts" not in compose and "@EXTRA_HOSTS@" not in compose  # 内网入口不映射
        runner_env = zf.read("myworkspace-runner/runner.env").decode()
        info = zf.getinfo("myworkspace-runner/runner.env")
        assert (info.external_attr >> 16) & 0o777 == 0o600
    values = dict(
        line.split("=", 1) for line in runner_env.splitlines() if "=" in line and line[0] != "#"
    )
    assert values["MYWS_RUNNER_URL"] == "https://10.0.0.5:8443"
    assert "10.0.0.5" in values["MYWS_RUNNER_BLOCK"]
    token = values["MYWS_RUNNER_TOKEN"]
    assert token.startswith("mwr_")
    await poll(env, token)  # 上线
    sample_id = await begin_sample(env, token)
    report = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": sample_id,
            "physical_host_id": physical_id(token),
            "mem_bytes": POLL["mem_bytes"],
            "mem_free_bytes": 24 * 1024**3,
            "disk_bytes": POLL["disk_bytes"],
            "disk_free_bytes": POLL["disk_free_bytes"],
            "load": 0.5,
        },
        headers=runner_headers(token),
    )
    assert report.status_code == 200, report.text
    return token


async def begin_sample(env: Env, token: str) -> str:
    result = await env.client.post(
        "/runner-api/v1/report/start", json={}, headers=runner_headers(token)
    )
    assert result.status_code == 200, result.text
    assert result.json()["expires_in"] == 90
    return str(result.json()["sample_id"])


async def test_report_sampling_requires_auth_and_cannot_be_replayed(env: Env) -> None:
    token = await add_host(env)
    denied = await env.client.post("/runner-api/v1/report/start", json={})
    assert denied.status_code == 401
    sample_id = await begin_sample(env, token)
    payload = {
        "sample_id": sample_id,
        "physical_host_id": physical_id(token),
        "mem_bytes": POLL["mem_bytes"],
        "mem_free_bytes": 24 << 30,
        "disk_bytes": POLL["disk_bytes"],
        "disk_free_bytes": POLL["disk_free_bytes"],
        "load": 0.1,
    }
    first = await env.client.post(
        "/runner-api/v1/report", json=payload, headers=runner_headers(token)
    )
    assert first.status_code == 200, first.text
    replay = await env.client.post(
        "/runner-api/v1/report", json=payload, headers=runner_headers(token)
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "REPORT_SAMPLE_STALE"


async def poll(env: Env, token: str, wait: int = 0) -> list[dict[str, Any]]:
    response = await env.client.post(
        "/runner-api/v1/poll", json={**POLL, "wait_seconds": wait}, headers=runner_headers(token)
    )
    assert response.status_code == 200, response.text
    commands: list[dict[str, Any]] = response.json()["commands"]
    return commands


_RECEIVED: dict[str, list[dict[str, Any]]] = {}


async def next_command(env: Env, token: str, kind: str, seconds: float = 10) -> dict[str, Any]:
    """像 runner-agent 一样领指令；一次可能领到好几条，没用上的留到下次。"""
    buffer = _RECEIVED.setdefault(token, [])
    loop = asyncio.get_running_loop()
    until = loop.time() + seconds
    while True:
        for command in buffer:
            if command["kind"] == kind:
                buffer.remove(command)
                return command
        if loop.time() >= until:
            raise AssertionError(f"没有等到 {kind} 指令")
        buffer.extend(await poll(env, token, wait=1))
        await asyncio.sleep(0.05)


async def report_result(
    env: Env, token: str, command_id: str, result: dict[str, Any], status: str = "done"
) -> bool:
    response = await env.client.post(
        f"/runner-api/v1/commands/{command_id}/result",
        json={"status": status, "result": result, "error": None if status == "done" else "坏了"},
        headers=runner_headers(token),
    )
    assert response.status_code == 200, response.text
    accepted: bool = response.json()["accepted"]
    return accepted


async def enable_runner(env: Env, instance_id: str, limits: dict[str, float] | None = None) -> None:
    current = await env.client.get(
        f"/api/v1/agents/instances/{instance_id}/runner", headers=env.headers()
    )
    assert current.status_code == 200, current.text
    response = await env.client.put(
        f"/api/v1/agents/instances/{instance_id}/runner",
        json={"version": current.json()["version"], "enabled": True, "limits": limits or {}},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True


async def agent(env: Env, *, limits: dict[str, float] | None = None) -> tuple[str, str]:
    instance_id = await new_instance(env)
    await enable_runner(env, instance_id, limits)
    return instance_id, await issue_token(env, instance_id)


async def create_env(env: Env, token: str, rtoken: str, **arguments: Any) -> dict[str, Any]:
    """env_create：一边调用工具，一边当 runner-agent 回报建好了。"""
    body = {
        "purpose": "跑单元测试",
        "lifetime": "session",
        "spec": {"image": "python:3.12-slim"},
        **arguments,
    }
    task = asyncio.create_task(call(env, token, "env_create", body))
    command = await next_command(env, rtoken, "create_env")
    assert await report_result(
        env, rtoken, command["id"], {"seconds": 2.5, "image_digest": "python@sha256:" + "a" * 64}
    )
    result = await task
    assert result["isError"] is False, result
    return {"result": result["structuredContent"], "command": command}


async def test_runner_api_requires_the_runner_credential(env: Env) -> None:
    token = await add_host(env)
    for headers in (
        runner_headers(None),
        runner_headers("mwr_" + "x" * 43),
        runner_headers(token, ingress="public"),  # 入口不对
        {"Authorization": f"Bearer {token}"},  # 没有入口请求头
    ):
        response = await env.client.post("/runner-api/v1/poll", json=POLL, headers=headers)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "RUNNER_UNAUTHORIZED"
    # 实例凭证不能当运行服务器凭证用
    instance_token = await issue_token(env, await new_instance(env))
    response = await env.client.post(
        "/runner-api/v1/poll", json=POLL, headers=runner_headers(instance_token)
    )
    assert response.status_code == 401
    # 重新下载接入包：旧凭证立即失效
    again = await env.client.post("/api/v1/runner/host/bundle", headers=env.headers(write=True))
    assert again.status_code == 200
    response = await env.client.post(
        "/runner-api/v1/poll", json=POLL, headers=runner_headers(token)
    )
    assert response.status_code == 401
    overview = await env.client.get("/api/v1/runner/overview", headers=env.headers())
    assert overview.status_code == 200, overview.text
    host = overview.json()["host"]
    assert host["status"] == "online" and "runsc" in host["runtimes"]


async def test_runner_tools_need_the_instance_switch(env: Env) -> None:
    await add_host(env)
    instance_id = await new_instance(env)
    token = await issue_token(env, instance_id)
    listed = (await rpc(env, token, "tools/list")).json()["result"]["tools"]
    assert not {t["name"] for t in listed} & {"env_create", "env_exec", "runner_templates"}
    result = await call(
        env, token, "env_create", {"purpose": "x", "lifetime": "once", "command": "true"}
    )
    assert tool_error(result)["code"] == "FORBIDDEN_CAPABILITY"
    await enable_runner(env, instance_id)
    listed = (await rpc(env, token, "tools/list")).json()["result"]["tools"]
    names = {t["name"] for t in listed}
    assert {"env_create", "env_exec", "env_files", "runner_template_save", "env_find"} <= names


async def test_env_create_exec_and_secret_handling(env: Env) -> None:
    rtoken = await add_host(env)
    instance_id, token = await agent(env)
    secret = await env.client.post(
        "/api/v1/agents/secrets",
        json={"name": "GitHub", "kind": "api_token", "value": SECRET_VALUE},
        headers=env.headers(write=True),
    )
    assert secret.status_code == 201, secret.text
    detail = await env.client.get(f"/api/v1/agents/instances/{instance_id}", headers=env.headers())
    granted = await env.client.put(
        f"/api/v1/agents/instances/{instance_id}/secret-grants",
        json={
            "version": detail.json()["version"],
            "grants": [{"secret_id": secret.json()["id"], "ref_id": "gh_token"}],
        },
        headers=env.headers(write=True),
    )
    assert granted.status_code == 200, granted.text
    # 没授权的密钥不能用
    denied = await call(
        env,
        token,
        "env_create",
        {
            "purpose": "x",
            "lifetime": "session",
            "spec": {"image": "alpine:3", "secrets": ["other_key"]},
        },
    )
    assert tool_error(denied)["code"] == "FORBIDDEN"

    created = await create_env(
        env,
        token,
        rtoken,
        reuse_key="unit-tests",
        spec={
            "image": "python:3.12-slim",
            "secrets": ["gh_token"],
            "network": "allowlist",
            "domains": ["pypi.org", "*.pythonhosted.org"],
            "env": {"PYTHONUNBUFFERED": "1"},
        },
    )
    environment = created["result"]["environment"]
    assert environment["state"] == "running"
    assert environment["runtime"] == "gvisor" and environment["user"] == "user"
    assert environment["untrusted"]["purpose"] == "跑单元测试"
    payload = created["command"]["payload"]
    assert payload["secret_values"] == {"gh_token": SECRET_VALUE}
    assert payload["network_mode"] == "allowlist" and payload["runtime"] == "gvisor"
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        stored = await db.get(RunnerCommand, uuid.UUID(created["command"]["id"]))
        assert stored is not None and "secret_values" not in stored.payload
        assert SECRET_VALUE not in str(stored.payload)
        access = list(
            await db.scalars(
                select(AgentSecretAccess).where(AgentSecretAccess.ref_id == "gh_token")
            )
        )
        assert access and access[0].result == "ok"

    # 同一个 reuse_key：复用，不再下发 create_env
    reused = await call(
        env,
        token,
        "env_create",
        {
            "purpose": "再跑一次",
            "lifetime": "session",
            "reuse_key": "unit-tests",
            "spec": {"image": "python:3.12-slim", "secrets": ["gh_token"]},
        },
    )
    assert reused["structuredContent"]["reused"] is True
    assert reused["structuredContent"]["environment"]["id"] == environment["id"]

    # env_exec：同步返回；输出里的密钥被脱敏
    task = asyncio.create_task(
        call(
            env,
            token,
            "env_exec",
            {"environment_id": environment["id"], "command": "pytest -q", "wait_seconds": 20},
        )
    )
    command = await next_command(env, rtoken, "exec")
    assert command["payload"]["cmd"] == ["sh", "-lc", "pytest -q"]
    assert command["payload"]["secret_values"] == {"gh_token": SECRET_VALUE}
    output = f"3 passed\ntoken={SECRET_VALUE}\n".encode()
    assert await report_result(
        env,
        rtoken,
        command["id"],
        {"exit_code": 0, "output_b64": base64.b64encode(output).decode(), "seconds": 1.2},
    )
    # 重复回报只认第一次
    assert not await report_result(env, rtoken, command["id"], {"exit_code": 1})
    result = await task
    run = result["structuredContent"]
    assert run["status"] == "succeeded" and run["exit_code"] == 0
    assert "3 passed" in run["untrusted"]["output"]
    assert SECRET_VALUE not in run["untrusted"]["output"]

    # 执行记录：命令脱敏记录，有 env_exec 的直接执行记录
    records = await env.client.get(
        "/api/v1/ai/tool-executions", params={"tool_name": "env_exec"}, headers=env.headers()
    )
    assert records.status_code == 200, records.text
    items = records.json()["items"]
    assert items and items[0]["status"] == "succeeded"

    # 管理员：运行记录里有完整输出（脱敏）
    runs = await env.client.get("/api/v1/runner/runs", headers=env.headers())
    assert runs.status_code == 200, runs.text
    run_id = runs.json()["items"][0]["run_id"]
    full = await env.client.get(f"/api/v1/runner/runs/{run_id}", headers=env.headers())
    assert SECRET_VALUE not in full.text and "3 passed" in full.json()["untrusted"]["output"]


async def test_long_runs_wake_the_agent(env: Env) -> None:
    rtoken = await add_host(env)
    instance_id, token = await agent(env)
    environment = (await create_env(env, token, rtoken))["result"]["environment"]
    started = await call(
        env,
        token,
        "env_exec",
        {"environment_id": environment["id"], "command": ["sleep", "600"], "wait_seconds": 0},
    )
    run = started["structuredContent"]
    assert run["status"] in ("queued", "running") and run["finished_at"] is None
    command = await next_command(env, rtoken, "exec")
    await report_result(
        env, rtoken, command["id"], {"exit_code": 0, "output_b64": "", "seconds": 600}
    )
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        wakeups = list(
            await db.scalars(
                select(AgentWakeup).where(AgentWakeup.instance_id == uuid.UUID(instance_id))
            )
        )
    assert len(wakeups) == 1 and wakeups[0].source == "runner"
    assert run["run_id"] in wakeups[0].prompt
    status = await call(env, token, "env_run", {"run_id": run["run_id"]})
    assert status["structuredContent"]["status"] == "succeeded"

    # 超时：环境停止
    task = asyncio.create_task(
        call(
            env,
            token,
            "env_exec",
            {
                "environment_id": environment["id"],
                "command": "x",
                "timeout_seconds": 5,
                "wait_seconds": 20,
            },
        )
    )
    command = await next_command(env, rtoken, "exec")
    await report_result(
        env, rtoken, command["id"], {"exit_code": None, "timed_out": True, "seconds": 5}
    )
    timed_out = (await task)["structuredContent"]
    assert timed_out["status"] == "timeout"
    found = await call(env, token, "env_find", {})
    assert found["structuredContent"]["items"][0]["state"] == "stopped"


async def test_quota_and_capability_checks(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env, limits={"envs": 1})
    await create_env(env, token, rtoken)
    second = await call(
        env,
        token,
        "env_create",
        {"purpose": "又一个", "lifetime": "session", "spec": {"image": "alpine:3"}},
    )
    assert tool_error(second)["code"] == "QUOTA_EXCEEDED"
    _other, token2 = await agent(env)
    kvm = await call(
        env,
        token2,
        "env_create",
        {
            "purpose": "安卓模拟器",
            "lifetime": "session",
            "spec": {"image": "budtmo/docker-android:emulator_14.0", "runtime": "kvm"},
        },
    )
    error = tool_error(kvm)
    assert error["code"] == "NO_ELIGIBLE_SERVER"
    once = await call(
        env, token2, "env_create", {"purpose": "x", "lifetime": "once", "spec": {"image": "a:1"}}
    )
    assert tool_error(once)["code"] == "VALIDATION_FAILED"


async def test_once_environment_runs_and_is_deleted(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    task = asyncio.create_task(
        call(
            env,
            token,
            "env_create",
            {
                "purpose": "算一下",
                "lifetime": "once",
                "spec": {"image": "python:3.12-slim", "runtime": "runc", "user": "root"},
                "command": ["python", "-c", "print(1+1)"],
                "files": [{"path": "data/input.txt", "text": "hello"}],
            },
        )
    )
    created = await next_command(env, rtoken, "create_env")
    assert created["payload"]["files"] == [
        {"path": "data/input.txt", "b64": base64.b64encode(b"hello").decode()}
    ]
    assert created["payload"]["runtime"] == "runc" and created["payload"]["user"] == "root"
    await report_result(env, rtoken, created["id"], {"seconds": 1})
    executed = await next_command(env, rtoken, "exec")
    await report_result(
        env,
        rtoken,
        executed["id"],
        {"exit_code": 0, "output_b64": base64.b64encode(b"2\n").decode(), "seconds": 0.3},
    )
    result = (await task)["structuredContent"]
    assert result["run"]["status"] == "succeeded" and "2" in result["run"]["untrusted"]["output"]
    assert result["environment"]["state"] == "pending_delete"
    # 下一轮回收：删除
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await runner_reclaim.tick(db, app.state.settings, app.state.redis)
    deleted = await next_command(env, rtoken, "delete_env")
    await report_result(env, rtoken, deleted["id"], {})
    found = await call(env, token, "env_find", {"include_deleted": True})
    assert found["structuredContent"]["items"][0]["state"] == "deleted"
    # 非默认选项（runc、root）通知了管理员
    notes = await env.client.get("/api/v1/notifications", headers=env.headers())
    assert any("runc" in n["body"] and "root" in n["body"] for n in notes.json()["items"]), (
        notes.text
    )


async def test_templates_build_on_the_runner(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    task = asyncio.create_task(
        call(
            env,
            token,
            "runner_template_save",
            {
                "name": "py-tests",
                "description": "Python 3.12 + pytest",
                "spec": {"image": "python:3.12-slim", "steps": ["RUN pip install pytest"]},
                "wait_seconds": 20,
            },
        )
    )
    build = await next_command(env, rtoken, "build_template")
    assert build["payload"]["steps"] == ["RUN pip install pytest"]
    assert build["payload"]["tag"].startswith("myworkspace/tpl-")
    await report_result(
        env,
        rtoken,
        build["id"],
        {
            "image_ref": build["payload"]["tag"],
            "image_digest": "sha256:" + "b" * 64,
            "image_bytes": 150 * 1024 * 1024,
            "seconds": 42.0,
            "log": "Successfully built",
        },
    )
    saved = (await task)["structuredContent"]
    template = saved["template"]
    assert saved["created"] is True and template["build_status"] == "ready"
    assert template["untrusted"]["description"] == "Python 3.12 + pytest"
    assert template["mine"] is True and template["image_mb"] == 150
    # 有安装步骤的规格不能直接建环境：要先建模板
    direct = await call(
        env,
        token,
        "env_create",
        {
            "purpose": "x",
            "lifetime": "session",
            "spec": {"image": "python:3.12-slim", "steps": ["RUN true"]},
        },
    )
    assert tool_error(direct)["code"] == "VALIDATION_FAILED"
    created = await create_env(env, token, rtoken, template="py-tests")
    assert created["command"]["payload"]["image"] == build["payload"]["tag"]
    assert created["result"]["environment"]["template"] == "py-tests"
    # 别人的模板不能改
    _other, token2 = await agent(env)
    other = await call(
        env,
        token2,
        "runner_template_save",
        {"name": "py-tests", "spec": {"image": "alpine:3"}, "wait_seconds": 0},
    )
    assert tool_error(other)["code"] == "FORBIDDEN"
    listed = await env.client.get("/api/v1/runner/templates", headers=env.headers())
    assert [t["name"] for t in listed.json()["items"]] == ["py-tests"]
    # 管理员页面：带版本号与构建日志，改状态按版本号防并发
    item = listed.json()["items"][0]
    assert isinstance(item["version"], int) and "build_log" in item
    stale = await env.client.put(
        f"/api/v1/runner/templates/{item['id']}/status",
        json={"status": "disabled", "version": item["version"] - 1},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    disabled = await env.client.put(
        f"/api/v1/runner/templates/{item['id']}/status",
        json={"status": "disabled", "version": item["version"]},
        headers=env.headers(write=True),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["version"] == item["version"] + 1


async def test_report_marks_lost_and_stray_containers(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    environment = (await create_env(env, token, rtoken))["result"]["environment"]
    stray_id = str(uuid.uuid4())
    sample_id = await begin_sample(env, rtoken)
    report = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": sample_id,
            "physical_host_id": physical_id(rtoken),
            "containers": [{"env_id": stray_id, "name": "mw-env-0123456789ab", "state": "running"}],
            "volumes": [],
            "images": [],
            "egress": {
                environment["id"]: [
                    {"host": "pypi.org", "port": 443, "allowed": True, "count": 3, "bytes": 9000},
                    {"host": "10.0.0.8", "port": 22, "allowed": False, "count": 1, "bytes": 0},
                ]
            },
            "mem_bytes": 32 * 1024**3,
            "mem_free_bytes": 20 * 1024**3,
        },
        headers=runner_headers(rtoken),
    )
    assert report.status_code == 200, report.text
    assert report.json() == {"lost": [environment["id"]], "stray": ["mw-env-0123456789ab"]}
    listed = await env.client.get(
        "/api/v1/runner/environments", params={"include_deleted": True}, headers=env.headers()
    )
    item = listed.json()["items"][0]
    assert item["state"] == "failed" and "丢失" in item["state_reason"]
    assert item["egress"]["hosts"]["10.0.0.8:22"]["denied"] == 1


async def test_paused_container_report_keeps_its_compute_reservation(env: Env) -> None:
    from app.domain.runner_placement import compute_reserved
    from app.models import RunnerEnvironment
    from tests.workspace_support import app_of

    runner_token = await add_host(env)
    _instance, token = await agent(env)
    environment = (await create_env(env, token, runner_token))["result"]["environment"]
    sample_id = await begin_sample(env, runner_token)
    response = await env.client.post(
        "/runner-api/v1/report",
        json={
            "sample_id": sample_id,
            "physical_host_id": physical_id(runner_token),
            "containers": [{"env_id": environment["id"], "name": "paused", "state": "paused"}],
        },
        headers=runner_headers(runner_token),
    )
    assert response.status_code == 200, response.text
    async with app_of(env.client).state.sessionmaker() as db:
        item = await db.get(RunnerEnvironment, uuid.UUID(environment["id"]))
        assert compute_reserved(item), "paused processes retain their allocated memory"
        assert item.state != "stopped"


async def test_deadlines_retry_or_mark_unknown(env: Env) -> None:
    rtoken = await add_host(env)
    _instance, token = await agent(env)
    environment = (await create_env(env, token, rtoken))["result"]["environment"]
    started = await call(
        env,
        token,
        "env_exec",
        {"environment_id": environment["id"], "command": "make", "wait_seconds": 0},
    )
    await next_command(env, rtoken, "exec")  # 领走了，但一直不回报
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(RunnerCommand)
            .where(RunnerCommand.kind == "exec")
            .values(deadline_at=specs.now() - timedelta(seconds=1))
        )
        await db.commit()
        result = await runner_reclaim.tick(db, app.state.settings, app.state.redis)
    assert result.expired == 1
    status = await call(env, token, "env_run", {"run_id": started["structuredContent"]["run_id"]})
    assert status["structuredContent"]["status"] == "unknown"


async def test_disabling_the_switch_stops_environments(env: Env) -> None:
    rtoken = await add_host(env)
    instance_id, token = await agent(env)
    environment = (await create_env(env, token, rtoken))["result"]["environment"]
    current = await env.client.get(
        f"/api/v1/agents/instances/{instance_id}/runner", headers=env.headers()
    )
    off = await env.client.put(
        f"/api/v1/agents/instances/{instance_id}/runner",
        json={"version": current.json()["version"], "enabled": False},
        headers=env.headers(write=True),
    )
    assert off.status_code == 200, off.text
    stop = await next_command(env, rtoken, "stop_env")
    assert stop["payload"]["env_id"] == environment["id"]
    await report_result(env, rtoken, stop["id"], {})
    result = await call(env, token, "env_find", {})
    assert tool_error(result)["code"] == "FORBIDDEN_CAPABILITY"
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        stored = await db.get(RunnerEnvironment, uuid.UUID(environment["id"]))
        assert stored is not None and stored.state == "stopped"
    # 移除运行服务器：还有没删除的环境时拒绝
    overview = await env.client.get("/api/v1/runner/overview", headers=env.headers())
    version = overview.json()["host"]["version"]
    removed = await env.client.delete(
        "/api/v1/runner/host", params={"version": version}, headers=env.headers(write=True)
    )
    assert removed.status_code == 409
    assert removed.json()["error"]["code"] == "RUNNER_HAS_ENVIRONMENTS"
