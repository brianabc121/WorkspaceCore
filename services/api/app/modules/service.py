"""模块在各工作空间的启用状态（T-0159；docs/modules/README.md）。

- 没有记录时按清单的 default_enabled；
- 只有 owner 能启用 / 停用；依赖的模块都启用时才能启用，有启用的模块依赖它时不能停用；
- 停用只改启用记录，模块自己的数据不动，再启用就恢复；
- 启用 / 停用写审计日志，并发出 modules 事件让前端刷新导航。
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events
from app.domain.audit import AuditTrail
from app.domain.policy import WorkspaceAccess, has_role
from app.models import WorkspaceModule
from app.modules import registry
from app.modules.base import ModuleManifest
from app.schemas.modules import ModuleList, ModuleOut
from app.schemas.workspaces import Role

MODULE_DISABLED = "MODULE_DISABLED"
logger = logging.getLogger("app.modules")


def disabled_error() -> ApiError:
    return ApiError(404, MODULE_DISABLED, "这个功能在当前工作空间没有启用")


async def enabled_keys(db: AsyncSession, workspace_id: uuid.UUID) -> set[str]:
    """当前工作空间启用的模块 key。"""
    rows = await db.execute(
        select(WorkspaceModule.module_key, WorkspaceModule.enabled).where(
            WorkspaceModule.workspace_id == workspace_id
        )
    )
    explicit: dict[str, bool] = {key: enabled for key, enabled in rows.tuples()}
    return {m.key for m in registry.MODULES if explicit.get(m.key, m.default_enabled)}


async def is_enabled(db: AsyncSession, workspace_id: uuid.UUID, key: str) -> bool:
    manifest = registry.get(key)
    if manifest is None:
        return False
    enabled = await db.scalar(
        select(WorkspaceModule.enabled).where(
            WorkspaceModule.workspace_id == workspace_id, WorkspaceModule.module_key == key
        )
    )
    return manifest.default_enabled if enabled is None else bool(enabled)


async def visible_tool_modules(
    db: AsyncSession, settings: Settings, workspace_id: uuid.UUID
) -> set[str]:
    """工具对智能体可见的模块：模块启用，且清单的 tools_visible（如果有）也返回 True。"""
    visible: set[str] = set()
    for key in await enabled_keys(db, workspace_id):
        manifest = registry.BY_KEY[key]
        if not manifest.tools:
            continue
        if manifest.tools_visible is not None:
            check: Any = registry.resolve(manifest.tools_visible)
            if not await check(db, settings, workspace_id):
                continue
        visible.add(key)
    return visible


class ModuleService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: Any = None) -> None:
        self.db = db
        self.settings = settings
        # ConnectorRuntime：交给模块的 on_toggle（例如新闻早报重新生成配置文件）
        self.runtime = runtime

    def _out(self, manifest: ModuleManifest, enabled: bool, access: WorkspaceAccess) -> ModuleOut:
        missing = registry.missing_requirements(manifest, self.settings)
        tools = [name for name, key in registry.tool_owners().items() if key == manifest.key]
        return ModuleOut(
            key=manifest.key,
            name=manifest.name,
            description=manifest.description,
            version=manifest.version,
            enabled=enabled,
            default_enabled=manifest.default_enabled,
            ready=not missing,
            missing=[r.env for r in missing],
            depends_on=list(manifest.depends_on),
            dependents=registry.dependents(manifest.key),
            tools=sorted(tools),
            widgets=list(manifest.widgets),
            can_manage=has_role(access.role, Role.owner),
        )

    async def list(self, access: WorkspaceAccess) -> ModuleList:
        enabled = await enabled_keys(self.db, access.workspace.id)
        return ModuleList(items=[self._out(m, m.key in enabled, access) for m in registry.MODULES])

    async def update(
        self, access: WorkspaceAccess, key: str, enabled: bool, client_ip: str | None
    ) -> ModuleOut:
        manifest = registry.get(key)
        if manifest is None:
            raise ApiError(404, "NOT_FOUND", "没有这个模块")
        workspace_id = access.workspace.id
        current = await enabled_keys(self.db, workspace_id)
        if enabled:
            missing = [d for d in manifest.depends_on if d not in current]
            if missing:
                names = "、".join(registry.BY_KEY[d].name for d in missing)
                raise ApiError(409, "MODULE_DEPENDENCY", f"请先启用它依赖的模块：{names}")
        else:
            blocking = [d for d in registry.dependents(key) if d in current]
            if blocking:
                names = "、".join(registry.BY_KEY[d].name for d in blocking)
                raise ApiError(409, "MODULE_DEPENDENCY", f"请先停用依赖它的模块：{names}")
        if (key in current) != enabled:
            statement = insert(WorkspaceModule).values(
                workspace_id=workspace_id,
                module_key=key,
                enabled=enabled,
                updated_by=access.user_id,
            )
            await self.db.execute(
                statement.on_conflict_do_update(
                    index_elements=[WorkspaceModule.workspace_id, WorkspaceModule.module_key],
                    set_={
                        "enabled": enabled,
                        "updated_by": access.user_id,
                        "updated_at": func.now(),
                    },
                )
            )
            await AuditTrail(self.db, self.settings).record(
                "module.enabled" if enabled else "module.disabled",
                workspace_ids=[workspace_id],
                actor_id=access.user_id,
                resource_type="module",
                resource_id=key,
                metadata={"module": key},
                client_ip=client_ip,
            )
            events.mark(self.db, workspace_id, "modules", "overview")
            await self.db.commit()
            await self._on_toggle(manifest, workspace_id, enabled)
        return self._out(manifest, enabled, access)

    async def _on_toggle(
        self, manifest: ModuleManifest, workspace_id: uuid.UUID, enabled: bool
    ) -> None:
        if manifest.on_toggle is None:
            return
        try:
            hook: Any = registry.resolve(manifest.on_toggle)
            await hook(self.db, self.settings, self.runtime, workspace_id, enabled)
        except Exception as error:  # 开关已经生效；钩子失败只记日志
            await self.db.rollback()
            logger.warning(
                "module toggle hook failed",
                extra={"module": manifest.key, "error_type": type(error).__name__},
            )
