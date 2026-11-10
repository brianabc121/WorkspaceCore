"""模块在各工作空间的启用状态（T-0159；docs/modules/README.md）。

- 没有记录时按清单的 default_enabled；
- 只有 owner 能启用 / 停用；依赖的模块都启用时才能启用，有启用的模块依赖它时不能停用；
- 停用只改启用记录，模块自己的数据不动，再启用就恢复；
- 启用 / 停用写审计日志，并发出 modules 事件让前端刷新导航；
- 应用中心（T-0210）：实际启停一次 state_version 加 1；启停、同步重试共用工作空间的模块操作锁
  （跨进程的 advisory lock），钩子的结果写进同步记录（app/modules/sync.py），失败不回滚开关。
"""

import uuid
from dataclasses import dataclass
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
from app.modules.locks import advisory_lock, module_operation_lock
from app.modules.sync import ModuleSyncCoordinator
from app.schemas.modules import ModuleList, ModuleOut
from app.schemas.workspaces import Role

MODULE_DISABLED = "MODULE_DISABLED"
MODULE_BUSY = "MODULE_BUSY"


def disabled_error() -> ApiError:
    return ApiError(404, MODULE_DISABLED, "这个功能在当前工作空间没有启用")


async def enabled_keys(db: AsyncSession, workspace_id: uuid.UUID) -> set[str]:
    """当前工作空间启用的模块 key。"""
    states = await module_states(db, workspace_id)
    return {key for key, state in states.items() if state.enabled}


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


@dataclass(frozen=True)
class ModuleState:
    enabled: bool
    version: int


async def module_states(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, ModuleState]:
    """每个已注册模块在这个工作空间的启用状态与目标版本（没有记录：清单默认值、版本 0）。"""
    rows = await db.execute(
        select(
            WorkspaceModule.module_key, WorkspaceModule.enabled, WorkspaceModule.state_version
        ).where(WorkspaceModule.workspace_id == workspace_id)
    )
    explicit = {key: ModuleState(enabled, version) for key, enabled, version in rows.all()}
    return {m.key: explicit.get(m.key, ModuleState(m.default_enabled, 0)) for m in registry.MODULES}


def busy_error() -> ApiError:
    return ApiError(409, MODULE_BUSY, "这个工作空间正在启用 / 停用模块或同步配置，请稍后再试")


def check_state_version(current: int, expected: int | None) -> None:
    if expected is not None and expected != current:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "模块状态已被其他操作修改，请刷新后重试",
            details={"current_state_version": current},
        )


class ModuleService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: Any = None) -> None:
        self.db = db
        self.settings = settings
        # ConnectorRuntime：交给模块的 on_toggle（例如新闻早报重新生成配置文件）
        self.runtime = runtime

    def out(
        self, manifest: ModuleManifest, state: ModuleState, access: WorkspaceAccess
    ) -> ModuleOut:
        missing = registry.missing_requirements(manifest, self.settings)
        tools = [name for name, key in registry.tool_owners().items() if key == manifest.key]
        return ModuleOut(
            key=manifest.key,
            name=manifest.name,
            description=manifest.description,
            version=manifest.version,
            enabled=state.enabled,
            state_version=state.version,
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
        states = await module_states(self.db, access.workspace.id)
        return ModuleList(items=[self.out(m, states[m.key], access) for m in registry.MODULES])

    async def update(
        self,
        access: WorkspaceAccess,
        key: str,
        enabled: bool,
        client_ip: str | None,
        *,
        expected_state_version: int | None = None,
    ) -> ModuleOut:
        """启用 / 停用；持有工作空间的模块操作锁完成“检查依赖 → 写目标 → 提交 → 同步”。

        expected_state_version（新页面必带）与当前不一致时 409 VERSION_CONFLICT；旧客户端可以不带，
        但同样拿锁、同样检查依赖。目标没变时不写审计、不执行钩子（之前同步失败请用重试接口）。
        """
        manifest = registry.get(key)
        if manifest is None:
            raise ApiError(404, "NOT_FOUND", "没有这个模块")
        workspace_id = access.workspace.id
        async with advisory_lock(self.db, module_operation_lock(workspace_id)) as acquired:
            if not acquired:
                raise busy_error()
            states = await module_states(self.db, workspace_id)
            state = states[key]
            check_state_version(state.version, expected_state_version)
            current = {k for k, v in states.items() if v.enabled}
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
            if state.enabled == enabled:
                return self.out(manifest, state, access)
            statement = insert(WorkspaceModule).values(
                workspace_id=workspace_id,
                module_key=key,
                enabled=enabled,
                state_version=1,
                updated_by=access.user_id,
            )
            version = await self.db.scalar(
                statement.on_conflict_do_update(
                    index_elements=[WorkspaceModule.workspace_id, WorkspaceModule.module_key],
                    set_={
                        "enabled": enabled,
                        "state_version": WorkspaceModule.state_version + 1,
                        "updated_by": access.user_id,
                        "updated_at": func.now(),
                    },
                ).returning(WorkspaceModule.state_version)
            )
            state = ModuleState(enabled, int(version or 0))
            await AuditTrail(self.db, self.settings).record(
                "module.enabled" if enabled else "module.disabled",
                workspace_ids=[workspace_id],
                actor_id=access.user_id,
                resource_type="module",
                resource_id=key,
                metadata={"module": key, "state_version": state.version},
                client_ip=client_ip,
            )
            events.mark(self.db, workspace_id, "modules", "overview")
            await self.db.commit()
            await ModuleSyncCoordinator(self.db, self.settings, self.runtime).run(
                manifest,
                workspace_id,
                state_version=state.version,
                enabled=enabled,
                trigger="toggle",
                actor_id=access.user_id,
                client_ip=client_ip,
            )
        return self.out(manifest, state, access)
