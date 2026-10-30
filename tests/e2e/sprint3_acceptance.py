#!/usr/bin/env python3
"""Sprint 3 全栈验收（T-0023）：Compose 全栈 + 真实 Vikunja 2.7.1，验证 DoD 与 A05 ~ A09（接口级）。

DoD 见 docs/DEVELOPMENT_SPEC.md 第 13 节，验收场景见第 15 节：

- 连接：受限 Token 保存后不回显；测试连接逐项通过；首次同步带来项目与任务；Worker 自动注册 Webhook
- DoD：在 Vikunja 里直接修改 / 新建 / 删除任务，经 Webhook 出现在工作台
- A06：同一 Idempotency-Key 重放，Vikunja 里只有一个任务；A07：同键不同请求 409
- 经工作台修改与删除任务（version 校验、旧版本 409）
- A08：同一个已签名的事件投递两次，Inbox 只记录一次、只处理一次；签名错误 401
- A09：停掉 caddy 让 Webhook 丢失，周期对账（CI 中 RECONCILE_INTERVAL_MINUTES=1）最终纠正投影
- A05：停掉 Vikunja，首页任务模块 stale、其余组件照常，写入 503；恢复后手动同步回到 ok
- 审计日志不含 Token 与任务内容
浏览器部分（连接 Vikunja 的页面、任务视图）在 T-0022 之后补充。

前提：Compose 全栈已启动并迁移（make up && make migrate），Vikunja 已启动（make vikunja-up）。
不要求空库：每次运行用新的工作台用户、Vikunja 用户与项目。
用法：python3 tests/e2e/sprint3_acceptance.py [--base-url http://127.0.0.1:8080]
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py、seed.py；CI（ci-infra.yml）与本机共用。
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from e2e_support import (
    ROOT,
    Client,
    Report,
    Response,
    compose_exec,
    default_base_url,
    env_value,
    parse_base_url,
)
from seed import create_user

PASSWORD = "e2e sprint3 passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
VIKUNJA_PASSWORD = "e2e-vikunja-" + uuid.uuid4().hex  # 只用于本次运行创建的 Vikunja 用户
# Token 的权限：核验 ADR 第 3 节的最小权限，加上自动注册 Webhook 需要的 projects_webhooks
TOKEN_PERMISSIONS = {
    "projects": ["read_all", "read_one"],
    "tasks": ["create", "read_all", "read_one", "update", "delete"],
    "projects_webhooks": ["create", "read_all", "update", "delete"],
    "labels": ["read_all"],
}
WEBHOOK_TIMEOUT = 30  # Webhook 路径：实测约 1 秒
RECONCILE_TIMEOUT = 150  # 周期对账：CI 中每分钟一次

# 在 api 容器里运行：取出该集成加密保存的 Webhook 密钥，对请求体签名，只输出签名
SIGN_SCRIPT = r"""
import asyncio, base64, hashlib, hmac, os, sys
from sqlalchemy import text
from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain.connectors import webhook_context
from app.domain.credentials import CredentialBox

async def main():
    settings = get_settings()
    engine = create_engine(settings)
    async with engine.connect() as conn:
        ref = (await conn.execute(
            text("SELECT webhook_secret_ref FROM workspace.integrations WHERE id = :id"),
            {"id": sys.argv[1]},
        )).scalar_one()
    await engine.dispose()
    box = CredentialBox.from_settings(settings)
    secret = box.resolve(ref, context=webhook_context(sys.argv[1])).get_secret_value()
    body = base64.b64decode(os.environ["E2E_BODY"])
    print(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())

asyncio.run(main())
"""


# ---------------------------------------------------------------- 工具


def http(
    method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None
) -> Response:
    # url 来自 --base-url（已限定 http(s)）或本机的 Vikunja 端口
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return Response(resp.status, resp.read(), resp.headers)
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read(), error.headers)


def reachable(url: str) -> bool:
    """服务重启期间连接会被拒绝或重置：当作还没就绪。"""
    try:
        return http("GET", url).status == 200
    except OSError:
        return False


class Vikunja:
    """直接访问 Vikunja（模拟用户在原生界面里的操作）。"""

    def __init__(self, base: str) -> None:
        self.api = f"{base}/api/v2"
        self.auth: dict[str, str] = {}

    def call(self, method: str, path: str, body: Any = None, *, merge: bool = False) -> Response:
        headers = {"Accept": "application/json", **self.auth}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            merge_patch = "application/merge-patch+json"
            headers["Content-Type"] = merge_patch if merge else "application/json"
        return http(method, self.api + path, data, headers)

    def json(self, method: str, path: str, body: Any = None, *, merge: bool = False) -> Any:
        response = self.call(method, path, body, merge=merge)
        if response.status >= 400:
            detail = f"{response.status} {response.text[:200]}"
            raise RuntimeError(f"Vikunja {method} {path} 失败：{detail}")
        return response.json()


def project_name() -> str:
    return os.environ.get("COMPOSE_PROJECT_NAME") or "my-workspace"


def vikunja_compose(*args: str) -> str:
    """与 Makefile 的 VIKUNJA_COMPOSE 相同：独立的项目名，加入 core 的 integrations 网络。"""
    command = [
        "docker",
        "compose",
        "-p",
        f"{project_name()}-vikunja",
        "-f",
        "infra/vikunja/compose.yaml",
        "--env-file",
        ".env",
        *args,
    ]
    env = {**os.environ, "WORKSPACE_PROJECT": project_name()}
    result = subprocess.run(  # noqa: S603 -- 固定的 docker compose 命令
        command, cwd=ROOT, capture_output=True, text=True, check=False, env=env
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} 失败：{result.stderr.strip()[-500:]}")
    return result.stdout


def core_compose(*args: str) -> None:
    result = subprocess.run(  # noqa: S603 -- 固定的 docker compose 命令
        ["docker", "compose", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} 失败：{result.stderr.strip()[-500:]}")


def sql(query: str) -> str:
    user = env_value("POSTGRES_USER", "workspace") or "workspace"
    database = env_value("POSTGRES_DB", "workspace") or "workspace"
    return compose_exec("postgres", "psql", "-U", user, "-d", database, "-tAc", query).strip()


def literal(value: str) -> str:
    """拼进 SQL 的值只允许本脚本生成的 UUID 或十六进制哈希，先校验格式。"""
    if not re.fullmatch(r"[0-9a-f-]{32,64}", value):
        raise ValueError(f"不是 UUID 或哈希：{value!r}")
    return f"'{value}'"


# 拼进 SQL 的值都先经过 literal() 校验（只允许 UUID 或十六进制哈希）
_INBOX_EVENT = "SELECT status || ' ' || attempts FROM workspace.webhook_inbox WHERE event_key = {}"
_INBOX_TOTAL = "SELECT count(*) FROM workspace.webhook_inbox WHERE integration_id = {}"
_AUDIT = (
    "SELECT string_agg(action || ' ' || metadata_redacted_json::text, E'\\n')"
    " FROM workspace.audit_logs WHERE workspace_id = {}"
    " AND (action LIKE 'task.%' OR action LIKE 'integration.%')"
)


def inbox_event(event_key: str) -> str:
    """该事件在 Inbox 里的“状态 次数”，每行一条。"""
    return sql(_INBOX_EVENT.format(literal(event_key)))


def inbox_total(integration_id: str) -> str:
    return sql(_INBOX_TOTAL.format(literal(integration_id)))


def audit_text(workspace_id: str) -> str:
    return sql(_AUDIT.format(literal(workspace_id)))


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


def stamp_ns() -> str:
    """Vikunja 投递里的 time：RFC 3339，纳秒。"""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}123Z"


# ---------------------------------------------------------------- 验收


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    parser.add_argument(
        "--vikunja-url",
        default=f"http://127.0.0.1:{env_value('VIKUNJA_PORT', '3456')}",
        help="从本机访问 Vikunja 的地址（模拟原生界面里的操作）",
    )
    args = parser.parse_args()
    base = parse_base_url(args.base_url)
    report = Report()
    print(f"Sprint 3 全栈验收：{base}，Vikunja {args.vikunja_url}")
    run = uuid.uuid4().hex[:8]

    current = compose_exec("api", "alembic", "current")
    report.check("迁移在最新版本（alembic current 为 head）", "(head)" in current, current.strip())
    vk = Vikunja(args.vikunja_url.rstrip("/"))
    info = vk.json("GET", "/info")
    report.check(
        "Vikunja 版本为锁定的 2.7.1", info.get("version") == "v2.7.1", str(info.get("version"))
    )

    # ---------------------------------------------------------------- Vikunja 用户与受限 Token
    username = f"e2e{run}"
    vikunja_compose(
        "exec",
        "-T",
        "vikunja",
        "/app/vikunja/vikunja",
        "user",
        "create",
        "-u",
        username,
        "-e",
        f"{username}@e2e.example.com",
        "-p",
        VIKUNJA_PASSWORD,
    )
    session = vk.json("POST", "/login", {"username": username, "password": VIKUNJA_PASSWORD})
    vk.auth = {"Authorization": f"Bearer {session['token']}"}
    expires = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = vk.json(
        "POST",
        "/tokens",
        {"title": f"workspace-e2e-{run}", "expires_at": expires, "permissions": TOKEN_PERMISSIONS},
    )["token"]
    project = vk.json("POST", "/projects", {"title": f"验收项目 {run}"})
    native = vk.json("POST", f"/projects/{project['id']}/tasks", {"title": "原生任务"})

    # ---------------------------------------------------------------- 工作台：连接 Vikunja（S3-02）
    email = f"owner-{run}@e2e.example.com"
    _, workspace, _ = create_user(email, "Owner", PASSWORD)
    owner = Client(base)
    login = owner.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if login.status != 200:
        sys.exit(f"登录失败：{login.summary}")
    ws = {"X-Workspace-Id": workspace}
    created = owner.request(
        "POST",
        "/api/v1/integrations",
        {
            "provider": "vikunja",
            "name": "Vikunja",
            "base_url": "http://vikunja:3456",
            "public_url": args.vikunja_url,
            "api_token": token,
        },
        headers=ws,
    )
    report.check(
        "保存集成 201，响应不含 Token",
        created.status == 201 and token not in created.text,
        created.summary,
    )
    if created.status != 201:
        return report.finish()
    integration_id = created.json()["id"]
    tested = owner.request("POST", f"/api/v1/integrations/{integration_id}/test", headers=ws).json()
    checks = {c["name"]: c["ok"] for c in tested["checks"]}
    report.check(
        "测试连接：可达、版本 2.7.1、Token、任务读权限、Webhook 权限都通过",
        tested["ok"]
        and tested["status"] == "active"
        and tested["compatible"]
        and all(checks.values()),
        json.dumps(checks),
    )

    def ws_tasks() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = owner.request(
            "GET", "/api/v1/tasks?status=all&limit=100", headers=ws
        ).json()["items"]
        return items

    def titles() -> set[str]:
        return {t["title"] for t in ws_tasks()}

    projects = owner.request("GET", "/api/v1/projects", headers=ws).json()["items"]
    project_uuid = next((p["id"] for p in projects if p["title"] == project["title"]), None)
    report.check("首次同步：项目出现在工作台", project_uuid is not None)
    report.check("首次同步：Vikunja 里已有的任务出现在工作台", "原生任务" in titles())

    target = f"http://caddy:8080/api/v1/webhooks/vikunja/{integration_id}"

    def registered() -> dict[str, Any] | None:
        """Vikunja 里有指向本集成的 Webhook，且 Worker 已记下注册结果。"""
        hooks = vk.json("GET", f"/projects/{project['id']}/webhooks").get("items") or []
        state: dict[str, Any] = owner.request("GET", "/api/v1/integrations", headers=ws).json()[
            "items"
        ][0]["webhooks"]
        ours = any(h.get("target_url") == target for h in hooks)
        return state if ours and state["mode"] == "auto" and state["registered_projects"] else None

    webhooks = wait_for(registered, WEBHOOK_TIMEOUT)
    report.check(
        "Worker 自动为项目注册 Webhook（目标为 integrations 网络里的 caddy）",
        webhooks is not None,
        json.dumps(webhooks),
    )

    # ---------------------------------------------------------------- DoD：原生改动出现在工作台
    vk.json("PATCH", f"/tasks/{native['id']}", {"title": "原生修改"}, merge=True)
    report.check(
        "DoD：在 Vikunja 里修改任务，经 Webhook 出现在工作台",
        bool(wait_for(lambda: "原生修改" in titles(), WEBHOOK_TIMEOUT)),
    )
    extra = vk.json("POST", f"/projects/{project['id']}/tasks", {"title": "原生新建"})
    report.check(
        "在 Vikunja 里新建任务，出现在工作台",
        bool(wait_for(lambda: "原生新建" in titles(), WEBHOOK_TIMEOUT)),
    )
    vk.call("DELETE", f"/tasks/{extra['id']}")
    report.check(
        "在 Vikunja 里删除任务，从工作台消失（tombstone）",
        bool(wait_for(lambda: "原生新建" not in titles(), WEBHOOK_TIMEOUT)),
    )

    # ---------------------------------------------------------------- A06 / A07
    def vikunja_titles() -> list[str]:
        items = vk.json("GET", f"/projects/{project['id']}/tasks?per_page=50") or []
        return [t["title"] for t in (items.get("items") if isinstance(items, dict) else items)]

    key = str(uuid.uuid4())
    body = {"project_id": project_uuid, "title": "幂等创建", "priority": 3}
    first = owner.request("POST", "/api/v1/tasks", body, headers={**ws, "Idempotency-Key": key})
    replay = owner.request("POST", "/api/v1/tasks", body, headers={**ws, "Idempotency-Key": key})
    report.check(
        "A06：同一 Idempotency-Key 重放返回同一结果，响应头 Idempotent-Replayed",
        first.status == replay.status == 201
        and replay.headers.get("Idempotent-Replayed") == "true"
        and first.json() == replay.json(),
        f"{first.summary} / {replay.summary}",
    )
    report.check(
        "A06：Vikunja 里只有一个任务（重复提交不创建双份）",
        vikunja_titles().count("幂等创建") == 1,
        str(vikunja_titles()),
    )
    reused = owner.request(
        "POST",
        "/api/v1/tasks",
        {**body, "title": "换了内容"},
        headers={**ws, "Idempotency-Key": key},
    )
    report.check(
        "A07：同一个键换了请求内容返回 409 IDEMPOTENCY_KEY_REUSED",
        reused.status == 409 and reused.code == "IDEMPOTENCY_KEY_REUSED",
        reused.summary,
    )
    report.check("A07：被拒绝的请求没有到达 Vikunja", "换了内容" not in vikunja_titles())

    # ---------------------------------------------------------------- 经工作台修改与删除
    task = first.json()
    edited = owner.request(
        "PATCH",
        f"/api/v1/tasks/{task['id']}",
        {"version": task["version"], "title": "工作台修改", "done": True},
        headers={**ws, "Idempotency-Key": str(uuid.uuid4())},
    )
    report.check(
        "经工作台修改任务 200，Vikunja 里同步变化",
        edited.status == 200 and "工作台修改" in vikunja_titles(),
        edited.summary,
    )
    time.sleep(1.1)  # Vikunja 按秒保存 updated：让下面的修改落在下一秒（核验 ADR 第 5.5 节）
    vk.json("PATCH", f"/tasks/{native['id']}", {"priority": 5}, merge=True)
    native_ws = wait_for(
        lambda: next(
            (t for t in ws_tasks() if t["title"] == "原生修改" and t["priority"] == 5), None
        ),
        WEBHOOK_TIMEOUT,
    )
    stale_version = owner.request(
        "PATCH",
        f"/api/v1/tasks/{(native_ws or {}).get('id')}",
        {"version": "2000-01-01T00:00:00Z", "priority": 1},
        headers={**ws, "Idempotency-Key": str(uuid.uuid4())},
    )
    report.check(
        "用旧 version 修改返回 409 VERSION_CONFLICT（带当前任务）",
        stale_version.status == 409 and stale_version.code == "VERSION_CONFLICT",
        stale_version.summary,
    )
    deleted = owner.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}?version={edited.json().get('version', '')}",
        headers={**ws, "Idempotency-Key": str(uuid.uuid4())},
    )
    report.check(
        "经工作台删除任务 204，Vikunja 里已不存在",
        deleted.status == 204 and "工作台修改" not in vikunja_titles(),
        deleted.summary,
    )

    # ---------------------------------------------------------------- A08：重复送达
    current_task = vk.json("GET", f"/tasks/{native['id']}")
    event = json.dumps(
        {
            "event_name": "task.updated",
            "time": stamp_ns(),
            "data": {"task": current_task, "project": project, "doer": {"id": 1}},
        }
    ).encode()
    signature = compose_exec(
        "api",
        "python",
        "-c",
        SIGN_SCRIPT,
        integration_id,
        env={"E2E_BODY": base64.b64encode(event).decode()},
    ).strip()
    hook_url = f"{base}/api/v1/webhooks/vikunja/{integration_id}"
    signed = {"Content-Type": "application/json", "X-Vikunja-Signature": signature}
    deliveries = [http("POST", hook_url, event, signed) for _ in range(3)]
    report.check(
        "A08：同一个已签名的事件投递 3 次都返回 202",
        all(d.status == 202 for d in deliveries),
        ", ".join(d.summary for d in deliveries),
    )
    event_key = hashlib.sha256(event).hexdigest()
    rows = inbox_event(event_key)
    report.check("A08：Inbox 里只记录一次", len(rows.splitlines()) == 1, rows)
    processed = wait_for(lambda: inbox_event(event_key) == "completed 1", WEBHOOK_TIMEOUT)
    report.check("A08：事件只处理一次（completed，attempts = 1）", bool(processed))
    forged = http("POST", hook_url, event.replace(b"task.updated", b"task.deleted"), signed)
    report.check(
        "签名与请求体不匹配返回 401 WEBHOOK_SIGNATURE_INVALID",
        forged.status == 401 and forged.code == "WEBHOOK_SIGNATURE_INVALID",
        forged.summary,
    )

    # -------------------------------------------------- A09：Webhook 丢失，周期对账纠正
    before = inbox_total(integration_id)
    core_compose("stop", "caddy")
    try:
        vk.json("PATCH", f"/tasks/{native['id']}", {"title": "Webhook 丢失的修改"}, merge=True)
        time.sleep(8)  # Vikunja 约 2.3 秒内重试 6 次后丢弃（核验 ADR 第 6 节）
    finally:
        core_compose("start", "caddy")
    wait_for(lambda: reachable(f"{base}/healthz"), 60)
    after = inbox_total(integration_id)
    report.check(
        "A09：caddy 停止期间的 Webhook 已丢失（Inbox 没有新事件）",
        before == after,
        f"{before} → {after}",
    )
    corrected = wait_for(lambda: "Webhook 丢失的修改" in titles(), RECONCILE_TIMEOUT, interval=3)
    report.check("A09：周期对账最终纠正投影（没有手动同步）", bool(corrected))

    # ---------------------------------------------------------------- A05：第三方服务断开
    vikunja_compose("stop", "vikunja")
    try:
        down = owner.request(
            "POST",
            "/api/v1/tasks",
            {"project_id": project_uuid, "title": "断开时创建"},
            headers={**ws, "Idempotency-Key": str(uuid.uuid4())},
        )
        report.check(
            "A05：Vikunja 断开时创建任务返回 503 EXTERNAL_UNAVAILABLE",
            down.status == 503 and down.code == "EXTERNAL_UNAVAILABLE",
            down.summary,
        )
        page = owner.request("GET", "/api/v1/tasks", headers=ws)
        report.check(
            "A05：任务列表仍可读（本地投影），sync 为 stale",
            page.status == 200
            and page.json()["sync"]["status"] == "stale"
            and len(page.json()["items"]) >= 1,
            json.dumps((page.json() or {}).get("sync")),
        )
        overview = owner.request("GET", "/api/v1/dashboard/overview", headers=ws)
        module = (overview.json() or {}).get("modules", {}).get("tasks", {})
        report.check(
            "A05：首页任务模块 stale，汇总仍显示，其余组件照常",
            overview.status == 200
            and module.get("status") == "stale"
            and overview.json()["summary"]["pending_tasks"] is not None
            and len(overview.json()["widgets"]) >= 4,
            json.dumps(module),
        )
    finally:
        vikunja_compose("start", "vikunja")
    wait_for(lambda: reachable(f"{vk.api}/info"), 90)
    synced = owner.request("POST", f"/api/v1/integrations/{integration_id}/sync", headers=ws)
    report.check("恢复后手动同步 202", synced.status == 202, synced.summary)
    recovered = wait_for(
        lambda: (
            owner.request("GET", "/api/v1/dashboard/overview", headers=ws).json()["modules"][
                "tasks"
            ]["status"]
            == "ok"
        ),
        RECONCILE_TIMEOUT,
        interval=2,
    )
    report.check("A05：Vikunja 恢复后首页任务模块回到 ok", bool(recovered))

    # ---------------------------------------------------------------- 审计
    audit = audit_text(workspace)
    report.check(
        "审计：集成与任务的写操作都有记录，且不含 Token 与任务标题",
        all(
            a in audit
            for a in (
                "integration.created",
                "integration.tested",
                "task.created",
                "task.updated",
                "task.deleted",
                "integration.sync_requested",
            )
        )
        and token not in audit
        and "幂等创建" not in audit,
    )
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
