"""侧栏布局（T-0161）：自定义分组、页面挪分组、零代码页面。

分组用 key 引用：内置分组 workspace（工作空间）、tools（连接与工具）、admin（管理）、apps（应用），
自定义分组 custom-<id>。零代码页面五种：
- link：外部链接（只允许 http(s)，新窗口打开）；
- page：站内页面（以 / 开头的站内路径，例如某个项目页）；
- note：知识库笔记（note_id）；
- dashboard：仪表盘（dashboard_id）；
- embed：嵌入页（https 地址，来源必须在部署设置 EMBED_ORIGINS 里，页面上用带沙箱的 iframe 显示）。
"""

import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.workspaces import Role

BUILTIN_SECTIONS = ("workspace", "tools", "admin", "apps")
_PATH = re.compile(r"/(?!/)[A-Za-z0-9\-._~/%?=&#:@+]*")

ItemId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
SectionKey = Annotated[
    str, StringConstraints(pattern=r"^(workspace|tools|admin|apps|custom-[a-z0-9][a-z0-9-]{0,39})$")
]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
PageKind = Literal["link", "page", "note", "dashboard", "embed"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NavSection(_Strict):
    id: ItemId = Field(description="分组 id；引用时写 custom-<id>")
    title: Title


class NavPage(_Strict):
    id: ItemId
    title: Title
    section: SectionKey
    kind: PageKind
    url: Annotated[str, StringConstraints(max_length=2048)] | None = Field(
        default=None, description="link / embed：http(s) 地址"
    )
    path: Annotated[str, StringConstraints(max_length=512)] | None = Field(
        default=None, description="page：以 / 开头的站内路径"
    )
    target_id: UUID | None = Field(default=None, description="note / dashboard：笔记或仪表盘的 id")
    min_role: Role = Field(default=Role.viewer, description="至少什么角色能在侧栏看到")

    @model_validator(mode="after")
    def _target(self) -> "NavPage":
        if self.kind in ("link", "embed"):
            if not self.url or self.path or self.target_id:
                raise ValueError(f"{self.kind} 页面只填 url")
        elif self.kind == "page":
            if not self.path or self.url or self.target_id:
                raise ValueError("page 页面只填 path")
            if not _PATH.fullmatch(self.path):
                raise ValueError("path 必须是以 / 开头的站内路径")
        elif not self.target_id or self.url or self.path:
            raise ValueError(f"{self.kind} 页面只填 target_id")
        return self


class NavigationLayout(_Strict):
    sections: list[NavSection] = Field(default_factory=list, max_length=12)
    pages: list[NavPage] = Field(default_factory=list, max_length=50)
    placements: dict[Annotated[str, StringConstraints(max_length=200)], SectionKey] = Field(
        default_factory=dict, max_length=100, description="已有页面挪到哪个分组：{站内路径: 分组}"
    )
    section_order: list[SectionKey] = Field(
        default_factory=list, max_length=16, description="分组顺序；没列出的排在后面"
    )

    @model_validator(mode="after")
    def _references(self) -> "NavigationLayout":
        custom = {f"custom-{s.id}" for s in self.sections}
        if len(custom) != len(self.sections):
            raise ValueError("分组 id 重复")
        if len({p.id for p in self.pages}) != len(self.pages):
            raise ValueError("页面 id 重复")
        known = custom | set(BUILTIN_SECTIONS)
        used = [p.section for p in self.pages] + list(self.placements.values()) + self.section_order
        missing = sorted({key for key in used if key not in known})
        if missing:
            raise ValueError(f"引用了不存在的分组：{'、'.join(missing)}")
        for path in self.placements:
            if not _PATH.fullmatch(path):
                raise ValueError("placements 的键必须是以 / 开头的站内路径")
        return self


class NavigationIn(NavigationLayout):
    version: int = Field(ge=0, description="读到的版本；不一致返回 409 VERSION_CONFLICT")


class NavigationOut(NavigationLayout):
    version: int
    embed_origins: list[str] = Field(description="部署允许嵌入的来源（.env 的 EMBED_ORIGINS）")
    can_manage: bool = Field(description="当前用户能否修改（只有 owner）")
