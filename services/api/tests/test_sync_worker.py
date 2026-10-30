"""Worker 的同步作业（T-0021，S3-05 / S3-06）：真实 PostgreSQL / Redis + 内存版 Vikunja。

直接调用 app/domain/sync.py 的作业函数（Worker 里的 ARQ 作业只是薄包装）。覆盖：
- Inbox 处理：事件生效（以 Vikunja 当前状态为准）、重复送达只产生一次效果（A08）、乱序不写旧数据、
  删除记 tombstone、项目删除连带任务、新项目里的任务
- 失败：暂时性错误指数退避重试、超过次数进 dead_letter、Token 被拒不再重试；
  处理进程中途退出后按租约重新领取；并发领取互不重叠（SKIP LOCKED）
- 周期对账：Webhook 丢失后由增量 / 全量纠正（A09）、失败不前移游标、锁、重启后继续、首页 ok / stale
- Webhook 注册、手动同步作业、过期数据清理
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.domain import sync, sync_lock
from app.domain.sync import WEBHOOK_EVENTS
from app.jobs.queue import SYNC_INTEGRATION
from app.repositories import inbox
from tests.helpers import ClientFactory, asyncpg_dsn, run_sql
from tests.vikunja_support import (
    CONNECTOR_SETTINGS,
    Env,
    deliver,
    open_env,
    sync_context,
    use_fake,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url, webhook_max_attempts=2) as value:
        yield value


class Setup:
    """已连接的工作空间：项目“工作”“生活”，“工作”里一个任务。"""

    def __init__(
        self,
        env: Env,
        integration: dict[str, Any],
        work: dict[str, Any],
        life: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        self.env = env
        self.integration = integration
        self.integration_id = uuid.UUID(integration["id"])
        self.work = work
        self.life = life
        self.task = task


@pytest.fixture
async def ready(env: Env) -> Setup:
    work = env.fake.add_project("工作")
    life = env.fake.add_project("生活")
    task = env.fake.add_task(work["id"], "写周报")
    integration = await env.connect()
    return Setup(env, integration, work, life, task)


async def rows(env: Env, sql: str, *args: Any) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(asyncpg_dsn(env.database_url))
    try:
        return [dict(r) for r in await conn.fetch(sql, *args)]
    finally:
        await conn.close()


async def events(env: Env) -> list[dict[str, Any]]:
    return await rows(
        env,
        "SELECT event_type, status, attempts, error_text, next_retry_at, processed_at"
        " FROM workspace.webhook_inbox ORDER BY received_at, id",
    )


async def titles(env: Env) -> set[str]:
    return {t["title"] for t in await env.tasks(status="all")}


async def integration_status(env: Env) -> str:
    status: str = (await env.integration())["status"]
    return status


def touch(env: Env, task_id: int, **fields: Any) -> dict[str, Any]:
    """在 Vikunja 里直接改任务（不经过工作台，也不投递 Webhook）。"""
    task = env.fake.tasks[task_id]
    task.update(fields, updated=env.fake.tick())
    return task


async def make_due(env: Env) -> None:
    await run_sql(
        env.database_url,
        "UPDATE workspace.webhook_inbox SET next_retry_at = now() - interval '1 second'"
        " WHERE status IN ('retry', 'processing')",
    )


# ---------------------------------------------------------------- Inbox 处理


async def test_event_applies_the_current_state_once(ready: Setup) -> None:
    """A08：同一事件重复送达最多执行一次有效副作用。"""
    env = ready.env
    touch(env, ready.task["id"], title="改过的标题")
    body = env.fake.delivery("task.updated", task=env.fake.tasks[ready.task["id"]])
    for _ in range(3):
        assert (await deliver(env, ready.integration["id"], body)).status_code == 202
    gets_before = len(env.fake.calls("GET", r"/tasks/\d+"))
    assert await sync.process_inbox(env.sync) == 1
    assert len(env.fake.calls("GET", r"/tasks/\d+")) == gets_before + 1
    assert await titles(env) == {"改过的标题"}
    [event] = await events(env)
    assert (event["status"], event["attempts"], event["error_text"]) == ("completed", 1, None)
    assert event["processed_at"] is not None
    assert await sync.process_inbox(env.sync) == 0  # 处理过的事件不会再被领取


async def test_out_of_order_data_does_not_overwrite_newer(ready: Setup) -> None:
    env = ready.env
    old = dict(env.fake.tasks[ready.task["id"]])
    touch(env, ready.task["id"], title="新标题")
    await deliver(env, ready.integration["id"], env.fake.delivery("task.updated", task=old))
    await sync.process_inbox(env.sync)
    assert await titles(env) == {"新标题"}
    # Vikunja 返回了更旧的数据（例如读到了落后的副本）：投影比较 updated，不回退
    env.fake.tasks[ready.task["id"]] = old
    await deliver(env, ready.integration["id"], env.fake.delivery("task.updated", task=old))
    await sync.process_inbox(env.sync)
    assert await titles(env) == {"新标题"}


async def test_deleted_task_is_tombstoned(ready: Setup) -> None:
    env = ready.env
    last_state = env.fake.tasks.pop(ready.task["id"])
    await deliver(env, ready.integration["id"], env.fake.delivery("task.deleted", task=last_state))
    await sync.process_inbox(env.sync)
    assert await env.tasks(status="all") == []
    [event] = await events(env)
    assert event["status"] == "completed"


async def test_deleted_project_removes_its_tasks(ready: Setup) -> None:
    env = ready.env
    project = env.fake.projects.pop(ready.work["id"])
    env.fake.tasks.pop(ready.task["id"])
    await deliver(
        env, ready.integration["id"], env.fake.delivery("project.deleted", project=project)
    )
    await sync.process_inbox(env.sync)
    assert set(await env.projects()) == {"生活"}
    assert await env.tasks(status="all") == []


async def test_task_in_a_new_project_brings_the_project(ready: Setup) -> None:
    env = ready.env
    side = env.fake.add_project("新项目")
    task = env.fake.add_task(side["id"], "新项目里的任务")
    await deliver(env, ready.integration["id"], env.fake.delivery("task.created", task=task))
    await sync.process_inbox(env.sync)
    projects = await env.projects()
    assert "新项目" in projects
    assert (await env.task_by_title("新项目里的任务"))["project_id"] == projects["新项目"]


async def test_task_whose_project_is_already_gone(ready: Setup) -> None:
    env = ready.env
    side = env.fake.add_project("马上删除")
    task = env.fake.add_task(side["id"], "孤立的任务")
    env.fake.projects.pop(side["id"])
    await deliver(env, ready.integration["id"], env.fake.delivery("task.created", task=task))
    await sync.process_inbox(env.sync)
    [event] = await events(env)
    assert event["status"] == "completed"
    assert (await env.task_by_title("孤立的任务"))["project_id"] is None


async def test_project_update_event(ready: Setup) -> None:
    env = ready.env
    env.fake.projects[ready.life["id"]].update(title="生活（改名）", updated=env.fake.tick())
    project = env.fake.projects[ready.life["id"]]
    await deliver(
        env, ready.integration["id"], env.fake.delivery("project.updated", project=project)
    )
    await sync.process_inbox(env.sync)
    assert "生活（改名）" in await env.projects()


async def test_transient_failures_retry_with_backoff_then_dead_letter(ready: Setup) -> None:
    env = ready.env
    env.fake.fault("GET", r"/tasks/\d+", 503, times=10)
    task = env.fake.tasks[ready.task["id"]]
    await deliver(env, ready.integration["id"], env.fake.delivery("task.updated", task=task))
    before = datetime.now(UTC)
    await sync.process_inbox(env.sync)
    [event] = await events(env)
    assert (event["status"], event["attempts"], event["error_text"]) == ("retry", 1, "http_error")
    assert event["next_retry_at"] >= before + timedelta(seconds=20)  # 30 秒 ±20%
    assert await integration_status(env) == "unavailable"
    assert await sync.process_inbox(env.sync) == 0  # 还没到重试时间
    await make_due(env)
    await sync.process_inbox(env.sync)
    [event] = await events(env)
    # 测试里 webhook_max_attempts=2：第二次失败后进 dead_letter，不再自动重试
    assert (event["status"], event["attempts"]) == ("dead_letter", 2)
    assert event["error_text"] == "http_error:max_attempts"
    assert event["next_retry_at"] is None
    # Vikunja 恢复后，新的事件照常处理，集成恢复 active
    env.fake.faults.clear()
    touch(env, ready.task["id"], title="恢复后")
    body = env.fake.delivery("task.updated", task=env.fake.tasks[ready.task["id"]])
    await deliver(env, ready.integration["id"], body)
    await sync.process_inbox(env.sync)
    assert await titles(env) == {"恢复后"}
    assert await integration_status(env) == "active"


def test_retry_delay_grows_exponentially_with_jitter() -> None:
    delays = [sync.retry_delay(n) for n in range(1, 9)]
    assert timedelta(seconds=24) <= delays[0] <= timedelta(seconds=36)
    assert timedelta(seconds=48) <= delays[1] <= timedelta(seconds=72)
    assert all(d <= timedelta(hours=1, minutes=12) for d in delays)
    assert sync.retry_delay(1, retry_after=600) >= timedelta(seconds=600)


async def test_rejected_token_is_not_retried(ready: Setup) -> None:
    env = ready.env
    env.fake.token = "tk_" + "q" * 40
    task = env.fake.tasks[ready.task["id"]]
    await deliver(env, ready.integration["id"], env.fake.delivery("task.updated", task=task))
    await sync.process_inbox(env.sync)
    [event] = await events(env)
    assert (event["status"], event["error_text"]) == ("dead_letter", "token_rejected")
    assert await integration_status(env) == "unauthorized"


async def test_events_of_a_crashed_worker_are_reclaimed(ready: Setup) -> None:
    """规范第 10 节：进程重启后可继续。"""
    env = ready.env
    touch(env, ready.task["id"], title="崩溃前收到")
    body = env.fake.delivery("task.updated", task=env.fake.tasks[ready.task["id"]])
    await deliver(env, ready.integration["id"], body)
    # 某个 Worker 领取后中途退出：事件停在 processing
    async with env.sync.sessions() as db:
        claimed = await inbox.claim(db, now=datetime.now(UTC), lease=sync.LEASE, limit=10)
        assert len(claimed) == 1
        await db.commit()
    assert await sync.process_inbox(env.sync) == 0  # 租约未到期，不会被重复领取
    await make_due(env)  # 租约到期
    assert await sync.process_inbox(env.sync) == 1
    [event] = await events(env)
    assert (event["status"], event["attempts"]) == ("completed", 2)
    assert await titles(env) == {"崩溃前收到"}


async def test_concurrent_claims_do_not_overlap(ready: Setup) -> None:
    env = ready.env
    task = env.fake.tasks[ready.task["id"]]
    for _ in range(4):
        await deliver(env, ready.integration["id"], env.fake.delivery("task.updated", task=task))
    now = datetime.now(UTC)
    async with env.sync.sessions() as first, env.sync.sessions() as second:
        a = await inbox.claim(first, now=now, lease=sync.LEASE, limit=2)
        b = await inbox.claim(second, now=now, lease=sync.LEASE, limit=10)
        assert len(a) == 2
        assert len(b) == 2
        assert {e.id for e in a}.isdisjoint({e.id for e in b})
        await first.rollback()
        await second.rollback()


async def test_events_for_inactive_integrations_are_skipped(ready: Setup) -> None:
    env = ready.env
    task = env.fake.tasks[ready.task["id"]]
    await deliver(env, ready.integration["id"], env.fake.delivery("task.updated", task=task))
    await run_sql(env.database_url, "UPDATE workspace.integrations SET status = 'pending'")
    await sync.process_inbox(env.sync)
    [event] = await events(env)
    assert (event["status"], event["error_text"]) == ("completed", "integration_inactive")


# ---------------------------------------------------------------- 周期对账


async def test_reconcile_corrects_lost_webhooks(ready: Setup) -> None:
    """A09：Webhook 丢失时，对账最终纠正投影。"""
    env = ready.env
    touch(env, ready.task["id"], title="没有 Webhook 的修改")
    env.fake.add_task(ready.life["id"], "没有 Webhook 的新任务")
    assert await sync.reconcile_all(env.sync) == 1
    assert await titles(env) == {"没有 Webhook 的修改", "没有 Webhook 的新任务"}
    # 删除在增量里看不到；下一次全量对账补上
    env.fake.tasks.pop(ready.task["id"])
    await sync.reconcile_all(env.sync)
    assert "没有 Webhook 的修改" in await titles(env)
    await run_sql(
        env.database_url,
        "UPDATE workspace.sync_cursors SET last_success_at = now() - interval '2 hours'"
        " WHERE resource_type = 'full_sync'",
    )
    await sync.reconcile_all(env.sync)
    assert await titles(env) == {"没有 Webhook 的新任务"}


async def test_incremental_sync_notices_deleted_projects(ready: Setup) -> None:
    env = ready.env
    env.fake.projects.pop(ready.work["id"])
    env.fake.tasks.pop(ready.task["id"])
    assert await sync.sync_integration(env.sync, ready.integration_id) == "incremental"
    assert set(await env.projects()) == {"生活"}
    assert await env.tasks(status="all") == []


async def test_reconcile_restores_freshness(ready: Setup) -> None:
    env = ready.env
    await run_sql(
        env.database_url,
        "UPDATE workspace.sync_cursors SET last_success_at = now() - interval '1 hour'",
    )
    overview = await env.get("/api/v1/dashboard/overview")
    assert overview["modules"]["tasks"]["status"] == "stale"
    await sync.reconcile_all(env.sync)
    overview = await env.get("/api/v1/dashboard/overview")
    assert overview["modules"]["tasks"]["status"] == "ok"


async def test_failed_reconcile_keeps_the_cursor(ready: Setup) -> None:
    env = ready.env
    [before] = await rows(
        env,
        "SELECT last_success_at, cursor_value FROM workspace.sync_cursors"
        " WHERE resource_type = 'task'",
    )
    env.fake.fault("GET", "/projects", 503, times=5)
    assert await sync.sync_integration(env.sync, ready.integration_id) == "failed"
    [after] = await rows(
        env,
        "SELECT last_success_at, cursor_value, last_error FROM workspace.sync_cursors"
        " WHERE resource_type = 'task'",
    )
    assert after["last_success_at"] == before["last_success_at"]
    assert after["cursor_value"] == before["cursor_value"]
    assert after["last_error"] == "http_error"
    assert await integration_status(env) == "unavailable"
    env.fake.faults.clear()
    assert await sync.sync_integration(env.sync, ready.integration_id) == "incremental"
    assert await integration_status(env) == "active"
    [recovered] = await rows(
        env, "SELECT last_error FROM workspace.sync_cursors WHERE resource_type = 'task'"
    )
    assert recovered["last_error"] is None


async def test_reconcile_skips_integrations_being_synced(ready: Setup) -> None:
    env = ready.env
    redis = env.sync.redis
    owner = sync_lock.new_owner("test")
    assert await sync_lock.acquire(redis, ready.integration_id, owner)
    assert await sync.reconcile_all(env.sync) == 0
    await sync_lock.release(redis, ready.integration_id, owner)
    assert await sync.reconcile_all(env.sync) == 1


async def test_reconcile_continues_after_a_restart(
    ready: Setup, make_client: ClientFactory
) -> None:
    """游标在数据库里：新进程从上次的位置继续增量，而不是从头全量。"""
    env = ready.env
    touch(env, ready.task["id"], title="重启后")
    async with make_client(**CONNECTOR_SETTINGS) as restarted:
        use_fake(restarted, env.fake)
        context = sync_context(restarted)
        assert await sync.sync_integration(context, ready.integration_id) == "incremental"
    assert await titles(env) == {"重启后"}


async def test_unauthorized_and_disabled_integrations_are_not_reconciled(ready: Setup) -> None:
    env = ready.env
    await run_sql(env.database_url, "UPDATE workspace.integrations SET status = 'unauthorized'")
    assert await sync.reconcile_all(env.sync) == 0
    assert await sync.sync_integration(env.sync, ready.integration_id) == "skipped"


# ---------------------------------------------------------------- 手动同步作业


async def test_manual_sync_job_runs_a_full_sync_and_releases_the_lock(ready: Setup) -> None:
    env = ready.env
    path = f"/api/v1/integrations/{ready.integration['id']}/sync"
    assert (await env.client.post(path, headers=env.headers(write=True))).status_code == 202
    [(_, (integration_id, owner))] = [j for j in env.queue.jobs if j[0] == SYNC_INTEGRATION]
    env.fake.tasks.pop(ready.task["id"])  # 全量同步才能发现的删除
    assert await sync.run_manual_sync(env.sync, uuid.UUID(integration_id), owner) is True
    assert await env.tasks(status="all") == []
    assert (await env.client.post(path, headers=env.headers(write=True))).status_code == 202


async def test_manual_sync_job_does_not_run_alongside_another_sync(ready: Setup) -> None:
    env = ready.env
    other = sync_lock.new_owner("cron")
    assert await sync_lock.acquire(env.sync.redis, ready.integration_id, other)
    assert await sync.run_manual_sync(env.sync, ready.integration_id, "manual:x") is False
    await sync_lock.release(env.sync.redis, ready.integration_id, other)


# ---------------------------------------------------------------- Webhook 注册


async def test_webhooks_are_registered_once_per_project(ready: Setup) -> None:
    env = ready.env
    env.fake.add_project("已归档", archived=True)
    await sync.reconcile_all(env.sync)  # 让投影里有归档项目
    assert await sync.register_for(env.sync, ready.integration_id) == "auto"
    target = f"http://caddy:8080/api/v1/webhooks/vikunja/{ready.integration['id']}"
    hooks = list(env.fake.webhooks.values())
    assert {h["project_id"] for h in hooks} == {ready.work["id"], ready.life["id"]}  # 不含归档
    assert all(h["target_url"] == target for h in hooks)
    assert all(tuple(h["events"]) == WEBHOOK_EVENTS for h in hooks)
    assert len({h["secret"] for h in hooks}) == 1
    assert (await env.integration())["webhooks"] == {
        "mode": "auto",
        "registered_projects": 2,
        "reason": None,
    }
    assert await sync.register_for(env.sync, ready.integration_id) == "auto"
    assert len(env.fake.webhooks) == 2  # 已有同一地址的 Webhook：不重复注册
    # 注册时交给 Vikunja 的密钥与工作台校验签名用的是同一个：端到端投递成功
    secret = hooks[0]["secret"]
    task = env.fake.tasks[ready.task["id"]]
    body = env.fake.delivery("task.updated", task=task)
    response = await deliver(env, ready.integration["id"], body, secret=secret)
    assert response.status_code == 202


async def test_registration_without_permission_falls_back_to_reconcile(ready: Setup) -> None:
    env = ready.env
    env.fake.webhooks_allowed = False
    assert await sync.register_for(env.sync, ready.integration_id) == "unavailable"
    webhooks = (await env.integration())["webhooks"]
    assert (webhooks["mode"], webhooks["reason"]) == ("unavailable", "missing_permission")


async def test_full_sync_registers_webhooks_for_new_projects(ready: Setup) -> None:
    env = ready.env
    await sync.register_for(env.sync, ready.integration_id)
    later = env.fake.add_project("后来的项目")
    await run_sql(
        env.database_url,
        "UPDATE workspace.sync_cursors SET last_success_at = now() - interval '2 hours'"
        " WHERE resource_type = 'full_sync'",
    )
    assert await sync.sync_integration(env.sync, ready.integration_id) == "full"
    assert later["id"] in {h["project_id"] for h in env.fake.webhooks.values()}


async def test_registration_needs_a_configured_address(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with open_env(make_client, migrated_database_url, webhook_base_url="") as env:
        env.fake.add_project("工作")
        integration = await env.connect()
        integration_id = uuid.UUID(integration["id"])
        assert await sync.register_for(env.sync, integration_id) == "unavailable"
        assert (await env.integration())["webhooks"]["reason"] == "not_configured"
        assert env.fake.webhooks == {}


# ---------------------------------------------------------------- 清理


async def test_purge_removes_expired_records(ready: Setup) -> None:
    env = ready.env
    task = env.fake.tasks[ready.task["id"]]
    for name in ("task.updated", "task.created"):
        await deliver(env, ready.integration["id"], env.fake.delivery(name, task=task))
    await sync.process_inbox(env.sync)
    await run_sql(
        env.database_url,
        "UPDATE workspace.webhook_inbox SET processed_at = now() - interval '8 days'"
        " WHERE event_type = 'task.updated'",
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.webhook_inbox SET status = 'dead_letter',"
        " processed_at = now() - interval '30 days' WHERE event_type = 'task.created'",
    )
    projects = await env.projects()
    created = await env.create_task({"project_id": projects["工作"], "title": "留下幂等记录"})
    assert created.status_code == 201
    await run_sql(
        env.database_url,
        "UPDATE workspace.idempotency_requests SET expires_at = now() - interval '1 minute'",
    )
    await sync.purge(env.sync)
    remaining = await events(env)
    assert [e["status"] for e in remaining] == ["dead_letter"]  # dead_letter 保留，便于排查
    assert (
        await run_sql(env.database_url, "SELECT count(*) FROM workspace.idempotency_requests") == 0
    )
