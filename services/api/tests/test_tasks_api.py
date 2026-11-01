"""项目与任务接口的集成测试（T-0020，S3-03 / S3-04）：真实 PostgreSQL / Redis + 内存版 Vikunja。

覆盖 Sprint 3 契约第 5 ~ 8、10 节：
- 读取走本地投影（不访问 Vikunja）、过滤 / 排序 / 分页、新鲜度
- 写入的 Idempotency-Key：重放（A06）、换内容复用（A07）、仍在处理、结果未知后的查询、
  确定没发出时可以重试
- 修改 / 删除的 version 比较、删除的幂等、外部错误的映射与集成状态
- 权限：viewer 只读、member 可写、其他工作空间看不到
- 首页的任务模块：汇总、今日任务、stale / unauthorized
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.helpers import BOB, ClientFactory, add_user, add_workspace, run_sql
from tests.vikunja_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        yield value


@pytest.fixture
async def ready(env: Env) -> Env:
    """已连接、同步过一个项目和一个任务的工作空间。"""
    work = env.fake.add_project("工作")
    env.fake.add_project("生活")
    env.fake.add_task(work["id"], "写周报", priority=3, description="**本周**")
    await env.connect()
    return env


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


async def new_task(env: Env, title: str = "新任务", **fields: Any) -> dict[str, Any]:
    projects = await env.projects()
    body = {"project_id": projects["工作"], "title": title, **fields}
    response = await env.create_task(body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# ---------------------------------------------------------------- 读取


async def test_reads_come_from_the_projection(ready: Env) -> None:
    before = len(ready.fake.requests)
    page = await ready.get("/api/v1/tasks")
    assert page["sync"]["status"] == "ok"
    [task] = page["items"]
    assert task["title"] == "写周报"
    assert task["description"] == "**本周**"  # Markdown 原文
    assert task["external_id"].startswith("vikunja:task:")
    assert task["source_url"].startswith("https://tasks.example.com/tasks/")
    assert task["project_id"] == (await ready.projects())["工作"]
    detail = await ready.get(f"/api/v1/tasks/{task['id']}")
    assert detail == task
    projects = await ready.get("/api/v1/projects")
    assert [p["title"] for p in projects["items"]] == ["工作", "生活"]
    assert projects["sync"]["status"] == "ok"
    assert len(ready.fake.requests) == before  # 读取不访问 Vikunja


async def test_task_filters_sort_and_pagination(env: Env) -> None:
    work = env.fake.add_project("工作")
    life = env.fake.add_project("生活")
    now = datetime.now(UTC)
    env.fake.add_task(work["id"], "明天", priority=1, due_date=_stamp(now + timedelta(days=1)))
    env.fake.add_task(work["id"], "下周", priority=5, due_date=_stamp(now + timedelta(days=7)))
    env.fake.add_task(work["id"], "无截止", priority=2)
    env.fake.add_task(life["id"], "买菜 100%_", priority=4, due_date=_stamp(now))
    env.fake.add_task(life["id"], "已完成", done=True)
    await env.connect()
    projects = await env.projects()

    assert [t["title"] for t in await env.tasks()] == ["买菜 100%_", "明天", "下周", "无截止"]
    assert [t["title"] for t in await env.tasks(status="done")] == ["已完成"]
    assert len(await env.tasks(status="all")) == 5
    assert [t["title"] for t in await env.tasks(sort="priority")] == [
        "下周",
        "买菜 100%_",
        "无截止",
        "明天",
    ]
    assert {t["title"] for t in await env.tasks(project_id=projects["生活"])} == {"买菜 100%_"}
    assert {t["title"] for t in await env.tasks(priority=[1, 5])} == {"明天", "下周"}
    assert [t["title"] for t in await env.tasks(q="100%_")] == ["买菜 100%_"]  # LIKE 通配符被转义
    assert [t["title"] for t in await env.tasks(q="%")] == ["买菜 100%_"]
    soon = await env.tasks(due_before=(now + timedelta(days=2)).isoformat())
    assert {t["title"] for t in soon} == {"买菜 100%_", "明天"}

    titles: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = await env.get("/api/v1/tasks", **params)
        titles += [t["title"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert titles == ["买菜 100%_", "明天", "下周", "无截止"]

    bad = await env.client.get(
        "/api/v1/tasks", params={"cursor": "not-a-cursor"}, headers=env.headers()
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["loc"] == ["query", "cursor"]
    searched = await env.get("/api/v1/projects", q="工")
    assert [p["title"] for p in searched["items"]] == ["工作"]


async def test_project_progress_counts_open_and_done_tasks(env: Env) -> None:
    """首页“项目进度”组件（T-0064）：有未完成任务的项目在前，归档项目不显示。"""
    work = env.fake.add_project("工作")
    life = env.fake.add_project("生活")
    env.fake.add_project("空项目")
    env.fake.add_project("旧项目", archived=True)
    for title in ("周报", "评审", "部署"):
        env.fake.add_task(work["id"], title)
    env.fake.add_task(work["id"], "已上线", done=True)
    env.fake.add_task(life["id"], "买菜")
    env.fake.add_task(life["id"], "交水费", done=True)
    env.fake.add_task(life["id"], "体检", done=True)
    await env.connect()
    page = await env.get("/api/v1/projects/progress")
    assert page["sync"]["status"] == "ok"
    rows = [(p["title"], p["open_tasks"], p["done_tasks"]) for p in page["items"]]
    assert rows == [("工作", 3, 1), ("生活", 1, 2), ("空项目", 0, 0)]
    assert page["items"][0]["source_url"].startswith("https://tasks.example.com/projects/")
    limited = await env.get("/api/v1/projects/progress", limit=1)
    assert [p["title"] for p in limited["items"]] == ["工作"]
    too_many = await env.client.get(
        "/api/v1/projects/progress", params={"limit": 21}, headers=env.headers()
    )
    assert too_many.status_code == 422


async def test_reads_without_an_integration(env: Env) -> None:
    page = await env.get("/api/v1/tasks")
    assert page["items"] == []
    assert page["sync"] == {
        "status": "not_configured",
        "as_of": None,
        "reason": "not_configured",
        "retry_at": None,
    }
    missing = await env.client.get(f"/api/v1/tasks/{uuid.uuid4()}", headers=env.headers())
    assert missing.status_code == 404


# ---------------------------------------------------------------- 创建与 Idempotency-Key


async def test_create_task_and_replay_with_the_same_key(ready: Env) -> None:
    """A06：同一个 Idempotency-Key 重放不重复创建。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    due = datetime(2026, 11, 3, 18, 0, tzinfo=UTC).isoformat()
    body = {"project_id": projects["工作"], "title": "交报告", "priority": 4, "due_at": due}
    first = await ready.create_task(body, key)
    assert first.status_code == 201, first.text
    assert "Idempotent-Replayed" not in first.headers
    created = first.json()
    assert (created["title"], created["priority"]) == ("交报告", 4)
    assert created["due_at"] == "2026-11-03T18:00:00Z"
    assert created["project_id"] == projects["工作"]
    replay = await ready.create_task(body, key)
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == created
    assert len(ready.fake.calls("POST", r"/projects/\d+/tasks")) == 1
    assert [t["title"] for t in ready.fake.tasks.values()].count("交报告") == 1
    assert (await ready.get(f"/api/v1/tasks/{created['id']}"))["title"] == "交报告"
    audit = await run_sql(
        ready.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'task.created'",
    )
    assert audit == 1


async def test_reusing_a_key_for_a_different_request_is_rejected(ready: Env) -> None:
    """A07：同一个键换了请求内容返回 409。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "第一次"}
    assert (await ready.create_task(body, key)).status_code == 201
    reused = await ready.create_task({**body, "title": "第二次"}, key)
    assert reused.status_code == 409
    assert error_code(reused) == "IDEMPOTENCY_KEY_REUSED"
    assert len(ready.fake.calls("POST", r"/projects/\d+/tasks")) == 1


async def test_writes_require_an_idempotency_key(ready: Env) -> None:
    projects = await ready.projects()
    response = await ready.client.post(
        "/api/v1/tasks",
        json={"project_id": projects["工作"], "title": "没有键"},
        headers=ready.headers(write=True),
    )
    assert response.status_code == 422
    invalid = await ready.client.post(
        "/api/v1/tasks",
        json={"project_id": projects["工作"], "title": "坏键"},
        headers={**ready.headers(write=True), "Idempotency-Key": "abc"},
    )
    assert invalid.status_code == 422
    assert ready.fake.calls("POST", r"/projects/\d+/tasks") == []


class Gate(httpx.AsyncBaseTransport):
    """让匹配的请求停在发出之前，直到测试放行（模拟一个很慢的 Vikunja）。"""

    def __init__(self, inner: httpx.AsyncBaseTransport, method: str) -> None:
        self.inner = inner
        self.method = method
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == self.method:
            self.entered.set()
            await self.release.wait()
        return await self.inner.handle_async_request(request)


async def test_concurrent_duplicate_is_in_progress(ready: Env) -> None:
    projects = await ready.projects()
    gate = Gate(ready.fake.transport, "POST")
    app_of(ready.client).state.connectors.transport = gate
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "慢慢创建"}
    first = asyncio.create_task(ready.create_task(body, key))
    await asyncio.wait_for(gate.entered.wait(), timeout=5)
    duplicate = await ready.create_task(body, key)
    assert duplicate.status_code == 409
    assert error_code(duplicate) == "IDEMPOTENCY_IN_PROGRESS"
    assert duplicate.headers["Retry-After"] == "1"
    gate.release.set()
    assert (await first).status_code == 201
    assert (await ready.create_task(body, key)).headers["Idempotent-Replayed"] == "true"
    assert len(ready.fake.calls("POST", r"/projects/\d+/tasks")) == 1


async def test_unknown_result_is_resolved_without_creating_twice(ready: Env) -> None:
    """请求已到达 Vikunja 但响应丢了：返回 504；用同一个键重发时查到已创建的任务。"""
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "响应丢失"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", "lose_response")
    lost = await ready.create_task(body, key)
    assert lost.status_code == 504
    assert error_code(lost) == "RESULT_UNKNOWN"
    assert lost.json()["error"]["details"] == {"retry_with_same_key": True}
    resolved = await ready.create_task(body, key)
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["title"] == "响应丢失"
    assert len(ready.fake.calls("POST", r"/projects/\d+/tasks")) == 1  # 没有再发一次创建
    assert [t["title"] for t in ready.fake.tasks.values()].count("响应丢失") == 1
    replay = await ready.create_task(body, key)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == resolved.json()


async def test_unknown_result_that_did_not_apply(ready: Env) -> None:
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "其实没建"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", "timeout_before")
    assert (await ready.create_task(body, key)).status_code == 504
    not_applied = await ready.create_task(body, key)
    assert not_applied.status_code == 409
    assert error_code(not_applied) == "OPERATION_NOT_APPLIED"
    again = await ready.create_task(body, key)
    assert (again.status_code, again.headers["Idempotent-Replayed"]) == (409, "true")
    # 用新的键重新提交才会真正创建
    assert (await ready.create_task(body)).status_code == 201
    assert len(ready.fake.calls("POST", r"/projects/\d+/tasks")) == 2


async def test_server_error_after_sending_is_treated_as_unknown(ready: Env) -> None:
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "5xx"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", 500)
    failed = await ready.create_task(body, key)
    assert failed.status_code == 503
    assert error_code(failed) == "EXTERNAL_UNAVAILABLE"
    # 不能确定有没有生效：同一个键重发先查询，查不到就是没生效
    assert error_code(await ready.create_task(body, key)) == "OPERATION_NOT_APPLIED"
    assert (await ready.integration())["status"] == "unavailable"


async def test_not_sent_can_be_retried_with_the_same_key(ready: Env) -> None:
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "连接失败"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", "connect")
    failed = await ready.create_task(body, key)
    assert failed.status_code == 503
    assert error_code(failed) == "EXTERNAL_UNAVAILABLE"
    assert (await ready.integration())["status"] == "unavailable"
    retried = await ready.create_task(body, key)
    assert retried.status_code == 201
    assert "Idempotent-Replayed" not in retried.headers
    assert (await ready.integration())["status"] == "active"  # 成功后恢复


async def test_unreachable_vikunja_marks_the_data_stale(ready: Env) -> None:
    """A05：第三方服务断开时首页任务模块显示 stale，其余组件照常；读取仍走投影。"""
    projects = await ready.projects()
    ready.fake.fault("POST", r"/projects/\d+/tasks", "connect")
    failed = await ready.create_task({"project_id": projects["工作"], "title": "断开时创建"})
    assert failed.status_code == 503
    page = await ready.get("/api/v1/tasks")
    assert page["sync"]["status"] == "stale"
    assert page["sync"]["reason"] == "source_unavailable"
    assert [t["title"] for t in page["items"]] == ["写周报"]
    overview = await ready.get("/api/v1/dashboard/overview")
    module = overview["modules"]["tasks"]
    assert (module["status"], module["reason"], module["retryable"]) == (
        "stale",
        "source_unavailable",
        True,
    )
    assert overview["summary"]["pending_tasks"] == 1  # stale 仍显示数据
    assert len(overview["widgets"]) == 4  # 其余组件照常
    # 恢复后第一次成功访问就回到 ok
    assert (
        await ready.create_task({"project_id": projects["工作"], "title": "恢复后"})
    ).status_code == 201
    assert (await ready.get("/api/v1/tasks"))["sync"]["status"] == "ok"


async def test_rate_limit_keeps_retry_after_and_allows_retry(ready: Env) -> None:
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "限流"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", 429, headers={"Retry-After": "7"})
    limited = await ready.create_task(body, key)
    assert limited.status_code == 503
    assert error_code(limited) == "EXTERNAL_RATE_LIMITED"
    assert limited.headers["Retry-After"] == "7"
    assert (await ready.create_task(body, key)).status_code == 201


async def test_vikunja_validation_errors_are_replayed(ready: Env) -> None:
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "被拒绝"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", 422)
    rejected = await ready.create_task(body, key)
    assert rejected.status_code == 422
    assert error_code(rejected) == "EXTERNAL_VALIDATION_FAILED"
    replay = await ready.create_task(body, key)
    assert (replay.status_code, replay.headers["Idempotent-Replayed"]) == (422, "true")
    assert error_code(replay) == "EXTERNAL_VALIDATION_FAILED"


async def test_create_in_an_unknown_project_is_404(ready: Env) -> None:
    response = await ready.create_task({"project_id": str(uuid.uuid4()), "title": "x"})
    assert response.status_code == 404
    assert ready.fake.calls("POST", r"/projects/\d+/tasks") == []


# ---------------------------------------------------------------- 修改


async def test_update_task_checks_the_version(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    key = uuid.uuid4()
    change = {"version": task["version"], "title": "写月报", "done": True, "due_at": None}
    updated = await ready.update_task(task["id"], change, key)
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["title"], body["done"], body["due_at"]) == ("写月报", True, None)
    assert body["done_at"] is not None
    assert body["version"] != task["version"]
    assert body["description"] == "**本周**"  # 没改的字段保持不变
    replay = await ready.update_task(task["id"], change, key)
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == body
    assert len(ready.fake.calls("PATCH", r"/tasks/\d+")) == 1

    stale = await ready.update_task(task["id"], {"version": task["version"], "priority": 1})
    assert stale.status_code == 409
    assert error_code(stale) == "VERSION_CONFLICT"
    details = stale.json()["error"]["details"]
    assert details["current_version"] == body["version"]
    assert details["current"]["title"] == "写月报"
    assert len(ready.fake.calls("PATCH", r"/tasks/\d+")) == 1


async def test_conflict_refreshes_the_projection(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    external = int(task["external_id"].rsplit(":", 1)[1])
    ready.fake.tasks[external].update(title="别人改了", updated=ready.fake.tick())
    stale = await ready.update_task(task["id"], {"version": task["version"], "priority": 2})
    assert stale.status_code == 409
    assert (await ready.get(f"/api/v1/tasks/{task['id']}"))["title"] == "别人改了"


async def test_move_task_to_another_project(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    projects = await ready.projects()
    moved = await ready.update_task(
        task["id"], {"version": task["version"], "project_id": projects["生活"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_id"] == projects["生活"]
    unknown = await ready.update_task(
        task["id"], {"version": moved.json()["version"], "project_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 404


async def test_unknown_update_result_is_checked_against_vikunja(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    key = uuid.uuid4()
    change = {"version": task["version"], "priority": 5}
    ready.fake.fault("PATCH", r"/tasks/\d+", "lose_response")
    assert (await ready.update_task(task["id"], change, key)).status_code == 504
    resolved = await ready.update_task(task["id"], change, key)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["priority"] == 5
    assert len(ready.fake.calls("PATCH", r"/tasks/\d+")) == 1

    other = await ready.task_by_title("写周报")
    key = uuid.uuid4()
    change = {"version": other["version"], "title": "没有生效"}
    ready.fake.fault("PATCH", r"/tasks/\d+", "timeout_before")
    assert (await ready.update_task(other["id"], change, key)).status_code == 504
    not_applied = await ready.update_task(other["id"], change, key)
    assert error_code(not_applied) == "OPERATION_NOT_APPLIED"


async def test_task_deleted_in_vikunja_becomes_404(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    ready.fake.tasks.clear()
    response = await ready.update_task(task["id"], {"version": task["version"], "priority": 1})
    assert response.status_code == 404
    gone = await ready.client.get(f"/api/v1/tasks/{task['id']}", headers=ready.headers())
    assert gone.status_code == 404  # 投影已记 tombstone
    assert await ready.tasks(status="all") == []


# ---------------------------------------------------------------- 删除


async def test_delete_task_is_idempotent(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    key = uuid.uuid4()
    deleted = await ready.delete_task(task["id"], task["version"], key)
    assert deleted.status_code == 204
    assert ready.fake.tasks == {}
    gone = await ready.client.get(f"/api/v1/tasks/{task['id']}", headers=ready.headers())
    assert gone.status_code == 404
    replay = await ready.delete_task(task["id"], task["version"], key)
    assert (replay.status_code, replay.headers["Idempotent-Replayed"]) == (204, "true")
    # 换一个键再删：投影已经知道它被删除了，同样是 204，不再访问 Vikunja
    before = len(ready.fake.requests)
    assert (await ready.delete_task(task["id"], task["version"])).status_code == 204
    assert len(ready.fake.requests) == before
    assert len(ready.fake.calls("DELETE", r"/tasks/\d+")) == 1
    missing = await ready.delete_task(str(uuid.uuid4()), "v")
    assert missing.status_code == 404


async def test_delete_when_already_gone_in_vikunja(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    ready.fake.tasks.clear()
    assert (await ready.delete_task(task["id"], task["version"])).status_code == 204
    assert ready.fake.calls("DELETE", r"/tasks/\d+") == []


async def test_delete_checks_the_version_and_unknown_results(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    stale = await ready.delete_task(task["id"], "2000-01-01T00:00:00Z")
    assert stale.status_code == 409
    assert error_code(stale) == "VERSION_CONFLICT"
    key = uuid.uuid4()
    ready.fake.fault("DELETE", r"/tasks/\d+", "timeout_before")
    assert (await ready.delete_task(task["id"], task["version"], key)).status_code == 504
    not_applied = await ready.delete_task(task["id"], task["version"], key)
    assert error_code(not_applied) == "OPERATION_NOT_APPLIED"
    assert len(ready.fake.tasks) == 1

    key = uuid.uuid4()
    ready.fake.fault("DELETE", r"/tasks/\d+", "lose_response")
    assert (await ready.delete_task(task["id"], task["version"], key)).status_code == 504
    assert (await ready.delete_task(task["id"], task["version"], key)).status_code == 204


# ---------------------------------------------------------------- 外部错误与集成状态


async def test_revoked_token_is_502_and_marks_the_integration(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    ready.fake.token = "tk_" + "r" * 40
    response = await ready.update_task(task["id"], {"version": task["version"], "priority": 1})
    assert response.status_code == 502
    assert error_code(response) == "INTEGRATION_UNAUTHORIZED"
    assert (await ready.integration())["status"] == "unauthorized"
    page = await ready.get("/api/v1/tasks")
    assert page["sync"]["status"] == "unauthorized"
    assert [t["title"] for t in page["items"]] == ["写周报"]  # 投影里的数据仍可读
    overview = await ready.get("/api/v1/dashboard/overview")
    assert overview["modules"]["tasks"]["status"] == "unauthorized"
    assert overview["summary"]["pending_tasks"] is None


async def test_rejected_token_on_create_can_be_retried_with_the_same_key(ready: Env) -> None:
    projects = await ready.projects()
    key = uuid.uuid4()
    body = {"project_id": projects["工作"], "title": "换 Token 后重试"}
    ready.fake.fault("POST", r"/projects/\d+/tasks", 401)
    rejected = await ready.create_task(body, key)
    assert rejected.status_code == 502
    assert error_code(rejected) == "INTEGRATION_UNAUTHORIZED"
    assert (await ready.integration())["status"] == "unauthorized"
    # Vikunja 没有处理这个请求：不保留结果，Token 恢复后同一个键照常创建
    retried = await ready.create_task(body, key)
    assert retried.status_code == 201, retried.text
    assert "Idempotent-Replayed" not in retried.headers
    assert (await ready.integration())["status"] == "active"


async def test_forbidden_resource_is_403_without_marking_the_integration(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    ready.fake.fault("GET", r"/tasks/\d+", 403)
    response = await ready.update_task(task["id"], {"version": task["version"], "priority": 1})
    assert response.status_code == 403
    assert error_code(response) == "EXTERNAL_FORBIDDEN"
    assert (await ready.integration())["status"] == "active"


async def test_writes_need_a_tested_integration(ready: Env) -> None:
    integration = await ready.integration()
    rotated = await ready.client.patch(
        f"/api/v1/integrations/{integration['id']}",
        json={"version": integration["version"], "api_token": "tk_" + "n" * 40},
        headers=ready.headers(write=True),
    )
    assert rotated.json()["status"] == "pending"
    task = await ready.task_by_title("写周报")
    key = uuid.uuid4()
    response = await ready.update_task(task["id"], {"version": task["version"], "priority": 1}, key)
    assert response.status_code == 409
    assert error_code(response) == "INTEGRATION_UNAVAILABLE"
    # 没有调用 Vikunja，也没有留下幂等记录：测试通过后用同一个键可以重试
    assert ready.fake.calls("PATCH", r"/tasks/\d+") == []
    assert (
        await run_sql(ready.database_url, "SELECT count(*) FROM workspace.idempotency_requests")
        == 0
    )


# ---------------------------------------------------------------- 权限


async def test_viewer_reads_member_writes(ready: Env) -> None:
    task = await ready.task_by_title("写周报")
    projects = await ready.projects()
    async with ready.member("viewer@example.com", "viewer") as viewer:
        headers = {"X-Workspace-Id": ready.workspace_id, "X-CSRF-Token": viewer.cookies["ws_csrf"]}
        assert (await viewer.get("/api/v1/tasks", headers=headers)).status_code == 200
        denied = await viewer.post(
            "/api/v1/tasks",
            json={"project_id": projects["工作"], "title": "viewer"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert denied.status_code == 403
        denied_delete = await viewer.delete(
            f"/api/v1/tasks/{task['id']}",
            params={"version": task["version"]},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert denied_delete.status_code == 403
    assert ready.fake.calls("POST", r"/projects/\d+/tasks") == []
    async with ready.member("member@example.com", "member") as member:
        headers = {
            "X-Workspace-Id": ready.workspace_id,
            "X-CSRF-Token": member.cookies["ws_csrf"],
            "Idempotency-Key": str(uuid.uuid4()),
        }
        created = await member.post(
            "/api/v1/tasks",
            json={"project_id": projects["工作"], "title": "member"},
            headers=headers,
        )
        assert created.status_code == 201, created.text


async def test_other_workspaces_cannot_see_tasks(ready: Env, make_client: ClientFactory) -> None:
    task = await ready.task_by_title("写周报")
    bob_id = await add_user(ready.database_url, BOB["email"], BOB["password"])
    bob_workspace = await add_workspace(ready.database_url, bob_id, "Bob 的空间")
    async with make_client() as bob:
        assert (await bob.post("/api/v1/auth/login", json=BOB)).status_code == 200
        own = {"X-Workspace-Id": str(bob_workspace)}
        assert (await bob.get(f"/api/v1/tasks/{task['id']}", headers=own)).status_code == 404
        assert (await bob.get("/api/v1/tasks", headers=own)).json()["items"] == []
        foreign = {"X-Workspace-Id": ready.workspace_id}
        assert (await bob.get("/api/v1/tasks", headers=foreign)).status_code == 404


# ---------------------------------------------------------------- 首页任务模块


async def test_overview_tasks_module_reads_the_projection(env: Env) -> None:
    work = env.fake.add_project("工作")
    env.fake.add_project("归档", archived=True)
    now = datetime.now(UTC)
    env.fake.add_task(work["id"], "今天", due_date=_stamp(now.replace(hour=0, minute=0, second=1)))
    env.fake.add_task(work["id"], "逾期", due_date=_stamp(now - timedelta(days=3)))
    env.fake.add_task(work["id"], "以后", due_date=_stamp(now + timedelta(days=3)))
    env.fake.add_task(work["id"], "完成了", done=True, due_date=_stamp(now))
    await env.connect()
    overview = await env.get("/api/v1/dashboard/overview")
    module = overview["modules"]["tasks"]
    assert (module["status"], module["retryable"]) == ("ok", True)
    assert module["as_of"] is not None
    assert overview["summary"]["pending_tasks"] == 3
    assert overview["summary"]["active_projects"] == 1
    today = overview["today_tasks"]
    assert [t["title"] for t in today] == ["逾期", "今天"]
    assert {t["project_title"] for t in today} == {"工作"}
    assert all(t["source_url"].startswith("https://tasks.example.com/") for t in today)


async def test_overview_marks_old_data_as_stale(ready: Env) -> None:
    await run_sql(
        ready.database_url,
        "UPDATE workspace.sync_cursors SET last_success_at = now() - interval '1 hour'",
    )
    overview = await ready.get("/api/v1/dashboard/overview")
    assert overview["modules"]["tasks"]["status"] == "stale"
    assert overview["modules"]["tasks"]["reason"] == "sync_overdue"
    assert overview["summary"]["pending_tasks"] == 1  # stale 仍然显示数据
    assert (await ready.get("/api/v1/tasks"))["sync"]["status"] == "stale"
