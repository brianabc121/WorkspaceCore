"""Webhook 接收与手动同步接口的集成测试（T-0021，S3-05 / S3-06）：真实 PostgreSQL / Redis。

覆盖 Sprint 3 契约第 9 节与第 4 节的 sync 接口：签名校验（含集成不存在 / 已停用）、
请求体上限、非 JSON、重复送达只记录一次、payload 只保存最小字段、投递处理作业、
测试连接通过后投递 Webhook 注册、手动同步的加锁与 409。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from app.domain import sync_lock
from app.domain.webhooks import MAX_BODY_BYTES, sign
from app.jobs.queue import PROCESS_WEBHOOKS, REGISTER_WEBHOOKS, SYNC_INTEGRATION
from tests.helpers import BASE_URL, ClientFactory, asyncpg_dsn, run_sql
from tests.vikunja_support import Env, app_of, deliver, open_env, webhook_secret

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


@pytest.fixture
async def connected(env: Env) -> tuple[Env, dict[str, Any], dict[str, Any]]:
    """已连接的集成与一个任务（Vikunja 里的原始数据）。"""
    work = env.fake.add_project("工作")
    task = env.fake.add_task(work["id"], "写周报")
    integration = await env.connect()
    return env, integration, task


async def inbox_rows(env: Env) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(asyncpg_dsn(env.database_url))
    try:
        rows = await conn.fetch(
            "SELECT event_type, status, payload_json::text AS payload, event_key"
            " FROM workspace.webhook_inbox ORDER BY received_at"
        )
    finally:
        await conn.close()
    return [dict(row) for row in rows]


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---------------------------------------------------------------- 签名与请求体


async def test_signed_delivery_is_accepted_once(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    """A08 的接收部分：同一事件重复送达返回 202，只记录一次。"""
    env, integration, task = connected
    body = env.fake.delivery("task.updated", task=task)
    first = await deliver(env, integration["id"], body)
    assert first.status_code == 202, first.text
    assert first.json() == {"status": "accepted"}
    again = await deliver(env, integration["id"], body)
    assert again.status_code == 202
    rows = await inbox_rows(env)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "task.updated"
    assert rows[0]["status"] == "pending"
    # 只投递一次处理作业（重复的事件不需要）
    assert env.queue.names().count(PROCESS_WEBHOOKS) == 1


async def test_payload_is_not_trusted_or_stored(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, task = connected
    body = env.fake.delivery("task.updated", task={**task, "title": "攻击者写的标题"})
    assert (await deliver(env, integration["id"], body)).status_code == 202
    [row] = await inbox_rows(env)
    assert "攻击者写的标题" not in row["payload"]
    assert "someone" not in row["payload"]  # doer 不保存
    assert f'"task_id": {task["id"]}' in row["payload"]
    assert f'"project_id": {task["project_id"]}' in row["payload"]


async def post_raw(
    env: Env, integration_id: str, body: bytes, headers: dict[str, str]
) -> httpx.Response:
    async with httpx.AsyncClient(transport=env.client._transport, base_url=BASE_URL) as raw:
        return await raw.post(
            f"/api/v1/webhooks/vikunja/{integration_id}", content=body, headers=headers
        )


async def test_bad_signatures_are_rejected_the_same_way(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, task = connected
    body = env.fake.delivery("task.updated", task=task)
    wrong = await deliver(env, integration["id"], body, secret="不是这个密钥")  # noqa: S106
    assert wrong.status_code == 401
    assert error_code(wrong) == "WEBHOOK_SIGNATURE_INVALID"
    # 集成不存在：同样的 401，不泄露集成是否存在
    missing_integration = await deliver(env, str(uuid.uuid4()), body, secret="x")  # noqa: S106
    assert missing_integration.status_code == 401
    assert error_code(missing_integration) == "WEBHOOK_SIGNATURE_INVALID"
    unsigned = await post_raw(env, integration["id"], body, {"Content-Type": "application/json"})
    assert unsigned.status_code == 401
    # 请求体改了一个字节：原来的签名不再匹配
    secret = await webhook_secret(env, integration["id"])
    tampered = body.replace(b"task.updated", b"task.deleted")
    replayed = await post_raw(
        env, integration["id"], tampered, {"X-Vikunja-Signature": sign(secret, body)}
    )
    assert replayed.status_code == 401
    assert await inbox_rows(env) == []


async def test_disabled_integration_rejects_deliveries(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, task = connected
    disabled = await env.client.patch(
        f"/api/v1/integrations/{integration['id']}",
        json={"version": integration["version"], "enabled": False},
        headers=env.headers(write=True),
    )
    assert disabled.json()["status"] == "disabled"
    response = await deliver(env, integration["id"], env.fake.delivery("task.updated", task=task))
    assert response.status_code == 401
    assert await inbox_rows(env) == []


async def test_oversized_and_invalid_bodies(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, _ = connected
    huge = b'{"event_name":"task.updated","pad":"' + b"x" * MAX_BODY_BYTES + b'"}'
    too_large = await deliver(env, integration["id"], huge)
    assert too_large.status_code == 413
    assert error_code(too_large) == "PAYLOAD_TOO_LARGE"
    not_json = await deliver(env, integration["id"], b"not json")
    assert not_json.status_code == 400
    array = await deliver(env, integration["id"], b"[1, 2]")
    assert array.status_code == 400
    assert await inbox_rows(env) == []


async def test_unknown_events_are_recorded_harmlessly(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, _ = connected
    body = b'{"event_name": "DROP TABLE", "time": "x", "data": {"task": {"id": "1"}}}'
    assert (await deliver(env, integration["id"], body)).status_code == 202
    [row] = await inbox_rows(env)
    assert row["event_type"] == "unknown"
    assert '"task_id": null' in row["payload"]


async def test_queue_failure_does_not_lose_the_event(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, task = connected
    env.queue.fail = True
    response = await deliver(env, integration["id"], env.fake.delivery("task.created", task=task))
    assert response.status_code == 202  # 已落库：Worker 每分钟的兜底轮询会处理
    assert len(await inbox_rows(env)) == 1


async def test_webhook_needs_no_session_or_csrf(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, task = connected
    env.client.cookies.clear()
    response = await deliver(env, integration["id"], env.fake.delivery("task.updated", task=task))
    assert response.status_code == 202


# ---------------------------------------------------------------- 测试连接后注册 Webhook


async def test_successful_test_queues_webhook_registration(env: Env) -> None:
    env.fake.add_project("工作")
    integration = await env.connect()
    assert (REGISTER_WEBHOOKS, (integration["id"],)) in env.queue.jobs


async def test_no_registration_without_webhook_permission(env: Env) -> None:
    env.fake.add_project("工作")
    env.fake.webhooks_allowed = False
    await env.connect()
    assert REGISTER_WEBHOOKS not in env.queue.names()


# ---------------------------------------------------------------- 手动同步


async def test_manual_sync_is_queued_and_locked(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, _ = connected
    path = f"/api/v1/integrations/{integration['id']}/sync"
    queued = await env.client.post(path, headers=env.headers(write=True))
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert (body["integration_id"], body["mode"]) == (integration["id"], "full")
    [(_, (job_integration, owner))] = [j for j in env.queue.jobs if j[0] == SYNC_INTEGRATION]
    assert job_integration == integration["id"]
    assert owner.startswith("manual:")
    busy = await env.client.post(path, headers=env.headers(write=True))
    assert busy.status_code == 409
    assert error_code(busy) == "SYNC_IN_PROGRESS"
    # 作业结束释放锁后可以再次同步
    redis = app_of(env.client).state.redis
    await sync_lock.release(redis, uuid.UUID(integration["id"]), owner)
    assert (await env.client.post(path, headers=env.headers(write=True))).status_code == 202
    audit = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'integration.sync_requested'",
    )
    assert audit == 2


async def test_manual_sync_releases_the_lock_when_queueing_fails(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, _ = connected
    env.queue.fail = True
    path = f"/api/v1/integrations/{integration['id']}/sync"
    failed = await env.client.post(path, headers=env.headers(write=True))
    assert failed.status_code == 500  # 队列（Redis）不可用属于服务端故障
    env.queue.fail = False
    assert (await env.client.post(path, headers=env.headers(write=True))).status_code == 202


async def test_manual_sync_needs_a_tested_integration(env: Env) -> None:
    created = (await env.add_integration()).json()
    response = await env.client.post(
        f"/api/v1/integrations/{created['id']}/sync", headers=env.headers(write=True)
    )
    assert response.status_code == 409
    assert error_code(response) == "INTEGRATION_UNAVAILABLE"
    missing = await env.client.post(
        f"/api/v1/integrations/{uuid.uuid4()}/sync", headers=env.headers(write=True)
    )
    assert missing.status_code == 404


async def test_manual_sync_is_admin_only(
    connected: tuple[Env, dict[str, Any], dict[str, Any]],
) -> None:
    env, integration, _ = connected
    async with env.member("member@example.com", "member") as member:
        headers = {"X-Workspace-Id": env.workspace_id, "X-CSRF-Token": member.cookies["ws_csrf"]}
        response = await member.post(
            f"/api/v1/integrations/{integration['id']}/sync", headers=headers
        )
        assert response.status_code == 403
