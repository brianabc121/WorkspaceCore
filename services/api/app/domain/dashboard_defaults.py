"""新工作空间的默认“首页”仪表盘与入门组件（契约 ADR 第 3 节）。

与迁移 0004 为已有工作空间补建的内容一致；这里不依赖认证模块，首次安装也可以直接调用。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dashboard
from app.repositories import dashboards as dashboard_repo
from app.schemas.dashboards import (
    NoteConfig,
    NoteWidgetCreate,
    QuickLinksWidgetCreate,
    TodayTasksWidgetCreate,
    TodoSummaryWidgetCreate,
    WidgetCreate,
    WidgetLayout,
)

DEFAULT_DASHBOARD_TITLE = "首页"


def _layout(x: int, y: int, w: int, h: int, mobile_y: int) -> WidgetLayout:
    return WidgetLayout.model_validate(
        {"desktop": {"x": x, "y": y, "w": w, "h": h}, "mobile": {"y": mobile_y, "h": h}}
    )


def starter_widgets() -> list[WidgetCreate]:
    return [
        NoteWidgetCreate(
            widget_type="note",
            config=NoteConfig(
                text="欢迎使用 My Workspace。进入编辑模式后可以拖动、调整或添加组件。"
            ),
            layout=_layout(0, 0, 6, 3, 0),
        ),
        QuickLinksWidgetCreate(widget_type="quick_links", layout=_layout(6, 0, 6, 3, 3)),
        TodoSummaryWidgetCreate(widget_type="todo_summary", layout=_layout(0, 3, 4, 3, 6)),
        TodayTasksWidgetCreate(widget_type="today_tasks", layout=_layout(4, 3, 8, 4, 9)),
    ]


async def add_widgets(
    db: AsyncSession, dashboard_id: uuid.UUID, widgets: list[WidgetCreate]
) -> None:
    for widget in widgets:
        await dashboard_repo.add_widget(
            db,
            dashboard_id=dashboard_id,
            widget_type=widget.widget_type,
            config=widget.config.model_dump(mode="json"),
            layout=widget.layout.model_dump(mode="json"),
        )


async def create_default_dashboard(
    db: AsyncSession, *, workspace_id: uuid.UUID, owner_id: uuid.UUID | None
) -> Dashboard:
    dashboard = await dashboard_repo.create(
        db,
        workspace_id=workspace_id,
        owner_id=owner_id,
        title=DEFAULT_DASHBOARD_TITLE,
        is_default=True,
    )
    await add_widgets(db, dashboard.id, starter_widgets())
    return dashboard
