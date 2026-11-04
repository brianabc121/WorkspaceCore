"""远程运维动作（T-0114，docs/v2/AGENT_OPS_PLAN.md 第 6.1 节）。

目标服务器沿用 SSH 自动部署测试里的进程内 asyncssh 服务器与假的 docker
（tests/test_agent_ssh_install.py）：先真的走一遍 SSH 部署，再执行运维动作。
覆盖：写死的命令、输出脱敏、二次认证、同一实例只跑一个、与部署互斥、超时、
权限（owner / admin）、审计不记输出、7 天清理、未部署的实例不可用。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.domain import agent_ops
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql
from tests.test_agent_ssh_install import (
    SETTINGS,
    FakeHost,
    Owner,
    _install_ok,
    _token,
    fast_online,  # noqa: F401 - pytest fixture（autouse：部署时不等 5 秒）
    ssh_host,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def owner(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Owner]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Owner(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


def ops_url(instance_id: str, suffix: str = "") -> str:
    return f"/api/v1/agents/instances/{instance_id}/ops{suffix}"


async def run(owner: Owner, instance_id: str, body: dict[str, Any]) -> httpx.Response:
    return await owner.client.post(ops_url(instance_id), json=body, headers=owner.headers())


async def finished(owner: Owner, instance_id: str, action_id: str) -> dict[str, Any]:
    for _ in range(400):
        response = await owner.client.get(
            ops_url(instance_id, f"/{action_id}"), headers=owner.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        if data["status"] != "running":
            return data
        await asyncio.sleep(0.05)
    raise AssertionError("运维动作 20 秒内没有结束")


async def run_ok(owner: Owner, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await run(owner, instance_id, body)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "running"
    return await finished(owner, instance_id, response.json()["id"])


def slow_docker(host: FakeHost) -> None:
    """$STATE/slow 存在时假的 docker 先睡 2 秒（模拟一个还没结束的动作）。"""
    path = host.bin / "docker"
    path.write_text(
        path.read_text().replace(
            'echo "docker $*" >> "$FAKE_LOG"\n',
            'echo "docker $*" >> "$FAKE_LOG"\nif [ -f "$STATE/slow" ]; then sleep 2; fi\n',
            1,
        )
    )


async def deployed(owner: Owner, host: FakeHost, port: int) -> str:
    instance_id, version = await owner.prepare(port)
    await _install_ok(owner, host, instance_id, version)
    return instance_id


async def test_actions_run_fixed_commands_and_redact_output(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id = await deployed(owner, host, port)
        state = (await owner.client.get(ops_url(instance_id), headers=owner.headers())).json()
        assert state["available"] is True and state["reason"] is None and state["items"] == []

        status = await run_ok(owner, instance_id, {"action": "status"})
        assert status["status"] == "succeeded" and status["exit_code"] == 0
        assert "docker compose ps --all" in status["output"]
        assert "openclaw   running" in status["output"] and "uptime" in status["output"]
        assert status["expires_at"] > status["started_at"]

        # 日志里有实例凭证：保存前就替换掉，接口、库、审计里都没有原文
        token = _token(host)
        logs = await run_ok(owner, instance_id, {"action": "logs"})
        assert logs["service"] == "openclaw" and "--tail 200 openclaw" in logs["output"]
        assert "mwa_***" in logs["output"] and token not in logs["output"]
        stored = await run_sql(
            owner.database_url,
            "SELECT count(*) FROM (SELECT row_to_json(a)::text AS t"
            " FROM workspace.agent_ops_actions a"
            " UNION ALL SELECT row_to_json(l)::text FROM workspace.audit_logs l) rows"
            " WHERE strpos(t, $1) > 0",
            token,
        )
        assert stored == 0
        tunnel = await run_ok(owner, instance_id, {"action": "logs", "service": "tunnel"})
        assert "--tail 200 tunnel" in tunnel["output"]

        report = await run_ok(owner, instance_id, {"action": "report"})
        assert "usage-report.mjs" in report["output"] and "host-report.mjs" in report["output"]
        calls = "\n".join(host.calls())
        assert "docker compose logs --no-color --tail 200 openclaw" in calls

        # 列表不带输出，最近的在前；审计记下动作与退出码，不记输出
        state = (await owner.client.get(ops_url(instance_id), headers=owner.headers())).json()
        assert [i["action"] for i in state["items"]] == ["report", "logs", "logs", "status"]
        assert all(i["output"] is None for i in state["items"])
        finished_audits = await run_sql(
            owner.database_url,
            "SELECT count(*) FROM workspace.audit_logs WHERE action = 'agents.ops.finished'"
            " AND metadata_redacted_json->>'exit_code' = '0'",
        )
        assert finished_audits == 4
        outputs_in_audit = await run_sql(
            owner.database_url,
            "SELECT count(*) FROM workspace.audit_logs WHERE action LIKE 'agents.ops.%'"
            " AND metadata_redacted_json ? 'output'",
        )
        assert outputs_in_audit == 0


async def test_dangerous_actions_need_the_password(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id = await deployed(owner, host, port)
        for action in ("restart", "up", "prune"):
            missing = await run(owner, instance_id, {"action": action})
            assert missing.status_code == 403
            assert missing.json()["error"]["code"] == "STEP_UP_REQUIRED"
        wrong = await run(owner, instance_id, {"action": "restart", "password": "not my password"})
        assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "STEP_UP_FAILED"
        done = await run_ok(
            owner, instance_id, {"action": "restart", "password": OWNER["password"]}
        )
        assert done["status"] == "succeeded"
        assert "docker compose restart openclaw" in "\n".join(host.calls())
        pruned = await run_ok(
            owner, instance_id, {"action": "prune", "password": OWNER["password"]}
        )
        assert pruned["status"] == "succeeded" and "docker image prune -f" in pruned["output"]
        # 服务名只用于查看日志；不接受命令文本
        bad = await run(owner, instance_id, {"action": "status", "service": "openclaw"})
        assert bad.status_code == 422
        bad = await run(owner, instance_id, {"action": "logs", "service": "openclaw; rm -rf /"})
        assert bad.status_code == 422
        bad = await run(owner, instance_id, {"action": "shell", "command": "id"})
        assert bad.status_code == 422


async def test_one_action_at_a_time_and_not_during_a_deploy(
    owner: Owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id = await deployed(owner, host, port)
        slow_docker(host)
        (host.state / "slow").touch()
        first = await run(owner, instance_id, {"action": "status"})
        assert first.status_code == 202
        second = await run(owner, instance_id, {"action": "logs"})
        assert second.status_code == 409 and second.json()["error"]["code"] == "AGENT_OPS_RUNNING"
        state = (await owner.client.get(ops_url(instance_id), headers=owner.headers())).json()
        assert state["running"]["id"] == first.json()["id"]
        # 同一条 SSH 通道：动作进行中时不能开始部署
        ssh_state = await owner.state(instance_id)
        install = await owner.install(instance_id, ssh_state["target"]["version"], None)
        assert install.status_code == 409
        assert install.json()["error"]["code"] == "AGENT_SSH_JOB_RUNNING"
        await finished(owner, instance_id, first.json()["id"])

        # 超时：记为失败、写明原因
        monkeypatch.setattr(agent_ops, "ACTION_TIMEOUT", 0.5)
        timed = await run_ok(owner, instance_id, {"action": "logs"})
        assert timed["status"] == "failed" and timed["error_code"] == "timeout"
        (host.state / "slow").unlink()
        await asyncio.sleep(2.2)  # 等假服务器上那条还在睡的命令结束，再关掉服务器


async def test_admin_can_use_member_cannot(
    owner: Owner, tmp_path: Path, make_client: ClientFactory
) -> None:
    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id = await deployed(owner, host, port)
        for role in ("admin", "member"):
            creds = {"email": f"{role}@example.com", "password": f"{role} password 123456"}
            user = await add_user(owner.database_url, creds["email"], creds["password"])
            await add_member(owner.database_url, uuid.UUID(owner.workspace), user, role)
            async with make_client(**SETTINGS) as other:
                assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
                headers = {"X-Workspace-Id": owner.workspace, **csrf(other)}
                listed = await other.get(ops_url(instance_id), headers=headers)
                posted = await other.post(
                    ops_url(instance_id),
                    json={"action": "restart", "password": creds["password"]},
                    headers=headers,
                )
                if role == "admin":
                    assert listed.status_code == 200 and posted.status_code == 202, posted.text
                    action_id = posted.json()["id"]
                    for _ in range(400):
                        got = await other.get(
                            ops_url(instance_id, f"/{action_id}"), headers=headers
                        )
                        if got.json()["status"] != "running":
                            break
                        await asyncio.sleep(0.05)
                    assert got.json()["status"] == "succeeded"
                else:
                    assert listed.status_code == 403 and posted.status_code == 403


async def test_unavailable_without_ssh_deploy_and_purge(owner: Owner, tmp_path: Path) -> None:
    manual = await owner.instance()
    state = (await owner.client.get(ops_url(manual), headers=owner.headers())).json()
    assert state == {"available": False, "reason": "not_deployed", "running": None, "items": []}
    refused = await run(owner, manual, {"action": "status"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "AGENT_OPS_UNAVAILABLE"
    assert refused.json()["error"]["details"]["reason"] == "not_deployed"

    host = FakeHost(tmp_path)
    async with ssh_host(host) as (port, _):
        instance_id = await deployed(owner, host, port)
        done = await run_ok(owner, instance_id, {"action": "status"})
    await run_sql(
        owner.database_url,
        "UPDATE workspace.agent_ops_actions SET expires_at = now() - interval '1 minute'"
        " WHERE id = $1",
        uuid.UUID(done["id"]),
    )
    gone = await owner.client.get(ops_url(instance_id, f"/{done['id']}"), headers=owner.headers())
    assert gone.status_code == 404
    async with owner.app.state.sessionmaker() as db:
        assert await agent_ops.purge(db) == 1
        await db.commit()
    audits = await run_sql(
        owner.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action LIKE 'agents.ops.%'",
    )
    assert audits == 2  # 记录删除后审计仍在


async def test_restart_interrupts_running_actions(owner: Owner) -> None:
    instance_id = await owner.instance()
    now = datetime.now(UTC)
    await run_sql(
        owner.database_url,
        "INSERT INTO workspace.agent_ops_actions (workspace_id, instance_id, action, status,"
        " started_at, expires_at) VALUES ($1, $2, 'status', 'running', $3, $4)",
        uuid.UUID(owner.workspace),
        uuid.UUID(instance_id),
        now,
        now + timedelta(days=7),
    )
    runner = owner.app.state.ssh_jobs
    assert await runner.recover() == 0  # 返回的是安装任务的数量
    status = await run_sql(
        owner.database_url,
        "SELECT status FROM workspace.agent_ops_actions WHERE instance_id = $1",
        uuid.UUID(instance_id),
    )
    assert status == "interrupted"


def test_redact_removes_tokens_keys_and_private_keys() -> None:
    text = (
        "Authorization: Bearer abc.def.ghi\n"
        "curl -H 'authorization: token ghp_123456' x\n"
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnop\n"
        '{"password": "hunter22", "apiKey": "zzz"}\n'
        "MYWS_INSTANCE_TOKEN=mwa_secretvalue\n"
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
        "-----BEGIN PRIVATE KEY-----\ntruncated"
    )
    out = agent_ops.redact(text)
    for secret in (
        "abc.def.ghi",
        "ghp_123456",
        "sk-proj-abcdefghijklmnop",
        "hunter22",
        "zzz",
        "mwa_secretvalue",
        "eyJhbGciOiJIUzI1NiJ9",
        "AAAA",
        "truncated",
    ):
        assert secret not in out, secret
    assert agent_ops.redact("openclaw   running") == "openclaw   running"


def test_output_keeps_the_tail_within_the_limit() -> None:
    output = agent_ops.Output()
    output.write("前面" * 40_000)
    output.write("最后一行")
    text, truncated = output.result()
    assert truncated and len(text.encode()) <= agent_ops.OUTPUT_LIMIT
    assert text.endswith("最后一行\n")


def test_commands_are_fixed_and_quoted() -> None:
    directory = "/home/de ploy/myworkspace-openclaw"
    for action in ("status", "restart", "up", "prune", "report"):
        for command in agent_ops.commands(action, None, directory):
            assert "de ploy" not in command or "'/home/de ploy/myworkspace-openclaw'" in command
    assert agent_ops.commands("logs", "image-proxy", directory)[0].endswith(
        "logs --no-color --tail 200 image-proxy"
    )
    with pytest.raises(ValueError):
        agent_ops.commands("logs", "openclaw; id", directory)
    with pytest.raises(ValueError):
        agent_ops.commands("shell", None, directory)
    assert agent_ops.timeout_for("up") == 180 and agent_ops.timeout_for("restart") == 60
