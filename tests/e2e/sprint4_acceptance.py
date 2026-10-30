#!/usr/bin/env python3
"""Sprint 4 全栈验收（T-0032）：Compose 全栈 + 模拟 GitHub，验证 DoD 与 A10 ~ A13、A17（接口级）。

DoD 见 docs/DEVELOPMENT_SPEC.md 第 13 节，验收场景见第 15 节：

- GitHub 只读：连接、逐仓库权限检查、首次同步；
  开发者页的仓库 / Issue（不含 PR）/ PR / 运行与故障摘要
- A10：GitHub 上删除的 Issue 在全量同步后从列表与搜索里消失；
  被关联的任务写 tombstone 后，收藏里不留僵尸引用；删除日程后搜不到
- A17：GitHub 主限流（403）与次级限流（429 + retry-after）时列表照常显示投影，
  sync 标明 rate_limited 与恢复时间；恢复前 Worker 不再请求 GitHub
- A13：Token 失效后集成变为 unauthorized，页面提示重新连接，Worker 不再重试
- A12：169.254.169.254 等危险地址作为集成地址、AFFiNE 地址或收藏链接都被拒绝
- A11 与 DoD“搜索结果只显示可见资源”：别人的私有收藏、其他工作空间的数据都搜不到
- DoD“无未授权文档正文抓取”：AFFiNE 地址指向模拟服务，验收结束时它没有收到任何请求

模拟 GitHub（fake_github_server.py）复用后端测试的内存版 GitHub，运行在 api 镜像里，
加入 integrations 网络。前提：Compose 全栈已启动并迁移，.env 里
GITHUB_API_URL=http://fake-github:8080、INTEGRATION_ALLOWED_HOSTS 含 fake-github、
RECONCILE_INTERVAL_MINUTES=1（CI 的 ci-infra.yml 这样设置）。
不要求空库：每次运行用新的工作台用户。
用法：python3 tests/e2e/sprint4_acceptance.py [--base-url http://127.0.0.1:8080]
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py、seed.py。
"""

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from e2e_support import (
    ROOT,
    Client,
    Report,
    compose_exec,
    default_base_url,
    env_value,
    parse_base_url,
)
from seed import add_member, create_user

PASSWORD = "e2e sprint4 passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
FAKE_NAME = "ws-e2e-fake-github"
FAKE_PORT = 18090
FAKE_REPO = "e2e/workspace"
FAKE_AFFINE = "http://fake-github:8080/affine"
SYNC_TIMEOUT = 60
RECONCILE_WAIT = 75  # CI 中每分钟对账一次：等过一轮


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 -- 固定的 docker 命令
        list(args), cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} 失败：{result.stderr.strip()[-400:]}")
    return result.stdout.strip()


def project_name() -> str:
    return env_value("COMPOSE_PROJECT_NAME") or "my-workspace"


def start_fake(token: str) -> None:
    """在 api 镜像里启动模拟 GitHub：在默认网络发布控制端口，再加入 integrations 网络。

    在 integrations 网络里的别名是 fake-github（Worker 用 GITHUB_API_URL 访问它）。
    """
    run("docker", "rm", "-f", FAKE_NAME, check=False)
    api = run("docker", "compose", "ps", "-q", "api")
    image = run("docker", "inspect", "--format", "{{.Config.Image}}", api)
    run(
        "docker",
        "run",
        "-d",
        "--name",
        FAKE_NAME,
        "-p",
        f"127.0.0.1:{FAKE_PORT}:8080",
        "-e",
        f"FAKE_GITHUB_TOKEN={token}",
        "-e",
        f"FAKE_GITHUB_REPO={FAKE_REPO}",
        "-v",
        f"{ROOT}/services/api/tests:/fake/tests:ro",
        "-v",
        f"{ROOT}/tests/e2e/fake_github_server.py:/fake/server.py:ro",
        "--entrypoint",
        "python",
        image,
        "/fake/server.py",
    )
    run(
        "docker",
        "network",
        "connect",
        "--alias",
        "fake-github",
        f"{project_name()}_integrations",
        FAKE_NAME,
    )


def fake_call(method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{FAKE_PORT}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw) if raw else None


def fake_ready() -> bool:
    try:
        return fake_call("GET", "/_fake/stats") is not None
    except (OSError, urllib.error.URLError):
        return False


def wait_for[T](
    predicate: Callable[[], T | None], timeout: float, interval: float = 1.0
) -> T | None:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def sql(query: str) -> str:
    user = env_value("POSTGRES_USER", "workspace") or "workspace"
    database = env_value("POSTGRES_DB", "workspace") or "workspace"
    return compose_exec("postgres", "psql", "-U", user, "-d", database, "-tAc", query).strip()


def literal(value: str) -> str:
    """拼进 SQL 的值只允许本脚本生成的 UUID 或十六进制串，先校验格式。"""
    if not re.fullmatch(r"[0-9a-f-]{32,64}", value):
        raise ValueError(f"不是 UUID 或十六进制串：{value!r}")
    return f"'{value}'"


# 模拟 Vikunja 同步写入的任务投影（A10 / A11 用）；拼进 SQL 的值都经过 literal() 校验
_INTEGRATION = (
    "INSERT INTO workspace.integrations"
    " (workspace_id, provider, name, base_url, public_url, status)"
    " VALUES ({}, 'vikunja', 'e2e-vikunja', 'http://vikunja:3456', 'http://vikunja:3456',"
    " 'active')"
    " ON CONFLICT (workspace_id, provider, name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
)
_TASK = (
    "INSERT INTO workspace.external_resources"
    " (workspace_id, integration_id, resource_type, external_id, title, source_url, metadata_json)"
    " VALUES ({}, {}, 'task', {}, '验收发布清单', 'http://vikunja:3456/tasks/1',"
    ' \'{{"description": ""}}\'::jsonb) RETURNING id'
)
_TOMBSTONE = "UPDATE workspace.external_resources SET deleted_at = now() WHERE id = {}"
_AUDIT = (
    "SELECT coalesce(string_agg(action || ' ' || metadata_redacted_json::text, E'\\n'), '')"
    " FROM workspace.audit_logs WHERE workspace_id = {}"
)


def returning(query: str) -> str:
    """INSERT … RETURNING 的第一行（psql 在结果之后还会输出 INSERT 0 1）。"""
    return sql(query).splitlines()[0]


def vikunja_task(workspace_id: str) -> str:
    """在 workspace 里写一条任务投影，返回它的 UUID（没有凭据，Worker 不会同步这个集成）。"""
    ws = literal(workspace_id)
    integration = returning(_INTEGRATION.format(ws))
    return returning(_TASK.format(ws, literal(integration), literal(uuid.uuid4().hex)))


def quote(value: str) -> str:
    return urllib.parse.quote(value)


def seconds_until(value: str | None) -> float | None:
    """接口返回的 retry_at 距现在还有多少秒。"""
    if not value:
        return None
    return (datetime.fromisoformat(value) - datetime.now(UTC)).total_seconds()


def login(base: str, email: str) -> Client:
    client = Client(base)
    response = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if response.status != 200:
        sys.exit(f"登录失败：{response.summary}")
    return client


# ---------------------------------------------------------------- 验收


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    args = parser.parse_args()
    base = parse_base_url(args.base_url)
    report = Report()
    print(f"Sprint 4 全栈验收：{base}")
    tag = uuid.uuid4().hex[:8]
    token = "github_pat_e2e_" + secrets.token_hex(20)
    start_fake(token)
    if not wait_for(fake_ready, 30):
        sys.exit("模拟 GitHub 没有就绪")
    try:
        code = run_checks(base, report, tag, token)
    finally:
        run("docker", "rm", "-f", FAKE_NAME, check=False)
    return code


def run_checks(base: str, report: Report, tag: str, token: str) -> int:
    """按场景顺序写成一个流程，便于对照验收报告。"""
    current = compose_exec("api", "alembic", "current")
    report.check("迁移在最新版本（alembic current 为 head）", "(head)" in current, current.strip())

    owner_email = f"owner4-{tag}@e2e.example.com"
    _, workspace, _ = create_user(owner_email, "Owner", PASSWORD)
    viewer_email = f"viewer4-{tag}@e2e.example.com"
    viewer_id, _, _ = create_user(viewer_email, "Viewer", PASSWORD)
    add_member(workspace, viewer_id, "viewer")
    outsider_email = f"outsider4-{tag}@e2e.example.com"
    _, outsider_ws, _ = create_user(outsider_email, "Outsider", PASSWORD)
    owner = login(base, owner_email)
    ws = {"X-Workspace-Id": workspace}

    def get(path: str, client: Client = owner, headers: dict[str, str] | None = None) -> Any:
        response = client.request("GET", path, headers=headers or ws)
        if response.status != 200:
            raise RuntimeError(f"GET {path} 失败：{response.summary}")
        return response.json()

    # ------------------------------------------------ GitHub：连接与首次同步（S4-04）
    created = owner.request(
        "POST",
        "/api/v1/integrations",
        {"provider": "github", "name": "GitHub", "api_token": token, "repositories": [FAKE_REPO]},
        headers=ws,
    )
    integration = created.json() if created.status == 201 else {}
    report.check(
        "新建 GitHub 集成 201，响应不含 Token，地址固定",
        created.status == 201
        and token not in created.text
        and integration.get("base_url") == "https://api.github.com",
        created.summary,
    )
    integration_id = str(integration.get("id"))
    tested = owner.request("POST", f"/api/v1/integrations/{integration_id}/test", headers=ws)
    result = tested.json() if tested.status == 200 else {}
    repo_checks = (result.get("repositories") or [{}])[0].get("checks") or []
    report.check(
        "测试连接通过：可达、版本、Token，逐仓库四项读权限",
        bool(result.get("ok"))
        and result.get("status") == "active"
        and {c["name"] for c in repo_checks} == {"metadata", "issues", "pull_requests", "actions"},
        tested.summary,
    )
    issues = get("/api/v1/developer/issues?state=all")
    pulls = get("/api/v1/developer/pulls?state=all")
    runs = get("/api/v1/developer/runs")
    repos = get("/api/v1/developer/repos")
    report.check(
        "首次同步：仓库、Issue（不含 PR）、PR、Actions 运行都在开发者页",
        [r["full_name"] for r in repos["items"]] == [FAKE_REPO]
        and [i["title"] for i in issues["items"]] == ["验收：登录失败"]
        and [p["title"] for p in pulls["items"]] == ["验收：修复登录"]
        and len(runs["items"]) == 2,
        f"repos={len(repos['items'])} issues={len(issues['items'])} runs={len(runs['items'])}",
    )
    failed = [r for r in runs["items"] if r["conclusion"] == "failure"]
    report.check(
        "失败的运行带故障摘要（失败的 job 与 step 名称）",
        bool(failed)
        and failed[0]["failure_summary"]
        == {"failed_jobs": [{"name": "test", "failed_steps": ["Run pytest"]}]},
    )
    report.check("开发者页 sync 为 ok", issues["sync"]["status"] == "ok", str(issues["sync"]))

    def manual_sync() -> int:
        return owner.request(
            "POST", f"/api/v1/integrations/{integration_id}/sync", headers=ws
        ).status

    # ------------------------------------------------ A10：GitHub 上删除的 Issue
    fake_call(
        "POST", "/_fake/control", {"delete_issue": {"repo": FAKE_REPO, "title": "验收：登录失败"}}
    )
    report.check("手动同步排队 202", manual_sync() == 202)
    gone = wait_for(lambda: get("/api/v1/developer/issues?state=all")["items"] == [], SYNC_TIMEOUT)
    hits = get("/api/v1/search?q=" + quote("登录失败"))["items"]
    report.check(
        "A10：GitHub 上删除的 Issue 在全量同步后从开发者页与搜索中消失",
        bool(gone) and not [h for h in hits if h["resource_type"] == "issue"],
    )

    # ------------------------------------------------ A17：主限流（403 + x-ratelimit-remaining: 0）
    fake_call("POST", "/_fake/control", {"rate_limit": "primary", "reset_in": 80})
    manual_sync()

    def limited() -> dict[str, Any] | None:
        sync = get("/api/v1/developer/pulls?state=all")["sync"]
        return sync if sync.get("reason") == "rate_limited" else None

    sync = wait_for(limited, SYNC_TIMEOUT) or {}
    pulls = get("/api/v1/developer/pulls?state=all")
    report.check(
        "A17：限流时列表照常显示投影，sync 为 stale + rate_limited 并带恢复时间",
        sync.get("status") == "stale" and bool(sync.get("retry_at")) and len(pulls["items"]) == 1,
        str(sync),
    )
    overview = get("/api/v1/dashboard/overview")["modules"].get("github", {})
    report.check(
        "A17：首页 github 模块同样标明限流与恢复时间",
        overview.get("reason") == "rate_limited" and bool(overview.get("retry_at")),
        str(overview),
    )
    before = fake_call("GET", "/_fake/stats")["github"]
    manual_sync()
    time.sleep(8)
    after = fake_call("GET", "/_fake/stats")["github"]
    report.check(
        "A17：恢复时间之前 Worker 不再请求 GitHub（避免被封禁）",
        after == before,
        f"请求数 {before} → {after}",
    )
    fake_call("POST", "/_fake/control", {"rate_limit": None})
    time.sleep(max(0, 82 - 8))

    def synced_after(as_of: str | None) -> Callable[[], bool]:
        """反复触发同步，直到出现一次新的成功同步（as_of 晚于限流前的那次）。

        只看 status 不够：恢复时间一过，上一次成功同步还没过期，status 就会回到 ok。
        """
        baseline = datetime.fromisoformat(as_of) if as_of else datetime.min.replace(tzinfo=UTC)

        def check() -> bool:
            manual_sync()
            state = get("/api/v1/developer/pulls?state=all")["sync"]
            newer = bool(state["as_of"]) and datetime.fromisoformat(state["as_of"]) > baseline
            return state["status"] == "ok" and newer

        return check

    report.check(
        "限流结束后有新的成功同步，sync 回到 ok",
        bool(wait_for(synced_after(sync.get("as_of")), SYNC_TIMEOUT, 5)),
    )

    # ------------------------------------------------ A17：次级限流（429 + retry-after: 30）
    fake_call("POST", "/_fake/control", {"rate_limit": "secondary"})
    manual_sync()
    sync = wait_for(limited, SYNC_TIMEOUT) or {}
    wait = seconds_until(sync.get("retry_at"))
    report.check(
        "A17：次级限流同样标明 rate_limited，恢复时间按 retry-after（30 秒）计算",
        sync.get("status") == "stale" and wait is not None and 0 < wait <= 31,
        f"{sync}，还要等 {wait} 秒",
    )
    fake_call("POST", "/_fake/control", {"rate_limit": None})
    time.sleep(max(0.0, (wait or 0) + 2))
    report.check(
        "次级限流结束后有新的成功同步",
        bool(wait_for(synced_after(sync.get("as_of")), SYNC_TIMEOUT, 5)),
    )

    # ------------------------------------------------ A13：Token 失效
    fake_call("POST", "/_fake/control", {"token": "github_pat_" + secrets.token_hex(20)})
    manual_sync()

    def unauthorized() -> bool:
        return get("/api/v1/developer/issues")["sync"]["status"] == "unauthorized"

    report.check(
        "A13：Token 失效后开发者页 sync 为 unauthorized（提示重新连接）",
        bool(wait_for(unauthorized, SYNC_TIMEOUT)),
    )
    status = next(i for i in get("/api/v1/integrations")["items"] if i["id"] == integration_id)
    report.check(
        "A13：集成状态为 unauthorized", status["status"] == "unauthorized", status["status"]
    )
    before = fake_call("GET", "/_fake/stats")["github"]
    time.sleep(RECONCILE_WAIT)
    after = fake_call("GET", "/_fake/stats")["github"]
    report.check(
        "A13：之后的周期对账不再请求 GitHub（不无限重试）",
        after == before,
        f"请求数 {before} → {after}",
    )

    # ------------------------------------------------ A12：危险地址
    metadata_url = "http://169.254.169.254/latest/meta-data"
    vikunja = owner.request(
        "POST",
        "/api/v1/integrations",
        {
            "provider": "vikunja",
            "name": "危险",
            "base_url": metadata_url,
            "api_token": "tk_" + "a" * 32,
        },
        headers=ws,
    )
    source_bad = owner.request(
        "POST",
        "/api/v1/knowledge/sources",
        {"provider": "affine", "name": "危险", "base_url": "http://169.254.169.254"},
        headers=ws,
    )
    report.check(
        "A12：169.254.169.254 作为集成地址或 AFFiNE 地址都被拒绝（422），不回显地址",
        vikunja.status == 422
        and source_bad.status == 422
        and "169.254" not in vikunja.text + source_bad.text,
        f"{vikunja.status} / {source_bad.status}",
    )

    # ------------------------------------------------ 知识入口（S4-01）与 A10 关联
    source = owner.request(
        "POST",
        "/api/v1/knowledge/sources",
        {"provider": "affine", "name": "AFFiNE", "base_url": FAKE_AFFINE},
        headers=ws,
    )
    report.check("配置 AFFiNE 地址 201", source.status == 201, source.summary)
    bad_links = [
        "javascript:alert(1)",
        "http://169.254.169.254/workspace/ws/doc",
        f"{FAKE_AFFINE}/workspace/ws/all",
    ]
    reasons = []
    for url in bad_links:
        response = owner.request(
            "POST", "/api/v1/knowledge/bookmarks", {"url": url, "title": "x"}, headers=ws
        )
        reasons.append(
            response.json()["error"]["details"].get("reason") if response.status == 422 else None
        )
    report.check(
        "A12：脚本链接、元数据地址（不属于 AFFiNE 地址）、非文档页面的收藏都被拒绝并说明原因",
        reasons == ["scheme_not_allowed", "host_mismatch", "not_a_document"],
        str(reasons),
    )
    task_id = vikunja_task(workspace)
    bookmark = owner.request(
        "POST",
        "/api/v1/knowledge/bookmarks",
        {
            "url": f"{FAKE_AFFINE}/workspace/ws1/doc1?mode=page#x",
            "title": "验收发布手册",
            "note": "上线前检查",
            "linked_resource_ids": [task_id],
        },
        headers=ws,
    )
    saved = bookmark.json() if bookmark.status == 201 else {}
    report.check(
        "收藏 AFFiNE 文档：链接规范化（保留 mode、去掉片段），关联任务",
        bookmark.status == 201
        and saved.get("url") == f"{FAKE_AFFINE}/workspace/ws1/doc1?mode=page"
        and [link["resource_id"] for link in saved.get("links", [])] == [task_id],
        bookmark.summary,
    )
    private = owner.request(
        "POST",
        "/api/v1/knowledge/bookmarks",
        {"url": f"{FAKE_AFFINE}/share/ws1/doc2", "title": "验收私人笔记", "visibility": "private"},
        headers=ws,
    )
    report.check("私有收藏 201", private.status == 201, private.summary)
    sql(_TOMBSTONE.format(literal(task_id)))
    relisted = get("/api/v1/knowledge/resources")["items"]
    linked = [b for b in relisted if b["id"] == saved.get("id")]
    report.check(
        "A10：被关联的任务写 tombstone 后，收藏里不留僵尸引用，搜索也不再返回它",
        bool(linked)
        and linked[0]["links"] == []
        and not get("/api/v1/search?q=" + quote("发布清单"))["items"],
    )

    # ------------------------------------------------ 本地日历（S4-02）
    event = owner.request(
        "POST",
        "/api/v1/calendar/events",
        {
            "title": "验收发布评审",
            "timezone": "Asia/Shanghai",
            "start_at": "2026-11-02T10:00:00+08:00",
            "end_at": "2026-11-02T11:00:00+08:00",
        },
        headers=ws,
    )
    event_data = event.json() if event.status == 201 else {}
    window = urllib.parse.urlencode(
        {"start": "2026-11-01T00:00:00+08:00", "end": "2026-11-08T00:00:00+08:00"}
    )
    agenda = get(f"/api/v1/calendar/events?{window}")
    report.check(
        "日历：新建事件存 UTC，区间查询返回",
        event_data.get("start_at") == "2026-11-02T02:00:00Z"
        and [e["id"] for e in agenda["items"]] == [event_data.get("id")],
        event.summary,
    )

    # ------------------------------------------------ 搜索与可见性（S4-03、S4-05、A11）
    vikunja_task(outsider_ws)  # 与已删除的任务同名，只存在于别人的工作空间
    results = get("/api/v1/search?q=" + quote("验收"))["items"]
    kinds = {r["resource_type"] for r in results}
    titles = {r["title"] for r in results}
    report.check(
        "搜索跨资源：收藏、日程、GitHub 仓库 / PR 都能搜到",
        {"bookmark", "event", "pull", "repo"} <= kinds,
        str(sorted(kinds)),
    )
    report.check(
        "A11：搜不到另一个工作空间的数据（同名任务只存在于别人的工作空间）",
        "验收发布清单" not in titles,
    )
    viewer = login(base, viewer_email)
    viewer_titles = {
        r["title"] for r in get("/api/v1/search?q=" + quote("验收"), client=viewer)["items"]
    }
    report.check(
        "A11：viewer 搜不到别人的私有收藏，但能搜到工作空间可见的内容",
        "验收私人笔记" not in viewer_titles and "验收发布手册" in viewer_titles,
        str(sorted(viewer_titles)),
    )
    outsider = login(base, outsider_email)
    foreign = outsider.request("GET", "/api/v1/search?q=x", headers=ws)
    report.check("A11：非成员搜索别人的工作空间返回 404", foreign.status == 404, foreign.summary)
    deleted = owner.request(
        "DELETE",
        f"/api/v1/calendar/events/{event_data.get('id')}?version={event_data.get('version')}",
        headers=ws,
    )
    after_delete = get("/api/v1/search?q=" + quote("发布评审"))["items"]
    report.check("A10：删除日程后搜索不再返回", deleted.status == 204 and not after_delete)

    # ------------------------------------------------ DoD：无未授权文档正文抓取
    stats = fake_call("GET", "/_fake/stats")
    affine_requests = [p for p in stats["other"] if p.startswith("/affine")]
    report.check(
        "DoD：工作台从未请求 AFFiNE 地址（收藏只做字符串校验，不抓取正文）",
        affine_requests == [],
        str(affine_requests),
    )
    audit = sql(_AUDIT.format(literal(workspace)))
    report.check(
        "审计日志记录了集成、收藏、日程的写操作，且不含 Token、标题、备注与链接",
        "bookmark.created" in audit
        and "calendar_event.created" in audit
        and "integration.created" in audit
        and token not in audit
        and "验收发布手册" not in audit
        and "上线前检查" not in audit
        and "fake-github" not in audit,
    )
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
