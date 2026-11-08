"""侧栏布局（T-0161；docs/v2/MODULES_PLAN.md 第 2 节）。

- 成员都能读（只返回自己角色能看到的零代码页面）；只有 owner 能改，整份替换 + version 防并发覆盖；
- 外部链接只允许 http(s)、不能带账号密码；嵌入页只允许部署设置 EMBED_ORIGINS 里的 https 来源，
  且不能是工作台自己（页面上用带沙箱的 iframe 显示，CSP 的 frame-src 由 Caddy 按同一个变量放行）；
- 笔记、仪表盘必须属于当前工作空间；
- 修改写审计 navigation.updated，发 navigation 事件让前端刷新侧栏。
"""

from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events
from app.domain.audit import AuditTrail
from app.domain.policy import WorkspaceAccess, has_role
from app.models import Dashboard, KnowledgeNote
from app.models.navigation import WorkspaceNavigation
from app.schemas.navigation import NavigationIn, NavigationLayout, NavigationOut, NavPage
from app.schemas.workspaces import Role


def _invalid(loc: list[str | int], message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", *loc], "msg": message, "type": "value_error"}]},
    )


class NavigationService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def _row(self, access: WorkspaceAccess) -> WorkspaceNavigation | None:
        return await self.db.get(WorkspaceNavigation, access.workspace.id)

    def _out(
        self, layout: NavigationLayout, version: int, access: WorkspaceAccess
    ) -> NavigationOut:
        visible = [p for p in layout.pages if has_role(access.role, p.min_role)]
        return NavigationOut(
            sections=layout.sections,
            pages=visible,
            placements=layout.placements,
            section_order=layout.section_order,
            version=version,
            embed_origins=self.settings.allowed_embed_origins,
            can_manage=has_role(access.role, Role.owner),
        )

    async def get(self, access: WorkspaceAccess) -> NavigationOut:
        row = await self._row(access)
        if row is None:
            return self._out(NavigationLayout(), 0, access)
        return self._out(NavigationLayout.model_validate(row.layout), row.version, access)

    async def _check_pages(self, access: WorkspaceAccess, pages: list[NavPage]) -> None:
        own_origin = _origin(self.settings.public_base_url)
        allowed = set(self.settings.allowed_embed_origins)
        for index, page in enumerate(pages):
            loc: list[str | int] = ["pages", index]
            if page.kind in ("link", "embed"):
                parts = urlsplit(page.url or "")
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    raise _invalid([*loc, "url"], "只能是 http(s) 地址")
                if parts.username or parts.password:
                    raise _invalid([*loc, "url"], "地址里不能带账号或密码")
                if page.kind == "embed":
                    origin = _origin(page.url or "")
                    if origin == own_origin:
                        raise _invalid([*loc, "url"], "不能嵌入工作台自己的页面，请用“站内页面”")
                    if origin not in allowed:
                        raise _invalid(
                            [*loc, "url"],
                            "这个来源不允许嵌入：请在部署设置 EMBED_ORIGINS 里加上它（见部署指南）",
                        )
            elif page.kind == "note":
                found = await self.db.scalar(
                    select(KnowledgeNote.id).where(
                        KnowledgeNote.id == page.target_id,
                        KnowledgeNote.workspace_id == access.workspace.id,
                    )
                )
                if found is None:
                    raise _invalid([*loc, "target_id"], "笔记不存在")
            elif page.kind == "dashboard":
                found = await self.db.scalar(
                    select(Dashboard.id).where(
                        Dashboard.id == page.target_id,
                        Dashboard.workspace_id == access.workspace.id,
                    )
                )
                if found is None:
                    raise _invalid([*loc, "target_id"], "仪表盘不存在")

    async def update(
        self, access: WorkspaceAccess, body: NavigationIn, client_ip: str | None
    ) -> NavigationOut:
        row = await self._row(access)
        current = 0 if row is None else row.version
        if body.version != current:
            raise ApiError(409, "VERSION_CONFLICT", "侧栏布局已被修改，请刷新后再试")
        await self._check_pages(access, body.pages)
        layout = NavigationLayout.model_validate(body.model_dump(exclude={"version"}))
        stored = layout.model_dump(mode="json")
        if row is None:
            row = WorkspaceNavigation(
                workspace_id=access.workspace.id,
                layout=stored,
                version=1,
                updated_by=access.user_id,
            )
            self.db.add(row)
        else:
            row.layout = stored
            row.version = current + 1
            row.updated_by = access.user_id
        await AuditTrail(self.db, self.settings).record(
            "navigation.updated",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="navigation",
            resource_id=access.workspace.id,
            metadata={
                "sections": len(layout.sections),
                "pages": len(layout.pages),
                "placements": len(layout.placements),
            },
            client_ip=client_ip,
        )
        events.mark(self.db, access.workspace.id, "navigation")
        await self.db.commit()
        return self._out(layout, row.version, access)


def _origin(url: str) -> str:
    parts = urlsplit(url.strip())
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}{port}"
