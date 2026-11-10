# ruff: noqa: F811 - 测试函数的参数名与导入的 fixture 同名是 pytest 的用法
"""启停的状态版本与同步记录（T-0210，docs/v2/app-center/ AC-2）。

- state_version：实际启停一次加 1；带过期版本 409；旧客户端不带版本仍可用；相同目标不重复执行；
- 钩子失败 / 超时 / 旧钩子不返回结果：开关不回滚，同步记为 failed / unknown，原因只是固定类别；
- 遗留的执行中记录显示为中断；旧版本的记录不冒充当前目标；重试按最新目标、不改开关；
- 跨进程的操作锁：另一个数据库连接持有时 409 MODULE_BUSY 且不写；并发的依赖启停不破坏依赖关系；
- 新闻早报：真实的配置文件写失败记为 config_render_failed；配置文件写锁是部署级的，
  保存设置、Worker、加排除词、启停钩子共用，排队的写入拿到锁后按最新状态生成。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from app.modules import registry, sync
from app.modules.base import HookOutcome, ModuleManifest
from app.modules.news import service as news_service
from app.modules.news.service import NewsService
from tests.helpers import asyncpg_dsn, run_sql
from tests.test_im_accounts_callbacks import ImEnv
from tests.test_news_api import (  # noqa: F401 - 复用早报测试的环境（fixture 按名字注入）
    BODY,
    _feedback,
    conf,
    conf_dir,
    env,
    fake_dns,
    save,
    settings,
    trendradar,
)

pytestmark = pytest.mark.integration
SECRET = "hook-secret-should-not-leak"  # noqa: S105 - 测试用的假值
BEHAVIOR: dict[str, Any] = {"mode": "applied", "calls": []}


async def demo_hook(
    db: Any, settings: Any, runtime: Any, workspace_id: uuid.UUID, enabled: bool
) -> HookOutcome | None:
    """测试模块的钩子：行为由 BEHAVIOR["mode"] 决定。"""
    BEHAVIOR["calls"].append(enabled)
    mode = BEHAVIOR["mode"]
    if mode == "raise":
        raise RuntimeError(SECRET)
    if mode == "sleep":
        await asyncio.sleep(5)
    if mode == "legacy":
        return None
    if mode == "failed":
        return HookOutcome.failed(SECRET)  # 不是固定类别：只记 hook_failed
    return HookOutcome.applied()


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> ModuleManifest:
    manifest = ModuleManifest(
        key="demo",
        name="演示模块",
        description="测试用的带同步钩子的模块",
        on_toggle="tests.test_module_sync:demo_hook",
    )
    digest = ModuleManifest(
        key="demo_child", name="演示子模块", description="依赖 demo", depends_on=("demo",)
    )
    modules = (*registry.MODULES, manifest, digest)
    monkeypatch.setattr(registry, "MODULES", modules)
    monkeypatch.setattr(registry, "BY_KEY", {m.key: m for m in modules})
    BEHAVIOR["mode"] = "applied"
    BEHAVIOR["calls"] = []
    return manifest


async def _patch(env: ImEnv, key: str, body: dict[str, Any], expect: int = 200) -> Any:
    response = await env.client.patch(
        f"/api/v1/modules/{key}", json=body, headers=env.headers(write=True)
    )
    assert response.status_code == expect, response.text
    return response.json()


async def _view(env: ImEnv, key: str) -> dict[str, Any]:
    response = await env.client.get(f"/api/v1/modules/{key}/management", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def _reconcile(env: ImEnv, key: str, version: int, expect: int = 200) -> Any:
    response = await env.client.post(
        f"/api/v1/modules/{key}/reconcile",
        json={"expected_state_version": version},
        headers=env.headers(write=True),
    )
    assert response.status_code == expect, response.text
    return response.json()


async def _count(env: ImEnv, sql: str, *args: Any) -> int:
    value = await run_sql(env.database_url, sql, *args)
    return int(value or 0)


@asynccontextmanager
async def hold(env: ImEnv, name: str) -> AsyncIterator[None]:
    """在另一个数据库连接上持有 advisory lock（模拟另一个 api 进程或 Worker）。"""
    conn = await asyncpg.connect(asyncpg_dsn(env.database_url))
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", name)
        yield
    finally:
        await conn.close()


# ---------------------------------------------------------------- 版本


async def test_versioned_toggle_is_idempotent_and_legacy_compatible(
    env: ImEnv, demo: ModuleManifest
) -> None:
    view = await _view(env, "demo")
    assert view["module"]["state_version"] == 0 and view["module"]["enabled"] is False
    assert view["sync"]["state"] == "unrecorded"

    enabled = await _patch(env, "demo", {"enabled": True, "expected_state_version": 0})
    assert enabled["enabled"] is True and enabled["state_version"] == 1
    conflict = await _patch(
        env, "demo", {"enabled": False, "expected_state_version": 0}, expect=409
    )
    assert conflict["error"]["code"] == "VERSION_CONFLICT"
    assert conflict["error"]["details"] == {"current_state_version": 1}
    # 旧客户端不带版本：照常启停
    assert (await _patch(env, "demo", {"enabled": False}))["state_version"] == 2
    # 目标没变：不加版本、不写审计、不执行钩子
    assert (await _patch(env, "demo", {"enabled": False}))["state_version"] == 2
    assert (await _patch(env, "demo", {"enabled": False, "expected_state_version": 2}))[
        "state_version"
    ] == 2
    assert BEHAVIOR["calls"] == [True, False]
    assert (
        await _count(
            env,
            "SELECT count(*) FROM workspace.audit_logs WHERE resource_id = 'demo' "
            "AND action IN ('module.enabled', 'module.disabled')",
        )
        == 2
    )
    assert await _count(env, "SELECT count(*) FROM workspace.module_sync_attempts") == 2
    view = await _view(env, "demo")
    assert view["sync"]["state"] == "succeeded" and view["sync"]["state_version"] == 2
    assert view["sync"]["target_enabled"] is False and view["sync"]["trigger"] == "toggle"
    assert view["actions"]["can_retry_sync"] is False


async def test_hook_failure_keeps_target_and_records_category(
    env: ImEnv, demo: ModuleManifest
) -> None:
    for mode in ("raise", "failed"):
        BEHAVIOR["mode"] = mode
        current = (await _view(env, "demo"))["module"]
        target = not current["enabled"]
        out = await _patch(env, "demo", {"enabled": target})
        assert out["enabled"] is target  # 开关已经保存，不回滚
        view = await _view(env, "demo")
        assert view["module"]["enabled"] is target
        assert (view["sync"]["state"], view["sync"]["reason"]) == ("failed", "hook_failed")
        assert view["attention"] is True and view["actions"]["can_retry_sync"] is True
    data = await env.client.get("/api/v1/modules/demo/activity", headers=env.headers())
    assert SECRET not in data.text
    failed = [i for i in data.json()["items"] if i["action"] == "module.sync.failed"]
    assert len(failed) == 2
    assert failed[0]["metadata"]["reason"] == "hook_failed"
    assert set(failed[0]["metadata"]) == {
        "module",
        "state_version",
        "attempt_id",
        "target_enabled",
        "trigger",
        "reason",
    }
    rows = await run_sql(
        env.database_url,
        "SELECT string_agg(coalesce(reason, ''), ',') FROM workspace.module_sync_attempts",
    )
    assert SECRET not in rows

    # 重试：按当前目标执行，不改开关
    BEHAVIOR["mode"] = "applied"
    before = await _view(env, "demo")
    retried = await _reconcile(env, "demo", before["module"]["state_version"])
    assert retried["module"] == before["module"]
    assert retried["sync"]["state"] == "succeeded" and retried["sync"]["trigger"] == "retry"
    assert BEHAVIOR["calls"][-1] is before["module"]["enabled"]
    assert retried["attention"] is False
    actions = [
        i["action"]
        for i in (
            await env.client.get("/api/v1/modules/demo/activity", headers=env.headers())
        ).json()["items"]
    ]
    assert actions[:2] == ["module.sync.succeeded", "module.sync.retried"]
    stale = await _reconcile(env, "demo", before["module"]["state_version"] - 1, expect=409)
    assert stale["error"]["code"] == "VERSION_CONFLICT"


async def test_timeout_and_legacy_hooks_are_unknown(
    env: ImEnv, demo: ModuleManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "HOOK_TIMEOUT_SECONDS", 0.3)
    BEHAVIOR["mode"] = "sleep"
    await _patch(env, "demo", {"enabled": True})
    view = await _view(env, "demo")
    assert (view["sync"]["state"], view["sync"]["reason"]) == ("unknown", "timeout")
    assert view["module"]["enabled"] is True and view["attention"] is True

    BEHAVIOR["mode"] = "legacy"
    retried = await _reconcile(env, "demo", 1)
    assert (retried["sync"]["state"], retried["sync"]["reason"]) == (
        "unknown",
        "result_unverified",
    )
    # 没有钩子的模块：not_required，重试也不写记录
    attempts = await _count(env, "SELECT count(*) FROM workspace.module_sync_attempts")
    await _patch(env, "demo_child", {"enabled": True})
    plain = await _reconcile(env, "demo_child", 1)
    assert plain["sync"]["state"] == "not_required" and plain["actions"]["can_retry_sync"] is False
    assert await _count(env, "SELECT count(*) FROM workspace.module_sync_attempts") == attempts


async def test_interrupted_and_old_attempts_do_not_count_as_current(
    env: ImEnv, demo: ModuleManifest
) -> None:
    await _patch(env, "demo", {"enabled": True})  # 版本 1，成功
    workspace = uuid.UUID(env.workspace_id)
    # 进程崩溃留下的“执行中”记录（版本 1，十分钟前开始）
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.module_sync_attempts (workspace_id, module_key, state_version, "
        "target_enabled, trigger, state, started_at, created_at) "
        "VALUES ($1, 'demo', 1, true, 'retry', 'running', now() - interval '10 minutes', now())",
        workspace,
    )
    view = await _view(env, "demo")
    assert (view["sync"]["state"], view["sync"]["reason"]) == ("unknown", "interrupted")
    assert view["actions"]["can_retry_sync"] is True

    # 重试先把遗留记录改成 unknown / interrupted，再执行
    retried = await _reconcile(env, "demo", 1)
    assert retried["sync"]["state"] == "succeeded"
    leftover = await run_sql(
        env.database_url,
        "SELECT state || '/' || reason FROM workspace.module_sync_attempts "
        "WHERE trigger = 'retry' AND started_at < now() - interval '5 minutes'",
    )
    assert leftover == "unknown/interrupted"

    # 版本 2：旧版本较晚写入的失败记录不挡住当前目标的结果
    await _patch(env, "demo", {"enabled": False})
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.module_sync_attempts (workspace_id, module_key, state_version, "
        "target_enabled, trigger, state, reason, created_at) "
        "VALUES ($1, 'demo', 1, true, 'retry', 'failed', 'hook_failed', now() + interval '1 hour')",
        workspace,
    )
    view = await _view(env, "demo")
    assert view["sync"]["state"] == "succeeded" and view["sync"]["state_version"] == 2


# ---------------------------------------------------------------- 跨进程的操作锁


async def test_operation_lock_is_shared_across_connections(
    env: ImEnv, demo: ModuleManifest
) -> None:
    async with hold(env, f"module-op:{env.workspace_id}"):
        busy = await _patch(env, "demo", {"enabled": True}, expect=409)
        assert busy["error"]["code"] == "MODULE_BUSY"
        busy = await _reconcile(env, "demo", 0, expect=409)
        assert busy["error"]["code"] == "MODULE_BUSY"
        # 锁不挡读
        assert (await _view(env, "demo"))["module"]["enabled"] is False
    assert await _count(env, "SELECT count(*) FROM workspace.workspace_modules") == 0
    assert BEHAVIOR["calls"] == []
    assert (await _patch(env, "demo", {"enabled": True}))["state_version"] == 1


async def test_parallel_dependency_toggles_keep_the_invariant(
    env: ImEnv, demo: ModuleManifest
) -> None:
    BEHAVIOR["mode"] = "applied"
    for _ in range(6):
        await _patch(env, "demo", {"enabled": True})
        await _patch(env, "demo_child", {"enabled": False})
        off, child = await asyncio.gather(
            env.client.patch(
                "/api/v1/modules/demo", json={"enabled": False}, headers=env.headers(write=True)
            ),
            env.client.patch(
                "/api/v1/modules/demo_child",
                json={"enabled": True},
                headers=env.headers(write=True),
            ),
        )
        assert {off.status_code, child.status_code} <= {200, 409}
        for response in (off, child):
            if response.status_code == 409:
                assert response.json()["error"]["code"] in ("MODULE_BUSY", "MODULE_DEPENDENCY")
        states = {
            m["key"]: m["enabled"]
            for m in (await env.client.get("/api/v1/modules", headers=env.headers())).json()[
                "items"
            ]
        }
        assert not (states["demo_child"] and not states["demo"]), states


# ---------------------------------------------------------------- 新闻早报


async def test_real_news_render_failure_is_reported(env: ImEnv) -> None:
    # 没打开早报推送：不写文件，记 not_required
    await _patch(env, "news", {"enabled": False})
    view = await _view(env, "news")
    assert (view["sync"]["state"], view["sync"]["reason"]) == (
        "not_required",
        "application_not_active",
    )
    await _patch(env, "news", {"enabled": True})
    await save(env)
    assert conf(env)["notification"]["enabled"] is True

    # 配置目录变成了一个普通文件：真实的写文件路径抛 OSError
    directory = conf_dir(env)
    backup = directory.with_name("trendradar.bak")
    directory.rename(backup)
    directory.write_text("not a directory")
    try:
        out = await _patch(env, "news", {"enabled": False})
        assert out["enabled"] is False
        view = await _view(env, "news")
        assert (view["sync"]["state"], view["sync"]["reason"]) == (
            "failed",
            "config_render_failed",
        )
        assert view["attention"] is True
        # 错误说明留在设置行里（模块停用后页面接口 404，直接查库）
        error = await run_sql(env.database_url, "SELECT render_error FROM workspace.news_settings")
        assert error.startswith("配置文件写入失败")
    finally:
        directory.unlink()
        backup.rename(directory)
    retried = await _reconcile(env, "news", view["module"]["state_version"])
    assert retried["sync"]["state"] == "succeeded" and retried["module"]["enabled"] is False
    assert conf(env)["notification"]["enabled"] is False
    assert await run_sql(env.database_url, "SELECT render_error FROM workspace.news_settings") == ""


async def test_render_lock_is_deployment_wide(env: ImEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(news_service, "RENDER_LOCK_WAIT_SECONDS", 0.5)
    await save(env)
    name = f"news-config:{env.app.state.settings.news_config_dir}"
    async with hold(env, name):
        # 另一个进程在写配置文件：保存设置拿不到锁，什么都不改
        before = await settings(env)
        response = await env.client.put(
            "/api/v1/news/settings",
            json={**BODY, "version": before["version"], "exclusions": ["新词"]},
            headers=env.headers(write=True),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "NEWS_CONFIG_BUSY"
        assert (await settings(env))["version"] == before["version"]
        # 启停照常保存；钩子等不到锁，同步记为 failed / busy
        assert (await _patch(env, "news", {"enabled": False}))["enabled"] is False
        view = await _view(env, "news")
        assert (view["sync"]["state"], view["sync"]["reason"]) == ("failed", "busy")
    assert conf(env)["notification"]["enabled"] is True  # 还是旧配置，所以要重试
    retried = await _reconcile(env, "news", view["module"]["state_version"])
    assert retried["sync"]["state"] == "succeeded"
    assert conf(env)["notification"]["enabled"] is False


async def test_queued_renders_use_the_latest_state(
    env: ImEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """设置保存、加排除词、Worker 在停用之前就开始排队；拿到锁后都按停用后的状态生成。"""
    monkeypatch.setattr(news_service, "RENDER_LOCK_WAIT_SECONDS", 20.0)
    await save(env)
    assert conf(env)["notification"]["enabled"] is True
    name = f"news-config:{env.app.state.settings.news_config_dir}"
    current = await settings(env)

    async def worker() -> str:
        async with env.app.state.sessionmaker() as db:
            service = NewsService(db, env.app.state.settings, env.app.state.connectors)
            return await service.relearn_and_render()

    async with hold(env, name):
        saving = asyncio.create_task(
            env.client.put(
                "/api/v1/news/settings",
                json={**BODY, "version": current["version"], "push_times": ["08:00"]},
                headers=env.headers(write=True),
            )
        )
        excluding = asyncio.create_task(
            env.client.post(
                "/api/v1/news/feedback",
                json=_feedback("dismiss", "https://example.com/a", exclude_word="明星"),
                headers=env.headers(write=True),
            )
        )
        relearning = asyncio.create_task(worker())
        await asyncio.sleep(0.5)  # 三个写入都在等锁
        assert not (saving.done() or excluding.done() or relearning.done())
        disabling = asyncio.create_task(_patch(env, "news", {"enabled": False}))
        await asyncio.sleep(0.5)
    saved, excluded, relearned, disabled = await asyncio.gather(
        saving, excluding, relearning, disabling
    )
    # 加排除词先拿到锁时会改设置版本：排在后面的保存按乐观并发 409（不会覆盖）
    assert saved.status_code in (200, 409), saved.text
    if saved.status_code == 409:
        assert saved.json()["error"]["code"] == "VERSION_CONFLICT"
    assert excluded.status_code == 201, excluded.text
    assert relearned == ""
    assert disabled["enabled"] is False
    # 最后的配置文件：停用后的“不抓取、不推送”
    assert conf(env)["notification"]["enabled"] is False
    view = await _view(env, "news")
    assert view["sync"]["state"] == "succeeded"
    # 排队期间的修改都保存了（再启用时恢复）
    await _patch(env, "news", {"enabled": True})
    restored = await settings(env)
    assert "明星" in restored["exclusions"]
    if saved.status_code == 200:
        assert restored["push_times"] == ["08:00"]
    assert conf(env)["notification"]["enabled"] is True


def test_lock_names_are_fixed(tmp_path: Path) -> None:
    from app.modules.locks import module_operation_lock

    workspace = uuid.uuid4()
    assert module_operation_lock(workspace) == f"module-op:{workspace}"
