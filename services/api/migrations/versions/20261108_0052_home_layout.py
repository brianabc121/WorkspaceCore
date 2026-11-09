"""默认首页换成首页效果图的布局（T-0163，docs/v2/HOME_PLAN.md 第 4 节）

只处理**从没改过**的默认首页：组件恰好是迁移 0004（以及之后首次安装）建的四个入门组件
（文本便签、常用链接、待办汇总、今日任务，配置与位置都没动过）。这样的首页换成新的五个组件：
工作概况、任务进展、AI 新闻速览、我的待办、提醒与日程，仪表盘版本加 1。
改过的首页保持原样，可以在“自定义首页 → 更多 → 恢复默认首页”自己换。

downgrade 把仍是新默认布局（没改过）的首页换回四个入门组件；改过的保持原样。

NEW_WIDGETS 必须与 app/domain/dashboard_defaults.starter_widgets() 一致
（tests/test_home_migration.py 检查）。

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-08
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

Widget = tuple[str, dict[str, Any], dict[str, Any]]


def _layout(x: int, y: int, w: int, h: int, mobile_y: int) -> dict[str, Any]:
    return {"desktop": {"x": x, "y": y, "w": w, "h": h}, "mobile": {"y": mobile_y, "h": h}}


OLD_WIDGETS: list[Widget] = [
    (
        "note",
        {"text": "欢迎使用 My Workspace。进入编辑模式后可以拖动、调整或添加组件。"},
        _layout(0, 0, 6, 3, 0),
    ),
    ("quick_links", {"links": []}, _layout(6, 0, 6, 3, 3)),
    ("todo_summary", {}, _layout(0, 3, 4, 3, 6)),
    ("today_tasks", {"limit": 5}, _layout(4, 3, 8, 4, 9)),
]

NEW_WIDGETS: list[Widget] = [
    ("work_summary", {"reminder_days": 2}, _layout(0, 0, 12, 2, 0)),
    ("task_progress", {"limit": 7}, _layout(0, 2, 8, 7, 2)),
    ("news_briefing", {"lines": 6}, _layout(8, 2, 4, 6, 13)),
    ("my_todos", {"limit": 5}, _layout(0, 9, 8, 4, 9)),
    ("calendar_agenda", {"days": 7, "limit": 5}, _layout(8, 8, 4, 5, 19)),
]


def _fingerprint(widgets: Sequence[Widget]) -> list[str]:
    return sorted(
        json.dumps([kind, config, layout], sort_keys=True, ensure_ascii=False)
        for kind, config, layout in widgets
    )


def _replace(old: Sequence[Widget], new: Sequence[Widget]) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT d.id, w.widget_type, w.config_json, w.layout_json
            FROM workspace.dashboards d
            LEFT JOIN workspace.widgets w ON w.dashboard_id = d.id
            WHERE d.is_default
            """
        )
    ).all()
    found: dict[Any, list[Widget]] = {}
    for dashboard_id, kind, config, layout in rows:
        widgets = found.setdefault(dashboard_id, [])  # 没有组件的首页也记下（不会匹配）
        if kind is not None:
            widgets.append((kind, config, layout))
    target = _fingerprint(old)
    for dashboard_id, widgets in found.items():
        if _fingerprint(widgets) != target:
            continue
        bind.execute(
            sa.text("DELETE FROM workspace.widgets WHERE dashboard_id = :id"), {"id": dashboard_id}
        )
        for kind, config, layout in new:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO workspace.widgets
                        (dashboard_id, widget_type, config_json, layout_json)
                    VALUES (:id, :kind, CAST(:config AS jsonb), CAST(:layout AS jsonb))
                    """
                ),
                {
                    "id": dashboard_id,
                    "kind": kind,
                    "config": json.dumps(config, ensure_ascii=False),
                    "layout": json.dumps(layout),
                },
            )
        bind.execute(
            sa.text(
                "UPDATE workspace.dashboards SET version = version + 1, updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": dashboard_id},
        )


def upgrade() -> None:
    _replace(OLD_WIDGETS, NEW_WIDGETS)


def downgrade() -> None:
    _replace(NEW_WIDGETS, OLD_WIDGETS)
