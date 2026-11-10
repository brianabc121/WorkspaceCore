"""一台服务器多个 OpenClaw 实例（T-0145）：初始化服务器、部署两个实例、只卸载自己、移除与手动归属。

T-0179 起初始化、部署、卸载都用服务器上保存的登录密码（不装公钥）。

沿用 test_agent_ssh_install.py 的假服务器（进程内 asyncssh + 假的 docker / sudo / crontab）。
"""

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest

from app import agent_provisioning as prov
from tests import test_agent_ssh_install as base
from tests.helpers import run_sql
from tests.test_agent_ssh_install import PASSWORD, FakeHost, Owner, _heartbeat, ssh_host

pytestmark = pytest.mark.integration

# 沿用部署测试的 fixture：owner（登录好的 owner 客户端）、fast_online（等实例上线不等太久）
owner = base.owner
fast_online = base.fast_online


async def _json(owner: Owner, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    response = await owner.client.request(method, path, headers=owner.headers(), **kwargs)
    return response.status_code, response.json() if response.content else None


async def _server_job(owner: Owner, server_id: str) -> dict[str, Any]:
    for _ in range(600):
        _, detail = await _json(owner, "GET", f"/api/v1/agents/servers/{server_id}")
        job = detail["job"]
        if job and job["status"] != "running":
            return dict(detail)
        await asyncio.sleep(0.05)
    raise AssertionError("准备服务器 30 秒内没有结束")


async def _online(owner: Owner, host: FakeHost, instance_id: str) -> None:
    """模拟这个实例新部署的 OpenClaw 用新凭证连上工作台（它自己的目录里的凭证）。"""
    env = host.home / prov.INSTALL_ROOT_NAME / instance_id.replace("-", "")[:12] / "myworkspace.env"
    while True:
        if env.exists() and (host.state / "up").exists():
            token = next(
                (
                    line.split("=", 1)[1]
                    for line in env.read_text().splitlines()
                    if line.startswith("MYWS_INSTANCE_TOKEN=")
                ),
                None,
            )
            if token:
                await run_sql(
                    owner.database_url,
                    "UPDATE workspace.agent_credentials SET last_used_at = now()"
                    " WHERE token_hash = $1",
                    hashlib.sha256(token.encode()).hexdigest(),
                )
        await asyncio.sleep(0.05)


async def _deploy(
    owner: Owner, host: FakeHost, instance_id: str, server_id: str, *, password: str | None = None
) -> dict[str, Any]:
    version = await owner.instance_version(instance_id)
    body: dict[str, Any] = {"server_id": server_id, "version": version}
    if password:
        body["password"] = password
    beat = asyncio.create_task(_online(owner, host, instance_id))
    try:
        code, state = await _json(
            owner, "POST", f"/api/v1/agents/instances/{instance_id}/deploy", json=body
        )
        assert code == 202, state
        for _ in range(600):
            _, state = await _json(
                owner, "GET", f"/api/v1/agents/instances/{instance_id}/deployment"
            )
            if state["job"] and state["job"]["status"] != "running":
                break
            await asyncio.sleep(0.05)
    finally:
        beat.cancel()
    assert state["job"]["status"] == "succeeded", state["job"]
    return dict(state)


async def _confirmed_server(owner: Owner, port: int) -> dict[str, Any]:
    server = await owner.add_server(port)
    code, detail = await _json(
        owner,
        "POST",
        f"/api/v1/agents/servers/{server['id']}/confirm",
        json={"version": server["version"], "fingerprint": server["ssh"]["host_key_fingerprint"]},
    )
    assert code == 200, detail
    return dict(detail["server"])


async def test_prepare_once_then_deploy_two_instances(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    (host.state / "crontab").write_text("0 1 * * * echo keep-me\n")
    async with ssh_host(host) as (port, _):
        server = await _confirmed_server(owner, port)
        first = await owner.instance()
        # 初始化不再接受密码（T-0178：用添加服务器时保存的密码）
        code, refused = await _json(
            owner,
            "POST",
            f"/api/v1/agents/servers/{server['id']}/prepare",
            json={"version": server["version"], "password": PASSWORD},
        )
        assert code == 422 and refused["error"]["code"] == "VALIDATION_FAILED"
        assert server["initialization_status"] == "not_initialized"
        assert server["deploy_block_reason"] == "not_initialized"

        # 初始化服务器：用保存的密码；不装公钥、不建实例目录
        code, started = await _json(
            owner,
            "POST",
            f"/api/v1/agents/servers/{server['id']}/prepare",
            json={"version": server["version"]},
        )
        assert code == 202, started
        detail = await _server_job(owner, server["id"])
        assert detail["job"]["kind"] == "prepare" and detail["job"]["status"] == "succeeded"
        assert [s["key"] for s in detail["job"]["steps"]] == list(prov.PREPARE_STEPS)
        ssh_info = detail["server"]["ssh"]
        assert ssh_info["client_key_installed"] is False and ssh_info["prepared_at"]
        assert detail["server"]["initialization_status"] == "ready"
        assert detail["server"]["deployable"] is True
        # 装了 Docker：登录一次、重新登录一次（docker 组生效），都是密码
        assert host.logins == ["password", "password"] and host.authorized() == []
        assert list((host.home / prov.INSTALL_ROOT_NAME).iterdir()) == []

        # 两个实例部署到同一台服务器：各自用保存的密码登录一次
        second = await owner.instance()
        await _deploy(owner, host, first, server["id"])
        state = await _deploy(owner, host, second, server["id"])
        assert host.logins == ["password"] * 4, "全程只用密码，没有公钥登录"
        assert state["deployment"]["server_id"] == server["id"]
        root = host.home / prov.INSTALL_ROOT_NAME
        dirs = sorted(p.name for p in root.iterdir())
        assert dirs == sorted(i.replace("-", "")[:12] for i in (first, second))
        cron = (host.state / "crontab").read_text().splitlines()
        assert cron[0] == "0 1 * * * echo keep-me" and len(cron) == 15
        assert host.authorized() == [], "不再给服务器装工作台的公钥"
        for instance_id in (first, second):
            code, shown = await _json(owner, "GET", f"/api/v1/agents/instances/{instance_id}")
            assert shown["server_id"] == server["id"] and shown["server_manual"] is True

        # 服务器详情：两个智能体，都是部署的
        _, detail = await _json(owner, "GET", f"/api/v1/agents/servers/{server['id']}")
        rows = {row["instance_id"]: row for row in detail["agents"]}
        assert set(rows) == {first, second} and all(r["deployed"] for r in rows.values())
        assert all(r["deployment_state"] == "deployed" for r in rows.values())
        assert all(r["latest_job"]["kind"] == "install" for r in rows.values())
        assert rows[first]["deployment_project"] == (
            "myworkspace-openclaw-" + first.replace("-", "")[:12]
        )
        assert detail["server"]["instances_total"] == 2
        assert detail["server"]["managed_deployments"] == 2
        # 部署成功写了 runtime_installed（T-0180）：成功数是确定的
        assert detail["server"]["successful_deployments"] == 2
        assert detail["server"]["deployment_counts_partial"] is False
        assert detail["server"]["has_unlimited_limits"] is True

        # 已经部署在这台上：不能再部署到另一台
        code, created = await _json(
            owner,
            "POST",
            "/api/v1/agents/servers",
            json={
                "name": "另一台",
                "host": "127.0.0.1",
                "port": port,
                "username": "ops",
                "login_password": PASSWORD,
            },
        )
        assert code == 201, created
        code, elsewhere = await _json(
            owner,
            "POST",
            f"/api/v1/agents/instances/{first}/deploy",
            json={
                "server_id": created["server"]["id"],
                "version": await owner.instance_version(first),
            },
        )
        assert code == 409 and elsewhere["error"]["code"] == "AGENT_DEPLOYED_ELSEWHERE"

        # 卸载第一个并删除目录：第二个不受影响
        beat = asyncio.create_task(_heartbeat(owner, host))
        try:
            code, _ = await _json(
                owner,
                "POST",
                f"/api/v1/agents/instances/{first}/undeploy",
                json={"version": await owner.instance_version(first), "remove_files": True},
            )
            assert code == 202
            for _ in range(600):
                _, state = await _json(owner, "GET", f"/api/v1/agents/instances/{first}/deployment")
                if state["job"]["kind"] == "uninstall" and state["job"]["status"] != "running":
                    break
                await asyncio.sleep(0.05)
        finally:
            beat.cancel()
        assert state["job"]["status"] == "succeeded", state["job"]
        assert state["deployment"] is None and state["server"] is None
        assert sorted(p.name for p in root.iterdir()) == [second.replace("-", "")[:12]]
        cron = (host.state / "crontab").read_text().splitlines()
        assert len(cron) == 8 and all(first.replace("-", "")[:12] not in c for c in cron)

        # 还有部署的实例：不能移除
        code, blocked = await _json(
            owner,
            "DELETE",
            f"/api/v1/agents/servers/{server['id']}",
            params={
                "version": (await _json(owner, "GET", f"/api/v1/agents/servers/{server['id']}"))[1][
                    "server"
                ]["version"]
            },
        )
        assert code == 409 and blocked["error"]["code"] == "AGENT_SERVER_HAS_DEPLOYMENTS"
    deployments = await run_sql(
        owner.database_url, "SELECT count(*) FROM workspace.agent_deployments"
    )
    assert deployments == 1


async def test_manual_assignment_and_auto_server_removal(owner: Owner) -> None:
    instance = await owner.instance()
    auto = uuid.uuid4()
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_servers (id, workspace_id, name, kind)"
        " VALUES ($1, $2, '自动识别的服务器', 'auto')",
        auto,
        uuid.UUID(owner.workspace),
    )
    _, listed = await _json(owner, "GET", "/api/v1/agents/servers")
    assert [u["id"] for u in listed["unassigned"]] == [instance]
    code, state = await _json(
        owner,
        "PUT",
        f"/api/v1/agents/instances/{instance}/server",
        json={"server_id": str(auto), "version": await owner.instance_version(instance)},
    )
    assert code == 200, state
    assert state["server"]["id"] == str(auto) and state["deployment"] is None
    _, shown = await _json(owner, "GET", f"/api/v1/agents/instances/{instance}")
    assert shown["server_manual"] is True
    _, listed = await _json(owner, "GET", "/api/v1/agents/servers")
    assert listed["unassigned"] == [] and listed["items"][0]["instances_total"] == 1
    # 自动识别的服务器：没有 SSH，初始化“不适用”，不能选它部署，也不计入待初始化
    auto_out = listed["items"][0]
    assert auto_out["initialization_status"] is None and auto_out["deployable"] is False
    assert auto_out["deploy_block_reason"] == "not_ssh" and auto_out["ssh"] is None
    assert listed["summary"]["server_count"] == 1
    assert listed["summary"]["not_initialized_count"] == 0
    assert listed["summary"]["needs_credentials_count"] == 0
    # 别的工作空间的服务器：404
    code, _ = await _json(
        owner,
        "PUT",
        f"/api/v1/agents/instances/{instance}/server",
        json={"server_id": str(uuid.uuid4()), "version": await owner.instance_version(instance)},
    )
    assert code == 404
    # 自动识别的服务器可以直接移除：实例改为未归属
    code, _ = await _json(owner, "DELETE", f"/api/v1/agents/servers/{auto}", params={"version": 1})
    assert code == 204
    _, shown = await _json(owner, "GET", f"/api/v1/agents/instances/{instance}")
    assert shown["server_id"] is None and shown["server_manual"] is False
    # 改回自动识别
    code, state = await _json(
        owner,
        "PUT",
        f"/api/v1/agents/instances/{instance}/server",
        json={"server_id": None, "version": await owner.instance_version(instance)},
    )
    assert code == 200 and state["server"] is None
    actions = await run_sql(
        owner.database_url,
        "SELECT count(*) FROM workspace.audit_logs"
        " WHERE action IN ('agents.instance.server_assigned', 'agents.server.removed')",
    )
    assert actions == 3
