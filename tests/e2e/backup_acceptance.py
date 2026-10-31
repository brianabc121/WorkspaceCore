#!/usr/bin/env python3
"""备份恢复验收（T-0042，A15：备份后在空环境恢复，数据 / 登录 / 首页配置通过 Smoke Test）。

步骤：
1. 新建用户，改显示名，往首页加一个便签组件并改布局，新建一个日程。
2. make backup（scripts/backup.sh）写入一个临时的本地 restic 仓库。
3. 在正在运行的栈上删掉日程、改掉便签：恢复出来的应该是备份时的状态，而不是现在的。
4. scripts/restore-verify.sh --keep：在隔离的临时项目里恢复并做冒烟检查，保留环境。
5. 在临时环境里用同一个账号登录（A15 登录），核对显示名、首页布局与便签内容、日程、审计日志。
6. 确认正在运行的栈没有被恢复影响（日程仍是删除的状态），删除临时环境与临时仓库。

前提：Compose 全栈已启动并迁移；.env 里有 BACKUP_PASSWORD（make env 生成）。不要求空库。
用法：python3 tests/e2e/backup_acceptance.py [--base-url http://127.0.0.1:8080]
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py、seed.py。
"""

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from typing import Any

from e2e_support import ROOT, Client, Report, default_base_url, env_value, parse_base_url
from seed import create_user

PASSWORD = "e2e backup passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
RESTORE_PORT = env_value("RESTORE_HTTP_PORT", "18080") or "18080"


def run_script(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # 固定的仓库脚本，参数来自本脚本自身
    return subprocess.run(  # noqa: S603
        list(args), cwd=ROOT, env={**os.environ, **env}, capture_output=True, text=True, check=False
    )


def login(base: str, email: str) -> Client:
    client = Client(base)
    response = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if response.status != 200:
        sys.exit(f"登录 {base} 失败：{response.summary}")
    return client


def note_of(overview: dict[str, Any], widget_id: str) -> dict[str, Any]:
    return next((w for w in overview.get("widgets", []) if w["id"] == widget_id), {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    args = parser.parse_args()
    base = parse_base_url(args.base_url)
    report = Report()
    run = uuid.uuid4().hex[:8]
    repository = f"backups/a15-{run}"
    env = {"BACKUP_REPOSITORY": f"./{repository}", "RESTORE_HTTP_PORT": RESTORE_PORT}
    print(f"备份恢复验收（A15）：{base}，临时仓库 {repository}，临时环境端口 {RESTORE_PORT}")
    try:
        run_checks(base, report, run, env)
    finally:
        run_script("scripts/restore-verify.sh", "--cleanup", env=env)
        shutil.rmtree(ROOT / repository, ignore_errors=True)
    return report.finish()


def run_checks(base: str, report: Report, run: str, env: dict[str, str]) -> None:
    email = f"a15-{run}@backup.example.com"
    _, workspace_id, dashboard_id = create_user(email, "A15", PASSWORD)
    owner = login(base, email)
    h = {"X-Workspace-Id": workspace_id}

    # ---------------------------------------------------------------- 1. 备份前的数据
    name = f"备份前的显示名 {run}"
    owner.request("PATCH", "/api/v1/me", {"display_name": name})
    note_text = f"备份时的便签 {run}"
    note = (
        owner.request(
            "POST",
            f"/api/v1/dashboards/{dashboard_id}/widgets",
            {
                "widget_type": "note",
                "config": {"text": note_text},
                "layout": {
                    "desktop": {"x": 6, "y": 30, "w": 5, "h": 3},
                    "mobile": {"y": 40, "h": 3},
                },
            },
            headers=h,
        ).json()
        or {}
    )
    event = (
        owner.request(
            "POST",
            "/api/v1/calendar/events",
            {
                "title": f"备份时的日程 {run}",
                "timezone": "Asia/Shanghai",
                "start_at": "2026-11-03T10:00:00+08:00",
                "end_at": "2026-11-03T11:00:00+08:00",
            },
            headers=h,
        ).json()
        or {}
    )
    before = owner.request("GET", "/api/v1/dashboard/overview", headers=h).json() or {}
    saved = note_of(before, str(note.get("id")))
    report.check(
        "准备数据：显示名、首页便签（含布局）与日程已写入",
        bool(saved) and saved["config"]["text"] == note_text and bool(event.get("id")),
    )

    # ---------------------------------------------------------------- 2. 备份
    backup = run_script("scripts/backup.sh", env=env)
    report.check(
        "make backup：生成加密备份并按保留策略清理",
        backup.returncode == 0 and "备份完成" in backup.stdout,
        (backup.stdout + backup.stderr).strip().splitlines()[-1]
        if (backup.stdout + backup.stderr).strip()
        else "",
    )
    if backup.returncode != 0:
        print(backup.stdout[-2000:], backup.stderr[-2000:], sep="\n")
        return
    repo = ROOT / env["BACKUP_REPOSITORY"]
    report.check(
        "备份是加密的：仓库里找不到 pg_dump 归档的文件头和本次写入的文字",
        not any(
            b"PGDMP" in path.read_bytes() or note_text.encode() in path.read_bytes()
            for path in repo.rglob("*")
            if path.is_file()
        ),
    )

    # ---------------------------------------------------------------- 3. 备份之后的改动
    owner.request(
        "DELETE",
        f"/api/v1/calendar/events/{event.get('id')}?version={event.get('version')}",
        headers=h,
    )
    owner.request(
        "PATCH",
        f"/api/v1/widgets/{note.get('id')}",
        {
            "widget_type": "note",
            "version": saved.get("version"),
            "config": {"text": "备份之后改的"},
        },
        headers=h,
    )

    # ---------------------------------------------------------------- 4. 在隔离环境恢复
    restore = run_script("scripts/restore-verify.sh", "--keep", env=env)
    output = restore.stdout + restore.stderr
    report.check(
        "make restore-verify：隔离环境恢复、迁移检查、冒烟检查通过",
        restore.returncode == 0
        and "冒烟检查全部通过" in output
        and "迁移检查：已到最新版本" in output,
        next((line for line in output.splitlines() if line.startswith("数据检查")), ""),
    )
    if restore.returncode != 0:
        print(output[-3000:])
        return

    # ---------------------------------------------------------------- 5. 在恢复出来的环境里核对
    restored_base = f"http://127.0.0.1:{env['RESTORE_HTTP_PORT']}"
    restored = login(restored_base, email)
    report.check("A15 恢复后用原来的账号和密码登录成功", True)
    me = (restored.request("GET", "/api/v1/me").json() or {}).get("user") or {}
    report.check(
        "A15 恢复后显示名是备份时的", me.get("display_name") == name, str(me.get("display_name"))
    )
    overview = restored.request("GET", "/api/v1/dashboard/overview", headers=h).json() or {}
    widget = note_of(overview, str(note.get("id")))
    report.check(
        "A15 恢复后首页配置一致：便签内容与布局都是备份时的",
        bool(widget)
        and widget["config"]["text"] == note_text
        and widget["layout"] == saved["layout"],
        str(widget.get("config")),
    )
    events = (
        restored.request(
            "GET",
            "/api/v1/calendar/events?start=2026-11-03T00:00:00%2B08:00&end=2026-11-04T00:00:00%2B08:00",
            headers=h,
        ).json()
        or {}
    )
    report.check(
        "A15 恢复后日程还在（备份之后才删除）",
        f"备份时的日程 {run}" in [e["title"] for e in events.get("items", [])],
    )
    audit = restored.request("GET", "/api/v1/audit-logs?limit=50", headers=h).json() or {}
    report.check("A15 恢复后审计日志还在", len(audit.get("items", [])) > 0)

    # ---------------------------------------------------------------- 6. 正在运行的栈不受影响
    live = (
        owner.request(
            "GET",
            "/api/v1/calendar/events?start=2026-11-03T00:00:00%2B08:00&end=2026-11-04T00:00:00%2B08:00",
            headers=h,
        ).json()
        or {}
    )
    report.check(
        "恢复演练不影响正在运行的栈（日程仍是删除后的状态）",
        f"备份时的日程 {run}" not in [e["title"] for e in live.get("items", [])],
    )


if __name__ == "__main__":
    sys.exit(main())
