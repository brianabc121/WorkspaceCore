"""仪表盘与组件的业务：读写、Registry 校验、乐观并发、数量上限、审计与首页聚合。

约定见 docs/decisions/2026-10-30-sprint-2-dashboard-contract.md。权限（viewer 读 / member 写、
工作空间归属）由路由层的 workspace_role 依赖先行检查，这里再按工作空间限定每一条查询。
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import dashboard_defaults, overview
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess, not_found
from app.models import Dashboard, Widget
from app.repositories import dashboards as dashboard_repo
from app.repositories import workspaces as workspace_repo
from app.schemas.dashboards import (
    MAX_DASHBOARDS_PER_WORKSPACE,
    MAX_WIDGETS_PER_DASHBOARD,
    DashboardCreate,
    DashboardOut,
    DashboardOverview,
    DashboardUpdate,
    OverviewSummary,
    TaskBrief,
    WidgetCreate,
    WidgetLayout,
    WidgetOut,
    WidgetPlacement,
    WidgetUpdate,
)

logger = logging.getLogger("app.dashboards")

# 布局的行号上限（与 schemas.dashboards 的 y 取值范围一致）
_MAX_ROW = 999


def version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _limit_exceeded(message: str) -> ApiError:
    return ApiError(409, "LIMIT_EXCEEDED", message)


def _invalid(loc: list[str | int], message: str) -> ApiError:
    """与请求校验失败相同的 422 格式（只含位置与原因，不回显输入）。"""
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", *loc], "msg": message, "type": "value_error"}]},
    )


def _dashboard_out(dashboard: Dashboard) -> DashboardOut:
    return DashboardOut.model_validate(dashboard, from_attributes=True)


def _parse_layout(widget: Widget) -> WidgetLayout | None:
    try:
        return WidgetLayout.model_validate(widget.layout_json)
    except ValidationError:
        logger.warning("widget layout invalid", extra={"widget_id": str(widget.id)})
        return None


def _fallback_layout(desktop_y: int = 0, mobile_y: int = 0) -> WidgetLayout:
    """库里的布局无法解析时（只可能来自手工改库）的兜底位置，不让整个接口失败。"""
    return WidgetLayout.model_validate(
        {
            "desktop": {"x": 0, "y": min(desktop_y, _MAX_ROW), "w": 4, "h": 3},
            "mobile": {"y": min(mobile_y, _MAX_ROW), "h": 3},
        }
    )


def _widget_out(widget: Widget, layout: WidgetLayout | None = None) -> WidgetOut:
    if layout is None:
        layout = _parse_layout(widget) or _fallback_layout()
    config = widget.config_json if isinstance(widget.config_json, dict) else {}
    return WidgetOut(
        id=widget.id,
        dashboard_id=widget.dashboard_id,
        widget_type=widget.widget_type,
        config=config,
        layout=layout,
        version=widget.version,
        created_at=widget.created_at,
        updated_at=widget.updated_at,
    )


def _widgets_out(widgets: Sequence[Widget]) -> list[WidgetOut]:
    """按桌面端 (y, x) 排序，与网格从上到下、从左到右的阅读顺序一致。

    布局无法解析的组件紧接着放在其余组件下方（而不是第 999 行），页面不会因此被撑得很高。
    """
    parsed = [(widget, _parse_layout(widget)) for widget in widgets]
    valid = [layout for _, layout in parsed if layout is not None]
    fallback = _fallback_layout(
        max((layout.desktop.y + layout.desktop.h for layout in valid), default=0),
        max((layout.mobile.y + layout.mobile.h for layout in valid), default=0),
    )
    items = [_widget_out(widget, layout or fallback) for widget, layout in parsed]
    return sorted(items, key=lambda w: (w.layout.desktop.y, w.layout.desktop.x, str(w.id)))


class DashboardService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        providers: Sequence[overview.ModuleProvider],
    ) -> None:
        self.db = db
        self.settings = settings
        self.providers = providers
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 仪表盘

    async def list_dashboards(self, access: WorkspaceAccess) -> list[DashboardOut]:
        dashboards = await dashboard_repo.list_for_workspace(self.db, access.workspace.id)
        return [_dashboard_out(d) for d in dashboards]

    async def create_dashboard(
        self, context: AuthContext, access: WorkspaceAccess, body: DashboardCreate
    ) -> DashboardOut:
        workspace_id = access.workspace.id
        # 锁住工作空间行再计数：并发创建时超限的一方返回 409，而不是一起插入超过上限
        await workspace_repo.lock(self.db, workspace_id)
        if await dashboard_repo.count_for_workspace(self.db, workspace_id) >= (
            MAX_DASHBOARDS_PER_WORKSPACE
        ):
            raise _limit_exceeded(f"每个工作空间最多 {MAX_DASHBOARDS_PER_WORKSPACE} 个仪表盘")
        dashboard = await dashboard_repo.create(
            self.db, workspace_id=workspace_id, owner_id=context.user.id, title=body.title
        )
        await dashboard_defaults.add_widgets(self.db, dashboard.id, body.widgets)
        await self.audit.record(
            "dashboard.created",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="dashboard",
            resource_id=dashboard.id,
            metadata={"widgets": len(body.widgets)},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return _dashboard_out(dashboard)

    async def update_dashboard(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        dashboard_id: uuid.UUID,
        body: DashboardUpdate,
    ) -> DashboardOut:
        if body.is_default:
            # 切换默认仪表盘会改动另一行：先锁住工作空间行，并发的切换排队执行。
            # 加锁顺序固定为“工作空间 → 仪表盘”，没有反向加锁的路径，不会死锁
            await workspace_repo.lock(self.db, access.workspace.id)
        dashboard = await self._locked_dashboard(access, dashboard_id)
        if body.version != dashboard.version:
            raise version_conflict(dashboard.version)

        changed: list[str] = []
        if body.title is not None and body.title != dashboard.title:
            dashboard.title = body.title
            changed.append("title")
        if body.is_default and not dashboard.is_default:
            # 先取消原默认再设置，满足“每个工作空间恰好一个默认”的唯一索引
            await dashboard_repo.clear_default(self.db, access.workspace.id)
            dashboard.is_default = True
            changed.append("is_default")
        if body.layout is not None:
            await self._save_layout(dashboard, body.layout)
            changed.append("layout")

        if changed:
            dashboard.version += 1
            await self.db.flush()
            await self.audit.record(
                "dashboard.updated",
                workspace_ids=[access.workspace.id],
                actor_id=context.user.id,
                resource_type="dashboard",
                resource_id=dashboard.id,
                metadata={"fields": changed},
                client_ip=context.client_ip,
            )
        await self.db.commit()
        return _dashboard_out(dashboard)

    async def _save_layout(self, dashboard: Dashboard, layout: list[WidgetPlacement]) -> None:
        widgets = {w.id: w for w in await dashboard_repo.list_widgets(self.db, dashboard.id)}
        submitted = [item.widget_id for item in layout]
        if len(set(submitted)) != len(submitted) or set(submitted) != set(widgets):
            raise _invalid(["layout"], "必须恰好列出该仪表盘当前的全部组件（每个一次）")
        for item in layout:
            widgets[item.widget_id].layout_json = item.layout.model_dump(mode="json")

    async def _locked_dashboard(
        self, access: WorkspaceAccess, dashboard_id: uuid.UUID
    ) -> Dashboard:
        dashboard = await dashboard_repo.get_for_workspace(
            self.db, dashboard_id=dashboard_id, workspace_id=access.workspace.id, lock=True
        )
        if dashboard is None:
            raise not_found()
        return dashboard

    # ---------------------------------------------------------------- 组件

    async def list_widgets(
        self, access: WorkspaceAccess, dashboard_id: uuid.UUID
    ) -> list[WidgetOut]:
        dashboard = await dashboard_repo.get_for_workspace(
            self.db, dashboard_id=dashboard_id, workspace_id=access.workspace.id
        )
        if dashboard is None:
            raise not_found()
        return _widgets_out(await dashboard_repo.list_widgets(self.db, dashboard.id))

    async def create_widget(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        dashboard_id: uuid.UUID,
        body: WidgetCreate,
    ) -> WidgetOut:
        dashboard = await self._locked_dashboard(access, dashboard_id)
        if await dashboard_repo.count_widgets(self.db, dashboard.id) >= MAX_WIDGETS_PER_DASHBOARD:
            raise _limit_exceeded(f"每个仪表盘最多 {MAX_WIDGETS_PER_DASHBOARD} 个组件")
        widget = await dashboard_repo.add_widget(
            self.db,
            dashboard_id=dashboard.id,
            widget_type=body.widget_type,
            config=body.config.model_dump(mode="json"),
            layout=body.layout.model_dump(mode="json"),
        )
        dashboard.version += 1
        await self.db.flush()
        await self._audit_widget("widget.created", context, access, widget)
        await self.db.commit()
        return _widget_out(widget)

    async def update_widget(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        widget_id: uuid.UUID,
        body: WidgetUpdate,
    ) -> WidgetOut:
        widget, _ = await self._locked_widget(access, widget_id)
        if body.widget_type != widget.widget_type:
            raise _invalid(["widget_type"], "组件类型不能修改")
        if body.version != widget.version:
            raise version_conflict(widget.version)
        config = body.config.model_dump(mode="json")
        if config != widget.config_json:
            widget.config_json = config
            widget.version += 1
            await self.db.flush()
            await self._audit_widget("widget.updated", context, access, widget)
        await self.db.commit()
        return _widget_out(widget)

    async def delete_widget(
        self, context: AuthContext, access: WorkspaceAccess, widget_id: uuid.UUID
    ) -> None:
        widget, dashboard = await self._locked_widget(access, widget_id)
        await self._audit_widget("widget.deleted", context, access, widget)
        await dashboard_repo.delete_widget(self.db, widget.id)
        dashboard.version += 1
        await self.db.commit()

    async def _locked_widget(
        self, access: WorkspaceAccess, widget_id: uuid.UUID
    ) -> tuple[Widget, Dashboard]:
        """锁住组件所属的仪表盘（与同一仪表盘上的写操作串行），再重新读取组件。

        锁之前读到的组件可能在等锁期间被并发删除：拿到锁后重新查询，不存在就按 404 处理。
        """
        widget = await dashboard_repo.get_widget_for_workspace(
            self.db, widget_id=widget_id, workspace_id=access.workspace.id
        )
        if widget is None:
            raise not_found()
        dashboard = await self._locked_dashboard(access, widget.dashboard_id)
        widget = await dashboard_repo.get_widget_for_workspace(
            self.db, widget_id=widget_id, workspace_id=access.workspace.id, refresh=True
        )
        if widget is None:
            raise not_found()
        return widget, dashboard

    async def _audit_widget(
        self, action: str, context: AuthContext, access: WorkspaceAccess, widget: Widget
    ) -> None:
        # 不记录配置内容（便签文字、链接）
        await self.audit.record(
            action,
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="widget",
            resource_id=widget.id,
            metadata={"dashboard_id": str(widget.dashboard_id), "widget_type": widget.widget_type},
            client_ip=context.client_ip,
        )

    # ---------------------------------------------------------------- 首页聚合

    async def overview(self, access: WorkspaceAccess) -> DashboardOverview:
        dashboard = await dashboard_repo.get_default(self.db, access.workspace.id)
        if dashboard is None:
            # 正常情况下首次安装与迁移 0004 已经创建；这里只为异常数据兜底
            dashboard = await dashboard_defaults.create_default_dashboard(
                self.db, workspace_id=access.workspace.id, owner_id=access.workspace.owner_id
            )
            await self.db.commit()
        widgets = await dashboard_repo.list_widgets(self.db, dashboard.id)
        modules = await overview.collect(
            self.providers,
            access.workspace.id,
            total_timeout=self.settings.dashboard_overview_timeout_seconds,
        )
        summary: dict[str, Any] = {
            "pending_tasks": None,
            "active_projects": None,
            "unread_notifications": None,
        }
        today_tasks: list[TaskBrief] = []
        for result in modules.values():
            if result.status.status in {"ok", "stale"}:
                summary.update({k: v for k, v in result.summary.items() if k in summary})
                today_tasks.extend(result.today_tasks)
        return DashboardOverview(
            dashboard=_dashboard_out(dashboard),
            widgets=_widgets_out(widgets),
            summary=OverviewSummary(**summary),
            modules={name: result.status for name, result in modules.items()},
            today_tasks=today_tasks,
            generated_at=datetime.now(UTC),
        )
