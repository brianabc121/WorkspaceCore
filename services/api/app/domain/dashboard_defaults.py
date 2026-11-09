"""新工作空间的默认“首页”仪表盘与组件。

T-0163 起按首页效果图（docs/v2/HOME_PLAN.md 第 4 节）：
工作概况、任务进展、AI 新闻速览、我的待办、提醒与日程。
迁移 0052 把从没改过的旧默认首页（迁移 0004 的四个入门组件）换成这一套；“恢复默认首页”也用它。
这里不依赖认证模块，首次安装也可以直接调用。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dashboard
from app.repositories import dashboards as dashboard_repo
from app.schemas.dashboards import (
    CalendarAgendaWidgetCreate,
    MyTodosWidgetCreate,
    NewsBriefingWidgetCreate,
    TaskProgressWidgetCreate,
    WidgetCreate,
    WidgetLayout,
    WorkSummaryWidgetCreate,
)

DEFAULT_DASHBOARD_TITLE = "首页"


def _layout(x: int, y: int, w: int, h: int, mobile_y: int) -> WidgetLayout:
    return WidgetLayout.model_validate(
        {"desktop": {"x": x, "y": y, "w": w, "h": h}, "mobile": {"y": mobile_y, "h": h}}
    )


def starter_widgets() -> list[WidgetCreate]:
    return [
        WorkSummaryWidgetCreate(widget_type="work_summary", layout=_layout(0, 0, 12, 2, 0)),
        TaskProgressWidgetCreate(widget_type="task_progress", layout=_layout(0, 2, 8, 7, 2)),
        NewsBriefingWidgetCreate(widget_type="news_briefing", layout=_layout(8, 2, 4, 6, 13)),
        MyTodosWidgetCreate(widget_type="my_todos", layout=_layout(0, 9, 8, 4, 9)),
        CalendarAgendaWidgetCreate(widget_type="calendar_agenda", layout=_layout(8, 8, 4, 5, 19)),
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
