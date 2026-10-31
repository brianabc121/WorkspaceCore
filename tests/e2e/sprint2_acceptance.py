#!/usr/bin/env python3
"""Sprint 2 全栈验收（T-0017）：经 Caddy 对外入口验证规范第 13 节的 DoD 与验收场景（接口级）。

覆盖 A03（viewer 修改 Widget 返回 403）、A04（保存并刷新首页布局一致，版本号更新）、
A05（尚无 Connector：任务模块显示 unavailable，汇总值为 null，其余组件照常返回）、
版本冲突（两个客户端用同一版本保存，后者 409）、跨工作空间 404、
DoD“未知 widget type 安全报错”（422 且不回显输入）、DoD“部分 Widget 失败不影响整页”
（库里有一条类型与布局都已损坏的组件时，首页聚合仍然 200，其余组件不受影响）、审计日志不含组件内容。
拖拽与页面渲染属于浏览器验收，见 docs/acceptance/sprint-2.md。

前提：Compose 全栈已启动并迁移（make up && make migrate）。不要求空库：本脚本每次运行都用新的邮箱，
用户与工作空间由同目录的 seed.py 经 `docker compose exec api` 用应用自身的代码写入。
用法：python3 tests/e2e/sprint2_acceptance.py [--base-url http://127.0.0.1:8080]
  - 请求的 Origin 取 --base-url 的来源，它必须是 PUBLIC_BASE_URL 或在 CORS_ALLOWED_ORIGINS 里。
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py、seed.py；CI（ci-infra.yml）与本机共用。
"""

import argparse
import copy
import sys
import time
import uuid
from typing import Any

from e2e_support import Client, Report, compose_exec, default_base_url, parse_base_url
from seed import add_corrupt_widget, add_member, create_user

PASSWORD = "e2e sprint2 passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
# 首页聚合的总耗时上限（契约 ADR 第 8 节，DASHBOARD_OVERVIEW_TIMEOUT_SECONDS 的默认值）
OVERVIEW_BUDGET_SECONDS = 3.0
STARTER_TYPES = ["note", "quick_links", "todo_summary", "today_tasks"]
# 用来确认错误响应和审计日志不回显输入内容的标记
ECHO_MARK = "e2e-should-not-echo"


def seed_user(run: str, name: str) -> tuple[str, str, str]:
    """返回 (邮箱, 用户 ID, 自己的工作空间 ID)。"""
    email = f"{name.lower()}-{run}@e2e.example.com"
    user_id, workspace_id, _ = create_user(email, name, PASSWORD)
    return email, user_id, workspace_id


def login(base: str, email: str) -> Client:
    client = Client(base)
    response = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if response.status != 200:
        sys.exit(f"{email} 登录失败：{response.summary}")
    return client


def layouts(overview: dict[str, Any]) -> dict[str, Any]:
    return {w["id"]: w["layout"] for w in overview.get("widgets", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    base = parse_base_url(parser.parse_args().base_url)
    report = Report()
    print(f"Sprint 2 全栈验收：{base}")

    current = compose_exec("api", "alembic", "current")
    report.check("迁移在最新版本（alembic current 为 head）", "(head)" in current, current.strip())

    run = uuid.uuid4().hex[:8]
    alice_email, _, workspace = seed_user(run, "Alice")
    victor_email, victor_id, _ = seed_user(run, "Victor")
    molly_email, molly_id, _ = seed_user(run, "Molly")
    olga_email, _, olga_workspace = seed_user(run, "Olga")
    add_member(workspace, victor_id, "viewer")
    add_member(workspace, molly_id, "member")
    in_workspace = {"X-Workspace-Id": workspace}

    alice = login(base, alice_email)
    victor = login(base, victor_email)
    molly = login(base, molly_email)
    olga = login(base, olga_email)

    # ---------------------------------------------------------------- 首页聚合与 A05
    started = time.monotonic()
    first = alice.request("GET", "/api/v1/dashboard/overview", headers=in_workspace)
    elapsed = time.monotonic() - started
    overview = first.json() or {}
    report.check("首页聚合 200", first.status == 200, first.summary)
    report.check(
        f"首页聚合耗时在 {OVERVIEW_BUDGET_SECONDS:g} 秒上限内",
        elapsed < OVERVIEW_BUDGET_SECONDS,
        f"{elapsed:.2f}s",
    )
    dashboard = overview.get("dashboard") or {}
    report.check(
        "新工作空间带默认首页",
        dashboard.get("title") == "首页" and dashboard.get("is_default") is True,
    )
    widgets = overview.get("widgets") or []
    report.check(
        "默认首页带 4 个入门组件（按桌面端从上到下、从左到右）",
        [w["widget_type"] for w in widgets] == STARTER_TYPES,
        ", ".join(w["widget_type"] for w in widgets),
    )
    tasks = (overview.get("modules") or {}).get("tasks") or {}
    report.check(
        "A05 尚无任务 Connector：tasks 模块 unavailable / not_configured",
        tasks.get("status") == "unavailable" and tasks.get("reason") == "not_configured",
        str(tasks),
    )
    report.check(
        "A05 任务汇总未知时为 null（不以 0 冒充），today_tasks 为空；"
        "未读通知是本地数据，Sprint 5 起为实际值 0",
        overview.get("summary")
        == {"pending_tasks": None, "active_projects": None, "unread_notifications": 0}
        and overview.get("today_tasks") == [],
        str(overview.get("summary")),
    )
    report.check(
        "A05 模块不可用时其余组件照常返回",
        {"note", "quick_links"} <= {w["widget_type"] for w in widgets},
    )
    listed = alice.request("GET", "/api/v1/dashboards", headers=in_workspace).json() or {}
    report.check(
        "仪表盘列表只有默认首页",
        [d["id"] for d in listed.get("items", [])] == [dashboard.get("id")],
    )

    dashboard_id = str(dashboard.get("id"))
    version = int(dashboard.get("version", 0))
    by_type = {w["widget_type"]: w for w in widgets}
    note = by_type.get("note") or {}

    # ---------------------------------------------------------------- A04 保存并刷新布局
    saved = copy.deepcopy(layouts(overview))
    for widget_id, layout in saved.items():
        # 整体下移一行并左右对调：桌面端 x 变为 12 - x - w，移动端顺序反转
        desktop = layout["desktop"]
        desktop["x"] = 12 - desktop["x"] - desktop["w"]
        desktop["y"] += 1
        layout["mobile"]["y"] = 20 - layout["mobile"]["y"]
        saved[widget_id] = layout
    placements = [{"widget_id": wid, "layout": layout} for wid, layout in saved.items()]
    body: dict[str, Any] = {"version": version, "layout": placements}
    other_tab = login(base, alice_email)
    save = alice.request("PATCH", f"/api/v1/dashboards/{dashboard_id}", body, headers=in_workspace)
    report.check(
        "A04 保存布局 → 200，仪表盘版本 +1",
        save.status == 200 and (save.json() or {}).get("version") == version + 1,
        save.summary,
    )
    for name, client in {"同一会话刷新": alice, "另一个会话": other_tab}.items():
        again = client.request("GET", "/api/v1/dashboard/overview", headers=in_workspace).json()
        again = again or {}
        report.check(
            f"A04 {name}后布局与保存的一致，版本号已更新",
            layouts(again) == saved
            and (again.get("dashboard") or {}).get("version") == version + 1,
        )
    stale = other_tab.request(
        "PATCH", f"/api/v1/dashboards/{dashboard_id}", body, headers=in_workspace
    )
    details = ((stale.json() or {}).get("error") or {}).get("details") or {}
    report.check(
        "版本冲突：另一个会话用旧版本保存 → 409 VERSION_CONFLICT，返回最新版本",
        stale.status == 409
        and stale.code == "VERSION_CONFLICT"
        and details.get("current_version") == version + 1,
        f"{stale.status} {stale.code} {details}",
    )
    partial = alice.request(
        "PATCH",
        f"/api/v1/dashboards/{dashboard_id}",
        {"version": version + 1, "layout": placements[:-1]},
        headers=in_workspace,
    )
    report.check(
        "保存布局必须列出全部组件，缺少时 → 422",
        partial.status == 422 and partial.code == "VALIDATION_FAILED",
        partial.summary,
    )
    after = alice.request("GET", "/api/v1/dashboard/overview", headers=in_workspace).json() or {}
    report.check(
        "被拒绝的保存没有改动布局和版本",
        layouts(after) == saved and (after.get("dashboard") or {}).get("version") == version + 1,
    )
    version += 1

    # ---------------------------------------------------------------- A03 viewer 只读
    note_path = f"/api/v1/widgets/{note.get('id')}"
    note_update = {
        "widget_type": "note",
        "version": note.get("version"),
        "config": {"text": f"viewer 改的 {ECHO_MARK}"},
    }
    new_widget = {
        "widget_type": "note",
        "config": {"text": "viewer 加的"},
        "layout": {"desktop": {"x": 0, "y": 20, "w": 4, "h": 2}, "mobile": {"y": 30, "h": 2}},
    }
    seen = victor.request("GET", "/api/v1/dashboard/overview", headers=in_workspace)
    report.check("A03 viewer 可以查看首页", seen.status == 200, seen.summary)
    denied = {
        "修改 Widget 配置": victor.request("PATCH", note_path, note_update, headers=in_workspace),
        "添加 Widget": victor.request(
            "POST", f"/api/v1/dashboards/{dashboard_id}/widgets", new_widget, headers=in_workspace
        ),
        "删除 Widget": victor.request("DELETE", note_path, headers=in_workspace),
        "保存布局": victor.request(
            "PATCH",
            f"/api/v1/dashboards/{dashboard_id}",
            {**body, "version": version},
            headers=in_workspace,
        ),
        "新建仪表盘": victor.request(
            "POST", "/api/v1/dashboards", {"title": "viewer 的"}, headers=in_workspace
        ),
    }
    for name, response in denied.items():
        report.check(
            f"A03 viewer {name} → 403",
            response.status == 403 and response.code == "FORBIDDEN",
            response.summary,
        )
    unchanged = alice.request("GET", "/api/v1/dashboard/overview", headers=in_workspace).json()
    unchanged = unchanged or {}
    unchanged_note: dict[str, Any] = next(
        (w for w in unchanged.get("widgets", []) if w["id"] == note.get("id")), {}
    )
    report.check(
        "A03 viewer 的请求没有改动任何数据",
        unchanged_note.get("config") == note.get("config")
        and unchanged_note.get("version") == note.get("version")
        and len(unchanged.get("widgets", [])) == len(widgets)
        and (unchanged.get("dashboard") or {}).get("version") == version,
    )

    # member 可以写：确认上面的 403 来自角色，而不是写接口本身不可用
    member_note = {**note_update, "config": {"text": f"member 改的 {ECHO_MARK}"}}
    edited = molly.request("PATCH", note_path, member_note, headers=in_workspace)
    report.check(
        "member 修改 Widget 配置 → 200，组件版本 +1",
        edited.status == 200 and (edited.json() or {}).get("version") == note.get("version", 0) + 1,
        edited.summary,
    )
    conflict = alice.request("PATCH", note_path, note_update, headers=in_workspace)
    report.check(
        "Widget 版本冲突：用旧版本修改 → 409 VERSION_CONFLICT",
        conflict.status == 409 and conflict.code == "VERSION_CONFLICT",
        conflict.summary,
    )
    added = molly.request(
        "POST", f"/api/v1/dashboards/{dashboard_id}/widgets", new_widget, headers=in_workspace
    )
    added_id = (added.json() or {}).get("id")
    removed = molly.request("DELETE", f"/api/v1/widgets/{added_id}", headers=in_workspace)
    final = alice.request("GET", "/api/v1/dashboard/overview", headers=in_workspace).json() or {}
    report.check(
        "member 添加并删除组件 → 201 / 204，仪表盘版本各 +1",
        added.status == 201
        and removed.status == 204
        and (final.get("dashboard") or {}).get("version") == version + 2,
        f"{added.status} {removed.status}",
    )
    version += 2

    # ---------------------------------------------------------------- 跨工作空间
    outsider = {
        "非成员读取首页": olga.request("GET", "/api/v1/dashboard/overview", headers=in_workspace),
        "非成员修改 Widget": olga.request("PATCH", note_path, note_update, headers=in_workspace),
        "用自己的工作空间读取别人的仪表盘组件": olga.request(
            "GET",
            f"/api/v1/dashboards/{dashboard_id}/widgets",
            headers={"X-Workspace-Id": olga_workspace},
        ),
        "用自己的工作空间删除别人的组件": olga.request(
            "DELETE", note_path, headers={"X-Workspace-Id": olga_workspace}
        ),
    }
    for name, response in outsider.items():
        report.check(
            f"跨工作空间：{name} → 404",
            response.status == 404 and response.code == "NOT_FOUND",
            response.summary,
        )

    # ---------------------------------------------------------------- DoD 未知 widget type
    invalid = {
        "未知 widget type": {**new_widget, "widget_type": "retired_widget"},
        "非 http(s) 链接": {
            **new_widget,
            "widget_type": "quick_links",
            "config": {"links": [{"title": ECHO_MARK, "url": f"javascript:{ECHO_MARK}"}]},
        },
        "多余字段": {**new_widget, "config": {"text": "x", "html": ECHO_MARK}},
    }
    for name, payload in invalid.items():
        response = alice.request(
            "POST", f"/api/v1/dashboards/{dashboard_id}/widgets", payload, headers=in_workspace
        )
        report.check(
            f"DoD 创建组件时{name} → 422，错误里不回显输入",
            response.status == 422
            and response.code == "VALIDATION_FAILED"
            and ECHO_MARK not in response.text
            and "Traceback" not in response.text,
            response.summary,
        )

    # ---------------------------------------------------------------- DoD 部分 Widget 失败
    broken_id = add_corrupt_widget(dashboard_id)
    page = alice.request("GET", "/api/v1/dashboard/overview", headers=in_workspace)
    page_widgets = (page.json() or {}).get("widgets") or []
    desktops = [layout["desktop"] for layout in saved.values()]
    mobiles = [layout["mobile"] for layout in saved.values()]
    broken: dict[str, Any] = next((w for w in page_widgets if w["id"] == broken_id), {})
    report.check(
        "DoD 库里有一条已损坏的组件时首页聚合仍然 200",
        page.status == 200,
        page.summary,
    )
    report.check(
        "DoD 损坏的组件原样标出类型，配置为空，交给前端显示错误卡片",
        broken.get("widget_type") == "retired_widget" and broken.get("config") == {},
        str(broken),
    )
    below = {
        "desktop": {"x": 0, "y": max(d["y"] + d["h"] for d in desktops), "w": 4, "h": 3},
        "mobile": {"y": max(m["y"] + m["h"] for m in mobiles), "h": 3},
    }
    report.check(
        "DoD 无法解析的布局紧接着放在其余组件下方，不会把页面撑高",
        broken.get("layout") == below,
        str(broken.get("layout")),
    )
    report.check(
        "DoD 其余组件不受影响",
        {w["id"]: w["layout"] for w in page_widgets if w["id"] != broken_id} == saved,
    )
    listed_widgets = alice.request(
        "GET", f"/api/v1/dashboards/{dashboard_id}/widgets", headers=in_workspace
    )
    report.check(
        "DoD 仪表盘组件列表同样 200",
        listed_widgets.status == 200
        and len((listed_widgets.json() or {}).get("items", [])) == len(page_widgets),
        listed_widgets.summary,
    )

    # ---------------------------------------------------------------- 审计日志
    logs = alice.request("GET", "/api/v1/audit-logs?limit=100", headers=in_workspace)
    actions = {entry["action"] for entry in (logs.json() or {}).get("items", [])}
    expected = {"dashboard.updated", "widget.updated", "widget.created", "widget.deleted"}
    report.check(
        "审计日志记录了保存布局与组件增删改",
        logs.status == 200 and expected <= actions,
        ", ".join(sorted(expected - actions)),
    )
    report.check("审计日志不含组件的文本内容", ECHO_MARK not in logs.text)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
