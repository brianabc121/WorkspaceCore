"""一次点击新建并部署（T-0180）：一个事务、幂等、收据、并发与恢复。

`POST /agents/deployments` 的 request_id 同时是 Idempotency-Key；目标服务器沿用
test_agent_ssh_install.py 的进程内假服务器（先真实地初始化）。
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.domain import agent_deployments, idempotency
from app.domain.agent_servers import AgentServerService
from app.domain.agent_ssh_install import SshJobRunner
from tests import test_agent_ssh_install as base
from tests.helpers import (
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    csrf,
    run_sql,
)
from tests.test_agent_ssh_install import PASSWORD, SETTINGS, FakeHost, Owner, ssh_host

pytestmark = pytest.mark.integration

owner = base.owner
fast_online = base.fast_online


async def _call(owner: Owner, method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = {**owner.headers(), **kwargs.pop("headers", {})}
    return await owner.client.request(method, path, headers=headers, **kwargs)


async def _server(owner: Owner, server_id: str) -> dict[str, Any]:
    response = await _call(owner, "GET", f"/api/v1/agents/servers/{server_id}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["server"]
    return data


async def _ready_server(owner: Owner, port: int) -> str:
    """添加、确认、初始化一台服务器（不建实例）。"""
    server = await owner.add_server(port)
    current = await _server(owner, server["id"])
    confirmed = await _call(
        owner,
        "POST",
        f"/api/v1/agents/servers/{server['id']}/confirm",
        json={"version": current["version"], "fingerprint": current["ssh"]["host_key_fingerprint"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    job = await owner.initialize(server["id"])
    assert job["status"] == "succeeded", job
    return str(server["id"])


def _body(server: dict[str, Any], request_id: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "server_id": server["id"],
        "server_version": server["version"],
        "name": "甲",
        **extra,
    }


async def _deploy(owner: Owner, body: dict[str, Any], **kwargs: Any) -> httpx.Response:
    headers = {"Idempotency-Key": body["request_id"], **kwargs.pop("headers", {})}
    return await _call(
        owner, "POST", "/api/v1/agents/deployments", json=body, headers=headers, **kwargs
    )


async def _receipt(owner: Owner, request_id: str) -> httpx.Response:
    return await _call(owner, "GET", f"/api/v1/agents/deployments/requests/{request_id}")


async def _count(owner: Owner, table: str, where: str = "true") -> int:
    count: int = await run_sql(
        owner.database_url,
        f"SELECT count(*) FROM workspace.{table} WHERE {where}",  # noqa: S608 - 测试里写死的表名
    )
    return count


async def _finished(owner: Owner, instance_id: str) -> dict[str, Any]:
    for _ in range(600):
        state = (
            await _call(owner, "GET", f"/api/v1/agents/instances/{instance_id}/deployment")
        ).json()
        if state["job"] and state["job"]["status"] != "running":
            return dict(state)
        await asyncio.sleep(0.05)
    raise AssertionError("部署 30 秒内没有结束")


async def _online(owner: Owner, host: FakeHost, instance_id: str) -> None:
    """模拟这个实例新部署的 OpenClaw 用新凭证连上工作台。"""
    env = host.home / "myworkspace-openclaw" / instance_id.replace("-", "")[:12] / "myworkspace.env"
    while True:
        if env.exists() and (host.state / "up").exists():
            for line in env.read_text().splitlines():
                if line.startswith("MYWS_INSTANCE_TOKEN="):
                    import hashlib

                    await run_sql(
                        owner.database_url,
                        "UPDATE workspace.agent_credentials SET last_used_at = now()"
                        " WHERE token_hash = $1",
                        hashlib.sha256(line.split("=", 1)[1].encode()).hexdigest(),
                    )
        await asyncio.sleep(0.05)


@pytest.fixture
async def ready_server_id(owner: Owner, tmp_path: Path) -> str:
    """一台已经真实初始化好的服务器（初始化任务真的跑完，之后服务器关掉）。"""
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        return await _ready_server(owner, port)


@pytest.fixture
def parked(ready_server_id: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[uuid.UUID]]:
    """（服务器初始化之后）任务不真正运行：停在 running（本进程认为它在跑），记下启动了哪些任务。"""
    started: list[uuid.UUID] = []
    monkeypatch.setattr(SshJobRunner, "start", lambda self, job_id, *a, **k: started.append(job_id))
    monkeypatch.setattr(SshJobRunner, "running", lambda self, job_id: True)
    yield started


# ---------------------------------------------------------------- 正常路径


async def test_one_click_creates_and_deploys_with_safe_defaults(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id = await _ready_server(owner, port)
        server = await _server(owner, server_id)
        body = _body(server, description="说明", im_nickname="小甲", cpus=2, mem_limit_mb=4096)
        response = await _deploy(owner, body)
        assert response.status_code == 202, response.text
        assert "Idempotent-Replayed" not in response.headers
        data = response.json()
        instance = data["instance"]
        assert data["request_id"] == body["request_id"]
        assert instance["network_mode"] == "public"
        assert instance["scope"] == "read" and instance["max_role"] == "member"
        assert instance["im_nickname"] == "小甲" and instance["description"] == "说明"
        assert instance["limits"] == {"mem_limit_mb": 4096, "cpus": 2.0}
        for flag in ("project_admin", "im_media", "stop_others"):
            assert instance[flag]["enabled"] is False
        assert data["deployment"]["deployment"]["server_id"] == server_id
        job = data["deployment"]["job"]
        assert job["kind"] == "install" and job["status"] == "running"

        beat = asyncio.create_task(_online(owner, host, instance["id"]))
        try:
            state = await _finished(owner, instance["id"])
        finally:
            beat.cancel()
        assert state["job"]["status"] == "succeeded", state["job"]
        assert state["deployment"]["runtime_installed"] is True

        # 收据：同一个实例、同一个任务；accepted 只表示接纳，进度看 deployment.job
        receipt = await _receipt(owner, body["request_id"])
        assert receipt.status_code == 200, receipt.text
        got = receipt.json()
        assert got["state"] == "accepted" and got["error_code"] is None
        assert got["instance"]["id"] == instance["id"]
        assert got["deployment"]["job"]["id"] == job["id"]
        assert got["deployment"]["job"]["status"] == "succeeded"

        # 同一个请求号重发：返回同一个实例，不再建、不重新部署
        again = await _deploy(owner, body)
        assert again.status_code == 202, again.text
        assert again.headers["Idempotent-Replayed"] == "true"
        assert again.json()["instance"]["id"] == instance["id"]
        assert again.json()["deployment"]["job"]["id"] == job["id"]

    assert await _count(owner, "agent_instances") == 1
    assert await _count(owner, "agent_ssh_jobs", "kind = 'install'") == 1
    assert await _count(owner, "agent_deployments") == 1
    # 幂等记录只存 ID 与状态（不存请求原文、名称、密码）
    record = await run_sql(
        owner.database_url,
        "SELECT row_to_json(r)::text FROM workspace.idempotency_requests r"
        " WHERE idempotency_key = $1",
        uuid.UUID(body["request_id"]),
    )
    row = json.loads(record)
    assert row["status"] == "succeeded"
    assert set(row["context_json"]) == {
        "op",
        "server_id",
        "instance_id",
        "job_id",
        "request_id",
        "started_at",
    }
    assert row["response_json"] == {
        "body": {
            "request_id": body["request_id"],
            "instance_id": instance["id"],
            "job_id": job["id"],
        }
    }
    assert "小甲" not in record and "说明" not in record and PASSWORD not in record
    actions = await run_sql(
        owner.database_url,
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.%'",
    )
    for action in (
        "agents.instance.created",
        "agents.instance.limits_updated",
        "agents.ssh.install_started",
        "agents.ssh.install_succeeded",
    ):
        assert action in actions, actions


async def test_failed_job_keeps_the_instance_and_continues_with_deploy(
    owner: Owner, tmp_path: Path
) -> None:
    host = FakeHost(tmp_path)
    (host.state / "plugin_fail").touch()
    async with ssh_host(host) as (port, _):
        server_id = await _ready_server(owner, port)
        body = _body(await _server(owner, server_id))
        response = await _deploy(owner, body)
        assert response.status_code == 202, response.text
        instance_id = response.json()["instance"]["id"]
        state = await _finished(owner, instance_id)
        assert state["job"]["status"] == "failed"
        # 命令已接纳（accepted），任务失败看 deployment.job；实例保留
        receipt = (await _receipt(owner, body["request_id"])).json()
        assert receipt["state"] == "accepted"
        assert receipt["deployment"]["job"]["status"] == "failed"
        # 重发新建命令：不建新实例、不重启失败的任务
        again = await _deploy(owner, body)
        assert again.json()["instance"]["id"] == instance_id
        assert await _count(owner, "agent_ssh_jobs", "kind = 'install'") == 1
        # 修好之后用现有的部署接口继续同一个实例
        (host.state / "plugin_fail").unlink()
        beat = asyncio.create_task(_online(owner, host, instance_id))
        try:
            retried = await owner.client.post(
                f"/api/v1/agents/instances/{instance_id}/deploy",
                json={"server_id": server_id, "version": await owner.instance_version(instance_id)},
                headers=owner.headers(),
            )
            assert retried.status_code == 202, retried.text
            state = await _finished(owner, instance_id)
        finally:
            beat.cancel()
        assert state["job"]["status"] == "succeeded", state["job"]
    assert await _count(owner, "agent_instances") == 1
    assert await _count(owner, "agent_deployments") == 1


# ---------------------------------------------------------------- 前提不满足：什么都不建


async def test_preconditions_create_nothing(
    owner: Owner, tmp_path: Path, make_client: ClientFactory
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id = await _ready_server(owner, port)
        server = await _server(owner, server_id)
        failed: dict[str, str] = {}

        async def refused(body: dict[str, Any], status: int, code: str) -> None:
            response = await _deploy(owner, body)
            assert response.status_code == status, response.text
            assert response.json()["error"]["code"] == code
            failed[body["request_id"]] = code

        await refused(_body({**server, "version": server["version"] + 1}), 409, "VERSION_CONFLICT")
        await refused(_body({**server, "id": str(uuid.uuid4())}), 404, "NOT_FOUND")
        # 没保存密码 / 没初始化 / 同机有任务
        target = f"server_id = '{server_id}'"
        await run_sql(
            owner.database_url,
            f"UPDATE workspace.agent_ssh_targets SET login_password_ref = NULL WHERE {target}",  # noqa: S608
        )
        await refused(_body(server), 409, "AGENT_SERVER_CREDENTIAL_REQUIRED")
        current = await _server(owner, server_id)
        supplied = await _call(
            owner,
            "PUT",
            f"/api/v1/agents/servers/{server_id}/credentials",
            json={"version": current["version"], "login_password": PASSWORD},
        )
        assert supplied.status_code == 200
        server = await _server(owner, server_id)
        await run_sql(
            owner.database_url,
            f"UPDATE workspace.agent_ssh_targets SET prepared_at = NULL WHERE {target}",  # noqa: S608
        )
        await run_sql(
            owner.database_url,
            "UPDATE workspace.agent_ssh_jobs SET status = 'failed', finished_at = now()"
            " WHERE kind = 'prepare'",
        )
        await refused(_body(server), 409, "AGENT_SERVER_NOT_INITIALIZED")
        await run_sql(
            owner.database_url,
            f"UPDATE workspace.agent_ssh_targets SET prepared_at = now() WHERE {target}",  # noqa: S608
        )
        # 总开关关闭
        await run_sql(
            owner.database_url,
            "INSERT INTO workspace.agent_settings (workspace_id, agents_enabled, version)"
            " VALUES ($1, false, 2)",
            uuid.UUID(owner.workspace),
        )
        await refused(_body(server), 409, "AGENT_DISABLED")
        await run_sql(
            owner.database_url, "UPDATE workspace.agent_settings SET agents_enabled = true"
        )
        # 自动识别的服务器
        auto = uuid.uuid4()
        await run_sql(
            owner.database_url,
            "INSERT INTO workspace.agent_servers (id, workspace_id, name, kind)"
            " VALUES ($1, $2, 'auto', 'auto')",
            auto,
            uuid.UUID(owner.workspace),
        )
        await refused(_body({"id": str(auto), "version": 1}), 409, "AGENT_SERVER_NOT_SSH")
        # 不接受地址、密码、镜像、目录、网络模式、权限开关
        for extra in (
            {"login_password": "x"},
            {"host": "10.0.0.1"},
            {"image": "openclaw/openclaw:latest"},
            {"install_dir": "/srv/x"},
            {"network_mode": "intranet"},
            {"scope": "read_write"},
            {"project_admin": True},
        ):
            response = await _deploy(owner, _body(server, **extra))
            assert response.status_code == 422, (extra, response.text)
        # request_id 与 Idempotency-Key 不一致；没带 Idempotency-Key
        mismatch = await _deploy(
            owner, _body(server), headers={"Idempotency-Key": str(uuid.uuid4())}
        )
        assert mismatch.status_code == 422
        missing = await _call(owner, "POST", "/api/v1/agents/deployments", json=_body(server))
        assert missing.status_code == 422
        # 资源上限范围
        for limits in ({"cpus": 0.05}, {"mem_limit_mb": 128}, {"cpus": 300}):
            response = await _deploy(owner, _body(server, **limits))
            assert response.status_code == 422, limits

        # 被拒绝的请求：收据是 failed（带错误码），重发得到同样的错误
        for request_id, code in failed.items():
            got = (await _receipt(owner, request_id)).json()
            assert got["state"] == "failed" and got["error_code"] == code, got
            assert got["instance"] is None
        first = next(iter(failed))
        replay = await _call(
            owner,
            "GET",
            f"/api/v1/agents/deployments/requests/{first}",
        )
        assert replay.status_code == 200
        assert host.logins.count("password") == 2  # 只有初始化时的两次登录

    # 外网接入入口没有配置（另一个应用实例的设置）
    async with make_client(**{**SETTINGS, "agent_public_url": ""}) as other:
        login = await other.post(
            "/api/v1/auth/login",
            json={"email": OWNER["email"], "password": OWNER["password"]},
        )
        assert login.status_code == 200
        response = await other.post(
            "/api/v1/agents/deployments",
            json=_body(await _server(owner, server_id), request_id=(rid := str(uuid.uuid4()))),
            headers={"X-Workspace-Id": owner.workspace, "Idempotency-Key": rid, **csrf(other)},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "AGENT_INGRESS_NOT_CONFIGURED"

    assert await _count(owner, "agent_instances") == 0
    assert await _count(owner, "agent_deployments") == 0
    assert await _count(owner, "agent_ssh_jobs", "kind = 'install'") == 0
    assert await _count(owner, "audit_logs", "action = 'agents.instance.created'") == 0


async def test_only_the_owner_in_its_own_workspace(
    owner: Owner, tmp_path: Path, make_client: ClientFactory
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        server_id = await _ready_server(owner, port)
        server = await _server(owner, server_id)
    creds = {"email": "admin@example.com", "password": "admin password 123456"}
    user = await add_user(owner.database_url, creds["email"], creds["password"])
    await add_member(owner.database_url, uuid.UUID(owner.workspace), user, "admin")
    other_ws = await add_workspace(owner.database_url, user, "另一个空间")
    async with make_client(**SETTINGS) as other:
        assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
        body = _body(server)
        as_admin = await other.post(
            "/api/v1/agents/deployments",
            json=body,
            headers={
                "X-Workspace-Id": owner.workspace,
                "Idempotency-Key": body["request_id"],
                **csrf(other),
            },
        )
        assert as_admin.status_code == 403
        # 自己是 owner 的另一个工作空间：这台服务器看不到
        elsewhere = await other.post(
            "/api/v1/agents/deployments",
            json=body,
            headers={
                "X-Workspace-Id": str(other_ws),
                "Idempotency-Key": body["request_id"],
                **csrf(other),
            },
        )
        assert elsewhere.status_code == 404, elsewhere.text
    assert await _count(owner, "agent_instances") == 0


# ---------------------------------------------------------------- 并发


async def test_concurrent_same_request_creates_one_instance(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID]
) -> None:
    server_id = ready_server_id
    body = _body(await _server(owner, server_id))
    responses = await asyncio.gather(*(_deploy(owner, body) for _ in range(4)))
    codes = sorted(r.status_code for r in responses)
    created = [r for r in responses if r.status_code == 202]
    assert created, [r.text for r in responses]
    assert all(c in (202, 409) for c in codes), [r.text for r in responses]
    for response in responses:
        if response.status_code == 409:
            assert response.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert len({r.json()["instance"]["id"] for r in created}) == 1
    assert sum("Idempotent-Replayed" not in r.headers for r in created) == 1
    assert await _count(owner, "agent_instances") == 1
    assert await _count(owner, "agent_ssh_jobs", "kind = 'install'") == 1
    assert len(parked) == 1
    got = (await _receipt(owner, body["request_id"])).json()
    assert got["state"] == "accepted"


async def test_same_request_id_with_a_different_body_conflicts(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID]
) -> None:
    server_id = ready_server_id
    body = _body(await _server(owner, server_id))
    assert (await _deploy(owner, body)).status_code == 202
    changed = await _deploy(owner, {**body, "name": "乙"})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert await _count(owner, "agent_instances") == 1


async def test_concurrent_requests_on_one_server_leave_no_extra_instance(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID]
) -> None:
    server_id = ready_server_id
    server = await _server(owner, server_id)
    first, second = _body(server), _body(server, name="乙")
    responses = await asyncio.gather(_deploy(owner, first), _deploy(owner, second))
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 409], [r.text for r in responses]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
    assert await _count(owner, "agent_instances") == 1
    assert await _count(owner, "agent_ssh_jobs", "kind = 'install'") == 1
    lost = first if responses[0].status_code == 409 else second
    got = (await _receipt(owner, lost["request_id"])).json()
    assert got["state"] == "failed" and got["error_code"] == "AGENT_SSH_JOB_RUNNING"
    runner = owner.app.state.ssh_jobs
    assert runner._reserved == set(parked), "失败的那一个已经取消登记"


async def test_database_mutex_rolls_back_the_loser_and_releases_its_reservation(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """进程内的忙检查被绕过（例如另一个 API 进程）：数据库的部分唯一索引兜底，
    输的一方整个事务回滚（没有实例、部署记录），进程内的登记也取消。"""
    server_id = ready_server_id
    server = await _server(owner, server_id)
    first = await _deploy(owner, _body(server))
    assert first.status_code == 202
    real_busy = AgentServerService._busy

    async def not_busy(self: AgentServerService, target: Any) -> bool:
        await real_busy(self, target)
        return False

    monkeypatch.setattr(AgentServerService, "_busy", not_busy)
    body = _body(await _server(owner, server_id), name="乙")
    second = await _deploy(owner, body)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
    assert await _count(owner, "agent_instances") == 1
    assert await _count(owner, "agent_deployments") == 1
    assert owner.app.state.ssh_jobs._reserved == set(parked)
    assert (await _receipt(owner, body["request_id"])).json()["state"] == "failed"


# ---------------------------------------------------------------- 崩溃与响应丢失


async def test_crash_before_commit_leaves_nothing_and_the_same_request_runs_once(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    server_id = ready_server_id
    body = _body(await _server(owner, server_id))

    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("进程在提交前退出")

    with monkeypatch.context() as patch:
        patch.setattr(idempotency, "succeed", crash)
        crashed = await _deploy(owner, body)
    assert crashed.status_code == 500
    assert await _count(owner, "agent_instances") == 0
    assert await _count(owner, "agent_deployments") == 0
    assert await _count(owner, "agent_ssh_jobs", "kind = 'install'") == 0
    assert owner.app.state.ssh_jobs._reserved == set()
    # 刚中断时收据是 processing；超过“处理中”的时限后是 interrupted
    assert (await _receipt(owner, body["request_id"])).json()["state"] == "processing"
    busy = await _deploy(owner, body)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    await run_sql(
        owner.database_url,
        "UPDATE workspace.idempotency_requests SET updated_at = now() - $1::interval"
        " WHERE idempotency_key = $2",
        idempotency.STALE_PROCESSING + timedelta(seconds=5),
        uuid.UUID(body["request_id"]),
    )
    got = (await _receipt(owner, body["request_id"])).json()
    assert got["state"] == "interrupted" and got["instance"] is None
    # 同一个请求号重发：之前确实什么都没建，执行一次
    retried = await _deploy(owner, body)
    assert retried.status_code == 202, retried.text
    assert "Idempotent-Replayed" not in retried.headers
    assert await _count(owner, "agent_instances") == 1
    assert len(parked) == 1


async def test_response_lost_after_commit_is_recovered_from_the_receipt(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    server_id = ready_server_id
    body = _body(await _server(owner, server_id))

    async def lost(*args: Any, **kwargs: Any) -> None:
        raise ConnectionResetError("响应没送到浏览器")

    with monkeypatch.context() as patch:
        patch.setattr(agent_deployments.AgentDeploymentService, "_out", lost)
        failed = await _deploy(owner, body)
    assert failed.status_code == 500
    # 已经提交：收据找到唯一的实例与任务
    got = (await _receipt(owner, body["request_id"])).json()
    assert got["state"] == "accepted"
    instance_id = got["instance"]["id"]
    assert got["deployment"]["job"]["status"] == "running"
    again = await _deploy(owner, body)
    assert again.status_code == 202 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["instance"]["id"] == instance_id
    assert await _count(owner, "agent_instances") == 1
    assert len(parked) == 1, "重发不再启动任务"


async def test_receipt_is_only_for_the_original_actor_and_workspace(
    owner: Owner, ready_server_id: str, parked: list[uuid.UUID], make_client: ClientFactory
) -> None:
    server_id = ready_server_id
    # 别的写接口用过的键（替换密码，带 Idempotency-Key）：不是部署请求，收据 404
    server = await _server(owner, server_id)
    other_key = str(uuid.uuid4())
    replaced = await _call(
        owner,
        "PUT",
        f"/api/v1/agents/servers/{server_id}/credentials",
        json={"version": server["version"], "login_password": PASSWORD},
        headers={"Idempotency-Key": other_key},
    )
    assert replaced.status_code == 200, replaced.text
    assert (await _receipt(owner, other_key)).status_code == 404
    body = _body(await _server(owner, server_id))
    assert (await _deploy(owner, body)).status_code == 202
    # 不存在的请求号：404
    assert (await _receipt(owner, str(uuid.uuid4()))).status_code == 404
    # 同一个工作空间里的另一个 owner、自己的另一个工作空间：都查不到
    creds = {"email": "owner2@example.com", "password": "owner2 password 123456"}
    user = await add_user(owner.database_url, creds["email"], creds["password"])
    await add_member(owner.database_url, uuid.UUID(owner.workspace), user, "owner")
    async with make_client(**SETTINGS) as other:
        assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
        response = await other.get(
            f"/api/v1/agents/deployments/requests/{body['request_id']}",
            headers={"X-Workspace-Id": owner.workspace},
        )
        assert response.status_code == 404
    owner_id = await run_sql(
        owner.database_url,
        "SELECT owner_id FROM workspace.workspaces WHERE id = $1",
        uuid.UUID(owner.workspace),
    )
    mine_elsewhere = await add_workspace(owner.database_url, owner_id, "我的另一个空间")
    response = await owner.client.get(
        f"/api/v1/agents/deployments/requests/{body['request_id']}",
        headers={"X-Workspace-Id": str(mine_elsewhere)},
    )
    assert response.status_code == 404
