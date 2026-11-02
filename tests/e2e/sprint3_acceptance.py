#!/usr/bin/env python3
"""项目与任务的全栈验收（T-0082 起：工作台自己的项目与任务；之前是 T-0023 的真实 Vikunja 验收）。

经 Caddy 对外入口验证（接口级）：

- 项目：新建、改名（version）、旧版本 409、归档后默认不列出、删除连带任务
- 任务：新建带 Idempotency-Key；A06 同一个键重放只创建一次；A07 同键不同请求 409；
  修改 / 删除带 version，旧版本 409；删除幂等；完成时间；标签
- 首页任务模块 ok，汇总与今日任务来自本地数据；搜索能找到项目与任务
- 权限：viewer 只读（写入 403）；其他工作空间看不到（404），也不能拿别人的项目建任务
- Vikunja 已移除：集成接口不接受 provider=vikunja，Webhook 端点不存在
- 审计日志不含任务内容

前提：Compose 全栈已启动并迁移（make up && make migrate）。不要求空库：每次运行用新的工作台用户。
用法：python3 tests/e2e/sprint3_acceptance.py [--base-url http://127.0.0.1:8080]
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py、seed.py；CI（ci-infra.yml）与本机共用。
"""

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from e2e_support import Client, Report, Response, default_base_url, parse_base_url
from seed import add_member, create_user

PASSWORD = "e2e sprint3 passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
SECRET_MARK = "e2e-task-content-should-not-be-audited"  # noqa: S105 -- 只是标记文本


def login(base: str, email: str) -> Client:
    client = Client(base)
    response = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if response.status != 200:
        sys.exit(f"{email} 登录失败：{response.summary}")
    return client


class Workspace:
    def __init__(self, client: Client, workspace_id: str) -> None:
        self.client = client
        self.headers = {"X-Workspace-Id": workspace_id}

    def call(
        self, method: str, path: str, body: Any = None, *, key: uuid.UUID | None = None
    ) -> Response:
        headers = dict(self.headers)
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        return self.client.request(method, path, body, headers=headers)

    def get(self, path: str) -> Any:
        response = self.call("GET", path)
        if response.status != 200:
            raise RuntimeError(f"GET {path} 失败：{response.summary}")
        return response.json()

    def titles(self, query: str = "status=all&limit=100") -> list[str]:
        return sorted(t["title"] for t in self.get(f"/api/v1/tasks?{query}")["items"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    base = parse_base_url(parser.parse_args().base_url)
    report = Report()
    print(f"项目与任务验收：{base}")
    run = uuid.uuid4().hex[:8]

    owner_email = f"owner-{run}@e2e.example.com"
    _, workspace_id, _ = create_user(owner_email, "Owner", PASSWORD)
    owner = login(base, owner_email)
    viewer_email = f"viewer-{run}@e2e.example.com"
    viewer_id, _, _ = create_user(viewer_email, "Viewer", PASSWORD)
    add_member(workspace_id, viewer_id, "viewer")
    outsider_email = f"outsider-{run}@e2e.example.com"
    _, outsider_ws, _ = create_user(outsider_email, "Outsider", PASSWORD)
    ws = Workspace(owner, workspace_id)

    # ---------------------------------------------------------------- 项目
    created = ws.call("POST", "/api/v1/projects", {"title": f"验收项目 {run}"})
    project = created.json() if created.status == 201 else {}
    report.check(
        "新建项目：201，version 为 1，任务数为 0",
        created.status == 201 and project.get("version") == 1 and project.get("open_tasks") == 0,
        created.summary,
    )
    path = f"/api/v1/projects/{project.get('id')}"
    renamed = ws.call("PATCH", path, {"version": 1, "title": f"验收项目改名 {run}"})
    stale = ws.call("PATCH", path, {"version": 1, "title": "旧版本"})
    report.check(
        "项目改名带 version；旧版本 409 VERSION_CONFLICT",
        renamed.status == 200
        and renamed.json().get("version") == 2
        and stale.status == 409
        and stale.code == "VERSION_CONFLICT",
        f"{renamed.status} / {stale.status} {stale.code}",
    )
    project_id = project.get("id")

    # ------------------------------------------------ 任务：Idempotency-Key（A06、A07）
    key = uuid.uuid4()
    soon = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    body = {
        "project_id": project_id,
        "title": "验收任务",
        "description": SECRET_MARK,
        "priority": 4,
        "due_at": soon,
        "labels": ["验收", "验收", "e2e"],
    }
    first = ws.call("POST", "/api/v1/tasks", body, key=key)
    replay = ws.call("POST", "/api/v1/tasks", body, key=key)
    task = first.json() if first.status == 201 else {}
    report.check(
        "新建任务 201，标签去重；A06 同一个 Idempotency-Key 重放原样返回，只创建一次",
        first.status == 201
        and task.get("labels") == ["验收", "e2e"]
        and replay.status == 201
        and replay.headers.get("Idempotent-Replayed") == "true"
        and replay.json() == task
        and ws.titles().count("验收任务") == 1,
        f"{first.status} / {replay.status}",
    )
    reused = ws.call("POST", "/api/v1/tasks", {**body, "title": "换了内容"}, key=key)
    report.check(
        "A07 同一个键换了请求内容：409 IDEMPOTENCY_KEY_REUSED，没有创建",
        reused.status == 409
        and reused.code == "IDEMPOTENCY_KEY_REUSED"
        and "换了内容" not in ws.titles(),
        f"{reused.status} {reused.code}",
    )
    missing_key = ws.call("POST", "/api/v1/tasks", {**body, "title": "没有键"})
    report.check(
        "写任务必须带 Idempotency-Key：422", missing_key.status == 422, missing_key.summary
    )
    second = ws.call(
        "POST", "/api/v1/tasks", {"project_id": project_id, "title": "第二个任务"}, key=uuid.uuid4()
    )

    # ---------------------------------------------------------------- 修改与删除（version）
    task_path = f"/api/v1/tasks/{task.get('id')}"
    done = ws.call(
        "PATCH", task_path, {"version": task.get("version"), "done": True}, key=uuid.uuid4()
    )
    old = ws.call(
        "PATCH", task_path, {"version": task.get("version"), "title": "旧"}, key=uuid.uuid4()
    )
    report.check(
        "修改任务带 version：标为完成后有完成时间；旧版本 409 VERSION_CONFLICT",
        done.status == 200
        and done.json().get("done") is True
        and done.json().get("done_at") is not None
        and old.status == 409
        and old.code == "VERSION_CONFLICT",
        f"{done.status} / {old.status} {old.code}",
    )

    # ---------------------------------------------------------------- 首页与搜索
    overview = ws.get("/api/v1/dashboard/overview")
    report.check(
        "首页任务模块 ok；汇总 1 个未完成、1 个项目",
        overview["modules"]["tasks"]["status"] == "ok"
        and overview["summary"]["pending_tasks"] == 1
        and overview["summary"]["active_projects"] == 1,
        str(overview["summary"]),
    )
    found = ws.get("/api/v1/search?q=" + "验收项目改名")
    hits = {(r["resource_type"], r["provider"]) for r in found["items"]}
    task_hits = ws.get("/api/v1/search?q=" + "第二个任务")["items"]
    report.check(
        "搜索找到工作台自己的项目与任务（provider=local，没有外部链接），任务带项目名",
        ("project", "local") in hits
        and len(task_hits) == 1
        and task_hits[0]["source_url"] is None
        and task_hits[0]["context"] == f"验收项目改名 {run}",
        str(hits),
    )

    # ---------------------------------------------------------------- 权限
    viewer = Workspace(login(base, viewer_email), workspace_id)
    attempts = [
        viewer.call(
            "POST", "/api/v1/tasks", {"project_id": project_id, "title": "x"}, key=uuid.uuid4()
        ),
        viewer.call("POST", "/api/v1/projects", {"title": "viewer"}),
        viewer.call("PATCH", path, {"version": 2, "title": "viewer"}),
        viewer.call("DELETE", f"{path}?version=2"),
    ]
    report.check(
        "viewer 能读任务与项目；写入都是 403",
        viewer.call("GET", "/api/v1/tasks").status == 200
        and viewer.call("GET", "/api/v1/projects").status == 200
        and [a.status for a in attempts] == [403, 403, 403, 403],
        str([a.status for a in attempts]),
    )
    outsider = Workspace(login(base, outsider_email), outsider_ws)
    foreign = Workspace(outsider.client, workspace_id)
    stolen = outsider.call(
        "POST", "/api/v1/tasks", {"project_id": project_id, "title": "越权"}, key=uuid.uuid4()
    )
    report.check(
        "其他工作空间：看不到任务与项目（404），也不能拿别人的项目建任务（404）",
        outsider.call("GET", task_path).status == 404
        and outsider.call("GET", path).status == 404
        and foreign.call("GET", "/api/v1/tasks").status == 404
        and stolen.status == 404,
        f"{stolen.status}",
    )

    # ---------------------------------------------------------------- Vikunja 已移除
    vikunja = ws.call(
        "POST",
        "/api/v1/integrations",
        {
            "provider": "vikunja",
            "name": "Vikunja",
            "base_url": "http://vikunja:3456",
            "api_token": "tk_x",
        },
    )
    webhook = owner.request("POST", f"/api/v1/webhooks/vikunja/{uuid.uuid4()}", {}, csrf=False)
    report.check(
        "Vikunja 已移除：集成接口拒绝 provider=vikunja（422），Webhook 端点不存在（404 / 405）",
        vikunja.status == 422 and webhook.status in {404, 405},
        f"{vikunja.status} / {webhook.status}",
    )

    # ---------------------------------------------------------------- 删除
    stale_delete = ws.call("DELETE", f"{task_path}?version=1", key=uuid.uuid4())
    current = ws.get(task_path)
    delete_key = uuid.uuid4()
    deleted = ws.call("DELETE", f"{task_path}?version={current['version']}", key=delete_key)
    again = ws.call("DELETE", f"{task_path}?version={current['version']}", key=uuid.uuid4())
    report.check(
        "删除任务：旧版本 409；删除 204；已经不存在也是 204（幂等）",
        stale_delete.status == 409 and deleted.status == 204 and again.status == 204,
        f"{stale_delete.status} / {deleted.status} / {again.status}",
    )
    archived = ws.call("PATCH", path, {"version": 2, "archived": True})
    listed = [p["id"] for p in ws.get("/api/v1/projects")["items"]]
    with_archived = [p["id"] for p in ws.get("/api/v1/projects?include_archived=true")["items"]]
    report.check(
        "归档的项目默认不列出，include_archived=true 时列出",
        archived.status == 200 and project_id not in listed and project_id in with_archived,
        archived.summary,
    )
    removed = ws.call("DELETE", f"{path}?version=3")
    report.check(
        "删除项目 204，它的任务一起删除",
        removed.status == 204
        and second.status == 201
        and ws.call("GET", f"/api/v1/tasks/{second.json().get('id')}").status == 404
        and ws.titles() == [],
        removed.summary,
    )

    # ---------------------------------------------------------------- 审计
    audit = ws.get("/api/v1/audit-logs?limit=100")
    actions = {e["action"] for e in audit["items"]}
    report.check(
        "审计记录项目与任务的增删改，不含任务内容",
        {
            "project.created",
            "project.updated",
            "project.deleted",
            "task.created",
            "task.updated",
            "task.deleted",
        }
        <= actions
        and SECRET_MARK not in str(audit),
        ", ".join(sorted(a for a in actions if a.startswith(("project.", "task.")))),
    )
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
